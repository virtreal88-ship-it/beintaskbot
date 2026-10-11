"""AI conversation context contracts."""

import ast
import unittest
from pathlib import Path
from unittest.mock import Mock

from legacy_chat_ai_context import (
    _ai_history_lines,
    _history_message_is_incoming,
    _is_history_audio,
)


ROOT = Path(__file__).resolve().parents[1]


class DirectionTests(unittest.TestCase):
    def test_false_string_is_manager_not_customer(self):
        self.assertFalse(_history_message_is_incoming({"incoming": "false"}))
        self.assertTrue(_history_message_is_incoming({"incoming": "true"}))
        self.assertFalse(_history_message_is_incoming({"direction": "outgoing"}))
        self.assertTrue(_history_message_is_incoming({"direction": "received"}))

    def test_audio_is_recognized_from_kind_or_extension(self):
        self.assertTrue(_is_history_audio({"message_type": "call_in"}))
        self.assertTrue(_is_history_audio({"file_name": "voice.ogg"}))
        self.assertFalse(_is_history_audio({"file_name": "photo.jpg"}))


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_uses_last_thirty_and_stable_roles(self):
        rows = [
            {"incoming": index % 2 == 0, "text": f"message-{index}"}
            for index in range(35)
        ]
        lines, transcribed, unavailable = await _ai_history_lines(
            rows, transcribe=lambda _item, _lead: "",
        )
        self.assertEqual(len(lines), 30)
        self.assertNotIn("message-4", "\n".join(lines))
        self.assertIn("Müştəri: message-34", lines)
        self.assertEqual((transcribed, unavailable), (0, 0))

    async def test_existing_transcript_is_reused(self):
        transcribe = Mock(return_value="provider text")
        lines, transcribed, unavailable = await _ai_history_lines(
            [{"incoming": True, "message_type": "audio", "transcript": "hazır mətn"}],
            reply_context=True,
            transcribe=transcribe,
        )
        transcribe.assert_not_called()
        self.assertIn("Müştəri (səsli mesajın mətni): hazır mətn", lines)
        self.assertEqual((transcribed, unavailable), (1, 0))

    async def test_failed_voice_is_explicit_in_reply_context(self):
        lines, transcribed, unavailable = await _ai_history_lines(
            [{"incoming": False, "message_type": "audio"}],
            reply_context=True,
            transcribe=lambda _item, _lead: "",
        )
        self.assertIn("Satış meneceri: [Səs yazısı var", lines[0])
        self.assertEqual((transcribed, unavailable), (0, 1))


class ContextArchitectureTests(unittest.TestCase):
    def test_module_is_bounded_and_moved_from_bot(self):
        source = (ROOT / "legacy_chat_ai_context.py").read_text(encoding="utf-8-sig")
        self.assertLessEqual(len(source.splitlines()), 250)
        self.assertNotIn("import bot", source)
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertFalse({"_is_history_audio", "_history_message_is_incoming", "_ai_history_lines"} & definitions)


if __name__ == "__main__":
    unittest.main()
