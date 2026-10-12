"""Legacy employee-to-Kommo-funnel ownership rules."""

from __future__ import annotations

from legacy_employee_access import (
    _load_employee_access_records, employee_access_profile, get_rufat_compat_chat_ids,
)
from legacy_user_registry import load_users
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
    stored = _load_employee_access_records().get(str(employee_id))
    if isinstance(stored, dict) and "pipeline_id" in stored:
        try:
            pipeline_id = int(stored.get("pipeline_id") or 0)
        except (TypeError, ValueError):
            pipeline_id = 0
        if pipeline_id <= 0 or not employee_access_profile(employee_id).get("active"):
            return None
        user = load_users().get(str(employee_id)) or {}
        stages, names, ui = load_pipeline_stage_maps(pipeline_id)
        return {
            "chat_id": employee_id,
            "pipeline_id": pipeline_id,
            "name": str(user.get("name") or "Əməkdaş").strip(),
            "stages": stages,
            "stage_names": names,
            "ui_stages": ui,
        }
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


def configured_funnel_owners() -> list[dict]:
    """Return active funnel owners from saved settings plus legacy fallbacks."""
    candidate_ids = set(_load_employee_access_records()) | set(load_users())
    candidate_ids.update({str(ADMIN_CHAT_ID), str(RUFAT_CHAT_ID), str(HUSEYN_CHAT_ID), str(RASIM_CHAT_ID)})
    owners: list[dict] = []
    seen_pipelines: set[int] = set()
    for raw_id in candidate_ids:
        try:
            owner = get_funnel_owner(int(raw_id))
        except (TypeError, ValueError):
            continue
        if not owner:
            continue
        pipeline_id = int(owner.get("pipeline_id") or 0)
        if not pipeline_id or pipeline_id in seen_pipelines:
            continue
        seen_pipelines.add(pipeline_id)
        owners.append(owner)
    return sorted(owners, key=lambda row: (str(row.get("name") or "").casefold(), int(row["pipeline_id"])))


def all_personal_pipeline_ids() -> set[int]:
    return {int(owner["pipeline_id"]) for owner in configured_funnel_owners()}


def employee_personal_pipeline_ids() -> set[int]:
    return {
        int(owner["pipeline_id"]) for owner in configured_funnel_owners()
        if int(owner.get("chat_id") or 0) != int(ADMIN_CHAT_ID)
    }


def kommo_user_id_for_employee_funnel(pipeline_id: int) -> int | None:
    return KOMMO_TASK_RESPONSIBLE_USER_ID if int(pipeline_id) in employee_personal_pipeline_ids() else None


def owner_name_for_pipeline(pipeline_id: int) -> str:
    wanted = int(pipeline_id)
    owner = next((row for row in configured_funnel_owners() if int(row["pipeline_id"]) == wanted), None)
    return str((owner or {}).get("name") or "")


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
