"""Focused regressions for the provider-independent Connect AI path."""
import inspect
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo


if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = MagicMock()
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg'] = psycopg
    sys.modules['psycopg.rows'] = rows


import tenant_ai_agent_actions as actions
import tenant_ai_agent_connect as connect_ai
import tenant_ai_agent_worker as worker
import tenant_ai_agents_store as agents_store
from tenant_ai_agent_schedule import is_open, next_open
from tenant_ai_agents_policy import agent as validate_agent
from tenant_chat_ai_policy import history
from tenant_linear_policy import TenantLinearError
from tenant_web_routes import TenantWebContext, ai_agent_routes


TENANT = '00000000-0000-0000-0000-000000000101'
CHANNEL = '00000000-0000-0000-0000-000000000102'
DRAFT = '00000000-0000-0000-0000-000000000103'


class Cursor:
    def __init__(self, *, ones=()):
        self.ones = list(ones)
        self.executed = []
        self.connection = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.ones.pop(0) if self.ones else None


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        cursor.connection = self
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True


class ConnectAiFlowTests(unittest.TestCase):
    def test_overnight_schedule_uses_the_shift_start_day_and_defers_after_close(self):
        schedule = {'timezone': 'Asia/Baku', 'days': [0], 'start': '22:00', 'end': '06:00'}
        baku = ZoneInfo('Asia/Baku')
        # Monday shift is still open at Tuesday 02:00, despite Tuesday not
        # being selected. It closes precisely at 06:00.
        self.assertTrue(is_open(schedule, now=datetime(2026, 10, 19, 22, 0, tzinfo=baku)))
        self.assertTrue(is_open(schedule, now=datetime(2026, 10, 20, 2, 0, tzinfo=baku)))
        closed = datetime(2026, 10, 20, 6, 0, tzinfo=baku)
        self.assertFalse(is_open(schedule, now=closed))
        self.assertEqual(next_open(schedule, now=closed),
                         datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(validate_agent({'schedule': schedule})['schedule'], schedule)
        with self.assertRaises(TenantLinearError):
            validate_agent({'schedule': {**schedule, 'end': '22:00'}})

    def test_connect_worker_has_a_second_schedule_check_after_generation(self):
        source = inspect.getsource(worker.process_connect_one)
        self.assertIn('final_open_at = _next_open', source)
        self.assertIn('defer_job, job, final_open_at, reservation', source)
        self.assertLess(source.index('final_open_at = _next_open'), source.index("phase = 'connect_send'"))

    def test_connect_new_inquiry_and_phone_trigger_are_evidence_based(self):
        triggers = [
            {'id': 'new', 'trigger_type': 'new_inquiry', 'condition': {}, 'actions': []},
            {'id': 'phone', 'trigger_type': 'phone_received', 'condition': {}, 'actions': []},
            {'id': 'phrase', 'trigger_type': 'phrase', 'condition': {'phrase': 'support|help'}, 'actions': []},
        ]
        matched = actions.matching_triggers(triggers, 'Need HELP please', first_inquiry=True,
                                           phone='994102135103')
        self.assertEqual({item['id'] for item in matched}, {'new', 'phone', 'phrase'})
        self.assertEqual(connect_ai._whatsapp_phone('994102135103@s.whatsapp.net'), '994102135103')
        self.assertEqual(connect_ai._whatsapp_phone('12345-678@g.us'), '')

    def test_unavailable_voice_keeps_the_later_text_in_model_context(self):
        lines, missing = history([
            {'external_id': 'voice-1', 'direction': 'incoming', 'body': '', 'message_type': 'voice',
             'media_url': '', 'happened_at': '2026-10-16T00:00:00+00:00'},
            {'external_id': 'text-2', 'direction': 'incoming', 'body': 'My internet is down', 'message_type': 'text',
             'media_url': '', 'happened_at': '2026-10-16T00:01:00+00:00'},
        ], {})
        self.assertEqual(missing, 1)
        self.assertIn('Audio unavailable', lines[0]['text'])
        self.assertEqual(lines[-1]['text'], 'My internet is down')
        # The queue replaces a prior job with the newer event rather than
        # leaving a failed attachment at the head of a per-chat queue.
        source = inspect.getsource(connect_ai.enqueue)
        self.assertIn("state='queued'", source)
        self.assertIn('event_fingerprint=EXCLUDED.event_fingerprint', source)

    def test_connect_handoff_after_accepted_reply_moves_dialogue_to_human(self):
        cursor = Cursor(ones=[{'revision': 9}])
        connection = Connection(cursor)
        job = {'tenant_id': TENANT, 'agent_id': CHANNEL, 'conversation_id': 'client@s.whatsapp.net',
               'lease_token': 'lease-a', 'revision': 3, 'event_fingerprint': 'event-a', 'provider':'connect', 'channel_id':CHANNEL}
        with patch.object(connect_ai, '_connect', return_value=connection), \
             patch.object(connect_ai, 'ensure_schema'), \
             patch('tenant_support_inbox_store.handoff_ticket') as inbox, \
             patch('tenant_support_inbox_store.handoff_recorded', return_value=False), \
             patch('tenant_support_inbox_store.summarize_created'):
            self.assertTrue(connect_ai.finish_sent_after_reply_handoff(job, {'run_id': DRAFT}, 'message-a'))
        state_sql = next(sql for sql, _params in cursor.executed if "state='human'" in sql)
        self.assertIn('trigger_reply_sent_then_handoff', state_sql)
        self.assertTrue(any("state='sent'" in sql for sql, _params in cursor.executed))

    def test_connect_test_draft_cannot_be_promoted_to_a_real_whatsapp_send(self):
        cursor = Cursor(ones=[{'provider': 'connect', 'test_mode': 'true'}])
        connection = Connection(cursor)
        profile = {'tenant_id': TENANT, 'telegram_id': 7}
        with patch.object(agents_store, '_connect', return_value=connection), \
             patch.object(agents_store, 'ensure_schema'), \
             patch.object(agents_store, '_authorize', return_value=(TENANT, 7)):
            with self.assertRaises(TenantLinearError):
                agents_store.reserve_draft_send(profile, DRAFT)
        self.assertFalse(any("state='sending'" in sql for sql, _params in cursor.executed))

    def test_accepted_connect_manual_draft_handoff_uses_connect_state_not_kommo(self):
        cursor = Cursor(ones=[{'provider': 'connect','payload':{'channel_id':CHANNEL},'source_fingerprint':'event-a'}, {'chat_id': 'client@s.whatsapp.net', 'handoff_after_send': True}])
        connection = Connection(cursor)
        with patch.object(agents_store, '_connect', return_value=connection), \
             patch.object(agents_store, 'ensure_schema'), \
             patch('tenant_support_inbox_store.handoff_ticket'), \
             patch('tenant_support_inbox_store.handoff_recorded', return_value=False), \
             patch('tenant_support_inbox_store.summarize_created'):
            self.assertEqual(agents_store.finish_draft_send(TENANT, DRAFT, 'accepted'), 'approved')
        transition = next(params for sql, params in cursor.executed if "trigger_reply_sent_then_handoff" in sql)
        self.assertEqual(transition[1], 'connect')

    def test_pause_route_is_mounted_and_connect_draft_send_has_final_state_guard(self):
        context = TenantWebContext(member=lambda _request: None, origin='https://crm.pro.az', logger=MagicMock(),
                                  crm_request=MagicMock(), wait_slot=MagicMock(), bot=lambda: None)
        routes = {(method, path) for method, path, _handler in ai_agent_routes(context)}
        self.assertIn(('POST', '/api/platform/ai/conversations/{conversation_id}/pause'), routes)
        api_source = Path(__file__).resolve().parents[1].joinpath('tenant_ai_agents_api.py').read_text(encoding='utf-8-sig')
        self.assertIn('send_ai_reply_if_active', api_source)
        self.assertIn("reserved['expected_revision']", api_source)
        self.assertIn("finish_draft_send,reserved['tenant_id'],reserved['id'],'accepted'", api_source)

    def test_pause_is_diagnosed_as_pause_not_as_human_handoff(self):
        cursor = Cursor()
        connection = Connection(cursor)
        profile = {'tenant_id': TENANT, 'telegram_id': 7}
        with patch.object(agents_store, '_connect', return_value=connection), \
             patch.object(agents_store, 'ensure_schema'), \
             patch.object(agents_store, '_authorize', return_value=(TENANT, 7)), \
             patch.object(agents_store, '_conversation_provider_allowed', return_value=CHANNEL):
            result = agents_store.set_conversation_state(
                profile, 'client@s.whatsapp.net', 'paused', 'manager_pause', 'connect', CHANNEL)
        self.assertEqual(result['state'], 'paused')
        skipped = [params for sql, params in cursor.executed
                   if 'saas_tenant_webhook_events' in sql and "state='skipped'" in sql]
        jobs = [params for sql, params in cursor.executed
                if 'saas_ai_agent_jobs' in sql and "state='skipped'" in sql]
        self.assertTrue(skipped)
        self.assertTrue(jobs)
        self.assertEqual(skipped[0][0], 'manager_pause')
        self.assertEqual(jobs[0][0], 'manager_pause')


if __name__ == '__main__':
    unittest.main()
