"""Timer/KPI actions used by the legacy task web interface."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from legacy_action_result import LegacyActionResult


@dataclass(frozen=True)
class TaskSessionContext:
    has_active_session: Callable[[int, int], bool]
    start_session: Callable[[int, int], bool]
    pause_session: Callable[[int, int, int], bool]
    finish_session: Callable[..., Mapping[str, Any] | None]
    get_task: Callable[[int], Mapping[str, Any] | None]


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return default


def handle_task_session_action(
    action: str,
    data: Mapping[str, Any],
    chat_id: int,
    context: TaskSessionContext,
) -> LegacyActionResult:
    task_id = _integer(data.get("task_id"))
    if action == "check_started":
        return LegacyActionResult({
            "success": True,
            "started": bool(task_id and context.has_active_session(chat_id, task_id)),
        })
    if not task_id:
        return LegacyActionResult(
            {"success": False, "error": "task_id lazımdır."}
        )
    if action == "start_task":
        started = context.start_session(chat_id, task_id)
        return LegacyActionResult({
            "success": True,
            "message": "▶️ Başladı! Vaxt sayılır." if started else "⏱️ Artıq başlayıb.",
        })
    if action == "pause_task":
        elapsed_seconds = _integer(data.get("elapsed_seconds"))
        context.pause_session(chat_id, task_id, elapsed_seconds)
        return LegacyActionResult({"success": True, "message": "Dayandırıldı."})

    task_type_id = _integer(data.get("task_type_id"), 1)
    deadline_ts = 0
    task = context.get_task(task_id) or {}
    if task:
        task_type_id = _integer(task.get("task_type_id"), task_type_id)
        deadline_ts = _integer(task.get("complete_till"))
    result = context.finish_session(
        chat_id,
        task_id,
        task_type_id,
        str(data.get("delay_reason") or ""),
        deadline_ts=deadline_ts,
    )
    if not result:
        return LegacyActionResult(
            {"success": False, "error": "Əvvəlcə 'Başla' basın."}
        )
    return LegacyActionResult({
        "success": True,
        "message": f"✅ Bitdi! KPI: {result['kpi_score']}/100",
        "kpi_score": result["kpi_score"],
        "actual_minutes": result["actual_minutes"],
        "target_minutes": result["target_minutes"],
        "needs_reason": result["needs_reason"],
    })
