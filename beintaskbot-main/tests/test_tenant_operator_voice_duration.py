"""Synthetic ten-minute owner-pilot bounds; no credentials or provider I/O."""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tenant_operator_voice_session import Session, Refused
from tenant_operator_voice_pilot import PilotAdmission
import tenant_operator_voice_orchestration as orchestration
import tenant_operator_realtime_wire as wire
import tenant_voice_test_grant as grant


class PilotDuration(unittest.IsolatedAsyncioTestCase):
    def dependencies(self, pilot=True):
        scope=(str(uuid.uuid4()),7,str(uuid.uuid4()),'synthetic-duration')
        sid=str(uuid.uuid4())
        authority=orchestration.ScopedAuthority({}, {}, SimpleNamespace(
            tenant=scope[0],actor=scope[1],channel_id=scope[2],conversation_id=scope[3]))
        authority.check=AsyncMock()
        lease=SimpleNamespace(claim=AsyncMock(),assert_current=AsyncMock(),release=AsyncMock())
        provider=SimpleNamespace(mock_only=True,bind_pilot_claim=AsyncMock(),bind_claim=AsyncMock(),send=AsyncMock())
        policy=PilotAdmission(authority,lease,scope,sid) if pilot else None
        return scope,dict(authority=authority,ledger=None if pilot else Mock(),lease=lease,
                          provider=provider,context=Mock(),client=Mock(),session_id=sid,pilot_policy=policy)

    def session(self, pilot=True, **options):
        scope,deps=self.dependencies(pilot)
        return Session(scope,**deps,**options)

    def test_only_pilot_builder_selects_ten_minutes(self):
        scope,deps=self.dependencies()
        with patch.object(orchestration,'create_pilot_provider',return_value=deps['provider']):
            session=orchestration._compose_pilot_session(deps['authority'],Mock(),deps['lease'],deps['session_id'])
        self.assertEqual(session.max_seconds,600)
        self.assertEqual(session.max_input_bytes,28_800_000)
        self.assertIsNone(session.ledger)
        self.assertIsNone(session.transcript)
        self.assertIs(type(session.pilot_policy),PilotAdmission)
        self.assertEqual(self.session(pilot=False).max_seconds,120)
        self.assertEqual(self.session().max_seconds,120,'constructor default remains conservative')

    def test_general_builder_keeps_original_default_and_financial_guard(self):
        _,deps=self.dependencies(False)
        with patch.object(orchestration,'require_voice_budget_ready') as gate, \
             patch.object(orchestration,'create_production_provider',return_value=deps['provider']):
            session=orchestration._compose_server_session(deps['authority'],Mock(),deps['lease'],deps['session_id'])
        gate.assert_called_once_with()
        self.assertEqual(session.max_seconds,120)
        self.assertEqual(session.max_input_bytes,5_760_000)
        self.assertIsInstance(session.ledger,orchestration.ScopedBudget)
        self.assertIsNone(session.pilot_policy)
        self.assertFalse(grant.ENFORCEMENT_VERIFIED)
        self.assertFalse(grant.RECONCILIATION_VERIFIED)
        self.assertEqual(grant.CAP,5_000_000)
        self.assertEqual(grant.HOLD,2_130_018)

    def test_duration_limits_and_policy_binding_fail_closed(self):
        for pilot,seconds in ((True,601),(False,121),(True,0),(True,float('inf'))):
            with self.subTest(pilot=pilot,seconds=seconds),self.assertRaisesRegex(Refused,'invalid_limits'):
                self.session(pilot,max_seconds=seconds)
        scope,deps=self.dependencies()
        deps['pilot_policy']=Mock()
        with self.assertRaisesRegex(Refused,'pilot_policy_invalid'):
            Session(scope,**deps,max_seconds=600)
        scope,deps=self.dependencies()
        deps['pilot_policy'].scope=('other',7,'other','other')
        with self.assertRaisesRegex(Refused,'pilot_policy_scope_mismatch'):
            Session(scope,**deps,max_seconds=600)

    async def test_watchdog_survives_two_minutes_and_stops_at_ten(self):
        session=self.session(max_seconds=600)
        session.closed=False;session.started=100;session.fence=1
        session.end=AsyncMock()
        with patch('tenant_operator_voice_session.asyncio.sleep',new=AsyncMock()), \
             patch('tenant_operator_voice_session.time',SimpleNamespace(monotonic=Mock(side_effect=[220,699.999,700]))):
            await session._watch()
        self.assertEqual(session.authority.check.await_count,2)
        self.assertEqual(session.lease.assert_current.await_count,2)
        session.end.assert_awaited_once_with('duration_limit')

    async def test_pre_send_deadline_rechecked_without_waiting_for_watchdog(self):
        session=self.session(max_seconds=600)
        session.closed=False;session.started=100
        for kind in ('input_audio_buffer.append','response.create'):
            with self.subTest(kind=kind):
                session.provider.send.reset_mock()
                with patch('tenant_operator_voice_session.time',SimpleNamespace(monotonic=Mock(return_value=220))):
                    await session._send_provider({'type':kind})
                with patch('tenant_operator_voice_session.time',SimpleNamespace(monotonic=Mock(return_value=699.999))):
                    await session._send_provider({'type':kind})
                self.assertEqual(session.provider.send.await_count,2)
                with patch('tenant_operator_voice_session.time',SimpleNamespace(monotonic=Mock(return_value=700))), \
                     self.assertRaisesRegex(Refused,'session_duration_limit'):
                    await session._send_provider({'type':kind})
                self.assertEqual(session.provider.send.await_count,2)

    async def test_input_remains_bounded_and_per_frame_output_token_limits_unchanged(self):
        session=self.session(max_seconds=600)
        session.closed=False;session.fence=1
        session.input_turn={'manual':True,'stopped':False,'audio_bytes':0}
        session.input_bytes=session.max_input_bytes-480
        await session._receive_client({'type':'audio','pcm':bytes(480)})
        self.assertEqual(session.input_bytes,28_800_000)
        with self.assertRaisesRegex(Refused,'session_input_limit'):
            await session._receive_client({'type':'audio','pcm':bytes(2)})
        self.assertEqual(session.provider.send.await_count,1)
        self.assertEqual(session.MAX_FRAME,9600)
        with self.assertRaisesRegex(Refused,'invalid_pcm_frame'):
            await session._receive_client({'type':'audio','pcm':bytes(9602)})
        self.assertEqual(session.MAX_OUTPUT_BYTES,4_800_000)
        self.assertEqual(wire.OUTPUT_LIMIT,4096)
        self.assertEqual(wire.MAX_WIRE_MESSAGE,262144)
        self.assertEqual(wire.MINIMUM_HOLD,2_130_018)
