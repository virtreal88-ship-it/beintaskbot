import unittest

from legacy_chat_delivery import deliver_kommo_message


class LegacyChatDeliveryTests(unittest.TestCase):
    def test_whatsapp_voice_is_sent_without_caption_then_text(self):
        calls = []

        def send(*args, **kwargs):
            calls.append((args, kwargs))
            return True, "", 200

        result = deliver_kommo_message(
            channel="whatsapp", talk_id=7, chat_id="", text="caption",
            upload_name="voice.ogg", is_voice=True, attachment={"type": "voice"},
            reply_ids=[], reply_preview="", voice_caption="Səsli mesaj",
            send_message=send, quiet_quote=lambda text, _: text,
            visible_quote=lambda text, _: text,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.sent_text, "Səsli mesaj")
        self.assertEqual(calls[0][0][1], "")
        self.assertEqual(calls[1][0][1], "caption")

    def test_social_reply_falls_back_to_visible_quote(self):
        calls = []

        def send(*args, **kwargs):
            calls.append((args, kwargs))
            if kwargs.get("reply_to"):
                return False, "reply unsupported", 400
            return True, "", 200

        result = deliver_kommo_message(
            channel="instagram", talk_id=9, chat_id="chat", text="Cavab",
            upload_name="", is_voice=False, attachment=None,
            reply_ids=["external-1"], reply_preview="Sual", voice_caption="voice",
            send_message=send, quiet_quote=lambda text, _: text,
            visible_quote=lambda text, preview: f"> {preview}\n{text}",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.sent_text, "> Sual\nCavab")
        self.assertEqual(len(calls), 2)

    def test_whatsapp_reply_uses_quiet_quote(self):
        def send(*args, **kwargs):
            self.assertEqual(args[1], "quiet:Cavab")
            return True, "", 200

        result = deliver_kommo_message(
            channel="whatsapp", talk_id=1, chat_id="", text="Cavab",
            upload_name="", is_voice=False, attachment=None,
            reply_ids=[], reply_preview="Sual", voice_caption="voice",
            send_message=send, quiet_quote=lambda text, _: f"quiet:{text}",
            visible_quote=lambda text, _: text,
        )
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
