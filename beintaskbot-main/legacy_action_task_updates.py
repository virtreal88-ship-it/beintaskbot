"""Small legacy task mutations that do not belong in the HTTP dispatcher."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any, Callable, Mapping

from legacy_action_result import LegacyActionResult


@dataclass(frozen=True)
class TaskUpdateContext:
    task_allowed_for_chat: Callable[[int, int], bool]
    update_task: Callable[[int, dict[str, Any]], Any]
    invalidate_overview: Callable[[], None]
    employee_type: Callable[[int], str]
    has_active_session: Callable[[int, int], bool]
    start_session: Callable[[int, int], bool]
    finish_session: Callable[..., Any]
    timezone: tzinfo


def _task_id(data: Mapping[str, Any]) -> int:
    try:
        return int(data.get("task_id") or 0)
    except (TypeError, ValueError):
        return 0


def handle_task_update_action(
    action: str,
    data: Mapping[str, Any],
    chat_id: int,
    context: TaskUpdateContext,
) -> LegacyActionResult:
    task_id = _task_id(data)
    if not task_id:
        return LegacyActionResult({"success": False, "error": "task_id yoxdur."})
    if not context.task_allowed_for_chat(task_id, chat_id):
        return LegacyActionResult(
            {
                "success": False,
                "error": "Доступ запрещён: задача не относится к воронке Rüfət.",
            },
            403,
        )
    if action == "delete_task":
        updated = context.update_task(
            task_id, {"is_completed": True, "result": {"text": "Tamamlandı"}}
        )
        return LegacyActionResult({
            "success": bool(updated),
            "message": "🗑 Tapşırıq silindi (bağlandı)!" if updated else "",
            **({"error": "Silinmədi."} if not updated else {}),
        })

    preset = str(data.get("time_preset") or "+2h")
    reason = str(data.get("reason") or "")
    now = datetime.now(tz=context.timezone)
    if preset == "sabah":
        deadline = (now + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
    elif preset == "gelen_hefte":
        days_ahead = 7 - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        deadline = (now + timedelta(days=days_ahead)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
    else:
        deadline = now + timedelta(hours=2)
    updated = context.update_task(task_id, {"complete_till": int(deadline.timestamp())})
    if updated:
        context.invalidate_overview()
    if reason == "Çatdıra bilmirəm" and context.employee_type(chat_id) == "salary":
        if not context.has_active_session(chat_id, task_id):
            context.start_session(chat_id, task_id)
        context.finish_session(chat_id, task_id, 1, reason, deadline_ts=0)
    return LegacyActionResult({
        "success": bool(updated),
        **({"message": "Vaxt dəyişdirildi."} if updated else {"error": "Yeniləmə uğursuz."}),
    })
