"""Prevent the legacy action dispatcher from becoming a monolith again."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LegacyActionDispatcherArchitectureTests(unittest.TestCase):
    def _functions(self):
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8-sig"))
        return {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_dispatcher_is_small_and_large_actions_are_named_handlers(self):
        functions = self._functions()
        dispatcher = functions["handle_api_action"]
        self.assertLessEqual(dispatcher.end_lineno - dispatcher.lineno + 1, 150)
        expected = {
            "_handle_legacy_add_note",
            "_handle_legacy_task",
            "_handle_legacy_stage",
            "_handle_legacy_create_deal",
            "_handle_legacy_update_task",
            "_handle_legacy_complete_task",
        }
        self.assertTrue(expected.issubset(functions))
        source = ast.get_source_segment(
            (ROOT / "bot.py").read_text(encoding="utf-8-sig"), dispatcher
        )
        for name in expected:
            self.assertIn(f"await {name}(", source)

    def test_completion_and_pending_actions_keep_side_effects_in_named_helpers(self):
        functions = self._functions()
        self.assertLessEqual(
            functions["_handle_legacy_complete_task"].end_lineno
            - functions["_handle_legacy_complete_task"].lineno
            + 1,
            210,
        )
        self.assertLessEqual(
            functions["resolve_pending_action"].end_lineno
            - functions["resolve_pending_action"].lineno
            + 1,
            300,
        )
        self.assertIn("_notify_legacy_task_completion", functions)
        self.assertIn("_resolve_pending_stage_change", functions)
        self.assertIn("_resolve_pending_executor_change", functions)

    def test_deal_chat_send_endpoint_is_a_bounded_coordinator(self):
        functions = self._functions()
        handler = functions["handle_api_deal_chat_send"]
        self.assertLessEqual(handler.end_lineno - handler.lineno + 1, 210)
        retired_reader = functions["handle_api_deal_chat"]
        self.assertLessEqual(
            retired_reader.end_lineno - retired_reader.lineno + 1, 5
        )

    def test_notifications_endpoint_offloads_blocking_kommo_work(self):
        functions = self._functions()
        handler = functions["handle_api_notifications"]
        self.assertLessEqual(handler.end_lineno - handler.lineno + 1, 75)
        source = ast.get_source_segment(
            (ROOT / "bot.py").read_text(encoding="utf-8-sig"), handler
        )
        self.assertIn("asyncio.to_thread", source)
        self.assertIn("_load_legacy_notification_tasks", functions)

    def test_web_server_startup_only_composes_route_groups(self):
        functions = self._functions()
        startup = functions["start_webhook_server"]
        self.assertLessEqual(startup.end_lineno - startup.lineno + 1, 15)
        self.assertLessEqual(
            functions["_register_legacy_web_routes"].end_lineno
            - functions["_register_legacy_web_routes"].lineno
            + 1,
            130,
        )
        self.assertLessEqual(
            functions["_register_tenant_web_routes"].end_lineno
            - functions["_register_tenant_web_routes"].lineno
            + 1,
            180,
        )

    def test_waiting_queue_http_handler_offloads_legacy_sync_work(self):
        functions = self._functions()
        handler = functions["handle_api_gozleme"]
        self.assertLessEqual(handler.end_lineno - handler.lineno + 1, 75)
        source = ast.get_source_segment(
            (ROOT / "bot.py").read_text(encoding="utf-8-sig"), handler
        ) or ""
        self.assertIn("asyncio.to_thread", source)
        self.assertIn("_load_legacy_gozleme_response", functions)

    def test_personal_funnel_overview_is_split_into_loader_indexes_and_presenter(self):
        functions = self._functions()
        overview = functions["build_rufat_overview"]
        self.assertLessEqual(overview.end_lineno - overview.lineno + 1, 220)
        for helper in (
            "_load_personal_funnel_leads",
            "_index_personal_funnel_leads",
            "_build_personal_funnel_deals",
        ):
            self.assertIn(helper, functions)


if __name__ == "__main__":
    unittest.main()
