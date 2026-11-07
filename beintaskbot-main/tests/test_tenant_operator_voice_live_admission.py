"""The pilot's constructor, start, and wire gates share one server policy."""
import asyncio
import uuid
import unittest
from unittest.mock import patch

from tenant_linear_policy import TenantLinearError
from tenant_operator_realtime_wire import OpenAIRealtimeProvider, _require_live_admission
from tenant_operator_voice_pilot import PilotAdmission
from tenant_operator_voice_session import Refused, Session


class Authority:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.checks = []

    async def check(self, scope, stage):
        self.checks.append((scope, stage))
        if not self.allowed:
            raise PermissionError('owner authorization revoked')


class Lease:
    def __init__(self, claim_ok=True):
        self.claim_ok = claim_ok
        self.claims = 0
        self.current = set()
        self.releases = 0

    async def claim(self, scope):
        self.claims += 1
        if not self.claim_ok:
            return None
        self.current.add((scope, 1))
        return 1

    async def assert_current(self, scope, fence):
        if (scope, fence) not in self.current:
            raise PermissionError('lease not current')

    async def confirm_provider_open(self, scope, fence):
        await self.assert_current(scope, fence)
        return True

    async def release(self, scope, fence):
        self.releases += 1
        self.current.discard((scope, fence))
        return True


class Context:
    async def read_authorized(self, _scope):
        return {'existing_copilot_instructions': 'Never send messages.', 'lines': [], 'extra_context': {}}

    async def revalidate(self, _scope):
        return True


class Client:
    async def send(self, _event):
        return None


class MockProvider:
    mock_only = True

    def __init__(self):
        self.opens = 0
        self.closed = False
        self.sent = []

    async def bind_pilot_claim(self, *_):
        return None

    async def open(self, *_args, **_kwargs):
        self.opens += 1

    async def send(self, event):
        self.sent.append(dict(event))

    async def close(self):
        self.closed = True


class LiveAdmission(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = ('00000000-0000-0000-0000-000000000001', 77,
                      '00000000-0000-0000-0000-000000000002', 'dialog')
        self.session_id = str(uuid.uuid4())
        self.authority = Authority()
        self.lease = Lease()
        self.policy = PilotAdmission(self.authority, self.lease, self.scope, self.session_id)

    def make_real_provider(self, credential):
        return OpenAIRealtimeProvider(credential, self.policy)

    def test_constructor_binds_exact_policy_to_authority_lease_and_provider(self):
        provider = self.make_real_provider(lambda: None)
        session = Session(self.scope, authority=self.authority, ledger=None, lease=self.lease,
                          context=Context(), provider=provider, client=Client(),
                          session_id=self.session_id, pilot_policy=self.policy)
        self.assertIs(session.pilot_policy, provider.guard)

        other = PilotAdmission(self.authority, self.lease, self.scope, str(uuid.uuid4()))
        wrong_provider = self.make_real_provider(lambda: None)
        with self.assertRaisesRegex(Refused, 'provider_not_trusted'):
            Session(self.scope, authority=self.authority, ledger=None, lease=self.lease,
                    context=Context(), provider=wrong_provider, client=Client(),
                    session_id=self.session_id, pilot_policy=other)

    async def test_direct_provider_and_wire_entry_refuse_before_credential_or_network(self):
        calls = []

        async def credential():
            calls.append('credential')
            return 'synthetic-only'

        provider = self.make_real_provider(credential)
        with self.assertRaises(TenantLinearError):
            await provider.open({}, tools=[], create_response=False, interrupt_response=False,
                                transcription='gpt-transcribe', reconnect=False)
        self.assertEqual(calls, [])
        with self.assertRaises(TenantLinearError):
            await _require_live_admission(self.policy, ('other', 77, 'channel', 'dialog'), self.session_id)
        self.assertEqual(calls, [])

    async def test_provider_rechecks_revocation_and_lease_loss_before_credential_use(self):
        calls = []

        async def credential():
            calls.append('credential')
            return 'synthetic-only'

        provider = self.make_real_provider(credential)
        self.lease.current.add((self.scope, 1))
        await self.policy.admit(self.scope, self.session_id, 1)
        self.authority.allowed = False
        with self.assertRaises(TenantLinearError):
            await provider.open({}, tools=[], create_response=False, interrupt_response=False,
                                transcription='gpt-transcribe', reconnect=False)
        self.authority.allowed = True
        self.lease.current.clear()
        with self.assertRaises(TenantLinearError):
            await provider.open({}, tools=[], create_response=False, interrupt_response=False,
                                transcription='gpt-transcribe', reconnect=False)
        self.assertEqual(calls, [])

    async def test_session_start_denial_and_lease_failure_precede_provider_open(self):
        provider = MockProvider()
        denied = PilotAdmission(Authority(allowed=False), self.lease, self.scope, self.session_id)
        session = Session(self.scope, authority=denied.authority, ledger=None, lease=self.lease,
                          context=Context(), provider=provider, client=Client(),
                          session_id=self.session_id, pilot_policy=denied)
        with self.assertRaises(PermissionError):
            await session.start()
        self.assertEqual(provider.opens, 0)
        self.assertEqual(self.lease.claims, 0)

        self.lease.claim_ok = False
        session = Session(self.scope, authority=self.authority, ledger=None, lease=self.lease,
                          context=Context(), provider=provider, client=Client(),
                          session_id=self.session_id, pilot_policy=self.policy)
        with self.assertRaisesRegex(Refused, 'session_already_active'):
            await session.start()
        self.assertEqual(provider.opens, 0)

    async def test_session_start_admits_once_then_rechecks_same_policy_before_provider(self):
        provider = MockProvider()
        session = Session(self.scope, authority=self.authority, ledger=None, lease=self.lease,
                          context=Context(), provider=provider, client=Client(),
                          session_id=self.session_id, pilot_policy=self.policy,
                          max_seconds=5, idle_seconds=4)
        await session.start()
        self.assertEqual(provider.opens, 1)
        self.assertIn((self.scope, 'pilot_admit'), self.authority.checks)
        self.assertTrue(await self.policy.assert_live(self.scope, self.session_id))
        self.assertTrue(await session.end('test'))
        self.assertTrue(provider.closed)

    async def test_provider_startup_may_complete_after_previous_three_second_cutoff(self):
        class SlowProvider(MockProvider):
            async def open(self, *_args, **_kwargs):
                await asyncio.sleep(3.05)
                self.opens += 1

        provider = SlowProvider()
        session = Session(self.scope, authority=self.authority, ledger=None, lease=self.lease,
                          context=Context(), provider=provider, client=Client(),
                          session_id=self.session_id, pilot_policy=self.policy,
                          max_seconds=30, idle_seconds=10)
        await session.start()
        self.assertEqual(provider.opens, 1)
        await session.end('test')


if __name__ == '__main__':
    unittest.main()
