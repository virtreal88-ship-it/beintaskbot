import unittest
import uuid
from transcript_draft import TranscriptDraft


class TranscriptDraftTests(unittest.TestCase):
    def setUp(self):self.draft=TranscriptDraft(str(uuid.uuid4()),'synthetic-binding')

    def test_late_transcriptions_keep_original_item_order_and_quality(self):
        for item,role in [('u1','employee'),('a1','copilot'),('u2','employee')]:self.draft.register(item,role)
        self.draft.input_completed('u2',0,'synthetic azərbaycan')
        self.draft.input_completed('u1',0,'synthetic русский')
        turns=self.draft.finalize()
        self.assertEqual([t['turn_sequence'] for t in turns],[0,1,2])
        self.assertEqual([t['text'] for t in turns],['synthetic русский','','synthetic azərbaycan'])
        self.assertEqual(turns[0]['transcript_quality'],'automatic_unverified')
        self.assertEqual(turns[1]['transcript_status'],'unavailable')
        self.assertNotIn('audio',str(turns))

    def test_missing_blank_failed_and_duplicates_are_not_exact(self):
        for item in ('one','two','three'):self.draft.register(item,'employee')
        self.draft.input_failed('one',0);self.draft.input_completed('two',0,'  ')
        with self.assertRaises(ValueError):self.draft.input_completed('one',0,'late result')
        with self.assertRaises(ValueError):self.draft.input_completed('missing',0,'wrong item')
        turns=self.draft.finalize()
        self.assertEqual([t['transcript_status'] for t in turns],['unavailable']*3)
        self.assertEqual([t['text'] for t in turns],['']*3)
        with self.assertRaises(ValueError):self.draft.finalize()

    def test_limit_and_authority_fields(self):
        self.draft.register('u','employee')
        with self.assertRaises(ValueError):self.draft.register('u','copilot')
        with self.assertRaises(ValueError):self.draft.input_completed('u',1,'wrong content')
        with self.assertRaises(ValueError):self.draft.input_completed('u',0,'x'*1601)
        with self.assertRaises(ValueError):self.draft.register('x','system')

    def test_complete_device_playback_retains_generated_text_as_unverified(self):
        self.draft.register('a','copilot')
        with self.assertRaises(ValueError):self.draft.device_playback_complete('a')
        self.draft.assistant_audio_done('a');self.draft.device_playback_complete('a')
        self.draft.assistant_transcript_completed('a','synthetic generated answer')
        turn=self.draft.finalize()[0]
        self.assertEqual(turn['text'],'synthetic generated answer')
        self.assertEqual(turn['transcript_quality'],'automatic_unverified')
        self.assertEqual(self.draft.playback_evidence,{0:'device_playback_complete'})

    def test_interruption_or_missing_playback_never_exports_generated_text(self):
        self.draft.register('a','copilot');self.draft.register('b','copilot')
        self.draft.assistant_transcript_completed('a','unheard')
        self.draft.assistant_audio_done('a');self.draft.device_playback_complete('a');self.draft.interrupted('a')
        self.draft.assistant_transcript_completed('b','unconfirmed')
        self.draft.assistant_audio_done('b')
        turns=self.draft.finalize()
        self.assertEqual([t['text'] for t in turns],['',''])
        self.assertEqual(self.draft.playback_evidence,{})
