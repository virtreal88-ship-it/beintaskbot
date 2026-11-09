"""Latency telemetry must never contain payloads or change response routing."""
import asyncio
import base64
import logging
import time
import unittest
from unittest.mock import AsyncMock

from tenant_operator_voice_session import Session, _log_voice_latency


class LatencyDiagnosticsTest(unittest.IsolatedAsyncioTestCase):
    def test_stage_and_duration_are_allowlisted_and_bounded(self):
        with self.assertLogs('tenant_operator_voice_session',level='INFO') as logs:
            _log_voice_latency('talk_end_to_commit_ack',1.0,1.25)
            _log_voice_latency('response_to_first_audio',0.0,1e100)
            for stage,start,end in [('private phrase',1,2),([],1,2),('dispatch_checks',float('nan'),1),
                                    ('dispatch_checks',2,1),('dispatch_checks',True,2)]:
                _log_voice_latency(stage,start,end)
        self.assertEqual(len(logs.output),2)
        self.assertIn('stage=talk_end_to_commit_ack duration_ms=250',logs.output[0])
        self.assertIn('stage=response_to_first_audio duration_ms=600000',logs.output[1])
        self.assertNotIn('private phrase',str(logs.output))

    async def test_commit_and_playback_emit_only_once_without_ids_or_payloads(self):
        s=object.__new__(Session)
        s.closed=False;s.scope=('private tenant',7,'private channel','private conversation')
        s.authority=type('A',(),{'check':AsyncMock()})();s.lease=type('L',(),{'assert_current':AsyncMock()})();s.fence=1
        s.lock=asyncio.Lock();s._send_provider=AsyncMock();s._send_client=AsyncMock()
        s._accept_committed_input=AsyncMock();s.muted=False;s.seen_input_item_ids=set();s.transcript=None
        s.input_turn={'sequence':1,'stopped':False,'item_id':None,'manual':True,'audio_bytes':2}
        s.response_inflight=True;s.provider_response_id='private response';s.finished_response_ids=set()
        s.assistant_response_ids={};s.truncated_playback_items=set()
        s.cancel_requested=False;s.item=None;s.produced=0;s.interrupt_pending=False;s.output_finished=False
        s.greeting_pending=False;s._latency_response_sent_at=time.monotonic()
        s._latency_first_audio_at=None;s._latency_first_audio_item=None
        s._dispatch_pending=AsyncMock();s.response_pending=False;s.pending_input_item_id=None
        with self.assertLogs('tenant_operator_voice_session',level=logging.INFO) as logs:
            await s._receive_client({'type':'talk_end'})
            await s._receive_provider({'type':'input_audio_buffer.committed','item_id':'private input'})
            event={'type':'response.output_audio.delta','response_id':'private response',
                   'item_id':'private output','content_index':0,'delta':base64.b64encode(b'\x01\x02').decode()}
            await s._receive_provider(event)
            await s._receive_provider(event)
            await s._receive_client({'type':'playback_scheduled','item_id':'private output','context_state':'running'})
            await s._receive_client({'type':'playback_scheduled','item_id':'private output','context_state':'running'})
            await s._receive_provider({'type':'response.output_audio.done','response_id':'private response',
                                       'item_id':'private output','content_index':0})
            await s._receive_client({'type':'played_all','item_id':'private output'})
        emitted='\n'.join(logs.output)
        self.assertEqual(emitted.count('stage=talk_end_to_commit_ack'),1)
        self.assertEqual(emitted.count('stage=response_to_first_audio'),1)
        self.assertEqual(emitted.count('stage=first_audio_to_playback'),1)
        self.assertEqual(emitted.count('stage=output_done_to_played_all'),1)
        self.assertNotIn('private',emitted)
        self.assertNotIn(event['delta'],emitted)
        self.assertEqual(s._send_client.await_count,3)

    async def test_unacknowledged_output_reports_elapsed_time_on_session_end(self):
        s=object.__new__(Session);s._opening=False;s._end_task=None;s.closed=False;s.watchdog=None
        s.output_finished=True;s.interrupt_pending=False;s._latency_output_done_at=time.monotonic()-1
        s._finish_end=AsyncMock(return_value=True)
        with self.assertLogs('tenant_operator_voice_session',level='INFO') as logs:
            self.assertTrue(await s.end('user_end'))
        self.assertTrue(any('stage=output_done_pending_at_end' in line for line in logs.output))
        self.assertIsNone(s._latency_output_done_at)

    async def test_interrupted_output_is_not_reported_as_pending_playback(self):
        s=object.__new__(Session);s._opening=False;s._end_task=None;s.closed=False;s.watchdog=None
        s.output_finished=True;s.interrupt_pending=True;s._latency_output_done_at=time.monotonic()-1
        s._finish_end=AsyncMock(return_value=True)
        with self.assertLogs('tenant_operator_voice_session',level='INFO') as logs:
            self.assertTrue(await s.end('user_end'))
        self.assertFalse(any('stage=output_done_pending_at_end' in line for line in logs.output))

    async def test_dispatch_checks_measure_the_existing_gate(self):
        s=object.__new__(Session);s.queued_input_item_ids=[];s.pending_input_item_id='private input'
        s.response_pending=True;s.interrupt_pending=False;s.truncate_pending=None;s.response_inflight=False
        s.current_reservation=None;s.item=None;s.closed=False;s.response_number=0;s.pilot_policy=None
        s.scope=('tenant',7,'channel','conversation');s.session_id='private session';s.executor='test'
        s.context=type('C',(),{'revalidate':AsyncMock()})();s.authority=type('A',(),{'check':AsyncMock()})()
        s.lease=type('L',(),{'assert_current':AsyncMock()})();s.fence=1
        s.ledger=type('D',(),{'reserve':AsyncMock(return_value={}), 'claim':AsyncMock(return_value={})})()
        s._check_receipt=lambda *_:None;s.provider=type('P',(),{'bind_claim':AsyncMock()})()
        s._send_provider=AsyncMock()
        s._latency_commit_times={'private input':time.monotonic()}
        with self.assertLogs('tenant_operator_voice_session',level='INFO') as logs:
            await s._dispatch_pending()
        self.assertTrue(any('stage=dispatch_checks duration_ms=' in line for line in logs.output))
        self.assertTrue(any('stage=commit_ack_to_dispatch duration_ms=' in line for line in logs.output))
        self.assertNotIn('private',str(logs.output))
        self.assertEqual(s._latency_commit_times,{})
        s._send_provider.assert_awaited_once_with({'type':'response.create'})


if __name__=='__main__':unittest.main()
