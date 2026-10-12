"""Legacy webapp contact editing isolated from the HTTP dispatcher."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from legacy_action_result import LegacyActionResult


@dataclass(frozen=True)
class DealContactContext:
    is_funnel_chat: Callable[[int], bool]
    is_admin: Callable[[int], bool]
    lead_allowed_for_chat: Callable[[int, int], bool]
    get_lead: Callable[[int], Mapping[str, Any] | None]
    update_contact: Callable[[int, dict[str, Any]], Any]
    create_contact_for_lead: Callable[[int, str, str], Any]
    update_lead: Callable[[int, dict[str, Any]], Any]
    patch_overview_contact: Callable[[int, str, str], None]
    invalidate_overview: Callable[[], None]
    record_pulse: Callable[..., None]


def _integer(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _first_contact_id(lead: Mapping[str, Any] | None) -> int:
    contacts = ((lead or {}).get("_embedded") or {}).get("contacts") or []
    if not contacts:
        return 0
    return _integer(contacts[0].get("id"))


def handle_deal_contact_edit(
    data: Mapping[str, Any],
    chat_id: int,
    context: DealContactContext,
) -> LegacyActionResult:
    """Update the linked contact, creating it only when no usable contact exists."""
    lead_id = _integer(data.get("lead_id"))
    name = str(data.get("name") or data.get("contact_name") or "").strip()
    phone = str(data.get("phone") or "").strip()
    access = bool(
        lead_id
        and (
            context.is_funnel_chat(chat_id)
            or context.is_admin(chat_id)
            or context.lead_allowed_for_chat(lead_id, chat_id)
        )
    )
    if not access:
        return LegacyActionResult(
            {"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."},
            400,
        )

    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    if phone:
        payload["custom_fields_values"] = [{
            "field_code": "PHONE",
            "values": [{"value": phone, "enum_code": "WORK"}],
        }]
    if not payload:
        return LegacyActionResult(
            {"success": False, "error": "Ad və ya telefon yazın."}, 400
        )

    contact_id = _integer(data.get("contact_id"))
    ok = bool(contact_id and context.update_contact(contact_id, payload))
    if not ok:
        lead = context.get_lead(lead_id) or {}
        contact_id = _first_contact_id(lead)
        ok = bool(contact_id and context.update_contact(contact_id, payload))
    if not ok:
        ok = bool(context.create_contact_for_lead(lead_id, name, phone))

    if ok:
        if name:
            try:
                context.update_lead(lead_id, {"name": name})
            except Exception:
                pass
        context.patch_overview_contact(lead_id, name, phone)
        context.invalidate_overview()
        context.record_pulse(
            lead_id,
            "deal_edit_contact",
            contact_name=name,
            phone=phone,
        )
    return LegacyActionResult(
        {
            "success": ok,
            "message": "Kontakt yeniləndi." if ok else "Kontakt yenilənmədi.",
        }
    )
