"""Deterministic local protocol tests, without a provider connection."""
import asyncio
import base64
import unittest
from unittest.mock import AsyncMock

from tenant_operator_voice_session import Session, Refused, ProviderResponseRejected
from tenant_operator_voice_gateway import _provider_failure_fields, _log_playback_queue_latency, make_handler


class StalePlaybackAckTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        s=self.s=object.__new__(Session)
        s.closed=False;s.scope=('tenant','actor','channel','dialog');s.fence=1
        s.authority=type('Authority',(),{'check':AsyncMock()})();s.lease=type('Lease',(),{'assert_current':AsyncMock()})()
        s.lock=asyncio.Lock();s._send_client=AsyncMock();s._dispatch_pending=AsyncMock();s._notify_listening_if_idle=AsyncMock()
        s.response_inflight=True;s.provider_response_id='response-1';s.finished_response_ids=set()
        s.assistant_response_ids={};s.truncated_playback_items=set();s.cancel_requested=False
        s.item=None;s.produced=0;s.interrupt_pending=False;s.output_finished=False;s.greeting_pending=False
        s.transcript=None
        s.truncate_pending=None;s.truncate_pending_audio_end_ms=None
        self.audio={'type':'response.output_audio.delta','response_id':'response-1','item_id':'item-1',
                    'content_index':0,'delta':base64.b64encode(bytes(2)).decode()}

    async def test_confirmed_truncation_is_only_stale_ack_accepted_across_response_change(self):
        s=self.s
        await s._receive_provider(self.audio)
        s.interrupt_pending=True;s.truncate_pending='item-1';s.truncate_pending_audio_end_ms=0;s.cancel_requested=True
        with self.assertRaisesRegex(Refused,'invalid_truncation_confirmation'):
            await s._receive_provider({'type':'conversation.item.truncated','item_id':'item-1','content_index':0,'audio_end_ms':1})
        self.assertFalse(s.truncated_playback_items)
        await s._receive_provider({'type':'conversation.item.truncated','item_id':'item-1','content_index':0,'audio_end_ms':0})
        s.cancel_requested=False;s.provider_response_id='response-2'
        await s._receive_provider({**self.audio,'response_id':'response-2','item_id':'item-2'})
        await s._receive_client({'type':'playback_scheduled','item_id':'item-1','context_state':'running'})
        self.assertEqual(s.item,'item-2')
        for invalid in ('unknown',[],{},None,42,'','x'*129):
            with self.subTest(value=repr(invalid)), self.assertRaisesRegex(Refused,'invalid_playback_start_ack'):
                await s._receive_client({'type':'playback_scheduled','item_id':invalid,'context_state':'running'})
        for invalid in ('bad',[],{},None,42):
            with self.subTest(context=repr(invalid)), self.assertRaisesRegex(Refused,'invalid_playback_start_ack'):
                await s._receive_client({'type':'playback_scheduled','item_id':'item-1','context_state':invalid})
        with self.assertRaisesRegex(Refused,'assistant_item_response_conflict'):
            await s._receive_provider({**self.audio,'response_id':'response-2'})

    async def test_audio_error_fields_are_codes_and_boolean_matches_only(self):
        for response_id,expected,match in [('different','unreserved_response',False),('response-1','audio_after_completion',True)]:
            self.s.output_finished=True
            event={**self.audio,'response_id':response_id}
            with self.assertRaises(ProviderResponseRejected) as caught:
                await self.s._receive_provider(event)
            fields=_provider_failure_fields('provider_event',event,caught.exception)
            self.assertEqual(fields,(expected,'provider_event','response.output_audio.delta','none','none',True,match))
            self.assertNotIn(response_id,repr(fields))

    async def test_audio_after_done_and_overlapping_item_fail_closed_with_distinct_codes(self):
        s=self.s
        await s._receive_provider(self.audio)
        overlap={**self.audio,'item_id':'item-2'}
        with self.assertRaises(ProviderResponseRejected) as caught:
            await s._receive_provider(overlap)
        self.assertEqual(_provider_failure_fields('provider_event',overlap,caught.exception)[0],
                         'overlapping_output_items')
        self.setUp();s=self.s
        await s._receive_provider(self.audio)
        await s._receive_provider({'type':'response.output_audio.done','response_id':'response-1',
                                   'item_id':'item-1','content_index':0})
        with self.assertRaises(ProviderResponseRejected) as caught:
            await s._receive_provider(self.audio)
        self.assertEqual(_provider_failure_fields('provider_event',self.audio,caught.exception)[0],
                         'audio_after_completion')

    def test_queue_diagnostic_excludes_payload(self):
        with self.assertLogs('tenant_operator_voice_session',level='INFO') as logs:
            _log_playback_queue_latency({'type':'playback_scheduled','item_id':'private id'},0)
            _log_playback_queue_latency({'type':'audio','pcm':'private audio'},0)
        self.assertEqual(len(logs.output),1)
        self.assertIn('stage=playback_ack_server_queue duration_ms=',logs.output[0])
        self.assertNotIn('private',repr(logs.output))

    async def test_gateway_preserves_audio_ack_end_order_and_logs_only_duration(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        seen=[]
        class FakeSession:
            def __init__(self):self.closed=False;self.provider=object()
            async def start(self):pass
            async def receive_client(self,event):seen.append(event['type'])
            async def end(self,reason):self.closed=True
        session=FakeSession()
        async def authenticate(request):return ('tenant','actor','channel','dialog')
        app=web.Application()
        app.router.add_get('/voice',make_handler(allowed_origins=['https://test.local'],
            authenticate=authenticate,build_session=lambda scope,client:session))
        client=TestClient(TestServer(app));await client.start_server()
        try:
            with self.assertLogs('tenant_operator_voice_session',level='INFO') as logs:
                ws=await client.ws_connect('/voice',headers={'Origin':'https://test.local'})
                await ws.send_bytes(b'private audio')
                await ws.send_json({'type':'playback_scheduled','item_id':'private id','context_state':'running'})
                await ws.send_json({'type':'end'})
                await ws.receive()
            self.assertEqual(seen,['audio','playback_scheduled','end'])
            self.assertEqual(sum('stage=playback_ack_server_queue' in line for line in logs.output),1)
            self.assertNotIn('private',repr(logs.output))
        finally:
            await client.close()
