"""Media URL and payload parsing contracts without external requests."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


def load_module(filename: str, injected: dict, excluded: set[str]):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    tree.body = [
        node for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.module not in excluded
    ]
    namespace = {"__name__": filename.removesuffix(".py") + "_test", **injected}
    exec(compile(tree, filename, "exec"), namespace)
    return SimpleNamespace(**namespace)


class MediaPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_module(
            "legacy_kommo_media_policy.py",
            {"urlparse": urlparse, "KOMMO_BASE_URL": "https://account.kommo.test"},
            {"legacy_runtime_config", "urllib.parse"},
        )

    def test_allow_list_matches_real_hosts_not_lookalikes(self):
        self.assertTrue(self.policy._is_allowed_media_url("https://drive.kommo.com/file"))
        self.assertTrue(self.policy._is_allowed_media_url("https://media.pps.whatsapp.net/file"))
        self.assertTrue(self.policy._is_allowed_media_url("https://calls.zadarma.com/record"))
        self.assertFalse(self.policy._is_allowed_media_url("https://kommo.com.evil.test/file"))
        self.assertFalse(self.policy._is_allowed_media_url("https://kommo.com@evil.test/file"))
        self.assertFalse(self.policy._is_allowed_media_url("javascript:alert(1)"))

    def test_relative_and_protocol_relative_urls_are_normalized(self):
        self.assertEqual(
            self.policy._as_absolute_media_url("/api/file/7"),
            "https://account.kommo.test/api/file/7",
        )
        self.assertEqual(
            self.policy._as_absolute_media_url("//cdn.kommo.com/file"),
            "https://cdn.kommo.com/file",
        )


class MediaParserTests(unittest.TestCase):
    def setUp(self):
        policy = MediaPolicyTests()
        policy.setUp()
        self.policy = policy.policy
        self.parser = load_module(
            "legacy_kommo_media_parser.py",
            {
                "_as_absolute_media_url": self.policy._as_absolute_media_url,
                "_is_allowed_avatar_url": self.policy._is_allowed_avatar_url,
            },
            {"legacy_kommo_media_policy"},
        )

    def test_media_walk_prefers_attachment_and_skips_profile_fields(self):
        payload = {
            "author": {"avatar": "https://lookaside.fbsbx.com/avatar.jpg"},
            "attachment": {"download_link": "https://drive.kommo.com/file.ogg"},
        }
        self.assertEqual(
            self.parser._extract_media_url(payload),
            "https://drive.kommo.com/file.ogg",
        )

    def test_magic_bytes_override_unhelpful_content_type(self):
        self.assertEqual(
            self.parser._sniff_media_type(b"OggS\x00\x00", "application/octet-stream"),
            "audio/ogg",
        )
        self.assertEqual(self.parser._sniff_media_type(b"", "", name="voice.mp3"), "audio/mpeg")

    def test_nested_uuid_and_text_are_bounded_and_detected(self):
        file_uuid = "12345678-1234-1234-1234-123456789012"
        payload = {"items": [{"file": {"uuid": file_uuid}, "caption": "Salam"}]}
        self.assertEqual(self.parser._extract_file_uuid(payload), file_uuid)
        self.assertEqual(self.parser._extract_nested_text(payload["items"]), "Salam")
        self.assertEqual(self.parser._extract_file_uuid({"x": {"x": {"x": {"x": {"x": {"uuid": file_uuid}}}}}}), "")

    def test_avatar_requires_an_allowed_avatar_host(self):
        allowed = {"author": {"avatar": "https://lookaside.fbsbx.com/photo.jpg"}}
        blocked = {"author": {"avatar": "https://evil.test/photo.jpg"}}
        self.assertEqual(self.parser._first_avatar_url(allowed), allowed["author"]["avatar"])
        self.assertEqual(self.parser._first_avatar_url(blocked), "")


class MediaArchitectureTests(unittest.TestCase):
    def test_modules_are_bounded_and_removed_from_bot(self):
        for filename in ("legacy_kommo_media_policy.py", "legacy_kommo_media_parser.py"):
            source = (ROOT / filename).read_text(encoding="utf-8-sig")
            with self.subTest(filename=filename):
                self.assertLessEqual(len(source.splitlines()), 250)
                self.assertNotIn("import bot", source)
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        moved = {
            "_media_host", "_is_allowed_media_url", "_as_absolute_media_url",
            "_extract_media_url", "_sniff_media_type", "_extract_file_uuid",
        }
        self.assertFalse(moved & definitions)


if __name__ == "__main__":
    unittest.main()
