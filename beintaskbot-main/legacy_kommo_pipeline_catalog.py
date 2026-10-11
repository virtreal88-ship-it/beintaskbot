"""Legacy Kommo pipeline/stage catalog with a bounded process cache."""

from __future__ import annotations

import logging
import re
import time

from crm_stage_labels import stage_display_name
from legacy_runtime_config import (
    HUSEYN_PIPELINE_ID,
    HUSEYN_STAGE_NAMES,
    HUSEYN_STAGES,
    KOMMO_BASE_URL,
    NIZAMI_PIPELINE_ID,
    NIZAMI_STAGE_NAMES,
    NIZAMI_STAGES,
    RASIM_PIPELINE_ID,
    RASIM_STAGE_NAMES,
    RASIM_STAGES,
    RUFAT_PIPELINE_ID,
    RUFAT_STAGE_NAMES,
    RUFAT_STAGES,
)

logger = logging.getLogger(__name__)
_http = None
_headers: dict[str, str] = {}
_pipeline_stage_cache: dict[int, tuple[dict, dict, list]] = {}
_all_pipelines_cache: list[dict] | None = None
_all_pipelines_cache_at = 0.0


def configure_pipeline_catalog(*, http, headers: dict[str, str], app_logger=None) -> None:
    """Attach the already configured legacy transport without network work."""
    global _http, _headers, logger
    _http = http
    _headers = dict(headers)
    if app_logger is not None:
        logger = app_logger


def _fold_stage_name(value: str) -> str:
    table = str.maketrans({
        "ə": "e", "ı": "i", "ö": "o", "ü": "u", "ğ": "g", "ş": "s", "ç": "c",
        "Ə": "e", "İ": "i", "Ö": "o", "Ü": "u", "Ğ": "g", "Ş": "s", "Ç": "c",
    })
    return str(value or "").translate(table).casefold().strip()


def _stage_key_from_kommo(name: str, status_id: int) -> str:
    try:
        stage_id = int(status_id)
    except (TypeError, ValueError):
        stage_id = 0
    if stage_id == 142:
        return "ugurlu"
    if stage_id == 143:
        return "imtina"
    folded = _fold_stage_name(name)
    needles = (
        ("nerazobrann", "nerazobrannoye"), ("неразобран", "nerazobrannoye"),
        ("unparsed", "nerazobrannoye"), ("sorgular", "sorgular"),
        ("sorgu", "sorgular"), ("danisiq", "danisiqlar"), ("telimat", "telimat"),
        ("yeni sifaris", "yeni_sifaris"), ("geri don", "geri_donusler"),
        ("soyuq zeng", "soyuq_zeng"), ("cavab gozlenilir", "cavab_gozlenilir"),
        ("gorus", "gorusler"), ("qurasdirma", "qurashdirma"),
        ("qurashdirma", "qurashdirma"), ("muzakire", "muzakire"),
        ("icra olunur", "icra_olunur"), ("gozleme", "gozleme"),
        ("ugurlu", "ugurlu"), ("imtina", "imtina"),
        ("successfully", "ugurlu"), ("closed - won", "ugurlu"),
        ("closed - lost", "imtina"),
    )
    for needle, key in needles:
        if needle in folded:
            return key
    return re.sub(r"[^a-z0-9]+", "_", folded).strip("_") or f"stage_{stage_id}"


def _personal_pipeline_packs() -> dict[int, tuple[dict, dict]]:
    return {
        int(RUFAT_PIPELINE_ID): (RUFAT_STAGES, RUFAT_STAGE_NAMES),
        int(HUSEYN_PIPELINE_ID): (HUSEYN_STAGES, HUSEYN_STAGE_NAMES),
        int(RASIM_PIPELINE_ID): (RASIM_STAGES, RASIM_STAGE_NAMES),
        int(NIZAMI_PIPELINE_ID): (NIZAMI_STAGES, NIZAMI_STAGE_NAMES),
    }


def _stage_maps_from_statuses(statuses: list) -> tuple[dict, dict, list]:
    stages: dict[str, int] = {}
    names: dict[int, str] = {}
    ui: list[tuple[str, str]] = []
    used: set[str] = set()
    rows = sorted(statuses or [], key=lambda row: int((row or {}).get("sort") or 0))
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            stage_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        label = stage_display_name(stage_id, row.get("name") or stage_id)
        key = _stage_key_from_kommo(label, stage_id)
        if key in used:
            key = f"{key}_{stage_id}"
        used.add(key)
        stages[key] = stage_id
        names[stage_id] = label
        ui.append((key, label))
    return stages, names, ui


def load_pipeline_stage_maps(pipeline_id: int, *, fallback: bool = True) -> tuple[dict, dict, list]:
    pipeline = int(pipeline_id)
    cached = _pipeline_stage_cache.get(pipeline)
    if cached:
        return cached
    pack = _personal_pipeline_packs().get(pipeline)
    if pack:
        stages, names = pack
        ui = [(key, names.get(stage_id, key)) for key, stage_id in stages.items()]
        _pipeline_stage_cache[pipeline] = (dict(stages), dict(names), ui)
        return _pipeline_stage_cache[pipeline]
    stages: dict[str, int] = {}
    names: dict[int, str] = {}
    ui: list[tuple[str, str]] = []
    try:
        response = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads/pipelines/{pipeline}", headers=_headers, timeout=12)
        if response.status_code == 200:
            statuses = (response.json().get("_embedded") or {}).get("statuses") or []
            stages, names, ui = _stage_maps_from_statuses(statuses)
    except Exception as exc:
        logger.warning("Pipeline %s stage load failed: %s", pipeline, exc)
    if not stages and fallback:
        stages, names, ui = dict(RUFAT_STAGES), dict(RUFAT_STAGE_NAMES), [
            (key, RUFAT_STAGE_NAMES.get(stage_id, key)) for key, stage_id in RUFAT_STAGES.items()
        ]
    if stages:
        _pipeline_stage_cache[pipeline] = (stages, names, ui)
    return stages, names, ui


def _ordered_ui_for_pipeline(pipeline_id: int, statuses: list) -> list[dict]:
    stages, names, ui = load_pipeline_stage_maps(pipeline_id, fallback=False)
    key_by_id = {int(stage_id): key for key, stage_id in stages.items()}
    ordered: list[dict] = []
    seen: set[str] = set()
    for row in sorted(statuses or [], key=lambda item: int((item or {}).get("sort") or 0)):
        if not isinstance(row, dict):
            continue
        try:
            stage_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        key = key_by_id.get(stage_id)
        if not key:
            continue
        ordered.append({"key": key, "name": names.get(stage_id) or str(row.get("name") or key)})
        seen.add(key)
    ordered.extend({"key": key, "name": label} for key, label in ui if key not in seen)
    return ordered


def load_all_kommo_pipelines(*, force: bool = False) -> list[dict]:
    global _all_pipelines_cache, _all_pipelines_cache_at
    now = time.time()
    if not force and _all_pipelines_cache and now - _all_pipelines_cache_at < 300:
        return _all_pipelines_cache
    try:
        response = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads/pipelines", headers=_headers, timeout=15)
        rows = (response.json().get("_embedded") or {}).get("pipelines") or [] if response.status_code == 200 else []
    except Exception as exc:
        logger.warning("Kommo pipelines list failed: %s", exc)
        return _all_pipelines_cache or []
    result: list[dict] = []
    packs = _personal_pipeline_packs()
    for pipeline in sorted(rows, key=lambda item: int((item or {}).get("sort") or 0)):
        if not isinstance(pipeline, dict) or pipeline.get("is_archive"):
            continue
        try:
            pipeline_id = int(pipeline.get("id"))
        except (TypeError, ValueError):
            continue
        statuses = (pipeline.get("_embedded") or {}).get("statuses") or []
        if pipeline_id not in packs and pipeline_id not in _pipeline_stage_cache:
            stages, names, ui = _stage_maps_from_statuses(statuses)
            if stages:
                _pipeline_stage_cache[pipeline_id] = (stages, names, ui)
        ordered = _ordered_ui_for_pipeline(pipeline_id, statuses)
        if ordered:
            result.append({
                "id": pipeline_id,
                "name": str(pipeline.get("name") or pipeline_id),
                "sort": int(pipeline.get("sort") or 0),
                "is_main": bool(pipeline.get("is_main")),
                "stages": ordered,
            })
    if result:
        _all_pipelines_cache, _all_pipelines_cache_at = result, now
    return result or (_all_pipelines_cache or [])
