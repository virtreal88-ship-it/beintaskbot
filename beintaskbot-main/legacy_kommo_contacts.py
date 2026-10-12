"""Legacy Kommo contact search and CRUD operations."""

from __future__ import annotations

import logging
import re

from legacy_runtime_config import KOMMO_BASE_URL

logger = logging.getLogger(__name__)
_http = None
_headers: dict[str, str] = {}


def configure_contact_service(*, http, headers: dict[str, str], app_logger=None) -> None:
    global _http, _headers, logger
    _http = http
    _headers = dict(headers)
    if app_logger is not None:
        logger = app_logger


def search_contact_by_phone(phone: str) -> list:
    digits = re.sub(r"[^\d]", "", phone)
    target_suffix = digits[-9:] if len(digits) >= 9 else digits
    variants = set()
    if len(digits) >= 9:
        variants.add(digits[-9:])
    if digits.startswith("994"):
        variants.add(digits[3:])
        variants.add("+" + digits)
    elif digits.startswith("0") and len(digits) == 10:
        variants.add(digits[1:])
        variants.add("994" + digits[1:])
    else:
        variants.add(digits)
    variants.add(phone.strip())
    contacts = []
    seen_ids = set()
    for variant in variants:
        try:
            response = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/contacts",
                headers=_headers,
                params={"query": variant, "limit": 5},
                timeout=8,
            )
            if response.status_code != 200:
                continue
            for contact in response.json().get("_embedded", {}).get("contacts", []):
                if contact["id"] in seen_ids:
                    continue
                matched = False
                for field in contact.get("custom_fields_values", []) or []:
                    if field.get("field_code") != "PHONE":
                        continue
                    for value in field.get("values", []):
                        contact_digits = re.sub(r"[^\d]", "", value.get("value", ""))
                        if contact_digits[-9:] == target_suffix:
                            matched = True
                            break
                    if matched:
                        break
                if matched:
                    seen_ids.add(contact["id"])
                    contacts.append(contact)
        except Exception as exc:
            logger.error("Search contact error (%s): %s", variant, exc)
    return contacts


def search_contacts_by_query(query: str) -> list:
    value = str(query or "").strip()
    digits = re.sub(r"[^\d]", "", value)
    if len(digits) >= 7:
        return search_contact_by_phone(value)
    if len(value) < 2:
        return []
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/contacts",
            headers=_headers,
            params={"query": value, "limit": 10, "with": "leads"},
            timeout=8,
        )
        if response.status_code != 200:
            return []
        return [
            item for item in (response.json().get("_embedded") or {}).get("contacts") or []
            if isinstance(item, dict)
        ]
    except Exception as exc:
        logger.error("Search contacts query error: %s", exc)
        return []


def _contact_phone_value(contact: dict) -> str:
    for field in (contact or {}).get("custom_fields_values") or []:
        if not isinstance(field, dict) or field.get("field_code") != "PHONE":
            continue
        values = field.get("values") or []
        if values and isinstance(values[0], dict):
            return str(values[0].get("value") or "").strip()
    return ""


def get_contact_details(contact_id: int) -> dict | None:
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}",
            headers=_headers,
            params={"with": "leads"},
            timeout=8,
        )
        return response.json() if response.status_code == 200 else None
    except Exception as exc:
        logger.error("Contact details error: %s", exc)
        return None


def update_contact_kommo(contact_id: int, data: dict) -> dict | None:
    try:
        response = _http.patch(
            f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}",
            headers=_headers,
            json=data,
            timeout=8,
        )
        return response.json() if response.status_code == 200 else None
    except Exception as exc:
        logger.error("Update contact error: %s", exc)
        return None


def create_contact_kommo(
    name: str,
    phone: str,
    custom_fields: list | None = None,
    responsible_user_id: int = 10932455,
) -> dict | None:
    payload = [{
        "name": name,
        "responsible_user_id": responsible_user_id,
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}
        ] + (custom_fields or []),
    }]
    try:
        response = _http.post(
            f"{KOMMO_BASE_URL}/api/v4/contacts",
            headers=_headers,
            json=payload,
            timeout=8,
        )
        return response.json() if response.status_code in (200, 201) else None
    except Exception as exc:
        logger.error("Create contact error: %s", exc)
        return None


def create_contact_for_lead(lead_id: int, name: str, phone: str) -> dict | None:
    """Create one contact already linked to an existing Kommo lead."""
    payload = [{
        "name": str(name or "").strip() or "Müştəri",
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}
        ] if phone else [],
        "_embedded": {"leads": [{"id": int(lead_id)}]},
    }]
    try:
        response = _http.post(
            f"{KOMMO_BASE_URL}/api/v4/contacts",
            headers=_headers,
            json=payload,
            timeout=8,
        )
        return response.json() if response.status_code in (200, 201) else None
    except Exception as exc:
        logger.error("Create linked contact error: %s", exc)
        return None


def _leads_linked_to_contact(contact_id: int) -> list:
    contact = get_contact_details(int(contact_id))
    leads = list((contact or {}).get("_embedded", {}).get("leads", []) or [])
    if leads:
        return leads
    try:
        response = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/contacts/{int(contact_id)}/leads",
            headers=_headers,
            timeout=8,
        )
        if response.status_code == 200:
            return list(response.json().get("_embedded", {}).get("leads", []) or [])
    except Exception as exc:
        logger.error("Contact leads lookup error: %s", exc)
    return []
