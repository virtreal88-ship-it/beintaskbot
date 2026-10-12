"""Contracts for the extracted legacy Kommo pipeline catalog."""

import ast
import logging
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from crm_stage_labels import stage_display_name


ROOT = Path(__file__).resolve().parents[1]


def definitions(filename: str) -> set[str]:
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def catalog_namespace():
    tree = ast.parse((ROOT / "legacy_kommo_pipeline_catalog.py").read_text(encoding="utf-8-sig"))
    tree.body = [
        node for node in tree.body
        if not isinstance(node, ast.ImportFrom)
        or node.module not in {"crm_stage_labels", "legacy_runtime_config"}
    ]
    stages = {"sorgular": 10, "ugurlu": 142, "imtina": 143}
    names = {10: "Sorğular", 142: "Uğurla tamamlandı", 143: "İmtina olundu"}
    namespace = {
        "__name__": "legacy_kommo_pipeline_catalog_test",
        "logging": logging,
        "re": re,
        "time": SimpleNamespace(time=lambda: 1000.0),
        "stage_display_name": stage_display_name,
        "RUFAT_PIPELINE_ID": 1, "HUSEYN_PIPELINE_ID": 2,
        "RASIM_PIPELINE_ID": 3, "NIZAMI_PIPELINE_ID": 4,
        "RUFAT_STAGES": stages, "HUSEYN_STAGES": stages,
        "RASIM_STAGES": stages, "NIZAMI_STAGES": stages,
        "RUFAT_STAGE_NAMES": names, "HUSEYN_STAGE_NAMES": names,
        "RASIM_STAGE_NAMES": names, "NIZAMI_STAGE_NAMES": names,
        "KOMMO_BASE_URL": "https://account.kommo.test",
    }
    exec(compile(tree, "<pipeline-catalog>", "exec"), namespace)
    return namespace


class LegacyKommoPipelineTests(unittest.TestCase):
    def test_bot_composes_pipeline_modules_without_owning_their_functions(self):
        bot_names = definitions("bot.py")
        moved = {
            "_fold_stage_name", "_stage_key_from_kommo", "_stage_maps_from_statuses", "load_pipeline_stage_maps",
            "load_all_kommo_pipelines", "get_funnel_owner", "get_pipeline_id_for_chat",
            "get_rufat_completion_stage", "configured_funnel_owners", "_lead_pipeline_id",
        }
        self.assertFalse(moved & bot_names)
        self.assertIn("configure_pipeline_catalog(", (ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        self.assertIn("_fold_stage_name, _pipeline_stage_cache", (ROOT / "bot.py").read_text(encoding="utf-8-sig"))

    def test_modules_are_bounded_and_do_not_import_bot(self):
        for filename in ("legacy_kommo_pipeline_catalog.py", "legacy_kommo_funnels.py"):
            source = (ROOT / filename).read_text(encoding="utf-8-sig")
            with self.subTest(filename=filename):
                self.assertLessEqual(len(source.splitlines()), 250)
                self.assertNotIn("import bot", source)

    def test_personal_pipeline_uses_static_map_without_http(self):
        namespace = catalog_namespace()
        http = Mock()
        namespace["configure_pipeline_catalog"](http=http, headers={"Authorization": "secret"})
        stages, names, ui = namespace["load_pipeline_stage_maps"](1)
        self.assertEqual(stages["sorgular"], 10)
        self.assertEqual(names[142], "Uğurla tamamlandı")
        self.assertIn(("imtina", "İmtina olundu"), ui)
        http.get.assert_not_called()

    def test_remote_pipeline_is_cached_and_terminal_labels_are_localized(self):
        namespace = catalog_namespace()
        response = Mock(status_code=200)
        response.json.return_value = {"_embedded": {"statuses": [
            {"id": 50, "name": "Danışıqlar", "sort": 1},
            {"id": 142, "name": "Успешно реализовано", "sort": 2},
        ]}}
        http = Mock()
        http.get.return_value = response
        namespace["configure_pipeline_catalog"](http=http, headers={})
        first = namespace["load_pipeline_stage_maps"](99, fallback=False)
        second = namespace["load_pipeline_stage_maps"](99, fallback=False)
        self.assertEqual(first[0]["ugurlu"], 142)
        self.assertEqual(first[1][142], "Uğurla tamamlandı")
        self.assertEqual(first, second)
        http.get.assert_called_once_with(
            "https://account.kommo.test/api/v4/leads/pipelines/99", headers={}, timeout=12,
        )


if __name__ == "__main__":
    unittest.main()
