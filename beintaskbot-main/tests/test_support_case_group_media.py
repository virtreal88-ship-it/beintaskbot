import unittest
from unittest.mock import MagicMock

import tenant_knowledge_candidates_store as store


TENANT = "11111111-1111-4111-8111-111111111111"
CHANNEL = "22222222-2222-4222-8222-222222222222"
GROUP = "12345-67890@g.us"


class SupportCaseGroupMediaTests(unittest.TestCase):
    def test_group_identity_uses_verified_title_and_tenant_scoped_observed_participants(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"relation": "saas_connect_participant_display_names"}
        cur.fetchall.side_effect = [
            [
                {"raw": {"participantJid": "994501111111@s.whatsapp.net", "participantName": "Nicat Xaliqov"}, "author_name": "Nicat Xaliqov", "channel": "connect:" + CHANNEL},
                {"raw": {"participantJid": "994502222222@s.whatsapp.net"}, "author_name": "994502222222", "channel": "connect:" + CHANNEL},
            ],
            [{"participant_jid": "994501111111@s.whatsapp.net", "display_name": "Nicat Xaliqov"}],
        ]
        result = store._case_customer(cur, TENANT, {"channel_id": CHANNEL, "conversation_id": GROUP},
                                      {"source_message_ids": ["message-1", "message-2"]},
                                      {"customer_name": "Printer support"})
        self.assertEqual(result["conversation_name"], "Printer support")
        self.assertEqual(result["participants"], ["Nicat Xaliqov"])
        self.assertEqual(result["participant_count"], 2)
        self.assertEqual((result["customer_name"], result["customer_phone"], result["company_name"]), ("", "", ""))
        sql, args = cur.execute.call_args_list[-1].args
        self.assertIn("tenant_id=%s::uuid", sql)
        self.assertIn("connect_channel_id::text=ANY(%s::text[])", sql)
        self.assertEqual(args[:3], (TENANT, [CHANNEL], GROUP))
        source_sql, source_args = cur.execute.call_args_list[0].args
        self.assertIn("saas_connect_conversations", source_sql)
        self.assertIn("c.conversation_id=%s", source_sql)
        self.assertEqual(source_args, (TENANT, ["message-1", "message-2"], GROUP))

    def test_group_jid_is_never_used_as_title_or_customer_identity(self):
        cur = MagicMock()
        cur.fetchall.side_effect = [[], []]
        result = store._case_customer(cur, TENANT, {"channel_id": CHANNEL, "conversation_id": GROUP},
                                      {"source_message_ids": []}, {"customer_name": GROUP})
        self.assertEqual(result["conversation_name"], "")
        self.assertEqual(result["customer_name"], "")
        self.assertEqual(result["customer_phone"], "")

    def test_media_processing_receipt_projection_contains_only_status_facts(self):
        receipt = {"id": "source-message", "status": "processed", "code": "vision_supplied",
                   "audio_status": "", "transcript": "private transcript must not cross this projection"}
        projected = store._public_media_processing([receipt], ["source-message", "unrelated"], "completed")
        self.assertEqual(projected, [{"id": "source-message", "status": "processed", "code": "vision_supplied",
                                      "audio_status": "", "extraction_status": "completed"}])



if __name__ == "__main__":
    unittest.main()
