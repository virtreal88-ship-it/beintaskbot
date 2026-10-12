"""Legacy webapp deal actions with explicit runtime dependencies."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from legacy_action_result import LegacyActionResult

DealActionResult = LegacyActionResult


@dataclass(frozen=True)
class DealUnreachableContext:
    employee_access_profile: Callable[[int], Mapping[str, Any]]
    lead_allowed_for_chat: Callable[[int, int], bool]
    load_pipeline_stage_maps: Callable[..., tuple[Mapping[str, int], Mapping[int, str], list[Any]]]
    update_lead: Callable[[int, dict[str, Any]], Any]
    invalidate_overview: Callable[[], None]
    clear_terminal_reentry: Callable[[int], None]
    record_pulse: Callable[..., None]
    target_pipeline_id: int
    pipeline_stage_cache: dict[Any, Any]


@dataclass(frozen=True)
class DealNoteContext:
    lead_allowed_for_chat: Callable[[int, int], bool]
    add_note: Callable[[int, str, str], Any]
    update_note: Callable[[int, str, str, int], tuple[bool, str]]
    delete_note: Callable[[int, str, int], tuple[bool, str]]
    note_entity_kind: Callable[[str], str]
    first_note_id: Callable[[Any], Any]
    invalidate_overview: Callable[[], None]
    record_pulse: Callable[..., None]


def _normalized_stage_name(value: object) -> str:
    return " ".join(
        str(value or "")
        .casefold()
        .replace("ı", "i")
        .replace("\u0307", "")
        .split()
    )


async def handle_deal_unreachable(
    data: Mapping[str, Any],
    chat_id: int,
    context: DealUnreachableContext,
) -> DealActionResult:
    """Move an accessible deal to the one configured postponed stage."""
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0

    access, allowed = await asyncio.gather(
        asyncio.to_thread(context.employee_access_profile, chat_id),
        asyncio.to_thread(context.lead_allowed_for_chat, lead_id, chat_id)
        if lead_id
        else asyncio.sleep(0, result=False),
    )
    if (
        not access.get("active")
        or "deals" not in (access.get("permissions") or [])
        or not lead_id
        or not allowed
    ):
        return DealActionResult(
            {"success": False, "error": "Bu sövdələşmə üçün icazəniz yoxdur."},
            403,
        )

    pipeline_id = int(context.target_pipeline_id)
    context.pipeline_stage_cache.pop(pipeline_id, None)
    stages, names, _ui = await asyncio.to_thread(
        context.load_pipeline_stage_maps,
        pipeline_id,
        fallback=False,
    )
    expected = _normalized_stage_name("Təxirə salınıb")
    matches = [
        int(stage_id)
        for stage_id, name in names.items()
        if _normalized_stage_name(name) == expected and int(stage_id) not in {142, 143}
    ]
    if len(matches) != 1:
        return DealActionResult(
            {
                "success": False,
                "error": "Sövdələşmələr vərəqində Təxirə salınıb mərhələsi tapılmadı.",
            },
            400,
        )

    status_id = matches[0]
    updated = await asyncio.to_thread(
        context.update_lead,
        lead_id,
        {"pipeline_id": pipeline_id, "status_id": status_id},
    )
    if not updated:
        return DealActionResult(
            {"success": False, "error": "Sövdələşmə köçürülmədi. Yenidən cəhd edin."},
            502,
        )

    context.invalidate_overview()
    context.clear_terminal_reentry(lead_id)
    stage_key = next(
        (key for key, stage_id in stages.items() if int(stage_id) == status_id),
        "",
    )
    context.record_pulse(
        lead_id,
        "deal_edit",
        pipeline_id=pipeline_id,
        stage_key=stage_key,
    )
    return DealActionResult(
        {
            "success": True,
            "pipeline_id": pipeline_id,
            "status_id": status_id,
            "stage_key": stage_key,
            "stage_name": names[status_id],
            "message": "Sövdələşmə Təxirə salınıb mərhələsinə köçürüldü.",
        }
    )


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def handle_deal_note(
    action: str,
    data: Mapping[str, Any],
    chat_id: int,
    context: DealNoteContext,
) -> DealActionResult:
    """Create, edit or softly delete one note for an accessible deal."""
    lead_id = _integer(data.get("lead_id"))
    note_id = _integer(data.get("note_id"))
    text = str(data.get("text") or "").strip()
    entity_type = str(data.get("entity_type") or "leads")
    entity_id = _integer(data.get("entity_id"))
    if not entity_id and context.note_entity_kind(entity_type) == "leads":
        entity_id = lead_id

    required = bool(lead_id and context.lead_allowed_for_chat(lead_id, chat_id))
    if action == "deal_add_note":
        required = required and bool(text)
    else:
        required = required and bool(note_id and entity_id)
        if action == "deal_edit_note":
            required = required and bool(text)
    if not required:
        return DealActionResult(
            {"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."},
            400,
        )

    if action == "deal_add_note":
        result = context.add_note(lead_id, text, "leads")
        context.invalidate_overview()
        if result:
            context.record_pulse(lead_id, "deal_add_note")
        return DealActionResult(
            {
                "success": bool(result),
                "note_id": context.first_note_id(result),
                "message": "Qeyd əlavə edildi." if result else "Qeyd əlavə olunmadı.",
            }
        )

    if action == "deal_edit_note":
        ok, error = context.update_note(note_id, text, entity_type, entity_id)
        success_message, failure_message = "Qeyd yeniləndi.", "Qeyd yenilənmədi."
    else:
        ok, error = context.delete_note(note_id, entity_type, entity_id)
        success_message, failure_message = "Qeyd silindi.", "Qeyd silinmədi."
    if ok:
        context.invalidate_overview()
        context.record_pulse(lead_id, action)
    return DealActionResult(
        {
            "success": bool(ok),
            "message": success_message if ok else failure_message,
            "error": "" if ok else error,
        },
        200 if ok else 400,
    )
