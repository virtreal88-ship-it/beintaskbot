"""Regression coverage for legacy task completion side effects."""
from datetime import timezone
from unittest import TestCase
from unittest.mock import Mock

from legacy_task_completion_notifications import (
    TaskCompletionNotificationContext,
    notify_legacy_task_completion,
)


class LegacyTaskCompletionNotificationsTests(TestCase):
    def setUp(self):
        self.post = Mock()
        self.pending = Mock()
        self.push = Mock()
        self.balance = Mock()
        self.context = TaskCompletionNotificationContext(
            logger=Mock(), timezone=timezone.utc, task_type_names={1: "Zəng"},
            kommo_users={7: "Rüfət"}, stage_names={5: "Müzakirə"},
            telegram_token="token", task_creators_file="creators.json", rufat_chat_id=20,
            name_to_chat={"Nizami": 30}, http_post=self.post,
            get_employee_name=lambda _chat, fallback: fallback,
            get_kommo_user_id=lambda _chat: 7,
            get_employee_type=lambda _chat: "piecework",
            add_balance_transaction=self.balance, get_admin_chat=lambda _user: 10,
            requires_approval=lambda _chat: True,
            get_lead_details=lambda _lead: {"status_id": 5},
            notification_enabled=lambda *_args: True,
            save_pending_action=self.pending, send_push_to_admin=self.push,
            read_json=lambda _path: {"4": "Nizami"},
        )

    def test_keeps_balance_approval_and_creator_notices(self):
        notify_legacy_task_completion(
            self.context, chat_id=2, task_id=4, task_data={"text": "[12] Müştəriyə zəng"},
            task_type_id=1, task_deadline_ts=0, kpi_result=None, delay_reason="",
            lead_id=8, contact_name="Aysel", phone="0500000000", note_text="Hazırdır",
            task_result_text="", link="https://crm.example/deal/8", task_creator_is_rufat=False,
        )
        self.balance.assert_called_once()
        self.pending.assert_called_once()
        self.push.assert_called_once()
        self.assertEqual(self.post.call_count, 2)
        approval_text = self.post.call_args_list[0].kwargs["json"]["text"]
        creator_text = self.post.call_args_list[1].kwargs["json"]["text"]
        self.assertIn("Müzakirə", approval_text)
        self.assertIn("Müştəriyə zəng", approval_text)
        self.assertIn("Sizin yaratdığınız", creator_text)

    def test_skips_admin_approval_when_policy_is_off(self):
        self.context = TaskCompletionNotificationContext(
            **{**self.context.__dict__, "requires_approval": lambda _chat: False}
        )
        notify_legacy_task_completion(
            self.context, chat_id=2, task_id=4, task_data={"text": "Tapşırıq"},
            task_type_id=1, task_deadline_ts=0, kpi_result=None, delay_reason="", lead_id=None,
            contact_name="", phone="", note_text="", task_result_text="", link="",
            task_creator_is_rufat=False,
        )
        self.pending.assert_not_called()
        self.push.assert_not_called()
        self.assertEqual(self.post.call_count, 1)
