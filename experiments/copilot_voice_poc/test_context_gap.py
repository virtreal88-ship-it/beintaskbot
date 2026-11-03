"""Exercise the real text context constructor with synthetic saved turns.

The reproduction asserts the known incompatibility. It is not a safety pass.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'beintaskbot-main'))


class ExistingContextTests(unittest.TestCase):
    def test_current_text_context_strips_voice_incompleteness_and_session_identity(self):
        import tenant_operator_copilot as copilot
        command = {'mode':'chat', 'lead_id':1, 'channel_id':'synthetic-channel',
                   'conversation_id':'synthetic-dialog', 'prompt':'synthetic question',
                   '_agent_id':'synthetic-agent', '_agent_binding':'synthetic-binding'}
        rows = [{'external_id':'synthetic-source','direction':'incoming','body':'synthetic customer message',
                 'message_type':'text','channel':'connect:synthetic-channel','raw':{}}]
        turns = [{'role':'copilot','text':'synthetic incomplete transcript','agent_binding':'synthetic-binding',
                  'voice_session_id':'synthetic-session','transcript_status':'partial'}]
        with patch('tenant_episode_media.ManualExecution'), \
             patch('tenant_ai_agent_run_store.knowledge_context',return_value=[]):
            result = copilot._draft_context('synthetic-tenant',1,command,{}, {'updated_at':'synthetic'},
                {'api_key':'synthetic-placeholder'},'synthetic-model',{},rows,turns)
        projected = result['extra_context']['prior_internal_copilot_turns'][0]
        self.assertNotIn('voice_session_id', projected)
        self.assertNotIn('transcript_status', projected)
        self.assertEqual(projected['text'], 'synthetic incomplete transcript')
