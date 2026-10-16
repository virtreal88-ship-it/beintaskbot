"""Regression coverage for operator-visible AI dialogue control state."""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = MagicMock()
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg'] = psycopg
    sys.modules['psycopg.rows'] = rows


import tenant_ai_agents_store as store


class ConversationControlStateTests(unittest.TestCase):
    def _row(self, **changes):
        row = {
            'provider': 'connect', 'state': 'active', 'reason': '',
            'agent_id': '00000000-0000-0000-0000-000000000102',
            'agent_enabled': True, 'company_enabled': True,
            'company_emergency_stop': False, 'tenant_status': 'active',
            'latest_job_state': 'sent', 'latest_job_error': '',
            'latest_event_state': 'completed', 'latest_event_error': '',
        }
        row.update(changes)
        return row

    @patch.object(store, 'runtime_flag', return_value=True)
    def test_active_human_pause_and_blocked_states_are_presented_unambiguously(self, _flag):
        active = store._decorate_conversation_state(self._row())
        self.assertEqual(active['display_state'], 'active')
        self.assertFalse(active['resume_allowed'])

        paused = store._decorate_conversation_state(self._row(state='paused', reason='manager_pause'))
        self.assertEqual(paused['display_state'], 'paused')
        self.assertTrue(paused['resume_allowed'])
        self.assertEqual(paused['display_reason'], 'manager_pause')

        human = store._decorate_conversation_state(self._row(state='human', reason='manager_replied'))
        self.assertEqual(human['display_state'], 'human')
        self.assertTrue(human['resume_allowed'])
        self.assertEqual(human['display_reason'], 'manager_replied')

        blocked = store._decorate_conversation_state(self._row(company_emergency_stop=True))
        self.assertEqual(blocked['display_state'], 'blocked')
        self.assertEqual(blocked['display_reason'], 'emergency_stop')

    @patch.object(store, 'runtime_flag', return_value=True)
    def test_resume_is_hidden_when_an_explicit_human_or_pause_state_has_a_mandatory_block(self, _flag):
        for change, reason in (
            ({'company_enabled': False}, 'company_ai_disabled'),
            ({'agent_enabled': False}, 'agent_disabled'),
            ({'company_emergency_stop': True}, 'emergency_stop'),
        ):
            with self.subTest(change=change):
                item = store._decorate_conversation_state(self._row(state='human', reason='manager_replied', **change))
                self.assertEqual(item['display_state'], 'human')
                self.assertFalse(item['resume_allowed'])
                self.assertEqual(item['resume_block_reason'], reason)
                self.assertEqual(item['display_reason'], 'manager_replied')

    @patch.object(store, 'runtime_flag', return_value=False)
    def test_server_kill_switch_is_a_visible_blocker(self, _flag):
        item = store._decorate_conversation_state(self._row(state='paused'))
        self.assertFalse(item['resume_allowed'])
        self.assertEqual(item['resume_block_reason'], 'server_ai_disabled')


if __name__ == '__main__':
    unittest.main()
