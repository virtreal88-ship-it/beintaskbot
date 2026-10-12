"""Explicit deal routing and stage edits for the legacy web application."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from legacy_action_result import LegacyActionResult


@dataclass(frozen=True)
class DealRoutingContext:
    lead_allowed_for_chat: Callable[[int, int], bool]
    normalize_assignee_name: Callable[[object], str]
    move_lead_to_assignee: Callable[[int, str], bool]
    route_for_employee: Callable[[str], tuple[int, int] | None]
    load_pipeline_stage_maps: Callable[..., tuple[Mapping[str, int], Mapping[int, str], list[Any]]]
    invalidate_overview: Callable[[], None]
    record_pulse: Callable[..., None]
    get_partner: Callable[[int], Any]
    get_partner_list: Callable[[int], list[Any]]
    get_lead: Callable[[int], Mapping[str, Any] | None]
    lead_pipeline_id: Callable[[Mapping[str, Any]], int]
    pipeline_for_chat: Callable[[int], int]
    is_admin: Callable[[int], bool]
    personal_pipeline_ids: Callable[[], set[int]]
    update_lead: Callable[[int, dict[str, Any]], Any]
    apply_source: Callable[..., Any]
    patch_overview_stage: Callable[[int, str], None]
    clear_terminal_reentry: Callable[[int], None]


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def handle_deal_edit(
    data: Mapping[str, Any],
    chat_id: int,
    context: DealRoutingContext,
) -> LegacyActionResult:
    """Route a deal to an employee or update its stage inside an allowed pipeline."""
    lead_id = _integer(data.get("lead_id"))
    if not lead_id or not context.lead_allowed_for_chat(lead_id, chat_id):
        return LegacyActionResult(
            {"success": False, "error": "Доступ запрещён."}, 403
        )

    assignee_name = context.normalize_assignee_name(
        data.get("assignee_name") or data.get("executor")
    )
    if assignee_name:
        if not context.move_lead_to_assignee(lead_id, assignee_name):
            return LegacyActionResult(
                {"success": False, "error": "İcraçı üçün vərəq tapılmadı."}, 400
            )
        route = context.route_for_employee(assignee_name)
        pipeline_id = _integer(route[0]) if route else 0
        status_id = _integer(route[1]) if route else 0
        stage_key = ""
        stage_name = ""
        if pipeline_id and status_id:
            stages, names, _ui = context.load_pipeline_stage_maps(pipeline_id)
            stage_name = str(names.get(status_id) or "")
            stage_key = next(
                (key for key, value in stages.items() if int(value) == status_id), ""
            )
        context.invalidate_overview()
        context.record_pulse(
            lead_id, "deal_edit", pipeline_id=pipeline_id, stage_key=stage_key
        )
        return LegacyActionResult({
            "success": True,
            "message": f"Sövdələşmə {assignee_name}-ə yönləndirildi.",
            "assignee_name": assignee_name,
            "pipeline_id": pipeline_id,
            "stage_key": stage_key,
            "stage_name": stage_name,
            "partner": context.get_partner(lead_id),
            "partners": context.get_partner_list(chat_id),
        })

    stage_key = str(data.get("stage_key") or "")
    lead = context.get_lead(lead_id) or {}
    pipeline_id = context.lead_pipeline_id(lead) or context.pipeline_for_chat(chat_id)
    if context.is_admin(chat_id):
        requested_pipeline = _integer(data.get("pipeline_id"))
        if requested_pipeline:
            pipeline_id = requested_pipeline
        stages, _names, _ui = context.load_pipeline_stage_maps(
            pipeline_id, fallback=False
        )
    elif pipeline_id in context.personal_pipeline_ids():
        stages, _names, _ui = context.load_pipeline_stage_maps(pipeline_id)
    else:
        stages = {}
    if stage_key not in stages:
        return LegacyActionResult(
            {"success": False, "error": "Mərhələ tapılmadı."}
        )
    if not context.update_lead(
        lead_id, {"status_id": stages[stage_key], "pipeline_id": pipeline_id}
    ):
        return LegacyActionResult(
            {"success": False, "error": "Mərhələ dəyişdirilmədi."}
        )
    source_label = str(data.get("source") or data.get("menbe") or "").strip()
    if source_label:
        contacts = (lead.get("_embedded") or {}).get("contacts") or []
        contact_id = _integer(contacts[0].get("id")) if contacts else 0
        context.apply_source(contact_id or None, lead_id, source_label, overwrite=True)
        context.invalidate_overview()
    context.patch_overview_stage(lead_id, stage_key)
    context.clear_terminal_reentry(lead_id)
    context.record_pulse(
        lead_id, "deal_edit", pipeline_id=pipeline_id, stage_key=stage_key
    )
    return LegacyActionResult({
        "success": True,
        "message": "Sövdələşmə yeniləndi.",
        "stage_key": stage_key,
        "partner": context.get_partner(lead_id),
        "partners": context.get_partner_list(chat_id),
    })
