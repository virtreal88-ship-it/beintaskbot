"""Legacy webapp task actions with injected CRM and notification ports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any, Callable, Mapping

from legacy_action_result import LegacyActionResult


@dataclass(frozen=True)
class DealTaskContext:
    lead_allowed_for_chat: Callable[[int, int], bool]
    create_task: Callable[..., Any]
    update_task: Callable[[int, dict[str, Any]], Any]
    active_assignees: Mapping[Any, tuple[str, str, int]]
    reminder_task_type_id: int
    timezone: tzinfo
    get_funnel_owner: Callable[[int], Mapping[str, Any] | None]
    get_employee_name: Callable[[int, str], str]
    get_chat_id_by_name: Callable[[str], int | None]
    send_telegram_text: Callable[[int, str], Any]
    send_push_notification: Callable[[str, str, str], Any]
    remember_staff_notice: Callable[..., Any]
    kommo_base_url: str
    invalidate_overview: Callable[[], None]
    record_pulse: Callable[..., None]
    created_task_id: Callable[[Any], Any]


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def handle_deal_task(
    action: str,
    data: Mapping[str, Any],
    chat_id: int,
    context: DealTaskContext,
) -> LegacyActionResult:
    """Create, edit or softly delete a task linked to an accessible deal."""
    lead_id = _integer(data.get("lead_id"))
    task_id = _integer(data.get("task_id"))
    text = str(data.get("text") or "").strip()
    if not lead_id or not context.lead_allowed_for_chat(lead_id, chat_id):
        return LegacyActionResult(
            {"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."},
            400,
        )

    if action == "deal_add_task":
        if not text:
            return LegacyActionResult(
                {"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."},
                400,
            )
        deadline_ts = _integer(data.get("deadline_ts"))
        if not deadline_ts:
            deadline_ts = int(
                (datetime.now(tz=context.timezone) + timedelta(hours=2)).timestamp()
            )
        task_type_id = _integer(data.get("task_type_id") or data.get("task_type")) or 4232112
        if task_type_id not in {4232112, int(context.reminder_task_type_id)}:
            task_type_id = 4232112
        executor = str(data.get("executor") or "Rüfət Həsənzadə").strip()
        executor_ids = {
            name: user_id for name, _short, user_id in context.active_assignees.values()
        }
        responsible_user_id = executor_ids.get(executor)
        if not responsible_user_id:
            return LegacyActionResult(
                {"success": False, "error": "İcraçı tanınmadı."}, 400
            )
        owner = context.get_funnel_owner(chat_id)
        creator = (owner or {}).get("name") or context.get_employee_name(
            chat_id, "Rüfət Həsənzadə"
        )
        result = context.create_task(
            lead_id,
            text,
            deadline_ts,
            responsible_user_id=responsible_user_id,
            entity_type="leads",
            task_type_id=task_type_id,
            creator_name=creator,
        )
        if result:
            executor_chat = context.get_chat_id_by_name(executor)
            if executor_chat and int(executor_chat) != int(chat_id):
                deadline = datetime.fromtimestamp(
                    deadline_ts, tz=context.timezone
                ).strftime("%d.%m.%Y %H:%M")
                context.send_telegram_text(
                    executor_chat,
                    f"📋 Yeni tapşırıq ({creator}):\n\n📝 {text}\n👤 {executor}\n⏰ {deadline}\n🔗 {context.kommo_base_url}/leads/detail/{lead_id}",
                )
                context.send_push_notification(
                    str(executor_chat), "📋 Yeni tapşırıq!", f"{creator} → {text[:80]}"
                )
                context.remember_staff_notice(
                    executor_chat,
                    "new_task",
                    "Yeni tapşırıq!",
                    f"{creator} → {text[:80]}",
                    lead_id,
                )
        context.invalidate_overview()
        if result:
            context.record_pulse(lead_id, "deal_add_task")
        return LegacyActionResult(
            {
                "success": bool(result),
                "task_id": context.created_task_id(result),
                "message": "Tapşırıq əlavə edildi." if result else "Tapşırıq əlavə olunmadı.",
            }
        )

    if not task_id or (action == "deal_edit_task" and not text):
        return LegacyActionResult(
            {"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."},
            400,
        )
    if action == "deal_edit_task":
        payload: dict[str, Any] = {"text": text}
        deadline_ts = _integer(data.get("deadline_ts"))
        if deadline_ts:
            payload["complete_till"] = deadline_ts
        ok = bool(context.update_task(task_id, payload))
        pulse_kind = "deal_edit_task"
        success_message, failure_message = "Tapşırıq yeniləndi.", "Tapşırıq yenilənmədi."
    else:
        ok = bool(
            context.update_task(
                task_id, {"is_completed": True, "result": {"text": "Silindi"}}
            )
        )
        pulse_kind = "deal_delete_task"
        success_message, failure_message = "Tapşırıq silindi.", "Tapşırıq silinmədi."
    if ok:
        context.invalidate_overview()
        context.record_pulse(lead_id, pulse_kind)
    return LegacyActionResult(
        {"success": ok, "message": success_message if ok else failure_message}
    )
