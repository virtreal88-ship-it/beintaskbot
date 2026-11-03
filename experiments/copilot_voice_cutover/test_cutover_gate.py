import os
import unittest
from unittest.mock import patch

from cutover_gate import VoiceWriteUnavailable, observe_receipts, require_voice_write_ready


class Cursor:
    def __init__(self, row): self.row = row; self.queries = []
    def execute(self, query): self.queries.append(query)
    def fetchone(self): return self.row


class CutoverGateTests(unittest.TestCase):
    def test_no_configuration_or_caller_claim_can_open_gate(self):
        with patch.dict(os.environ, {'COPILOT_VOICE_ENABLED':'true','COPILOT_DRAINED':'true'}):
            with self.assertRaisesRegex(VoiceWriteUnavailable, 'legacy_writer_drain_not_proven'):
                require_voice_write_ready()
            with self.assertRaises(TypeError):
                require_voice_write_ready(verified=True)

    def test_zero_receipts_are_diagnostics_not_drain_proof(self):
        cur = Cursor({'sending':0, 'unknown':0})
        observation = observe_receipts(cur)
        self.assertEqual((observation.sending,observation.unknown),(0,0))
        self.assertEqual(len(cur.queries),1)
        self.assertTrue(cur.queries[0].lstrip().upper().startswith('SELECT'))
        with self.assertRaises(VoiceWriteUnavailable): require_voice_write_ready()

    def test_invalid_observations_fail_closed(self):
        for row in (None, {}, {'sending':False,'unknown':0}, {'sending':-1,'unknown':0}, {'sending':'0','unknown':0}):
            with self.assertRaises(VoiceWriteUnavailable): observe_receipts(Cursor(row))


if __name__ == '__main__': unittest.main()
