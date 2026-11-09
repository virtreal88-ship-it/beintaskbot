from types import SimpleNamespace
import asyncio
import unittest
from unittest.mock import patch

from tenant_operator_voice_gateway import _startup_failure_code, make_handler
from tenant_operator_voice_session import LeaseAlreadyActive, Refused


def test_only_exact_pre_open_lease_refusal_is_exposed():
    session = SimpleNamespace(fence=None)
    refusal = LeaseAlreadyActive('session_already_active')
    assert _startup_failure_code(refusal, session, False) == 'voice_session_busy'
    assert _startup_failure_code(refusal, session, True) == 'voice_unavailable'
    assert _startup_failure_code(refusal, SimpleNamespace(fence=2), False) == 'voice_unavailable'
    assert _startup_failure_code(refusal, None, False) == 'voice_unavailable'
    assert _startup_failure_code(Refused('session_already_active'), session, False) == 'voice_unavailable'
    assert _startup_failure_code(LeaseAlreadyActive('other'), session, False) == 'voice_unavailable'
    assert _startup_failure_code(RuntimeError('private provider detail'), session, False) == 'voice_unavailable'


class _Socket:
    def __init__(self, **_kwargs):
        self.closed = False
        self.close_code = None
        self.messages = []
    def can_prepare(self, _request):
        return SimpleNamespace(ok=True)
    async def prepare(self, _request):
        pass
    async def send_json(self, message):
        self.messages.append(message)
    async def close(self):
        self.closed = True
    def exception(self):
        return None
    def __aiter__(self):
        return self
    async def __anext__(self):
        await asyncio.Event().wait()
        raise StopAsyncIteration


class _Session:
    def __init__(self, failure):
        self.fence = None
        self.closed = True
        self.failure = failure
    async def start(self):
        raise self.failure
    async def end(self, _reason):
        pass


class GatewayStartFailure(unittest.IsolatedAsyncioTestCase):
    async def test_busy_and_other_errors_send_only_allowlisted_codes(self):
        for exc, expected in [
            (LeaseAlreadyActive('session_already_active'), 'voice_session_busy'),
            (Refused('private provider detail'), 'voice_unavailable'),
        ]:
            async def authenticate(_request):
                return ('tenant', 'actor', 'channel', 'conversation')
            handler = make_handler(allowed_origins=['https://crm.pro.az'],
                                   authenticate=authenticate,
                                   build_session=lambda _scope, _client: _Session(exc))
            with patch('tenant_operator_voice_gateway.web.WebSocketResponse', _Socket):
                result = await handler(SimpleNamespace(headers={'Origin':'https://crm.pro.az'}))
            self.assertEqual(result.messages, [{'type':'error', 'code':expected}])
            self.assertTrue(result.closed)
