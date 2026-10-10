"""Public marketplace localization must not require authentication."""

import ast
from pathlib import Path
import unittest
from types import SimpleNamespace

from aiohttp import web

from public_integration_pages import localized_page


ROOT = Path(__file__).resolve().parents[1]


def public_request(language=None):
    return SimpleNamespace(query={} if language is None else {"lang": language})


class PublicIntegrationPagesTests(unittest.TestCase):
    def test_explicit_languages_map_to_existing_pages(self):
        for kind, language, filename in (
            ("site", "en", "integration-en.html"),
            ("site", "ru", "integration-ru.html"),
            ("privacy", "en", "privacy-policy-en.html"),
            ("privacy", "ru", "privacy-policy.html"),
        ):
            with self.subTest(kind=kind, language=language):
                response = localized_page(public_request(language), kind)
                self.assertIsInstance(response, web.FileResponse)
                self.assertEqual(response._path, ROOT / "docs" / filename)
                self.assertTrue(response._path.is_file())
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_no_language_keeps_original_site_and_policy(self):
        for kind in ("site", "privacy"):
            self.assertIsNone(localized_page(public_request(), kind))

    def test_tour_is_only_a_public_illustration_for_supported_languages(self):
        response = localized_page(SimpleNamespace(query={"lang": "en", "tour": "chat"}), "site")
        self.assertEqual(response._path, ROOT / "docs" / "integration-tour.html")
        response = localized_page(SimpleNamespace(query={"lang": "ru", "tour": "../../bot.py"}), "site")
        self.assertEqual(response._path, ROOT / "docs" / "integration-ru.html")

    def test_query_cannot_choose_arbitrary_files(self):
        for language in ("../../bot.py", "tr", "EN", "ru/../../bot.py"):
            self.assertIsNone(localized_page(public_request(language), "site"))
        self.assertIsNone(localized_page(public_request("en"), "secrets"))

    def test_russian_public_pages_have_no_other_crm_brand(self):
        for filename in ("integration-ru.html", "privacy-policy.html"):
            text = (ROOT / "docs" / filename).read_text(encoding="utf-8")
            self.assertIn('lang="ru"', text)
            self.assertNotIn("kommo", text.lower())
            self.assertIn("n.qasimov@bein.az", text)
            self.assertNotIn("<script", text.lower())

    def test_handlers_return_localized_response_before_legacy_fallback(self):
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        for name, kind in (("serve_landing_page", "site"), ("serve_privacy_policy", "privacy")):
            function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
            calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "localized_page"]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].args[1].value, kind)
            early_return = next(node for node in function.body if isinstance(node, ast.If))
            self.assertIsInstance(early_return.body[0], ast.Return)
            self.assertEqual(early_return.body[0].value.id, "localized")


if __name__ == "__main__":
    unittest.main()
