"""Legacy employee-to-Kommo-funnel ownership rules."""

from __future__ import annotations

from legacy_employee_access import get_rufat_compat_chat_ids
from legacy_kommo_pipeline_catalog import load_pipeline_stage_maps
from legacy_runtime_config import (
    ADMIN_CHAT_ID,
    GOZLEME_PIPELINE_ID,
    HUSEYN_CHAT_ID,
    HUSEYN_PIPELINE_ID,
    KOMMO_TASK_RESPONSIBLE_USER_ID,
    KOMMO_USERS,
    NIZAMI_PIPELINE_ID,
    PIPELINE_ID,
    RASIM_CHAT_ID,
    RASIM_PIPELINE_ID,
    RUFAT_CHAT_ID,
    RUFAT_PIPELINE_ID,
    STAGES,
    STAGE_NAMES,
)


def is_rufat_chat(chat_id) -> bool:
    try:
        return int(chat_id) in get_rufat_compat_chat_ids()
    except (TypeError, ValueError):
        return False


def get_funnel_owner(chat_id) -> dict | None:
    try:
        employee_id = int(chat_id)
    except (TypeError, ValueError):
        return None
    if is_rufat_chat(employee_id):
        stages, names, ui = load_pipeline_stage_maps(RUFAT_PIPELINE_ID)
        return {
            "chat_id": RUFAT_CHAT_ID,
            "pipeline_id": int(RUFAT_PIPELINE_ID),
            "name": "Rüfət Həsənzadə",
            "stages": stages,
            "stage_names": names,
            "ui_stages": ui,
        }
    mapping = {
        ADMIN_CHAT_ID: (NIZAMI_PIPELINE_ID, "Nizami Qasımov"),
        HUSEYN_CHAT_ID: (HUSEYN_PIPELINE_ID, "Hüseyn Səfərov"),
        RASIM_CHAT_ID: (RASIM_PIPELINE_ID, "Rasim Əsgərov"),
    }
    row = mapping.get(employee_id)
    if not row:
        return None
    pipeline_id, name = row
    stages, names, ui = load_pipeline_stage_maps(pipeline_id)
    return {
        "chat_id": employee_id,
        "pipeline_id": int(pipeline_id),
        "name": name,
        "stages": stages,
        "stage_names": names,
        "ui_stages": ui,
    }


def is_funnel_chat(chat_id) -> bool:
    return get_funnel_owner(chat_id) is not None


def all_personal_pipeline_ids() -> set[int]:
    return {
        int(RUFAT_PIPELINE_ID), int(NIZAMI_PIPELINE_ID),
        int(HUSEYN_PIPELINE_ID), int(RASIM_PIPELINE_ID),
    }


def employee_personal_pipeline_ids() -> set[int]:
    return {int(RUFAT_PIPELINE_ID), int(HUSEYN_PIPELINE_ID), int(RASIM_PIPELINE_ID)}


def kommo_user_id_for_employee_funnel(pipeline_id: int) -> int | None:
    return KOMMO_TASK_RESPONSIBLE_USER_ID if int(pipeline_id) in employee_personal_pipeline_ids() else None


def owner_name_for_pipeline(pipeline_id: int) -> str:
    return {
        int(RUFAT_PIPELINE_ID): "Rüfət Həsənzadə",
        int(NIZAMI_PIPELINE_ID): "Nizami Qasımov",
        int(HUSEYN_PIPELINE_ID): "Hüseyn Səfərov",
        int(RASIM_PIPELINE_ID): "Rasim Əsgərov",
    }.get(int(pipeline_id), "")


def _lead_pipeline_id(lead) -> int:
    raw = (lead or {}).get("pipeline_id", 0) if isinstance(lead, dict) else 0
    if isinstance(raw, dict):
        raw = raw.get("id") or raw.get("pipeline_id") or 0
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def employee_name_for_lead(lead: dict | None) -> str:
    if not isinstance(lead, dict):
        return ""
    name = owner_name_for_pipeline(_lead_pipeline_id(lead))
    return name or str(KOMMO_USERS.get(lead.get("responsible_user_id"), "") or "").strip()


def personal_entry_stage(pipeline_id: int) -> tuple[int, int] | None:
    stages, _names, ui = load_pipeline_stage_maps(pipeline_id)
    for key, _label in ui:
        if key in {"nerazobrannoye", "ugurlu", "imtina"}:
            continue
        status_id = stages.get(key)
        if status_id:
            return int(pipeline_id), int(status_id)
    status_id = stages.get("sorgular") or stages.get("nizami")
    return (int(pipeline_id), int(status_id)) if status_id else None


def get_pipeline_id_for_chat(chat_id=None) -> int:
    owner = get_funnel_owner(chat_id)
    return int(owner["pipeline_id"]) if owner else PIPELINE_ID


def get_pipeline_stages_for_chat(chat_id=None) -> tuple[dict, dict]:
    owner = get_funnel_owner(chat_id)
    return (owner["stages"], owner["stage_names"]) if owner else (STAGES, STAGE_NAMES)


def get_rufat_completion_stage(
    pipeline_key: str,
    stage_key: str,
    chat_id=None,
    lead_pipeline_id: int | None = None,
) -> tuple[int, int, str] | None:
    if pipeline_key in ("rufat", "samil", "own", "personal"):
        owner = get_funnel_owner(chat_id)
        pipeline_id = int(lead_pipeline_id or (owner or {}).get("pipeline_id") or 0)
        if pipeline_id in all_personal_pipeline_ids():
            stages, names, _ui = load_pipeline_stage_maps(pipeline_id)
            status_id = stages.get(stage_key)
            if status_id:
                return pipeline_id, int(status_id), names.get(int(status_id), stage_key)
        if owner:
            status_id = owner["stages"].get(stage_key)
            if status_id:
                return int(owner["pipeline_id"]), int(status_id), owner["stage_names"].get(int(status_id), stage_key)
        return None
    if pipeline_key != "operations":
        return None
    normalized = "rufat" if stage_key in ("samil", "shamil") else stage_key
    operation_stages = {
        "rufat": (109988184, "Rüfət Həsənzadə"), "soltan": (109988188, "Soltan Abbasov"),
        "huseyn": (109988192, "Hüseyn Səfərov"), "nizami": (109988196, "Nizami Qasımov"),
        "rasim": (109988200, "Rasim Əsgərov"), "sermaye": (109988204, "Sərmayə Əhmədsoy"),
        "asya": (109988208, "Asya Agayeva"), "nurane": (109988212, "Nuranə Şirinova"),
        "ugurlu": (142, "Uğurla tamamlandı"), "imtina": (143, "İmtina olundu"),
    }
    stage = operation_stages.get(normalized)
    return (GOZLEME_PIPELINE_ID, int(stage[0]), stage[1]) if stage else None
