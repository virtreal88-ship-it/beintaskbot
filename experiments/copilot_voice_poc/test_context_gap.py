"""Exercise the real text context constructor with synthetic saved turns."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'beintaskbot-main'))


class ExistingContextTests(unittest.TestCase):
    def test_text_context_preserves_voice_incompleteness_and_session_identity(self):
        import tenant_operator_copilot as copilot
        command = {'mode':'chat', 'lead_id':1, 'channel_id':'synthetic-channel',
                   'conversation_id':'synthetic-dialog', 'prompt':'synthetic question',
                   '_agent_id':'synthetic-agent', '_agent_binding':'synthetic-binding'}
        rows = [{'external_id':'synthetic-source','direction':'incoming','body':'synthetic customer message',
                 'message_type':'text','channel':'connect:synthetic-channel','raw':{}}]
        turns = [{'role':'copilot','text':'synthetic incomplete transcript','agent_binding':'synthetic-binding',
                  'voice_session_id':'00000000-0000-4000-8000-000000000001','transcript_status':'partial'}]
        with patch('tenant_episode_media.ManualExecution'), \
             patch('tenant_ai_agent_run_store.knowledge_context',return_value=[]):
            result = copilot._draft_context('synthetic-tenant',1,command,{}, {'updated_at':'synthetic'},
                {'api_key':'synthetic-placeholder'},'synthetic-model',{},rows,turns)
        projected = result['extra_context']['prior_internal_copilot_turns'][0]
        self.assertEqual(projected['voice_session_id'], '00000000-0000-4000-8000-000000000001')
        self.assertEqual(projected['transcript_status'], 'partial')
        self.assertEqual(projected['text'], 'synthetic incomplete transcript')

    def test_projection_preserves_legacy_text_and_filters_unknown_metadata(self):
        from tenant_operator_copilot import _project_prior_turn
        legacy={'role':'employee','text':'legacy','agent_binding':'binding','unknown':'secret'}
        self.assertEqual(_project_prior_turn(legacy),{'role':'employee','text':'legacy','agent_binding':'binding'})
        unknown=_project_prior_turn({**legacy,'transcript_status':'unexpected','voice_session_id':'not-a-uuid',
                                    'turn_sequence':True,'transcript_quality':'unexpected'})
        self.assertEqual(unknown['transcript_status'],'unknown')
        self.assertEqual(unknown['transcript_quality'],'unknown')
        self.assertEqual(unknown['text'],'')
        self.assertNotIn('voice_session_id',unknown)
        self.assertNotIn('turn_sequence',unknown)
        self.assertNotIn('unknown',unknown)
