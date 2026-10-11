"""Legacy Telegram bootstrap composition contracts."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import legacy_app_bootstrap as bootstrap


ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_handlers_and_jobs_are_registered_from_descriptors(self):
        app = SimpleNamespace(add_handler=Mock(), job_queue=SimpleNamespace(
            run_repeating=Mock(), run_daily=Mock(),
        ))
        builder = Mock()
        builder.token.return_value = builder
        builder.connect_timeout.return_value = builder
        builder.read_timeout.return_value = builder
        builder.write_timeout.return_value = builder
        builder.post_init.return_value = builder
        builder.build.return_value = app
        with (
            patch.object(bootstrap.Application, "builder", return_value=builder),
            patch.object(bootstrap, "CommandHandler", side_effect=lambda name, cb: ("command", name, cb)),
            patch.object(bootstrap, "CallbackQueryHandler", side_effect=lambda cb, pattern: ("callback", pattern, cb)),
            patch.object(bootstrap, "MessageHandler", side_effect=lambda filt, cb: ("message", filt, cb)),
        ):
            result = bootstrap.build_telegram_application(
                token="hidden", post_init=Mock(),
                command_handlers=[("start", Mock())],
                callback_handlers=[("^x", Mock())],
                message_handlers=[("filter", Mock())],
                repeating_jobs=[(Mock(), 60, 10, {"max_instances": 1})],
                daily_jobs=[(Mock(), "05:00")],
            )
        self.assertIs(result, app)
        self.assertEqual(app.add_handler.call_count, 3)
        app.job_queue.run_repeating.assert_called_once()
        app.job_queue.run_daily.assert_called_once()

    def test_polling_keeps_update_and_queue_contract(self):
        app = Mock()
        bootstrap.run_polling(app)
        app.run_polling.assert_called_once_with(
            allowed_updates=bootstrap.Update.ALL_TYPES,
            drop_pending_updates=True,
        )


class BootstrapArchitectureTests(unittest.TestCase):
    def test_module_is_bounded_and_bot_only_declares_descriptors(self):
        source = (ROOT / "legacy_app_bootstrap.py").read_text(encoding="utf-8-sig")
        self.assertLessEqual(len(source.splitlines()), 250)
        self.assertNotIn("import bot", source)
        bot_source = (ROOT / "bot.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("Application.builder()", bot_source)
        self.assertNotIn("job_queue.run_repeating", bot_source)
        tree = ast.parse(bot_source)
        definitions = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertNotIn("_run_http_server", definitions)


if __name__ == "__main__":
    unittest.main()
