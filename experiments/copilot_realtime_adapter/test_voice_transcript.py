import unittest
import uuid

from session import Session as _Session  # Adds the repository's production module path.
from tenant_operator_voice_transcript import TranscriptDraft, transcription_cost_microusd


class VoiceTranscriptTests(unittest.TestCase):
    def test_duration_receipt_is_rounded_and_bounded(self):
        self.assertEqual(transcription_cost_microusd({'type':'duration','seconds':1}),75)
        self.assertEqual(transcription_cost_microusd({'type':'duration','seconds':0.01}),1)
        self.assertEqual(transcription_cost_microusd({'type':'duration','seconds':120}),9_000)
        self.assertIsNone(transcription_cost_microusd({'type':'duration','seconds':120.01}))
        self.assertIsNone(transcription_cost_microusd({'type':'tokens','seconds':1}))
        self.assertIsNone(transcription_cost_microusd({'type':'duration','seconds':float('nan')}))

    def test_out_of_order_transcripts_keep_provider_item_order_and_dedupe(self):
        draft=TranscriptDraft(str(uuid.uuid4()),'binding')
        for item,role in [('user-1','employee'),('assistant-1','copilot'),('user-2','employee')]:
            draft.register(item,role)
        self.assertEqual(draft.input_completed('user-2',0,'synthetic AZ'),'completed')
        self.assertEqual(draft.input_completed('user-1',0,'synthetic RU'),'completed')
        self.assertEqual(draft.input_completed('user-1',0,'synthetic RU'),'duplicate')
        entries=draft.finalize()
        self.assertEqual([item['turn_sequence'] for item in entries],[0,1,2])
        self.assertEqual([item['text'] for item in entries],['synthetic RU','','synthetic AZ'])
        self.assertEqual(entries[1]['transcript_status'],'unavailable')

    def test_interrupted_or_unplayed_reply_is_never_persisted(self):
        draft=TranscriptDraft(str(uuid.uuid4()),'binding')
        draft.register('assistant-1','copilot')
        draft.assistant_transcript_completed('assistant-1','synthetic unplayed answer')
        draft.assistant_audio_done('assistant-1')
        draft.interrupted('assistant-1')
        draft.interrupted('assistant-1')
        entry=draft.finalize()[0]
        self.assertEqual(entry['text'],'')
        self.assertEqual(entry['transcript_status'],'unavailable')

    def test_played_reply_is_stored_as_automatic_unverified_not_heard(self):
        draft=TranscriptDraft(str(uuid.uuid4()),'binding')
        draft.register('assistant-1','copilot')
        draft.assistant_audio_done('assistant-1')
        draft.assistant_transcript_completed('assistant-1','synthetic generated answer')
        draft.device_playback_complete('assistant-1')
        self.assertEqual(draft.device_playback_complete('assistant-1'),'duplicate')
        entry=draft.finalize()[0]
        self.assertEqual(entry['transcript_status'],'complete')
        self.assertEqual(entry['transcript_quality'],'automatic_unverified')
        self.assertNotIn('audio',entry)


if __name__=='__main__':
    unittest.main()
