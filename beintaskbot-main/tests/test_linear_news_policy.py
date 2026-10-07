import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from linear_news_policy import classify_news_source, news_source_key


class NewsPolicyTests(unittest.TestCase):
    def setUp(self):
        self.issue = {"id": "BS-1", "source_id": "1", "parent_id": "", "title": "New dashboard",
                      "description": "Add a dashboard", "labels": [], "project": "AKUL", "status": {"name": "Done"}}
        self.create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"kind":"feature"}'))]))
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))

    def test_subtasks_and_unknown_hierarchy_do_not_call_ai(self):
        self.assertEqual(classify_news_source(dict(self.issue, parent_id="parent"), self.client, "model")["kind"], "subtask")
        self.issue.pop("parent_id")
        self.assertEqual(classify_news_source(self.issue, self.client, "model")["kind"], "unknown")
        self.create.assert_not_called()

    def test_ai_classification_is_strict_and_fail_closed(self):
        for kind in ("feature", "bug_fix", "optimization", "other"):
            self.create.return_value.choices[0].message.content = json.dumps({"kind": kind})
            self.assertEqual(classify_news_source(self.issue, self.client, "model")["kind"], kind)
        self.create.return_value.choices[0].message.content = '{"kind":"maybe"}'
        with self.assertRaises(ValueError):
            classify_news_source(self.issue, self.client, "model")
        self.create.side_effect = RuntimeError("AI offline")
        with self.assertRaises(RuntimeError):
            classify_news_source(self.issue, self.client, "model")

    def test_source_key_ignores_timestamps_but_tracks_text_and_hierarchy(self):
        key = news_source_key(self.issue)
        self.assertEqual(key, news_source_key(dict(self.issue, updated_at="later")))
        self.assertNotEqual(key, news_source_key(dict(self.issue, description="Fix broken dashboard")))
        self.assertNotEqual(key, news_source_key(dict(self.issue, parent_id="parent")))

    def test_sync_generates_copy_only_for_main_feature_and_caches_exclusions(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text(encoding='utf-8-sig'))
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_sync_linear_news')
        save = Mock()
        generate = Mock(return_value={"title": "New dashboard", "summary": "A new capability", "body": ""})
        ns = {"asyncio": asyncio, "LINEAR_NEWS_TELEGRAM_ENABLED": True, "llm_client": self.client, "LLM_MODEL": "model",
              "sync_linear_news_environments": Mock(), "restore_linear_news_publications_once": Mock(),
              "require_linear_news_review_once": Mock(), "list_linear_news": Mock(return_value=[]),
              "_linear_news_project_key": lambda issue: "AKUL", "_linear_news_current_publish_slot": lambda: "slot",
              "claim_linear_news_publish_slot": Mock(return_value=True), "_linear_news_ai_content": generate,
              "upsert_linear_news": save, "prune_linear_news": Mock(), "logger": Mock()}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<news>', 'exec'), ns)
        self.create.side_effect = [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"kind":"bug_fix"}'))]),
                                  SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"kind":"feature"}'))])]
        issues = [dict(self.issue, source_id="sub", parent_id="parent"),
                  dict(self.issue, source_id="bug"), self.issue]
        asyncio.run(ns['_sync_linear_news'](issues))
        self.assertEqual(save.call_count, 3)
        self.assertEqual([call.kwargs['raw']['news_classification']['kind'] for call in save.call_args_list], ['subtask', 'bug_fix', 'feature'])
        generate.assert_called_once_with(self.issue)
        ns['claim_linear_news_publish_slot'].assert_called_once()
        archived = [{"source_issue_id": call.kwargs["source_issue_id"],
                     "raw": call.kwargs["raw"], "approval_status": "pending"}
                    for call in save.call_args_list]
        ns['list_linear_news'].return_value = archived
        asyncio.run(ns['_sync_linear_news'](issues))
        self.assertEqual(self.create.call_count, 2, "Cached classification must not charge AI again")
        self.assertEqual(save.call_count, 3)
        # Even a previously approved bug/subtask must not reach the scheduler.
        ns['list_linear_news'].return_value = [dict(row, approval_status="approved", reviewed_at="now") for row in archived[:2]]
        ns['_publish_linear_news_to_telegram'] = Mock()
        asyncio.run(ns['_sync_linear_news'](issues[:2]))
        ns['_publish_linear_news_to_telegram'].assert_not_called()

    def test_graphql_requests_parent_without_filtering_kanban(self):
        source = (Path(__file__).resolve().parents[1] / 'bot.py').read_text(encoding='utf-8-sig')
        loader = source[source.index('def _load_linear_tesdiq_issues'):source.index('def _invalidate_linear_tesdiq_cache')]
        self.assertEqual(loader.count('parent { id }') + loader.count('parent {{ id }}'), 3)
        self.assertIn('"parent_id":', loader)


if __name__ == '__main__':
    unittest.main()
