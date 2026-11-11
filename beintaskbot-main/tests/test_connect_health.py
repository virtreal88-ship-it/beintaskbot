from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch, AsyncMock, Mock
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tenant_connect_health as health

TENANT='00000000-0000-4000-8000-000000000123'

NOW = datetime(2026,11,11,12,tzinfo=timezone.utc)

class PolicyTests(unittest.TestCase):
    def observe(self, state, status, seconds=0):
        return health.observe(state,status,(NOW+timedelta(seconds=seconds)).isoformat(),NOW+timedelta(seconds=seconds))

    def test_disconnect_debounce(self):
        state,_=self.observe({},'disconnected')
        self.assertFalse(health.due(state,NOW+timedelta(seconds=89)))
        self.assertTrue(health.due(state,NOW+timedelta(seconds=90)))

    def test_connecting_longer_debounce(self):
        state,_=self.observe({},'connecting')
        self.assertFalse(health.due(state,NOW+timedelta(seconds=299)))
        self.assertTrue(health.due(state,NOW+timedelta(seconds=300)))

    def test_transient_reconnect_has_no_notice(self):
        state,_=self.observe({},'connecting')
        state,recovery=self.observe(state,'connected',30)
        self.assertIsNone(recovery)
        self.assertFalse(health.due(state,NOW+timedelta(days=1)))

    def test_repeated_bad_events_do_not_postpone_alert(self):
        state,_=self.observe({},'disconnected')
        again,_=self.observe(state,'disconnected',80)
        self.assertEqual(state['incidentId'],again['incidentId'])
        self.assertTrue(health.due(again,NOW+timedelta(seconds=90)))

    def test_silence_never_causes_outage(self):
        state,_=self.observe({},'connected')
        self.assertFalse(health.due(state,NOW+timedelta(days=365)))
        self.assertFalse(health.due({},NOW))

    def test_unknown_is_neither_failure_nor_recovery(self):
        state,_=self.observe({},'disconnected'); state['alertQueued']=True; state['alertAttempted']=True
        state,recovery=self.observe(state,'unknown',100)
        self.assertIsNone(recovery)
        self.assertFalse(health.due(state,NOW+timedelta(days=1)))
        state,recovery=self.observe(state,'connected',110)
        self.assertIsNotNone(recovery)

    def test_out_of_order_and_duplicate_ignored(self):
        state,_=self.observe({},'connected',20)
        for status,seconds in (('disconnected',0),('connected',20)):
            again,recovery=self.observe(state,status,seconds)
            self.assertEqual(state,again); self.assertIsNone(recovery)

    def test_unknown_retires_unattempted_queue_and_restarts_debounce(self):
        state,_=self.observe({},'error');state['alertQueued']=True
        first=state['incidentId']
        state,_=self.observe(state,'unknown',100)
        self.assertIsNone(state['incidentId']);self.assertFalse(state['alertQueued'])
        state,_=self.observe(state,'error',101)
        self.assertNotEqual(first,state['incidentId']);self.assertEqual(state['incidentGeneration'],2)
        self.assertFalse(health.due(state,NOW+timedelta(seconds=190)))
        self.assertTrue(health.due(state,NOW+timedelta(seconds=191)))

    def test_equal_timestamp_conflict_is_unknown_until_newer_observation(self):
        for first,last in [('disconnected','connected'),('connected','disconnected')]:
            state,_=self.observe({},first)
            state,recovery=self.observe(state,last)
            self.assertEqual(state['status'],'unknown');self.assertIsNone(recovery)
            self.assertIn('ambiguousTimestamp',state)
            self.assertFalse(health.due(state,NOW+timedelta(days=1)))
            state,_=self.observe(state,'connected',1)
            self.assertEqual(state['status'],'connected');self.assertNotIn('ambiguousTimestamp',state)

    def test_one_alert_and_recovery_per_incident(self):
        state,_=self.observe({},'error'); original=state['incidentId']; state['alertQueued']=True
        self.assertFalse(health.due(state,NOW+timedelta(days=1)))
        state,recovery=self.observe(state,'connected',100)
        self.assertEqual(original,recovery)
        state,recovery=self.observe(state,'connected',101)
        self.assertIsNone(recovery)
        state,_=self.observe(state,'disconnected',102)
        self.assertNotEqual(original,state['incidentId'])

    def test_new_incident_invalidates_old_recovery_marker(self):
        state,_=self.observe({},'error'); state['alertQueued']=True
        first=state['incidentId']
        state,_=self.observe(state,'connected',100)
        self.assertEqual(state['recoveredIncidentId'],first)
        state,_=self.observe(state,'connected',101)
        self.assertEqual(state['recoveredIncidentId'],first)
        state,_=self.observe(state,'error',102)
        self.assertIsNone(state['recoveredIncidentId'])
        self.assertEqual(state['incidentGeneration'],2)
        second=state['incidentId'];state['alertQueued']=True
        state,recovery=self.observe(state,'connected',200)
        self.assertEqual(recovery,second)
        self.assertEqual(state['recoveredIncidentId'],second)
        self.assertNotEqual(first,second)

    def test_bad_times_and_status_rejected(self):
        for status,when in [('open',NOW.isoformat()),('connected','2026-11-11T12:00:00'),('connected',(NOW+timedelta(hours=1)).isoformat())]:
            with self.assertRaises(ValueError):health.observe({},status,when,NOW)

    def test_json_restart_preserves_dedup(self):
        state,_=self.observe({},'error');state['alertQueued']=True
        restored=json.loads(json.dumps(state))
        self.assertFalse(health.due(restored,NOW+timedelta(days=1)))
        self.assertEqual(state['incidentId'],self.observe(restored,'error',100)[0]['incidentId'])

class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_does_not_prepare_or_send(self):
        with patch.dict('os.environ',{},clear=True), patch.object(health,'prepare') as prepare:
            bot=AsyncMock(); await health.deliver(bot,Mock())
            prepare.assert_not_called();bot.send_message.assert_not_called()

    async def test_uncertain_delivery_checkpoint_no_retry(self):
        item={'send':True,'tenant_id':TENANT,'recipient_id':42,'kind':'down','tenant_name':'Tenant','channel_name':'Channel'}
        bot=AsyncMock();bot.send_message.side_effect=TimeoutError()
        with patch.dict('os.environ',{'CONNECT_HEALTH_ALERTS_ENABLED':'true','CONNECT_HEALTH_ALERT_TENANT_IDS':TENANT}), patch.object(health,'prepare'), patch.object(health,'claim',side_effect=[item,None]), patch.object(health,'finish') as finish:
            await health.deliver(bot,Mock())
        self.assertEqual(bot.send_message.await_count,1)
        self.assertEqual(finish.call_args.args[1].status,'unknown')
        self.assertEqual(bot.send_message.call_args.kwargs['chat_id'],42)

    async def test_allowlist_removed_after_claim_before_send(self):
        import os
        item={'send':True,'tenant_id':TENANT,'recipient_id':42}
        called=[False]
        def changing_claim():
            if called[0]:return None
            called[0]=True
            os.environ['CONNECT_HEALTH_ALERT_TENANT_IDS']=''
            return item
        bot=AsyncMock()
        with patch.dict('os.environ',{'CONNECT_HEALTH_ALERTS_ENABLED':'true','CONNECT_HEALTH_ALERT_TENANT_IDS':TENANT}), patch.object(health,'prepare'), patch.object(health,'claim',side_effect=changing_claim), patch.object(health,'finish') as finish:
            await health.deliver(bot,Mock())
        bot.send_message.assert_not_called()
        finish.assert_called_once_with(item,skipped=True)

    async def test_missing_allowlist_blocks_sender(self):
        with patch.dict('os.environ',{'CONNECT_HEALTH_ALERTS_ENABLED':'true','CONNECT_HEALTH_ALERT_TENANT_IDS':''}), patch.object(health,'prepare') as prepare:
            bot=AsyncMock();await health.deliver(bot,Mock())
            prepare.assert_not_called();bot.send_message.assert_not_called()

if __name__=='__main__':unittest.main()
