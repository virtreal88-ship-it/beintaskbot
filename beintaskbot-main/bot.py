#!/usr/bin/env python3
"""
Telegram Bot with Kommo CRM Integration — AI Function Calling Architecture
- OpenAI function calling replaces old state machine
- Role-based user registration
- Background notifications (task deadlines, morning digest, stuck deals)
- Voice message transcription
- Conversation history (last 10 messages per user)
- Multi-variant phone search
- Azerbaijani interface
- Kommo webhook endpoint for stage change notifications
"""
import os
import re
from concurrent.futures import ThreadPoolExecutor
import json
import base64
import math
import hmac
import hashlib
import logging
import requests
import subprocess
import tempfile
import glob
import traceback
import asyncio
import uuid
import threading
import collections
import random
import time as _time_module
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, quote, unquote
from openai import OpenAI
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, MenuButtonWebApp, WebAppInfo
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from aiohttp import web
from pywebpush import webpush, WebPushException
from gh_storage import read_json, write_json
# import sqlite3  # replaced by gh_storage

# ─── Configuration ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
_KOMMO_TOKEN_FALLBACK = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImp0aSI6Ijk4MzE4ZjNhZjUzZTUzOTQ2MzkwMmJjYzM2NmMzZWY4YzI1MzFlMDRjNGM4NzUzYzg3MDM1MTYxOGIzYWM2Y2IyMDJjZjcxNjkwZTliYTIyIn0.eyJhdWQiOiIwNzkyYzY5ZS1hYjcxLTQ2MWQtYWY4YS05ODI0NjVjZDIyYWMiLCJqdGkiOiI5ODMxOGYzYWY1M2U1Mzk0NjM5MDJiY2MzNjZjM2VmOGMyNTMxZTA0YzRjODc1M2M4NzAzNTE2MThiM2FjNmNiMjAyY2Y3MTY5MGU5YmEyMiIsImlhdCI6MTc5MDE5MjIwNCwibmJmIjoxNzkwMTkyMjA0LCJleHAiOjE5Mjc2NzA0MDAsInN1YiI6IjEwOTMyNDU1IiwiZ3JhbnRfdHlwZSI6IiIsImFjY291bnRfaWQiOjMyNTI0MzU5LCJiYXNlX2RvbWFpbiI6ImtvbW1vLmNvbSIsInZlcnNpb24iOjIsInNjb3BlcyI6WyJjcm0iLCJmaWxlcyIsImZpbGVzX2RlbGV0ZSIsImxpc3RfZXh0ZXJuYWxfbWVzc2FnZXMiLCJub3RpZmljYXRpb25zIiwicHVzaF9ub3RpZmljYXRpb25zIiwic2VuZF9leHRlcm5hbF9tZXNzYWdlcyIsInVzZXJzX2FjdGl2YXRlIiwidXNlcnNfYWRkIiwidXNlcnNfZGVhY3RpdmF0ZSJdLCJoYXNoX3V1aWQiOiJiZDliOWU2ZC05ZTIxLTQzMjUtODIzYS04YmFhM2Q0NDg4ODgiLCJhcGlfZG9tYWluIjoiYXBpLWcua29tbW8uY29tIn0.AbWmj1TI6yhfr2bv3TUyeHWKSMuJFTkyHdhPEihPkg17FVtHenV1yB0pILNRWGrflI5MkBkJvY30B5oVnz3BgxYOmjVjGRnwIGDk1nuhIoJ6SDlWpXvTme3EQGP-0DlGx_ITEWrMpB7l25WnJb0S9VqVZEA-D5LckFN-UOjYH4us-EDfNxPqKT2tsFXMJd3jynsT6iJrYviBTU1eGrDNZhI3yCp5On-XKxVFK67nEdlfxkrZ6lJhevcJIREUwJmaQkJy4Md_ePNwMc6Dh7k6tP-i0ri58abSOPstaOGpoWSNhvPg2gqNsMxSpKXevelBZzvT0-k57NdsgW2yaANwsg"
KOMMO_TOKEN = (os.environ.get("KOMMO_TOKEN") or "").strip() or _KOMMO_TOKEN_FALLBACK
KOMMO_DOMAIN = "texnikidestek50.kommo.com"
KOMMO_BASE_URL = f"https://{KOMMO_DOMAIN}"
BAKU_TZ = timezone(timedelta(hours=4))
LLM_MODEL = "gpt-4.1-mini"
WEBHOOK_PORT = int(os.environ.get("PORT", 8080))

# VAPID keys for Web Push
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "6i8cjNC8eztEI8LpdwvKAFcKKr-lXR9oEES_zFIbN74")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "BH097FHI9PEdu_jf8XblQnnlS9mEtvPgKSnCkm5uERGGljVryVGl-dhTKKxg_HIfASiujCM_MF2A49N3xRTNNtc")
VAPID_CLAIMS = {"sub": "mailto:admin@beinsystems.com"}
# Push subscriptions loaded from gh_storage on demand

# OpenAI client
llm_client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY", "sk-C7kqpsGHciC9Mf9oA63xvy"),
    base_url=os.environ.get("OPENAI_API_BASE", "https://api.manus.im/api/llm-proxy/v1"),
)

# ─── Pipeline & Users Configuration ─────────────────────────────────────────
PIPELINE_ID = 8329347
# Rüfət's current Telegram account is canonical. Do not let an old deployment
# variable silently make the retired Şamil UID the primary account.
RUFAT_CHAT_ID = 6824377548
SAMIL_CHAT_ID = RUFAT_CHAT_ID  # Deprecated alias for old integrations
# Historical web-app links used Şamil's Telegram UID.  Keep those links
# working while the current employee account uses RUFAT_CHAT_ID.
RUFAT_COMPAT_CHAT_IDS = {RUFAT_CHAT_ID, 7962757442}
RUFAT_LOGIN_LINK = f"https://worker-production-3e3e.up.railway.app/webapp?uid={RUFAT_CHAT_ID}"
RUFAT_PIPELINE_ID = int(os.environ.get("RUFAT_PIPELINE_ID", os.environ.get("SAMIL_PIPELINE_ID", "14357580")))
# Rüfət's current pipeline snapshot. Do not alter other pipeline mappings here.
RUFAT_STAGES = {
    "nerazobrannoye": 110897252,
    "sorgular": 110897256,
    "danisiqlar": 110897260,
    "yeni_sifaris": 111109080,
    "geri_donusler": 110897264,
    "soyuq_zeng": 110897348,
    "cavab_gozlenilir": 110897336,
    "gorusler": 110897344,
    "qurashdirma": 111109084,
    "muzakire": 111109104,
    "ugurlu": 142,
    "imtina": 143,
}
RUFAT_STAGE_NAMES = {
    110897252: "Неразобранное",
    110897256: "sorgular",
    110897260: "danişıqlar",
    111109080: "yeni sifariş",
    110897264: "geri donüşlər",
    110897348: "soyuq zəng",
    110897336: "cavab gözlənilir",
    110897344: "görüşlər",
    111109084: "quraşdırma",
    111109104: "müzakirə",
    142: "Успешно реализовано",
    143: "Закрыто и не реализовано",
}
ADMIN_CHAT_ID = 1628569350
ADMIN_KOMMO_USER_ID = 10932455
HUSEYN_CHAT_ID = 7329891614
RASIM_CHAT_ID = 7920785774
NIZAMI_PIPELINE_ID = 14243944
# Kommo funnel literally named Sövdələşmələr. Nizami's Çatlar also reads it, including NÖMRƏ ALINIB.
SOVDELESMELER_PIPELINE_ID = 8329347
HUSEYN_PIPELINE_ID = 14358480
RASIM_PIPELINE_ID = 14461812
# Live Kommo snapshots. Nizami's pipeline is still the shared Gözləmə board.
HUSEYN_STAGES = {
    "nerazobrannoye": 110904704,
    "sorgular": 110904708,
    "icra_olunur": 110904712,
    "gorusler": 110904716,
    "qurashdirma": 110904792,
    "gozleme": 111702228,
    "muzakire": 111702232,
    "ugurlu": 142,
    "imtina": 143,
}
HUSEYN_STAGE_NAMES = {
    110904704: "Неразобранное",
    110904708: "Yeni sorgu",
    110904712: "icra olunur",
    110904716: "görüş",
    110904792: "quraşdırma",
    111702228: "gözləmə",
    111702232: "müzakirə",
    142: "Успешно реализовано",
    143: "Закрыто и не реализовано",
}
RASIM_STAGES = {
    "nerazobrannoye": 111700780,
    "sorgular": 111700784,
    "yeni_sifaris": 111700788,
    "icra_olunur": 111700792,
    "gozleme": 111702372,
    "gorusler": 111702376,
    "qurashdirma": 111702380,
    "muzakire": 111702384,
    "ugurlu": 142,
    "imtina": 143,
}
RASIM_STAGE_NAMES = {
    111700780: "Неразобранное",
    111700784: "Sorgular",
    111700788: "yeni sifariş",
    111700792: "icra olunur",
    111702372: "gözləmə",
    111702376: "görüş",
    111702380: "quraşdırma",
    111702384: "müzakirə",
    142: "Успешно реализовано",
    143: "Закрыто и не реализовано",
}
NIZAMI_STAGES = {
    "nerazobrannoye": 109988180,
    "sorgular": 109988196,
    "danisiqlar": 112086092,
    "telimat": 112086096,
    "yeni_sifaris": 112086100,
    "gozleme": 110774676,
    "muzakire": 110722180,
    "ugurlu": 142,
    "imtina": 143,
}
NIZAMI_STAGE_NAMES = {
    109988180: "Неразобранное",
    109988196: "yeni sorgu",
    112086092: "danışıqlar",
    112086096: "təlimat",
    112086100: "yeni sifariş",
    110774676: "gözləmə",
    110722180: "Müzakirə",
    142: "Успешно реализовано",
    143: "Закрыто и не реализовано",
}
TECHNICAL_SUPPORT_NAME = "Texniki Dəstək"
_UPD_MARKER = {"Rüfət": ("Rüfət Həsənzadə", 15532668), "Soltan": ("Soltan Abbasov", 15531960), "Hüseyn": ("Hüseyn Səfərov", 15532668), "Rasim": ("Rasim Əsgərov", 15532668), "Özüm": ("Nizami Qasımov", 10932455)}

STAGES = {
    "nerazobrannoye": 66107683,
    "danisiqlar": 108537924,
    "qiymet_teklifi": 66107691,
    "teqdimat": 66107699,
    "teqdimat_olundu": 96880440,
    "yeni_sifaris": 94525176,
    "gorus": 108537892,
    "daxili_muzakire": 108538104,
    "qurashdirma": 108537896,
    "dusunur": 108537976,
    "ugurlu": 142,
    "imtina": 143,
}
STAGE_NAMES = {
    66107683: "Nerazobrannoye",
    108537924: "danışıqlar",
    66107691: "Qiymət təklifi",
    66107699: "Təqdimat",
    96880440: "Təqdimat olundu",
    94525176: "yeni sifariş",
    108537892: "görüş",
    108538104: "daxili müzakirə",
    108537896: "quraşdırma",
    108537976: "Düşünür",
    142: "uğurlu sifariş",
    143: "imtina olundu",
}
# Notify Admin only when a Sövdələşmələr deal moves to "Nömrə alınıb".
# Kommo status ID verified from the pipeline configuration.
NOTIFY_STAGE_ID = 108537924
KOMMO_USERS = {
    10932455: "Nizami Qasımov",
    15531960: "Soltan Abbasov",
    15532668: "Admin",
}
_STAGE_TASK_TEXTS = {
    "qiymet_teklifi": "Qiymət təklifini göndər",
    "teqdimat": "Müştəriyə təqdimat keçirmək",
    "yeni_sifaris": "Yeni sifarişi rəsmiləşdirmək",
    "gorus": "Müştəri ilə görüş keçirmək",
    "qurashdirma": "Quraşdırmanı həyata keçirmək",
}
TASK_TYPE_NAMES = {
    1: "Əlaqə saxla",
    2: "Görüş",
    3263995: "Təqdimat",
    3263999: "Quraşdırma",
    3267595: "Zəng et",
    4229224: "Cavab gözlənilir",
    4232112: "aktiv",
    4232108: "Import",
    4239844: "passiv",
}

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bot")

# ─── User Registration Storage ───────────────────────────────────────────────
_USER_DB_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")

# Telegram identities are fixed employee accounts. They must remain usable after
# every deploy/restart without requiring the employee to send /start again.
_KNOWN_EMPLOYEE_REGISTRATIONS = {
    1628569350: ("Nizami Qasımov", 10932455),
    RUFAT_CHAT_ID: ("Rüfət Həsənzadə", 15532668),
    7262243946: ("Soltan Abbasov", 15531960),
    7329891614: ("Hüseyn Səfərov", 15532668),
    7920785774: ("Rasim Əsgərov", 15532668),
    1289510272: ("Sərmayə Əhmədsoy", 15532668),
    6596538872: ("Asya Agayeva", 15532668),
    1142054888: ("Nuranə Şirinova", 15532668),
}

def load_users() -> dict:
    """Load registrations from durable storage, with a local cache fallback.

    The deployment filesystem is ephemeral, so local users.json alone cannot be
    the source of truth. Merge the durable copy with the local cache so a fresh
    deploy does not make registered employees appear unregistered.
    """
    local_users = {}
    if os.path.exists(_USER_DB_LOCAL):
        try:
            with open(_USER_DB_LOCAL, "r") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    local_users = loaded
        except Exception:
            pass

    durable_users = {}
    try:
        loaded = read_json("users.json") or {}
        if isinstance(loaded, dict):
            durable_users = loaded
    except Exception as exc:
        logger.warning(f"users.json durable load failed: {exc}")

    # Keep existing local values, while restoring any registrations that only
    # exist in the durable data branch after a redeploy.
    users = dict(durable_users)
    users.update(local_users)
    if users and users != local_users:
        try:
            with open(_USER_DB_LOCAL, "w") as f:
                json.dump(users, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return users

def ensure_known_employee_registrations() -> None:
    """Seed fixed employee accounts once, so /start is never needed per deploy."""
    users = load_users()
    changed = False
    for chat_id, (name, kommo_user_id) in _KNOWN_EMPLOYEE_REGISTRATIONS.items():
        key = str(chat_id)
        current = users.get(key)
        if not isinstance(current, dict):
            current = {}
        expected = {
            "role": "Admin" if chat_id == ADMIN_CHAT_ID else "Əməkdaş",
            "name": name,
            # Keep historical Kommo IDs for employee display and compatibility.
            # New task assignments are routed to Admin by the assignee maps.
            "kommo_user_id": kommo_user_id,
        }
        if any(current.get(field) != value for field, value in expected.items()):
            current.update(expected)
            users[key] = current
            changed = True
    if changed:
        save_users(users)
        logger.info("Known employee registrations restored for startup")


def save_users(data: dict):
    try:
        with open(_USER_DB_LOCAL, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    try:
        write_json("users.json", data)
    except Exception as e:
        logger.error(f"save_users gh error: {e}")

def get_chat_id_for_kommo_user(kommo_user_id: int) -> int | None:
    if int(kommo_user_id or 0) == ADMIN_KOMMO_USER_ID:
        return ADMIN_CHAT_ID
    users = load_users()
    for chat_id_str, info in users.items():
        if info.get("kommo_user_id") == kommo_user_id:
            return int(chat_id_str)
    return None

# Salary employees that historically shared the Sahə Meneceri Kommo license.
# Their Telegram identities remain separate; new work is routed to Admin.
_SALARY_CHAT_IDS = {RUFAT_CHAT_ID, 7262243946, 7329891614, 7920785774, 1289510272, 6596538872, 1142054888}

def get_kommo_user_id_for_chat(chat_id: int) -> int | None:
    users = load_users()
    info = users.get(str(chat_id))
    if info:
        uid = info.get("kommo_user_id")
        if uid:
            return uid
    # Fallback for known employees
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return None
    if cid == 1628569350:
        return 10932455  # Admin
    if cid in _SALARY_CHAT_IDS:
        return 15532668  # Legacy display identity; new assignments use Admin
    return None

def is_admin(chat_id: int) -> bool:
    """Return whether a Telegram chat belongs to the Admin account."""
    try:
        if int(chat_id) == ADMIN_CHAT_ID:
            return True
    except (TypeError, ValueError):
        pass
    # Do not infer admin access from a shared Kommo user ID. Several legacy
    # employee accounts used the same Sahə Meneceri license.
    return False


_PENDING_ACTIONS_FILE = "pending_actions.json"
_TASK_PRIORITIES_FILE = "task_priorities.json"
_TASK_CREATORS_FILE = "task_creators.json"
_VALID_TASK_PRIORITIES = {"urgent", "medium", "low"}


def task_created_by_rufat(task_id) -> bool:
    """Return whether the task creator is the second administrator, Rüfət."""
    try:
        creators = read_json(_TASK_CREATORS_FILE) or {}
        creator = str(creators.get(str(task_id), "")).strip().casefold()
        # Keep legacy Şamil records recognizable, while all new records use Rüfət.
        return creator in {"rüfət", "rüfət əliyev", "şamil", "şamil əliyev"}
    except Exception:
        return False
_PENDING_EXECUTOR_NAMES = {
    "Rüfət": "Rüfət Həsənzadə",
    "Soltan": "Soltan Abbasov",
    "Hüseyn": "Hüseyn Səfərov",
    "Rasim": "Rasim Əsgərov",
}


def get_pending_actions() -> list:
    """Return persisted pending actions, tolerating an empty legacy document."""
    actions = read_json(_PENDING_ACTIONS_FILE) or []
    if not isinstance(actions, list):
        logger.warning("pending_actions.json is not a list; ignoring invalid content")
        return []
    return actions


def save_pending_action(action_type: str, data: dict, options: list) -> dict:
    """Persist a new admin action and return its public representation."""
    actions = get_pending_actions()
    action = {
        "id": str(uuid.uuid4())[:8],
        "type": action_type,
        "created_at": datetime.now(tz=BAKU_TZ).isoformat(),
        "resolved": False,
        "data": data,
        "options": options,
    }
    actions.insert(0, action)
    if not write_json(_PENDING_ACTIONS_FILE, actions[:500]):
        logger.error("Failed to persist pending action %s", action["id"])
    return action


def delete_pending_action(action_id: str) -> bool:
    """Remove a persisted pending action entirely without resolving it."""
    actions = get_pending_actions()
    remaining = [action for action in actions if str(action.get("id")) != str(action_id)]
    if len(remaining) == len(actions):
        return False
    if not write_json(_PENDING_ACTIONS_FILE, remaining):
        logger.error("Failed to delete pending action %s", action_id)
        return False
    return True


def _normalize_task_priority(priority: str) -> str:
    normalized = str(priority or "").strip().lower()
    return normalized if normalized in _VALID_TASK_PRIORITIES else ""


# Təcili alarm: lead/task registry {task_id: {task info}} for 15-min repeated pushes
_tecili_tasks: dict = {}


def register_tecili_task(create_result: dict):
    """Track an urgent task so the 15-min alarm loop keeps notifying the assignee."""
    try:
        task = create_result.get("_embedded", {}).get("tasks", [{}])[0]
        task_id = task.get("id")
        if not task_id:
            return
        _tecili_tasks[int(task_id)] = {
            "task_id": int(task_id),
            "entity_id": task.get("entity_id"),
            "entity_type": task.get("entity_type", "leads"),
            "text": task.get("text", ""),
            "responsible_user_id": task.get("responsible_user_id"),
        }
        logger.info(f"Təcili task registered for alarm: {task_id}")
    except Exception as exc:
        logger.warning(f"register_tecili_task failed: {exc}")


def unregister_tecili_task(task_id):
    """Stop the 15-min alarm for a completed urgent task."""
    try:
        if _tecili_tasks.pop(int(task_id), None) is not None:
            logger.info(f"Təcili task {task_id} removed from alarm registry")
    except (TypeError, ValueError):
        pass


def save_task_priority(create_result: dict, priority: str) -> bool:
    """Persist priority for a task returned by Kommo's create-task endpoint."""
    normalized = _normalize_task_priority(priority)
    if not normalized:
        return True
    if normalized == "urgent":
        register_tecili_task(create_result)
    try:
        task_id = create_result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
    except (AttributeError, IndexError, TypeError):
        task_id = None
    if not task_id:
        logger.error("Cannot persist task priority: created task id is missing")
        return False
    priorities = read_json(_TASK_PRIORITIES_FILE) or {}
    if not isinstance(priorities, dict):
        logger.warning("task_priorities.json is not a dict; resetting invalid content")
        priorities = {}
    priorities[str(task_id)] = normalized
    if not write_json(_TASK_PRIORITIES_FILE, priorities):
        logger.error("Failed to persist priority for task %s", task_id)
        return False
    return True


def mark_pending_action_resolved(
    action_id: str = None,
    action_type: str = None,
    choice: str = None,
    **data_matches,
) -> bool:
    """Resolve the newest matching persisted action without running it again."""
    actions = get_pending_actions()
    for action in actions:
        if action.get("resolved"):
            continue
        if action_id and action.get("id") != action_id:
            continue
        if action_type and action.get("type") != action_type:
            continue
        action_data = action.get("data") or {}
        if any(
            expected is not None and str(action_data.get(key)) != str(expected)
            for key, expected in data_matches.items()
        ):
            continue
        import traceback as _tb
        caller = _tb.extract_stack()[-2]
        logger.info(f"MARK_RESOLVED: action_id={action.get('id')}, type={action.get('type')}, choice={choice}, caller={caller.filename}:{caller.lineno}:{caller.name}")
        action["resolved"] = True
        action["resolved_at"] = datetime.now(tz=BAKU_TZ).isoformat()
        action["resolved_by"] = f"{caller.filename.split('/')[-1]}:{caller.lineno}"
        if choice:
            action["resolved_choice"] = choice
        if not write_json(_PENDING_ACTIONS_FILE, actions):
            logger.error("Failed to mark pending action %s as resolved", action.get("id"))
            return False
        return True
    return False


def _stage_key_for_status(status_id: int) -> str | None:
    for stage_key, configured_status_id in STAGES.items():
        if configured_status_id == status_id:
            return stage_key
    return None


def _stage_key_for_name(stage_name: str) -> str | None:
    normalized_name = str(stage_name or "").strip().casefold()
    if normalized_name in STAGES:
        return normalized_name
    for status_id, display_name in STAGE_NAMES.items():
        if display_name.casefold() == normalized_name:
            return _stage_key_for_status(status_id)
    return None


def _send_telegram_text(chat_id, text: str):
    """Best-effort Telegram notification usable from synchronous API handlers."""
    if not chat_id:
        return
    try:
        _http.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": int(chat_id), "text": text, "disable_web_page_preview": True},
            timeout=8,
        )
    except Exception as exc:
        logger.warning("Pending action Telegram notification failed: %s", exc)


def _close_pending_telegram_message(action: dict, result_text: str):
    """Remove stale inline buttons when an action is resolved in the PWA."""
    action_data = action.get("data") or {}
    chat_id = action_data.get("telegram_chat_id")
    message_id = action_data.get("telegram_message_id")
    if not chat_id or not message_id:
        return
    try:
        _http.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
            json={
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "text": result_text,
                "disable_web_page_preview": True,
            },
            timeout=8,
        )
    except Exception as exc:
        logger.warning("Pending action Telegram cleanup failed: %s", exc)


def _clear_runtime_pending_action(action: dict):
    """Remove volatile Telegram callback state after a PWA resolution."""
    if not _bot_app:
        return
    action_data = action.get("data") or {}
    conf_key = action_data.get("conf_key")
    callback_key = action_data.get("callback_key")
    if conf_key:
        _bot_app.bot_data.pop(f"confirm_{conf_key}", None)
    if callback_key:
        _bot_app.bot_data.get("pending_stage_change", {}).pop(callback_key, None)
        _bot_app.bot_data.get("pending_next_stages", {}).pop(callback_key, None)


def _create_stage_task(lead_id: int, stage_key: str, sender_name: str = "") -> bool:
    """Create the standard two-hour task associated with a pipeline stage."""
    task_text = _STAGE_TASK_TEXTS.get(stage_key)
    if not task_text:
        return True
    deadline_ts = int((datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
    if stage_key == "qiymet_teklifi" or sender_name in ("", "Webhook", "Nizami Qasımov", "Admin"):
        responsible_user_id = 10932455
    else:
        task_text = f"[{sender_name}] {task_text}"
        responsible_user_id = 10932455
    return bool(create_task(
        int(lead_id),
        task_text,
        deadline_ts,
        responsible_user_id=responsible_user_id,
        entity_type="leads",
    ))


def _apply_pending_kpi_stars(action_data: dict, stars: int) -> bool:
    """Attribute optional KPI stars (1-5 → 20-100) to the executor behind a pending action."""
    try:
        stars = int(stars or 0)
    except (TypeError, ValueError):
        return False
    if stars <= 0:
        return False
    stars = min(stars, 5)
    score = stars * 20
    # Determine executor telegram id: creator_chat_id > sender_chat_id > sender_name mapping
    employee_tg_id = None
    for key in ("creator_chat_id", "sender_chat_id"):
        raw = action_data.get(key)
        try:
            candidate = int(raw)
        except (TypeError, ValueError):
            continue
        if candidate and candidate != ADMIN_CHAT_ID:
            employee_tg_id = candidate
            break
    if not employee_tg_id:
        sender_name = action_data.get("sender_name") or ""
        mapped = NAME_TO_CHAT.get(sender_name)
        if mapped and int(mapped) != ADMIN_CHAT_ID:
            employee_tg_id = int(mapped)
    if not employee_tg_id:
        logger.info("KPI stars skipped: no executor found in action data")
        return False
    task_id = action_data.get("task_id") or 0
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        task_id = 0
    saved = set_kpi_score(employee_tg_id, task_id, score, corrected_by=ADMIN_CHAT_ID)
    if not saved:
        # No existing kpi.json entry for this task — create one directly
        try:
            kpi_data = read_json("kpi.json") or {}
            key = f"{employee_tg_id}_{task_id}"
            kpi_data[key] = {
                "sessions": [],
                "kpi_score": score,
                "manual_correction": True,
                "corrected_by": ADMIN_CHAT_ID,
                "corrected_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "completed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "pending_confirm_stars",
            }
            saved = write_json("kpi.json", kpi_data)
        except Exception as kpi_err:
            logger.error(f"KPI stars write error: {kpi_err}")
            return False
    if saved:
        logger.info(f"KPI stars applied: employee={employee_tg_id}, task={task_id}, score={score}")
    return bool(saved)


def resolve_pending_action(action_id: str, choice: str, kpi_score: int = 0, stars: int = 0, amount: float = 0) -> tuple[bool, str]:
    """Execute one persisted admin action and resolve it only after success."""
    actions = get_pending_actions()
    action = next((item for item in actions if item.get("id") == action_id), None)
    if not action:
        return False, "Sorğu tapılmadı."
    if action.get("resolved"):
        return False, "Sorğu artıq həll edilib."
    action_options = action.get("options") or []
    _executor_names_set = {"\u015eamil", "Soltan", "H\u00fcseyn", "Rasim", "\u00d6z\u00fcm"}
    if choice not in action_options:
        if choice != "T\u0259sdiq et" and not choice.startswith("stage_change:") and choice not in _executor_names_set:
            return False, "Yanl\u0131\u015f se\u00e7im."

    action_type = action.get("type")
    action_data = action.get("data") or {}
    lead_id = action_data.get("lead_id")
    contact_name = action_data.get("contact_name") or "—"
    phone = action_data.get("phone") or "—"
    stage_name = action_data.get("stage_name") or "—"
    link = action_data.get("link") or (f"{KOMMO_BASE_URL}/leads/detail/{lead_id}" if lead_id else "")

    # ── Universal stage change: choice="stage_change:{stage_key_or_name}" works for ANY action type ──
    if choice.startswith("stage_change:"):
        requested_stage = choice.split(":", 1)[1].strip()
        # Short display names from PWA dropdown -> STAGES keys
        _stage_alias = {"uğurlu": "ugurlu", "i\u0307mtina": "imtina", "imtina": "imtina"}
        alias_key = _stage_alias.get(requested_stage.casefold())
        status_id = STAGES.get(alias_key) if alias_key else STAGES.get(requested_stage.casefold())
        if not status_id:
            status_id = next(
                (sid for sid, dn in STAGE_NAMES.items() if dn.casefold() == requested_stage.casefold()),
                None,
            )
        if not status_id:
            # Try partial match
            status_id = next(
                (sid for sid, dn in STAGE_NAMES.items() if requested_stage.casefold() in dn.casefold()),
                None,
            )
        if not lead_id:
            return False, "Lead ID tapılmadı. Köhnə sorğu ola bilər."
        if not status_id:
            return False, f"Mərhələ tapılmadı: {requested_stage}"
        if not update_lead_kommo(
            int(lead_id),
            {"status_id": int(status_id), "pipeline_id": PIPELINE_ID},
        ):
            return False, "Kommo mərhələsi dəyişdirilmədi."
        new_stage_display = STAGE_NAMES.get(int(status_id), requested_stage)
        # Do NOT resolve the action — only change stage. Card stays until swipe-right Təsdiq et.
        # Update stage_name in action data for display
        actions = get_pending_actions()
        for a in actions:
            if a.get("id") == action_id:
                a.setdefault("data", {})["stage_name"] = new_stage_display
                break
        write_json(_PENDING_ACTIONS_FILE, actions)
        return True, f"Mərhələ dəyişdirildi: {new_stage_display}. Sorğu hələ açıqdır."

    # ── Universal executor change: choice is an executor short name, card does NOT close ──
    _executor_short_names = {"\u015eamil", "Soltan", "H\u00fcseyn", "Rasim", "\u00d6z\u00fcm"}
    if choice in _executor_short_names and action_type != "assign_executor":
        # Update existing task's responsible user in Kommo
        task_id = action_data.get("task_id")
        if choice == "\u00d6z\u00fcm":
            new_responsible = 10932455
            new_name = "Nizami Qas\u0131mov"
        else:
            full_name = _PENDING_EXECUTOR_NAMES.get(choice)
            if not full_name:
                return False, "\u0130cra\u00e7\u0131 tan\u0131nmad\u0131."
            new_responsible = 10932455
            new_name = full_name
        if task_id:
            update_data = {"responsible_user_id": new_responsible}
            update_task_kommo(task_id, update_data)
            # Notify new assignee about the transfer
            _new_chat = NAME_TO_CHAT.get(new_name)
            if _new_chat and _new_chat != ADMIN_CHAT_ID:
                _client = action_data.get("contact_name", "")
                _task_desc = action_data.get("task_text", "")
                _link = action_data.get("link", "")
                try:
                    import asyncio
                    asyncio.ensure_future(_bot_app.bot.send_message(
                        _new_chat,
                        f"\ud83d\udce8 *Siz\u0259 yeni tap\u015f\u0131r\u0131q t\u0259yin edildi!*\n\n\ud83d\udcdd {_task_desc}\n\ud83d\udc64 {_client}\n\ud83d\udd17 {_link}",
                        parse_mode="Markdown", disable_web_page_preview=True))
                    send_push_notification(str(_new_chat), '\ud83d\udce8 Yeni tap\u015f\u0131r\u0131q!', f'{_client} - {_task_desc}')
                except: pass
        # Route Rüfət to his personal pipeline/sorğular; keep existing routing for others.
        _lead_id_exec = action_data.get("lead_id")
        if _lead_id_exec:
            try:
                if not move_lead_to_icraci(_lead_id_exec, new_name):
                    logger.warning("Failed to route assigned deal %s to %s", _lead_id_exec, new_name)
            except Exception as exc:
                logger.warning("Failed to route assigned deal: %s", exc)
        # Notify cavabdeh (creator) about executor assignment
        _sender_name_uc = action_data.get("sender_name", "")
        _sender_chat_uc = action_data.get("sender_chat_id") or NAME_TO_CHAT.get(_sender_name_uc)
        if _sender_chat_uc and int(_sender_chat_uc) != ADMIN_CHAT_ID:
            _client_uc = action_data.get("contact_name", "")
            _task_uc = action_data.get("task_text", "")
            try:
                import asyncio
                asyncio.ensure_future(_bot_app.bot.send_message(
                    int(_sender_chat_uc),
                    f"\u2705 Sizin tap\u015f\u0131r\u0131\u011f\u0131n\u0131z t\u0259yin edildi:\n\n\ud83d\udcdd {_task_uc}\n\ud83d\udc64 {_client_uc}\n\ud83d\udc77 \u0130cra\u00e7\u0131: {new_name}",
                    parse_mode="Markdown", disable_web_page_preview=True))
                send_push_notification(str(_sender_chat_uc), '\u2705 Tap\u015f\u0131r\u0131q t\u0259yin edildi', f'{_client_uc} - {new_name}')
            except Exception:
                pass
        # Resolve card (remove from pending)
        mark_pending_action_resolved(action_id, f"\u0130cra\u00e7\u0131: {new_name}")
        return True, f"\u0130cra\u00e7\u0131 t\u0259yin edildi: {new_name}"

    if action_type == "assign_executor":
        if choice == "Təsdiq et":
            # Təsdiq et = assign task to admin (Özüm)
            task_text_te = action_data.get("task_text") or ""
            stage_key_te = action_data.get("stage_key") or _stage_key_for_name(stage_name)
            if not task_text_te and stage_key_te:
                task_text_te = _STAGE_TASK_TEXTS.get(stage_key_te, "")
            if lead_id and task_text_te:
                if action_data.get("deadline"):
                    try:
                        dl_ts = int(datetime.strptime(action_data["deadline"], "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ).timestamp())
                    except:
                        dl_ts = int((datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
                else:
                    dl_ts = int((datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
                task_type_id_te = action_data.get("task_type_id")
                # Keep the original creator on tasks assigned to Admin too.
                # This prevents Rüfət-created tasks from entering Nizami's
                # confirmation flow when another employee completes them.
                _creator_name_te = action_data.get("sender_name", "")
                create_task(
                    int(lead_id), task_text_te, dl_ts,
                    responsible_user_id=10932455,
                    entity_type="leads",
                    task_type_id=int(task_type_id_te) if task_type_id_te else None,
                    creator_name=_creator_name_te,
                )
            if stars:
                _apply_pending_kpi_stars(action_data, stars)
            if not mark_pending_action_resolved(action_id=action_id, choice=choice):
                return False, "Sorğu bağlanmadı."
            _clear_runtime_pending_action(action)
            return True, "Tapşırıq sizin adınıza yaradıldı."
        if choice not in ("Ləğv et", "Rədd et"):
            stage_key = action_data.get("stage_key") or _stage_key_for_name(stage_name)
            task_text = _STAGE_TASK_TEXTS.get(stage_key) if stage_key else None
            # For cnftask-style (task creation from employee): use stored task_text
            if not task_text and action_data.get("task_text"):
                task_text = action_data["task_text"]
            if not lead_id or not task_text:
                return False, "Mərhələ tapşırığı müəyyən edilmədi."
            if choice == "Özüm":
                responsible_user_id = 10932455
            else:
                full_name = _PENDING_EXECUTOR_NAMES.get(choice)
                if not full_name:
                    return False, "İcraçı tanınmadı."
                responsible_user_id = 10932455
            # Use stored deadline or default 2h
            if action_data.get("deadline"):
                try:
                    deadline_ts = int(datetime.strptime(action_data["deadline"], "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ).timestamp())
                except:
                    deadline_ts = int((datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
            else:
                deadline_ts = int((datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
            task_type_id = action_data.get("task_type_id")
            # Preserve the original creator on the exact task returned by Kommo.
            # This is essential for Rüfət-created tasks: their completion must be
            # reported to Rüfət, not sent to Nizami for confirmation.
            _sender_name_ae = action_data.get("sender_name", "")
            _created_task_ae = create_task(
                int(lead_id),
                task_text,
                deadline_ts,
                responsible_user_id=responsible_user_id,
                entity_type="leads",
                task_type_id=int(task_type_id) if task_type_id else None,
                creator_name=_sender_name_ae,
            )
            if not _created_task_ae:
                return False, "Kommo-da tapşırıq yaradılmadı."
            # Keep a compatibility fallback for legacy Kommo responses that do
            # not expose the created task ID in the response payload.
            if _sender_name_ae:
                try:
                    _created_task_id_ae = (_created_task_ae.get("_embedded", {})
                                          .get("tasks", [{}])[0].get("id"))
                    if _created_task_id_ae:
                        _cr = read_json(_TASK_CREATORS_FILE) or {}
                        _cr[str(_created_task_id_ae)] = _sender_name_ae
                        write_json(_TASK_CREATORS_FILE, _cr)
                except Exception:
                    pass
            # Notify creator (cavabdeh) that task was assigned
            _sender_chat_ae = action_data.get("sender_chat_id") or NAME_TO_CHAT.get(_sender_name_ae)
            if _sender_chat_ae and int(_sender_chat_ae) != ADMIN_CHAT_ID:
                _client_ae2 = action_data.get("contact_name", "")
                _assigned_to = _PENDING_EXECUTOR_NAMES.get(choice) or choice
                try:
                    import asyncio
                    asyncio.ensure_future(_bot_app.bot.send_message(
                        int(_sender_chat_ae),
                        f"\u2705 Sizin tapşırığınız təyin edildi:\n\n\ud83d\udcdd {task_text}\n\ud83d\udc64 {_client_ae2}\n\ud83d\udc77 İcraçı: {_assigned_to}",
                        parse_mode="Markdown", disable_web_page_preview=True))
                    send_push_notification(str(_sender_chat_ae), '\u2705 Tapşırıq təyin edildi', f'{_client_ae2} - {_assigned_to}')
                except Exception:
                    pass
            # Notify new assignee
            _ae_name = "Nizami Qas\u0131mov" if choice == "\u00d6z\u00fcm" else (_PENDING_EXECUTOR_NAMES.get(choice) or "")
            _target_chat_ae = get_chat_id_by_name(_ae_name) if _ae_name else None
            if _target_chat_ae and int(_target_chat_ae) != ADMIN_CHAT_ID:
                _client_ae = action_data.get("contact_name", "")
                _link_ae = action_data.get("link", "")
                try:
                    import asyncio
                    asyncio.ensure_future(_bot_app.bot.send_message(
                        int(_target_chat_ae),
                        f"\ud83d\udce8 *Siz\u0259 yeni tap\u015f\u0131r\u0131q t\u0259yin edildi!*\n\n\ud83d\udcdd {task_text}\n\ud83d\udc64 {_client_ae}\n\ud83d\udd17 {_link_ae}",
                        parse_mode="Markdown", disable_web_page_preview=True))
                    send_push_notification(str(_target_chat_ae), '\ud83d\udce8 Yeni tap\u015f\u0131r\u0131q!', f'{_client_ae} - {task_text}')
                except: pass
            # Route the deal after assignment. Rüfət must always receive it in
            # his own pipeline at the exact `sorgular` stage.
            if _target_chat_ae:
                if not move_lead_to_icraci(int(lead_id), _ae_name):
                    logger.error(
                        "Failed to route assigned deal: lead=%s assignee=%s",
                        lead_id, _ae_name,
                    )
                    return False, "İcraçı təyin edildi, lakin sövdələşmə köçürülmədi."
                if _ae_name == "Rüfət Həsənzadə":
                    # Explicit notification is intentional: the stage-change
                    # webhook may be delayed or suppressed as bot-initiated.
                    _rufat_msg = (
                        "📥 Rüfət Həsənzadə bölməsinə yeni sövdələşmə daxil oldu!\n\n"
                        f"👤 {_client_ae or 'Adsız'}\n"
                        f"📝 {task_text}\n"
                        f"📞 {action_data.get('phone', '')}\n"
                        f"📌 Mərhələ: sorğular\n"
                        f"🔗 {_link_ae}"
                    )
                    try:
                        asyncio.ensure_future(_bot_app.bot.send_message(
                            int(_target_chat_ae), _rufat_msg,
                            disable_web_page_preview=True,
                        ))
                        send_push_notification(
                            str(_target_chat_ae),
                            "📥 Yeni sövdələşmə: sorğular",
                            f"{_client_ae or 'Adsız'} — {task_text}",
                        )
                    except Exception as exc:
                        logger.warning("Rüfət notification failed: %s", exc)
        result_message = "Sorğu ləğv edildi." if choice in ("Ləğv et", "Rədd et") else f"Tapşırıq {choice} üçün yaradıldı."

    elif action_type == "confirm_stage":
        if choice == "Təsdiq et":
            status_id = action_data.get("status_id")
            if not lead_id or not status_id:
                return False, "Mərhələ məlumatı natamamdır."
            target_pipeline_id = int(action_data.get("pipeline_id") or PIPELINE_ID)
            if not update_lead_kommo(
                int(lead_id),
                {"status_id": int(status_id), "pipeline_id": target_pipeline_id},
            ):
                return False, "Kommo mərhələsi dəyişdirilmədi."
            stage_key = action_data.get("stage_key") or _stage_key_for_status(int(status_id))
            sender_name = action_data.get("sender_name", "")
            if sender_name not in NAME_TO_CHAT:
                sender_name = get_employee_name_by_chat_id(action_data.get("sender_chat_id"), "")
            if stage_key in _STAGE_TASK_TEXTS and not _create_stage_task(
                int(lead_id), stage_key, sender_name
            ):
                return False, "Mərhələ təsdiqləndi, lakin avtomatik tapşırıq yaradılmadı."
            result_message = f"Mərhələ təsdiqləndi: {stage_name}."
            sender_text = "✅ Admin sorğunuzu təsdiqlədi."
        elif choice == "Rədd et":
            result_message = "Mərhələ dəyişikliyi rədd edildi."
            sender_text = "❌ Admin sorğunuzu rədd etdi."
        else:
            return False, "Yanlış seçim."
        _send_telegram_text(
            action_data.get("sender_chat_id"),
            f"{sender_text}\n👤 {contact_name}\n📝 Mərhələ: {stage_name}\n"
            f"📞 {phone}\n⏰ {datetime.now(tz=BAKU_TZ).strftime('%d.%m.%Y %H:%M')}\n🔗 {link}",
        )

    elif action_type == "reassign_task":
        task_id = action_data.get("task_id")
        update_data = action_data.get("update_data") or {}
        creator_chat_id = action_data.get("creator_chat_id")
        if choice == "Rədd et":
            result_message = "Dəyişiklik rədd edildi."
            _send_telegram_text(creator_chat_id, "❌ Dəyişiklik rədd edildi.")
        else:
            _UPD_MARKER = {"Təsdiq et": None, "Rüfət": ("Rüfət Həsənzadə", 15532668), "Soltan": ("Soltan Abbasov", 15531960), "Hüseyn": ("Hüseyn Səfərov", 15532668), "Rasim": ("Rasim Əsgərov", 15532668), "Texniki": (TECHNICAL_SUPPORT_NAME, 15532668), "Özüm": ("Nizami Qasımov", 10932455)}
            if choice != "Təsdiq et":
                marker_info = _UPD_MARKER.get(choice)
                if marker_info:
                    new_name, new_id = marker_info
                    update_data["responsible_user_id"] = new_id
                    import re as _re3
                    current_text = update_data.get("text", "")
                    if not current_text and task_id:
                        try:
                            t_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=HEADERS, timeout=8)
                            if t_resp.status_code == 200:
                                current_text = t_resp.json().get("text", "")
                        except: pass
                    old_text = _re3.sub(r"^\[.*?\]\s*", "", current_text)
                    if new_name and choice != "Özüm":
                        update_data["text"] = f"[{new_name}] {old_text}"
                    else:
                        update_data["text"] = old_text
            if task_id and update_data:
                if not update_task_kommo(task_id, update_data):
                    return False, "Yeniləmə uğursuz oldu."
            chosen_name = choice if choice == "Təsdiq et" else ((_UPD_MARKER.get(choice) or ("",))[0] or choice)
            result_message = f"Təsdiq ləndi! İcraçı: {chosen_name}"
            _send_telegram_text(creator_chat_id, "✅ Dəyişiklik təsdiq edildi!")

    elif action_type == "change_stage":
        # Təsdiq et = only confirm KPI, do NOT change stage (stage is changed via stage_change: or Mərhələ dəyiş button)
        if choice == "Təsdiq et":
            # Only apply KPI score, no stage change
            if kpi_score and action_data.get("task_id") and action_data.get("sender_name"):
                employee_tg_id = NAME_TO_CHAT.get(action_data["sender_name"])
                if employee_tg_id:
                    set_kpi_score(int(employee_tg_id), int(action_data["task_id"]), kpi_score, corrected_by=ADMIN_CHAT_ID)
            result_message = "Təsdiq edildi."
        else:
            # Explicit stage selection from options list
            status_id = next(
                (sid for sid, display_name in STAGE_NAMES.items() if display_name.casefold() == choice.casefold()),
                None,
            )
            if not lead_id or not status_id:
                return False, "Seçilmiş mərhələ tapılmadı."
            if not update_lead_kommo(
                int(lead_id),
                {"status_id": int(status_id), "pipeline_id": PIPELINE_ID},
            ):
                return False, "Kommo mərhələsi dəyişdirilmədi."
            stage_name = STAGE_NAMES.get(int(status_id), choice)
            # Apply KPI score if provided
            if kpi_score and action_data.get("task_id") and action_data.get("sender_name"):
                employee_tg_id = NAME_TO_CHAT.get(action_data["sender_name"])
                if employee_tg_id:
                    set_kpi_score(int(employee_tg_id), int(action_data["task_id"]), kpi_score, corrected_by=ADMIN_CHAT_ID)
            result_message = f"Mərhələ dəyişdirildi: {stage_name}."

    else:
        return False, "Naməlum sorğu növü."

    # Admin-entered amount first sits in Gözləmədə; Maliyyə confirm adds it to balance.
    if choice == "Təsdiq et":
        try:
            payout_amount = float(amount or 0)
        except (TypeError, ValueError):
            payout_amount = 0.0
        if math.isfinite(payout_amount) and payout_amount > 0:
            _sal_employee_tg = None
            for _k in ("sender_chat_id", "employee_chat_id"):
                _v = action_data.get(_k)
                if _v and int(_v) != ADMIN_CHAT_ID:
                    _sal_employee_tg = int(_v)
                    break
            if not _sal_employee_tg:
                _sn = action_data.get("sender_name", "")
                _sal_employee_tg = NAME_TO_CHAT.get(_sn)
                if _sal_employee_tg:
                    _sal_employee_tg = int(_sal_employee_tg)
            if _sal_employee_tg:
                _sal_name = _EMPLOYEE_NAMES_BY_TG.get(_sal_employee_tg, "")
                _sal_task_id = action_data.get("task_id", 0)
                try:
                    _sal_task_id = int(_sal_task_id)
                except (TypeError, ValueError):
                    _sal_task_id = 0
                add_balance_transaction(
                    telegram_id=_sal_employee_tg,
                    task_id=_sal_task_id,
                    amount=payout_amount,
                    task_text=f"Tapşırıq təsdiqləndi ({payout_amount:.2f} AZN)",
                    executor_name=_sal_name,
                    client=action_data.get("contact_name", ""),
                    phone=action_data.get("phone", ""),
                    task_type=action_data.get("task_type_name", ""),
                    result_text=action_data.get("note") or action_data.get("task_text") or "",
                    status="pending",
                )
    if not mark_pending_action_resolved(action_id=action_id, choice=choice):
        return False, "Əməliyyat icra olundu, lakin sorğu bağlanmadı."
    _clear_runtime_pending_action(action)
    _close_pending_telegram_message(
        action,
        f"✅ PWA-dan həll edildi: {choice}\n👤 {contact_name}\n📝 {result_message}\n"
        f"📞 {phone}\n⏰ {datetime.now(tz=BAKU_TZ).strftime('%d.%m.%Y %H:%M')}\n🔗 {link}",
    )
    return True, result_message


# Telegram identities remain the source of truth for employee names. The
# retired Sahə Meneceri Kommo license is no longer used for authorization.
TG_CHAT_TO_EMPLOYEE = {
    1628569350: "Nizami Qasımov",
    RUFAT_CHAT_ID: "Rüfət Həsənzadə",
    7262243946: "Soltan Abbasov",
    7329891614: "Hüseyn Səfərov",
    7920785774: "Rasim Əsgərov",
    1289510272: "Sərmayə Əhmədsoy",
    6596538872: "Asya Agayeva",
    1142054888: "Nuranə Şirinova",
}

def get_employee_name_by_chat_id(chat_id: int, default: str = "Əməkdaş") -> str:
    """Return the real employee name for a Telegram private-chat ID."""
    try:
        normalized_chat_id = int(chat_id)
    except (TypeError, ValueError):
        return default
    return TG_CHAT_TO_EMPLOYEE.get(normalized_chat_id, default)

# Name-to-chat mapping for marker-based notifications
NAME_TO_CHAT = {
    "Rüfət Həsənzadə": RUFAT_CHAT_ID,
    "Soltan Abbasov": 7262243946,
    "Hüseyn Səfərov": 7329891614,
    "Nizami Qasımov": 1628569350,
    "Rasim Əsgərov": 7920785774,
    "Sərmayə Əhmədsoy": 1289510272,
    "Asya Agayeva": 6596538872,
    "Nuranə Şirinova": 1142054888,
    # Keep historical task markers routable while writing the employee's real name.
    "Texniki tapşırıq": 8835096199,
    "Texniki": 8835096199,
    "Rüfət": RUFAT_CHAT_ID,
    "Soltan": 7262243946,
    "Hüseyn": 7329891614,
    "Nizami": 1628569350,
    "Rasim": 7920785774,
    "Sərmayə": 1289510272,
    "Asya": 6596538872,
    "Nuranə": 1142054888,
}

def get_chat_id_by_name(name: str) -> int | None:
    return NAME_TO_CHAT.get(name)


def normalize_assignee_name(name: str) -> str:
    """Normalize employee labels without conflating task types with employee names."""
    cleaned = str(name or "").strip()
    if cleaned.casefold() in {"texniki", "texniki dəstək", "texniki destek", "texniki tapşırıq"}:
        return TECHNICAL_SUPPORT_NAME
    return cleaned

# ─── Message Maps (reply context) ───────────────────────────────────────────
MESSAGE_MAPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "message_maps.json")
_message_task_map: dict = {}
_message_lead_map: dict = {}

def load_message_maps():
    global _message_task_map, _message_lead_map
    try:
        if os.path.exists(MESSAGE_MAPS_FILE):
            with open(MESSAGE_MAPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _message_task_map = data.get("tasks", {})
                _message_lead_map = data.get("leads", {})
    except Exception as e:
        logger.error(f"Error loading message maps: {e}")

def save_message_maps():
    try:
        if len(_message_task_map) > 500:
            keys = sorted(_message_task_map.keys())[:-500]
            for k in keys: del _message_task_map[k]
        if len(_message_lead_map) > 500:
            keys = sorted(_message_lead_map.keys())[:-500]
            for k in keys: del _message_lead_map[k]
        with open(MESSAGE_MAPS_FILE, "w", encoding="utf-8") as f:
            json.dump({"tasks": _message_task_map, "leads": _message_lead_map}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving message maps: {e}")

load_message_maps()

def store_message_task(chat_id: int, message_id: int, task_id: int, task_text: str = "",
                       entity_id: int = None, entity_type: str = None, phone: str = None):
    key = f"{chat_id}:{message_id}"
    _message_task_map[key] = {
        "task_id": task_id, "task_text": task_text,
        "entity_id": entity_id, "entity_type": entity_type or "leads",
        "phone": phone or "", "ts": int(_time_module.time())
    }
    save_message_maps()

def get_task_from_reply(chat_id: int, message_id: int) -> dict | None:
    key = f"{chat_id}:{message_id}"
    return _message_task_map.get(key)

def store_message_lead(chat_id: int, message_id: int, lead_id: int, lead_name: str = "", phone: str = ""):
    key = f"{chat_id}:{message_id}"
    _message_lead_map[key] = {"lead_id": lead_id, "lead_name": lead_name, "phone": phone, "ts": int(_time_module.time())}
    save_message_maps()

def get_lead_from_reply(chat_id: int, message_id: int) -> dict | None:
    key = f"{chat_id}:{message_id}"
    return _message_lead_map.get(key)

# ─── Bot-created tasks (suppress webhook echo) ──────────────────────────────
_bot_created_tasks: set = set()
_bot_created_tasks_ts: dict = {}  # {task_id: timestamp} for time-based expiry
_pending_bot_task_leads: dict = {}  # {lead_id: timestamp} - suppress webhook before created id is known
_notified_task_webhooks: dict = {}  # {task_id: timestamp} - prevent duplicate webhook notifications
# Completion notification override for the synchronous AI completion flow.
_last_completed_task_creator_chat_id: int | None = None
# Bot-initiated lead stage changes (suppress webhook echo)
_bot_updated_tasks: dict = {}  # {task_id: timestamp} - suppress update webhook echo
_bot_changed_leads: dict = {}  # {lead_id: timestamp}
_webhook_stage_dedup: dict = {}  # {(lead_id, status_id): timestamp}

# ─── Pending registrations ───────────────────────────────────────────────────
_pending_partner_registration: dict = {}
_pending_employee_registration: dict = {}
_button_flow: dict = {}  # chat_id -> {"action": "task"/"stage"/"note", "step": "phone"/"text"/...}


# ─── Conversation History (in-memory) ───────────────────────────────────────
_conversation_history: dict = {}  # chat_id -> list of messages

def get_history(chat_id: int) -> list:
    return _conversation_history.get(chat_id, [])

def add_to_history(chat_id: int, role: str, content: str):
    if chat_id not in _conversation_history:
        _conversation_history[chat_id] = []
    _conversation_history[chat_id].append({"role": role, "content": content})
    # Keep last 10
    if len(_conversation_history[chat_id]) > 10:
        _conversation_history[chat_id] = _conversation_history[chat_id][-10:]

# ─── Helper functions ────────────────────────────────────────────────────────
def clean_transcription(text: str) -> str:
    cleaned = re.sub(r"\[\d{2}:\d{2}\.\d+\s*-\s*\d{2}:\d{2}\.\d+\]", "", text)
    cleaned = re.sub(r"\[\d{2}:\d{2}\.\d+\]", "", cleaned)
    return cleaned.strip()

def resolve_date_from_text(text: str) -> str | None:
    text_lower = text.lower().strip()
    now = datetime.now(tz=BAKU_TZ)
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if m:
        return m.group(0)
    if any(w in text_lower for w in ["bugün", "bu gün", "today"]):
        return now.strftime("%d.%m.%Y")
    if any(w in text_lower for w in ["sabah", "tomorrow"]):
        return (now + timedelta(days=1)).strftime("%d.%m.%Y")
    return None

def resolve_time_from_text(text: str) -> str | None:
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        return m.group(0)
    return None

# ─── Kommo API Helpers ───────────────────────────────────────────────────────
# Kommo strictly blocks the account above 7 requests per second.
# We enforce a hard limit of 6 RPS globally across all threads, sessions,
# webhooks, and background workers using:
# 1) A sliding 1.05s window to ensure at most 6 requests within ANY rolling second.
# 2) A minimum interval of 0.170s between consecutive requests.
# 3) A global cooldown coordinator upon encountering HTTP 429 to pause all threads.
KOMMO_MAX_RPS = 6
KOMMO_WINDOW_SEC = 1.05
KOMMO_MIN_GAP = max(0.170, 1.0 / KOMMO_MAX_RPS)

_kommo_pace_lock = threading.Lock()
_kommo_history = collections.deque()
_kommo_next_at = 0.0
_kommo_global_cooldown_until = 0.0

def _is_kommo_url(url) -> bool:
    try:
        raw = str(url or "").strip().lower()
        if "kommo.com" in raw or "amocrm.ru" in raw or "amocrm.com" in raw:
            return True
        host = (urlparse(raw).hostname or "").lower()
        return (
            host == "kommo.com" or host.endswith(".kommo.com")
            or host == "amocrm.ru" or host.endswith(".amocrm.ru")
            or host == "amocrm.com" or host.endswith(".amocrm.com")
        )
    except Exception:
        return False

def _kommo_engage_cooldown(seconds: float = 3.0) -> None:
    """Pause all Kommo requests across all threads for a cooldown period."""
    global _kommo_global_cooldown_until, _kommo_next_at
    with _kommo_pace_lock:
        now = _time_module.monotonic()
        _kommo_global_cooldown_until = max(_kommo_global_cooldown_until, now + seconds)
        _kommo_next_at = max(_kommo_next_at, _kommo_global_cooldown_until)
        _kommo_history.clear()

def _kommo_wait() -> None:
    """Enforce strict global rate limiting (<= 6 RPS) across all threads."""
    global _kommo_next_at, _kommo_global_cooldown_until
    while True:
        cooldown_delay = 0.0
        with _kommo_pace_lock:
            now = _time_module.monotonic()
            if now < _kommo_global_cooldown_until:
                cooldown_delay = _kommo_global_cooldown_until - now
        if cooldown_delay > 0:
            _time_module.sleep(cooldown_delay)
            continue

        with _kommo_pace_lock:
            now = _time_module.monotonic()
            if now < _kommo_global_cooldown_until:
                continue
            base = max(now, _kommo_next_at)
            while _kommo_history and _kommo_history[0] <= base - KOMMO_WINDOW_SEC:
                _kommo_history.popleft()
            if len(_kommo_history) >= KOMMO_MAX_RPS:
                slot = max(base, _kommo_history[0] + KOMMO_WINDOW_SEC)
            else:
                slot = base
            _kommo_next_at = slot + KOMMO_MIN_GAP
            _kommo_history.append(slot)

        delay = slot - _time_module.monotonic()
        interrupted = False
        while delay > 0:
            _time_module.sleep(min(delay, 0.05))
            if _time_module.monotonic() < _kommo_global_cooldown_until:
                interrupted = True
                break
            delay = slot - _time_module.monotonic()

        if interrupted:
            continue
        return

_orig_session_request = requests.Session.request

def _paced_session_request(self, method, url, *args, **kwargs):
    kommo = _is_kommo_url(url)
    response = None
    for attempt in range(3):
        if kommo:
            _kommo_wait()
        response = _orig_session_request(self, method, url, *args, **kwargs)
        if not kommo or getattr(response, "status_code", 0) != 429 or attempt >= 2:
            return response
        logger.warning("Kommo returned 429 on attempt %s; initiating global cooldown", attempt + 1)
        _kommo_engage_cooldown(2.5 * (attempt + 1))
    return response

requests.Session.request = _paced_session_request

def _token_has_scope(tok: str, scope: str) -> bool:
    try:
        parts = str(tok or "").split(".")
        if len(parts) < 2:
            return False
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return scope in (data.get("scopes") or [])
    except Exception:
        return False

# An empty env var is not the only failure mode: a stale or truncated
# KOMMO_TOKEN on Railway returns 401 and the whole CRM (deals, tasks, chats)
# looks empty even though the built-in token still works.
try:
    _kommo_probe = requests.get(
        f"{KOMMO_BASE_URL}/api/v4/account",
        headers={"Authorization": f"Bearer {KOMMO_TOKEN}"},
        timeout=10,
    )
    _kommo_probe_status = _kommo_probe.status_code
except Exception as _kommo_probe_exc:
    logger.warning("Kommo token probe failed: %s", _kommo_probe_exc)
    _kommo_probe_status = 0
if _kommo_probe_status != 200 and KOMMO_TOKEN != _KOMMO_TOKEN_FALLBACK:
    logger.error("Env KOMMO_TOKEN rejected with HTTP %s; using built-in token", _kommo_probe_status)
    KOMMO_TOKEN = _KOMMO_TOKEN_FALLBACK
elif (
    KOMMO_TOKEN != _KOMMO_TOKEN_FALLBACK
    and not _token_has_scope(KOMMO_TOKEN, "list_external_messages")
    and _token_has_scope(_KOMMO_TOKEN_FALLBACK, "list_external_messages")
):
    logger.info("Env KOMMO_TOKEN lacks list_external_messages; using built-in fallback with full chat permissions")
    KOMMO_TOKEN = _KOMMO_TOKEN_FALLBACK

HEADERS = {
    "Authorization": f"Bearer {KOMMO_TOKEN}",
    "Content-Type": "application/json",
}
# Reusable session with connection pooling for faster API calls
_http = requests.Session()
_http.headers.update(HEADERS)
_http.mount('https://', requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10))

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
    all_contacts = []
    seen_ids = set()
    for variant in variants:
        url = f"{KOMMO_BASE_URL}/api/v4/contacts"
        params = {"query": variant, "limit": 5}
        try:
            resp = _http.get(url, headers=HEADERS, params=params, timeout=8)
            if resp.status_code == 200:
                contacts = resp.json().get("_embedded", {}).get("contacts", [])
                for c in contacts:
                    if c["id"] not in seen_ids:
                        # Verify phone actually matches
                        phone_match = False
                        for cf in c.get("custom_fields_values", []) or []:
                            if cf.get("field_code") == "PHONE":
                                for val in cf.get("values", []):
                                    contact_digits = re.sub(r"[^\d]", "", val.get("value", ""))
                                    if contact_digits[-9:] == target_suffix:
                                        phone_match = True
                                        break
                            if phone_match:
                                break
                        if phone_match:
                            seen_ids.add(c["id"])
                            all_contacts.append(c)
        except Exception as e:
            logger.error(f"Search contact error ({variant}): {e}")
    return all_contacts


def search_contacts_by_query(query: str) -> list:
    q = str(query or "").strip()
    digits = re.sub(r"[^\d]", "", q)
    if len(digits) >= 7:
        return search_contact_by_phone(q)
    if len(q) < 2:
        return []
    try:
        resp = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/contacts",
            headers=HEADERS,
            params={"query": q, "limit": 10, "with": "leads"},
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        return [item for item in (resp.json().get("_embedded") or {}).get("contacts") or [] if isinstance(item, dict)]
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


def _pick_search_lead(contact: dict, chat_id: int) -> dict:
    lead_ids = _lead_ids_from_contact(contact)
    if not lead_ids:
        full = get_contact_details(int(contact.get("id") or 0)) if str(contact.get("id") or "").isdigit() else {}
        lead_ids = _lead_ids_from_contact(full or {})
    owner = get_funnel_owner(chat_id) if chat_id else None
    try:
        wanted_pipe = int((owner or {}).get("pipeline_id") or 0)
    except (TypeError, ValueError):
        wanted_pipe = 0
    ranked: list[tuple] = []
    for lid in lead_ids[:8]:
        lead = get_lead_details(lid) or {}
        if not lead:
            continue
        pipe = _lead_pipeline_id(lead)
        try:
            status = int(lead.get("status_id") or 0)
        except (TypeError, ValueError):
            status = 0
        closed = status in {142, 143}
        same = bool(wanted_pipe and pipe == wanted_pipe)
        allowed = True
        if chat_id and not is_admin(chat_id):
            allowed = lead_allowed_for_chat(lid, chat_id) or same
        if not allowed:
            continue
        ranked.append((0 if same and not closed else 1 if same else 2, closed, lid, lead))
    if not ranked:
        return {}
    ranked.sort()
    lid, lead = ranked[0][2], ranked[0][3]
    return {
        "lead_id": int(lid),
        "lead_name": str(lead.get("name") or "").strip(),
        "pipeline_id": _lead_pipeline_id(lead),
        "status_id": lead.get("status_id") or 0,
    }


def get_contact_details(contact_id: int) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}"
    try:
        resp = _http.get(url, headers=HEADERS, params={"with": "leads"}, timeout=8)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Contact details error: {e}")
    return None

def is_rufat_chat(chat_id) -> bool:
    try:
        return int(chat_id) in RUFAT_COMPAT_CHAT_IDS
    except (TypeError, ValueError):
        return False


def _fold_stage_name(value: str) -> str:
    table = str.maketrans({
        "ə": "e", "ı": "i", "ö": "o", "ü": "u", "ğ": "g", "ş": "s", "ç": "c",
        "Ə": "e", "İ": "i", "Ö": "o", "Ü": "u", "Ğ": "g", "Ş": "s", "Ç": "c",
    })
    return str(value or "").translate(table).casefold().strip()


def _stage_key_from_kommo(name: str, status_id: int) -> str:
    try:
        sid = int(status_id)
    except (TypeError, ValueError):
        sid = 0
    if sid == 142:
        return "ugurlu"
    if sid == 143:
        return "imtina"
    n = _fold_stage_name(name)
    needles = (
        ("nerazobrann", "nerazobrannoye"),
        ("неразобран", "nerazobrannoye"),
        ("unparsed", "nerazobrannoye"),
        ("sorgular", "sorgular"),
        ("sorgu", "sorgular"),
        ("danisiq", "danisiqlar"),
        ("telimat", "telimat"),
        ("yeni sifaris", "yeni_sifaris"),
        ("geri don", "geri_donusler"),
        ("soyuq zeng", "soyuq_zeng"),
        ("cavab gozlenilir", "cavab_gozlenilir"),
        ("gorus", "gorusler"),
        ("qurasdirma", "qurashdirma"),
        ("qurashdirma", "qurashdirma"),
        ("muzakire", "muzakire"),
        ("icra olunur", "icra_olunur"),
        ("gozleme", "gozleme"),
        ("ugurlu", "ugurlu"),
        ("imtina", "imtina"),
        ("successfully", "ugurlu"),
        ("closed - won", "ugurlu"),
        ("closed - lost", "imtina"),
    )
    for needle, key in needles:
        if needle in n:
            return key
    slug = re.sub(r"[^a-z0-9]+", "_", n).strip("_") or f"stage_{sid}"
    return slug


_pipeline_stage_cache: dict[int, tuple[dict, dict, list]] = {}
_all_pipelines_cache: list[dict] | None = None
_all_pipelines_cache_at = 0.0


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
            sid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        label = str(row.get("name") or sid)
        key = _stage_key_from_kommo(label, sid)
        if key in used:
            key = f"{key}_{sid}"
        used.add(key)
        stages[key] = sid
        names[sid] = label
        ui.append((key, label))
    return stages, names, ui


def load_pipeline_stage_maps(pipeline_id: int, *, fallback: bool = True) -> tuple[dict, dict, list]:
    """Return stages, stage_names, and ordered UI pairs for a Kommo pipeline."""
    pid = int(pipeline_id)
    cached = _pipeline_stage_cache.get(pid)
    if cached:
        return cached
    pack = _personal_pipeline_packs().get(pid)
    if pack:
        stages_map, names_map = pack
        ui = [(key, names_map.get(sid, key)) for key, sid in stages_map.items()]
        _pipeline_stage_cache[pid] = (dict(stages_map), dict(names_map), ui)
        return _pipeline_stage_cache[pid]
    stages: dict[str, int] = {}
    names: dict[int, str] = {}
    ui: list[tuple[str, str]] = []
    try:
        resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads/pipelines/{pid}", headers=HEADERS, timeout=12)
        if resp.status_code == 200:
            statuses = (resp.json().get("_embedded") or {}).get("statuses") or []
            stages, names, ui = _stage_maps_from_statuses(statuses)
    except Exception as exc:
        logger.warning("Pipeline %s stage load failed: %s", pid, exc)
    if not stages and fallback:
        stages, names, ui = dict(RUFAT_STAGES), dict(RUFAT_STAGE_NAMES), [
            (key, RUFAT_STAGE_NAMES.get(sid, key)) for key, sid in RUFAT_STAGES.items()
        ]
    if stages:
        _pipeline_stage_cache[pid] = (stages, names, ui)
    return stages, names, ui


def _ordered_ui_for_pipeline(pipeline_id: int, statuses: list) -> list[dict]:
    stages_map, names, ui = load_pipeline_stage_maps(pipeline_id, fallback=False)
    key_by_sid = {int(sid): key for key, sid in stages_map.items()}
    ordered: list[dict] = []
    seen: set[str] = set()
    for row in sorted(statuses or [], key=lambda item: int((item or {}).get("sort") or 0)):
        if not isinstance(row, dict):
            continue
        try:
            sid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        key = key_by_sid.get(sid)
        if not key:
            continue
        ordered.append({"key": key, "name": names.get(sid) or str(row.get("name") or key)})
        seen.add(key)
    for key, label in ui:
        if key in seen:
            continue
        ordered.append({"key": key, "name": label})
        seen.add(key)
    return ordered


def load_all_kommo_pipelines(*, force: bool = False) -> list[dict]:
    """All active Kommo funnels with statuses in CRM sort order."""
    global _all_pipelines_cache, _all_pipelines_cache_at
    now = _time_module.time()
    if not force and _all_pipelines_cache and (now - _all_pipelines_cache_at) < 300:
        return _all_pipelines_cache
    rows: list[dict] = []
    try:
        resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads/pipelines", headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            rows = (resp.json().get("_embedded") or {}).get("pipelines") or []
    except Exception as exc:
        logger.warning("Kommo pipelines list failed: %s", exc)
        if _all_pipelines_cache:
            return _all_pipelines_cache
        return []
    result: list[dict] = []
    packs = _personal_pipeline_packs()
    for pipeline in sorted(rows, key=lambda item: int((item or {}).get("sort") or 0)):
        if not isinstance(pipeline, dict) or pipeline.get("is_archive"):
            continue
        try:
            pid = int(pipeline.get("id"))
        except (TypeError, ValueError):
            continue
        statuses = (pipeline.get("_embedded") or {}).get("statuses") or []
        if pid not in packs and pid not in _pipeline_stage_cache:
            stages, names, ui = _stage_maps_from_statuses(statuses)
            if stages:
                _pipeline_stage_cache[pid] = (stages, names, ui)
        ordered = _ordered_ui_for_pipeline(pid, statuses)
        if not ordered:
            continue
        result.append({
            "id": pid,
            "name": str(pipeline.get("name") or pid),
            "sort": int(pipeline.get("sort") or 0),
            "is_main": bool(pipeline.get("is_main")),
            "stages": ordered,
        })
    if result:
        _all_pipelines_cache = result
        _all_pipelines_cache_at = now
    return result or (_all_pipelines_cache or [])


def get_funnel_owner(chat_id) -> dict | None:
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return None
    if cid in RUFAT_COMPAT_CHAT_IDS:
        stages, names, ui = load_pipeline_stage_maps(RUFAT_PIPELINE_ID)
        return {
            "chat_id": RUFAT_CHAT_ID, "pipeline_id": int(RUFAT_PIPELINE_ID),
            "name": "Rüfət Həsənzadə", "stages": stages, "stage_names": names, "ui_stages": ui,
        }
    mapping = {
        ADMIN_CHAT_ID: (NIZAMI_PIPELINE_ID, "Nizami Qasımov"),
        HUSEYN_CHAT_ID: (HUSEYN_PIPELINE_ID, "Hüseyn Səfərov"),
        RASIM_CHAT_ID: (RASIM_PIPELINE_ID, "Rasim Əsgərov"),
    }
    row = mapping.get(cid)
    if not row:
        return None
    pipeline_id, name = row
    stages, names_map, ui = load_pipeline_stage_maps(pipeline_id)
    return {
        "chat_id": cid, "pipeline_id": int(pipeline_id),
        "name": name, "stages": stages, "stage_names": names_map, "ui_stages": ui,
    }


def is_funnel_chat(chat_id) -> bool:
    return get_funnel_owner(chat_id) is not None


def all_personal_pipeline_ids() -> set[int]:
    return {int(RUFAT_PIPELINE_ID), int(NIZAMI_PIPELINE_ID), int(HUSEYN_PIPELINE_ID), int(RASIM_PIPELINE_ID)}


def employee_personal_pipeline_ids() -> set[int]:
    """Funnels owned by Rüfət / Hüseyn / Rasim — not admin Gözləmə."""
    return {int(RUFAT_PIPELINE_ID), int(HUSEYN_PIPELINE_ID), int(RASIM_PIPELINE_ID)}


def kommo_user_id_for_employee_funnel(pipeline_id: int) -> int | None:
    if int(pipeline_id) in employee_personal_pipeline_ids():
        return 15532668
    return None


def reassign_open_tasks_for_lead(lead_id: int, responsible_user_id: int) -> int:
    """Point every open task on the lead (and its contacts) at the funnel owner."""
    try:
        lid = int(lead_id)
        uid = int(responsible_user_id)
    except (TypeError, ValueError):
        return 0
    entities = [("leads", lid)]
    lead = get_lead_details(lid) or {}
    for contact in (lead.get("_embedded") or {}).get("contacts") or []:
        try:
            entities.append(("contacts", int(contact["id"])))
        except (KeyError, TypeError, ValueError):
            continue
    seen: set[int] = set()
    updated = 0
    for entity_type, entity_id in entities:
        for task in get_entity_tasks(entity_id, entity_type):
            try:
                task_id = int(task.get("id"))
            except (TypeError, ValueError):
                continue
            if task_id in seen:
                continue
            seen.add(task_id)
            try:
                current = int(task.get("responsible_user_id") or 0)
            except (TypeError, ValueError):
                current = 0
            if current == uid:
                continue
            if update_task_kommo(task_id, {"responsible_user_id": uid}):
                updated += 1
    if updated:
        logger.info("Reassigned %s open task(s) on lead %s to Kommo user %s", updated, lid, uid)
        try:
            invalidate_rufat_overview_cache()
        except NameError:
            pass
    return updated


def maybe_reassign_open_tasks_for_employee_funnel(lead_id, pipeline_id) -> int:
    uid = kommo_user_id_for_employee_funnel(int(pipeline_id or 0))
    if not uid or not lead_id:
        return 0
    try:
        return reassign_open_tasks_for_lead(int(lead_id), uid)
    except Exception as exc:
        logger.warning("Failed to reassign tasks for lead %s: %s", lead_id, exc)
        return 0


def owner_name_for_pipeline(pipeline_id: int) -> str:
    return {
        int(RUFAT_PIPELINE_ID): "Rüfət Həsənzadə",
        int(NIZAMI_PIPELINE_ID): "Nizami Qasımov",
        int(HUSEYN_PIPELINE_ID): "Hüseyn Səfərov",
        int(RASIM_PIPELINE_ID): "Rasim Əsgərov",
    }.get(int(pipeline_id), "")


def employee_name_for_lead(lead: dict | None) -> str:
    if not isinstance(lead, dict):
        return ""
    try:
        pipeline_id = int(lead.get("pipeline_id") or 0)
    except (TypeError, ValueError):
        pipeline_id = 0
    name = owner_name_for_pipeline(pipeline_id)
    if name:
        return name
    return str(KOMMO_USERS.get(lead.get("responsible_user_id"), "") or "").strip()


def personal_entry_stage(pipeline_id: int) -> tuple[int, int] | None:
    stages, _names, ui = load_pipeline_stage_maps(pipeline_id)
    skip = {"nerazobrannoye", "ugurlu", "imtina"}
    for key, _label in ui:
        if key in skip:
            continue
        status_id = stages.get(key)
        if status_id:
            return int(pipeline_id), int(status_id)
    status_id = stages.get("sorgular") or stages.get("nizami")
    if not status_id:
        return None
    return int(pipeline_id), int(status_id)


def move_lead_to_icraci(lead_id, assignee_name: str) -> bool:
    """Move a deal to the first stage of the selected icraçı funnel, else Gözləmə."""
    if not lead_id or not assignee_name:
        return False
    route = route_deal_for_employee(assignee_name)
    if not route:
        return False
    pipeline_id, status_id = route
    ok = bool(update_lead_kommo(int(lead_id), {"pipeline_id": int(pipeline_id), "status_id": int(status_id)}))
    if ok:
        try:
            invalidate_rufat_overview_cache()
        except NameError:
            pass
    return ok


def route_deal_for_employee(name: str = "", chat_id=None) -> tuple[int, int] | None:
    """Put an assigned deal into that employee's personal funnel, else Gözləmə."""
    owner = get_funnel_owner(chat_id) if chat_id else None
    if not owner and name:
        owner = get_funnel_owner(NAME_TO_CHAT.get(name))
    if owner:
        return personal_entry_stage(owner["pipeline_id"])
    waiting_chat = chat_id
    if waiting_chat is None and name:
        waiting_chat = NAME_TO_CHAT.get(name)
    try:
        status_id = TG_TO_STATUS_ID.get(int(waiting_chat)) if waiting_chat else None
    except (TypeError, ValueError):
        status_id = None
    if status_id:
        return int(GOZLEME_PIPELINE_ID), int(status_id)
    return None


def get_pipeline_id_for_chat(chat_id=None) -> int:
    owner = get_funnel_owner(chat_id)
    if owner:
        return int(owner["pipeline_id"])
    return PIPELINE_ID


def get_pipeline_stages_for_chat(chat_id=None) -> tuple[dict, dict]:
    owner = get_funnel_owner(chat_id)
    if owner:
        return owner["stages"], owner["stage_names"]
    return (STAGES, STAGE_NAMES)


def get_rufat_completion_stage(pipeline_key: str, stage_key: str, chat_id=None, lead_pipeline_id: int | None = None) -> tuple[int, int, str] | None:
    """Resolve the completion-stage choice across a personal funnel and Əməliyyatlar."""
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
    if pipeline_key == "operations":
        if stage_key in ("samil", "shamil"):
            stage_key = "rufat"
        operation_stages = {
            "rufat": (109988184, "Rüfət Həsənzadə"),
            "soltan": (109988188, "Soltan Abbasov"),
            "huseyn": (109988192, "Hüseyn Səfərov"),
            "nizami": (109988196, "Nizami Qasımov"),
            "rasim": (109988200, "Rasim Əsgərov"),
            "sermaye": (109988204, "Sərmayə Əhmədsoy"),
            "asya": (109988208, "Asya Agayeva"),
            "nurane": (109988212, "Nuranə Şirinova"),
            "ugurlu": (142, "Uğurla tamamlandı"),
            "imtina": (143, "İmtina olundu"),
        }
        stage = operation_stages.get(stage_key)
        if stage:
            return GOZLEME_PIPELINE_ID, int(stage[0]), stage[1]
    return None


def get_lead_details(lead_id: int) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}"
    try:
        resp = _http.get(url, headers=HEADERS, params={"with": "contacts"}, timeout=8)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Lead details error: {e}")
    return None

def get_task_deal_context(task_data: dict) -> dict:
    """Resolve a Kommo task to its deal and primary client details."""
    entity_id = task_data.get("entity_id")
    entity_type = _normalize_kommo_entity_type(task_data.get("entity_type", "contacts"))
    lead_id = entity_id if entity_type == "leads" else None
    contact = None

    if entity_id and entity_type == "contacts":
        contact = get_contact_details(int(entity_id))
        preferred = _preferred_lead_for_rufat(_leads_linked_to_contact(int(entity_id)))
        if preferred:
            lead_id = preferred.get("id")
    elif lead_id:
        lead = get_lead_details(int(lead_id))
        contacts = (lead or {}).get("_embedded", {}).get("contacts", [])
        if contacts:
            contact = get_contact_details(int(contacts[0]["id"]))

    client_name = (contact or {}).get("name", "")
    phones = _contact_phones(contact)
    phone = phones[0] if phones else ""

    return {
        "lead_id": int(lead_id) if lead_id else None,
        "client_name": client_name,
        "phone": phone,
        "link": f"{KOMMO_BASE_URL}/leads/detail/{lead_id}" if lead_id else "",
    }

def _contact_phones(contact: dict | None) -> list[str]:
    """Collect unique phone numbers from a Kommo contact payload."""
    if not isinstance(contact, dict):
        return []
    phones: list[str] = []
    seen: set[str] = set()

    def _add(raw) -> None:
        value = str(raw or "").strip()
        if value and value not in seen:
            seen.add(value)
            phones.append(value)

    _add(contact.get("phone"))
    extra = contact.get("phones")
    if isinstance(extra, list):
        for item in extra:
            _add(item)
    for field in contact.get("custom_fields_values") or []:
        if not isinstance(field, dict):
            continue
        code = str(field.get("field_code") or "").upper()
        name = str(field.get("field_name") or "").casefold()
        if code != "PHONE" and "phone" not in name and "telefon" not in name:
            continue
        for item in field.get("values") or []:
            if isinstance(item, dict):
                _add(item.get("value"))
            else:
                _add(item)
    return phones


def _contact_cache_entry(contact: dict | None) -> dict:
    phones = _contact_phones(contact)
    source = extract_menbe(contact)
    return {
        "name": (contact or {}).get("name", ""),
        "phone": phones[0] if phones else "",
        "phones": phones,
        "source": source,
        "menbe": source,
    }


def get_phone_from_entity(entity_id: int, entity_type: str) -> str:
    try:
        if entity_type == "leads":
            lead = get_lead_details(entity_id)
            if lead:
                contacts_emb = lead.get("_embedded", {}).get("contacts", [])
                if contacts_emb:
                    full_c = get_contact_details(contacts_emb[0]["id"])
                    phones = _contact_phones(full_c)
                    return phones[0] if phones else ""
        elif entity_type == "contacts":
            full_c = get_contact_details(entity_id)
            phones = _contact_phones(full_c)
            return phones[0] if phones else ""
    except Exception:
        pass
    return ""

def get_contact_name_from_entity(entity_id: int, entity_type: str) -> str:
    try:
        if entity_type == "leads":
            lead = get_lead_details(entity_id)
            if lead:
                contacts_emb = lead.get("_embedded", {}).get("contacts", [])
                if contacts_emb:
                    full_c = get_contact_details(contacts_emb[0]["id"])
                    if full_c:
                        return full_c.get("name", "")
        elif entity_type == "contacts":
            full_c = get_contact_details(entity_id)
            if full_c:
                return full_c.get("name", "")
    except Exception:
        pass
    return ""

def get_contact_notes(contact_id: int) -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}/notes"
    try:
        resp = _http.get(url, headers=HEADERS, params={"limit": 20, "order[updated_at]": "desc"}, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("notes", [])
    except:
        pass
    return []

def get_entity_tasks(entity_id: int, entity_type: str) -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks"
    params = {"filter[entity_id]": entity_id, "filter[entity_type]": entity_type, "filter[is_completed]": 0}
    try:
        resp = _http.get(url, headers=HEADERS, params=params, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("tasks", [])
    except:
        pass
    return []

def get_tasks(start: datetime, end: datetime, responsible_id: int = None) -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks"
    params = {
        "filter[is_completed]": 0,
        "filter[complete_till][from]": int(start.timestamp()),
        "filter[complete_till][to]": int(end.timestamp()),
        "limit": 50,
    }
    if responsible_id:
        params["filter[responsible_user_id]"] = responsible_id
    try:
        resp = _http.get(url, headers=HEADERS, params=params, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("tasks", [])
    except:
        pass
    return []

def get_all_incomplete_tasks() -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks"
    params = {"filter[is_completed]": 0, "limit": 250}
    try:
        resp = _http.get(url, headers=HEADERS, params=params, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("tasks", [])
    except:
        pass
    return []

_BOT_CREATED_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bot_created_cache.json")


def _save_bot_created_cache():
    try:
        cutoff = _time_module.time() - 180
        payload = {
            "tasks": {str(k): v for k, v in _bot_created_tasks_ts.items() if v >= cutoff},
            "leads": {str(k): v for k, v in _pending_bot_task_leads.items() if v >= cutoff},
        }
        with open(_BOT_CREATED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def _load_bot_created_cache():
    try:
        with open(_BOT_CREATED_CACHE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        cutoff = _time_module.time() - 180
        for k, v in (payload.get("tasks") or {}).items():
            ts = float(v or 0)
            if ts >= cutoff:
                _bot_created_tasks.add(int(k))
                _bot_created_tasks_ts[int(k)] = ts
        for k, v in (payload.get("leads") or {}).items():
            ts = float(v or 0)
            if ts >= cutoff:
                _pending_bot_task_leads[int(k)] = ts
    except Exception:
        pass


def _mark_pending_bot_task_lead(entity_id: int):
    try:
        lid = int(entity_id)
    except (TypeError, ValueError):
        return
    _pending_bot_task_leads[lid] = _time_module.time()
    if len(_pending_bot_task_leads) > 200:
        cutoff = _time_module.time() - 120
        for key, ts in list(_pending_bot_task_leads.items()):
            if ts < cutoff:
                _pending_bot_task_leads.pop(key, None)
    _save_bot_created_cache()


def _is_bot_created_task_webhook(task_id, entity_id) -> bool:
    _load_bot_created_cache()
    now = _time_module.time()
    if task_id:
        try:
            tid = int(task_id)
        except (TypeError, ValueError):
            tid = None
        if tid is not None:
            if tid in _bot_created_tasks:
                ts = _bot_created_tasks_ts.get(tid, 0)
                if now - ts < 180:
                    return True
                _bot_created_tasks.discard(tid)
                _bot_created_tasks_ts.pop(tid, None)
    if entity_id:
        try:
            lid = int(entity_id)
        except (TypeError, ValueError):
            lid = None
        if lid is not None:
            ts = _pending_bot_task_leads.get(lid, 0)
            if ts and now - ts < 180:
                return True
    return False


def create_task(entity_id: int, text: str, complete_till: int, responsible_user_id: int = None, entity_type: str = "contacts", task_type_id: int = 1, creator_name: str = "") -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks"
    task_payload = {
        "text": text,
        "complete_till": complete_till,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "responsible_user_id": responsible_user_id or 10932455,
    }
    if task_type_id:
        task_payload["task_type_id"] = task_type_id
    payload = [task_payload]
    logger.info(f"create_task: entity_id={entity_id}, text={text[:50]}, resp_user={responsible_user_id}, type_id={task_type_id}")
    _mark_pending_bot_task_lead(entity_id)
    try:
        resp = _http.post(url, headers=HEADERS, json=payload, timeout=8)
        if resp.status_code not in (200, 201):
            logger.error(f"create_task FAILED: status={resp.status_code}, body={resp.text[:300]}")
        if resp.status_code in (200, 201):
            result = resp.json()
            try:
                created_id = result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
                if created_id:
                    import time as _time
                    _bot_created_tasks.add(int(created_id))
                    _bot_created_tasks_ts[int(created_id)] = _time.time()
                    _save_bot_created_cache()
                    if len(_bot_created_tasks) > 500:
                        oldest = next(iter(_bot_created_tasks))
                        _bot_created_tasks.discard(oldest)
                        _bot_created_tasks_ts.pop(oldest, None)
                    # Store creator name
                    if creator_name:
                        _creators = read_json(_TASK_CREATORS_FILE) or {}
                        _creators[str(created_id)] = creator_name
                        # Keep max 500 entries
                        if len(_creators) > 500:
                            keys = list(_creators.keys())
                            for k in keys[:len(keys)-400]:
                                del _creators[k]
                        write_json(_TASK_CREATORS_FILE, _creators)
            except Exception:
                pass
            return result
    except Exception as e:
        logger.error(f"Create task error: {e}")
    return None

def add_note(entity_id: int, text: str, entity_type: str = "contacts") -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes"
    payload = [{"note_type": "common", "params": {"text": text}}]
    try:
        resp = _http.post(url, headers=HEADERS, json=payload, timeout=8)
        if resp.status_code in (200, 201):
            return resp.json()
    except Exception as e:
        logger.error(f"Add note error: {e}")
    return None

_NOTE_DELETED_MARK = "⟦silindi⟧"

def _note_is_deleted(item: dict | None) -> bool:
    text = str((item or {}).get("text") or "").strip()
    return (not text) or text == _NOTE_DELETED_MARK


def _lookup_note(note_id: int, kinds: list[str], entity_ids: list[int]) -> tuple[str, int, str]:
    nid = int(note_id)
    ids = [int(i) for i in entity_ids if int(i or 0)]
    for kind in kinds:
        for url, params in (
            (f"{KOMMO_BASE_URL}/api/v4/{kind}/notes/{nid}", None),
            (f"{KOMMO_BASE_URL}/api/v4/{kind}/notes", {"filter[id][]": nid, "limit": 1}),
        ):
            try:
                resp = _http.get(url, headers=HEADERS, params=params, timeout=8)
            except Exception as exc:
                logger.warning("Lookup note %s failed: %s", url, exc)
                continue
            if resp.status_code != 200:
                continue
            payload = resp.json() if resp.content else {}
            if isinstance(payload, dict) and payload.get("id"):
                notes = [payload]
            else:
                notes = ((payload or {}).get("_embedded") or {}).get("notes") or []
            if notes and isinstance(notes[0], dict):
                note = notes[0]
                try:
                    eid = int(note.get("entity_id") or 0)
                except (TypeError, ValueError):
                    eid = 0
                return kind, eid, str(note.get("note_type") or "common")
        for eid in ids:
            try:
                resp = _http.get(
                    f"{KOMMO_BASE_URL}/api/v4/{kind}/{eid}/notes",
                    headers=HEADERS,
                    params={"limit": 250, "filter[id][]": nid},
                    timeout=8,
                )
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            for note in ((resp.json() or {}).get("_embedded") or {}).get("notes") or []:
                if not isinstance(note, dict):
                    continue
                try:
                    if int(note.get("id") or 0) != nid:
                        continue
                except (TypeError, ValueError):
                    continue
                return kind, eid, str(note.get("note_type") or "common")
    return "", 0, ""


def _note_is_present(note_id: int, kind: str, entity_id: int) -> bool | None:
    """True when Kommo still has the note, False when a read proves it is gone."""
    try:
        nid = int(note_id)
        eid = int(entity_id or 0)
    except (TypeError, ValueError):
        return None
    if not nid or not kind:
        return None
    urls: list[tuple[str, dict | None]] = []
    if eid:
        urls.append((f"{KOMMO_BASE_URL}/api/v4/{kind}/{eid}/notes", {"filter[id][]": nid, "limit": 5}))
    urls.append((f"{KOMMO_BASE_URL}/api/v4/{kind}/notes/{nid}", None))
    saw_answer = False
    for url, params in urls:
        try:
            resp = _http.get(url, headers=HEADERS, params=params, timeout=8)
        except Exception as exc:
            logger.warning("Recheck note %s failed: %s", url, exc)
            continue
        if resp.status_code in {204, 404}:
            saw_answer = True
            continue
        if resp.status_code != 200:
            continue
        saw_answer = True
        payload = resp.json() if resp.content else {}
        notes = []
        if isinstance(payload, dict) and payload.get("id"):
            notes = [payload]
        elif isinstance(payload, dict):
            notes = ((payload.get("_embedded") or {}).get("notes") or [])
        for note in notes:
            if not isinstance(note, dict):
                continue
            try:
                if int(note.get("id") or 0) != nid:
                    continue
            except (TypeError, ValueError):
                continue
            if note.get("is_deleted") in {True, 1, "1"}:
                continue
            return True
        if url.endswith(f"/notes/{nid}"):
            return False
    if saw_answer:
        return False
    return None


def _note_text_is_blank(note_id: int, kind: str) -> bool:
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return False
    if not nid or not kind:
        return False
    try:
        resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/{kind}/notes/{nid}", headers=HEADERS, timeout=8)
    except Exception as exc:
        logger.warning("Blank note check %s failed: %s", note_id, exc)
        return False
    if resp.status_code in {204, 404}:
        return True
    if resp.status_code != 200:
        return False
    payload = resp.json() if resp.content else {}
    params = payload.get("params") if isinstance(payload, dict) and isinstance(payload.get("params"), dict) else {}
    return not str(params.get("text") or "").strip()


def _kommo_ajax_delete_note(note_id: int, entity_id: int, kind: str) -> bool:
    element_type = "1" if str(kind).startswith("contact") else "2"
    form_headers = {
        "Authorization": f"Bearer {KOMMO_TOKEN}",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    attempts = [
        (
            f"{KOMMO_BASE_URL}/ajax/v1/notes/set/",
            {
                "request[notes][delete][0][id]": str(note_id),
                "request[notes][delete][0][element_id]": str(entity_id or ""),
                "request[notes][delete][0][element_type]": element_type,
            },
        ),
        (
            f"{KOMMO_BASE_URL}/private/notes/edit2.php",
            {"ID": str(note_id), "ACTION": "NOTE_DELETE", "ELEMENT_ID": str(entity_id or ""), "ELEMENT_TYPE": element_type},
        ),
    ]
    for url, form in attempts:
        try:
            resp = _http.post(url, headers=form_headers, data=form, timeout=10)
        except Exception as exc:
            logger.warning("Ajax delete note failed: %s", exc)
            continue
        body = (resp.text or "")
        folded = body.lower()
        if resp.status_code in {200, 202, 204} and "<html" not in folded and "login" not in folded:
            if "error" in folded and "success" not in folded and "status\":\"ok" not in folded:
                logger.warning("Ajax delete note %s %s: %s", resp.status_code, url, body[:180])
                continue
            return True
        logger.warning("Ajax delete note %s %s: %s", resp.status_code, url, body[:180])
    try:
        resp = _http.post(
            f"{KOMMO_BASE_URL}/api/v2/notes",
            headers=HEADERS,
            json={"delete": [{"id": int(note_id)}]},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("v2 delete note failed: %s", exc)
        return False
    if resp.status_code in {200, 202, 204}:
        return True
    logger.warning("v2 delete note %s: %s", resp.status_code, (resp.text or "")[:180])
    return False


def _note_delete_error(raw: str) -> str:
    text = str(raw or "")
    if not text or text.startswith("{") or "FieldMissing" in text or "Bad Request" in text:
        return "Qeyd silinmədi."
    return text[:180]


def delete_note(note_id: int, entity_type: str = "leads", entity_id: int = 0, extra_ids: list[int] | None = None, note_type: str = "") -> tuple[bool, str]:
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return False, "Qeyd tapılmadı."
    try:
        eid = int(entity_id or 0)
    except (TypeError, ValueError):
        eid = 0
    ok, err = update_note(nid, " ", _note_entity_kind(entity_type), eid)
    if ok:
        _forget_cached_note(nid)
        return True, ""
    return False, err or "Qeyd silinmədi."

def _note_entity_kind(entity_type: str) -> str:
    raw = str(entity_type or "").strip().lower()
    if raw.startswith("contact") or raw in {"1", "contacts"}:
        return "contacts"
    return "leads"


def update_note(note_id: int, text: str, entity_type: str = "leads", entity_id: int = 0) -> tuple[bool, str]:
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return False, "Qeyd tapılmadı."
    try:
        eid = int(entity_id or 0)
    except (TypeError, ValueError):
        eid = 0
    primary = _note_entity_kind(entity_type)
    kinds = [primary] + ([k for k in ("leads", "contacts") if k != primary])
    body = {"id": nid, "params": {"text": str(text or "")}}
    if eid:
        body["entity_id"] = eid
    last_err = "Qeyd yenilənmədi."
    for kind in kinds:
        payloads = [[{**body, "note_type": "common"}], [body]]
        urls = [f"{KOMMO_BASE_URL}/api/v4/{kind}/notes"]
        if eid:
            urls.insert(0, f"{KOMMO_BASE_URL}/api/v4/{kind}/{eid}/notes")
        for url in urls:
            for payload in payloads:
                try:
                    resp = _http.patch(url, headers=HEADERS, json=payload, timeout=10)
                except Exception as exc:
                    logger.error("Update note error: %s", exc)
                    last_err = str(exc)
                    continue
                if resp.status_code in {200, 202, 204}:
                    return True, ""
                last_err = (resp.text or "")[:240] or f"HTTP {resp.status_code}"
                logger.warning("Update note %s %s: %s", resp.status_code, url, last_err)
    return False, last_err

def _created_task_id(result) -> int:
    if not isinstance(result, dict):
        return 0
    tasks = (result.get("_embedded") or {}).get("tasks") or []
    if tasks and isinstance(tasks[0], dict):
        try:
            return int(tasks[0].get("id") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _first_note_id(result) -> int:
    if not isinstance(result, dict):
        return 0
    notes = (result.get("_embedded") or {}).get("notes") or []
    if notes and isinstance(notes[0], dict):
        try:
            return int(notes[0].get("id") or 0)
        except (TypeError, ValueError):
            return 0
    return 0

def update_lead_kommo(lead_id: int, data: dict) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}"
    old_pipeline_id = None
    new_pipeline_id = 0
    try:
        new_pipeline_id = int((data or {}).get("pipeline_id") or 0)
    except (TypeError, ValueError):
        new_pipeline_id = 0
    if new_pipeline_id and kommo_user_id_for_employee_funnel(new_pipeline_id):
        details = get_lead_details(int(lead_id))
        if details:
            old_pipeline_id = _lead_pipeline_id(details)
    try:
        resp = _http.patch(url, headers=HEADERS, json=data, timeout=8)
        if resp.status_code == 200:
            # Track bot-initiated stage changes to suppress webhook echo
            if "status_id" in data:
                import time as _time
                _bot_changed_leads[int(lead_id)] = _time.time()
                # Cleanup old entries (>60s)
                cutoff = _time.time() - 60
                for k in list(_bot_changed_leads.keys()):
                    if _bot_changed_leads[k] < cutoff:
                        del _bot_changed_leads[k]
            if (
                new_pipeline_id
                and old_pipeline_id is not None
                and new_pipeline_id != old_pipeline_id
            ):
                maybe_reassign_open_tasks_for_employee_funnel(lead_id, new_pipeline_id)
            return resp.json()
    except Exception as e:
        logger.error(f"Update lead error: {e}")
    return None

def update_task_kommo(task_id, data: dict) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}"
    import time as _time
    _bot_updated_tasks[int(task_id)] = _time.time()
    if data.get("is_completed"):
        unregister_tecili_task(task_id)
    try:
        resp = _http.patch(url, headers=HEADERS, json=data, timeout=8)
        logger.info(f"update_task_kommo {task_id}: status={resp.status_code}")
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.error(f"update_task_kommo failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Update task error: {e}")
    return None

def update_contact_kommo(contact_id: int, data: dict) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}"
    try:
        resp = _http.patch(url, headers=HEADERS, json=data, timeout=8)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Update contact error: {e}")
    return None

def create_contact_kommo(name: str, phone: str, custom_fields: list = None, responsible_user_id: int = 10932455) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts"
    payload = [{
        "name": name,
        "responsible_user_id": responsible_user_id,
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}
        ] + (custom_fields or [])
    }]
    try:
        resp = _http.post(url, headers=HEADERS, json=payload, timeout=8)
        if resp.status_code in (200, 201):
            return resp.json()
    except Exception as e:
        logger.error(f"Create contact error: {e}")
    return None

def _normalize_kommo_entity_type(entity_type) -> str:
    value = str(entity_type or "").strip().lower()
    if value in ("lead", "leads"):
        return "leads"
    if value in ("contact", "contacts"):
        return "contacts"
    return value or "leads"


def _lead_pipeline_id(lead) -> int:
    raw = (lead or {}).get("pipeline_id", 0) if isinstance(lead, dict) else 0
    if isinstance(raw, dict):
        raw = raw.get("id") or raw.get("pipeline_id") or 0
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _cached_funnel_has_lead(chat_id, lead_id) -> bool:
    owner = get_funnel_owner(chat_id)
    if not owner or not lead_id:
        return False
    try:
        lid = int(lead_id)
        pid = int(owner["pipeline_id"])
    except (TypeError, ValueError):
        return False
    cached = _personal_overview_cache.get(pid) or {}
    for deal in cached.get("deals") or []:
        try:
            if int(deal.get("id")) == lid:
                return True
        except (TypeError, ValueError):
            continue
    for rows in (cached.get("deals_by_stage") or {}).values():
        for deal in rows or []:
            try:
                if int(deal.get("id")) == lid:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _task_entity_pipeline_ids(entity_type, entity_id, leads_pipeline_cache: dict, contact_pipeline_ids: dict) -> set[int]:
    """Resolve pipeline ids from already-fetched lead/contact caches only."""
    etype = _normalize_kommo_entity_type(entity_type)
    try:
        eid = int(entity_id)
    except (TypeError, ValueError):
        return set()
    if etype == "leads":
        pid = leads_pipeline_cache.get(eid)
        return {pid} if pid else set()
    return set(contact_pipeline_ids.get(eid) or [])


def _rufat_permitted_pipeline_ids(chat_id=None) -> set[int]:
    owner = get_funnel_owner(chat_id)
    if owner:
        return {int(owner["pipeline_id"])}
    return {int(RUFAT_PIPELINE_ID)}


def _rufat_may_use_lead(lead_id=None, lead=None, chat_id=None) -> bool:
    permitted = _rufat_permitted_pipeline_ids(chat_id)
    if lead is not None and _lead_pipeline_id(lead) in permitted:
        return True
    resolved_id = lead_id
    if resolved_id is None and isinstance(lead, dict):
        resolved_id = lead.get("id")
    if not resolved_id:
        return False
    details = get_lead_details(int(resolved_id))
    if details:
        return _lead_pipeline_id(details) in permitted
    return _cached_funnel_has_lead(chat_id, resolved_id)


def _leads_linked_to_contact(contact_id: int) -> list:
    contact = get_contact_details(int(contact_id))
    leads = list((contact or {}).get("_embedded", {}).get("leads", []) or [])
    if leads:
        return leads
    try:
        resp = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/contacts/{int(contact_id)}/leads",
            headers=HEADERS,
            timeout=8,
        )
        if resp.status_code == 200:
            return list(resp.json().get("_embedded", {}).get("leads", []) or [])
    except Exception as exc:
        logger.error("Contact leads lookup error: %s", exc)
    return []


def _preferred_lead_for_rufat(leads: list) -> dict | None:
    if not leads:
        return None
    ranked = []
    for item in leads:
        try:
            lead_id = int(item["id"] if isinstance(item, dict) else item)
        except (TypeError, ValueError, KeyError):
            continue
        lead = item if isinstance(item, dict) and item.get("pipeline_id") is not None else get_lead_details(lead_id)
        ranked.append(lead or {"id": lead_id})
    for wanted in (int(RUFAT_PIPELINE_ID), int(GOZLEME_PIPELINE_ID)):
        for lead in ranked:
            if _lead_pipeline_id(lead) == wanted:
                return lead
    return ranked[0]


def lead_belongs_to_pipeline(lead_id: int, pipeline_id: int) -> bool:
    lead = get_lead_details(int(lead_id))
    if lead:
        return _lead_pipeline_id(lead) == int(pipeline_id)
    owner = None
    for chat_id in (RUFAT_CHAT_ID, HUSEYN_CHAT_ID, RASIM_CHAT_ID, ADMIN_CHAT_ID):
        candidate = get_funnel_owner(chat_id)
        if candidate and int(candidate["pipeline_id"]) == int(pipeline_id):
            owner = candidate
            break
    return bool(owner and _cached_funnel_has_lead(owner["chat_id"], lead_id))


def lead_allowed_for_chat(lead_id: int, chat_id: int) -> bool:
    if is_admin(chat_id) or not is_funnel_chat(chat_id):
        return True
    return lead_belongs_to_pipeline(lead_id, get_pipeline_id_for_chat(chat_id))


def get_kommo_task(task_id) -> dict | None:
    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return None
    try:
        resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{tid}", headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            data = resp.json() or {}
            if isinstance(data, dict) and data.get("id"):
                return data
            embedded = (data.get("_embedded") or {}).get("tasks") or []
            if embedded:
                return embedded[0]
        resp = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/tasks",
            headers=HEADERS,
            params={"filter[id][]": tid, "limit": 1},
            timeout=8,
        )
        if resp.status_code == 200:
            tasks = (resp.json() or {}).get("_embedded", {}).get("tasks") or []
            if tasks:
                return tasks[0]
        logger.warning("get_kommo_task %s HTTP %s", tid, getattr(resp, "status_code", "?"))
    except Exception as exc:
        logger.error("get_kommo_task error: %s", exc)
    return None


def task_payload_allowed_for_chat(task: dict, chat_id: int) -> bool:
    if is_admin(chat_id) or not is_funnel_chat(chat_id):
        return True
    entity_id = (task or {}).get("entity_id")
    if not entity_id:
        return False
    entity_type = _normalize_kommo_entity_type((task or {}).get("entity_type", "leads"))
    if entity_type == "leads":
        allowed = _rufat_may_use_lead(lead_id=int(entity_id), chat_id=chat_id)
        if not allowed:
            allowed = _cached_funnel_has_lead(chat_id, entity_id)
        if not allowed:
            logger.warning(
                "task_allowed_for_chat: lead %s is outside this funnel",
                entity_id,
            )
        return allowed
    if entity_type == "contacts":
        allowed = any(
            _rufat_may_use_lead(
                lead=lead,
                lead_id=lead.get("id") if isinstance(lead, dict) else lead,
                chat_id=chat_id,
            )
            for lead in _leads_linked_to_contact(int(entity_id))
        )
        if not allowed:
            allowed = _cached_funnel_has_lead(chat_id, entity_id)
        if not allowed:
            logger.warning(
                "task_allowed_for_chat: contact %s has no funnel lead",
                entity_id,
            )
        return allowed
    logger.warning("task_allowed_for_chat: unsupported entity_type %s", entity_type)
    return False


def task_allowed_for_chat(task_id: int, chat_id: int) -> bool:
    if is_admin(chat_id) or not is_funnel_chat(chat_id):
        return True
    task = get_kommo_task(task_id)
    if not task:
        logger.warning("task_allowed_for_chat: task %s not found", task_id)
        return False
    return task_payload_allowed_for_chat(task, chat_id)


def get_leads_by_status(status_id: int, chat_id: int = None) -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/leads"
    params = {"filter[statuses][0][pipeline_id]": get_pipeline_id_for_chat(chat_id), "filter[statuses][0][status_id]": status_id, "limit": 50}
    try:
        resp = _http.get(url, headers=HEADERS, params=params, timeout=8)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("leads", [])
    except:
        pass
    return []

def get_user_name(user_id: int) -> str:
    return KOMMO_USERS.get(user_id, f"User {user_id}")

def format_contact_info(contact: dict, notes: list = None, tasks: list = None) -> str:
    name = contact.get("name", "Adsız")
    contact_id = contact.get("id", "")
    phone = ""
    email = ""
    partner = ""
    reg_date = ""
    for cf in (contact.get("custom_fields_values") or []):
        if cf.get("field_code") == "PHONE":
            vals = cf.get("values", [])
            if vals:
                phone = vals[0].get("value", "")
        elif cf.get("field_code") == "EMAIL":
            vals = cf.get("values", [])
            if vals:
                email = vals[0].get("value", "")
        elif cf.get("field_id") == 2989615:
            vals = cf.get("values", [])
            if vals:
                partner = vals[0].get("value", "")
        elif cf.get("field_id") == 2989617:
            vals = cf.get("values", [])
            if vals:
                reg_date = vals[0].get("value", "")
    # Created at
    created_at = contact.get("created_at", 0)
    created_str = datetime.fromtimestamp(created_at, tz=BAKU_TZ).strftime("%d.%m.%Y") if created_at else ""
    # Lead info + lead notes
    leads = contact.get("_embedded", {}).get("leads", [])
    lead_info = ""
    lead_notes_list = []
    if leads:
        lead_id = leads[0]["id"]
        lead = get_lead_details(lead_id)
        if lead:
            status_id = lead.get("status_id")
            stage_name = STAGE_NAMES.get(status_id, "Naməlum")
            responsible = KOMMO_USERS.get(lead.get("responsible_user_id"), "Naməlum")
            price = lead.get("price", 0)
            price_str = f"\n💰 Məbləğ: {price} AZN" if price else ""
            lead_info = f"\n\n📋 *Sövdələşmə:* {lead.get('name', '')}\n📌 Mərhələ: {stage_name}\n👤 Məsul: {responsible}{price_str}\n🔗 {KOMMO_BASE_URL}/leads/detail/{lead_id}"
            # Get lead notes too
            try:
                resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}/notes", headers=HEADERS, params={"limit": 20, "order[updated_at]": "desc"}, timeout=8)
                if resp.status_code == 200:
                    lead_notes_list = resp.json().get("_embedded", {}).get("notes", [])
            except:
                pass
    # Combine notes (contact + lead)
    all_notes = []
    for n in (notes or []):
        all_notes.append(n)
    for n in lead_notes_list:
        all_notes.append(n)
    # Sort by created_at desc
    all_notes.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    notes_text = ""
    if all_notes:
        notes_text = "\n\n📝 *Son qeydlər və hadisələr:*"
        shown = 0
        for n in all_notes:
            if shown >= 10:
                break
            note_type = n.get("note_type", "")
            params = n.get("params", {})
            text = ""
            if isinstance(params, dict):
                text = params.get("text", "") or params.get("service", "") or params.get("uniq", "")
            if not text and note_type == "common":
                text = params.get("text", "") if isinstance(params, dict) else str(params)
            if not text:
                continue
            created = n.get("created_at", 0)
            date_str = datetime.fromtimestamp(created, tz=BAKU_TZ).strftime("%d.%m %H:%M") if created else ""
            notes_text += f"\n  {date_str} • {text[:120]}"
            shown += 1
    # Tasks
    tasks_text = ""
    if tasks:
        open_tasks = [t for t in tasks if not t.get("is_completed")]
        if open_tasks:
            tasks_text = f"\n\n📋 *Açıq tapşırıqlar ({len(open_tasks)}):*"
            for t in open_tasks[:5]:
                dt = datetime.fromtimestamp(t.get("complete_till", 0), tz=BAKU_TZ)
                resp_name = KOMMO_USERS.get(t.get("responsible_user_id"), "")
                tasks_text += f"\n  ⏰ {dt.strftime('%d.%m %H:%M')} — {t.get('text', '')[:60]} ({resp_name})"
    # Build header
    header = f"👤 *{name}*\n📞 {phone}"
    if email:
        header += f"\n📧 {email}"
    if partner:
        header += f"\n🤝 Partnyor: {partner}"
    if reg_date:
        header += f"\n📅 Qeydiyyat: {reg_date}"
    elif created_str:
        header += f"\n📅 Yaradılıb: {created_str}"
    msg = f"{header}{lead_info}{notes_text}{tasks_text}"
    return msg

# ─── Partner helpers ─────────────────────────────────────────────────────────
MENBE_FIELD_ID = 2989615
MENBE_LABELS = ("Partner", "Instagram", "TikTok", "Facebook", "SEO", "WhatsApp")
_menbe_enums_cache: list | None = None
_menbe_enums_entity = ""


def fetch_partner_enums() -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/custom_fields"
    try:
        resp = _http.get(url, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            fields = resp.json().get("_embedded", {}).get("custom_fields", [])
            for f in fields:
                if f.get("id") == MENBE_FIELD_ID:
                    return f.get("enums", [])
    except:
        pass
    return []


def fetch_menbe_enums() -> list:
    """Load select options for contact/lead field mənbə (id 2989615)."""
    global _menbe_enums_cache, _menbe_enums_entity
    if _menbe_enums_cache:
        return _menbe_enums_cache
    for path, entity in (("contacts/custom_fields", "contacts"), ("leads/custom_fields", "leads")):
        try:
            resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/{path}", headers=HEADERS, timeout=8)
            if resp.status_code != 200:
                continue
            fields = (resp.json().get("_embedded") or {}).get("custom_fields", []) or []
            for field in fields:
                if int(field.get("id") or 0) != MENBE_FIELD_ID:
                    continue
                enums = field.get("enums") or []
                if enums:
                    _menbe_enums_cache = enums
                    _menbe_enums_entity = entity
                    return enums
        except Exception as exc:
            logger.warning("mənbə enums %s failed: %s", path, exc)
    return fetch_partner_enums()


def normalize_menbe_label(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    aliases = {
        "partner": "Partner", "partnyor": "Partner", "referral": "Partner",
        "instagram": "Instagram", "ig": "Instagram",
        "tiktok": "TikTok", "tt": "TikTok",
        "facebook": "Facebook", "fb": "Facebook", "meta": "Facebook",
        "seo": "SEO", "google": "SEO", "organic": "SEO", "yandex": "SEO",
        "whatsapp": "WhatsApp", "wa": "WhatsApp", "whats-app": "WhatsApp",
        "mənbə": "", "menbe": "",
    }
    folded = raw.casefold()
    if folded in aliases:
        return aliases[folded]
    for label in MENBE_LABELS:
        if label.casefold() == folded:
            return label
    return ""


def menbe_from_utm(*parts: str) -> str:
    blob = " ".join(str(part or "") for part in parts).casefold()
    if not blob.strip():
        return ""
    checks = (
        (("tiktok", "tt."), "TikTok"),
        (("instagram", "ig."), "Instagram"),
        (("facebook", "fb.", " fb "), "Facebook"),
        (("whatsapp", "whats-app", " wa.", " wa "), "WhatsApp"),
        (("seo", "google", "organic", "yandex"), "SEO"),
        (("partner", "partnyor", "referral"), "Partner"),
    )
    for needles, label in checks:
        if any(needle in blob for needle in needles):
            return label
    return normalize_menbe_label(blob)


def extract_menbe(entity: dict | None) -> str:
    if not isinstance(entity, dict):
        return ""
    for cf in entity.get("custom_fields_values") or []:
        try:
            field_id = int(cf.get("field_id") or 0)
        except (TypeError, ValueError):
            field_id = 0
        if field_id != MENBE_FIELD_ID:
            continue
        vals = cf.get("values") or []
        if not vals:
            continue
        return normalize_menbe_label(vals[0].get("value") or "") or str(vals[0].get("value") or "")
    return ""


def collect_utm_blob(entity: dict | None) -> str:
    if not isinstance(entity, dict):
        return ""
    parts: list[str] = []
    for cf in entity.get("custom_fields_values") or []:
        code = str(cf.get("field_code") or "").casefold()
        name = str(cf.get("field_name") or "").casefold()
        values = [str(val.get("value") or "") for val in (cf.get("values") or [])]
        joined_values = " ".join(values)
        if any(token in f"{code} {name}" for token in ("utm", "source", "источник", "mənbə", "menbe", "referr")):
            parts.extend(values)
        elif re.search(r"utm[_-]?(source|medium|campaign|content|term)=", joined_values, re.I):
            parts.append(joined_values)
    meta = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "referrer", "referer"):
        parts.append(str(meta.get(key) or ""))
    source = ((entity.get("_embedded") or {}).get("source") or {})
    if isinstance(source, dict):
        parts.append(str(source.get("name") or ""))
    return " ".join(part for part in parts if part)


_SOURCE_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_SOURCE_FIELD_HINTS = (
    "url", "link", "href", "refer", "landing", "ads", "advert", "click",
    "utm", "facebook", "instagram", "tiktok", "source", "form", "page", "кампан",
)


def collect_source_urls(entity: dict | None) -> list[str]:
    if not isinstance(entity, dict):
        return []
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        for match in _SOURCE_URL_RE.findall(str(raw or "")):
            url = match.rstrip(".,;)]")
            if url and url not in seen:
                seen.add(url)
                found.append(url)

    for cf in entity.get("custom_fields_values") or []:
        code = str(cf.get("field_code") or "").casefold()
        name = str(cf.get("field_name") or "").casefold()
        hint = f"{code} {name}"
        values = [str(val.get("value") or "") for val in (cf.get("values") or [])]
        if any(token in hint for token in _SOURCE_FIELD_HINTS) or any(_SOURCE_URL_RE.search(v) for v in values):
            for value in values:
                add(value)
    meta = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
    for key, value in meta.items():
        add(str(value or ""))
        if isinstance(value, dict):
            for nested in value.values():
                add(str(nested or ""))
    source = ((entity.get("_embedded") or {}).get("source") or {})
    if isinstance(source, dict):
        add(str(source.get("name") or ""))
        add(str(source.get("link") or source.get("url") or ""))
    return found


_PARTNER_LISTS_FILE = "partner_lists.json"
_DEAL_PARTNERS_FILE = "deal_partners.json"


def _load_json_dict(name: str) -> dict:
    data = read_json(name) or {}
    return data if isinstance(data, dict) else {}


def get_partner_list_for_chat(chat_id) -> list[str]:
    items = _load_json_dict(_PARTNER_LISTS_FILE).get(str(chat_id)) or []
    names: list[str] = []
    for raw in items:
        name = str(raw or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def add_partner_for_chat(chat_id, name: str) -> list[str]:
    name = str(name or "").strip()
    items = get_partner_list_for_chat(chat_id)
    if name and name not in items:
        items.append(name)
        data = _load_json_dict(_PARTNER_LISTS_FILE)
        data[str(chat_id)] = items
        write_json(_PARTNER_LISTS_FILE, data)
    return items


def get_deal_partner(lead_id) -> str:
    if not lead_id:
        return ""
    row = _load_json_dict(_DEAL_PARTNERS_FILE).get(str(int(lead_id)))
    if isinstance(row, dict):
        return str(row.get("name") or "").strip()
    return str(row or "").strip()


def set_deal_partner(lead_id, name: str, owner_chat_id=None) -> str:
    name = str(name or "").strip()
    data = _load_json_dict(_DEAL_PARTNERS_FILE)
    key = str(int(lead_id))
    if not name:
        data.pop(key, None)
    else:
        data[key] = {"name": name, "owner": owner_chat_id}
    write_json(_DEAL_PARTNERS_FILE, data)
    return name


def split_partner_and_utm(cf_value: str, utm_blob: str, lead_id: int | None = None) -> tuple[str, str]:
    cf_value = str(cf_value or "").strip()
    utm = menbe_from_utm(utm_blob)
    if not utm and cf_value in MENBE_LABELS:
        utm = cf_value
    partner = get_deal_partner(lead_id) if lead_id else ""
    if cf_value and cf_value not in MENBE_LABELS:
        partner = partner or cf_value
    return partner, utm


def attach_utm_and_partner(deals: list[dict], lead_by_id: dict, contacts: dict) -> None:
    """Fill Partner (personal) and UTM tag on overview deals without mixing them."""
    stored = _load_json_dict(_DEAL_PARTNERS_FILE)
    for deal in deals:
        if not isinstance(deal, dict):
            continue
        try:
            lead_id = int(deal.get("id") or 0)
        except (TypeError, ValueError):
            continue
        lead = lead_by_id.get(lead_id) or {}
        contact_id = None
        rows = deal.get("contacts") or []
        if rows and isinstance(rows[0], dict) and rows[0].get("id"):
            try:
                contact_id = int(rows[0]["id"])
            except (TypeError, ValueError):
                contact_id = None
        contact = contacts.get(contact_id or 0, {})
        cf_value = extract_menbe(contact) or extract_menbe(lead)
        blob = " ".join((collect_utm_blob(lead), collect_utm_blob(contact)))
        row = stored.get(str(lead_id))
        stored_name = str((row.get("name") if isinstance(row, dict) else row) or "").strip()
        partner, utm = split_partner_and_utm(cf_value, blob, None)
        partner = stored_name or partner
        deal["partner"] = partner
        deal["utm"] = utm
        deal["utm_tag"] = utm
        deal["source"] = utm
        deal["menbe"] = utm


def menbe_field_payload(label: str) -> dict | None:
    normalized = normalize_menbe_label(label)
    if not normalized:
        return None
    values = [{"value": normalized}]
    for enum in fetch_menbe_enums():
        if str(enum.get("value") or "").casefold() == normalized.casefold():
            if enum.get("id"):
                values[0]["enum_id"] = enum.get("id")
            break
    return {"field_id": MENBE_FIELD_ID, "values": values}


def set_contact_menbe(contact_id: int, label: str, *, overwrite: bool = True) -> bool:
    payload = menbe_field_payload(label)
    if not payload or not contact_id:
        return False
    if not overwrite:
        details = get_contact_details(int(contact_id)) or {}
        if extract_menbe(details):
            return True
    return bool(update_contact_kommo(int(contact_id), {"custom_fields_values": [payload]}))


def set_lead_menbe(lead_id: int, label: str) -> bool:
    payload = menbe_field_payload(label)
    if not payload or not lead_id:
        return False
    return bool(update_lead_kommo(int(lead_id), {"custom_fields_values": [payload]}))


def apply_menbe(contact_id: int | None, lead_id: int | None, source_label: str = "", utm_blob: str = "", *, overwrite: bool = False) -> str:
    global _menbe_enums_entity
    label = normalize_menbe_label(source_label) or menbe_from_utm(utm_blob)
    if not label:
        return ""
    if contact_id:
        set_contact_menbe(int(contact_id), label, overwrite=overwrite or bool(normalize_menbe_label(source_label)))
    if lead_id:
        fetch_menbe_enums()
        if _menbe_enums_entity == "leads":
            set_lead_menbe(int(lead_id), label)
    return label


def maybe_fill_menbe_from_lead(lead_id: int) -> str:
    lead = get_lead_details(int(lead_id)) or {}
    contacts = (lead.get("_embedded") or {}).get("contacts") or []
    contact_id = 0
    contact = {}
    if contacts:
        try:
            contact_id = int(contacts[0].get("id") or 0)
        except (TypeError, ValueError):
            contact_id = 0
        if contact_id:
            contact = get_contact_details(contact_id) or contacts[0]
    existing = extract_menbe(contact) or extract_menbe(lead)
    if existing:
        return existing
    blob = " ".join((collect_utm_blob(lead), collect_utm_blob(contact)))
    return apply_menbe(contact_id or None, lead_id, utm_blob=blob, overwrite=False)

# ─── OpenAI Function Calling Tools ──────────────────────────────────────────
AI_TOOLS = [
    {"type": "function", "function": {
        "name": "search_contact",
        "description": "Müştərini telefon nömrəsi ilə Kommo CRM-də axtarmaq",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string", "description": "Müştərinin telefon nömrəsi"}
        }, "required": ["phone"]}
    }},
    {"type": "function", "function": {
        "name": "create_task",
        "description": "Kommo CRM-də tapşırıq yaratmaq",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string", "description": "Müştərinin telefon nömrəsi"},
            "text": {"type": "string", "description": "Tapşırığın mətni"},
            "date": {"type": "string", "description": "DD.MM.YYYY formatında tarix (optional)"},
            "time": {"type": "string", "description": "HH:MM formatında vaxt (optional)"},
            "assign_to": {"type": "string", "enum": ["rufat", "soltan", "admin"], "description": "Kimin üçün tapşırıq yaradılır"}
        }, "required": ["phone", "text"]}
    }},
    {"type": "function", "function": {
        "name": "add_note",
        "description": "Müştəriyə qeyd əlavə etmək",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string", "description": "Müştərinin telefon nömrəsi"},
            "text": {"type": "string", "description": "Qeydin mətni"}
        }, "required": ["phone", "text"]}
    }},
    {"type": "function", "function": {
        "name": "change_stage",
        "description": "Sövdələşmənin mərhələsini dəyişmək",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string", "description": "Müştərinin telefon nömrəsi"},
            "stage": {"type": "string", "enum": ["danisiqlar","qiymet_teklifi","teqdimat","teqdimat_olundu","yeni_sifaris","gorus","daxili_muzakire","qurashdirma","dusunur","ugurlu","imtina"], "description": "Yeni mərhələ"}
        }, "required": ["phone", "stage"]}
    }},
    {"type": "function", "function": {
        "name": "complete_task",
        "description": "Müştərinin açıq tapşırığını tamamlamaq",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string", "description": "Müştərinin telefon nömrəsi"}
        }, "required": ["phone"]}
    }},
    {"type": "function", "function": {
        "name": "get_tasks",
        "description": "Tapşırıqları göstərmək (bugünkü, sabahkı, və ya hamısı)",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "enum": ["today", "tomorrow", "all"], "description": "Hansı dövr üçün"},
            "phone": {"type": "string", "description": "Müştərinin telefonu (optional - yalnız bir müştəri üçün)"}
        }, "required": ["period"]}
    }},
    {"type": "function", "function": {
        "name": "get_lead_info",
        "description": "Müştərinin sövdələşmə məlumatını göstərmək",
        "parameters": {"type": "object", "properties": {
            "phone": {"type": "string", "description": "Müştərinin telefon nömrəsi"}
        }, "required": ["phone"]}
    }},
]

AI_SYSTEM_PROMPT = """Sən Bein Systems şirkətinin CRM botusun. Azərbaycan dilində danış.

Sənin YEGĀNƏ rolu: istifadəçi mesajlarını təhlil et və MÜTLƏQ tool çağır. Sən heç nə "icra edə" bilməzsən, heç nə "yarada" bilməzsən — yalnız tool-lar vasitəsilə CRM-də əməliyyat aparırsan.

HƏR mesaja cavab verərkən MÜTLƏQ tool çağır. Heç vaxt "davam edim?", "icra edirəm", "gözləyin" kimi sözlər yazma. Sual vermə, birbaşa tool çağır.

QAYDALAR (prioritet sırası ilə):
1. MƏRHƏLƏ dəyişikliyi: "keçir", "keç", "mərhələyə keçir", "mərhələsinə", "etapa keçir", "statusu dəyiş" → change_stage. Bu ən yüksək prioritetdir!
2. TAPŞIRIĞ TAMAMLAMA: istifadəçi müştəri ilə əlaqə saxladığını bildirirə ("əlaqə saxladım", "zəng etdim", "yazdım", "görüşdüm", "danışdım", "cavab verdim", "məlumat verdim", "iş gördüm", "quraşdırdım", "təqdimat keçirdim") → complete_task
3. QEYD: yalnız açıq-aşkar "qeyd yaz", "qeyd et" deyildikdə → add_note
4. TAPŞIRIQ YARATMA: yeni iş/tapşırıq/əməliyyat lazımdırsa ("açılmalıdır", "etmək lazımdır", "tapşırıq") → create_task
5. Əgər telefon nömrəsi verilməyibsə, söhbət tarixçəsindən istifadə et və ya istifadəçidən soruş.
6. "Hə" və ya "bəli" cavabı — əvvəlki kontekstdən tool-u təkrar çağır.

Komanda: Admin (Texniki Destek), Rüfət Həsənzadə (satış), Soltan Abbasov (texnik).
Bugünkü tarix: {current_date}
Mesaj göndərən: {sender_name}"""

# ─── AI Tool Execution ───────────────────────────────────────────────────────
def execute_tool_search_contact(phone: str) -> str:
    contacts = search_contact_by_phone(phone)
    if not contacts:
        return f"❌ '{phone}' nömrəli müştəri tapılmadı."
    results = []
    for contact in contacts[:3]:
        full_contact = get_contact_details(contact["id"])
        if not full_contact:
            full_contact = contact
        notes = get_contact_notes(contact["id"])
        tasks = get_entity_tasks(contact["id"], "contacts")
        leads = full_contact.get("_embedded", {}).get("leads", [])
        for lead in leads:
            lead_tasks = get_entity_tasks(lead["id"], "leads")
            tasks.extend(lead_tasks)
        results.append(format_contact_info(full_contact, notes, tasks))
    return "\n\n".join(results)

def create_lead_for_contact(contact_id: int, contact_name: str, pipeline_id: int = None, status_id: int = None) -> int | None:
    """Create a deal linked to a contact in the requested pipeline."""
    payload = {
        "name": contact_name or str(contact_id),
        "_embedded": {"contacts": [{"id": int(contact_id)}]},
        "pipeline_id": int(pipeline_id or PIPELINE_ID),
    }
    if status_id:
        payload["status_id"] = int(status_id)
    headers = {
        "Authorization": f"Bearer {KOMMO_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        response = _http.post(
            f"{KOMMO_BASE_URL}/api/v4/leads",
            headers=headers,
            json=[payload],
            timeout=8,
        )
        if response.status_code in (200, 201):
            return response.json().get("_embedded", {}).get("leads", [{}])[0].get("id")
        logger.error(
            "Create lead for contact %s failed: status=%s body=%s",
            contact_id,
            response.status_code,
            response.text[:500],
        )
    except Exception as exc:
        logger.error("Create lead for contact %s error: %s", contact_id, exc)
    return None


def execute_tool_create_task(phone: str, text: str, date: str = None, time_str: str = None, assign_to: str = None, client_name: str = "", chat_id: int = None, assignee_name: str = "") -> dict:
    """Resolve the task entity, creating or selecting a deal in the icraçı funnel."""
    contacts = search_contact_by_phone(phone)
    if not contacts:
        contact_display_name = client_name or phone
        result = create_contact_kommo(contact_display_name, phone)
        if not result:
            return {"success": False, "message": f"❌ '{phone}' kontakt yaradıla bilmədi."}
        contact_data = result.get("_embedded", {}).get("contacts", [{}])[0]
        contact_id = contact_data.get("id")
        if not contact_id:
            return {"success": False, "message": f"❌ '{phone}' kontakt yaradıla bilmədi."}
        contacts = [{"id": contact_id, "name": contact_data.get("name") or phone}]
        logger.info("Auto-created contact %s for phone %s", contact_id, phone)

    contact = contacts[0]
    contact_id = int(contact["id"])
    contact_name = contact.get("name") or "Adsız"
    full_contact = get_contact_details(contact_id)
    if full_contact:
        contact_name = full_contact.get("name") or contact_name
    # Update contact name in Kommo if client_name provided
    if client_name:
        update_contact_kommo(contact_id, {"name": client_name})
        contact_name = client_name
    leads = (full_contact or {}).get("_embedded", {}).get("leads", [])
    route = route_deal_for_employee(assignee_name) if assignee_name else None
    if route:
        allowed_pipeline, entry_status = route
    else:
        allowed_pipeline = get_pipeline_id_for_chat(chat_id)
        entry_status = None
    lead_id = leads[0].get("id") if leads else None
    if not lead_id:
        lead_id = create_lead_for_contact(contact_id, contact_name, allowed_pipeline, entry_status)
        if not lead_id:
            return {"success": False, "message": "❌ Müştəri üçün sövdələşmə yaradıla bilmədi."}
        logger.info("Auto-created lead %s for contact %s", lead_id, contact_id)
    elif assignee_name:
        move_lead_to_icraci(lead_id, assignee_name)

    entity_id = int(lead_id)
    entity_type = "leads"
    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
    assignee_map = {"rufat": 10932455, "soltan": 15531960, "huseyn": 10932455, "rasim": 10932455, "texniki": 10932455, "admin": 10932455, "sahe_meneceri": 10932455}
    assignee_id = assignee_map.get(assign_to, 10932455)
    display_assignee = assignee_name or KOMMO_USERS.get(assignee_id, "Admin")
    return {
        "success": True, "needs_deadline": True,
        "contact_id": contact_id, "contact_name": contact_name,
        "entity_id": entity_id, "entity_type": entity_type,
        "link": link, "task_text": text,
        "assignee_id": assignee_id, "assignee_name": display_assignee,
        "phone": phone, "date": date, "time": time_str,
        "creator_chat_id": chat_id,
        "creator_name": get_employee_name_by_chat_id(chat_id, "") if chat_id else ""
    }

def execute_tool_add_note(phone: str, text: str) -> str:
    contacts = search_contact_by_phone(phone)
    if not contacts:
        return f"❌ '{phone}' nömrəli müştəri tapılmadı."
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    contact_id = contact.get("id")
    # Get phone from contact
    contact_phone = phone
    for cf in (contact.get("custom_fields_values") or []):
        if cf.get("field_code") == "PHONE":
            vals = cf.get("values", [])
            if vals:
                contact_phone = vals[0].get("value", phone)
                break
    # Get lead link if available
    link = f"{KOMMO_BASE_URL}/contacts/detail/{contact_id}"
    full_c = get_contact_details(contact_id)
    if full_c:
        leads = full_c.get("_embedded", {}).get("leads", [])
        if leads:
            lead_id = leads[0]["id"]
            link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
    result = add_note(contact_id, text, "contacts")
    if result:
        return f"✅ Qeyd əlavə edildi!\n👤 {contact_name}\n📞 {contact_phone}\n📝 {text}\n🔗 {link}"
    return "❌ Qeyd əlavə edilərkən xəta baş verdi."

def execute_tool_change_stage(phone: str, stage: str, chat_id: int) -> dict:
    """Return a stage change only when the deal belongs to the caller's pipeline."""
    contacts = search_contact_by_phone(phone)
    if not contacts:
        return {"success": False, "message": f"❌ '{phone}' nömrəli müştəri tapılmadı."}
    contact = contacts[0]
    full_c = get_contact_details(contact["id"])
    leads = (full_c or {}).get("_embedded", {}).get("leads", [])
    allowed_pipeline = get_pipeline_id_for_chat(chat_id)
    if is_funnel_chat(chat_id):
        leads = [lead for lead in leads if int(lead.get("pipeline_id", 0) or 0) == allowed_pipeline]
    if not leads:
        owner = get_funnel_owner(chat_id)
        owner_name = (owner or {}).get("name") or "öz"
        return {"success": False, "message": f"❌ Bu müştərinin {owner_name} vоронкаsında sövdələşməsi tapılmadı." if owner else "❌ Müştərinin sövdələşməsi tapılmadı."}
    lead_id = leads[0]["id"]
    stage_map, _ = get_pipeline_stages_for_chat(chat_id)
    status_id = stage_map.get(stage)
    if not status_id:
        return {"success": False, "message": f"❌ Naməlum mərhələ: {stage}"}
    return {
        "success": True, "needs_confirmation": not is_admin(chat_id) and not is_funnel_chat(chat_id),
        "lead_id": lead_id, "status_id": status_id, "stage": stage,
        "contact_name": contact.get("name", "Adsız"), "phone": phone
    }

def execute_tool_complete_task(phone: str) -> str:
    global _last_completed_task_creator_chat_id
    _last_completed_task_creator_chat_id = None
    contacts = search_contact_by_phone(phone)
    if not contacts:
        return f"❌ '{phone}' nömrəli müştəri tapılmadı."
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    contact_phone = phone
    # Get actual phone from contact
    for cf in (contact.get("custom_fields_values") or []):
        if cf.get("field_code") == "PHONE":
            vals = cf.get("values", [])
            if vals:
                contact_phone = vals[0].get("value", phone)
    # Find open tasks for this contact
    tasks = get_entity_tasks(contact["id"], "contacts")
    full_c = get_contact_details(contact["id"])
    leads = (full_c or {}).get("_embedded", {}).get("leads", [])
    lead_id = leads[0]["id"] if leads else None
    for lead in leads:
        tasks.extend(get_entity_tasks(lead["id"], "leads"))
    open_tasks = [t for t in tasks if not t.get("is_completed")]
    if not open_tasks:
        return f"⚠️ {contact_name} üçün açıq tapşırıq tapılmadı."
    # Complete the most recent task (closest deadline)
    open_tasks.sort(key=lambda t: t.get("complete_till", 0))
    task = open_tasks[0]
    # Tasks created by Rüfət report completion to Rüfət instead of Nizami.
    _creator_is_samil = task_created_by_rufat(task.get("id"))
    deadline_ts = task.get("complete_till", 0)
    deadline_str = datetime.fromtimestamp(deadline_ts, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M") if deadline_ts else ""
    responsible_id = task.get("responsible_user_id", 0)
    responsible_name = KOMMO_USERS.get(responsible_id, "")
    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}" if lead_id else ""
    res = update_task_kommo(task["id"], {"is_completed": True, "result": {"text": "Tamamlandı"}})
    if res:
        if _creator_is_samil:
            _last_completed_task_creator_chat_id = RUFAT_CHAT_ID
        result = (f"✅ Tapşırıq tamamlandı!\n\n"
                  f"👤 {contact_name}\n"
                  f"📞 {contact_phone}\n"
                  f"📝 {task.get('text', '')}\n"
                  f"⏰ Son tarix: {deadline_str}\n"
                  f"👤 Məsul: {responsible_name}")
        if link:
            result += f"\n🔗 {link}"
        return result
    return "❌ Tapşırıq tamamlanarkən xəta baş verdi."

def execute_tool_get_tasks(period: str, phone: str = None) -> str:
    now = datetime.now(tz=BAKU_TZ)
    if phone:
        contacts = search_contact_by_phone(phone)
        if not contacts:
            return f"❌ '{phone}' nömrəli müştəri tapılmadı."
        contact = contacts[0]
        tasks = get_entity_tasks(contact["id"], "contacts")
        full_c = get_contact_details(contact["id"])
        leads = (full_c or {}).get("_embedded", {}).get("leads", [])
        for lead in leads:
            tasks.extend(get_entity_tasks(lead["id"], "leads"))
        if not tasks:
            return f"✨ {contact.get('name', 'Müştəri')} üçün açıq tapşırıq yoxdur."
        msg = f"📋 *{contact.get('name', 'Müştəri')}* — açıq tapşırıqlar:\n\n"
        for i, t in enumerate(tasks, 1):
            dt = datetime.fromtimestamp(t.get("complete_till", 0), tz=BAKU_TZ)
            responsible = KOMMO_USERS.get(t.get("responsible_user_id"), "")
            msg += f"{i}. ⏰ {dt.strftime('%d.%m %H:%M')} — {t.get('text', '')}\n   👤 {responsible}\n"
        return msg
    # Period-based
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0)
        end = now.replace(hour=23, minute=59, second=59)
    elif period == "tomorrow":
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        end = (now + timedelta(days=1)).replace(hour=23, minute=59, second=59)
    else:
        tasks = get_all_incomplete_tasks()
        if not tasks:
            return "✨ Açıq tapşırıq yoxdur!"
        msg = f"📋 *Bütün açıq tapşırıqlar ({len(tasks)}):*\n\n"
        for i, t in enumerate(tasks[:20], 1):
            dt = datetime.fromtimestamp(t.get("complete_till", 0), tz=BAKU_TZ)
            responsible = KOMMO_USERS.get(t.get("responsible_user_id"), "")
            msg += f"{i}. ⏰ {dt.strftime('%d.%m %H:%M')} — {t.get('text', '')[:50]}\n   👤 {responsible}\n"
        return msg
    tasks = get_tasks(start, end)
    if not tasks:
        period_name = "bugün" if period == "today" else "sabah"
        return f"✨ {period_name.capitalize()} üçün tapşırıq yoxdur!"
    period_name = "Bugünkü" if period == "today" else "Sabahkı"
    msg = f"📋 *{period_name} tapşırıqlar ({len(tasks)}):*\n\n"
    for i, t in enumerate(tasks, 1):
        dt = datetime.fromtimestamp(t.get("complete_till", 0), tz=BAKU_TZ)
        responsible = KOMMO_USERS.get(t.get("responsible_user_id"), "")
        entity_id = t.get("entity_id")
        entity_type = t.get("entity_type", "leads")
        client_name = get_contact_name_from_entity(entity_id, entity_type) if entity_id else ""
        name_line = f" ({client_name})" if client_name else ""
        msg += f"{i}. ⏰ {dt.strftime('%H:%M')} — {t.get('text', '')[:50]}{name_line}\n   👤 {responsible}\n"
    return msg

def execute_tool_get_lead_info(phone: str) -> str:
    contacts = search_contact_by_phone(phone)
    if not contacts:
        return f"❌ '{phone}' nömrəli müştəri tapılmadı."
    contact = contacts[0]
    full_c = get_contact_details(contact["id"])
    if not full_c:
        return "❌ Kontakt məlumatı alınmadı."
    notes = get_contact_notes(contact["id"])
    tasks = get_entity_tasks(contact["id"], "contacts")
    leads = (full_c or {}).get("_embedded", {}).get("leads", [])
    for lead in leads:
        tasks.extend(get_entity_tasks(lead["id"], "leads"))
    return format_contact_info(full_c, notes, tasks)

# ─── Pending task creation (for deadline buttons) ───────────────────────────
_pending_tasks: dict = {}  # key -> task info
_pending_actions: dict = {}  # key -> {"action": ..., "args": ..., "chat_id": ..., "summary": ...}

# ─── AI Message Processing ───────────────────────────────────────────────────
async def process_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    """Process a text message through OpenAI function calling."""
    chat_id = update.message.chat_id
    sender_kommo_id = get_kommo_user_id_for_chat(chat_id)
    sender_name = KOMMO_USERS.get(sender_kommo_id, "İstifadəçi") if sender_kommo_id else "İstifadəçi"
    now = datetime.now(tz=BAKU_TZ)
    
    # Build messages
    system_prompt = AI_SYSTEM_PROMPT.format(
        current_date=now.strftime("%d.%m.%Y %H:%M (%A)"),
        sender_name=sender_name
    )
    messages = [{"role": "system", "content": system_prompt}]
    # Add conversation history
    history = get_history(chat_id)
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    
    try:
        response = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=AI_TOOLS,
            tool_choice="required",
            timeout=60,
        )
        msg = response.choices[0].message
        
        # If GPT wants to call a tool
        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)
            logger.info(f"AI tool call: {fn_name}({fn_args}) from {sender_name}")
            
            # For actions that modify CRM, show confirmation first
            needs_confirm = fn_name in ("add_note", "complete_task", "change_stage", "create_task")
            if needs_confirm:
                # Build a human-readable summary of what bot wants to do
                summary = _build_action_summary(fn_name, fn_args)
                action_key = str(uuid.uuid4())[:8]
                _pending_actions[action_key] = {
                    "action": fn_name,
                    "args": fn_args,
                    "chat_id": chat_id,
                    "summary": summary,
                    "user_text": user_text,
                }
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Təsdiq et", callback_data=f"actconf_{action_key}_yes"),
                        InlineKeyboardButton("❌ Ləğv et", callback_data=f"actconf_{action_key}_no"),
                    ]
                ]
                await update.message.reply_text(
                    f"🤖 {summary}\n\nTəsdiq edirsiniz?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                add_to_history(chat_id, "user", user_text)
                return
            
            # Non-modifying tools (search, get_tasks, get_lead_info) - execute immediately
            result_text = await execute_ai_tool(fn_name, fn_args, chat_id, update, context)
            
            if result_text:
                # Send result back to GPT for final response
                messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": tool_call.id, "type": "function", "function": {"name": fn_name, "arguments": tool_call.function.arguments}}]})
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_text})
                
                response2 = llm_client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    timeout=30,
                )
                final_text = response2.choices[0].message.content
                if final_text:
                    try:
                        await update.message.reply_text(final_text, parse_mode="Markdown", disable_web_page_preview=True)
                    except:
                        await update.message.reply_text(final_text, disable_web_page_preview=True)
                    add_to_history(chat_id, "user", user_text)
                    add_to_history(chat_id, "assistant", final_text)
        else:
            # No tool call even with required - ask user to be more specific
            final_text = msg.content if msg.content else None
            if final_text:
                try:
                    await update.message.reply_text(final_text, parse_mode="Markdown", disable_web_page_preview=True)
                except:
                    await update.message.reply_text(final_text, disable_web_page_preview=True)
            else:
                await update.message.reply_text("Anlamadım. Zəhmət olmasa müştərinin telefon nömrəsini və nə etmək istədiyinizi yazın.")
            add_to_history(chat_id, "user", user_text)
            if final_text:
                add_to_history(chat_id, "assistant", final_text)
    except Exception as e:
        logger.error(f"AI processing error: {e}\n{traceback.format_exc()}")
        await update.message.reply_text("⚠️ AI xətası baş verdi. Yenidən cəhd edin.")

async def execute_ai_tool(fn_name: str, fn_args: dict, chat_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Execute an AI tool and return result string. May send buttons directly."""
    if fn_name == "search_contact":
        return execute_tool_search_contact(fn_args["phone"])
    
    elif fn_name == "create_task":
        result = execute_tool_create_task(
            fn_args["phone"], fn_args["text"],
            fn_args.get("date"), fn_args.get("time"),
            fn_args.get("assign_to")
        )
        if isinstance(result, str):
            return result
        if not result["success"]:
            return result["message"]
        # If date and time provided, create immediately
        if result.get("date") and result.get("time"):
            try:
                dt = datetime.strptime(f"{result['date']} {result['time']}", "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ)
                complete_till = int(dt.timestamp())
                res = create_task(result["entity_id"], result["task_text"], complete_till,
                                  responsible_user_id=result["assignee_id"], entity_type=result["entity_type"],
                                  creator_name=result.get("creator_name", ""))
                if res:
                    msg = (f"✅ Tapşırıq yaradıldı!\n\n👤 {result['contact_name']}\n📞 {result['phone']}\n"
                           f"📝 {result['task_text']}\n⏰ {result['date']} {result['time']}\n"
                           f"👤 Məsul: {result['assignee_name']}\n🔗 {result['link']}")
                    # Notify assignee if not admin
                    if result["assignee_id"] != 10932455:
                        assignee_chat = get_chat_id_for_kommo_user(result["assignee_id"])
                        if assignee_chat:
                            try:
                                await context.bot.send_message(
                                    assignee_chat,
                                    f"📢 Yeni tapşırıq:\n\n👤 {result['contact_name']}\n📞 {result['phone']}\n"
                                    f"📝 {result['task_text']}\n⏰ {result['date']} {result['time']}\n🔗 {result['link']}",
                                    disable_web_page_preview=True
                                )
                                send_push_notification(str(assignee_chat), '📢 Yeni tapşırıq!', f"{result['contact_name']} - {result['task_text']}")
                            except:
                                pass
                    return msg
                return "❌ Tapşırıq yaradılarkən xəta."
            except ValueError:
                pass
        # No deadline - show deadline buttons
        task_key = str(uuid.uuid4())[:8]
        _pending_tasks[task_key] = result
        keyboard = [
            [
                InlineKeyboardButton("15 dəq", callback_data=f"taskdl_{task_key}_15m"),
                InlineKeyboardButton("1 saat", callback_data=f"taskdl_{task_key}_1h"),
            ],
            [
                InlineKeyboardButton("Bu gün", callback_data=f"taskdl_{task_key}_today"),
                InlineKeyboardButton("Sabah", callback_data=f"taskdl_{task_key}_tomorrow"),
            ],
            [InlineKeyboardButton("Bu həftə", callback_data=f"taskdl_{task_key}_week")],
        ]
        await update.message.reply_text(
            f"📋 Tapşırıq: {result['task_text']}\n👤 {result['contact_name']} ({result['phone']})\n👤 Məsul: {result['assignee_name']}\n\n⏰ Son tarix seçin:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return None  # Don't send back to GPT
    
    elif fn_name == "add_note":
        return execute_tool_add_note(fn_args["phone"], fn_args["text"])
    
    elif fn_name == "change_stage":
        result = execute_tool_change_stage(fn_args["phone"], fn_args["stage"], chat_id)
        if not result["success"]:
            return result["message"]
        if result.get("needs_confirmation"):
            # Non-admin: send to admin for confirmation
            conf_key = str(uuid.uuid4())[:8]
            context.bot_data[f"confirm_{conf_key}"] = {
                "phone": result["phone"], "stage": result["stage"],
                "lead_id": result["lead_id"], "status_id": result["status_id"],
                "sender_chat_id": chat_id, "sender_kommo_id": get_kommo_user_id_for_chat(chat_id)
            }
            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
            stage_display = STAGE_NAMES.get(result["status_id"], result["stage"])
            admin_chat = get_chat_id_for_kommo_user(10932455)
            sent = None
            if admin_chat:
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Təsdiq et", callback_data=f"conftr_{conf_key}_yes"),
                        InlineKeyboardButton("❌ Rədd et", callback_data=f"conftr_{conf_key}_no"),
                    ]
                ]
                try:
                    sent = await context.bot.send_message(
                        admin_chat,
                        f"🔄 *{sender_name}* mərhələ dəyişikliyi istəyir:\n\n"
                        f"👤 {result['contact_name']}\n📞 {result['phone']}\n"
                        f"📌 Yeni mərhələ: *{stage_display}*\n\n"
                        f"Təsdiq edirsiniz?",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception as e:
                    logger.error(f"Failed to send confirmation: {e}")
            save_pending_action("confirm_stage", {
                "contact_name": result["contact_name"],
                "phone": result["phone"],
                "lead_id": result["lead_id"],
                "status_id": result["status_id"],
                "stage_name": stage_display,
                "stage_key": result["stage"],
                "sender_name": sender_name,
                "sender_chat_id": chat_id,
                "conf_key": conf_key,
                "link": f"{KOMMO_BASE_URL}/leads/detail/{result['lead_id']}",
                "telegram_chat_id": admin_chat,
                "telegram_message_id": sent.message_id if sent else None,
            }, ["Təsdiq et", "Rədd et"])
            send_push_to_admin(
                f"{sender_name}: {result['contact_name']} → {stage_display}",
                title="🔄 Mərhələ təsdiqi",
                url="#pending",
            )
            return f"⏳ Sorğunuz Admin-ə göndərildi. Təsdiq gözlənilir.\n👤 {result['contact_name']} → {stage_display}"
        else:
            # Admin: execute immediately
            update_lead_kommo(result["lead_id"], {"status_id": result["status_id"], "pipeline_id": get_pipeline_id_for_chat(chat_id)})
            stage_display = STAGE_NAMES.get(result["status_id"], result["stage"])
            link = f"{KOMMO_BASE_URL}/leads/detail/{result['lead_id']}"
            return f"✅ Mərhələ dəyişdirildi!\n👤 {result['contact_name']}\n📌 {stage_display}\n🔗 {link}"
    
    elif fn_name == "complete_task":
        return execute_tool_complete_task(fn_args["phone"])
    
    elif fn_name == "get_tasks":
        return execute_tool_get_tasks(fn_args["period"], fn_args.get("phone"))
    
    elif fn_name == "get_lead_info":
        return execute_tool_get_lead_info(fn_args["phone"])
    
    return "⚠️ Naməlum əməliyyat."

# ─── Action Summary Builder ────────────────────────────────────────────────
def _build_action_summary(fn_name: str, fn_args: dict) -> str:
    """Build a human-readable summary of what the bot wants to do. Always includes client name."""
    phone = fn_args.get('phone', '')
    # Look up client name for all actions
    contact_name = ""
    contact_obj = None
    try:
        contacts = search_contact_by_phone(phone)
        if contacts:
            contact_obj = contacts[0]
            contact_name = contact_obj.get("name", "Adsız")
    except:
        pass
    client_line = f"👤 {contact_name}\n📞 {phone}" if contact_name else f"📞 {phone}"

    if fn_name == "add_note":
        return f"📝 Qeyd əlavə edəcəm:\n\n{client_line}\n💬 {fn_args.get('text', '')}"
    elif fn_name == "complete_task":
        # Find the actual task details
        if contact_obj:
            try:
                tasks = get_entity_tasks(contact_obj["id"], "contacts")
                full_c = get_contact_details(contact_obj["id"])
                leads = (full_c or {}).get("_embedded", {}).get("leads", [])
                for lead in leads:
                    tasks.extend(get_entity_tasks(lead["id"], "leads"))
                open_tasks = [t for t in tasks if not t.get("is_completed")]
                if open_tasks:
                    open_tasks.sort(key=lambda t: t.get("complete_till", 0))
                    task = open_tasks[0]
                    deadline_ts = task.get("complete_till", 0)
                    deadline_str = datetime.fromtimestamp(deadline_ts, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M") if deadline_ts else ""
                    responsible_name = KOMMO_USERS.get(task.get("responsible_user_id", 0), "")
                    return (f"✅ Tapşırığı tamamlayacam:\n\n"
                            f"{client_line}\n"
                            f"📝 {task.get('text', '')}\n"
                            f"⏰ Son tarix: {deadline_str}\n"
                            f"👤 Məsul: {responsible_name}")
                else:
                    return f"⚠️ {contact_name} üçün açıq tapşırıq tapılmadı."
            except:
                pass
        return f"✅ Tapşırığı tamamlayacam:\n\n{client_line}"
    elif fn_name == "create_task":
        assign_names = {"rufat": "Rüfət Həsənzadə", "soltan": "Soltan Abbasov", "admin": "Admin"}
        assignee = assign_names.get(fn_args.get('assign_to', ''), 'Admin')
        return (f"📋 Tapşırıq yaradacam:\n\n"
                f"{client_line}\n"
                f"📝 {fn_args.get('text', '')}\n"
                f"👤 Məsul: {assignee}")
    elif fn_name == "change_stage":
        stage_display = STAGE_NAMES.get(STAGES.get(fn_args.get('stage', ''), 0), fn_args.get('stage', ''))
        # Get current stage
        current_stage = ""
        if contact_obj:
            try:
                full_c = get_contact_details(contact_obj["id"])
                leads = (full_c or {}).get("_embedded", {}).get("leads", [])
                if leads:
                    lead = get_lead_details(leads[0]["id"])
                    if lead:
                        current_stage = STAGE_NAMES.get(lead.get("status_id", 0), "")
            except:
                pass
        stage_info = f"📌 {current_stage} → {stage_display}" if current_stage else f"📌 Yeni mərhələ: {stage_display}"
        return f"🔄 Mərhələ dəyişəcəm:\n\n{client_line}\n{stage_info}"
    return f"⚙️ Əməliyyat: {fn_name}"

# ─── Action Confirmation Callback ─────────────────────────────────────────────
async def action_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirmation/rejection of AI-proposed actions."""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # actconf_{key}_{yes/no}
    parts = data.split("_")
    if len(parts) < 3:
        return
    action_key = parts[1]
    decision = parts[2]
    pending = _pending_actions.pop(action_key, None)
    if not pending:
        try:
            await query.edit_message_text("⚠️ Vaxt keçib, yenidən cəhd edin.")
        except:
            pass
        return
    if decision == "no":
        try:
            await query.edit_message_text("❌ Ləğv edildi.")
        except:
            pass
        return
    # Execute the confirmed action
    fn_name = pending["action"]
    chat_id = pending["chat_id"]
    
    # Special case: reply-to-task completion
    if fn_name == "_complete_task_reply":
        task_id = pending["task_id"]
        task_info = pending["task_info"]
        user_text = pending["user_text"]
        try:
            res = update_task_kommo(task_id, {"is_completed": True, "result": {"text": user_text}})
            if res:
                entity_id = task_info.get("entity_id")
                entity_type = task_info.get("entity_type", "leads")
                if entity_id:
                    add_note(entity_id, user_text, entity_type)
                result_msg = f"✅ Tapşırıq tamamlandı!\n📝 {task_info.get('task_text', '')}\n💬 {user_text}"
                try:
                    await query.edit_message_text(result_msg)
                except:
                    pass
                # Notify the task creator for Rüfət-created tasks; otherwise
                # preserve the existing Nizami notification behavior.
                _creator_is_rufat_reply = task_created_by_rufat(task_id)
                _creator_chat_reply = RUFAT_CHAT_ID if _creator_is_rufat_reply else None
                admin_chat = _creator_chat_reply or get_chat_id_for_kommo_user(10932455)
                sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
                task_phone = task_info.get("phone", "")
                # Get contact name from entity
                task_entity_id = task_info.get("entity_id")
                task_entity_type = task_info.get("entity_type", "leads")
                contact_name_for_notif = ""
                if task_entity_id:
                    contact_name_for_notif = get_contact_name_from_entity(task_entity_id, task_entity_type) or ""
                    if not task_phone:
                        task_phone = get_phone_from_entity(task_entity_id, task_entity_type) or ""
                link_for_notif = f"{KOMMO_BASE_URL}/leads/detail/{task_entity_id}" if task_entity_type == "leads" and task_entity_id else ""
                if admin_chat and admin_chat != chat_id:
                    try:
                        _recipient_label = "tapşırıq yaradıcısı" if _creator_is_rufat_reply else "Admin"
                        notif_text = (f"✅ *{sender_name}* tapşırığı tamamladı ({_recipient_label} üçün):\n\n"
                                      f"👤 {contact_name_for_notif}\n📞 {task_phone}\n"
                                      f"📝 {task_info.get('task_text', '')}\n💬 {user_text}")
                        if link_for_notif:
                            notif_text += f"\n🔗 {link_for_notif}"
                        sent_notif = await context.bot.send_message(
                            admin_chat, notif_text,
                            parse_mode="Markdown", disable_web_page_preview=True
                        )
                        if sent_notif and task_entity_id:
                            store_message_lead(admin_chat, sent_notif.message_id, task_entity_id, contact_name_for_notif, task_phone)
                    except:
                        pass
            else:
                try:
                    await query.edit_message_text("❌ Xəta baş verdi.")
                except:
                    pass
        except Exception as e:
            logger.error(f"Task reply confirm error: {e}")
            try:
                await query.edit_message_text("⚠️ Xəta baş verdi.")
            except:
                pass
        return
    
    # Special case: create_task — show assignee selection buttons
    if fn_name == "create_task":
        fn_args = pending["args"]
        # Prepare task result for the multi-step flow
        result = execute_tool_create_task(
            fn_args["phone"], fn_args["text"],
            fn_args.get("date"), fn_args.get("time"),
            fn_args.get("assign_to")
        )
        if isinstance(result, str) or not result.get("success"):
            err_msg = result if isinstance(result, str) else result.get("message", "Xəta")
            try:
                await query.edit_message_text(err_msg)
            except:
                pass
            return
        # Store for assignee selection
        task_key = str(uuid.uuid4())[:8]
        _pending_tasks[f"ai_{task_key}"] = result
        keyboard = [
            [
                InlineKeyboardButton("Rüfət Həsənzadə", callback_data=f"aitask_{task_key}_rufat"),
                InlineKeyboardButton("Soltan Abbasov", callback_data=f"aitask_{task_key}_soltan"),
            ],
            [InlineKeyboardButton("Admin", callback_data=f"aitask_{task_key}_admin")],
        ]
        try:
            await query.edit_message_text(
                f"✅ Təsdiqləndi!\n\n"
                f"👤 {result['contact_name']}\n📞 {result['phone']}\n"
                f"📝 {result['task_text']}\n\n"
                f"👤 İcraçını seçin:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            pass
        return
    
    # Regular AI tool actions
    fn_args = pending["args"]
    try:
        result_text = await execute_ai_tool(fn_name, fn_args, chat_id, update, context)
        if result_text:
            try:
                await query.edit_message_text(result_text, parse_mode="Markdown", disable_web_page_preview=True)
            except:
                try:
                    await query.edit_message_text(result_text, disable_web_page_preview=True)
                except:
                    pass
            add_to_history(chat_id, "assistant", result_text)
            # Notify Nizami about confirmed actions, except completion of a
            # Rüfət-created task, which is routed to Rüfət.
            admin_chat = get_chat_id_for_kommo_user(10932455)
            notification_chat = (
                _last_completed_task_creator_chat_id
                if fn_name == "complete_task" and _last_completed_task_creator_chat_id
                else admin_chat
            )
            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
            if notification_chat and notification_chat != chat_id:
                action_labels = {"complete_task": "✅ tamamladı", "add_note": "📝 qeyd əlavə etdi", "change_stage": "🔄 mərhələ dəyişdi", "create_task": "📋 tapşırıq yaratdı"}
                action_label = action_labels.get(fn_name, fn_name)
                # Get phone from args for reply context
                notif_phone = fn_args.get("phone", "")
                try:
                    sent_admin = await context.bot.send_message(
                        notification_chat,
                        f"📢 *{sender_name}* {action_label}:\n{result_text[:500]}",
                        parse_mode="Markdown", disable_web_page_preview=True
                    )
                    # Store for reply context so admin can reply to this notification
                    if sent_admin and notif_phone:
                        contacts = search_contact_by_phone(notif_phone)
                        if contacts:
                            c = contacts[0]
                            full_c = get_contact_details(c["id"])
                            leads = (full_c or {}).get("_embedded", {}).get("leads", [])
                            if leads:
                                store_message_lead(notification_chat, sent_admin.message_id, leads[0]["id"], c.get("name", ""), notif_phone)
                except:
                    pass
    except Exception as e:
        logger.error(f"Action confirm execution error: {e}")
        try:
            await query.edit_message_text("⚠️ Xəta baş verdi.")
        except:
            pass

# ─── AI Task Assignee Callback ──────────────────────────────────────────────
async def ai_task_assign_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle assignee selection for AI-created tasks. Callback: aitask_{key}_{assignee}"""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # aitask_{key}_{assignee}
    parts = data.split("_")
    if len(parts) < 3:
        return
    task_key = parts[1]
    assignee_key = parts[2]
    pending_key = f"ai_{task_key}"
    task_data = _pending_tasks.get(pending_key)
    if not task_data:
        try:
            await query.edit_message_text("⚠️ Vaxt keçib, yenidən cəhd edin.")
        except:
            pass
        return
    # Update assignee
    assignee_map = {"rufat": (10932455, "Admin"), "soltan": (15531960, "Soltan Abbasov"), "admin": (10932455, "Admin"), "sahe_meneceri": (10932455, "Admin")}
    assignee_uid, assignee_name = assignee_map.get(assignee_key, (10932455, "Admin"))
    task_data["assignee_id"] = assignee_uid
    task_data["assignee_name"] = assignee_name
    _pending_tasks[pending_key] = task_data
    # Show deadline buttons
    keyboard = [
        [
            InlineKeyboardButton("15 dəq", callback_data=f"aitaskdl_{task_key}_15m"),
            InlineKeyboardButton("1 saat", callback_data=f"aitaskdl_{task_key}_1h"),
        ],
        [
            InlineKeyboardButton("Bu gün", callback_data=f"aitaskdl_{task_key}_today"),
            InlineKeyboardButton("Sabah", callback_data=f"aitaskdl_{task_key}_tomorrow"),
        ],
        [InlineKeyboardButton("Bu həftə", callback_data=f"aitaskdl_{task_key}_week")],
    ]
    try:
        await query.edit_message_text(
            f"✅ *{assignee_name}* seçildi.\n\n"
            f"👤 {task_data['contact_name']}\n📞 {task_data['phone']}\n"
            f"📝 {task_data['task_text']}\n\n⏰ Son tarix seçin:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except:
        pass

async def ai_task_deadline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deadline selection for AI-created tasks. Callback: aitaskdl_{key}_{deadline}"""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # aitaskdl_{key}_{deadline}
    parts = data.split("_")
    if len(parts) < 3:
        return
    task_key = parts[1]
    deadline_key = parts[2]
    pending_key = f"ai_{task_key}"
    task_data = _pending_tasks.pop(pending_key, None)
    if not task_data:
        try:
            await query.edit_message_text("⚠️ Vaxt keçib, yenidən cəhd edin.")
        except:
            pass
        return
    now = datetime.now(tz=BAKU_TZ)
    if deadline_key == "15m":
        deadline_dt = now + timedelta(minutes=15)
    elif deadline_key == "1h":
        deadline_dt = now + timedelta(hours=1)
    elif deadline_key == "today":
        deadline_dt = now.replace(hour=17, minute=0, second=0, microsecond=0)
        if deadline_dt <= now:
            deadline_dt += timedelta(days=1)
    elif deadline_key == "tomorrow":
        deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    elif deadline_key == "week":
        days_until_friday = (4 - now.weekday()) % 7
        if days_until_friday == 0 and now.hour >= 18:
            days_until_friday = 7
        deadline_dt = (now + timedelta(days=days_until_friday)).replace(hour=17, minute=0, second=0, microsecond=0)
    else:
        deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    deadline_ts = int(deadline_dt.timestamp())
    assignee_id = task_data["assignee_id"]
    assignee_name = task_data["assignee_name"]
    entity_id = task_data["entity_id"]
    entity_type = task_data["entity_type"]
    task_text = task_data["task_text"]
    link = task_data.get("link", "")
    contact_name = task_data["contact_name"]
    phone = task_data["phone"]
    _ai_creator = get_employee_name_by_chat_id(query.from_user.id, "")
    result = create_task(entity_id, task_text, deadline_ts, responsible_user_id=assignee_id, entity_type=entity_type, creator_name=_ai_creator)
    if result:
        result_text = (f"✅ Tapşırıq yaradıldı!\n\n"
                       f"👤 {contact_name}\n📞 {phone}\n"
                       f"📝 {task_text}\n"
                       f"⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n"
                       f"👤 Məsul: {assignee_name}\n🔗 {link}")
        # Notify assignee
        if assignee_id != 10932455:
            assignee_chat = get_chat_id_for_kommo_user(assignee_id)
            if assignee_chat:
                try:
                    sent_a = await context.bot.send_message(
                        assignee_chat,
                        f"📋 *Yeni tapşırıq!*\n\n👤 {contact_name}\n📞 {phone}\n"
                        f"📝 {task_text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n🔗 {link}",
                        parse_mode="Markdown", disable_web_page_preview=True
                    )
                    send_push_notification(str(assignee_chat), '📋 Yeni tapşırıq!', f"{contact_name} - {task_text}")
                    tid = result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
                    if tid and sent_a:
                        store_message_task(assignee_chat, sent_a.message_id, int(tid), task_text, entity_id=entity_id, entity_type=entity_type, phone=phone)
                except:
                    pass
        # Notify admin
        admin_chat = get_chat_id_for_kommo_user(10932455)
        sender_chat = query.message.chat_id if query.message else None
        if admin_chat and admin_chat != sender_chat:
            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(sender_chat), "Əməkdaş") if sender_chat else ""
            try:
                await context.bot.send_message(
                    admin_chat,
                    f"📢 *{sender_name}* tapşırıq yaratdı:\n\n👤 {contact_name}\n📞 {phone}\n"
                    f"📝 {task_text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n"
                    f"👤 Məsul: {assignee_name}\n🔗 {link}",
                    parse_mode="Markdown", disable_web_page_preview=True
                )
            except:
                pass
    else:
        result_text = "❌ Tapşırıq yaradılarkən xəta."
    try:
        await query.edit_message_text(result_text, parse_mode="Markdown", disable_web_page_preview=True)
    except:
        try:
            await query.edit_message_text(result_text, disable_web_page_preview=True)
        except:
            pass

# ─── Telegram Handlers ───────────────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    users = load_users()
    if str(chat_id) in users:
        info = users[str(chat_id)]
        await update.message.reply_text(
            f"👋 Salam, {info.get('name', '')}!\n\n"
            f"📱 CRM düyməsinə basaraq Mini App-dan istifadə edin və ya sərbəst mətn yazın.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    keyboard = [
        [InlineKeyboardButton("🤝 Partnyor", callback_data="reg_partnyor")],
        [InlineKeyboardButton("👤 Əməkdaş", callback_data="reg_emekdash")],
    ]
    await update.message.reply_text(
        "👋 Xoş gəlmisiniz! Qeydiyyat növünü seçin:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def role_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    users = load_users()
    info = users.get(str(chat_id))
    if info:
        await update.message.reply_text(f"👤 {info.get('name', 'Adsız')}\n🏷 Rol: {info.get('role', 'Naməlum')}")
    else:
        await update.message.reply_text("⚠️ Qeydiyyatdan keçməmisiniz. /start yazın.")

# ─── Registration Callbacks ──────────────────────────────────────────────────
async def registration_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    chat_id = query.message.chat_id
    data = query.data
    if data == "reg_partnyor":
        _pending_partner_registration[chat_id] = True
        try:
            await query.edit_message_text(
                "🤝 *Partnyor qeydiyyatı*\n\nAdınızı daxil edin (Kommo siyahısında qeyd olunduğu kimi):",
                parse_mode="Markdown"
            )
        except:
            pass
    elif data == "reg_emekdash":
        _pending_employee_registration[chat_id] = "__ask_name__"
        try:
            await query.edit_message_text(
                "👤 *Əməkdaş qeydiyyatı*\n\nAdınızı yazın:",
                parse_mode="Markdown"
            )
        except:
            pass

async def employee_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data
    parts = data.split("_")
    applicant_chat_id = int(parts[1])
    decision = parts[2]
    emp_name = _pending_employee_registration.pop(applicant_chat_id, None)
    if not emp_name or emp_name == "__ask_name__":
        try:
            await query.edit_message_text("⚠️ Məlumat tapılmadı.")
        except:
            pass
        return
    if decision == "yes":
        users = load_users()
        users[str(applicant_chat_id)] = {"role": "Əməkdaş", "name": emp_name}
        save_users(users)
        try:
            await query.edit_message_text(f"✅ {emp_name} əməkdaş kimi qeydiyyatdan keçdi.", parse_mode="Markdown")
        except:
            pass
        try:
            await context.bot.send_message(applicant_chat_id, "✅ Qeydiyyat təsdiqləndi! Mənə mesaj yaza bilərsiniz.")
        except:
            pass
    else:
        try:
            await query.edit_message_text(f"❌ {emp_name} rədd edildi.")
        except:
            pass
        try:
            await context.bot.send_message(applicant_chat_id, "❌ Qeydiyyat rədd edildi.")
        except:
            pass

# ─── Task Deadline Callback ──────────────────────────────────────────────────
async def task_deadline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # taskdl_{key}_{deadline}
    parts = data.split("_")
    if len(parts) < 3:
        return
    task_key = parts[1]
    deadline_key = parts[2]
    pending = _pending_tasks.pop(task_key, None)
    if not pending:
        try:
            await query.edit_message_text("⚠️ Vaxt keçib, yenidən cəhd edin.")
        except:
            pass
        return
    now = datetime.now(tz=BAKU_TZ)
    if deadline_key == "15m":
        deadline_dt = now + timedelta(minutes=15)
    elif deadline_key == "1h":
        deadline_dt = now + timedelta(hours=1)
    elif deadline_key == "today":
        deadline_dt = now.replace(hour=17, minute=0, second=0, microsecond=0)
        if deadline_dt <= now:
            deadline_dt += timedelta(days=1)
    elif deadline_key == "tomorrow":
        deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    elif deadline_key == "week":
        days_until_friday = (4 - now.weekday()) % 7
        if days_until_friday == 0 and now.hour >= 18:
            days_until_friday = 7
        deadline_dt = (now + timedelta(days=days_until_friday)).replace(hour=17, minute=0, second=0, microsecond=0)
    else:
        deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    
    deadline_ts = int(deadline_dt.timestamp())
    result = create_task(pending["entity_id"], pending["task_text"], deadline_ts,
                         responsible_user_id=pending["assignee_id"], entity_type=pending["entity_type"],
                         creator_name=pending.get("creator_name", ""))
    if result:
        msg = (f"✅ Tapşırıq yaradıldı!\n\n👤 {pending['contact_name']}\n📞 {pending['phone']}\n"
               f"📝 {pending['task_text']}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n"
               f"👤 Məsul: {pending['assignee_name']}\n🔗 {pending['link']}")
        try:
            await query.edit_message_text(msg, disable_web_page_preview=True)
        except:
            pass
        # Notify assignee
        if pending["assignee_id"] != 10932455:
            assignee_chat = get_chat_id_for_kommo_user(pending["assignee_id"])
            if assignee_chat:
                try:
                    sent_msg = await context.bot.send_message(
                        assignee_chat,
                        f"📢 Yeni tapşırıq:\n\n👤 {pending['contact_name']}\n📞 {pending['phone']}\n"
                        f"📝 {pending['task_text']}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n🔗 {pending['link']}",
                        disable_web_page_preview=True
                    )
                    send_push_notification(str(assignee_chat), '📢 Yeni tapşırıq!', f"{pending['contact_name']} - {pending['task_text']}")
                    task_id = result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
                    if task_id and sent_msg:
                        store_message_task(assignee_chat, sent_msg.message_id, int(task_id), pending["task_text"],
                                           entity_id=pending["entity_id"], entity_type=pending["entity_type"], phone=pending["phone"])
                except:
                    pass
        # Notify admin about task creation (if creator is not admin)
        chat_id = query.message.chat_id
        if not is_admin(chat_id):
            admin_chat = get_chat_id_for_kommo_user(10932455)
            if admin_chat:
                creator_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
                try:
                    await context.bot.send_message(
                        admin_chat,
                        f"📋 *{creator_name}* tapşırıq yaratdı:\n\n👤 {pending['contact_name']}\n📞 {pending['phone']}\n"
                        f"📝 {pending['task_text']}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n"
                        f"👤 Məsul: {pending['assignee_name']}\n🔗 {pending['link']}",
                        parse_mode="Markdown", disable_web_page_preview=True
                    )
                except:
                    pass
    else:
        try:
            await query.edit_message_text("❌ Tapşırıq yaradılarkən xəta baş verdi.")
        except:
            pass

# ─── Confirm Transition Callback ────────────────────────────────────────────
async def confirm_transition_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # conftr_{key}_{yes/no}
    parts = data.split("_")
    if len(parts) < 3:
        return
    conf_key = parts[1]
    decision = parts[2]
    pending = context.bot_data.pop(f"confirm_{conf_key}", None)
    if not pending:
        try:
            await query.edit_message_text("⚠️ Vaxt keçib.")
        except:
            pass
        return
    sender_chat_id = pending["sender_chat_id"]
    if decision == "yes":
        lead_id = pending["lead_id"]
        status_id = pending["status_id"]
        stage = pending["stage"]
        target_pipeline_id = int(pending.get("pipeline_id") or PIPELINE_ID)
        update_lead_kommo(lead_id, {"status_id": status_id, "pipeline_id": target_pipeline_id})
        stage_display = pending.get("stage_name") or STAGE_NAMES.get(status_id, stage)
        link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
        try:
            await query.edit_message_text(
                f"✅ Təsdiqləndi!\n📌 {stage_display}\n📞 {pending['phone']}\n🔗 {link}",
                disable_web_page_preview=True
            )
        except:
            pass
        try:
            await context.bot.send_message(sender_chat_id, f"✅ Admin sorğunuzu təsdiqlədi! Mərhələ: {stage_display}")
        except:
            pass
        # Auto-create task for new stage if applicable
        if lead_id and stage in _STAGE_TASK_TEXTS:
            _task_text = _STAGE_TASK_TEXTS[stage]
            _now_dt = datetime.now(tz=BAKU_TZ)
            _deadline_ts = int((_now_dt + timedelta(hours=2)).timestamp())
            if stage == "qiymet_teklifi":
                create_task(lead_id, _task_text, _deadline_ts, responsible_user_id=10932455, entity_type="leads")
            else:
                _marker = None
                for _nm, _cid in NAME_TO_CHAT.items():
                    if _cid == sender_chat_id and len(_nm) > 5:
                        _marker = _nm
                        break
                if _marker:
                    create_task(lead_id, f"[{_marker}] {_task_text}", _deadline_ts, responsible_user_id=10932455, entity_type="leads")
                else:
                    create_task(lead_id, _task_text, _deadline_ts, responsible_user_id=10932455, entity_type="leads")
    else:
        try:
            await query.edit_message_text(f"❌ Rədd edildi: {pending['phone']}")
        except:
            pass
        try:
            await context.bot.send_message(sender_chat_id, "❌ Admin sorğunuzu rədd etdi.")
        except:
            pass
    mark_pending_action_resolved(
        action_type="confirm_stage",
        conf_key=conf_key,
        choice="Təsdiq et" if decision == "yes" else "Rədd et",
    )

# ─── Stage Task Assign/Deadline Callbacks (from webhook) ────────────────────
async def stage_task_assign_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle assignee selection for webhook-triggered stage tasks.
    Callback: stgtask-{lead_id}-{stage_key}-{assignee}"""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data
    parts = data.split("-")
    if len(parts) < 4:
        return
    lead_id = int(parts[1])
    stage_key = parts[2]
    assignee_key = parts[3]
    if assignee_key == "cancel":
        try:
            await query.edit_message_text("❌ Ləğv edildi.")
        except:
            pass
        mark_pending_action_resolved(
            action_type="assign_executor",
            lead_id=lead_id,
            stage_key=stage_key,
            choice="Ləğv et",
        )
        return
    # Keep employee markers for the existing workflow; Kommo assignee is Admin.
    _ASSIGNEE_MARKER = {"rufat": "Rüfət Həsənzadə", "soltan": "Soltan Abbasov", "huseyn": "Hüseyn Səfərov", "rasim": "Rasim Əsgərov", "admin": ""}
    marker_name = _ASSIGNEE_MARKER.get(assignee_key, "")
    if assignee_key == "admin":
        assignee_uid = 10932455
        assignee_name = "Nizami Qasımov"
    else:
        assignee_uid = 10932455
        assignee_name = marker_name or "Admin"
    base_task_text = _STAGE_TASK_TEXTS.get(stage_key, "Mərhələ tapşırığı")
    task_text = f"[{marker_name}] {base_task_text}" if marker_name else base_task_text
    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
    # Show deadline buttons
    keyboard = [
        [
            InlineKeyboardButton("15 dəq", callback_data=f"stgdl-{lead_id}-{stage_key}-{assignee_key}-15m"),
            InlineKeyboardButton("1 saat", callback_data=f"stgdl-{lead_id}-{stage_key}-{assignee_key}-1h"),
        ],
        [
            InlineKeyboardButton("Bu gün", callback_data=f"stgdl-{lead_id}-{stage_key}-{assignee_key}-today"),
            InlineKeyboardButton("Sabah", callback_data=f"stgdl-{lead_id}-{stage_key}-{assignee_key}-tomorrow"),
        ],
        [InlineKeyboardButton("Bu həftə", callback_data=f"stgdl-{lead_id}-{stage_key}-{assignee_key}-week")],
    ]
    try:
        await query.edit_message_text(
            f"✅ *{assignee_name}* seçildi.\n\n📝 {task_text}\n🔗 {link}\n\n⏰ Son tarix seçin:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True
        )
    except:
        pass
    _pending_choice_by_assignee = {
        "rufat": "Rüfət", "soltan": "Soltan", "huseyn": "Hüseyn",
        "rasim": "Rasim", "texniki": "Texniki", "admin": "Özüm",
    }
    mark_pending_action_resolved(
        action_type="assign_executor",
        lead_id=lead_id,
        stage_key=stage_key,
        choice=_pending_choice_by_assignee.get(assignee_key, assignee_name),
    )

async def stage_task_deadline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deadline for webhook-triggered stage tasks.
    Callback: stgdl-{lead_id}-{stage_key}-{assignee}-{deadline}"""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data
    parts = data.split("-")
    if len(parts) < 5:
        return
    lead_id = int(parts[1])
    stage_key = parts[2]
    assignee_key = parts[3]
    deadline_key = parts[4]
    _ASSIGNEE_MARKER_DL = {"rufat": "Rüfət Həsənzadə", "soltan": "Soltan Abbasov", "huseyn": "Hüseyn Səfərov", "rasim": "Rasim Əsgərov", "admin": ""}
    marker_name = _ASSIGNEE_MARKER_DL.get(assignee_key, "")
    if assignee_key == "admin":
        assignee_uid = 10932455
        assignee_name = "Nizami Qasımov"
    else:
        assignee_uid = 10932455
        assignee_name = marker_name or "Admin"
    base_task_text = _STAGE_TASK_TEXTS.get(stage_key, "Mərhələ tapşırığı")
    task_text = f"[{marker_name}] {base_task_text}" if marker_name else base_task_text
    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
    now = datetime.now(tz=BAKU_TZ)
    if deadline_key == "15m":
        deadline_dt = now + timedelta(minutes=15)
    elif deadline_key == "1h":
        deadline_dt = now + timedelta(hours=1)
    elif deadline_key == "today":
        deadline_dt = now.replace(hour=17, minute=0, second=0, microsecond=0)
        if deadline_dt <= now:
            deadline_dt += timedelta(days=1)
    elif deadline_key == "tomorrow":
        deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    elif deadline_key == "week":
        days_until_friday = (4 - now.weekday()) % 7
        if days_until_friday == 0 and now.hour >= 18:
            days_until_friday = 7
        deadline_dt = (now + timedelta(days=days_until_friday)).replace(hour=17, minute=0, second=0, microsecond=0)
    else:
        deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    deadline_ts = int(deadline_dt.timestamp())
    result = create_task(lead_id, task_text, deadline_ts, responsible_user_id=assignee_uid, entity_type="leads")
    if result:
        if assignee_uid != 10932455 and marker_name:
            assignee_chat = get_chat_id_by_name(marker_name)
            if assignee_chat and _bot_app:
                try:
                    sent_a = await _bot_app.bot.send_message(
                        assignee_chat,
                        f"📋 *Yeni tapşırıq!*\n\n📝 {task_text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n🔗 {link}",
                        parse_mode="Markdown", disable_web_page_preview=True
                    )
                    tid = result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
                    if tid and sent_a:
                        store_message_task(assignee_chat, sent_a.message_id, int(tid), task_text, entity_id=lead_id, entity_type="leads")
                except:
                    pass
        result_text = f"✅ Tapşırıq *{assignee_name}*-ə təyin edildi!\n\n📝 {task_text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n🔗 {link}"
    else:
        result_text = f"⚠️ Tapşırıq yaratılmadı. Xəta.\n🔗 {link}"
    try:
        await query.edit_message_text(result_text, parse_mode="Markdown", disable_web_page_preview=True)
    except:
        pass

# ─── Overdue Task Callback ───────────────────────────────────────────────────
async def overdue_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # overdue_{task_id}_{action}
    parts = data.split("_")
    if len(parts) < 3:
        return
    task_id = int(parts[1])
    action = parts[2]
    if action == "done":
        res = update_task_kommo(task_id, {"is_completed": True, "result": {"text": "Tamamlandı"}})
        if res:
            try:
                await query.edit_message_text("✅ Tapşırıq tamamlandı!")
            except:
                pass
        else:
            try:
                await query.edit_message_text("❌ Xəta baş verdi.")
            except:
                pass
    elif action == "postpone":
        now = datetime.now(tz=BAKU_TZ)
        new_deadline = (now + timedelta(hours=2))
        res = update_task_kommo(task_id, {"complete_till": int(new_deadline.timestamp())})
        if res:
            try:
                await query.edit_message_text(f"⏰ 2 saat uzadıldı: {new_deadline.strftime('%H:%M')}")
            except:
                pass

# ─── Partner Handlers ────────────────────────────────────────────────────────
async def handle_partner_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str) -> bool:
    chat_id = update.message.chat_id
    if chat_id not in _pending_partner_registration:
        return False
    name_input = user_text.strip()
    partner_enums = fetch_partner_enums()
    if not partner_enums:
        await update.message.reply_text("⚠️ Partnyor siyahısı yüklənmədi. Sonra cəhd edin.")
        del _pending_partner_registration[chat_id]
        return True
    # Find matching partner
    matched = None
    for enum in partner_enums:
        if enum.get("value", "").lower() == name_input.lower():
            matched = enum
            break
    if not matched:
        for enum in partner_enums:
            if name_input.lower() in enum.get("value", "").lower():
                matched = enum
                break
    if matched:
        users = load_users()
        users[str(chat_id)] = {"role": "Partnyor", "name": matched["value"], "partner_enum_id": matched.get("id")}
        save_users(users)
        del _pending_partner_registration[chat_id]
        await update.message.reply_text(
            f"✅ Qeydiyyat tamamlandı!\n👤 {matched['value']} (Partnyor)\n\n"
            f"Müştəri nömrəsini göndərin — məlumat verəcəm."
        )
    else:
        available = ", ".join([e.get("value", "") for e in partner_enums[:10]])
        await update.message.reply_text(f"❌ '{name_input}' tapılmadı.\n\nMövcud partnyorlar: {available}\n\nYenidən yazın:")
    return True

async def partner_create_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    # partner_create_{phone}
    phone = query.data.replace("partner_create_", "")
    try:
        await query.edit_message_text(f"⚠️ Kontakt yaratma funksiyası hazırda aktiv deyil.\nTelefon: {phone}")
    except:
        pass

# ─── Handle Task Reply ───────────────────────────────────────────────────────
async def handle_task_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle replies to task notification messages. Returns True if handled."""
    if not update.message or not update.message.reply_to_message:
        return False
    chat_id = update.message.chat_id
    replied_msg_id = update.message.reply_to_message.message_id
    task_info = get_task_from_reply(chat_id, replied_msg_id)
    lead_info = get_lead_from_reply(chat_id, replied_msg_id)
    # Fallback: if no stored context, try to extract phone from replied message text
    if not task_info and not lead_info:
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        phone_match = re.search(r'\+?994\d{9}|0\d{9}', replied_text)
        if phone_match:
            extracted_phone = phone_match.group()
            lead_info = {"lead_name": "", "phone": extracted_phone}
        else:
            return False
    user_text = update.message.text.strip() if update.message.text else ""
    if not user_text:
        return False
    # Any reply to a task message = complete the task (+ add note with the reply text)
    if task_info:
        task_id = task_info.get("task_id")
        if task_id:
            # Check if it's a reschedule (date/time in reply)
            new_date = resolve_date_from_text(user_text)
            new_time = resolve_time_from_text(user_text)
            if new_date or new_time:
                now = datetime.now(tz=BAKU_TZ)
                if new_date and new_time:
                    new_dt = datetime.strptime(f"{new_date} {new_time}", "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ)
                elif new_date:
                    new_dt = datetime.strptime(f"{new_date} 09:00", "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ)
                elif new_time:
                    new_dt = datetime.strptime(f"{now.strftime('%d.%m.%Y')} {new_time}", "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ)
                else:
                    new_dt = None
                if new_dt:
                    res = update_task_kommo(task_id, {"complete_till": int(new_dt.timestamp())})
                    if res:
                        await update.message.reply_text(f"✅ Yeni vaxt: *{new_dt.strftime('%d.%m.%Y %H:%M')}*\n📝 {task_info.get('task_text', '')}", parse_mode="Markdown")
                        admin_chat = get_chat_id_for_kommo_user(10932455)
                        sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
                        if admin_chat and admin_chat != chat_id:
                            try:
                                await context.bot.send_message(
                                    admin_chat, f"⏰ *{sender_name}* vaxtı dəyişdi:\n📝 {task_info.get('task_text', '')}\n🕐 {new_dt.strftime('%d.%m.%Y %H:%M')}",
                                    parse_mode="Markdown"
                                )
                            except:
                                pass
                    return True
            # Otherwise — show confirmation before completing the task
            action_key = str(uuid.uuid4())[:8]
            _pending_actions[action_key] = {
                "action": "_complete_task_reply",
                "task_id": task_id,
                "task_info": task_info,
                "user_text": user_text,
                "chat_id": chat_id,
            }
            keyboard = [
                [
                    InlineKeyboardButton("✅ Bəli, tamamla", callback_data=f"actconf_{action_key}_yes"),
                    InlineKeyboardButton("❌ Xeyr", callback_data=f"actconf_{action_key}_no"),
                ]
            ]
            await update.message.reply_text(
                f"🤖 Tapşırığı tamamlayacam + qeyd əlavə edəcəm:\n\n"
                f"📝 Tapşırıq: {task_info.get('task_text', '')}\n"
                f"💬 Qeyd: {user_text}\n\n"
                f"Təsdiq edirsiniz?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return True
    # If replying to a lead notification, process as AI message with context
    if lead_info:
        # Add lead context to the message
        enriched = f"[Müştəri: {lead_info.get('lead_name', '')}, telefon: {lead_info.get('phone', '')}] {user_text}"
        await process_ai_message(update, context, enriched)
        return True
    return False

# ─── Voice Handler ───────────────────────────────────────────────────────────
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return
    voice = update.message.voice
    file_id = voice.file_id
    file_size = voice.file_size or 0
    if file_size > 20 * 1024 * 1024:
        await update.message.reply_text("❌ Səsli mesaj çox böyükdür (20MB-dan çox).")
        return
    status_msg = await update.message.reply_text("🎙 Səsli mesaj emal olunur...")
    try:
        new_file = await context.bot.get_file(file_id)
        ogg_path = f"/tmp/{file_id}.ogg"
        mp3_path = f"/tmp/{file_id}.mp3"
        downloaded = False
        for attempt in range(3):
            try:
                await new_file.download_to_drive(ogg_path)
                if os.path.exists(ogg_path) and os.path.getsize(ogg_path) > 0:
                    downloaded = True
                    break
            except Exception as dl_err:
                logger.warning(f"Voice download attempt {attempt+1} failed: {dl_err}")
                await asyncio.sleep(2)
        if not downloaded:
            raise Exception("Faylı yükləmək mümkün olmadı")
        conv_res = subprocess.run(["ffmpeg", "-i", ogg_path, "-y", mp3_path], capture_output=True, text=True, timeout=60)
        if conv_res.returncode != 0:
            raise Exception(f"FFmpeg: {conv_res.stderr[:200]}")
        stt_res = subprocess.run(["manus-speech-to-text", mp3_path], capture_output=True, text=True, timeout=120)
        if stt_res.returncode != 0:
            logger.warning(f"STT stderr: {stt_res.stderr[:300]}")
        transcription_files = glob.glob(f"/tmp/*transcription*.txt")
        if not transcription_files:
            raise Exception("Transkripsiya faylı tapılmadı")
        latest_file = max(transcription_files, key=os.path.getctime)
        with open(latest_file, "r") as f:
            transcribed_text = f.read().strip()
        transcribed_text = clean_transcription(transcribed_text)
        for fp in [ogg_path, mp3_path, latest_file]:
            try:
                os.remove(fp)
            except:
                pass
        if not transcribed_text:
            await status_msg.edit_text("❌ Səs tanınmadı.")
            return
        logger.info(f"Voice transcription: {transcribed_text[:300]}")
        try:
            await status_msg.edit_text(f"📝 _{transcribed_text[:500]}_", parse_mode="Markdown")
        except:
            pass
        await process_ai_message(update, context, transcribed_text)
    except Exception as e:
        logger.error(f"Voice error: {e}\n{traceback.format_exc()}")
        try:
            await status_msg.edit_text(f"❌ Audio xətası: {str(e)[:200]}")
        except:
            pass

# ─── Button Flow Handlers ────────────────────────────────────────────────────
def _next_step_after_phone(action: str) -> str:
    """Return the next step after phone/contact is resolved."""
    if action in ("task", "note"):
        return "text"
    elif action == "stage":
        return "stage_select"
    return "done"

async def start_button_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, button_text: str):
    """Start a step-by-step flow when user presses a main keyboard button."""
    chat_id = update.message.chat_id
    if button_text == "📋 Yeni tapşırıq":
        _button_flow[chat_id] = {"action": "task", "step": "phone"}
        await update.message.reply_text("📞 Müştərinin telefon nömrəsini yazın:")
    elif button_text == "🔄 Mərhələ dəyiş":
        await update.message.reply_text("📱 Mərhələ dəyişmək üçün Mini App-dan istifadə edin (sol alt küncdeki CRM düyməsi).")
    elif button_text == "📝 Qeyd əlavə et":
        _button_flow[chat_id] = {"action": "note", "step": "phone"}
        await update.message.reply_text("📞 Müştərinin telefon nömrəsini yazın:")
    elif button_text == "ℹ️ Müştəri info":
        _button_flow[chat_id] = {"action": "info", "step": "phone"}
        await update.message.reply_text("📞 Müştərinin telefon nömrəsini yazın:")

async def handle_button_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    """Handle step-by-step input for button flows."""
    chat_id = update.message.chat_id
    flow = _button_flow[chat_id]
    action = flow["action"]
    step = flow["step"]

    # Allow cancel
    if user_text.lower() in ("ləğv", "cancel", "/cancel"):
        del _button_flow[chat_id]
        await update.message.reply_text("❌ Ləğv edildi.")
        return

    # If user presses another main button, restart
    if user_text in ("📋 Yeni tapşırıq", "🔄 Mərhələ dəyiş", "📝 Qeyd əlavə et", "ℹ️ Müştəri info"):
        del _button_flow[chat_id]
        await start_button_flow(update, context, user_text)
        return

    if step == "phone":
        # Validate: must contain at least 7 digits
        digits_only = re.sub(r'[^\d]', '', user_text)
        if len(digits_only) < 7:
            await update.message.reply_text("❌ Düzgün telefon nömrəsi daxil edin (minimum 7 rəqəm):")
            return
        phone_match = re.search(r'\+?\d[\d\s\-]{7,}', user_text)
        phone = re.sub(r'[\s\-]', '', phone_match.group()) if phone_match else user_text.strip()
        contacts = search_contact_by_phone(phone)
        if not contacts:
            flow["phone"] = phone
            flow["step"] = "create_contact"
            keyboard = [
                [InlineKeyboardButton("✅ Yeni kontakt yarat", callback_data=f"btnflow_{chat_id}_newcontact")],
                [InlineKeyboardButton("❌ Ləğv et", callback_data=f"btnflow_{chat_id}_cancelflow")],
            ]
            await update.message.reply_text(
                f"❌ '{phone}' nömrəli müştəri tapılmadı.\n\nYeni kontakt yaratmaq istəyirsiniz?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        contact = contacts[0]
        contact_name = contact.get("name", "Adsız")
        flow["phone"] = phone
        flow["contact"] = contact
        flow["contact_name"] = contact_name

        if action == "info":
            del _button_flow[chat_id]
            result = execute_tool_get_lead_info(phone)
            try:
                await update.message.reply_text(result, parse_mode="Markdown", disable_web_page_preview=True)
            except:
                await update.message.reply_text(result, disable_web_page_preview=True)
            return
        elif action == "note":
            flow["step"] = "text"
            await update.message.reply_text(f"✅ {contact_name}\n\n📝 Qeydi yazın:")
            return
        elif action == "task":
            flow["step"] = "text"
            await update.message.reply_text(f"✅ {contact_name}\n\n📝 Tapşırığın mətnini yazın:")
            return
        elif action == "stage":
            flow["step"] = "stage_select"
            stage_list = [
                ("danisiqlar", "Danışıqlar"), ("qiymet_teklifi", "Qiymət təklifi"),
                ("teqdimat", "Təqdimat"), ("teqdimat_olundu", "Təqdimat olundu"),
                ("yeni_sifaris", "Yeni sifariş"), ("gorus", "Görüş"),
                ("qurashdirma", "Quraşdırma"), ("ugurlu", "Uğurlu sifariş"),
            ]
            flow["stage_options"] = [k for k, v in stage_list]
            keyboard = []
            row = []
            for i, (key, name) in enumerate(stage_list):
                row.append(InlineKeyboardButton(name, callback_data=f"btnflow_{chat_id}_stg{i}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            await update.message.reply_text(
                f"✅ {contact_name}\n📞 {phone}\n\n📌 Yeni mərhələ seçin:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

    elif step == "text":
        if action == "note":
            del _button_flow[chat_id]
            action_key = str(uuid.uuid4())[:8]
            _pending_actions[action_key] = {
                "action": "add_note",
                "args": {"phone": flow["phone"], "text": user_text},
                "chat_id": chat_id,
                "summary": f"📝 Qeyd əlavə edəcəm:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n💬 {user_text}",
            }
            keyboard = [
                [
                    InlineKeyboardButton("✅ Təsdiq et", callback_data=f"actconf_{action_key}_yes"),
                    InlineKeyboardButton("❌ Ləğv et", callback_data=f"actconf_{action_key}_no"),
                ]
            ]
            await update.message.reply_text(
                f"🤖 📝 Qeyd əlavə edəcəm:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n💬 {user_text}\n\nTəsdiq edirsiniz?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        elif action == "task":
            flow["task_text"] = user_text
            flow["step"] = "assignee"
            keyboard = [
                [
                    InlineKeyboardButton("Rüfət", callback_data=f"btnflow_{chat_id}_rufat"),
                    InlineKeyboardButton("Soltan", callback_data=f"btnflow_{chat_id}_soltan"),
                ],
                [InlineKeyboardButton("Admin", callback_data=f"btnflow_{chat_id}_admin")],
            ]
            await update.message.reply_text(
                f"📋 Tapşırıq:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n📝 {user_text}\n\n👤 Kim icra edəcək?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

    elif step == "stage_select":
        try:
            idx = int(user_text.strip()) - 1
            stage_options = flow.get("stage_options", [])
            if 0 <= idx < len(stage_options):
                selected_stage = stage_options[idx]
                del _button_flow[chat_id]
                stage_display = STAGE_NAMES.get(STAGES.get(selected_stage, 0), selected_stage)
                action_key = str(uuid.uuid4())[:8]
                _pending_actions[action_key] = {
                    "action": "change_stage",
                    "args": {"phone": flow["phone"], "stage": selected_stage},
                    "chat_id": chat_id,
                    "summary": f"🔄 Mərhələ dəyişəcəm:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n📌 Yeni mərhələ: {stage_display}",
                }
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Təsdiq et", callback_data=f"actconf_{action_key}_yes"),
                        InlineKeyboardButton("❌ Ləğv et", callback_data=f"actconf_{action_key}_no"),
                    ]
                ]
                await update.message.reply_text(
                    f"🤖 🔄 Mərhələ dəyişəcəm:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n📌 Yeni mərhələ: {stage_display}\n\nTəsdiq edirsiniz?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            else:
                await update.message.reply_text("❌ Səhv rəqəm. Yenidən seçin:")
                return
        except ValueError:
            await update.message.reply_text("❌ Rəqəm daxil edin (1-8):")
            return

    del _button_flow[chat_id]
    await update.message.reply_text("❌ Xəta baş verdi. Yenidən başlayın.")

async def btnflow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button flow callbacks: newcontact, cancelflow, stage selection, assignee."""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # btnflow_{chat_id}_{action}
    parts = data.split("_")
    if len(parts) < 3:
        return
    chat_id = int(parts[1])
    action_key = "_".join(parts[2:])  # handle multi-part keys like stg0
    flow = _button_flow.get(chat_id)

    # Cancel flow
    if action_key == "cancelflow":
        _button_flow.pop(chat_id, None)
        await query.edit_message_text("❌ Ləğv edildi.")
        return

    # Create new contact
    if action_key == "newcontact":
        if not flow:
            await query.edit_message_text("❌ Vaxtı keçib.")
            return
        phone = flow.get("phone", "")
        # Create contact with phone as name (user can rename later)
        result = create_contact_kommo(phone, phone)
        if result:
            contact_data = result.get("_embedded", {}).get("contacts", [{}])[0]
            contact_id = contact_data.get("id")
            # Create a lead for this contact
            lead_payload = [{
                "name": f"Sövdələşmə - {phone}",
                "pipeline_id": PIPELINE_ID,
                "status_id": STAGES["danisiqlar"],
                "responsible_user_id": 10932455,
                "_embedded": {"contacts": [{"id": contact_id}]}
            }]
            try:
                resp = _http.post(f"{KOMMO_BASE_URL}/api/v4/leads", headers=HEADERS, json=lead_payload, timeout=8)
                lead_result = resp.json() if resp.status_code in (200, 201) else None
            except:
                lead_result = None
            # Get full contact
            full_contact = get_contact_details(contact_id) or {"id": contact_id, "name": phone}
            flow["contact"] = full_contact
            flow["contact_name"] = phone
            flow["step"] = _next_step_after_phone(flow["action"])
            action = flow["action"]
            if action == "task":
                await query.edit_message_text(f"✅ Kontakt yaradıldı: {phone}\n\n📝 Tapşırığın mətnini yazın:")
            elif action == "note":
                await query.edit_message_text(f"✅ Kontakt yaradıldı: {phone}\n\n📝 Qeydi yazın:")
            elif action == "stage":
                stage_list = [
                    ("danisiqlar", "Danışıqlar"), ("qiymet_teklifi", "Qiymət təklifi"),
                    ("teqdimat", "Təqdimat"), ("teqdimat_olundu", "Təqdimat olundu"),
                    ("yeni_sifaris", "Yeni sifariş"), ("gorus", "Görüş"),
                    ("qurashdirma", "Quraşdırma"), ("ugurlu", "Uğurlu sifariş"),
                ]
                flow["stage_options"] = [k for k, v in stage_list]
                keyboard = []
                row = []
                for i, (key, name) in enumerate(stage_list):
                    row.append(InlineKeyboardButton(name, callback_data=f"btnflow_{chat_id}_stg{i}"))
                    if len(row) == 2:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)
                await query.edit_message_text(
                    f"✅ Kontakt yaradıldı: {phone}\n\n📌 Mərhələ seçin:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            elif action == "info":
                _button_flow.pop(chat_id, None)
                link = f"{KOMMO_BASE_URL}/contacts/detail/{contact_id}"
                await query.edit_message_text(f"✅ Kontakt yaradıldı: {phone}\n🔗 {link}", disable_web_page_preview=True)
        else:
            await query.edit_message_text("❌ Kontakt yaradılarkən xəta baş verdi.")
            _button_flow.pop(chat_id, None)
        return

    # Stage selection (stg0, stg1, ...)
    if action_key.startswith("stg"):
        if not flow:
            await query.edit_message_text("❌ Vaxtı keçib.")
            return
        try:
            idx = int(action_key[3:])
        except ValueError:
            return
        stage_options = flow.get("stage_options", [])
        if 0 <= idx < len(stage_options):
            selected_stage = stage_options[idx]
            stage_display = STAGE_NAMES.get(STAGES.get(selected_stage, 0), selected_stage)
            _button_flow.pop(chat_id, None)
            action_key_id = str(uuid.uuid4())[:8]
            _pending_actions[action_key_id] = {
                "action": "change_stage",
                "args": {"phone": flow["phone"], "stage": selected_stage},
                "chat_id": chat_id,
                "summary": f"🔄 Mərhələ dəyişəcəm:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n📌 Yeni mərhələ: {stage_display}",
            }
            keyboard = [
                [
                    InlineKeyboardButton("✅ Təsdiq et", callback_data=f"actconf_{action_key_id}_yes"),
                    InlineKeyboardButton("❌ Ləğv et", callback_data=f"actconf_{action_key_id}_no"),
                ]
            ]
            await query.edit_message_text(
                f"🤖 🔄 Mərhələ dəyişəcəm:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n📌 Yeni mərhələ: {stage_display}\n\nTəsdiq edirsiniz?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text("❌ Səhv seçim.")
        return

    # Assignee selection for task
    if not flow or flow.get("action") != "task":
        await query.edit_message_text("❌ Vaxtı keçib.")
        return
    assignee_map = {"rufat": (10932455, "Admin"), "soltan": (15531960, "Soltan Abbasov"), "admin": (10932455, "Admin"), "sahe_meneceri": (10932455, "Admin")}
    assignee_id, assignee_name = assignee_map.get(action_key, (10932455, "Admin"))
    flow["assignee_id"] = assignee_id
    flow["assignee_name"] = assignee_name
    flow["step"] = "deadline"
    keyboard = [
        [
            InlineKeyboardButton("15 dəq", callback_data=f"btnflowdl_{chat_id}_15m"),
            InlineKeyboardButton("1 saat", callback_data=f"btnflowdl_{chat_id}_1h"),
        ],
        [
            InlineKeyboardButton("Bu gün", callback_data=f"btnflowdl_{chat_id}_today"),
            InlineKeyboardButton("Sabah", callback_data=f"btnflowdl_{chat_id}_tomorrow"),
        ],
        [InlineKeyboardButton("Bu həftə", callback_data=f"btnflowdl_{chat_id}_week")],
    ]
    await query.edit_message_text(
        f"📋 Tapşırıq:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n📝 {flow['task_text']}\n👤 Məsul: {assignee_name}\n\n⏰ Son tarix seçin:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def btnflowdl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle deadline selection in button flow for task creation."""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # btnflowdl_{chat_id}_{deadline}
    parts = data.split("_")
    if len(parts) < 3:
        return
    chat_id = int(parts[1])
    dl_key = parts[2]
    flow = _button_flow.pop(chat_id, None)
    if not flow or flow.get("action") != "task":
        await query.edit_message_text("❌ Vaxtı keçib.")
        return
    now = datetime.now(tz=BAKU_TZ)
    dl_map = {
        "15m": now + timedelta(minutes=15),
        "1h": now + timedelta(hours=1),
        "today": now.replace(hour=18, minute=0, second=0),
        "tomorrow": (now + timedelta(days=1)).replace(hour=12, minute=0, second=0),
        "week": (now + timedelta(days=(7 - now.weekday()))).replace(hour=12, minute=0, second=0),
    }
    deadline_dt = dl_map.get(dl_key, now + timedelta(hours=1))
    complete_till = int(deadline_dt.timestamp())
    contact = flow["contact"]
    entity_id = contact["id"]
    entity_type = "contacts"
    full_c = get_contact_details(contact["id"])
    if full_c:
        leads = (full_c or {}).get("_embedded", {}).get("leads", [])
        if leads:
            entity_id = leads[0]["id"]
            entity_type = "leads"
    res = create_task(entity_id, flow["task_text"], complete_till,
                      responsible_user_id=flow["assignee_id"], entity_type=entity_type)
    if res:
        link = f"{KOMMO_BASE_URL}/{entity_type}/detail/{entity_id}"
        msg = (f"✅ Tapşırıq yaradıldı!\n\n"
               f"👤 {flow['contact_name']}\n📞 {flow['phone']}\n"
               f"📝 {flow['task_text']}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n"
               f"👤 Məsul: {flow['assignee_name']}\n🔗 {link}")
        await query.edit_message_text(msg, disable_web_page_preview=True)
        # Notify assignee
        if flow["assignee_id"] != 10932455:
            assignee_chat = get_chat_id_for_kommo_user(flow["assignee_id"])
            if assignee_chat:
                try:
                    sent = await context.bot.send_message(
                        assignee_chat,
                        f"📢 Yeni tapşırıq:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n"
                        f"📝 {flow['task_text']}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n🔗 {link}",
                        disable_web_page_preview=True
                    )
                    if sent:
                        store_message_task(assignee_chat, sent.message_id, res.get("id", 0),
                                           flow["task_text"], entity_id, entity_type, flow["phone"])
                except:
                    pass
        # Notify admin
        admin_chat = get_chat_id_for_kommo_user(10932455)
        sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
        if admin_chat and admin_chat != chat_id:
            try:
                await context.bot.send_message(
                    admin_chat,
                    f"📢 {sender_name} tapşırıq yaratdı:\n\n👤 {flow['contact_name']}\n📞 {flow['phone']}\n"
                    f"📝 {flow['task_text']}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n"
                    f"👤 Məsul: {flow['assignee_name']}\n🔗 {link}",
                    disable_web_page_preview=True
                )
            except:
                pass
    else:
        await query.edit_message_text("❌ Tapşırıq yaradılarkən xəta.")

# ─── Contact Message Handler ─────────────────────────────────────────────────
async def handle_contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shared contact from phone book."""
    if not update.message or not update.message.contact:
        return
    chat_id = update.message.chat_id
    users = load_users()
    if str(chat_id) not in users:
        await update.message.reply_text("⚠️ Qeydiyyatdan keçməmisiniz. /start yazın.")
        return
    contact = update.message.contact
    phone = contact.phone_number or ""
    if not phone:
        await update.message.reply_text("❌ Telefon nömrəsi tapılmadı.")
        return
    # Normalize: add + if starts with digit
    if phone and phone[0].isdigit():
        phone = "+" + phone
    # If in button flow on phone step, process it
    if chat_id in _button_flow and _button_flow[chat_id].get("step") == "phone":
        await handle_button_flow(update, context, phone)
        return
    # Otherwise treat as info request
    result = execute_tool_get_lead_info(phone)
    try:
        await update.message.reply_text(result, parse_mode="Markdown", disable_web_page_preview=True)
    except:
        await update.message.reply_text(result, disable_web_page_preview=True)

# ─── Web App Data Handler ────────────────────────────────────────────────────
async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle data sent from Telegram Web App (Mini App)."""
    if not update.message or not update.message.web_app_data:
        return
    chat_id = update.message.chat_id
    users = load_users()
    if str(chat_id) not in users:
        await update.message.reply_text("⚠️ Qeydiyyatdan keçməmisiniz. /start yazın.")
        return
    try:
        data = json.loads(update.message.web_app_data.data)
    except:
        await update.message.reply_text("❌ Xəta baş verdi.")
        return
    action = data.get("action")
    phone = data.get("phone", "")
    if action == "info":
        result = execute_tool_get_lead_info(phone)
        try:
            await update.message.reply_text(result, parse_mode="Markdown", disable_web_page_preview=True)
        except:
            await update.message.reply_text(result, disable_web_page_preview=True)
    elif action == "note":
        text = data.get("text", "")
        result = execute_tool_add_note(phone, text)
        await update.message.reply_text(result, disable_web_page_preview=True)
        # Admin notification
        admin_chat = get_chat_id_for_kommo_user(10932455)
        sender_name = None
        for _nm, _cid in NAME_TO_CHAT.items():
            if _cid == chat_id and len(_nm) > 5:
                sender_name = _nm
                break
        if not sender_name:
            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), 'Əməkdaş')
        if admin_chat and admin_chat != chat_id:
            try:
                await context.bot.send_message(admin_chat, f"📝 *{sender_name}* qeyd əlavə etdi:\n\n{result}", parse_mode="Markdown", disable_web_page_preview=True)
#                 send_push_to_admin(f"{sender_name} qeyd əlavə etdi: {result}", title="📝 Qeyd")
            except:
                pass
    elif action == "stage":
        stage = data.get("stage", "")
        result = execute_tool_change_stage(phone, stage, chat_id)
        if not result["success"]:
            await update.message.reply_text(result["message"])
        elif result.get("needs_confirmation"):
            # Non-admin: send to admin
            conf_key = str(uuid.uuid4())[:8]
            context.bot_data[f"confirm_{conf_key}"] = {
                "phone": result["phone"], "stage": stage,
                "lead_id": result["lead_id"], "status_id": result["status_id"],
                "sender_chat_id": chat_id, "sender_kommo_id": get_kommo_user_id_for_chat(chat_id)
            }
            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
            stage_display = get_pipeline_stages_for_chat(chat_id)[1].get(result["status_id"], stage)
            admin_chat = get_chat_id_for_kommo_user(10932455)
            sent = None
            if admin_chat:
                keyboard = [[InlineKeyboardButton("✅ Təsdiq et", callback_data=f"conftr_{conf_key}_yes"), InlineKeyboardButton("❌ Rədd et", callback_data=f"conftr_{conf_key}_no")]]
                try:
                    sent = await context.bot.send_message(admin_chat, f"🔄 *{sender_name}* mərhələ dəyişikliyi istəyir:\n\n👤 {result['contact_name']}\n📞 {result['phone']}\n📌 Yeni mərhələ: *{stage_display}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
                except:
                    pass
            send_push_to_admin(
                f"{sender_name} mərhələ dəyişikliyi: {result['contact_name']} → {stage_display}",
                title="🔄 Mərhələ",
            url="#pending",
            )
            await update.message.reply_text(f"⏳ Sorğunuz Admin-ə göndərildi.\n👤 {result['contact_name']} → {stage_display}")
        else:
            # Admin: execute
            update_lead_kommo(result["lead_id"], {"status_id": result["status_id"], "pipeline_id": get_pipeline_id_for_chat(chat_id)})
            stage_display = get_pipeline_stages_for_chat(chat_id)[1].get(result["status_id"], stage)
            link = f"{KOMMO_BASE_URL}/leads/detail/{result['lead_id']}"
            await update.message.reply_text(f"✅ Mərhələ dəyişdirildi!\n👤 {result['contact_name']}\n📌 {stage_display}\n🔗 {link}", disable_web_page_preview=True)
    elif action == "task":
        text = data.get("text", "")
        assignee = data.get("assignee", "admin")
        creator_name = get_employee_name_by_chat_id(chat_id, "")
        if assignee != "admin" and creator_name:
            text = re.sub(r"^\[[^\]]+\]\s*", "", text)
            text = f"[{creator_name}] {text}"
        deadline_key = data.get("deadline", "tomorrow")
        # Resolve deadline
        now = datetime.now(tz=BAKU_TZ)
        if deadline_key == "15m": deadline_dt = now + timedelta(minutes=15)
        elif deadline_key == "1h": deadline_dt = now + timedelta(hours=1)
        elif deadline_key == "today":
            deadline_dt = now.replace(hour=17, minute=0, second=0, microsecond=0)
            if deadline_dt <= now: deadline_dt += timedelta(days=1)
        elif deadline_key == "tomorrow": deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        elif deadline_key == "week":
            days_until_friday = (4 - now.weekday()) % 7
            if days_until_friday == 0 and now.hour >= 18: days_until_friday = 7
            deadline_dt = (now + timedelta(days=days_until_friday)).replace(hour=17, minute=0, second=0, microsecond=0)
        else: deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        result = execute_tool_create_task(phone, text, None, None, assignee)
        if isinstance(result, str):
            await update.message.reply_text(result)
            return
        if not result["success"]:
            await update.message.reply_text(result["message"])
            return
        deadline_ts = int(deadline_dt.timestamp())
        res = create_task(result["entity_id"], text, deadline_ts, responsible_user_id=result["assignee_id"], entity_type=result["entity_type"], creator_name=creator_name)
        if res:
            msg = f"✅ Tapşırıq yaradıldı!\n\n👤 {result['contact_name']}\n📞 {phone}\n📝 {text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n👤 Məsul: {result['assignee_name']}\n🔗 {result['link']}"
            await update.message.reply_text(msg, disable_web_page_preview=True)
            # Notify assignee
            if result["assignee_id"] != 10932455:
                assignee_chat = get_chat_id_for_kommo_user(result["assignee_id"])
                if assignee_chat:
                    try:
                        await context.bot.send_message(assignee_chat, f"📋 *Yeni tapşırıq!*\n\n👤 {result['contact_name']}\n📞 {phone}\n📝 {text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n🔗 {result['link']}", parse_mode="Markdown", disable_web_page_preview=True)
                    except: pass
            # Notify admin
            admin_chat = get_chat_id_for_kommo_user(10932455)
            if admin_chat and admin_chat != chat_id:
                sender_name = get_employee_name_by_chat_id(
                    chat_id,
                    KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş"),
                )
                try:
                    await context.bot.send_message(admin_chat, f"📋 *{sender_name}* tapşırıq yaratdı:\n\n👤 {result['contact_name']}\n📞 {phone}\n📝 {text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n👤 Məsul: {result['assignee_name']}\n🔗 {result['link']}", parse_mode="Markdown", disable_web_page_preview=True)
#                    send_push_to_admin(f"{sender_name} tapşırıq yaratdı: {result['contact_name']}", title="📋 Yeni tapşırıq")
                except: pass
        else:
            await update.message.reply_text("❌ Tapşırıq yaradılarkən xəta.")

# ─── Free Text Handler ───────────────────────────────────────────────────────
async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    chat_id = update.message.chat_id
    # Handle group mentions
    if update.message.chat.type in ("group", "supergroup"):
        bot_username = context.bot.username
        if not bot_username or f"@{bot_username}" not in user_text:
            return
        user_text = user_text.replace(f"@{bot_username}", "").strip()
    if not user_text:
        return
    # Check registration
    users = load_users()
    if str(chat_id) not in users:
        # Check pending registrations
        if chat_id in _pending_partner_registration:
            await handle_partner_registration(update, context, user_text)
            return
        if chat_id in _pending_employee_registration:
            emp_state = _pending_employee_registration[chat_id]
            if emp_state == "__ask_name__":
                _pending_employee_registration[chat_id] = user_text
                admin_chat = get_chat_id_for_kommo_user(10932455)
                if admin_chat:
                    keyboard = [
                        [
                            InlineKeyboardButton("✅ Təsdiq", callback_data=f"empreg_{chat_id}_yes"),
                            InlineKeyboardButton("❌ Rədd", callback_data=f"empreg_{chat_id}_no"),
                        ]
                    ]
                    try:
                        await context.bot.send_message(
                            admin_chat, f"👤 Yeni əməkdaş qeydiyyatı:\n\nAd: {user_text}\nChat ID: {chat_id}",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    except:
                        pass
                await update.message.reply_text("⏳ Sorğunuz Admin-ə göndərildi. Təsdiq gözlənilir.")
            return
        await update.message.reply_text("⚠️ Qeydiyyatdan keçməmisiniz. /start yazın.")
        return
    # Check if it's a reply to a task/lead notification
    if await handle_task_reply(update, context):
        return
    # Check pending partner registration
    if chat_id in _pending_partner_registration:
        await handle_partner_registration(update, context, user_text)
        return
    # All actions moved to Mini App
    await update.message.reply_text("📱 Bütün əməliyyatlar üçün Mini App-dan istifadə edin (sol aşağıdakı CRM düyməsi).")

# ─── Kommo Webhook Handler ───────────────────────────────────────────────────
_bot_app: Application = None

_inbox_pulse_rev = 0
_inbox_pulse_events: list[dict] = []
_inbox_pulse_lock = threading.Lock()


def _cached_lead_id_for_contact(contact_id: int) -> int:
    try:
        wanted = int(contact_id)
    except (TypeError, ValueError):
        return 0
    if not wanted:
        return 0
    for overview in _personal_overview_cache.values():
        if not isinstance(overview, dict):
            continue
        for deal in overview.get("deals") or []:
            if not isinstance(deal, dict):
                continue
            for contact in deal.get("contacts") or []:
                if not isinstance(contact, dict):
                    continue
                try:
                    current = int(contact.get("id") or 0)
                    lead_id = int(deal.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                if current == wanted and lead_id:
                    return lead_id
    return 0


def record_lead_pulse_event(
    lead_id: int,
    event_type: str = "deal_update",
    *,
    pipeline_id: int = 0,
    stage_key: str = "",
    preview: str = "",
    channel: str = "",
    contact_name: str = "",
    phone: str = "",
    missing: bool = False,
    refresh: bool = False,
    incoming_at: int = 0,
) -> None:
    """Record an incremental event into the pulse queue for browser polling."""
    global _inbox_pulse_rev
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return
    if not lid:
        return
    pipe = int(pipeline_id or 0)
    cname = str(contact_name or "")
    cphone = str(phone or "")
    # Check personal overview cache if missing details
    if not pipe or not cname or not cphone:
        for overview in _personal_overview_cache.values():
            if not isinstance(overview, dict):
                continue
            for deal in overview.get("deals") or []:
                if isinstance(deal, dict) and int(deal.get("id") or 0) == lid:
                    if not pipe:
                        try:
                            pipe = int(deal.get("pipeline_id") or 0)
                        except (TypeError, ValueError):
                            pipe = 0
                    if not cname:
                        cname = str(deal.get("contact_name") or "")
                    if not cphone:
                        cphone = str(deal.get("phone") or "")
                    break
            if pipe and cname:
                break

    now_ts = int(_time_module.time())
    if incoming_at > 0:
        event_incoming_at = incoming_at
    elif str(event_type or "").strip().lower() in {"incoming_message", "incoming"} and preview:
        event_incoming_at = now_ts
    else:
        event_incoming_at = 0

    with _inbox_pulse_lock:
        _inbox_pulse_rev += 1
        _inbox_pulse_events.append({
            "rev": _inbox_pulse_rev,
            "lead_id": lid,
            "type": str(event_type or "deal_update"),
            "pipeline_id": pipe,
            "stage_key": str(stage_key or ""),
            "contact_name": cname,
            "phone": cphone,
            "last_client_message": str(preview or ""),
            "last_incoming_at": event_incoming_at,
            "chat_channel": str(channel or ""),
            "missing": bool(missing),
            "refresh": bool(refresh),
        })
        if len(_inbox_pulse_events) > 150:
            del _inbox_pulse_events[:-150]
    try:
        _invalidate_deal_chat_cache(lid)
    except Exception:
        pass


async def _handle_kommo_task_webhook(data: dict):
    """Process add_task / update_task Kommo webhook events."""
    if not _bot_app:
        return
    possible_add_prefixes = ["tasks[add][0]", "task[add][0]"]
    possible_upd_prefixes = ["tasks[update][0]", "task[update][0]"]
    possible_gen_prefixes = ["task[0]", "tasks[0]"]
    is_add = any(k.startswith(p) for k in data for p in possible_add_prefixes)
    logger.info(f"Task webhook: is_add={is_add}, keys_sample={[k for k in list(data.keys())[:15]]}")
    is_upd = any(k.startswith(p) for k in data for p in possible_upd_prefixes)
    def _get(key):
        for p in possible_add_prefixes + possible_upd_prefixes + possible_gen_prefixes:
            v = data.get(f"{p}[{key}]")
            if v is not None:
                return v
        return data.get(key)
    task_id_raw = _get("id")
    task_text = _get("text") or "Tapşırıq"
    responsible_raw = _get("responsible_user_id")
    entity_id_raw = _get("element_id")
    entity_type_raw = _get("element_type")
    deadline_raw = _get("complete_till")
    created_by_raw = _get("created_by") or _get("created_user_id") or _get("created_by_id")
    task_type_id_raw = _get("task_type_id") or _get("task_type")
    # Task type names mapping
    _TASK_TYPE_NAMES = {
        1: "Əlaqə saxla", 2: "Görüş", 3263995: "Təqdimat",
        4187880: "Yeni", 3263999: "Quraşdırma", 3265439: "Tapşırıq",
        3267595: "Zəng et", 4229224: "Cavab gözlənilir",
        4232112: "aktiv", 4232108: "Import", 4239844: "passiv"
    }
    task_type_name = ""
    if task_type_id_raw:
        try:
            task_type_name = _TASK_TYPE_NAMES.get(int(task_type_id_raw), "")
        except:
            pass
    responsible_id = int(responsible_raw) if responsible_raw else None
    entity_id = int(entity_id_raw) if entity_id_raw else None
    entity_type_num = int(entity_type_raw) if entity_type_raw else None
    entity_type = "leads" if entity_type_num == 2 else "contacts" if entity_type_num == 1 else "leads"
    created_by = int(created_by_raw) if created_by_raw else None
    deadline_str = ""
    if deadline_raw:
        try:
            dl_dt = datetime.fromtimestamp(int(deadline_raw), tz=BAKU_TZ)
            deadline_str = dl_dt.strftime("%d.%m.%Y %H:%M")
        except:
            pass
    link = ""
    client_name = ""
    client_phone = ""
    if entity_id:
        link = f"{KOMMO_BASE_URL}/{'leads' if entity_type == 'leads' else 'contacts'}/detail/{entity_id}"
        client_name = get_contact_name_from_entity(entity_id, entity_type)
        client_phone = get_phone_from_entity(entity_id, entity_type)
        target_lid = entity_id if entity_type == "leads" else _cached_lead_id_for_contact(entity_id)
        if target_lid:
            record_lead_pulse_event(
                target_lid,
                "task_update",
                contact_name=client_name,
                phone=client_phone,
            )
    admin_chat = get_chat_id_for_kommo_user(10932455)
    # Suppress any webhook for bot-touched tasks
    import time as _time
    if task_id_raw:
        tid = int(task_id_raw)
        # Suppress if bot updated this task (complete, postpone, etc)
        if tid in _bot_updated_tasks:
            if _time.time() - _bot_updated_tasks[tid] < 120:
                logger.info(f"Webhook suppressed: bot-updated task {tid}")
                return
            else:
                del _bot_updated_tasks[tid]
    if _is_bot_created_task_webhook(task_id_raw, entity_id):
        logger.info(f"Webhook suppressed: bot-created task {task_id_raw} lead={entity_id}")
        return
    # Suppress duplicate webhook for same task_id (Kommo sends multiple add webhooks)
    if is_add and task_id_raw:
        tid = int(task_id_raw)
        if tid in _notified_task_webhooks:
            if _time.time() - _notified_task_webhooks[tid] < 300:
                logger.info(f"Webhook suppressed: duplicate add notification for task {tid}")
                return
        _notified_task_webhooks[tid] = _time.time()
        # Cleanup old entries
        if len(_notified_task_webhooks) > 200:
            _notified_task_webhooks.clear()
    # If not add and not update — ignore (generic task event, no notification needed)
    if not is_add and not is_upd:
        logger.info(f"Webhook ignored: neither add nor update, task_id={task_id_raw}")
        return
    # Suppress "Cavab gözlənilir" task type - no notifications
    if task_type_id_raw:
        try:
            if int(task_type_id_raw) == 4229224:
                return
        except:
            pass
    # Fetch last note for this entity
    last_note_text = ""
    if entity_id:
        try:
            _n_url = f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes"
            _n_resp = _http.get(_n_url, headers=HEADERS, params={"limit": 1, "order[updated_at]": "desc", "filter[note_type]": "common"}, timeout=8)
            if _n_resp.status_code == 200:
                _n_data = _n_resp.json().get("_embedded", {}).get("notes", [])
                if _n_data:
                    last_note_text = _n_data[0].get("params", {}).get("text", "")
        except:
            pass
    note_line = f"\n📝 Qeyd: {last_note_text}" if last_note_text else ""
    logger.info(f"Task webhook notification check: is_add={is_add}, task_id={task_id_raw}, created_by={created_by}, responsible={responsible_id}")
    if is_add and responsible_id and responsible_id != 10932455:
        # Check if task is already completed before notifying
        _task_completed = False
        if task_id_raw:
            try:
                _tc = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id_raw}", headers=HEADERS, timeout=8)
                if _tc.status_code == 200 and _tc.json().get("is_completed"):
                    _task_completed = True
                    logger.info(f"Assignee notification suppressed: task {task_id_raw} already completed")
            except:
                pass
        if not _task_completed:
            assignee_chat = get_chat_id_for_kommo_user(responsible_id)
            assignee_name = KOMMO_USERS.get(responsible_id, "Əməkdaş")
            creator_name = KOMMO_USERS.get(created_by, "Kommo") if created_by else "Kommo"
            name_line = f"\n👤 {client_name}" if client_name else ""
            phone_line = f"\n📞 {client_phone}" if client_phone else ""
            deadline_line = f"\n⏰ {deadline_str}" if deadline_str else ""
            link_line = f"\n🔗 {link}" if link else ""
            type_line = f"\n📌 {task_type_name}" if task_type_name else ""
            if assignee_chat:
                try:
                    sent = await _bot_app.bot.send_message(
                        assignee_chat,
                        f"📋 Yeni tapşırıq ({creator_name}):\n\n📝 {task_text}{type_line}{name_line}{phone_line}{deadline_line}{note_line}{link_line}",
                        disable_web_page_preview=True
                    )
                    if sent and task_id_raw:
                        store_message_task(assignee_chat, sent.message_id, int(task_id_raw), task_text,
                                           entity_id=entity_id, entity_type=entity_type, phone=client_phone)
                except:
                    pass
    # Notify admin about new tasks (skip if bot/admin created it or task already completed)
    if is_add and admin_chat and created_by != 10932455:
        # Double-check: verify task is not already completed via API
        _skip_notify = False
        if task_id_raw:
            try:
                _t_check = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id_raw}", headers=HEADERS, timeout=8)
                if _t_check.status_code == 200:
                    _t_json = _t_check.json()
                    if _t_json.get("is_completed"):
                        logger.info(f"Webhook suppressed: task {task_id_raw} already completed")
                        _skip_notify = True
            except:
                pass
        if not _skip_notify:
            name_line = f"\n👤 {client_name}" if client_name else ""
            phone_line = f"\n📞 {client_phone}" if client_phone else ""
            deadline_line = f"\n⏰ {deadline_str}" if deadline_str else ""
            link_line = f"\n🔗 {link}" if link else ""
            responsible_name = KOMMO_USERS.get(responsible_id, "") if responsible_id else ""
            resp_line = f"\n👤 Məsul: {responsible_name}" if responsible_name else ""
            type_line = f"\n📌 {task_type_name}" if task_type_name else ""
            creator_name_wh = KOMMO_USERS.get(created_by, "") if created_by else ""
            creator_line = f"\n👤 {creator_name_wh} → {responsible_name}" if creator_name_wh and responsible_name else resp_line
            try:
                await _bot_app.bot.send_message(
                    admin_chat,
                    f"📋 Kommo-da yeni tap\u015f\u0131r\u0131q:\n\n📝 {task_text}{type_line}{name_line}{phone_line}{deadline_line}{creator_line}{note_line}{link_line}",
                    disable_web_page_preview=True
                )
            except:
                pass


def _kommo_form_message_rows(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        return []
    message = data.get("message")
    if isinstance(message, dict) and isinstance(message.get("add"), list):
        return [row for row in message["add"] if isinstance(row, dict)]
    grouped: dict[int, dict] = {}
    for key, value in data.items():
        match = re.match(r"^message\[add\]\[(\d+)\]\[(.+)\]$", str(key))
        if not match:
            continue
        grouped.setdefault(int(match.group(1)), {})[match.group(2)] = value
    return [grouped[idx] for idx in sorted(grouped)]


def _incoming_message_preview(text: str, message_type: str) -> str:
    raw = str(text or "").strip()
    if _is_media_notice_text(raw):
        return "📷 Şəkil"
    clean = " ".join(raw.split())[:140]
    if clean:
        return clean
    kind = str(message_type or "").strip().lower()
    if kind in {"picture", "image", "sticker"}:
        return "Şəkil"
    if kind in {"voice", "audio", "ptt"}:
        return "Səs mesajı"
    if kind == "video":
        return "Video"
    if kind in {"file", "document"}:
        return "Fayl"
    return "Yeni mesaj"


_seen_incoming_ids: dict[str, float] = {}
_seen_incoming_lock = threading.Lock()


def _incoming_message_seen(message_id: str) -> bool:
    """True when this Kommo message was already announced. Retries must not notify again."""
    mid = str(message_id or "").strip()
    if not mid:
        return False
    now = _time_module.time()
    with _seen_incoming_lock:
        prev = _seen_incoming_ids.get(mid)
        if prev and now - prev < 86400:
            return True
        _seen_incoming_ids[mid] = now
        if len(_seen_incoming_ids) > 4000:
            cutoff = now - 86400
            for old in [key for key, ts in _seen_incoming_ids.items() if ts < cutoff]:
                _seen_incoming_ids.pop(old, None)
        return False


def _apply_inbox_incoming(
    lead_id: int,
    preview: str,
    created_at: int,
    origin: str,
    *,
    pipeline_id: int = 0,
    contact_name: str = "",
    phone: str = "",
) -> bool:
    """Update one cached chat from a Kommo incoming-message webhook. No funnel rebuild."""
    global _inbox_pulse_rev
    channel = _origin_channel_key(origin)
    found = False
    event_pipe = int(pipeline_id or 0)
    event_name = str(contact_name or "")
    event_phone = str(phone or "")
    for overview in _personal_overview_cache.values():
        if not isinstance(overview, dict):
            continue
        for deal in overview.get("deals") or []:
            if not isinstance(deal, dict):
                continue
            try:
                current_id = int(deal.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if current_id != int(lead_id):
                continue
            deal["last_client_message"] = preview
            deal["last_incoming_at"] = int(created_at or 0)
            try:
                chat_at = int(deal.get("chat_at") or 0)
            except (TypeError, ValueError):
                chat_at = 0
            deal["chat_at"] = max(chat_at, int(created_at or 0))
            if channel and not str(deal.get("chat_channel") or "").strip():
                deal["chat_channel"] = channel
            if not event_pipe:
                try:
                    event_pipe = int(deal.get("pipeline_id") or 0)
                except (TypeError, ValueError):
                    event_pipe = 0
            event_name = str(deal.get("contact_name") or event_name or "")
            event_phone = str(deal.get("phone") or event_phone or "")
            found = True
    with _inbox_pulse_lock:
        _inbox_pulse_rev += 1
        _inbox_pulse_events.append({
            "rev": _inbox_pulse_rev,
            "lead_id": int(lead_id),
            "type": "incoming_message",
            "pipeline_id": event_pipe,
            "contact_name": event_name,
            "phone": event_phone,
            "last_client_message": preview,
            "last_incoming_at": int(created_at or 0),
            "chat_channel": channel,
            "missing": not found,
            "refresh": False,
        })
        if len(_inbox_pulse_events) > 150:
            del _inbox_pulse_events[:-150]
    try:
        _invalidate_deal_chat_cache(int(lead_id))
    except Exception:
        pass
    return found


def _flag_inbox_refresh(lead_id: int) -> None:
    global _inbox_pulse_rev
    with _inbox_pulse_lock:
        _inbox_pulse_rev += 1
        _inbox_pulse_events.append({
            "rev": _inbox_pulse_rev,
            "lead_id": int(lead_id),
            "type": "deal_refresh",
            "missing": True,
            "refresh": True,
        })
        if len(_inbox_pulse_events) > 150:
            del _inbox_pulse_events[:-150]


def _minimal_inbox_deal(lead: dict, preview: str, created_at: int, channel: str) -> dict:
    _ids, phones = _contact_ids_and_phones(lead)
    try:
        lid = int(lead.get("id") or 0)
    except (TypeError, ValueError):
        lid = 0
    name = str(lead.get("name") or "").strip() or (phones[0] if phones else "Müştəri")
    try:
        pipe = int(lead.get("pipeline_id") or 0)
    except (TypeError, ValueError):
        pipe = 0
    return {
        "id": lid,
        "pipeline_id": pipe,
        "contact_name": name,
        "phone": phones[0] if phones else "",
        "phones": phones,
        "last_client_message": preview,
        "last_incoming_at": int(created_at or 0),
        "chat_at": int(created_at or 0),
        "chat_channel": channel or "whatsapp",
        "updated_at": int(created_at or 0),
        "created_at": int(lead.get("created_at") or 0),
        "stage_key": "",
        "stage_name": "",
        "tasks": [],
        "inbox_only": True,
    }


def _place_inbox_deal(lead: dict, preview: str, created_at: int, channel: str) -> bool:
    try:
        lid = int(lead.get("id") or 0)
        pipe = int(lead.get("pipeline_id") or 0)
    except (TypeError, ValueError):
        return False
    if not lid:
        return False
    targets: list[int] = []
    if pipe and isinstance(_personal_overview_cache.get(pipe), dict):
        targets.append(pipe)
    nizami = int(NIZAMI_PIPELINE_ID)
    if isinstance(_personal_overview_cache.get(nizami), dict):
        if pipe in {nizami, int(SOVDELESMELER_PIPELINE_ID)} or channel == "whatsapp":
            if nizami not in targets:
                targets.append(nizami)
    placed = False
    row = _minimal_inbox_deal(lead, preview, created_at, channel)
    for target in targets:
        overview = _personal_overview_cache.get(target) or {}
        deals = overview.get("deals")
        if not isinstance(deals, list):
            continue
        if any(isinstance(item, dict) and int(item.get("id") or 0) == lid for item in deals):
            placed = True
            continue
        deals.append(row)
        placed = True
    if placed:
        _schedule_chat_tail_warm([lid])
    return placed


def _pulse_event_visible(user_pipeline: int, row: dict, visible: dict, is_admin_user: bool = False) -> bool:
    if is_admin_user:
        return True
    try:
        lead_id = int(row.get("lead_id") or 0)
        event_pipe = int(row.get("pipeline_id") or 0)
    except (TypeError, ValueError):
        return False
    if lead_id and lead_id in visible:
        return True
    channel = str(row.get("chat_channel") or "")
    nizami = int(NIZAMI_PIPELINE_ID)
    rufat = int(RUFAT_PIPELINE_ID)
    if channel == "whatsapp":
        if not event_pipe or event_pipe == int(user_pipeline):
            return True
        if int(user_pipeline) == rufat and event_pipe in {rufat, int(SOVDELESMELER_PIPELINE_ID)}:
            return True
        if int(user_pipeline) == nizami and event_pipe in {nizami, int(SOVDELESMELER_PIPELINE_ID)}:
            return True
    if int(user_pipeline) == nizami:
        if event_pipe in {nizami, int(SOVDELESMELER_PIPELINE_ID)}:
            return True
        return channel == "whatsapp"
    return bool(event_pipe and event_pipe == int(user_pipeline))


async def _hydrate_inbox_lead(
    lead_id: int,
    preview: str,
    created_at: int,
    origin: str,
    talk_id: int = 0,
    message_id: str = "",
) -> None:
    try:
        lead = await asyncio.to_thread(get_lead_details, int(lead_id))
    except Exception as exc:
        logger.warning("Inbox hydrate failed lead=%s: %s", lead_id, exc)
        lead = None
    channel = _origin_channel_key(origin) or "whatsapp"
    pipe = 0
    name = ""
    phone = ""
    if isinstance(lead, dict):
        _place_inbox_deal(lead, preview, created_at, channel)
        try:
            pipe = int(lead.get("pipeline_id") or 0)
        except (TypeError, ValueError):
            pipe = 0
        _ids, phones = _contact_ids_and_phones(lead)
        name = str(lead.get("name") or "").strip()
        phone = phones[0] if phones else ""
    _apply_inbox_incoming(
        int(lead_id), preview, created_at, origin,
        pipeline_id=pipe, contact_name=name, phone=phone,
    )
    _capture_incoming_tail(
        int(lead_id), preview, created_at, origin,
        talk_id=talk_id, message_id=message_id,
    )
    _invalidate_deal_chat_cache(int(lead_id))
    record_lead_pulse_event(int(lead_id), "incoming_message", preview=preview, incoming_at=created_at)


async def _hydrate_inbox_contact(
    contact_id: int,
    preview: str,
    created_at: int,
    origin: str,
    talk_id: int = 0,
    message_id: str = "",
) -> None:
    cached = _cached_lead_id_for_contact(int(contact_id))
    if cached:
        await _hydrate_inbox_lead(cached, preview, created_at, origin, talk_id, message_id)
        return
    try:
        leads = await asyncio.to_thread(_leads_linked_to_contact, int(contact_id))
    except Exception as exc:
        logger.warning("Inbox contact hydrate failed contact=%s: %s", contact_id, exc)
        leads = []
    lead_id = 0
    for item in leads or []:
        try:
            lead_id = int(item.get("id") if isinstance(item, dict) else item)
        except (TypeError, ValueError, AttributeError):
            continue
        if lead_id:
            break
    if lead_id:
        await _hydrate_inbox_lead(lead_id, preview, created_at, origin, talk_id, message_id)


_USER_SEEN_FILE = "user_seen_leads.json"
_user_seen_lock = threading.Lock()


def _get_user_seen_map(chat_id: int) -> dict[str, int]:
    try:
        data = read_json(_USER_SEEN_FILE) or {}
        if not isinstance(data, dict):
            return {}
        cid = int(chat_id or 0)
        target_cids = RUFAT_COMPAT_CHAT_IDS if cid in RUFAT_COMPAT_CHAT_IDS else {cid}
        merged: dict[str, int] = {}
        for c in target_cids:
            user_data = data.get(str(c)) or {}
            if isinstance(user_data, dict):
                for k, v in user_data.items():
                    merged[str(k)] = max(int(merged.get(str(k)) or 0), int(v or 0))
        return merged
    except Exception:
        return {}


def _record_user_seen(chat_id: int, lead_id: int, seen_ts: int = 0) -> None:
    try:
        lid = int(lead_id or 0)
        cid = int(chat_id or 0)
        if not lid or not cid:
            return
        ts = int(seen_ts or _time_module.time())
        target_cids = RUFAT_COMPAT_CHAT_IDS if cid in RUFAT_COMPAT_CHAT_IDS else {cid}
        with _user_seen_lock:
            data = read_json(_USER_SEEN_FILE) or {}
            if not isinstance(data, dict):
                data = {}
            for c in target_cids:
                user_data = data.get(str(c)) or {}
                if not isinstance(user_data, dict):
                    user_data = {}
                user_data[str(lid)] = ts
                data[str(c)] = user_data
            write_json(_USER_SEEN_FILE, data)
        record_lead_pulse_event(lid, "deal_seen", preview="", incoming_at=0)
    except Exception as exc:
        logger.warning("Failed to record user seen lead=%s: %s", lead_id, exc)


def _inbox_pulse_payload(chat_id: int, since_rev: int) -> dict:
    is_admin_user = is_admin(chat_id)
    owner = get_funnel_owner(chat_id) or {}
    try:
        pipeline_id = int(owner.get("pipeline_id") or 0)
    except (TypeError, ValueError):
        pipeline_id = 0
    overview = _personal_overview_cache.get(pipeline_id) or {}
    visible: dict[int, dict] = {}
    for deal in overview.get("deals") or []:
        if not isinstance(deal, dict):
            continue
        try:
            lid = int(deal.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if lid:
            visible[lid] = deal
    with _inbox_pulse_lock:
        rev = int(_inbox_pulse_rev)
        fresh = [row for row in _inbox_pulse_events if int(row.get("rev") or 0) > int(since_rev or 0)]
    chats = []
    events = []
    seen_chats: set[int] = set()
    for row in reversed(fresh):
        try:
            lid = int(row.get("lead_id") or 0)
        except (TypeError, ValueError):
            continue
        if not lid or not _pulse_event_visible(pipeline_id, row, visible, is_admin_user=is_admin_user):
            continue
        deal = visible.get(lid) or {}
        incoming_at = int(row.get("last_incoming_at") or deal.get("last_incoming_at") or 0)
        events.append({
            "rev": int(row.get("rev") or 0),
            "lead_id": lid,
            "type": str(row.get("type") or "deal_update"),
            "pipeline_id": int(row.get("pipeline_id") or 0),
            "stage_key": str(row.get("stage_key") or ""),
            "contact_name": deal.get("contact_name") or row.get("contact_name") or "",
            "phone": deal.get("phone") or row.get("phone") or "",
        })
        if lid not in seen_chats and (row.get("last_client_message") or row.get("chat_channel")):
            seen_chats.add(lid)
            chats.append({
                "id": lid,
                "contact_name": deal.get("contact_name") or row.get("contact_name") or "",
                "phone": deal.get("phone") or row.get("phone") or "",
                "last_client_message": row.get("last_client_message") or deal.get("last_client_message") or "",
                "last_incoming_at": incoming_at,
                "chat_at": max(int(deal.get("chat_at") or 0), incoming_at),
                "chat_channel": row.get("chat_channel") or deal.get("chat_channel") or "",
            })
    return {
        "success": True,
        "rev": rev,
        "chats": chats,
        "events": events,
        "seen": _get_user_seen_map(chat_id),
        "refresh": False,
    }


async def handle_api_chats_pulse(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    if not is_funnel_chat(chat_id) and not is_admin(chat_id):
        return web.json_response({"success": False, "error": "Access denied"}, status=403)
    try:
        since_rev = int(request.rel_url.query.get("rev") or 0)
    except (TypeError, ValueError):
        since_rev = 0
    return web.json_response(_inbox_pulse_payload(chat_id, since_rev))


async def handle_kommo_webhook(request: web.Request) -> web.Response:
    """Handle incoming Kommo webhooks."""
    try:
        data = {}
        if "json" in str(request.content_type or ""):
            try:
                parsed = await request.json()
                data = parsed if isinstance(parsed, dict) else {}
            except Exception:
                data = {}
        if not data:
            data = dict(await request.post())
        logger.info(f"Webhook received: {list(data.keys())[:10]}")
        # Emit incremental pulse events for any lead events in webhook
        for k in data:
            m = re.match(r"^leads\[(status|update|add)\]\[(\d+)\]\[id\]$", str(k))
            if m:
                ev_kind = m.group(1)
                try:
                    wh_lid = int(data[k])
                    wh_pipe = 0
                    pipe_k = f"leads[{ev_kind}][{m.group(2)}][pipeline_id]"
                    if pipe_k in data:
                        try:
                            wh_pipe = int(data[pipe_k])
                        except:
                            pass
                    record_lead_pulse_event(wh_lid, f"deal_{ev_kind}", pipeline_id=wh_pipe)
                except:
                    pass
        incoming_rows = [
            row for row in _kommo_form_message_rows(data)
            if str(row.get("type") or "incoming").strip().lower() != "outgoing"
        ]
        if incoming_rows:
            for row in incoming_rows:
                entity_type = str(row.get("entity_type") or row.get("element_type") or "lead").strip().lower()
                try:
                    entity_id = int(row.get("entity_id") or row.get("element_id") or 0)
                except (TypeError, ValueError):
                    entity_id = 0
                if not entity_id:
                    continue
                try:
                    created_at = int(row.get("created_at") or 0)
                except (TypeError, ValueError):
                    created_at = 0
                if not created_at:
                    created_at = int(_time_module.time())
                preview = _incoming_message_preview(str(row.get("text") or ""), str(row.get("message_type") or ""))
                origin = str(row.get("origin") or "")
                attachment = row.get("attachment") if isinstance(row.get("attachment"), dict) else {}
                file_uuid = _extract_file_uuid(row) or _extract_file_uuid(attachment)
                media_url = _extract_media_url(row) or _extract_media_url(attachment)
                mtype = str(row.get("message_type") or row.get("type") or "").lower()
                raw_text = str(row.get("text") or "")
                if _is_media_notice_text(raw_text):
                    mtype = "picture"
                try:
                    talk_id = int(row.get("talk_id") or 0)
                except (TypeError, ValueError):
                    talk_id = 0
                message_id = str(row.get("id") or row.get("msgid") or row.get("message_id") or "").strip()
                if _incoming_message_seen(message_id):
                    continue
                if entity_type in {"contact", "contacts", "1"}:
                    mapped = _cached_lead_id_for_contact(entity_id)
                    if mapped:
                        _capture_incoming_tail(
                            mapped, preview, created_at, origin,
                            talk_id=talk_id, message_id=message_id,
                            media_url=media_url, file_uuid=file_uuid, message_type=mtype,
                        )
                        _invalidate_deal_chat_cache(mapped)
                        record_lead_pulse_event(mapped, "incoming_message", preview=preview, incoming_at=created_at)
                        if not _apply_inbox_incoming(mapped, preview, created_at, origin):
                            asyncio.create_task(_hydrate_inbox_lead(
                                mapped, preview, created_at, origin, talk_id, message_id,
                            ))
                        continue
                    asyncio.create_task(_hydrate_inbox_contact(
                        entity_id, preview, created_at, origin, talk_id, message_id,
                    ))
                    continue
                if entity_type and entity_type not in {"lead", "2", "leads"}:
                    continue
                _capture_incoming_tail(
                    entity_id, preview, created_at, origin,
                    talk_id=talk_id, message_id=message_id,
                    media_url=media_url, file_uuid=file_uuid, message_type=mtype,
                )
                _invalidate_deal_chat_cache(entity_id)
                record_lead_pulse_event(entity_id, "incoming_message", preview=preview, incoming_at=created_at)
                if not _apply_inbox_incoming(entity_id, preview, created_at, origin):
                    asyncio.create_task(_hydrate_inbox_lead(
                        entity_id, preview, created_at, origin, talk_id, message_id,
                    ))
            return web.Response(status=200, text="OK")
        is_task_event = any(k.startswith(("tasks[", "task[")) for k in data.keys())
        has_lead_event = any(k.startswith(("leads[status][0]", "leads[add][0]")) for k in data.keys())
        if is_task_event or has_lead_event:
            asyncio.create_task(_process_kommo_webhook_background(data))
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}\n{traceback.format_exc()}")
        return web.Response(status=200, text="OK")


async def _process_kommo_webhook_background(data: dict):
    try:
        # Detect event type
        is_task_event = any(k.startswith(("tasks[", "task[")) for k in data.keys())
        if is_task_event:
            await _handle_kommo_task_webhook(data)
            return
        add_lead_id = data.get("leads[add][0][id]")
        if add_lead_id:
            try:
                maybe_fill_menbe_from_lead(int(add_lead_id))
                invalidate_rufat_overview_cache()
            except Exception as exc:
                logger.warning("mənbə from new lead %s failed: %s", add_lead_id, exc)
        # Lead status change
        lead_keys = [k for k in data.keys() if k.startswith("leads[status][0]")]
        if not lead_keys:
            return
        lead_id = data.get("leads[status][0][id]")
        old_status_id = data.get("leads[status][0][old_status_id]")
        new_status_id = data.get("leads[status][0][status_id]")
        pipeline_id = data.get("leads[status][0][pipeline_id]")
        if not lead_id or not new_status_id:
            return
        lead_id = int(lead_id)
        old_status_id = int(old_status_id) if old_status_id else 0
        new_status_id = int(new_status_id)
        pipeline_id = int(pipeline_id) if pipeline_id else 0
        try:
            old_pipeline_id = int(data.get("leads[status][0][old_pipeline_id]") or 0)
        except (TypeError, ValueError):
            old_pipeline_id = 0
        if pipeline_id in employee_personal_pipeline_ids():
            if old_pipeline_id and pipeline_id != old_pipeline_id:
                maybe_reassign_open_tasks_for_employee_funnel(lead_id, pipeline_id)
            if pipeline_id == RUFAT_PIPELINE_ID:
                rufat_chat = NAME_TO_CHAT.get("Rüfət Həsənzadə")
                if rufat_chat and _bot_app:
                    rufat_stage = RUFAT_STAGE_NAMES.get(new_status_id, "Naməlum")
                    rufat_lead = get_lead_details(lead_id) or {}
                    rufat_msg = (f"🔄 Sizin vоронкаda mərhələ dəyişdi:\n\n"
                                  f"👤 {rufat_lead.get('name', lead_id)}\n📋 {rufat_lead.get('name', '')}\n📌 {rufat_stage}\n🔗 {KOMMO_BASE_URL}/leads/detail/{lead_id}")
                    try:
                        await _bot_app.bot.send_message(rufat_chat, rufat_msg, disable_web_page_preview=True)
                        send_push_notification(str(rufat_chat), "🔄 Mərhələ dəyişdi", f"{rufat_lead.get('name', lead_id)} — {rufat_stage}")
                    except Exception:
                        pass
            return
        if pipeline_id not in (PIPELINE_ID, RUFAT_PIPELINE_ID):
            return
        # Suppress webhook echo when bot itself changed the stage
        import time as _time
        if lead_id in _bot_changed_leads:
            if _time.time() - _bot_changed_leads[lead_id] < 120:
                logger.info(f"Webhook suppressed: bot-initiated stage change for lead {lead_id}")
                return
            else:
                del _bot_changed_leads[lead_id]
        # Notify only the target stage; ignore all other Sövdələşmələr stage changes.
        if new_status_id != NOTIFY_STAGE_ID:
            logger.info(f"Webhook ignored: stage {new_status_id} is not Nömrə alınıb")
            return
        # Deduplicate: same lead+stage within 60s = duplicate webhook
        import time as _time2
        _dedup_key = (lead_id, new_status_id)
        if _dedup_key in _webhook_stage_dedup and _time2.time() - _webhook_stage_dedup[_dedup_key] < 1800:
            logger.info(f"Webhook dedup: lead {lead_id} stage {new_status_id} already processed")
            return
        _webhook_stage_dedup[_dedup_key] = _time2.time()
        # Cleanup old dedup entries
        _cutoff = _time2.time() - 3600
        for k in list(_webhook_stage_dedup.keys()):
            if _webhook_stage_dedup[k] < _cutoff:
                del _webhook_stage_dedup[k]
        # Get lead details
        lead = get_lead_details(lead_id)
        lead_name = lead.get("name", "Adsız") if lead else "Adsız"
        contact_name = ""
        contact_phone = ""
        if lead:
            contacts_emb = lead.get("_embedded", {}).get("contacts", [])
            if contacts_emb:
                full_c = get_contact_details(contacts_emb[0]["id"])
                if full_c:
                    contact_name = full_c.get("name", "Adsız")
                    for cf in (full_c.get("custom_fields_values") or []):
                        if cf.get("field_code") == "PHONE":
                            vals = cf.get("values", [])
                            if vals:
                                contact_phone = vals[0].get("value", "")
                                break
        if not contact_name:
            contact_name = lead_name
        old_stage_name = STAGE_NAMES.get(old_status_id, "Naməlum")
        new_stage_name = STAGE_NAMES.get(new_status_id, "Naməlum")
        link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
        admin_chat = get_chat_id_for_kommo_user(10932455)
        if not admin_chat or not _bot_app:
            return
        # Qiymət təklifi - notification only (task creation is handled in API handler)
        if new_status_id == STAGES["qiymet_teklifi"]:
            msg = (f"💰 *Qiymət təklifi mərhələsinə keçdi:*\n\n"
                   f"👤 {contact_name}\n📞 {contact_phone}\n📋 {lead_name}\n🔗 {link}")
            try:
                sent = await _bot_app.bot.send_message(admin_chat, msg, parse_mode="Markdown", disable_web_page_preview=True)
                if sent:
                    store_message_lead(admin_chat, sent.message_id, lead_id, lead_name, contact_phone)
            except:
                pass
            return
        # Stages that require assignee selection + task creation
        _STAGE_TASK_KEYS = {
            STAGES["teqdimat"]: "teqdimat",
            STAGES["yeni_sifaris"]: "yeni_sifaris",
            STAGES["gorus"]: "gorus",
            STAGES["qurashdirma"]: "qurashdirma",
        }
        if new_status_id in _STAGE_TASK_KEYS:
            stage_key = _STAGE_TASK_KEYS[new_status_id]
            stage_display = STAGE_NAMES.get(new_status_id, new_stage_name)
            msg = (f"📋 *Mərhələ dəyişdi: {stage_display}*\n\n"
                   f"👤 {contact_name}\n📞 {contact_phone}\n🔗 {link}\n\nKim icra edəcək?")
            keyboard = [
                [
                    InlineKeyboardButton("Rüfət", callback_data=f"stgtask-{lead_id}-{stage_key}-rufat"),
                    InlineKeyboardButton("Soltan", callback_data=f"stgtask-{lead_id}-{stage_key}-soltan"),
                ],
                [
                    InlineKeyboardButton("Hüseyn", callback_data=f"stgtask-{lead_id}-{stage_key}-huseyn"),
                    InlineKeyboardButton("Rasim", callback_data=f"stgtask-{lead_id}-{stage_key}-rasim"),
                ],
                [
                    InlineKeyboardButton("Texniki", callback_data=f"stgtask-{lead_id}-{stage_key}-texniki"),
                    InlineKeyboardButton("Özüm", callback_data=f"stgtask-{lead_id}-{stage_key}-admin"),
                ],
                [
                    InlineKeyboardButton("❌ Ləğv", callback_data=f"stgtask-{lead_id}-{stage_key}-cancel"),
                ],
            ]
            sent = None
            try:
                sent = await _bot_app.bot.send_message(
                    admin_chat, msg, parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True
                )
                if sent:
                    store_message_lead(admin_chat, sent.message_id, lead_id, lead_name, contact_phone)
            except Exception as e:
                logger.error(f"Webhook stage-task error: {e}")
            save_pending_action("assign_executor", {
                "contact_name": contact_name,
                "phone": contact_phone,
                "lead_id": lead_id,
                "task_text": f"Mərhələ: {stage_display}",
                "stage_key": stage_key,
                "stage_name": stage_display,
                "link": link,
            }, ["Rüfət", "Soltan", "Hüseyn", "Rasim", "Texniki", "Özüm", "Ləğv et"])
            send_push_to_admin(f"Mərhələ dəyişdi: {stage_display} - {contact_name}", title="📋 İcraçı seçimi")
        else:
            # Plain notification
            msg = (f"🔄 *Mərhələ dəyişikliyi:*\n\n👤 {contact_name}\n📞 {contact_phone}\n"
                   f"📋 {lead_name}\n📌 {old_stage_name} → *{new_stage_name}*\n🔗 {link}")
            try:
                sent = await _bot_app.bot.send_message(admin_chat, msg, parse_mode="Markdown", disable_web_page_preview=True)
                if sent:
                    store_message_lead(admin_chat, sent.message_id, lead_id, lead_name, contact_phone)
            except:
                pass
    except Exception as exc:
        logger.error(f"Background webhook processing error: {exc}\n{traceback.format_exc()}")
    except Exception as e:
        logger.error(f"Webhook error: {e}\n{traceback.format_exc()}")
        return web.Response(status=200, text="OK")

async def health_check(request: web.Request) -> web.Response:
    at = int(_WA_LAST_HOOK.get("at") or 0)
    hook = f"hook={max(0, int(_time_module.time()) - at)}s" if at else "hook=none"
    incoming = f"in={int(_WA_LAST_HOOK.get('in') or 0)}"
    lead = int(_WA_LAST_HOOK.get("lead") or 0)
    lead_part = f" lead={lead}" if lead else ""
    sub = str(_WA_LAST_HOOK.get("sub") or "").strip()
    sub_part = f" sub={sub}" if sub else ""
    return web.Response(status=200, text=f"Bot is running v252 {hook} {incoming}{lead_part}{sub_part}")


async def handle_get_pending_actions(request: web.Request) -> web.Response:
    chat_id = request.rel_url.query.get("chat_id") or request.headers.get("X-TG-User-ID", "")
    if not is_admin(chat_id):
        return web.json_response({"error": "Unauthorized"}, status=403)
    actions = [action for action in get_pending_actions() if not action.get("resolved")]
    # Inject voice_url for each action
    for a in actions:
        eid = str(a.get("data", {}).get("lead_id") or a.get("data", {}).get("entity_id") or "")
        if eid and eid in _voice_urls:
            a.setdefault("data", {})["voice_url"] = f"/api/voice/{eid}"
    return web.json_response(actions)


async def handle_resolve_action(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "message": "Invalid JSON"}, status=400)
    chat_id = data.get("chat_id") or request.headers.get("X-TG-User-ID", "")
    if not is_admin(chat_id):
        return web.json_response({"error": "Unauthorized"}, status=403)
    action_id = data.get("id")
    choice = data.get("choice")
    kpi_score = data.get("kpi_score", 0)
    stars = data.get("stars", 0)
    amount = data.get("amount", 0)
    if not action_id or not choice:
        return web.json_response({"success": False, "message": "Sorğu və seçim tələb olunur."}, status=400)
    user_agent = request.headers.get("User-Agent", "unknown")
    logger.info(f"RESOLVE_ACTION: id={action_id}, choice={choice}, amount={amount}, chat_id={chat_id}, UA={user_agent[:80]}")
    try:
        stars = int(stars or 0)
    except (TypeError, ValueError):
        stars = 0
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        amount = 0
    success, message = resolve_pending_action(
        str(action_id), str(choice), kpi_score=int(kpi_score) if kpi_score else 0, stars=stars, amount=amount
    )
    return web.json_response({"success": success, "message": message})


async def handle_delete_pending_action(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "message": "Invalid JSON"}, status=400)
    chat_id = data.get("chat_id") or request.headers.get("X-TG-User-ID", "")
    if not is_admin(chat_id):
        return web.json_response({"error": "Unauthorized"}, status=403)
    action_id = data.get("id")
    if not action_id:
        return web.json_response({"success": False, "message": "Sorğu ID-si tələb olunur."}, status=400)
    if not delete_pending_action(str(action_id)):
        return web.json_response({"success": False, "message": "Sorğu tapılmadı və ya silinmədi."}, status=404)
    return web.json_response({"success": True})


async def handle_reject_pending_action(request: web.Request) -> web.Response:
    """Admin rejects a pending action (\u0130mtina) and notifies the executor to edit/resubmit."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "message": "Invalid JSON"}, status=400)
    chat_id = data.get("chat_id") or request.headers.get("X-TG-User-ID", "")
    if not is_admin(chat_id):
        return web.json_response({"error": "Unauthorized"}, status=403)
    action_id = data.get("id")
    if not action_id:
        return web.json_response({"success": False, "message": "Sor\u011fu ID-si t\u0259l\u0259b olunur."}, status=400)
    # Find the action
    actions = get_pending_actions()
    action = next((a for a in actions if a.get("id") == str(action_id) and not a.get("resolved")), None)
    if not action:
        return web.json_response({"success": False, "message": "Sor\u011fu tap\u0131lmad\u0131."}, status=404)
    # Mark as resolved with \u0130mtina
    mark_pending_action_resolved(action_id=str(action_id), choice="\u0130mtina")
    # Notify the executor
    action_data = action.get("data") or {}
    sender_name = action_data.get("sender_name", "")
    contact_name = action_data.get("contact_name", "")
    task_text = action_data.get("task_text", "")
    creator_chat_id = action_data.get("creator_chat_id")
    # Find executor chat_id
    target_chat = None
    if creator_chat_id:
        target_chat = int(creator_chat_id)
    elif sender_name:
        target_chat = get_chat_id_by_name(sender_name)
    if target_chat and _bot_app:
        reject_msg = f"\u274c Admin sor\u011funuzu r\u0259dd etdi (\u0130mtina):\n\n\ud83d\udc64 {contact_name}\n\ud83d\udcdd {task_text}\n\n\u270f\ufe0f Z\u0259hm\u0259t olmasa redakt\u0259 edib yenid\u0259n g\u00f6nd\u0259rin."
        try:
            await _bot_app.bot.send_message(target_chat, reject_msg)
        except Exception as e:
            logger.error(f"Reject notify error: {e}")
    return web.json_response({"success": True, "message": "\u0130mtina edildi."})


async def handle_pending_change_stage(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "message": "Invalid JSON"}, status=400)
    if not is_admin(data.get("chat_id") or request.headers.get("X-TG-User-ID", "")):
        return web.json_response({"error": "Unauthorized"}, status=403)
    action_id = data.get("id")
    stage_name = data.get("stage")
    action = next((a for a in get_pending_actions() if a["id"] == action_id), None)
    if not action:
        return web.json_response({"success": False, "message": "Sorğu tapılmadı."}, status=404)
    lead_id = action.get("data", {}).get("lead_id")
    status_id = next((sid for sid, dn in STAGE_NAMES.items() if dn.casefold() == stage_name.casefold()), None)
    if not lead_id:
        return web.json_response({"success": False, "message": "Lead ID tapılmadı. Köhnə sorğu ola bilər."})
    if not status_id:
        return web.json_response({"success": False, "message": f"Mərhələ tapılmadı: {stage_name}"})
    if not update_lead_kommo(int(lead_id), {"status_id": int(status_id), "pipeline_id": PIPELINE_ID}):
        return web.json_response({"success": False, "message": "Kommo xətası."})
    return web.json_response({"success": True, "message": f"Mərhələ dəyişdirildi: {stage_name}"})

async def handle_pending_change_executor(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "message": "Invalid JSON"}, status=400)
    if not is_admin(data.get("chat_id") or request.headers.get("X-TG-User-ID", "")):
        return web.json_response({"error": "Unauthorized"}, status=403)
    action_id = data.get("id")
    executor = data.get("executor")
    action = next((a for a in get_pending_actions() if a["id"] == action_id), None)
    if not action:
        return web.json_response({"success": False, "message": "Sorğu tapılmadı."}, status=404)
    task_id = action.get("data", {}).get("task_id")
    if not task_id:
        # Try to find open task from lead_id
        lead_id = action.get("data", {}).get("lead_id")
        if lead_id:
            try:
                lead_tasks = get_entity_tasks(int(lead_id), "leads")
                open_tasks = [t for t in lead_tasks if not t.get("is_completed")]
                if open_tasks:
                    open_tasks.sort(key=lambda t: t.get("complete_till", 0))
                    task_id = open_tasks[0]["id"]
            except:
                pass
    if not task_id:
        return web.json_response({"success": False, "message": "Tapşırıq ID tapılmadı."})
    marker_info = _UPD_MARKER.get(executor)
    update_data = {}
    if executor == "Özüm":
        update_data["responsible_user_id"] = 10932455
    elif marker_info:
        full_name, _ = marker_info
        # Sahə Meneceri is retired; route legacy employee choices to Admin.
        update_data["responsible_user_id"] = 10932455
        old_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=HEADERS)
        if old_resp.status_code == 200:
            old_text = old_resp.json().get("text", "")
            new_text = re.sub(r"^\[[^\]]+\]\s*", "", old_text)
            update_data["text"] = f"[{full_name}] {new_text}"
    else:
        return web.json_response({"success": False, "message": "İcraçı tanınmadı."})
    if not update_task_kommo(int(task_id), update_data):
        return web.json_response({"success": False, "message": "Yeniləmə uğursuz oldu."})
    return web.json_response({"success": True, "message": f"İcraçı dəyişdirildi: {executor}"})

async def handle_api_action(request: web.Request) -> web.Response:
    """Handle Web App API requests (fetch-based SPA)."""
    try:
        data = await request.json()
    except:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)
    tg_user_id = request.headers.get("X-TG-User-ID", "")
    # Find chat_id from telegram user id
    chat_id = None
    users = load_users()
    for cid, info in users.items():
        # Telegram user ID in header matches chat_id for private chats
        if cid == tg_user_id:
            chat_id = int(cid)
            break
    if not chat_id:
        try:
            chat_id = int(tg_user_id)
        except (TypeError, ValueError):
            chat_id = None
    if not chat_id:
        return web.json_response({"success": False, "error": "İstifadəçi tapılmadı. Botda /start yazın."}, status=403)
    action = data.get("action", "")
    phone = data.get("phone", "")
    try:
        if action == "deal_edit":
            lead_id = int(data.get("lead_id") or 0)
            if not lead_id or not lead_allowed_for_chat(lead_id, chat_id):
                return web.json_response({"success": False, "error": "Доступ запрещён."}, status=403)
            stage_key = str(data.get("stage_key") or "")
            lead = get_lead_details(lead_id) or {}
            pipeline_id = _lead_pipeline_id(lead) or get_pipeline_id_for_chat(chat_id)
            if is_admin(chat_id):
                try:
                    requested_pipeline = int(data.get("pipeline_id") or 0)
                except (TypeError, ValueError):
                    requested_pipeline = 0
                if requested_pipeline:
                    pipeline_id = requested_pipeline
                stages, _names, _ui = load_pipeline_stage_maps(pipeline_id, fallback=False)
            else:
                stages, _names, _ui = load_pipeline_stage_maps(pipeline_id) if pipeline_id in all_personal_pipeline_ids() else ({}, {}, [])
            if stage_key not in stages:
                return web.json_response({"success": False, "error": "Mərhələ tapılmadı."})
            source_label = str(data.get("source") or data.get("menbe") or "").strip()
            partner_name = str(data.get("partner") or "").strip()
            if not update_lead_kommo(lead_id, {"status_id": stages[stage_key], "pipeline_id": pipeline_id}):
                return web.json_response({"success": False, "error": "Mərhələ dəyişdirilmədi."})
            if "partner" in data:
                set_deal_partner(lead_id, partner_name, chat_id)
                if partner_name:
                    add_partner_for_chat(chat_id, partner_name)
                invalidate_rufat_overview_cache()
            elif source_label:
                contacts = (lead.get("_embedded") or {}).get("contacts") or []
                contact_id = int(contacts[0]["id"]) if contacts and contacts[0].get("id") else None
                apply_menbe(contact_id, lead_id, source_label, overwrite=True)
                invalidate_rufat_overview_cache()
            patch_rufat_overview_deal_stage(lead_id, stage_key)
            record_lead_pulse_event(lead_id, "deal_edit", pipeline_id=pipeline_id, stage_key=stage_key)
            return web.json_response({
                "success": True,
                "message": "Sövdələşmə yeniləndi.",
                "stage_key": stage_key,
                "partner": get_deal_partner(lead_id),
                "partners": get_partner_list_for_chat(chat_id),
            })
        elif action == "add_partner":
            name = str(data.get("name") or data.get("partner") or "").strip()
            if not name:
                return web.json_response({"success": False, "error": "Partner adını daxil edin."})
            partners = add_partner_for_chat(chat_id, name)
            return web.json_response({"success": True, "partners": partners, "partner": name})
        elif action == "deal_add_note":
            lead_id, text = int(data.get("lead_id") or 0), str(data.get("text") or "").strip()
            if not lead_id or not text or not lead_allowed_for_chat(lead_id, chat_id):
                return web.json_response({"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."}, status=400)
            result = add_note(lead_id, text, "leads")
            invalidate_rufat_overview_cache()
            if result:
                record_lead_pulse_event(lead_id, "deal_add_note")
            return web.json_response({
                "success": bool(result),
                "note_id": _first_note_id(result),
                "message": "Qeyd əlavə edildi." if result else "Qeyd əlavə olunmadı.",
            })
        elif action == "deal_edit_note":
            lead_id = int(data.get("lead_id") or 0)
            note_id = int(data.get("note_id") or 0)
            text = str(data.get("text") or "").strip()
            entity_type = str(data.get("entity_type") or "leads")
            try:
                entity_id = int(data.get("entity_id") or 0)
            except (TypeError, ValueError):
                entity_id = 0
            if not entity_id:
                entity_id = lead_id if _note_entity_kind(entity_type) == "leads" else 0
            if not lead_id or not note_id or not text or not lead_allowed_for_chat(lead_id, chat_id):
                return web.json_response({"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."}, status=400)
            ok, err = update_note(note_id, text, entity_type, entity_id)
            if ok:
                invalidate_rufat_overview_cache()
                record_lead_pulse_event(lead_id, "deal_edit_note")
            return web.json_response({
                "success": ok,
                "message": "Qeyd yeniləndi." if ok else "Qeyd yenilənmədi.",
                "error": "" if ok else err,
            }, status=200 if ok else 400)
        elif action == "deal_delete_note":
            lead_id = int(data.get("lead_id") or 0)
            note_id = int(data.get("note_id") or 0)
            entity_type = str(data.get("entity_type") or "leads")
            try:
                entity_id = int(data.get("entity_id") or 0)
            except (TypeError, ValueError):
                entity_id = 0
            if not entity_id:
                entity_id = lead_id if _note_entity_kind(entity_type) == "leads" else 0
            if not lead_id or not note_id or not lead_allowed_for_chat(lead_id, chat_id):
                return web.json_response({"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."}, status=400)
            ok, err = delete_note(note_id, entity_type, entity_id)
            if ok:
                invalidate_rufat_overview_cache()
                record_lead_pulse_event(lead_id, "deal_delete_note")
            return web.json_response({
                "success": ok,
                "message": "Qeyd silindi." if ok else "Qeyd silinmədi.",
                "error": "" if ok else err,
            }, status=200 if ok else 400)
        elif action == "deal_add_task":
            lead_id, text = int(data.get("lead_id") or 0), str(data.get("text") or "").strip()
            if not lead_id or not text or not lead_allowed_for_chat(lead_id, chat_id):
                return web.json_response({"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."}, status=400)
            try: deadline_ts = int(data.get("deadline_ts") or (datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
            except (TypeError, ValueError): deadline_ts = int((datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
            try:
                task_type_id = int(data.get("task_type_id") or data.get("task_type") or 4232112)
            except (TypeError, ValueError):
                task_type_id = 4232112
            if task_type_id not in {4232112, XATIRLAT_TASK_TYPE_ID}:
                task_type_id = 4232112
            executor = str(data.get("executor") or "Rüfət Həsənzadə").strip()
            executor_ids = {
                "Nizami Qasımov": 10932455,
                "Soltan Abbasov": 15531960,
                "Rüfət Həsənzadə": 10932455,
                "Hüseyn Səfərov": 10932455,
                "Rasim Əsgərov": 10932455,
                "Sərmayə Əhmədsoy": 10932455,
                "Asya Agayeva": 10932455,
                "Nuranə Şirinova": 10932455,
            }
            responsible_user_id = executor_ids.get(executor)
            if not responsible_user_id:
                return web.json_response({"success": False, "error": "İcraçı tanınmadı."}, status=400)
            owner = get_funnel_owner(chat_id)
            creator = (owner or {}).get("name") or get_employee_name_by_chat_id(chat_id, "Rüfət Həsənzadə")
            result = create_task(lead_id, text, deadline_ts, responsible_user_id=responsible_user_id, entity_type="leads", task_type_id=task_type_id, creator_name=creator)
            if result:
                executor_chat = get_chat_id_by_name(executor)
                try:
                    creator_chat = int(chat_id)
                except (TypeError, ValueError):
                    creator_chat = None
                if executor_chat and creator_chat and int(executor_chat) != creator_chat:
                    dl = datetime.fromtimestamp(deadline_ts, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M")
                    _send_telegram_text(
                        executor_chat,
                        f"📋 Yeni tapşırıq ({creator}):\n\n📝 {text}\n👤 {executor}\n⏰ {dl}\n🔗 {KOMMO_BASE_URL}/leads/detail/{lead_id}",
                    )
                    send_push_notification(str(executor_chat), "📋 Yeni tapşırıq!", f"{creator} → {text[:80]}")
            invalidate_rufat_overview_cache()
            if result:
                record_lead_pulse_event(lead_id, "deal_add_task")
            return web.json_response({
                "success": bool(result),
                "task_id": _created_task_id(result),
                "message": "Tapşırıq əlavə edildi." if result else "Tapşırıq əlavə olunmadı.",
            })
        elif action == "deal_edit_task":
            lead_id = int(data.get("lead_id") or 0)
            task_id = int(data.get("task_id") or 0)
            text = str(data.get("text") or "").strip()
            if not lead_id or not task_id or not text or not lead_allowed_for_chat(lead_id, chat_id):
                return web.json_response({"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."}, status=400)
            payload = {"text": text}
            try:
                deadline_ts = int(data.get("deadline_ts") or 0)
            except (TypeError, ValueError):
                deadline_ts = 0
            if deadline_ts:
                payload["complete_till"] = deadline_ts
            ok = bool(update_task_kommo(task_id, payload))
            if ok:
                invalidate_rufat_overview_cache()
                record_lead_pulse_event(lead_id, "deal_edit_task")
            return web.json_response({"success": ok, "message": "Tapşırıq yeniləndi." if ok else "Tapşırıq yenilənmədi."})
        elif action == "deal_delete_task":
            lead_id = int(data.get("lead_id") or 0)
            task_id = int(data.get("task_id") or 0)
            if not lead_id or not task_id or not lead_allowed_for_chat(lead_id, chat_id):
                return web.json_response({"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."}, status=400)
            ok = bool(update_task_kommo(task_id, {"is_completed": True, "result": {"text": "Silindi"}}))
            if ok:
                invalidate_rufat_overview_cache()
                record_lead_pulse_event(lead_id, "deal_delete_task")
            return web.json_response({"success": ok, "message": "Tapşırıq silindi." if ok else "Tapşırıq silinmədi."})
        elif action == "deal_edit_contact":
            lead_id = int(data.get("lead_id") or 0)
            name = str(data.get("name") or data.get("contact_name") or "").strip()
            phone = str(data.get("phone") or "").strip()
            if not lead_id or (not is_funnel_chat(chat_id) and not is_admin(chat_id) and not lead_allowed_for_chat(lead_id, chat_id)):
                return web.json_response({"success": False, "error": "Məlumat natamamdır və ya giriş yoxdur."}, status=400)
            contact_id = 0
            try:
                contact_id = int(data.get("contact_id") or 0)
            except (TypeError, ValueError):
                contact_id = 0
            payload: dict = {}
            if name:
                payload["name"] = name
            if phone:
                payload["custom_fields_values"] = [{"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}]
            if not payload:
                return web.json_response({"success": False, "error": "Ad və ya telefon yazın."}, status=400)

            ok = False
            if contact_id:
                ok = bool(update_contact_kommo(contact_id, payload))

            if not ok:
                lead = get_lead_details(lead_id) or {}
                contacts = (lead.get("_embedded") or {}).get("contacts") or []
                if contacts:
                    try:
                        cid = int(contacts[0].get("id") or 0)
                    except (TypeError, ValueError):
                        cid = 0
                    if cid:
                        ok = bool(update_contact_kommo(cid, payload))
                        contact_id = cid

            if not ok:
                # Create contact and link to lead
                create_payload = [{
                    "name": name or "Müştəri",
                    "custom_fields_values": [{"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}] if phone else [],
                    "_embedded": {"leads": [{"id": lead_id}]}
                }]
                try:
                    resp = _http.post(f"{KOMMO_BASE_URL}/api/v4/contacts", headers=HEADERS, json=create_payload, timeout=8)
                    ok = resp.status_code in (200, 201)
                except Exception as exc:
                    logger.error("Failed to create and link contact for lead %s: %s", lead_id, exc)

            if ok:
                if name:
                    try:
                        update_lead_kommo(lead_id, {"name": name})
                    except Exception:
                        pass
                for overview in _personal_overview_cache.values():
                    if not isinstance(overview, dict):
                        continue
                    for deal in overview.get("deals") or []:
                        if isinstance(deal, dict) and int(deal.get("id") or 0) == lead_id:
                            if name:
                                deal["contact_name"] = name
                                deal["name"] = name
                            if phone:
                                deal["phone"] = phone
                                deal["phones"] = [phone]
                invalidate_rufat_overview_cache()
                record_lead_pulse_event(lead_id, "deal_edit_contact", contact_name=name, phone=phone)
            return web.json_response({"success": ok, "message": "Kontakt yeniləndi." if ok else "Kontakt yenilənmədi."})
        elif action == "info":
            if is_funnel_chat(chat_id) and not is_admin(chat_id):
                contacts = search_contact_by_phone(phone)
                full_contact = get_contact_details(contacts[0]["id"]) if contacts else None
                if not any(lead_allowed_for_chat(int(lead.get("id")), chat_id) for lead in (full_contact or {}).get("_embedded", {}).get("leads", [])):
                    return web.json_response({"success": False, "error": "Доступ запрещён: сделка не в воронке Rüfət."}, status=403)
            result = execute_tool_get_lead_info(phone)
            return web.json_response({"success": True, "message": result})
        elif action == "add_note":
            text = data.get("text", "")
            if not text:
                return web.json_response({"success": False, "error": "Qeyd m\u0259tni bo\u015fdur."})
            task_id_note = data.get("task_id")
            if task_id_note:
                if not task_allowed_for_chat(task_id_note, chat_id):
                    return web.json_response({"success": False, "error": "Доступ запрещён: задача не относится к воронке Rüfət."}, status=403)
                try:
                    headers_k = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
                    t_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id_note}", headers=headers_k)
                    t_data = t_resp.json()
                    entity_id = t_data.get("entity_id")
                    entity_type = t_data.get("entity_type", "leads")
                    if entity_id:
                        note_payload = [{"note_type": "common", "params": {"text": text}}]
                        _http.post(f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes", headers={"Authorization": f"Bearer {KOMMO_TOKEN}", "Content-Type": "application/json"}, json=note_payload)
                except: pass
            if phone and not task_id_note and is_funnel_chat(chat_id) and not is_admin(chat_id):
                contacts = search_contact_by_phone(phone)
                full_contact = get_contact_details(contacts[0]["id"]) if contacts else None
                if not any(lead_allowed_for_chat(int(lead.get("id")), chat_id) for lead in (full_contact or {}).get("_embedded", {}).get("leads", [])):
                    return web.json_response({"success": False, "error": "Доступ запрещён: сделка не в воронке Rüfət."}, status=403)
            result = execute_tool_add_note(phone, text) if (phone and not task_id_note) else "OK"
            # Subtask
            if data.get("create_subtask") and data.get("subtask_text"):
                st_result = execute_tool_create_task(phone, data["subtask_text"], None, None, "rufat" if is_rufat_chat(chat_id) else "soltan", chat_id=chat_id)
                if isinstance(st_result, dict) and st_result.get("success"):
                    now = datetime.now(tz=BAKU_TZ)
                    deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                    create_task(st_result["entity_id"], data["subtask_text"], int(deadline_dt.timestamp()), responsible_user_id=st_result["assignee_id"], entity_type=st_result["entity_type"])
            # Admin notify
            admin_chat = get_chat_id_for_kommo_user(10932455)
            sender_name = None
            for _nm, _cid in NAME_TO_CHAT.items():
                if _cid == chat_id and len(_nm) > 5:
                    sender_name = _nm
                    break
            if not sender_name:
                sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), 'Əməkdaş')
            # Check if there's a pending assignee change waiting for this note
            conf_key = data.get("conf_key", "")
            pending = None
            found_conf_key = conf_key
            if _bot_app:
                pending_updates = _bot_app.bot_data.get("pending_updates", {})
                if conf_key and conf_key in pending_updates:
                    pending = pending_updates[conf_key]
                elif task_id_note:
                    # Fallback: find pending by task_id (compare as strings to avoid type mismatch)
                    for pk, pv in pending_updates.items():
                        if str(pv.get("task_id", "")) == str(task_id_note):
                            pending = pv
                            found_conf_key = pk
                            break
            if pending and admin_chat and _bot_app:
                # Send confirmation to admin WITH the note text + client details
                try:
                    pending["note_text"] = text  # store note in pending for later
                    # Get task details for rich notification
                    client_name = pending.get("contact_name", "")
                    client_phone = pending.get("phone", "")
                    task_type_name = ""
                    deal_link = pending.get("link", "")
                    try:
                        headers_k3 = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
                        t_resp3 = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{pending['task_id']}", headers=headers_k3, timeout=5)
                        t_data3 = t_resp3.json()
                        entity_id3 = t_data3.get("entity_id", "")
                        entity_type3 = t_data3.get("entity_type", "leads")
                        task_type_id = t_data3.get("task_type_id")
                        if task_type_id:
                            _LOCAL_TASK_TYPES = {4229218: "\u018flaq\u0259 saxla", 4229220: "G\u00f6r\u00fc\u015f", 4229222: "Qura\u015fd\u0131rma", 4229224: "Cavab g\u00f6zl\u0259nilir", 4229226: "T\u0259qdimat"}
                            task_type_name = _LOCAL_TASK_TYPES.get(task_type_id, "")
                        if entity_id3:
                            deal_link = f"{KOMMO_BASE_URL}/{entity_type3}/detail/{entity_id3}"
                            # Get lead/contact info
                            l_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/{entity_type3}/{entity_id3}", headers=headers_k3, timeout=5)
                            l_data = l_resp.json()
                            client_name = l_data.get("name", "") or client_name
                            # Get contact phone
                            contacts = l_data.get("_embedded", {}).get("contacts", [])
                            if contacts:
                                c_id = contacts[0].get("id")
                                if c_id:
                                    c_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/contacts/{c_id}", headers=headers_k3, timeout=5)
                                    c_data = c_resp.json()
                                    for cf in c_data.get("custom_fields_values", []):
                                        if cf.get("field_code") == "PHONE":
                                            client_phone = cf["values"][0].get("value", "") or client_phone
                                            break
                    except: pass
                    msg_text = f"\u270f\ufe0f {pending['sender_name']} icra\u00e7\u0131n\u0131 d\u0259yi\u015fm\u0259k ist\u0259yir:\n"
                    if client_name:
                        msg_text += f"\n\ud83d\udc64 {client_name}"
                    if client_phone:
                        msg_text += f"\n\ud83d\udcde {client_phone}"
                    msg_text += f"\n\ud83d\udcdd {pending.get('display_text', '')}"
                    if task_type_name:
                        msg_text += f"\n\ud83d\udccb N\u00f6v: {task_type_name}"
                    msg_text += f"\n\ud83d\udc64 {pending['sender_name']} \u2192 {pending['assignee_name_raw']}"
                    msg_text += f"\n\ud83d\udcac Qeyd: {text}"
                    if deal_link:
                        msg_text += f"\n\ud83d\udd17 {deal_link}"
                    kb_json = {"inline_keyboard": [
                        [{"text": "\u2705 T\u0259sdiq et", "callback_data": f"updtask-{found_conf_key}-yes"}],
                        [{"text": "\u015eamil", "callback_data": f"updtask-{found_conf_key}-rufat"}, {"text": "Soltan", "callback_data": f"updtask-{found_conf_key}-soltan"}],
                        [{"text": "H\u00fcseyn", "callback_data": f"updtask-{found_conf_key}-huseyn"}, {"text": "Rasim", "callback_data": f"updtask-{found_conf_key}-rasim"}],
                        [{"text": "Texniki", "callback_data": f"updtask-{found_conf_key}-texniki"}, {"text": "\u00d6z\u00fcm", "callback_data": f"updtask-{found_conf_key}-admin"}],
                        [{"text": "\u274c R\u0259dd et", "callback_data": f"updtask-{found_conf_key}-no"}]
                    ]}
                    tg_resp = _http.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": admin_chat, "text": msg_text, "reply_markup": kb_json}, timeout=8)
                    tg_msg_id = None
                    try:
                        tg_msg_id = tg_resp.json().get("result", {}).get("message_id")
                    except: pass
                    save_pending_action("reassign_task", {
                        "contact_name": client_name or pending.get('display_text', ''),
                        "phone": client_phone or pending.get("phone", ""),
                        "lead_id": entity_id3 if entity_id3 else None,
                        "task_id": pending.get('task_id'),
                        "task_text": pending.get('display_text', ''),
                        "task_type_name": task_type_name,
                        "stage_name": f"İcraçı: {pending['sender_name']} → {pending['assignee_name_raw']}",
                        "sender_name": pending['sender_name'],
                        "assignee_name_raw": pending['assignee_name_raw'],
                        "note": text,
                        "link": deal_link or pending.get("link", ""),
                        "conf_key": found_conf_key,
                        "update_data": pending.get('update_data'),
                        "creator_chat_id": pending.get('creator_chat_id'),
                        "telegram_chat_id": admin_chat,
                        "telegram_message_id": tg_msg_id,
                    }, ["Təsdiq et", "Rüfət", "Soltan", "Hüseyn", "Rasim", "Texniki", "Özüm", "Rədd et"])
                    send_push_to_admin(msg_text, title="✏️ İcraçı dəyişikliyi", url="#pending")
                except Exception as e:
                    logger.error(f"add_note conf notification error: {e}")
            elif admin_chat and admin_chat != chat_id and _bot_app:
                # Regular note notification (no pending assignee change)
                try:
                    note_msg = f"\ud83d\udcdd *{sender_name}* qeyd \u0259lav\u0259 etdi:\n\n\ud83d\udcac {text}"
                    if task_id_note:
                        try:
                            headers_k2 = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
                            t_resp2 = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id_note}", headers=headers_k2, timeout=5)
                            t_data2 = t_resp2.json()
                            entity_id2 = t_data2.get("entity_id", "")
                            entity_type2 = t_data2.get("entity_type", "leads")
                            task_text2 = t_data2.get("text", "")
                            if task_text2:
                                note_msg += f"\n\ud83d\udcdd {task_text2}"
                            if entity_id2:
                                note_msg += f"\n\ud83d\udd17 {KOMMO_BASE_URL}/{entity_type2}/detail/{entity_id2}"
                        except: pass
                    await _bot_app.bot.send_message(admin_chat, note_msg, parse_mode="Markdown", disable_web_page_preview=True)
#                    send_push_to_admin(note_msg, title="📝 Qeyd əlavə edildi")
                except: pass
            # Extract link from result message
            link = ""
            if "\ud83d\udd17" in result:
                parts = result.split("\ud83d\udd17 ")
                if len(parts) > 1:
                    link = parts[1].strip()
            return web.json_response({"success": True, "message": result, "link": link})
        elif action == "task":
            text = data.get("text", "")
            priority = _normalize_task_priority(data.get("priority", ""))
            assignee_name_raw = normalize_assignee_name(data.get("assigneeName") or data.get("assignee_name"))
            creator_name = get_employee_name_by_chat_id(chat_id, "")
            if not assignee_name_raw:
                return web.json_response({"success": False, "error": "İcraçı seçin."}, status=400)
            # Routing: all legacy Sahə Meneceri assignments now go to Admin.
            if assignee_name_raw.lower() in ("nizami", "nizami qasımov"):
                assignee = "admin"
            else:
                assignee = "admin"
            # Price is stored in text if provided (no more [Name] marker - we use pipeline stages)
            price_val = data.get("price", "").strip()
            if price_val:
                text = f"[{price_val}] {text}"
            deadline_key = data.get("deadline", "today")
            now = datetime.now(tz=BAKU_TZ)
            if deadline_key.startswith('custom:'):
                try:
                    custom_val = deadline_key.replace('custom:', '')
                    deadline_dt = datetime.fromisoformat(custom_val).replace(tzinfo=BAKU_TZ)
                except:
                    deadline_dt = now + timedelta(hours=2)
            elif deadline_key == "15m": deadline_dt = now + timedelta(minutes=15)
            elif deadline_key == "1h": deadline_dt = now + timedelta(hours=1)
            elif deadline_key == "today":
                deadline_dt = now.replace(hour=17, minute=0, second=0, microsecond=0)
                if deadline_dt <= now: deadline_dt += timedelta(days=1)
            elif deadline_key == "tomorrow": deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
            else: deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
            task_type_id = int(data.get("task_type") or data.get("task_type_id") or "1")
            # If entity_id passed directly (from subtask), use it instead of phone search
            direct_entity_id = data.get("entity_id")
            direct_entity_type = data.get("entity_type", "leads")
            if direct_entity_id:
                direct_entity_id = int(direct_entity_id)
                assignee_map_direct = {"rufat": 10932455, "soltan": 15531960, "huseyn": 10932455, "rasim": 10932455, "texniki": 10932455, "admin": 10932455, "sahe_meneceri": 10932455}
                assignee_id_direct = assignee_map_direct.get(assignee, 10932455)
                link_direct = f"{KOMMO_BASE_URL}/{direct_entity_type}/detail/{direct_entity_id}"
                logger.info(f"Subtask create: entity_id={direct_entity_id}, entity_type={direct_entity_type}, assignee={assignee}, assignee_id={assignee_id_direct}")
                result = {"success": True, "entity_id": direct_entity_id, "entity_type": direct_entity_type, "assignee_id": assignee_id_direct, "contact_name": "", "link": link_direct, "phone": phone, "assignee_name": assignee_name_raw or assignee}
            else:
                client_name_input = data.get("client_name", "").strip()
                result = execute_tool_create_task(phone, text, None, None, assignee, client_name=client_name_input, chat_id=chat_id, assignee_name=assignee_name_raw)
            if isinstance(result, str):
                return web.json_response({"success": False, "error": result})
            if not result.get("success"):
                return web.json_response({"success": False, "error": result.get("message", "Xəta")})
            deadline_ts = int(deadline_dt.timestamp())
            # If non-admin creating for OTHERS, send to admin for confirmation
            # If creating for themselves, no confirmation needed
            is_admin_user = is_admin(chat_id)
            creator_name = creator_name or get_employee_name_by_chat_id(chat_id, "")
            creates_for_self = (assignee_name_raw == creator_name) if (assignee_name_raw and creator_name) else False
            if not is_admin_user and not creates_for_self and _bot_app:
                # Store pending task in bot_data
                conf_key = str(uuid.uuid4())[:8]
                pending = {
                    "entity_id": result["entity_id"], "entity_type": result["entity_type"],
                    "text": text, "deadline_ts": deadline_ts, "assignee_id": result["assignee_id"],
                    "task_type_id": task_type_id, "assignee_name_raw": assignee_name_raw,
                    "contact_name": result["contact_name"], "phone": phone, "link": result.get("link", ""),
                    "creator_chat_id": chat_id, "priority": priority
                }
                _bot_app.bot_data.setdefault("pending_tasks", {})[conf_key] = pending
                sender_name = creator_name or KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
                display_text = text.replace(f'[{assignee_name_raw}] ', '') if assignee_name_raw else text
                admin_chat = get_chat_id_for_kommo_user(10932455)
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Rüfət", callback_data=f"cnftask-{conf_key}-rufat"), InlineKeyboardButton("Soltan", callback_data=f"cnftask-{conf_key}-soltan")],
                    [InlineKeyboardButton("Hüseyn", callback_data=f"cnftask-{conf_key}-huseyn"), InlineKeyboardButton("Rasim", callback_data=f"cnftask-{conf_key}-rasim")],
                    [InlineKeyboardButton("Texniki", callback_data=f"cnftask-{conf_key}-texniki"), InlineKeyboardButton("Özüm", callback_data=f"cnftask-{conf_key}-admin")],
                    [InlineKeyboardButton("❌ Rədd et", callback_data=f"cnftask-{conf_key}-no")],
                ])
                try:
                    await _bot_app.bot.send_message(admin_chat, f"📋 *{sender_name}* tapşırıq yaratmaq istəyir:\n\n👤 {result['contact_name']}\n📞 {phone}\n📝 {display_text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n👤 {sender_name} → {assignee_name_raw}\n🔗 {result.get('link','')}", parse_mode="Markdown", disable_web_page_preview=True, reply_markup=kb)
                    send_push_to_admin(f"{sender_name} tapşırıq yaratmaq istəyir: {result['contact_name']}", title="📋 Yeni tapşırıq")
                except: pass
                # Get lead stage for display
                _lead_stage_name = ""
                try:
                    _ld = get_lead_details(int(result.get('entity_id', 0)))
                    if _ld:
                        _sid = _ld.get("status_id")
                        _lead_stage_name = STAGE_NAMES.get(_sid, "")
                except: pass
                save_pending_action("assign_executor", {
                    "contact_name": result['contact_name'],
                    "phone": phone,
                    "lead_id": result.get('entity_id'),
                    "task_text": display_text,
                    "deadline": deadline_dt.strftime('%d.%m.%Y %H:%M'),
                    "sender_name": sender_name,
                    "assignee_name_raw": assignee_name_raw,
                    "stage_name": _lead_stage_name,
                    "link": result.get('link', ''),
                    "conf_key": conf_key,
                    "creator_chat_id": chat_id,
                }, ["Rüfət", "Soltan", "Hüseyn", "Rasim", "Texniki", "Özüm", "Rədd et"])
                return web.json_response({"success": True, "message": "⏳ Tapşırıq təsdiq üçün göndərildi.", "entity_id": result.get('entity_id'), "entity_type": result.get('entity_type', 'leads')})
            # Admin creates directly
            logger.info(f"Admin creating task: entity_id={result['entity_id']}, type={result['entity_type']}, assignee_id={result['assignee_id']}, task_type={task_type_id}, text={text[:50]}")
            _pwa_creator = get_employee_name_by_chat_id(chat_id, "Admin")
            res = create_task(int(result["entity_id"]), text, deadline_ts, responsible_user_id=int(result["assignee_id"]), entity_type=result["entity_type"], task_type_id=task_type_id, creator_name=_pwa_creator)
            logger.info(f"Create task result: {res}")
            if res:
                save_task_priority(res, priority)
                # Move the deal to the first stage of the selected icraçı funnel.
                if True:
                    _lead_id_to_move = result.get('entity_id') if result.get('entity_type') == 'leads' else None
                    if not _lead_id_to_move and result.get('entity_type') == 'contacts':
                        try:
                            _cr = _http.get(f"{KOMMO_BASE_URL}/api/v4/contacts/{result['entity_id']}/leads", headers=HEADERS, timeout=8)
                            if _cr.status_code == 200:
                                _leads = _cr.json().get('_embedded',{}).get('leads',[])
                                if _leads: _lead_id_to_move = _leads[0]['id']
                        except: pass
                    if assignee_name_raw and _lead_id_to_move:
                        try:
                            if move_lead_to_icraci(_lead_id_to_move, assignee_name_raw):
                                logger.info(f"Moved lead {_lead_id_to_move} to funnel of {assignee_name_raw}")
                        except Exception as _me:
                            logger.error(f"Failed to move lead to icraçı funnel: {_me}")
                msg = f"✅ Tapşırıq yaradıldı!\n👤 {result['contact_name']}\n📞 {phone}\n📝 {text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n👤 Məsul: {result['assignee_name']}"
                # Notify assignee by marker name
                if assignee_name_raw:
                    target_chat = get_chat_id_by_name(assignee_name_raw)
                    logger.info(f"create_task notify: assignee_name_raw={assignee_name_raw}, target_chat={target_chat}, chat_id={chat_id}")
                    # An Admin-created task must never generate a self-notification or self-push.
                    if target_chat and target_chat != chat_id and not (is_admin_user and target_chat == ADMIN_CHAT_ID):
                        display_text = text.replace(f'[{assignee_name_raw}] ', '')
                        notif_msg = f"📋 Yeni tapşırıq!\n\n👤 {result['contact_name']}\n📞 {phone}\n📝 {display_text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n🔗 {result['link']}"
                        try:
                            _http.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                json={"chat_id": target_chat, "text": notif_msg, "disable_web_page_preview": True},
                                timeout=8
                            )
                        except: pass
                        send_push_notification(str(target_chat), '📋 Yeni tapşırıq!', f"{result['contact_name']} - {display_text}")
                return web.json_response({"success": True, "message": msg, "link": result.get('link', ''), "entity_id": result.get('entity_id'), "entity_type": result.get('entity_type', 'leads'), "task_id": _created_task_id(res), "deadline": deadline_dt.strftime("%d.%m %H:%M")})
            return web.json_response({"success": False, "error": "Tapşırıq yaradılarkən xəta."})
        elif action == "stage":
            stage = data.get("stage", "")
            if not stage:
                return web.json_response({"success": False, "error": "Mərhələ seçilməyib."})
            result = execute_tool_change_stage(phone, stage, chat_id)
            if not result.get("success"):
                return web.json_response({"success": False, "error": result.get("message", "Xəta")})
            if result.get("needs_confirmation"):
                # Non-admin needs confirmation in both Telegram and the PWA.
                admin_chat = get_chat_id_for_kommo_user(10932455)
                sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
                stage_display = get_pipeline_stages_for_chat(chat_id)[1].get(result["status_id"], stage)
                conf_key = str(uuid.uuid4())[:8]
                if _bot_app:
                    _bot_app.bot_data[f"confirm_{conf_key}"] = {
                        "phone": result["phone"], "stage": stage,
                        "lead_id": result["lead_id"], "status_id": result["status_id"],
                        "sender_chat_id": chat_id,
                        "sender_kommo_id": get_kommo_user_id_for_chat(chat_id),
                    }
                sent = None
                if admin_chat and _bot_app:
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Təsdiq et", callback_data=f"conftr_{conf_key}_yes"),
                        InlineKeyboardButton("❌ Rədd et", callback_data=f"conftr_{conf_key}_no"),
                    ]])
                    try:
                        sent = await _bot_app.bot.send_message(
                            admin_chat,
                            f"🔄 *{sender_name}* mərhələ dəyişikliyi istəyir:\n\n"
                            f"👤 {result['contact_name']}\n📞 {result['phone']}\n📌 {stage_display}",
                            parse_mode="Markdown",
                            reply_markup=keyboard,
                        )
                    except Exception as exc:
                        logger.error(f"PWA stage confirmation send error: {exc}")
                send_push_to_admin(
                    f"{sender_name}: {result['contact_name']} → {stage_display}",
                    title="🔄 Mərhələ",
                url="#pending",
                )
                return web.json_response({"success": True, "message": f"✅ Admin-ə təsdiq sorğusu göndərildi.\n👤 {result['contact_name']}\n📌 {stage_display}"})
            update_lead_kommo(result["lead_id"], {"status_id": result["status_id"], "pipeline_id": get_pipeline_id_for_chat(chat_id)})
            stage_display = get_pipeline_stages_for_chat(chat_id)[1].get(result["status_id"], stage)
            # Notify admin
            admin_chat = get_chat_id_for_kommo_user(10932455)
            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
            if admin_chat and admin_chat != chat_id and _bot_app:
                try:
                    await _bot_app.bot.send_message(admin_chat, f"🔄 *{sender_name}* mərhələni dəyişdi:\n\n👤 {result['contact_name']}\n📞 {phone}\n📌 {stage_display}", parse_mode="Markdown")
#                    send_push_to_admin(f"{sender_name} mərhələni dəyişdi: {result['contact_name']} → {stage_display}", title="🔄 Mərhələ")
                except: pass
            link = f"{KOMMO_BASE_URL}/leads/detail/{result['lead_id']}"
            # Auto-create task for the new stage if applicable
            task_msg = ""
            if stage in _STAGE_TASK_TEXTS:
                task_text = _STAGE_TASK_TEXTS[stage]
                now_dt = datetime.now(tz=BAKU_TZ)
                deadline_ts = int((now_dt + timedelta(hours=2)).timestamp())
                if stage == "qiymet_teklifi":
                    create_task(result["lead_id"], task_text, deadline_ts, responsible_user_id=10932455, entity_type="leads")
                else:
                    kommo_uid = get_kommo_user_id_for_chat(chat_id) or 10932455
                    create_task(result["lead_id"], task_text, deadline_ts, responsible_user_id=kommo_uid, entity_type="leads")
                task_msg = f"\n\u2705 Tap\u015f\u0131r\u0131q: {task_text}"
            return web.json_response({"success": True, "message": f"✅ Mərhələ dəyişdirildi!\n👤 {result['contact_name']}\n📌 {stage_display}{task_msg}", "link": link})
        elif action == "create_deal":
            if not is_funnel_chat(chat_id):
                return web.json_response({"success": False, "error": "Bu əməliyyat yalnız öz huniniz üçün mümkündür."}, status=403)
            customer_name = str(data.get("customer_name") or "").strip()
            note_text = str(data.get("note") or data.get("text") or "").strip()
            stage_key = str(data.get("stage_key") or data.get("stage") or "").strip()
            phone_raw = str(phone or data.get("phone") or "").strip()
            if not customer_name:
                return web.json_response({"success": False, "error": "Müştəri adını daxil edin."})
            if not phone_raw or len(re.sub(r"\D", "", phone_raw)) < 7:
                return web.json_response({"success": False, "error": "Telefon nömrəsini daxil edin."})
            owner = get_funnel_owner(chat_id)
            if not owner:
                return web.json_response({"success": False, "error": "Huni tapılmadı."}, status=403)
            pipeline_id = int(owner["pipeline_id"])
            stages = owner["stages"]
            names = owner["stage_names"]
            if is_admin(chat_id):
                try:
                    requested_pipeline = int(data.get("pipeline_id") or 0)
                except (TypeError, ValueError):
                    requested_pipeline = 0
                if requested_pipeline:
                    pipeline_id = requested_pipeline
                    stages, names, _ui = load_pipeline_stage_maps(pipeline_id, fallback=False)
                if stage_key not in stages:
                    return web.json_response({"success": False, "error": "Mərhələni hunidən seçin."})
            else:
                working_keys = {
                    str(key)
                    for key, _label in (owner.get("ui_stages") or [])
                    if key not in ("ugurlu", "imtina", "nerazobrannoye")
                }
                if stage_key not in stages or (working_keys and stage_key not in working_keys):
                    return web.json_response({"success": False, "error": "Mərhələni öz huninizdən seçin."})
            partner_name = str(data.get("partner") or "").strip()
            utm_blob = " ".join((
                str(data.get("utm_source") or ""),
                str(data.get("utm_medium") or ""),
                str(data.get("utm_campaign") or ""),
                note_text,
            ))
            utm_label = menbe_from_utm(utm_blob)
            menbe_cf = menbe_field_payload(utm_label) if utm_label else None
            extra_fields = [menbe_cf] if menbe_cf else None
            contacts = search_contact_by_phone(phone_raw)
            contact_id = None
            if contacts:
                contact_id = int(contacts[0]["id"])
                update_payload = {"name": customer_name}
                if extra_fields:
                    update_payload["custom_fields_values"] = extra_fields
                update_contact_kommo(contact_id, update_payload)
            else:
                created = create_contact_kommo(customer_name, phone_raw, extra_fields)
                contact_id = ((created or {}).get("_embedded") or {}).get("contacts", [{}])[0].get("id")
            if not contact_id:
                return web.json_response({"success": False, "error": "Kontakt yaradıla bilmədi."})
            lead_id = create_lead_for_contact(int(contact_id), customer_name, pipeline_id, stages[stage_key])
            if not lead_id:
                return web.json_response({"success": False, "error": "Sövdələşmə yaradıla bilmədi."})
            applied_utm = apply_menbe(int(contact_id), int(lead_id), "", utm_blob, overwrite=False)
            if partner_name:
                set_deal_partner(int(lead_id), partner_name, chat_id)
                add_partner_for_chat(chat_id, partner_name)
            if note_text:
                add_note(int(lead_id), note_text, "leads")
            invalidate_rufat_overview_cache()
            stage_label = names.get(int(stages[stage_key]), stage_key)
            return web.json_response({
                "success": True,
                "message": f"✅ Sövdələşmə yaradıldı.\n👤 {customer_name}\n📌 {stage_label}",
                "lead_id": int(lead_id),
                "stage_key": stage_key,
                "partner": partner_name,
                "utm": applied_utm,
                "partners": get_partner_list_for_chat(chat_id),
                "link": f"{KOMMO_BASE_URL}/leads/detail/{lead_id}",
            })
        elif action == "update_task":
            task_id = data.get("task_id")
            if not task_id:
                return web.json_response({"success": False, "error": "task_id yoxdur."})
            if not task_allowed_for_chat(task_id, chat_id):
                return web.json_response({"success": False, "error": "Доступ запрещён: задача не относится к воронке Rüfət."}, status=403)
            update_data = {}
            if data.get("text"):
                update_data["text"] = data["text"]
            # Handle assignee change
            assignee = data.get("assignee")
            assignee_name_raw = normalize_assignee_name(data.get("assigneeName", ""))
            if assignee:
                assignee_map = {"rufat": 10932455, "soltan": 15531960, "huseyn": 10932455, "rasim": 10932455, "texniki": 10932455, "admin": 10932455, "sahe_meneceri": 10932455}
                assignee_id = assignee_map.get(assignee)
                if assignee_id:
                    update_data["responsible_user_id"] = assignee_id
            # Handle task type change
            task_type = data.get("task_type")
            if task_type:
                try:
                    update_data["task_type_id"] = int(task_type)
                except: pass
            if data.get("deadline"):
                now = datetime.now(tz=BAKU_TZ)
                dl = data["deadline"]
                if dl.startswith('custom:'):
                    try:
                        new_dl = datetime.fromisoformat(dl.replace('custom:', '')).replace(tzinfo=BAKU_TZ)
                    except:
                        new_dl = now + timedelta(hours=2)
                elif dl == "15m": new_dl = now + timedelta(minutes=15)
                elif dl == "1h": new_dl = now + timedelta(hours=1)
                elif dl == "today": new_dl = now.replace(hour=18, minute=0, second=0)
                elif dl == "tomorrow": new_dl = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0)
                elif dl == "week": new_dl = (now + timedelta(days=7)).replace(hour=12, minute=0, second=0)
                else: new_dl = now + timedelta(hours=2)
                update_data["complete_till"] = int(new_dl.timestamp())
            # Update contact name in Kommo if provided
            edit_client_name = data.get("client_name", "").strip()
            if edit_client_name and task_id:
                # Get task to find entity_id, then find contact and update name
                try:
                    t_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=HEADERS, timeout=8)
                    if t_resp.status_code == 200:
                        task_info = t_resp.json()
                        eid = task_info.get("entity_id")
                        etype = task_info.get("entity_type", "leads")
                        if eid and etype == "contacts":
                            update_contact_kommo(int(eid), {"name": edit_client_name})
                        elif eid and etype == "leads":
                            l_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads/{eid}?with=contacts", headers=HEADERS, timeout=8)
                            if l_resp.status_code == 200:
                                contacts_emb = l_resp.json().get("_embedded", {}).get("contacts", [])
                                if contacts_emb:
                                    update_contact_kommo(int(contacts_emb[0]["id"]), {"name": edit_client_name})
                except Exception as e:
                    logger.error(f"Edit client_name update error: {e}")
            _edit_priority = data.get("priority", "").strip()
            if not update_data and not edit_client_name and not _edit_priority:
                return web.json_response({"success": False, "error": "He\u00e7 n\u0259 d\u0259yi\u015fdirilm\u0259di."})
            # Non-admin changing assignee → send to admin for confirmation
            is_admin_user = is_admin(chat_id)
            creator_name = None
            for _n, _cid in NAME_TO_CHAT.items():
                if _cid == chat_id and len(_n) > 5:
                    creator_name = _n
                    break
            # Check if assignee actually changed (compare with original)
            original_assignee = data.get("originalAssignee", "")
            assignee_actually_changed = assignee_name_raw and original_assignee and assignee_name_raw != original_assignee
            if not is_admin_user and assignee_actually_changed and _bot_app:
                # Non-admin changing assignee -> save pending, DON'T notify yet (wait for note)
                sender_name = creator_name or KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "\u018fm\u0259kda\u015f")
                # Resolve contact details now, because this volatile pending entry may be
                # completed later through a note callback after the original UI request.
                pending_contact_name = data.get("client_name", "").strip()
                pending_phone = phone
                pending_link = ""
                try:
                    task_response = _http.get(
                        f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=HEADERS, timeout=8
                    )
                    if task_response.status_code == 200:
                        task_context = get_task_deal_context(task_response.json())
                        pending_contact_name = task_context.get("client_name") or pending_contact_name
                        pending_phone = task_context.get("phone") or pending_phone
                        pending_link = task_context.get("link") or pending_link
                except Exception as context_error:
                    logger.warning("Could not resolve reassignment contact context for task %s: %s", task_id, context_error)
                display_text = (data.get("text") or "").replace(f'[{assignee_name_raw}] ', '')
                conf_key = str(uuid.uuid4())[:8]
                _bot_app.bot_data.setdefault("pending_updates", {})[conf_key] = {
                    "task_id": task_id, "update_data": update_data,
                    "assignee_name_raw": assignee_name_raw, "sender_name": sender_name,
                    "creator_chat_id": chat_id, "display_text": display_text,
                    "contact_name": pending_contact_name,
                    "phone": pending_phone,
                    "link": pending_link,
                }
                # Return conf_key so frontend can attach note to this pending update
                return web.json_response({"success": True, "message": "\u2705 Yadda saxland\u0131.", "conf_key": conf_key})
            # Admin updates directly
            # Save priority locally (not a Kommo field)
            priority = data.get("priority", "").strip()
            if priority:
                task_priorities = read_json(_TASK_PRIORITIES_FILE) or {}
                task_priorities[str(task_id)] = priority
                write_json(_TASK_PRIORITIES_FILE, task_priorities)
            if not update_data:
                # Only client_name or priority was changed, no Kommo task fields to update
                # Still need entity_id for voice upload
                _eid = None
                _etype = "leads"
                try:
                    _tr = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=HEADERS, timeout=8)
                    if _tr.status_code == 200:
                        _td = _tr.json()
                        _eid = _td.get("entity_id")
                        _etype = _td.get("entity_type", "leads")
                except: pass
                return web.json_response({"success": True, "message": "\u2705 Yenil\u0259ndi!", "entity_id": _eid, "entity_type": _etype})
            result = update_task_kommo(task_id, update_data)
            if result:
                invalidate_rufat_overview_cache()
                link = ""
                try:
                    headers_k = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
                    t_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=headers_k)
                    t_data = t_resp.json()
                    entity_id = t_data.get("entity_id", "")
                    entity_type = t_data.get("entity_type", "leads")
                    link = f"{KOMMO_BASE_URL}/leads/detail/{entity_id}" if entity_id else ""
                    # Save note if provided
                    note_text = data.get("note", "").strip()
                    if note_text and entity_id:
                        note_payload = [{"note_type": "common", "params": {"text": note_text}}]
                        _http.post(f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes", headers={"Authorization": f"Bearer {KOMMO_TOKEN}", "Content-Type": "application/json"}, json=note_payload, timeout=8)
                except:
                    pass
                return web.json_response({"success": True, "message": "\u2705 Tap\u015f\u0131r\u0131q yenil\u0259ndi!", "link": link, "entity_id": entity_id, "entity_type": entity_type})
            else:
                return web.json_response({"success": False, "error": "Yenil\u0259m\u0259 u\u011fursuz oldu."})
        elif action == "update_task_deadline":
            task_id = data.get("task_id")
            time_preset = data.get("time_preset", "+2h")
            reason = data.get("reason", "")
            if not task_id:
                return web.json_response({"success": False, "error": "task_id yoxdur."})
            if not task_allowed_for_chat(task_id, chat_id):
                return web.json_response({"success": False, "error": "Доступ запрещён: задача не относится к воронке Rüfət."}, status=403)
            now = datetime.now(tz=BAKU_TZ)
            if time_preset == "+2h":
                new_dl = now + timedelta(hours=2)
            elif time_preset == "sabah":
                new_dl = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
            elif time_preset == "gelen_hefte":
                days_ahead = 7 - now.weekday()
                if days_ahead <= 0: days_ahead += 7
                new_dl = (now + timedelta(days=days_ahead)).replace(hour=10, minute=0, second=0, microsecond=0)
            else:
                new_dl = now + timedelta(hours=2)
            result = update_task_kommo(task_id, {"complete_till": int(new_dl.timestamp())})
            if result:
                invalidate_rufat_overview_cache()
            # If reason is employee's fault, record KPI=0
            if reason == "Çatdıra bilmirəm" and get_employee_type(chat_id) == "salary":
                # Auto-create session and finish with KPI=0 (missed deadline)
                if not has_active_session(chat_id, int(task_id)):
                    start_task_session(chat_id, int(task_id))
                # Finish with deadline_ts=0 to force KPI=0 (completed after deadline)
                finish_task_session(chat_id, int(task_id), 1, reason, deadline_ts=0)
                logger.info(f"KPI=0 recorded for task {task_id}, reason: {reason}")
            if result:
                return web.json_response({"success": True, "message": "Vaxt dəyişdirildi."})
            else:
                return web.json_response({"success": False, "error": "Yeniləmə uğursuz."})
        elif action == "check_started":
            task_id = data.get("task_id")
            if not task_id:
                return web.json_response({"success": True, "started": False})
            started = has_active_session(chat_id, int(task_id))
            return web.json_response({"success": True, "started": started})
        elif action == "complete_task":
            try:
                task_id = int(data.get("task_id"))
            except (TypeError, ValueError):
                task_id = 0
            if not task_id:
                return web.json_response({"success": False, "error": "task_id yoxdur."})
            task_data = get_kommo_task(task_id)
            if not task_data:
                return web.json_response({"success": False, "error": "Tapşırıq Kommo-da tapılmadı."})
            if not task_payload_allowed_for_chat(task_data, chat_id):
                return web.json_response({"success": False, "error": "Доступ запрещён: задача не относится к воронке Rüfət."}, status=403)

            task_context = get_task_deal_context(task_data)
            lead_id = task_context["lead_id"]
            link = task_context["link"]
            phone = data.get("phone", "") or task_context["phone"]
            contact_name = task_context["client_name"]
            note_text = data.get("note", "").strip()
            task_result_text = note_text or "Tamamlandı"
            delay_reason = data.get("delay_reason", "")
            task_type_id = int(task_data.get("task_type_id", 1) or 1)
            task_deadline_ts = int(task_data.get("complete_till", 0) or 0)
            samil_completion_stage = None
            if is_funnel_chat(chat_id) and not is_admin(chat_id):
                selected_pipeline = str(data.get("completion_pipeline", "")).strip()
                selected_stage = str(data.get("completion_stage", "")).strip()
                lead_pipeline_id = None
                if lead_id:
                    try:
                        lead_pipeline_id = _lead_pipeline_id(get_lead_details(int(lead_id)))
                    except Exception:
                        lead_pipeline_id = None
                samil_completion_stage = get_rufat_completion_stage(
                    selected_pipeline, selected_stage, chat_id=chat_id, lead_pipeline_id=lead_pipeline_id
                )
                if not samil_completion_stage:
                    return web.json_response({"success": False, "error": "Mərhələ seçin: öz vоронка və ya Əməliyyatlar lövhəsi."})

            # Salary KPI is deterministic: on/before the Kommo deadline = 100,
            # after the deadline = 0. Admin can correct it afterward.
            kpi_result = None
            try:
                emp_type = get_employee_type(chat_id)
                if emp_type == "salary":
                    # Auto-create session if none exists (timer removed from UI)
                    if not has_active_session(chat_id, int(task_id)):
                        start_task_session(chat_id, int(task_id))
                    kpi_result = finish_task_session(
                        chat_id,
                        int(task_id),
                        task_type_id,
                        delay_reason,
                        deadline_ts=task_deadline_ts,
                    )
            except Exception as kpi_err:
                logger.error(f"KPI processing error in complete_task: {kpi_err}\n{traceback.format_exc()}")

            # Determine the creator before any completion-stage branch. This
            # must be available not only in the final notification block, but
            # also when legacy/new_stage handling runs first.
            _task_creator_is_rufat = task_created_by_rufat(task_id)
            result = update_task_kommo(
                task_id,
                {"is_completed": True, "result": {"text": task_result_text}},
            )
            stage_msg = ""
            if result and lead_id:
                record_lead_pulse_event(int(lead_id), "task_complete")
                if samil_completion_stage:
                    target_pipeline_id, target_status_id, target_stage_name = samil_completion_stage
                    target_pipeline_name = owner_name_for_pipeline(target_pipeline_id) or "Əməliyyatlar lövhəsi"
                    if update_lead_kommo(
                        int(lead_id), {"pipeline_id": int(target_pipeline_id), "status_id": int(target_status_id)}
                    ):
                        stage_msg = f"\n📌 Mərhələ: {target_pipeline_name} → {target_stage_name}"
                        maps, _names, _ui = load_pipeline_stage_maps(int(target_pipeline_id)) if int(target_pipeline_id) in all_personal_pipeline_ids() else ({}, {}, [])
                        rufat_stage_key = next(
                            (key for key, sid in maps.items() if int(sid) == int(target_status_id)),
                            None,
                        )
                        if rufat_stage_key:
                            patch_rufat_overview_deal_stage(int(lead_id), rufat_stage_key)
                        else:
                            invalidate_rufat_overview_cache()
                    else:
                        logger.error("Rüfət completion stage update failed: lead=%s pipeline=%s status=%s", lead_id, target_pipeline_id, target_status_id)
                        stage_msg = "\n⚠️ Tapşırıq bağlandı, lakin mərhələ dəyişdirilmədi"
                    if target_status_id == 142:
                        conf_key = str(uuid.uuid4())[:8]
                        completion_sender = get_employee_name_by_chat_id(chat_id, "Rüfət Həsənzadə")
                        deadline_display = (
                            datetime.fromtimestamp(task_deadline_ts, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M")
                            if task_deadline_ts else "—"
                        )
                        task_desc = re.sub(r"^\[[^\]]+\]\s*", "", task_data.get("text", "")).strip() or "—"
                        admin_chat = get_chat_id_for_kommo_user(ADMIN_KOMMO_USER_ID) or ADMIN_CHAT_ID
                        sent = None
                        if _bot_app and admin_chat:
                            _bot_app.bot_data[f"confirm_{conf_key}"] = {
                                "lead_id": int(lead_id), "status_id": int(target_status_id),
                                "stage": selected_stage, "stage_name": target_stage_name,
                                "pipeline_id": int(target_pipeline_id), "sender_chat_id": int(chat_id),
                                "phone": phone or "—",
                            }
                            try:
                                keyboard = InlineKeyboardMarkup([[
                                    InlineKeyboardButton("✅ Təsdiq et", callback_data=f"conftr_{conf_key}_yes"),
                                    InlineKeyboardButton("❌ Rədd et", callback_data=f"conftr_{conf_key}_no"),
                                ]])
                                sent = await _bot_app.bot.send_message(
                                    int(admin_chat),
                                    f"🔄 *{completion_sender}* uğurla tamamladı — məbləğ təsdiqi:\n\n"
                                    f"👤 {contact_name or '—'}\n📝 {task_desc}\n📞 {phone or '—'}\n"
                                    f"⏰ {deadline_display}\n📌 {target_pipeline_name}: {target_stage_name}\n🔗 {link}",
                                    parse_mode="Markdown", reply_markup=keyboard, disable_web_page_preview=True,
                                )
                            except Exception as exc:
                                logger.error(f"Rüfət completion confirmation send error: {exc}")
                        save_pending_action("confirm_stage", {
                            "contact_name": contact_name or "—", "phone": phone or "—", "lead_id": int(lead_id),
                            "task_id": int(task_id),
                            "status_id": int(target_status_id), "stage_name": target_stage_name,
                            "stage_key": selected_stage, "pipeline_id": int(target_pipeline_id),
                            "sender_name": completion_sender, "sender_chat_id": int(chat_id), "conf_key": conf_key,
                            "link": link, "telegram_chat_id": admin_chat,
                            "telegram_message_id": sent.message_id if sent else None,
                        }, ["Təsdiq et", "Rədd et"])
                        send_push_to_admin(
                            f"{completion_sender}: {contact_name or '—'} → {target_stage_name}",
                            title="🔄 Uğurla tamamlandı — məbləğ", url="#pending",
                        )
                else:
                    # Existing behaviour for every employee other than Rüfət.
                    try:
                        _http.patch(f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}",
                            headers=HEADERS, json={"pipeline_id": GOZLEME_PIPELINE_ID, "status_id": 142}, timeout=8)
                    except Exception as _ue:
                        logger.error(f"Failed to move lead to Ugurlu: {_ue}")
            # Also change stage if requested by legacy clients.
            new_stage = data.get("new_stage")
            logger.info(f"complete_task: task_id={task_id}, new_stage={new_stage}, phone={phone}, result={bool(result)}")
            if new_stage:
                # Try to find lead: by phone or by task entity
                status_id = STAGES.get(new_stage)
                if phone:
                    stage_result = execute_tool_change_stage(phone, new_stage, chat_id)
                    logger.info(f"complete_task stage_result: {stage_result}")
                    if stage_result.get("success"):
                        lead_id = stage_result["lead_id"]
                        contact_name = stage_result.get("contact_name", "")
                        if stage_result.get("needs_confirmation") and not _task_creator_is_rufat:
                            admin_chat = get_chat_id_for_kommo_user(10932455)
                            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "\u018fm\u0259kda\u015f")
                            stage_display = STAGE_NAMES.get(stage_result["status_id"], new_stage)
                            conf_key = str(uuid.uuid4())[:8]
                            if _bot_app:
                                _bot_app.bot_data[f"confirm_{conf_key}"] = {
                                    "phone": phone, "stage": new_stage,
                                    "lead_id": lead_id, "status_id": stage_result["status_id"],
                                    "sender_chat_id": chat_id, "sender_kommo_id": get_kommo_user_id_for_chat(chat_id)
                                }
                            sent = None
                            if admin_chat and _bot_app:
                                try:
                                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("\u2705 T\u0259sdiq et", callback_data=f"conftr_{conf_key}_yes"), InlineKeyboardButton("\u274c R\u0259dd et", callback_data=f"conftr_{conf_key}_no")]])
                                    sent = await _bot_app.bot.send_message(admin_chat, f"\ud83d\udd04 *{sender_name}* m\u0259rh\u0259l\u0259 d\u0259yi\u015fikliyi ist\u0259yir:\n\n\ud83d\udc64 {contact_name}\n\ud83d\udcde {phone}\n\ud83d\udccc {stage_display}", parse_mode="Markdown", reply_markup=keyboard)
                                except Exception as e:
                                    logger.error(f"complete_task confirmation send error: {e}")
                            send_push_to_admin(
                                f"{sender_name}: {contact_name} → {stage_display}",
                                title="🔄 Mərhələ təsdiqi",
                                url="#pending",
                            )
                            stage_msg = f"\n\ud83d\udccc M\u0259rh\u0259l\u0259: Admin-\u0259 t\u0259sdiq sor\u011fusu g\u00f6nd\u0259rildi"
                        else:
                            update_lead_kommo(lead_id, {"status_id": stage_result["status_id"], "pipeline_id": PIPELINE_ID})
                            stage_display = STAGE_NAMES.get(stage_result["status_id"], new_stage)
                            stage_msg = f"\n\ud83d\udccc M\u0259rh\u0259l\u0259: {stage_display}"
                        link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
                else:
                    # No phone - try to get lead from task entity
                    try:
                        task_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=HEADERS, timeout=8)
                        if task_resp.status_code == 200:
                            task_data = task_resp.json()
                            entity_id = task_data.get("entity_id")
                            entity_type = task_data.get("entity_type", "contacts")
                            if entity_type == "leads":
                                lead_id = entity_id
                            elif entity_type == "contacts":
                                full_c = get_contact_details(entity_id)
                                if full_c:
                                    leads = full_c.get("_embedded", {}).get("leads", [])
                                    if leads:
                                        lead_id = leads[0]["id"]
                    except: pass
                    if lead_id and status_id:
                        if is_admin(chat_id) or _task_creator_is_rufat:
                            update_lead_kommo(lead_id, {"status_id": status_id, "pipeline_id": PIPELINE_ID})
                            stage_display = STAGE_NAMES.get(status_id, new_stage)
                            stage_msg = f"\n\ud83d\udccc M\u0259rh\u0259l\u0259: {stage_display}"
                        else:
                            admin_chat = get_chat_id_for_kommo_user(10932455)
                            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "\u018fm\u0259kda\u015f")
                            stage_display = STAGE_NAMES.get(status_id, new_stage)
                            conf_key = str(uuid.uuid4())[:8]
                            if _bot_app:
                                _bot_app.bot_data[f"confirm_{conf_key}"] = {
                                    "phone": phone, "stage": new_stage,
                                    "lead_id": lead_id, "status_id": status_id,
                                    "sender_chat_id": chat_id, "sender_kommo_id": get_kommo_user_id_for_chat(chat_id)
                                }
                            sent = None
                            if admin_chat and _bot_app:
                                try:
                                    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("\u2705 T\u0259sdiq et", callback_data=f"conftr_{conf_key}_yes"), InlineKeyboardButton("\u274c R\u0259dd et", callback_data=f"conftr_{conf_key}_no")]])
                                    sent = await _bot_app.bot.send_message(admin_chat, f"\ud83d\udd04 *{sender_name}* m\u0259rh\u0259l\u0259 d\u0259yi\u015fikliyi ist\u0259yir:\n\n\ud83d\udccc {stage_display}", parse_mode="Markdown", reply_markup=keyboard)
                                except Exception as e:
                                    logger.error(f"complete_task confirmation (no phone) error: {e}")
                            send_push_to_admin(
                                f"{sender_name}: {stage_display}",
                                title="🔄 Mərhələ təsdiqi",
                                url="#pending",
                            )
                            stage_msg = f"\n\ud83d\udccc M\u0259rh\u0259l\u0259: Admin-\u0259 t\u0259sdiq sor\u011fusu g\u00f6nd\u0259rildi"
                        link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
            # The employee note is both the task result (set above) and a
            # common note on the related deal.
            if note_text:
                if lead_id:
                    if not add_note(lead_id, note_text, "leads"):
                        logger.error(f"complete_task: failed to add deal note for lead {lead_id}")
                else:
                    logger.warning(f"complete_task: task {task_id} has no related deal for note")

            # Auto-create a task for legacy clients that explicitly submitted a
            # stage with completion. The new admin stage buttons only move the deal.
            stage_confirmed = stage_msg and "təsdiq" not in stage_msg
            if lead_id and new_stage in _STAGE_TASK_TEXTS and stage_confirmed:
                followup_text = _STAGE_TASK_TEXTS[new_stage]
                deadline_dt = datetime.now(tz=BAKU_TZ) + timedelta(hours=2)
                deadline_ts = int(deadline_dt.timestamp())
                if new_stage == "qiymet_teklifi":
                    create_task(lead_id, followup_text, deadline_ts, responsible_user_id=10932455, entity_type="leads")
                else:
                    creator_marker = get_employee_name_by_chat_id(chat_id, "")
                    if creator_marker:
                        followup_text = f"[{creator_marker}] {followup_text}"
                        create_task(lead_id, followup_text, deadline_ts, responsible_user_id=10932455, entity_type="leads")
                    else:
                        create_task(lead_id, followup_text, deadline_ts, responsible_user_id=10932455, entity_type="leads")
                stage_msg += f"\n✅ Yeni tapşırıq: {_STAGE_TASK_TEXTS[new_stage]}"

            # A task created by Rüfət is handled by his admin account; do not
            # send its completion to Nizami for confirmation.
            if not is_admin(chat_id):  # Admin doesn't need self-confirmation
              try:
                logger.info("complete_task: ENTERING notification block")
                task_text_full = task_data.get("text", "").strip()
                task_desc_display = re.sub(r"^\[[^\]]+\]\s*", "", task_text_full)
                task_type_name = TASK_TYPE_NAMES.get(task_type_id, f"Tip {task_type_id}")
                completion_sender = get_employee_name_by_chat_id(
                    chat_id,
                    KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş"),
                )
                # Queue piecework earnings for Admin confirmation and persist the
                # task context needed by the redesigned balance history.
                try:
                    # Price can be in [Price] or [Name:Price] format
                    price_match = re.match(r"^\[(?:[^:\]]*:)?(\d+(?:\.\d+)?)\]\s*(.*)", task_text_full)
                    if get_employee_type(chat_id) == "piecework" and price_match and price_match.group(1):
                        amount = float(price_match.group(1))
                        if amount > 0:
                            add_balance_transaction(
                                chat_id,
                                int(task_id),
                                amount,
                                price_match.group(2) or task_text_full,
                                executor_name=completion_sender,
                                client=contact_name or "—",
                                phone=phone or "—",
                                task_type=task_type_name,
                                result_text=note_text or task_result_text,
                                kpi=0,
                                status="pending",
                            )
                except Exception as balance_error:
                    logger.error(f"Pending balance transaction error: {balance_error}")

                admin_chat = get_chat_id_for_kommo_user(10932455) or 1628569350
                logger.info(f"complete_task notify: admin_chat={admin_chat}, contact={contact_name}, creator_is_samil={_task_creator_is_rufat}")

                # Tasks created by Rüfət must never create a confirmation request
                # for Nizami. They are reported to Rüfət as a regular completion
                # notification below. All other employee-created tasks keep the
                # existing Nizami confirmation flow.
                if admin_chat and not is_rufat_chat(chat_id) and not _task_creator_is_rufat:
                    deadline_display = (
                        datetime.fromtimestamp(task_deadline_ts, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M")
                        if task_deadline_ts else "—"
                    )
                    kpi_info = ""
                    if kpi_result:
                        timing_label = "vaxtında" if kpi_result["completed_before_deadline"] else "gecikib"
                        kpi_info = f"\n📊 KPI: {kpi_result['kpi_score']}/100 ({timing_label})"
                        if delay_reason:
                            kpi_info += f"\n⚠️ Səbəb: {delay_reason}"

                    # Get current deal stage
                    current_stage_name = "—"
                    if lead_id:
                        try:
                            lead_detail = get_lead_details(int(lead_id))
                            if lead_detail:
                                current_status_id = lead_detail.get("status_id")
                                current_stage_name = STAGE_NAMES.get(current_status_id, f"ID:{current_status_id}")
                        except Exception:
                            pass
                    completion_message = (
                        f"✅ {completion_sender} tapşırığı tamamladı:\n\n"
                        f"👤 {contact_name or '—'}\n"
                        f"📝 {task_desc_display or '—'}\n"
                        f"📞 {phone or '—'}\n"
                        f"⏰ {deadline_display}\n"
                        f"📋 {task_type_name}\n"
                        f"📌 Mərhələ: {current_stage_name}"
                    )
                    if note_text:
                        completion_message += f"\n💬 İcraçı qeydi: {note_text}"
                    completion_message += kpi_info
                    if link:
                        completion_message += f"\n🔗 {link}"

                    raw_task_text = task_data.get("text", "").strip()
                    task_price_match = re.match(r"^\[(?:[^:\]]*:)?(\d+(?:\.\d+)?)\]", raw_task_text)
                    callback_key = str(uuid.uuid4())[:8]
                    if _bot_app:
                        _bot_app.bot_data.setdefault("pending_stage_change", {})[callback_key] = {
                            "lead_id": lead_id,
                            "task_id": int(task_id),
                            "employee_tg_id": int(chat_id),
                            "task_text": re.sub(r"^\[[^\]]+\]\s*", "", raw_task_text) or "—",
                            "task_price": task_price_match.group(1) if task_price_match else "",
                        }
                    kb_json = {"inline_keyboard": [[{"text": "📋 Mərhələni dəyiş", "callback_data": f"chgstg-{callback_key}"}]]}
                    try:
                        _http.post(
                            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                            json={"chat_id": admin_chat, "text": completion_message, "reply_markup": kb_json, "disable_web_page_preview": True},
                            timeout=8
                        )
                        send_push_to_admin(completion_message, title="✅ Tapşırıq tamamlandı", url="#pending")
                        save_pending_action("change_stage", {
                            "contact_name": contact_name or "—",
                            "phone": phone or "—",
                            "lead_id": lead_id,
                            "task_id": int(task_id),
                            "sender_name": completion_sender,
                            "sender_chat_id": int(chat_id),
                            "task_text": re.sub(r"^\[[^\]]+\]\s*", "", raw_task_text) or "—",
                            "task_price": task_price_match.group(1) if task_price_match else "",
                            "note": note_text or "",
                            "stage_name": current_stage_name,
                            "description": "Tapşırıq tamamlandı. Yeni mərhələni seçin.",
                            "link": link,
                            "callback_key": callback_key,
                        }, ["Təsdiq et"] + [STAGE_NAMES.get(sid, sk) for sk, sid in STAGES.items()])
                    except Exception as notify_error:
                        logger.error(f"Completion notification error: {notify_error}")

                # Notify the task creator independently of the Admin confirmation
                # flow. In particular, Rüfət receives his own tasks' completion
                # notice, even though Nizami receives no confirmation request.
                try:
                    _creators_data = read_json(_TASK_CREATORS_FILE) or {}
                    _creator_name = _creators_data.get(str(task_id), "")
                    _creator_chat = RUFAT_CHAT_ID if _task_creator_is_rufat else (NAME_TO_CHAT.get(_creator_name) if _creator_name else None)
                    if _creator_chat and int(_creator_chat) != int(chat_id):
                        _c_cn = contact_name or "—"
                        _c_td = task_desc_display or "—"
                        _c_ph = phone or "—"
                        _creator_msg = (f"✅ Sizin yaratdığınız tapşırıq tamamlandı:\n\n"
                            f"👤 {_c_cn}\n"
                            f"📝 {_c_td}\n"
                            f"👷 İcraçı: {completion_sender}\n"
                            f"📞 {_c_ph}")
                        if link: _creator_msg += f"\n🔗 {link}"
                        _http.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                            json={"chat_id": int(_creator_chat), "text": _creator_msg, "disable_web_page_preview": True}, timeout=8)
                        send_push_notification(str(_creator_chat), '✅ Tapşırıq tamamlandı', f'{contact_name} - {task_desc_display}')
                except Exception as _cav_err:
                    logger.warning(f"Cavabdeh notification error: {_cav_err}")
              except Exception as notif_block_err:
                logger.error(f"complete_task notification block error: {notif_block_err}\n{traceback.format_exc()}")
              localStorage_key = f'timer_{task_id}'
              msg = f"\u2705 Tap\u015f\u0131r\u0131q tamamland\u0131!{stage_msg}"
              return web.json_response({"success": True, "message": msg, "link": link, "clear_timer": True})
            else:
              # Admin completing task - just return success
              msg = f"\u2705 Tap\u015f\u0131r\u0131q tamamland\u0131!{stage_msg}"
              return web.json_response({"success": True, "message": msg, "link": link, "clear_timer": True})
        elif action == "update_task_deadline":
            task_id = data.get("task_id")
            time_preset = data.get("time_preset", "+2h")
            if not task_id:
                return web.json_response({"success": False, "error": "task_id yoxdur."})
            now = datetime.now(tz=BAKU_TZ)
            if time_preset == "+2h":
                new_deadline = now + timedelta(hours=2)
            elif time_preset == "sabah":
                new_deadline = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0)
            elif time_preset == "gelen_hefte":
                new_deadline = (now + timedelta(days=7)).replace(hour=12, minute=0, second=0)
            else:
                new_deadline = now + timedelta(hours=2)
            result = update_task_kommo(task_id, {"complete_till": int(new_deadline.timestamp())})
            if result:
                return web.json_response({"success": True, "message": f"\u2705 Vaxt d\u0259yi\u015fdirildi: {new_deadline.strftime('%d.%m %H:%M')}"})
            else:
                return web.json_response({"success": False, "error": "Tapşırıq yenilənmədi."})
        elif action == "delete_task":
            task_id = data.get("task_id")
            if not task_id:
                return web.json_response({"success": False, "error": "task_id yoxdur."})
            # Kommo API does not support task deletion; mark as completed instead
            result = update_task_kommo(task_id, {"is_completed": True, "result": {"text": "Tamamlandı"}})
            if result:
                return web.json_response({"success": True, "message": "🗑 Tapşırıq silindi (bağlandı)!"})
            else:
                return web.json_response({"success": False, "error": "Silinmədi."})
        elif action == "start_task":
            task_id = data.get('task_id')
            if not task_id:
                return web.json_response({'success': False, 'error': 'task_id lazımdır.'})
            ok = start_task_session(chat_id, int(task_id))
            if ok:
                return web.json_response({'success': True, 'message': '▶️ Başladı! Vaxt sayılır.'})
            else:
                return web.json_response({'success': True, 'message': '⏱️ Artıq başlayıb.'})
        elif action == "payout":
            # Admin pays an employee - deduct from their balance
            if get_kommo_user_id_for_chat(chat_id) != 10932455:
                return web.json_response({'success': False, 'error': 'İcazə yoxdur.'})
            emp_id = int(data.get('employee_id', 0))
            amount = float(data.get('amount', 0))
            note = data.get('note', 'Ödəniş')
            if not emp_id or amount <= 0:
                return web.json_response({'success': False, 'error': 'Məbləğ və əməkdaş seçin.'})
            add_balance_transaction(
                emp_id,
                0,
                -amount,
                note,
                executor_name=get_employee_name_by_chat_id(emp_id, str(emp_id)),
                client="—",
                phone="—",
                task_type="Məxaric",
                result_text=note,
                kpi=0,
                status="confirmed",
                transaction_type="məxaric",
            )
            return web.json_response({'success': True, 'message': f'✅ {amount:.2f} AZN ödənildi.'})
        elif action == "pause_task":
            task_id = data.get('task_id')
            elapsed_seconds = data.get('elapsed_seconds', 0)
            if task_id:
                pause_task_session(chat_id, int(task_id), int(elapsed_seconds))
            return web.json_response({'success': True, 'message': 'Dayandırıldı.'})
        elif action == "finish_task":
            task_id = data.get("task_id")
            task_type_id = int(data.get("task_type_id", 1) or 1)
            delay_reason = data.get("delay_reason", "")
            if not task_id:
                return web.json_response({"success": False, "error": "task_id lazımdır."})
            deadline_ts = 0
            try:
                task_response = _http.get(
                    f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}",
                    headers=HEADERS,
                    timeout=8,
                )
                if task_response.status_code == 200:
                    task_payload = task_response.json()
                    task_type_id = int(task_payload.get("task_type_id", task_type_id) or task_type_id)
                    deadline_ts = int(task_payload.get("complete_till", 0) or 0)
            except Exception as exc:
                logger.error(f"finish_task deadline lookup error: {exc}")
            result = finish_task_session(
                chat_id,
                int(task_id),
                task_type_id,
                delay_reason,
                deadline_ts=deadline_ts,
            )
            if not result:
                return web.json_response({"success": False, "error": "Əvvəlcə 'Başla' basın."})
            return web.json_response({
                "success": True,
                "message": f"✅ Bitdi! KPI: {result['kpi_score']}/100",
                "kpi_score": result["kpi_score"],
                "actual_minutes": result["actual_minutes"],
                "target_minutes": result["target_minutes"],
                "needs_reason": result["needs_reason"],
            })
        elif action == "close_job_report":
            comment = data.get("master_comment", "")
            if not comment:
                return web.json_response({"success": False, "error": "Hesabat mətni boşdur."})
            # Notify admin about job completion
            admin_chat = get_chat_id_for_kommo_user(10932455)
            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
            if admin_chat and _bot_app:
                try:
                    await _bot_app.bot.send_message(admin_chat, f"✅ *{sender_name}* iş hesabatı:\n\n📝 {comment}", parse_mode="Markdown")
#                    send_push_to_admin(f"{sender_name}: {comment[:80]}", title="✅ İş hesabatı")
                except: pass
            return web.json_response({"success": True, "message": "✅ Hesabat göndərildi!"})
        else:
            return web.json_response({"success": False, "error": f"Naməlum əməliyyat: {action}"})
    except Exception as e:
        logger.error(f"API action error: {e}\n{traceback.format_exc()}")
        return web.json_response({"success": False, "error": "Server xətası."}, status=500)

async def _kommo_get_async(url: str, *, params: dict | None = None, timeout: int = 8):
    """Run a blocking Kommo GET outside the aiohttp event loop."""
    return await asyncio.to_thread(requests.get, url, headers=HEADERS, params=params, timeout=timeout)


async def _rufat_load_all(url: str, embedded_key: str, params: dict | None = None) -> list[dict]:
    """Load all pages for Rüfət data without changing other API consumers."""
    rows: list[dict] = []
    page = 1
    while True:
        try:
            response = await _kommo_get_async(url, params={**(params or {}), "page": page, "limit": 250}, timeout=12)
        except Exception as exc:
            logger.warning("Rüfət %s page %s unavailable: %s", embedded_key, page, exc)
            break
        if response.status_code == 204:
            break
        if response.status_code != 200:
            logger.warning("Rüfət %s page %s failed: %s", embedded_key, page, response.status_code)
            break
        payload = response.json()
        batch = payload.get("_embedded", {}).get(embedded_key, []) or []
        rows.extend(row for row in batch if isinstance(row, dict))
        if len(batch) < 250 and not payload.get("_links", {}).get("next"):
            break
        page += 1
    return rows


async def _load_rufat_contacts(contact_ids: set[int]) -> dict[int, dict]:
    """Fetch linked contacts in batches, preserving every phone field."""
    result: dict[int, dict] = {}
    ids = sorted(contact_ids)
    for start in range(0, len(ids), 250):
        try:
            response = await _kommo_get_async(
                f"{KOMMO_BASE_URL}/api/v4/contacts",
                params={"filter[id][]": ids[start:start + 250], "limit": 250},
                timeout=12,
            )
        except Exception as exc:
            logger.warning("Rüfət contact batch unavailable: %s", exc)
            continue
        if response.status_code != 200:
            logger.warning("Rüfət contact batch failed: %s", response.status_code)
            continue
        for contact in response.json().get("_embedded", {}).get("contacts", []) or []:
            try:
                result[int(contact["id"])] = contact
            except (KeyError, TypeError, ValueError):
                continue
    return result


def _talk_inbox_lead_ids(talk: dict, lead_ids: set[int], contact_to_lead: dict[int, int] | None = None) -> list[int]:
    lids: list[int] = []
    try:
        lids.append(int(talk.get("entity_id") or 0))
    except (TypeError, ValueError):
        pass
    try:
        contact_id = int(talk.get("contact_id") or 0)
    except (TypeError, ValueError):
        contact_id = 0
    if contact_id and contact_to_lead:
        mapped = int(contact_to_lead.get(contact_id) or 0)
        if mapped:
            lids.append(mapped)
    for lead in ((talk.get("_embedded") or {}).get("leads") or []):
        if not isinstance(lead, dict):
            continue
        try:
            lids.append(int(lead.get("id") or 0))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(lid for lid in lids if lid in lead_ids))


def _talk_any_lead_ids(talk: dict) -> list[int]:
    lids: list[int] = []
    entity_type = str(talk.get("entity_type") or "").lower()
    try:
        entity_id = int(talk.get("entity_id") or 0)
    except (TypeError, ValueError):
        entity_id = 0
    if entity_id and entity_type in {"", "lead", "leads"}:
        lids.append(entity_id)
    for lead in ((talk.get("_embedded") or {}).get("leads") or []):
        if not isinstance(lead, dict):
            continue
        try:
            lids.append(int(lead.get("id") or 0))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(lid for lid in lids if lid))


def _talk_last_looks_outgoing(talk: dict) -> bool:
    """Kommo talks stay unread after a manager reply from the phone; do not treat that as inbound."""
    if not isinstance(talk, dict):
        return False
    blobs: list[dict] = []
    for key in ("last_message", "last_message_body", "message"):
        val = talk.get(key)
        if isinstance(val, dict):
            blobs.append(val)
    embedded = talk.get("_embedded") if isinstance(talk.get("_embedded"), dict) else {}
    for key in ("messages", "last_messages"):
        rows = embedded.get(key)
        if isinstance(rows, list) and rows:
            last = rows[-1]
            if isinstance(last, dict):
                blobs.append(last)
    for blob in blobs:
        if blob.get("outgoing") is True:
            return True
        if blob.get("incoming") is True:
            return False
        direction = str(blob.get("direction") or blob.get("type") or blob.get("origin") or "").lower()
        if "outgoing" in direction or direction in {"out", "sent"}:
            return True
        if "incoming" in direction:
            return False
        author = blob.get("author") if isinstance(blob.get("author"), dict) else {}
        atype = str(author.get("type") or "").lower()
        if atype in {"user", "bot", "manager", "salesuser"}:
            return True
    return False


def _cloud_last_outgoing_at(lead_id: int) -> int:
    try:
        lid = int(lead_id or 0)
    except (TypeError, ValueError):
        return 0
    if not lid:
        return 0
    outgoing = [row for row in _sent_messages_for_lead(lid) if not row.get("incoming")]
    if not outgoing:
        return 0
    try:
        return max(int(row.get("created_at") or 0) for row in outgoing)
    except (TypeError, ValueError):
        return 0


def _cloud_outgoing_covers_talk(lead_id: int, updated: int) -> bool:
    last_out = _cloud_last_outgoing_at(lead_id)
    if not last_out:
        return False
    try:
        stamp = int(updated or 0)
    except (TypeError, ValueError):
        stamp = 0
    return stamp <= last_out + 20


_talk_unread_stamp: dict[int, tuple[int, int]] = {}


def _talk_unread_size(talk: dict) -> int:
    if not isinstance(talk, dict):
        return 0
    try:
        unread_n = int(talk.get("unread_messages_count") or talk.get("unread") or 0)
    except (TypeError, ValueError):
        unread_n = 0
    if unread_n > 0:
        return unread_n
    if talk.get("is_read") in {False, 0, "0", "false", "False"}:
        return 1
    return 0


def _stable_talk_incoming_at(lead_id: int, unread_n: int, updated: int) -> int:
    """Keep the first stamp for this unread count.

    Kommo moves talk.updated_at when nothing new arrived. A higher count is a new message.
    """
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return 0
    if unread_n <= 0:
        _talk_unread_stamp.pop(lid, None)
        return 0
    prev_n, prev_stamp = _talk_unread_stamp.get(lid, (0, 0))
    if prev_n == unread_n and prev_stamp:
        return prev_stamp
    stamp = int(updated or 0) or int(prev_stamp or 0)
    if stamp:
        _talk_unread_stamp[lid] = (unread_n, stamp)
    return stamp


def _apply_talk_to_inbox(
    talk: dict,
    lead_ids: set[int],
    client_by_lead: dict[int, str],
    channel_by_lead: dict[int, str],
    updated_by_lead: dict[int, int],
    contact_to_lead: dict[int, int] | None = None,
    avatar_by_lead: dict[int, str] | None = None,
    incoming_at_by_lead: dict[int, int] | None = None,
    outside_by_lead: dict[int, dict] | None = None,
) -> None:
    lids = _talk_inbox_lead_ids(talk, lead_ids, contact_to_lead)
    if not lids:
        if outside_by_lead is not None:
            try:
                updated = int(talk.get("updated_at") or talk.get("created_at") or 0)
            except (TypeError, ValueError):
                updated = 0
            for lid in _talk_any_lead_ids(talk):
                if lid in lead_ids:
                    continue
                prev = outside_by_lead.get(lid)
                try:
                    prev_ts = int((prev or {}).get("updated_at") or (prev or {}).get("created_at") or 0)
                except (TypeError, ValueError):
                    prev_ts = 0
                if not prev or updated >= prev_ts:
                    outside_by_lead[lid] = talk
        return
    channel = _talk_channel_key(talk)
    if channel not in {"whatsapp", "instagram", "facebook", "tiktok", "telegram"}:
        return
    try:
        updated = int(talk.get("updated_at") or talk.get("created_at") or 0)
    except (TypeError, ValueError):
        updated = 0
    unread_n = _talk_unread_size(talk)
    unread = unread_n > 0
    preview = "Yeni mesaj" if unread else "Çat"
    avatar = _first_avatar_url(talk)
    for lid in lids:
        known = updated_by_lead.get(lid, 0)
        if updated >= known:
            updated_by_lead[lid] = updated
            channel_by_lead[lid] = channel
            client_by_lead[lid] = preview
        if unread and incoming_at_by_lead is not None:
            if not _talk_last_looks_outgoing(talk) and not _cloud_outgoing_covers_talk(lid, updated):
                stamp = _stable_talk_incoming_at(lid, unread_n, updated)
                if stamp > incoming_at_by_lead.get(lid, 0):
                    incoming_at_by_lead[lid] = stamp
        if avatar and avatar_by_lead is not None:
            avatar_by_lead[lid] = avatar


async def _load_kommo_talks_inbox(
    lead_ids: set[int],
    contact_to_lead: dict[int, int] | None = None,
) -> tuple[dict[int, str], dict[int, str], dict[int, int], dict[int, str], dict[int, int], dict[int, dict]]:
    """Existing WhatsApp/Instagram deals still live in Kommo talks; Cloud inbound is extra."""
    client_by_lead: dict[int, str] = {}
    channel_by_lead: dict[int, str] = {}
    updated_by_lead: dict[int, int] = {}
    avatar_by_lead: dict[int, str] = {}
    incoming_at_by_lead: dict[int, int] = {}
    outside_by_lead: dict[int, dict] = {}
    if not lead_ids:
        return client_by_lead, channel_by_lead, updated_by_lead, avatar_by_lead, incoming_at_by_lead, outside_by_lead
    for page in range(1, 7):
        try:
            response = await _kommo_get_async(
                f"{KOMMO_BASE_URL}/api/v4/talks",
                params={"limit": 250, "page": page},
                timeout=8,
            )
        except Exception as exc:
            logger.warning("Kommo talks page %s unavailable: %s", page, exc)
            break
        if response.status_code == 204:
            break
        if response.status_code != 200:
            logger.warning("Kommo talks page %s failed: %s", page, response.status_code)
            break
        talks = (response.json().get("_embedded") or {}).get("talks") or []
        if not talks:
            break
        for talk in talks:
            if isinstance(talk, dict):
                _apply_talk_to_inbox(
                    talk,
                    lead_ids,
                    client_by_lead,
                    channel_by_lead,
                    updated_by_lead,
                    contact_to_lead,
                    avatar_by_lead,
                    incoming_at_by_lead,
                    outside_by_lead,
                )
        if len(talks) < 250:
            break
    return client_by_lead, channel_by_lead, updated_by_lead, avatar_by_lead, incoming_at_by_lead, outside_by_lead


async def _load_rufat_latest_notes(
    lead_ids: set[int],
    contact_to_lead: dict[int, int] | None = None,
) -> tuple[dict[int, str], dict[int, str], dict[int, str], dict[int, int], dict[int, str], dict[int, int], dict[int, dict]]:
    """Common notes plus Kommo talk channels so existing chats stay in Çatlar."""
    latest: dict[int, tuple[int, str]] = {}
    if not lead_ids:
        return {}, {}, {}, {}, {}, {}, {}
    page = 1
    max_pages = 4
    while page <= max_pages:
        try:
            response = await _kommo_get_async(
                f"{KOMMO_BASE_URL}/api/v4/leads/notes",
                params={"limit": 250, "page": page, "order[updated_at]": "desc"},
                timeout=12,
            )
        except Exception as exc:
            logger.warning("Rüfət bulk notes page %s unavailable: %s", page, exc)
            break
        if response.status_code == 204:
            break
        if response.status_code != 200:
            logger.warning("Rüfət bulk notes page %s failed: %s", page, response.status_code)
            break
        payload = response.json()
        notes = payload.get("_embedded", {}).get("notes", []) or []
        if not notes:
            break
        for note in notes:
            if not isinstance(note, dict):
                continue
            try:
                entity_id = int(note.get("entity_id"))
            except (TypeError, ValueError):
                continue
            if entity_id not in lead_ids:
                continue
            text = str((note.get("params") or {}).get("text") or "").strip()
            created = int(note.get("created_at") or note.get("updated_at") or 0)
            note_type = str(note.get("note_type") or "")
            if note_type == "common" and text and not _note_is_deleted({"text": text}):
                known = latest.get(entity_id)
                if not known or created > known[0]:
                    latest[entity_id] = (created, text)
        if len(notes) < 250 and not payload.get("_links", {}).get("next"):
            break
        page += 1
    client_by_lead, channel_by_lead, talk_updated, avatar_by_lead, incoming_at_by_lead, outside_by_lead = await _load_kommo_talks_inbox(lead_ids, contact_to_lead)
    return (
        {lead: value[1] for lead, value in latest.items()},
        client_by_lead,
        channel_by_lead,
        talk_updated,
        avatar_by_lead,
        incoming_at_by_lead,
        outside_by_lead,
    )


async def _load_rufat_open_tasks(entity_ids: list[int]) -> list[dict]:
    """Load open tasks only for pipeline leads/contacts, in small filter batches."""
    rows: list[dict] = []
    ids = [entity_id for entity_id in entity_ids if entity_id]
    if not ids:
        return rows
    chunk_size = 40
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start:start + chunk_size]
        page = 1
        while True:
            try:
                response = await _kommo_get_async(
                    f"{KOMMO_BASE_URL}/api/v4/tasks",
                    params={
                        "filter[is_completed]": 0,
                        "filter[entity_id][]": chunk,
                        "limit": 250,
                        "page": page,
                    },
                    timeout=12,
                )
            except Exception as exc:
                logger.warning("Rüfət task chunk unavailable: %s", exc)
                break
            if response.status_code == 204:
                break
            if response.status_code != 200:
                logger.warning("Rüfət task chunk failed: %s", response.status_code)
                break
            payload = response.json()
            batch = payload.get("_embedded", {}).get("tasks", []) or []
            rows.extend(row for row in batch if isinstance(row, dict))
            if len(batch) < 250 and not payload.get("_links", {}).get("next"):
                break
            page += 1
    return rows


def _format_rufat_deadline(deadline_ts: int | float | None, now: datetime) -> tuple[str, str, bool]:
    """Return the compact task deadline, full deadline and overdue flag."""
    try:
        ts = int(deadline_ts or 0)
    except (TypeError, ValueError):
        ts = 0
    if not ts:
        return "", "", False
    deadline_dt = datetime.fromtimestamp(ts, tz=BAKU_TZ)
    is_overdue = deadline_dt < now
    compact = deadline_dt.strftime("%d.%m %H:%M")
    if is_overdue:
        diff = now - deadline_dt
        hours = int(diff.total_seconds() // 3600)
        compact = f"{hours} saat gecikir" if hours > 0 else f"{int(diff.total_seconds() // 60)} dəq gecikir"
    return compact, deadline_dt.strftime("%d.%m.%Y %H:%M"), is_overdue


def _rufat_marker_name(task_text: str) -> str:
    """Normalize the optional employee marker at the beginning of a task."""
    match = re.match(
        r'^\[(Rüfət Həsənzadə|Soltan Abbasov|Hüseyn Səfərov|Nizami Qasımov|Rasim Əsgərov|Sərmayə Əhmədsoy|Asya Agayeva|Nuranə Şirinova|Texniki Dəstək|Texniki tapşırıq|Rüfət|Soltan|Hüseyn|Nizami|Rasim|Sərmayə|Asya|Nuranə|Texniki)(?::[\d.]+)?\]\s*',
        task_text or "",
    )
    if not match:
        return ""
    return {
        "Rüfət": "Rüfət Həsənzadə", "Soltan": "Soltan Abbasov", "Hüseyn": "Hüseyn Səfərov",
        "Nizami": "Nizami Qasımov", "Rasim": "Rasim Əsgərov", "Sərmayə": "Sərmayə Əhmədsoy",
        "Asya": "Asya Agayeva", "Nuranə": "Nuranə Şirinova", "Texniki": TECHNICAL_SUPPORT_NAME,
    }.get(match.group(1), match.group(1))


async def _load_personal_funnel_contact_ids() -> set[int]:
    """Contact IDs that already have a deal in Rüfət / Hüseyn / Rasim funnels."""
    ids: set[int] = set()
    for pid in employee_personal_pipeline_ids():
        cached = _personal_overview_cache.get(int(pid))
        deals = cached.get("deals") if isinstance(cached, dict) else None
        if isinstance(deals, list) and deals:
            for deal in deals:
                if not isinstance(deal, dict):
                    continue
                for row in deal.get("contacts") or []:
                    if not isinstance(row, dict):
                        continue
                    try:
                        ids.add(int(row.get("id")))
                    except (TypeError, ValueError):
                        continue
            continue
        page = 1
        while True:
            try:
                response = await _kommo_get_async(
                    f"{KOMMO_BASE_URL}/api/v4/leads",
                    params={
                        "filter[pipeline_id]": int(pid),
                        "with": "contacts",
                        "limit": 250,
                        "page": page,
                    },
                    timeout=12,
                )
            except Exception as exc:
                logger.warning("Personal funnel %s page %s unavailable: %s", pid, page, exc)
                break
            if response.status_code == 204:
                break
            if response.status_code != 200:
                logger.warning("Personal funnel %s page %s failed: %s", pid, page, response.status_code)
                break
            batch = response.json().get("_embedded", {}).get("leads", []) or []
            for lead in batch:
                if not isinstance(lead, dict):
                    continue
                for contact in (lead.get("_embedded") or {}).get("contacts", []) or []:
                    try:
                        ids.add(int(contact.get("id")))
                    except (TypeError, ValueError):
                        continue
            if len(batch) < 250:
                break
            page += 1
    return ids


async def build_rufat_overview(stage_key: str | None = None, *, owner_chat_id: int | None = None) -> dict:
    """Load one personal Kommo funnel for local stage switching and task lists."""
    owner = get_funnel_owner(owner_chat_id or RUFAT_CHAT_ID) or get_funnel_owner(RUFAT_CHAT_ID)
    pipeline_id = int(owner["pipeline_id"])
    funnel_stages = owner["stages"]
    funnel_names = owner["stage_names"]
    owner_name = owner["name"]
    if stage_key is not None and stage_key not in funnel_stages:
        stage_key = next(iter(funnel_stages), None)

    async def _load_all_rufat_leads() -> list[dict]:
        """Fetch every page from the pipeline so no stage is loaded on demand."""
        all_leads: list[dict] = []
        page = 1
        while True:
            response = await _kommo_get_async(
                f"{KOMMO_BASE_URL}/api/v4/leads",
                params={
                    "filter[pipeline_id]": pipeline_id,
                    "with": "contacts",
                    "limit": 250,
                    "page": page,
                },
                timeout=12,
            )
            # Kommo can respond with 204 when a page beyond the last one is requested.
            if response.status_code == 204:
                break
            if response.status_code == 429:
                await asyncio.sleep(1.5)
                response = await _kommo_get_async(
                    f"{KOMMO_BASE_URL}/api/v4/leads",
                    params={
                        "filter[pipeline_id]": pipeline_id,
                        "with": "contacts",
                        "limit": 250,
                        "page": page,
                    },
                    timeout=12,
                )
            if response.status_code == 204:
                break
            if response.status_code != 200:
                logger.error("Rüfət leads fetch failed: %s body=%s", response.status_code, (response.text or "")[:200])
                raise RuntimeError(f"Rüfət leads fetch failed: {response.status_code}")
            batch = response.json().get("_embedded", {}).get("leads", []) or []
            all_leads.extend(batch)
            if len(batch) < 250:
                break
            page += 1
        return all_leads

    leads = await _load_all_rufat_leads()
    if pipeline_id == int(NIZAMI_PIPELINE_ID):
        hide_contacts = await _load_personal_funnel_contact_ids()
        employee_pipelines = employee_personal_pipeline_ids()
        filtered_leads = []
        for lead in leads:
            if _lead_pipeline_id(lead) in employee_pipelines:
                continue
            linked_ids = set()
            for contact in (lead.get("_embedded") or {}).get("contacts", []) or []:
                try:
                    linked_ids.add(int(contact.get("id")))
                except (TypeError, ValueError):
                    continue
            if hide_contacts and linked_ids & hide_contacts:
                continue
            filtered_leads.append(lead)
        leads = filtered_leads

    status_to_key = {status_id: key for key, status_id in funnel_stages.items()}
    stage_counts = {stage_key: 0} if stage_key else {key: 0 for key in funnel_stages}
    lead_by_id: dict[int, dict] = {}
    lead_by_contact_id: dict[int, dict] = {}
    for lead in leads:
        try:
            lead_id = int(lead.get("id"))
        except (TypeError, ValueError):
            continue
        lead_by_id[lead_id] = lead
        # Kommo may return status_id as either an integer or a JSON string.
        # Normalize it before looking up the Rüfət pipeline stage; otherwise
        # valid deals silently receive an empty stage_key and disappear from
        # every visible stage in the web app.
        try:
            normalized_status_id = int(lead.get("status_id", 0) or 0)
        except (TypeError, ValueError):
            normalized_status_id = 0
        stage_key = status_to_key.get(normalized_status_id, "")
        if stage_key:
            stage_counts[stage_key] += 1
        for contact in lead.get("_embedded", {}).get("contacts", []) or []:
            try:
                contact_id = int(contact.get("id"))
            except (TypeError, ValueError):
                continue
            lead_by_contact_id.setdefault(contact_id, lead)

    # Fetch all linked contacts in bounded batches; one failed contact cannot hide a deal.
    contact_ids = {
        int(contact.get("id"))
        for lead in leads
        for contact in lead.get("_embedded", {}).get("contacts", []) or []
        if str(contact.get("id", "")).isdigit()
    }
    task_entity_ids = list(dict.fromkeys([*lead_by_id.keys(), *lead_by_contact_id.keys()]))
    contacts_request = asyncio.create_task(_load_rufat_contacts(contact_ids))
    notes_request = asyncio.create_task(_load_rufat_latest_notes(
        set(lead_by_id.keys()),
        {cid: int(lead.get("id") or 0) for cid, lead in lead_by_contact_id.items() if int(lead.get("id") or 0)},
    ))
    tasks_request = asyncio.create_task(_load_rufat_open_tasks(task_entity_ids))
    contacts = await contacts_request
    deals = []
    for lead_id, lead in lead_by_id.items():
        lead_contacts = lead.get("_embedded", {}).get("contacts", []) or []
        first_contact_id = None
        if lead_contacts:
            try:
                first_contact_id = int(lead_contacts[0].get("id"))
            except (TypeError, ValueError):
                pass
        # Keep the contact fields separate from the deal title, with embedded fallback.
        embedded_contact = next((c for c in lead_contacts if c.get("id") == first_contact_id), {})
        contact = contacts.get(first_contact_id or 0, embedded_contact or {"name": "", "custom_fields_values": []})
        contact_rows = []
        all_phones = []
        for linked_contact in lead_contacts:
            linked_id = linked_contact.get("id")
            full_contact = contacts.get(int(linked_id), linked_contact) if str(linked_id).isdigit() else linked_contact
            phones = _contact_phones(full_contact)
            all_phones.extend(phone for phone in phones if phone not in all_phones)
            contact_rows.append({"id": linked_id, "name": full_contact.get("name", ""), "phones": phones})
        phone = all_phones[0] if all_phones else ""
        # Normalize the status for this particular lead.  Do not reuse the
        # normalized_status_id variable from the previous counting loop: that
        # would assign every deal the stage of the last lead in the response.
        try:
            status_id = int(lead.get("status_id", 0) or 0)
        except (TypeError, ValueError):
            status_id = 0
        source = extract_menbe(contact) or extract_menbe(lead)
        deals.append({
            "id": lead_id,
            "pipeline_id": pipeline_id,
            "stage_key": status_to_key.get(status_id, ""),
            "stage_name": funnel_names.get(status_id, "Naməlum mərhələ"),
            "contact_name": contact.get("name", ""), "phone": phone, "phones": all_phones,
            "contacts": contact_rows,
            "source": source, "menbe": source,
            "created_at": lead.get("created_at", 0), "updated_at": lead.get("updated_at", 0),
            "last_note": "", "last_client_message": "", "last_incoming_at": 0, "chat_channel": "", "contact_avatar": _first_avatar_url(contact), "task_desc": "", "deadline": "", "deadline_ts": 0,
            "voice_url": f"/api/voice/{lead_id}" if str(lead_id) in _voice_urls else "",
            "kommo_link": f"{KOMMO_BASE_URL}/leads/detail/{lead_id}",
        })
    attach_utm_and_partner(deals, lead_by_id, contacts)
    deals.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
    deals_by_stage = {key: [] for key in funnel_stages}
    for deal in deals:
        stage_deals = deals_by_stage.get(deal.get("stage_key"))
        if stage_deals is not None:
            stage_deals.append(deal)

    # Attach all open tasks and use the nearest/most recent one as the compact summary.
    #
    # The Sövdələşmələr tab is the historical Şamil/Rüfət funnel.  Tasks linked
    # to its deals can be assigned to different Kommo users (for example Admin
    # after the employee-account migration), so filtering only by the legacy
    # Sahə Meneceri responsible_user_id silently hid valid deal tasks from
    # Rüfət's Tapşırıqlar tab.
    _all_tasks = await tasks_request
    _rufat_tasks = [
        task for task in _all_tasks
        if not task.get("is_completed")
    ]
    task_by_lead: dict[int, list[dict]] = {}
    for task in _rufat_tasks:
        entity_id = task.get("entity_id")
        entity_type = task.get("entity_type", "contacts")
        related_lead = lead_by_id.get(int(entity_id)) if entity_type == "leads" and entity_id else lead_by_contact_id.get(int(entity_id)) if entity_id else None
        if related_lead:
            related_id = int(related_lead["id"])
            task_by_lead.setdefault(related_id, []).append(task)
    for related_tasks in task_by_lead.values():
        related_tasks.sort(key=lambda task: (int(task.get("complete_till", 0) or 0) == 0, int(task.get("complete_till", 0) or 0), -int(task.get("created_at", 0) or 0)))
    note_by_lead, client_message_by_lead, channel_by_lead, talk_updated, avatar_by_lead, incoming_at_by_lead, outside_by_lead = await notes_request
    for deal in deals:
        lead_id = int(deal["id"])
        related_tasks = task_by_lead.get(lead_id, [])
        task = related_tasks[0] if related_tasks else {}
        deadline_ts = int(task.get("complete_till", 0) or 0)
        deal["last_note"] = note_by_lead.get(lead_id, "")
        deal["last_client_message"] = client_message_by_lead.get(lead_id, "")
        deal["last_incoming_at"] = int((incoming_at_by_lead or {}).get(lead_id) or 0)
        deal["last_outgoing_at"] = _cloud_last_outgoing_at(lead_id)
        deal["chat_channel"] = channel_by_lead.get(lead_id, "")
        deal["contact_avatar"] = (avatar_by_lead or {}).get(lead_id, "") or deal.get("contact_avatar") or ""
        try:
            talk_ts = int((talk_updated or {}).get(lead_id) or 0)
        except (TypeError, ValueError):
            talk_ts = 0
        try:
            current = int(deal.get("updated_at") or 0)
        except (TypeError, ValueError):
            current = 0
        deal["chat_at"] = talk_ts
        if talk_ts > current:
            deal["updated_at"] = talk_ts
        deal["task_desc"] = task.get("text", "")
        deal["deadline_ts"] = deadline_ts
        deal["deadline"] = datetime.fromtimestamp(deadline_ts, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M") if deadline_ts else ""
        deal["tasks"] = [{
            "id": related.get("id"),
            "text": related.get("text", ""),
            "complete_till": int(related.get("complete_till", 0) or 0),
            "deadline": datetime.fromtimestamp(int(related.get("complete_till", 0) or 0), tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M") if related.get("complete_till") else "",
            "task_type_id": related.get("task_type_id"),
        } for related in related_tasks]
    _apply_cloud_inbox_to_deals(deals, pipeline_id)
    if pipeline_id == int(NIZAMI_PIPELINE_ID):
        _inject_outside_funnel_talk_deals(
            deals,
            outside_by_lead or {},
            incoming_at_by_lead or {},
            client_message_by_lead or {},
            channel_by_lead or {},
            avatar_by_lead or {},
            talk_updated or {},
        )

    now = datetime.now(tz=BAKU_TZ)
    normal_tasks: list[dict] = []
    reminder_tasks: list[dict] = []
    task_type_names = {
        1: "Əlaqə saxla", 2: "Görüş", 3263995: "Təqdimat", 3263999: "Quraşdırma",
        3267595: "Zəng et", 4229224: "Cavab gözlənilir", 4232112: "aktiv",
        4232108: "Import", XATIRLAT_TASK_TYPE_ID: "passiv",
    }
    for task in _rufat_tasks:
        try:
            entity_id = int(task.get("entity_id"))
        except (TypeError, ValueError):
            continue
        entity_type = task.get("entity_type", "contacts")
        lead = lead_by_id.get(entity_id) if entity_type == "leads" else lead_by_contact_id.get(entity_id)
        if not lead:
            continue
        task_text = task.get("text", "")
        # Do not filter by the optional employee marker here.  A task belongs
        # in Rüfət's list because it is attached to a deal in Sövdələşmələr;
        # the Kommo responsible user and legacy marker are not reliable after
        # the account migration.
        task_type_id = task.get("task_type_id", 1)
        if task_type_id == 4229224:
            continue
        lead_id = int(lead["id"])
        lead_contacts = lead.get("_embedded", {}).get("contacts", []) or []
        first_contact_id = None
        if lead_contacts:
            try:
                first_contact_id = int(lead_contacts[0].get("id"))
            except (TypeError, ValueError):
                pass
        contact = contacts.get(first_contact_id or 0, {"name": lead.get("name", "")})
        deal = next((row for row in deals if int(row.get("id") or 0) == lead_id), None)
        phones = list((deal or {}).get("phones") or []) or _contact_phones(contact)
        phone = phones[0] if phones else ""
        deadline_ts = task.get("complete_till", 0)
        compact_deadline, full_deadline, is_overdue = _format_rufat_deadline(deadline_ts, now)
        price_match = re.match(r"^\[(?:[^:\]]*:)?(\d+(?:\.\d+)?)\]", task_text)
        item = {
            "id": task.get("id"), "task_id": task.get("id"),
            "title": "⚠️ Gecikmiş tapşırıq" if is_overdue else "📋 Aktiv tapşırıq",
            "desc": task_text, "task_text": task_text,
            "price": price_match.group(1) if price_match else "",
            "time": compact_deadline, "deadline": full_deadline, "deadline_ts": deadline_ts,
            "is_overdue": is_overdue, "entity_id": entity_id, "entity_type": entity_type,
            "lead_id": lead_id, "lead_name": lead.get("name", ""),
            "contact_name": contact.get("name", ""), "phone": phone, "phones": phones,
            "partner": (deal or {}).get("partner") or "",
            "utm": (deal or {}).get("utm") or (deal or {}).get("utm_tag") or "",
            "utm_tag": (deal or {}).get("utm_tag") or (deal or {}).get("utm") or "",
            "source": (deal or {}).get("utm") or (deal or {}).get("source") or "",
            "menbe": (deal or {}).get("utm") or (deal or {}).get("menbe") or "",
            "chat_channel": (deal or {}).get("chat_channel") or "",
            "responsible": owner_name, "assigneeName": owner_name, "assignee_name": owner_name,
            "kommo_link": f"{KOMMO_BASE_URL}/leads/detail/{lead_id}", "complete_till": deadline_ts,
            "task_type_name": task_type_names.get(task_type_id, ""), "task_type_id": task_type_id,
            "last_note": "", "priority": "",
            "stage_name": funnel_names.get(lead.get("status_id", 0), ""),
            "voice_url": f"/api/voice/{lead_id}" if str(lead_id) in _voice_urls else "", "created_by": "",
        }
        (reminder_tasks if task_type_id == XATIRLAT_TASK_TYPE_ID else normal_tasks).append(item)

    normal_tasks.sort(key=lambda item: (not item["is_overdue"], item["complete_till"] or 9999999999))
    reminder_tasks.sort(key=lambda item: (not item["is_overdue"], item["complete_till"] or 9999999999))
    return {
        "tasks": normal_tasks, "gozleme": reminder_tasks, "deals": deals,
        "deals_by_stage": deals_by_stage, "stage_counts": stage_counts,
        "user_name": owner_name,
        "stage_key": None,
        "ui_stages": owner.get("ui_stages") or [(key, funnel_names.get(sid, key)) for key, sid in funnel_stages.items()],
        "pipeline_id": pipeline_id,
        "funnel_owner": owner_name,
        "partners": get_partner_list_for_chat(owner.get("chat_id")),
    }


_rufat_overview_lock = asyncio.Lock()
_personal_overview_cache: dict[int, dict] = {}
_personal_overview_cache_at: dict[int, float] = {}
_RUFAT_OVERVIEW_CACHE_TTL = 30.0


def invalidate_rufat_overview_cache() -> None:
    """Drop in-memory personal funnel snapshots after a mutating action."""
    _personal_overview_cache.clear()
    _personal_overview_cache_at.clear()


def patch_rufat_overview_deal_stage(lead_id: int, stage_key: str) -> None:
    """Keep a cached overview aligned with a successful Kommo stage PATCH."""
    for overview in _personal_overview_cache.values():
        if not isinstance(overview, dict):
            continue
        pipeline_id = int(overview.get("pipeline_id") or 0)
        stages, names, _ui = load_pipeline_stage_maps(pipeline_id) if pipeline_id else (RUFAT_STAGES, RUFAT_STAGE_NAMES, [])
        if stage_key not in stages:
            continue
        status_id = stages[stage_key]
        stage_name = names.get(status_id, "Naməlum mərhələ")
        deal = None
        for item in overview.get("deals") or []:
            if not isinstance(item, dict):
                continue
            try:
                current_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if current_id != lead_id:
                continue
            item["stage_key"] = stage_key
            item["stage_name"] = stage_name
            deal = item
            break
        if deal is None:
            continue
        by_stage = overview.get("deals_by_stage")
        if isinstance(by_stage, dict):
            for key, items in list(by_stage.items()):
                by_stage[key] = [
                    item for item in (items or [])
                    if isinstance(item, dict) and int(item.get("id") or 0) != lead_id
                ]
            by_stage.setdefault(stage_key, []).append(deal)
            overview["stage_counts"] = {key: len(by_stage.get(key) or []) for key in stages}
        for task in list(overview.get("tasks") or []) + list(overview.get("gozleme") or []):
            if not isinstance(task, dict):
                continue
            try:
                task_lead_id = int(task.get("lead_id") or 0)
            except (TypeError, ValueError):
                continue
            if task_lead_id == lead_id:
                task["stage_name"] = stage_name
        _personal_overview_cache_at[pipeline_id] = _time_module.monotonic()
        return


async def get_rufat_overview(*, force: bool = False, owner_chat_id: int | None = None) -> dict:
    """Return a personal funnel workspace, reusing a short in-memory snapshot."""
    owner = get_funnel_owner(owner_chat_id or RUFAT_CHAT_ID) or get_funnel_owner(RUFAT_CHAT_ID)
    pipeline_id = int(owner["pipeline_id"])
    async with _rufat_overview_lock:
        now = _time_module.monotonic()
        cached = _personal_overview_cache.get(pipeline_id)
        cached_at = _personal_overview_cache_at.get(pipeline_id, 0.0)
        if cached is not None:
            _apply_cloud_inbox_to_deals(cached.get("deals") or [], pipeline_id)
            _schedule_chat_tail_warm(cached.get("deals") or [])
            if not force or now - cached_at < _RUFAT_OVERVIEW_CACHE_TTL:
                return _overview_with_partners(cached, owner)
        try:
            overview = await build_rufat_overview(owner_chat_id=owner["chat_id"])
            _personal_overview_cache[pipeline_id] = overview
            _personal_overview_cache_at[pipeline_id] = now
            _schedule_chat_tail_warm(overview.get("deals") or [])
            return _overview_with_partners(overview, owner)
        except Exception as exc:
            logger.error("Personal overview rebuild failed pipeline=%s: %s", pipeline_id, exc)
            if cached is not None:
                _apply_cloud_inbox_to_deals(cached.get("deals") or [], pipeline_id)
                return _overview_with_partners(cached, owner)
            raise


def _overview_with_partners(overview: dict, owner: dict) -> dict:
    payload = dict(overview or {})
    payload["partners"] = get_partner_list_for_chat((owner or {}).get("chat_id"))
    return payload


_DEAL_SHARE_TTL_SEC = 30 * 24 * 3600


def _deal_share_secret() -> bytes:
    return str(TELEGRAM_TOKEN or "").encode("utf-8")


def make_deal_share_token(lead_id: int) -> str:
    exp = int(_time_module.time()) + _DEAL_SHARE_TTL_SEC
    payload = f"{int(lead_id)}.{exp}"
    digest = hmac.new(_deal_share_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{digest}"


def parse_deal_share_token(token: str) -> int | None:
    raw = unquote(str(token or "")).strip().strip("\"'").rstrip("/").rstrip(".,)")
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    lead_s, exp_s, digest = parts
    try:
        lead_id = int(lead_s)
        exp = int(exp_s)
    except (TypeError, ValueError):
        return None
    if exp < int(_time_module.time()):
        return None
    payload = f"{lead_id}.{exp}"
    expected = hmac.new(_deal_share_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, digest):
        return None
    return lead_id


def _user_can_view_personal_lead(chat_id: int, lead: dict) -> bool:
    if is_admin(chat_id):
        return True
    lid = int(lead.get("id") or 0)
    if lid and _sent_messages_for_lead(lid):
        return True
    try:
        pipeline_id = int(lead.get("pipeline_id") or 0)
    except (TypeError, ValueError):
        return False
    if pipeline_id not in all_personal_pipeline_ids():
        owner = get_funnel_owner(chat_id)
        if owner and int(owner.get("pipeline_id") or 0) == int(NIZAMI_PIPELINE_ID):
            return True
        return False
    owner = get_funnel_owner(chat_id)
    return bool(owner and int(owner["pipeline_id"]) == pipeline_id)


_TELEPHONY_MEDIA_SUFFIXES = (
    "sipuni.com",
    "mango-office.ru",
    "onlinepbx.ru",
    "telphin.ru",
    "zadarma.com",
    "binotel.ua",
    "uiscom.ru",
    "callgear.ru",
    "voximplant.com",
    "mcn.ru",
    "novofon.com",
    "gravitel.ru",
    "beeline-cloud.ru",
    "proto.online",
    "uis.st",
)


def _media_host(raw: str) -> str:
    try:
        parsed = urlparse(str(raw or "").strip())
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed.netloc.split("@")[-1].split(":")[0].casefold()


def _is_allowed_kommo_media_url(raw: str) -> bool:
    host = _media_host(raw)
    if not host:
        return False
    return (
        host.endswith(".kommo.com")
        or host.endswith(".amocrm.ru")
        or host.endswith(".amocrm.com")
        or host.endswith(".amojo.ru")
        or host in {"kommo.com", "amocrm.ru", "amocrm.com", "amojo.kommo.com", "amojo.amocrm.ru"}
    )


_PROFILE_PHOTO_SUFFIXES = (
    "lookaside.fbsbx.com",
    "fbcdn.net",
    "cdninstagram.com",
    "pps.whatsapp.net",
    "mms.whatsapp.net",
    "graph.facebook.com",
)


def _is_allowed_avatar_url(raw: str) -> bool:
    if _is_allowed_media_url(raw):
        return True
    host = _media_host(raw)
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _PROFILE_PHOTO_SUFFIXES)


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def _is_allowed_media_url(raw: str) -> bool:
    if _is_allowed_kommo_media_url(raw):
        return True
    host = _media_host(raw)
    if not host:
        return False
    if _host_matches(host, _TELEPHONY_MEDIA_SUFFIXES) or _host_matches(host, _PROFILE_PHOTO_SUFFIXES):
        return True
    return host == "whatsapp.net" or host.endswith(".whatsapp.net")


def _as_absolute_media_url(raw: str) -> str:
    val = str(raw or "").strip()
    if not val:
        return ""
    if val.startswith("//"):
        val = "https:" + val
    if val.startswith("/"):
        val = f"{KOMMO_BASE_URL}{val}"
    parsed = urlparse(val)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return val
    return ""


def _walk_media_urls(value, depth: int = 0) -> list[str]:
    if depth > 5 or value is None:
        return []
    if isinstance(value, str):
        found = _as_absolute_media_url(value)
        return [found] if found else []
    if isinstance(value, dict):
        preferred = []
        for key in ("link", "url", "media", "recording", "recording_link", "call_record", "download_link", "file_link", "href"):
            preferred.extend(_walk_media_urls(value.get(key), depth + 1))
        extra = []
        skip = {
            "author", "sender", "avatar", "profile", "icon", "recipient",
            "chat", "talk", "phone", "uniq", "source", "duration",
        }
        for key, inner in value.items():
            if str(key).casefold() in skip:
                continue
            extra.extend(_walk_media_urls(inner, depth + 1))
        return preferred + extra
    if isinstance(value, list):
        found: list[str] = []
        for inner in value:
            found.extend(_walk_media_urls(inner, depth + 1))
        return found
    return []


def _extract_media_url(params: dict) -> str:
    if not isinstance(params, dict):
        return ""
    for url in _walk_media_urls(params):
        return url
    return ""


def _sniff_media_type(body: bytes, hinted: str = "", src: str = "", name: str = "") -> str:
    head = body[:16] if body else b""
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if head.startswith(b"RIFF") and b"WAVE" in head:
        return "audio/wav"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "audio/mp4"
    hinted = str(hinted or "").split(";")[0].strip().lower()
    if hinted.startswith("audio/") and hinted != "application/octet-stream":
        return hinted
    low = f"{src} {name}".lower()
    if any(token in low for token in (".mp3", ".mpeg")):
        return "audio/mpeg"
    if ".wav" in low:
        return "audio/wav"
    if any(token in low for token in (".m4a", ".mp4", ".aac")):
        return "audio/mp4"
    if any(token in low for token in (".ogg", ".oga", ".opus")):
        return "audio/ogg"
    return hinted or "application/octet-stream"


def _drive_file_download_url(file_uuid: str) -> str:
    uuid = str(file_uuid or "").strip()
    if not uuid:
        return ""
    try:
        info_resp = requests.get(
            f"https://drive-g.kommo.com/v1.0/files/{uuid}",
            headers={"Authorization": f"Bearer {KOMMO_TOKEN}"},
            timeout=8,
        )
        if info_resp.status_code == 200:
            return str((info_resp.json().get("_links") or {}).get("download", {}).get("href") or "").strip()
    except Exception as exc:
        logger.warning("Drive file %s failed: %s", uuid, exc)
    return ""


def _extract_nested_text(value, depth: int = 0) -> str:
    if depth > 5 or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "message", "body", "content", "caption", "service"):
            found = _extract_nested_text(value.get(key), depth + 1)
            if found:
                return found
    if isinstance(value, list):
        for inner in value:
            found = _extract_nested_text(inner, depth + 1)
            if found:
                return found
    return ""


def _extract_file_uuid(params, depth: int = 0) -> str:
    if depth > 4 or params is None:
        return ""
    if isinstance(params, dict):
        for key in ("file_uuid", "uuid", "fileId", "file_id", "attachment_id"):
            val = str(params.get(key) or "").strip()
            if len(val) >= 32:
                return val
        file_obj = params.get("file") if isinstance(params.get("file"), dict) else {}
        for key in ("file_uuid", "uuid", "fileId", "file_id", "attachment_id"):
            val = str(file_obj.get(key) or "").strip()
            if len(val) >= 32:
                return val
        for key, inner in params.items():
            if isinstance(inner, (dict, list)):
                found = _extract_file_uuid(inner, depth + 1)
                if found:
                    return found
    elif isinstance(params, list):
        for inner in params:
            found = _extract_file_uuid(inner, depth + 1)
            if found:
                return found
    return ""


def _looks_audio_name(name: str) -> bool:
    n = str(name or "").casefold()
    return any(n.endswith(ext) for ext in (".ogg", ".mp3", ".m4a", ".wav", ".opus", ".aac", ".oga", ".webm", ".mpeg")) or "voice" in n or "audio" in n


def _looks_image_name(name: str) -> bool:
    n = str(name or "").casefold()
    return any(n.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp")) or any(token in n for token in ("photo", "image", "şəkil", "sekil", "картинка", "фото"))


def _is_media_notice_text(text: str) -> bool:
    low = str(text or "").casefold()
    return "вам пришло медиа" in low or "медиа-сообщение" in low or "пожалуйста, подождите загрузки" in low or "пожалуйста, подождите" in low


def _looks_profile_photo(file_name: str, message_type: str = "") -> bool:
    blob = f"{file_name} {message_type}".casefold()
    return any(token in blob for token in ("profile", "avatar", "userpic", "фото профил", "pp.jpg", "pp.jpeg"))


def _first_avatar_url(*blobs, depth: int = 0) -> str:
    if depth > 4:
        return ""
    for blob in blobs:
        if isinstance(blob, str):
            url = _as_absolute_media_url(blob)
            if url and _is_allowed_avatar_url(url) and ("/profiles/" in url.lower() or _looks_profile_photo(url, "avatar")):
                return url
            continue
        if not isinstance(blob, dict):
            continue
        for key in ("avatar", "avatar_url", "photo", "picture", "icon"):
            raw = blob.get(key)
            if isinstance(raw, dict):
                raw = raw.get("url") or raw.get("link") or raw.get("src") or ""
            url = _as_absolute_media_url(str(raw or ""))
            if url and _is_allowed_avatar_url(url):
                return url
        embedded = blob.get("_embedded") if isinstance(blob.get("_embedded"), dict) else {}
        for item in (embedded.get("contacts") or [])[:3]:
            found = _first_avatar_url(item, depth=depth + 1)
            if found:
                return found
        for nested_key in ("author", "peer", "client", "contact", "chat"):
            nested = blob.get(nested_key)
            if isinstance(nested, dict):
                found = _first_avatar_url(nested, depth=depth + 1)
                if found:
                    return found
    return ""


def _deal_fmt_ts(ts) -> str:
    try:
        value = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M")


def _call_is_missed(blob: dict | None, text: str = "") -> bool:
    params = blob if isinstance(blob, dict) else {}
    status = " ".join(
        str(params.get(key) or "")
        for key in ("call_status", "status", "result", "state", "outcome")
    ).lower()
    folded = f"{status} {text}".lower()
    if any(token in folded for token in (
        "missed", "no_answer", "noanswer", "unanswered", "cancel", "busy",
        "пропущ", "buraxılmış", "cavabsız",
    )):
        return True
    if "duration" not in params and "duration_sec" not in params:
        return False
    raw = params.get("duration", params.get("duration_sec"))
    try:
        seconds = int(raw or 0)
    except (TypeError, ValueError):
        seconds = 0
    return seconds <= 0


def _is_chat_note_type(note_type: str, file_name: str = "", message_type: str = "") -> bool:
    ntype = str(note_type or "").casefold()
    combined = f"{ntype} {file_name} {message_type}".casefold()
    if ntype in {"sms_in", "sms_out", "amomail_message", "facebook_message", "instagram_business", "chat", "whatsapp", "telegram", "viber", "waba", "call_in", "call_out", "call_missed"}:
        return True
    if ntype in {"attachment", "file"}:
        return _looks_audio_name(file_name) or _looks_image_name(file_name) or message_type in {"voice", "audio", "picture", "video"}
    if message_type in {"voice", "audio", "picture", "video", "file", "sticker"}:
        return True
    if _looks_audio_name(file_name) or _looks_audio_name(combined) or _looks_image_name(file_name):
        return True
    return "message" in ntype or ntype.startswith("sms")


def _format_deal_note(note: dict, entity_type: str = "leads") -> dict | None:
    if not isinstance(note, dict):
        return None
    ntype = str(note.get("note_type") or "common")
    params = note.get("params") if isinstance(note.get("params"), dict) else {}
    created = int(note.get("created_at") or 0)
    file_name = str(params.get("file_name") or params.get("original_name") or "").strip()
    file_uuid = _extract_file_uuid(params)
    media = _extract_media_url(params)
    text = ""
    message_type = "text"
    if ntype == "common":
        text = str(params.get("text") or "").strip()
    elif ntype in {"call_in", "call_out"}:
        label = "Gələn zəng" if ntype == "call_in" else "Gedən zəng"
        phone = str(params.get("phone") or params.get("uniq") or "").strip()
        duration = params.get("duration")
        text = f"{label} {phone}".strip()
        if duration:
            text += f" ({duration}s)"
        if _call_is_missed(params, text):
            text = "Buraxılmış zəng"
            message_type = "call_missed"
        else:
            message_type = "audio"
    elif ntype in {"attachment", "file"}:
        text = _extract_nested_text(params) or file_name or "Fayl"
        if _looks_audio_name(file_name):
            message_type = "audio"
        elif _looks_image_name(file_name) or _looks_image_name(text):
            message_type = "picture"
        else:
            message_type = "file"
    else:
        text = _extract_nested_text(params)
        media = media or _extract_media_url(params)
    if not text and not media and not file_uuid:
        return None
    if _looks_audio_name(file_name) and not text:
        text = "Səs mesajı"
        message_type = "audio"
    elif _looks_image_name(file_name) and not text:
        text = file_name or "Şəkil"
        message_type = "picture"
    is_media_notice = _is_media_notice_text(text)
    audio_hint = message_type in {"audio", "voice", "ptt"} or _looks_audio_name(file_name)
    if is_media_notice and audio_hint:
        message_type = "audio"
        text = "Səs mesajı"
    elif is_media_notice:
        message_type = "picture"
        text = file_name or "Şəkil" if (media or file_uuid) else "📷 Şəkil"
    is_chat = _is_chat_note_type(ntype, file_name, message_type)
    if ntype == "common" and ("səs yazısı" in text.casefold() or "🎤" in text or is_media_notice):
        is_chat = True
        if not is_media_notice and message_type == "text":
            message_type = "audio"
    return {
        "id": note.get("id"),
        "entity_id": note.get("entity_id") or None,
        "entity_type": "contacts" if str(entity_type or "").startswith("contact") else "leads",
        "type": ntype,
        "text": text,
        "created_at": created,
        "created": _deal_fmt_ts(created),
        "media_url": media,
        "file_uuid": file_uuid,
        "file_name": file_name,
        "message_type": message_type,
        "is_chat": is_chat,
        "incoming": _note_is_incoming(ntype),
        "channel": _note_channel_key(note) or "",
        "author": KOMMO_USERS.get(note.get("created_by") or note.get("responsible_user_id"), "") or "",
        "msgid": _first_msgid(params, note),
    }


def _fetch_raw_notes(entity_type: str, entity_id: int, pages: int = 3) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, pages + 1):
        try:
            resp = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{int(entity_id)}/notes",
                headers=HEADERS,
                params={"limit": 250, "page": page, "order[created_at]": "desc"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Deal notes %s/%s failed: %s", entity_type, entity_id, exc)
            break
        if resp.status_code == 204:
            break
        if resp.status_code != 200:
            logger.warning("Deal notes %s/%s status %s", entity_type, entity_id, resp.status_code)
            break
        notes = resp.json().get("_embedded", {}).get("notes", []) or []
        if not notes:
            break
        rows.extend(note for note in notes if isinstance(note, dict))
        if len(notes) < 250:
            break
    return rows


def _find_raw_note(note_id: int, lead: dict | None = None, entity_type: str = "", entity_id: int = 0) -> dict | None:
    wanted = int(note_id or 0)
    if not wanted:
        return None
    targets: list[tuple[str, int]] = []
    if entity_type and entity_id:
        targets.append((str(entity_type), int(entity_id)))
    if lead:
        try:
            targets.append(("leads", int(lead.get("id") or 0)))
        except (TypeError, ValueError):
            pass
        for cid in _lead_contact_ids(lead):
            targets.append(("contacts", int(cid)))
    seen: set[tuple[str, int]] = set()
    for etype, eid in targets:
        if not eid or (etype, eid) in seen:
            continue
        seen.add((etype, eid))
        for note in _fetch_raw_notes(etype, eid):
            try:
                if int(note.get("id") or 0) == wanted:
                    return note
            except (TypeError, ValueError):
                continue
    return None


def _note_recording_src(note: dict) -> tuple[str, str]:
    params = note.get("params") if isinstance(note.get("params"), dict) else {}
    return _extract_media_url(params), _extract_file_uuid(params)


_entity_notes_cache: dict[tuple[str, int, int], tuple[float, list[dict]]] = {}
_entity_notes_lock = threading.Lock()
_ENTITY_NOTES_TTL = 90.0


def _fetch_entity_notes(entity_type: str, entity_id: int, pages: int = 1) -> list[dict]:
    eid = int(entity_id or 0)
    etype = str(entity_type or "leads")
    if not eid:
        return []
    pages = max(1, min(int(pages or 1), 3))
    cache_key = (etype, eid, pages)
    now = _time_module.monotonic()
    with _entity_notes_lock:
        cached = _entity_notes_cache.get(cache_key)
        if cached and (now - cached[0] < _ENTITY_NOTES_TTL):
            return list(cached[1])
    rows: list[dict] = []
    for page in range(1, pages + 1):
        try:
            resp = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/{etype}/{eid}/notes",
                headers=HEADERS,
                params={"limit": 50, "page": page, "order[created_at]": "desc"},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Deal notes %s/%s failed: %s", etype, eid, exc)
            break
        if resp.status_code == 204:
            break
        if resp.status_code != 200:
            logger.warning("Deal notes %s/%s status %s", etype, eid, resp.status_code)
            break
        notes = resp.json().get("_embedded", {}).get("notes", []) or []
        if not notes:
            break
        for note in notes:
            formatted = _format_deal_note(note, etype)
            if formatted:
                rows.append(formatted)
        if len(notes) < 50:
            break
    with _entity_notes_lock:
        _entity_notes_cache[cache_key] = (now, rows)
    return rows


def _fetch_open_tasks_for_entities(entity_ids: list[int]) -> list[dict]:
    ids = [int(item) for item in entity_ids if str(item).isdigit()]
    if not ids:
        return []
    rows: list[dict] = []
    seen: set[int] = set()
    chunk_size = 40
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start:start + chunk_size]
        try:
            resp = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/tasks",
                headers=HEADERS,
                params={"filter[is_completed]": 0, "filter[entity_id][]": chunk, "limit": 250},
                timeout=10,
            )
        except Exception as exc:
            logger.warning("Deal tasks failed: %s", exc)
            continue
        if resp.status_code != 200:
            continue
        for task in resp.json().get("_embedded", {}).get("tasks", []) or []:
            if task.get("is_completed"):
                continue
            try:
                task_id = int(task.get("id"))
            except (TypeError, ValueError):
                continue
            if task_id in seen:
                continue
            seen.add(task_id)
            deadline_ts = int(task.get("complete_till", 0) or 0)
            rows.append({
                "id": task_id,
                "text": task.get("text") or "",
                "complete_till": deadline_ts,
                "deadline": _deal_fmt_ts(deadline_ts),
                "task_type_id": task.get("task_type_id"),
                "responsible": KOMMO_USERS.get(task.get("responsible_user_id"), ""),
            })
    rows.sort(key=lambda item: (int(item.get("complete_till") or 0) == 0, int(item.get("complete_till") or 0)))
    return rows


_lead_talks_cache: dict[int, tuple[float, list[dict]]] = {}
_lead_talks_cache_lock = threading.Lock()
_LEAD_TALKS_CACHE_TTL = 60.0
_lead_open_talk: dict[int, int] = {}
_last_talk_tail_check: dict[int, float] = {}


def _fetch_talks(lead_id: int, contact_ids: list[int] | None = None, *, include_contacts: bool = True) -> list[dict]:
    lid = int(lead_id or 0)
    now = _time_module.monotonic()
    if lid:
        with _lead_talks_cache_lock:
            cached = _lead_talks_cache.get(lid)
            if cached and (now - cached[0] < _LEAD_TALKS_CACHE_TTL):
                return list(cached[1])

    talks: dict[int, dict] = {}

    def _ingest(params: dict) -> None:
        try:
            resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/talks", headers=HEADERS, params=params, timeout=10)
        except Exception as exc:
            logger.warning("Deal talks failed: %s", exc)
            return
        if resp.status_code != 200:
            logger.warning("Deal talks status %s", resp.status_code)
            return
        for talk in resp.json().get("_embedded", {}).get("talks", []) or []:
            try:
                talk_id = int(talk.get("talk_id") or talk.get("id"))
            except (TypeError, ValueError):
                continue
            talks[talk_id] = talk

    if lid:
        _ingest({"filter[entity_id][]": lid, "filter[entity_type]": "lead", "limit": 50})
    if not talks and include_contacts:
        for contact_id in contact_ids or []:
            _ingest({"filter[entity_id][]": int(contact_id), "filter[entity_type]": "contact", "limit": 50})
            if talks:
                break
            _ingest({"filter[contact_id][]": int(contact_id), "limit": 50})
            if talks:
                break

    res = list(talks.values())
    if lid and res:
        with _lead_talks_cache_lock:
            _lead_talks_cache[lid] = (now, res)
    return res


def _talk_is_open(talk: dict) -> bool:
    if not isinstance(talk, dict):
        return False
    if talk.get("is_closed") in {True, 1, "1", "true", "True"}:
        return False
    if talk.get("closed_at"):
        return False
    status = str(talk.get("status") or "").lower()
    if status in {"2", "closed"}:
        return False
    return True


NIZAMI_WHATSAPP_NUMBER = "994502072240"
CHAT_CHANNEL_LABELS = {
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "telegram": "Telegram",
}


RUFAT_WHATSAPP_NUMBER = os.environ.get("RUFAT_WHATSAPP_NUMBER", "994102135105")
# Kommo keeps both WhatsApp Lite numbers behind one channel and never tells the
# API which number owns a talk, so the sender number follows the Telegram user.
WA_SENDER_NUMBERS = {
    RUFAT_CHAT_ID: RUFAT_WHATSAPP_NUMBER,
    **{cid: RUFAT_WHATSAPP_NUMBER for cid in RUFAT_COMPAT_CHAT_IDS},
}


def _wa_sender_digits_for_chat(chat_id) -> str:
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return NIZAMI_WHATSAPP_NUMBER
    mapped = WA_SENDER_NUMBERS.get(cid)
    if mapped:
        return re.sub(r"\D", "", str(mapped))
    # Everyone except Rüfət writes through the shared WhatsApp Lite number.
    return NIZAMI_WHATSAPP_NUMBER


def _known_wa_sender_digits() -> set[str]:
    return {
        re.sub(r"\D", "", str(RUFAT_WHATSAPP_NUMBER)),
        re.sub(r"\D", "", str(NIZAMI_WHATSAPP_NUMBER)),
    }


def _hinted_wa_sender_digits(chat_id, hinted) -> str:
    default = _wa_sender_digits_for_chat(chat_id)
    wanted = re.sub(r"\D", "", str(hinted or ""))
    if not wanted or wanted not in _known_wa_sender_digits():
        return default
    rufat_digits = re.sub(r"\D", "", str(RUFAT_WHATSAPP_NUMBER))
    if wanted != rufat_digits:
        return wanted
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return default
    if is_admin(cid) or cid in RUFAT_COMPAT_CHAT_IDS:
        return wanted
    return default


def _wa_display_number(digits: str) -> str:
    clean = re.sub(r"\D", "", str(digits or ""))
    return f"+{clean}" if len(clean) >= 8 else ""


def _talk_blob(talk: dict) -> str:
    try:
        return json.dumps(talk, ensure_ascii=False).lower()
    except Exception:
        return str(talk or "").lower()


def _talk_has_digits(talk: dict, digits: str) -> bool:
    wanted = re.sub(r"\D", "", str(digits or ""))
    if len(wanted) < 8:
        return False
    blob = re.sub(r"\D", "", _talk_blob(talk))
    return wanted in blob or wanted[-9:] in blob


def _join_channel_fields(*parts) -> str:
    return " ".join(str(part or "").strip() for part in parts if str(part or "").strip()).lower()


def _origin_channel_key(blob: str) -> str:
    text = str(blob or "").lower()
    if not text.strip():
        return ""
    if "tiktok" in text or "tik tok" in text:
        return "tiktok"
    if "telegram" in text or "tg " in text or text.endswith(" tg"):
        return "telegram"
    if "instagram" in text:
        return "instagram"
    if "facebook" in text or "fb messenger" in text:
        return "facebook"
    if any(token in text for token in ("whatsapp", "waba", "amocrmwa", "amojo", "whats app", "whats-app")):
        return "whatsapp"
    return ""


def _talk_channel_key(talk: dict) -> str:
    chat = talk.get("chat") if isinstance(talk.get("chat"), dict) else {}
    origin = " ".join(
        str(part or "")
        for part in (
            talk.get("origin"),
            talk.get("source"),
            chat.get("type"),
            chat.get("origin"),
            talk.get("entity_type"),
        )
    ).strip().lower()
    key = _origin_channel_key(origin)
    if key:
        return key
    return "whatsapp" if origin in {"", "chat", "capi", "wa", "im"} or not origin.strip() else "other"


def _note_is_incoming(note_type: str) -> bool:
    text = str(note_type or "").lower()
    if "outgoing" in text or text in {"sms_out", "call_out"}:
        return False
    return "incoming" in text or text in {"sms_in", "call_in"}


def _note_channel_key(note: dict | None) -> str:
    data = note if isinstance(note, dict) else {}
    note_type = str(data.get("note_type") or data.get("type") or "").lower()
    if note_type in {"sms_in", "sms_out", "amomail_message"}:
        return ""
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    blob = _join_channel_fields(
        note_type,
        params.get("service"),
        params.get("origin"),
        params.get("source"),
        params.get("messenger"),
        params.get("type"),
        params.get("provider"),
        params.get("waba"),
    )
    key = _origin_channel_key(blob)
    if key:
        return key
    if note_type in {"incoming_chat_message", "outgoing_chat_message"}:
        return "whatsapp"
    return ""


def _talk_reply_rank(talk: dict) -> tuple:
    channel = _talk_channel_key(talk)
    wa = 1 if channel == "whatsapp" else 0
    opened = 1 if _talk_is_open(talk) else 0
    try:
        updated = int(talk.get("updated_at") or talk.get("created_at") or 0)
    except (TypeError, ValueError):
        updated = 0
    return (opened, wa, updated)


def _channels_from_talks(talks: list[dict], sender_digits: str = "") -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for talk in talks or []:
        key = _talk_channel_key(talk)
        if key not in CHAT_CHANNEL_LABELS:
            continue
        grouped.setdefault(key, []).append(talk)
    rows = []
    order = ["whatsapp", "instagram", "facebook", "tiktok", "telegram"]
    for key in order:
        group = grouped.get(key) or []
        if not group:
            continue
        if key == "whatsapp" and sender_digits:
            matched = [talk for talk in group if _talk_has_digits(talk, sender_digits)]
            if matched:
                group = matched
        group.sort(key=_talk_reply_rank, reverse=True)
        talk = group[0]
        rows.append({
            "key": key,
            "label": CHAT_CHANNEL_LABELS[key],
            "talk_id": _talk_id_of(talk),
            "chat_id": _talk_chat_id(talk),
            "open": _talk_is_open(talk),
            "sender_phone": _wa_display_number(sender_digits) if key == "whatsapp" else "",
        })
    return rows


def _talk_chat_id(talk: dict) -> str:
    if not isinstance(talk, dict):
        return ""
    for key in ("chat_id", "conversation_id"):
        val = str(talk.get(key) or "").strip()
        if val:
            return val
    origin = talk.get("origin")
    if isinstance(origin, dict):
        val = str(origin.get("chat_id") or origin.get("id") or "").strip()
        if val:
            return val
    chat = talk.get("chat")
    if isinstance(chat, dict):
        val = str(chat.get("id") or chat.get("chat_id") or "").strip()
        if val:
            return val
    return ""


def _talk_id_of(talk: dict) -> int:
    try:
        return int(talk.get("talk_id") or talk.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _ranked_reply_talk_ids(talks: list[dict]) -> list[int]:
    ranked = []
    seen: set[int] = set()
    for talk in talks or []:
        talk_id = _talk_id_of(talk)
        if not talk_id or talk_id in seen:
            continue
        seen.add(talk_id)
        ranked.append((_talk_reply_rank(talk), talk_id))
    ranked.sort(reverse=True)
    return [item[1] for item in ranked]


def _kommo_error_detail(resp) -> str:
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        # "Request validation failed" alone says nothing; keep the field paths.
        fields = []
        for group in payload.get("validation-errors") or []:
            for err in (group or {}).get("errors") or []:
                path = str((err or {}).get("path") or "").strip()
                text = str((err or {}).get("detail") or (err or {}).get("code") or "").strip()
                fields.append(f"{path}: {text}".strip(": "))
        detail = str(payload.get("detail") or payload.get("title") or payload.get("error") or "").strip()
        if fields:
            return f"{detail} — {'; '.join(fields)}".strip(" —")[:240]
        if detail:
            return detail
    return (resp.text or "")[:240]


def _resolve_channel_talk(lead: dict, channel: str, sender_digits: str = "", hinted: int = 0) -> tuple[int, str]:
    try:
        hinted_id = int(hinted or 0)
    except (TypeError, ValueError):
        hinted_id = 0
    try:
        lid = int(lead.get("id") or 0)
    except (TypeError, ValueError):
        lid = 0
    talks = _fetch_talks(lid, _lead_contact_ids(lead))
    wanted = str(channel or "whatsapp").strip().lower() or "whatsapp"
    channels = _channels_from_talks(talks, sender_digits)
    row = next((item for item in channels if item.get("key") == wanted), None)
    if hinted_id:
        hinted_row = next((item for item in channels if int(item.get("talk_id") or 0) == hinted_id), None)
        chat_id = str((hinted_row or row or {}).get("chat_id") or "")
        return hinted_id, chat_id
    if row and row.get("talk_id"):
        return int(row.get("talk_id") or 0), str(row.get("chat_id") or "")
    ranked = _ranked_reply_talk_ids(talks)
    talk_id = int(ranked[0]) if ranked else 0
    fallback = next((item for item in channels if int(item.get("talk_id") or 0) == talk_id), None)
    return talk_id, str((fallback or {}).get("chat_id") or "")


def _resolve_channel_talk_id(lead: dict, channel: str, sender_digits: str = "", hinted: int = 0) -> int:
    talk_id, _chat_id = _resolve_channel_talk(lead, channel, sender_digits, hinted)
    return talk_id


def _looks_chat_message_id(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text.lower().startswith("note-"):
        return False
    if "wamid" in text.lower():
        return True
    if re.fullmatch(r"[0-9a-fA-F-]{16,}", text):
        return True
    return not text.isdigit() or len(text) >= 6


def _first_msgid(*blobs) -> str:
    keys = ("msgid", "client_id", "amojo_msgid", "message_id")
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for key in keys:
            val = str(blob.get(key) or "").strip()
            if val and not val.lower().startswith("note-"):
                return val
        nested = blob.get("message") if isinstance(blob.get("message"), dict) else {}
        for key in keys:
            val = str(nested.get(key) or "").strip()
            if val and not val.lower().startswith("note-"):
                return val
    return ""


def _chat_message_ids(message: dict, nested: dict) -> tuple[str, str, str]:
    msg_id = str(nested.get("id") or message.get("id") or "").strip()
    msgid = _first_msgid(nested, message)
    wamid = _find_wamid(message)
    external = wamid or msgid or (msg_id if _looks_chat_message_id(msg_id) else "")
    return msg_id, msgid, external


def _first_wamid(*values) -> str:
    for raw in values:
        found = _find_wamid(raw)
        if found:
            return found
    return ""


def _kommo_quote_ids(*values) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for raw in values:
        val = str(raw or "").strip()
        if not val or val in seen:
            continue
        if val.lower().startswith("note-"):
            continue
        seen.add(val)
        rows.append(val)
    rows.sort(key=lambda item: (item.isdigit(), len(item) < 12, item.startswith("wamid") is False, item))
    return rows


def _kommo_quote_bodies(payload: dict, reply_id: str, reply_text: str = "") -> list[dict]:
    quote_id = str(reply_id or "").strip()
    preview = str(reply_text or "").strip()[:200]
    if not quote_id:
        return [payload]
    nested = {"id": quote_id, "msgid": quote_id}
    if preview:
        nested["type"] = "text"
        nested["text"] = preview
    return [
        {**payload, "reply_to": {"message": nested}},
        {**payload, "reply_to": {"message": {"id": quote_id}}},
        {**payload, "reply_to": {"message": {"msgid": quote_id}}},
    ]


def _post_kommo_json(url: str, body: dict, extra_headers: dict | None = None):
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    return _http.post(url, headers=headers, json=body, timeout=20)


def _is_official_talk_send(url: str) -> bool:
    text = str(url or "")
    return "/api/v4/talks/" in text and text.endswith("/send_message")


def _send_kommo_talk_message(
    talk_id: int,
    text: str,
    attachment: dict | None = None,
    reply_to: str = "",
    chat_id: str = "",
    reply_text: str = "",
    plain_fallback: bool = True,
) -> tuple[bool, str, int]:
    # Official send_message is text/attachment only and silently drops reply_to.
    # Native quotes go through amojo / ajax / talks messages only.
    att_clean = dict(attachment) if isinstance(attachment, dict) else None
    is_voice = bool(att_clean and att_clean.pop("is_voice", False))
    payload: dict = {}
    if text:
        payload["text"] = text
    elif att_clean:
        if not is_voice:
            payload["text"] = "Fayl"
    if att_clean:
        payload["attachment"] = att_clean
    if not payload:
        return False, "Mesaj boş ola bilməz", 0
    reply_id = str(reply_to or "").strip()
    talk_send = f"{KOMMO_BASE_URL}/api/v4/talks/{int(talk_id)}/send_message"
    talk_messages = f"{KOMMO_BASE_URL}/api/v4/talks/{int(talk_id)}/messages"
    ajax_talk_send = f"{KOMMO_BASE_URL}/ajax/v4/talks/{int(talk_id)}/send_message"
    ajax_talk_send_v2 = f"{KOMMO_BASE_URL}/ajax/v2/talks/{int(talk_id)}/send_message"
    attempts: list[tuple[str, dict, dict]] = []
    if reply_id:
        quote_bodies = _kommo_quote_bodies(payload, reply_id, reply_text)
        primary = quote_bodies[0]
        msgid_only = {**payload, "reply_to": {"message": {"msgid": reply_id}}}
        id_only = {**payload, "reply_to": {"message": {"id": reply_id}}}
        ajax = {"X-Requested-With": "XMLHttpRequest"}
        if chat_id:
            amojo_body = {"text": payload.get("text", text or ""), "reply_to": {"message": {"id": reply_id, "msgid": reply_id}}}
            if att_clean:
                amojo_body["attachment"] = att_clean
            if reply_text:
                amojo_body["reply_to"]["message"]["type"] = "text"
                amojo_body["reply_to"]["message"]["text"] = str(reply_text)[:200]
            if is_voice and att_clean:
                voice_amojo = dict(amojo_body)
                voice_amojo["attachment"] = {**att_clean, "type": "voice"}
                attempts.append((f"https://amojo.kommo.com/v2/chats/{chat_id}", voice_amojo, {}))
            attempts.append((f"https://amojo.kommo.com/v2/chats/{chat_id}", amojo_body, {}))
            if is_voice and att_clean:
                voice_pri = {**primary, "attachment": {**att_clean, "type": "voice"}}
                attempts.append((f"{KOMMO_BASE_URL}/ajax/v4/chats/{chat_id}/send", voice_pri, ajax))
                attempts.append((f"{KOMMO_BASE_URL}/ajax/v2/chats/{chat_id}/send", voice_pri, ajax))
            attempts.append((f"{KOMMO_BASE_URL}/ajax/v4/chats/{chat_id}/send", primary, ajax))
            attempts.append((f"{KOMMO_BASE_URL}/ajax/v2/chats/{chat_id}/send", primary, ajax))
        if is_voice and att_clean:
            voice_pri = {**primary, "attachment": {**att_clean, "type": "voice"}}
            attempts.append((talk_messages, voice_pri, {}))
            attempts.append((f"{KOMMO_BASE_URL}/ajax/v4/talks/{int(talk_id)}/messages", voice_pri, ajax))
            attempts.append((ajax_talk_send, voice_pri, ajax))
            attempts.append((ajax_talk_send_v2, voice_pri, ajax))
        attempts.append((talk_messages, primary, {}))
        attempts.append((talk_messages, msgid_only, {}))
        attempts.append((talk_messages, id_only, {}))
        attempts.append((f"{KOMMO_BASE_URL}/ajax/v4/talks/{int(talk_id)}/messages", primary, ajax))
        attempts.append((ajax_talk_send, primary, ajax))
        attempts.append((ajax_talk_send_v2, primary, ajax))
        if plain_fallback:
            if is_voice and att_clean and not text:
                voice_att = {**att_clean, "type": "voice"}
                audio_att = {**att_clean, "type": "audio"}
                file_att = {**att_clean, "type": "file"}
                attempts.append((talk_send, {"attachment": voice_att}, {}))
                attempts.append((talk_send, {"text": "", "attachment": voice_att}, {}))
                attempts.append((talk_send, {"attachment": audio_att}, {}))
                attempts.append((talk_send, {"text": "", "attachment": audio_att}, {}))
                attempts.append((talk_send, {"attachment": file_att}, {}))
                attempts.append((talk_send, {"text": "", "attachment": file_att}, {}))
            else:
                attempts.append((talk_send, payload, {}))
    if not reply_id:
        if is_voice and att_clean and not text:
            voice_att = {**att_clean, "type": "voice"}
            audio_att = {**att_clean, "type": "audio"}
            file_att = {**att_clean, "type": "file"}
            ajax = {"X-Requested-With": "XMLHttpRequest"}
            attempts.append((talk_send, {"attachment": voice_att}, {}))
            attempts.append((talk_send, {"text": "", "attachment": voice_att}, {}))
            attempts.append((ajax_talk_send, {"attachment": voice_att}, ajax))
            attempts.append((ajax_talk_send_v2, {"attachment": voice_att}, ajax))
            attempts.append((talk_send, {"attachment": audio_att}, {}))
            attempts.append((talk_send, {"text": "", "attachment": audio_att}, {}))
            attempts.append((talk_send, {"attachment": file_att}, {}))
            attempts.append((talk_send, {"text": "", "attachment": file_att}, {}))
        else:
            attempts.append((talk_send, payload, {}))
    last_detail = ""
    last_body = ""
    last_status = 0
    for url, body, extra in attempts:
        try:
            resp = _post_kommo_json(url, body, extra)
        except Exception as exc:
            logger.warning("Talk send failed: %s", exc)
            last_detail = "Kommo çata göndərmək alınmadı."
            last_status = 0
            continue
        last_status = resp.status_code
        if 200 <= resp.status_code < 300:
            return True, "", resp.status_code
        last_detail = _kommo_error_detail(resp)
        last_body = (resp.text or "")[:400]
        logger.warning("Talk send status %s %s: %s | %s", resp.status_code, url, last_detail, last_body)
    if last_status == 403:
        folded = f"{last_detail} {last_body}".lower()
        if "external chat" in folded:
            return False, "Kommo tokenində çat göndərmə hüququ yoxdur (Sending to external chats).", last_status
        return False, "Kommo müvəqqəti icazə vermir. Bir azdan yenidən yoxlayın.", last_status
    if last_status == 429:
        return False, "Kommo sorğu limitini aşıb. Bir azdan yenidən yoxlayın.", last_status
    if last_status == 422:
        return False, "Çat bağlıdır. Kommo-da söhbəti açın.", last_status
    if last_status == 402:
        return False, "Kommo Chat API limiti bitib.", last_status
    return False, last_detail or "Mesaj göndərilmədi.", last_status


def _send_kommo_talk_text(talk_id: int, text: str) -> tuple[bool, str, int]:
    return _send_kommo_talk_message(talk_id, text)


_DRIVE_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _is_drive_uuid(value: str) -> bool:
    return bool(_DRIVE_UUID_RE.fullmatch(str(value or "").strip()))


def _drive_uuids_from_payload(payload: dict | None) -> tuple[str, str]:
    data = payload if isinstance(payload, dict) else {}
    file_uuid = str(data.get("uuid") or "").strip()
    version_uuid = str(data.get("version_uuid") or data.get("file_version_uuid") or "").strip()
    links = data.get("_links") or {}
    self_href = str(((links.get("self") or {}).get("href")) or "")
    version_href = str(((links.get("download_version") or {}).get("href")) or "")
    download_href = str(((links.get("download") or {}).get("href")) or "")
    self_ids = _DRIVE_UUID_RE.findall(self_href)
    version_ids = _DRIVE_UUID_RE.findall(version_href)
    download_ids = _DRIVE_UUID_RE.findall(download_href)
    if not _is_drive_uuid(file_uuid) and self_ids:
        file_uuid = self_ids[-1]
    if not _is_drive_uuid(file_uuid) and download_ids:
        file_uuid = download_ids[-1]
    if not _is_drive_uuid(version_uuid) and version_ids:
        version_uuid = version_ids[-1]
    if not _is_drive_uuid(file_uuid) or not _is_drive_uuid(version_uuid):
        logger.warning("Drive upload missing UUIDs keys=%s version=%r", list(data.keys()), version_uuid)
        return "", ""
    return file_uuid, version_uuid


def _upload_kommo_drive_bytes(filename: str, content: bytes, content_type: str) -> tuple[str, str]:
    file_size = len(content or b"")
    if file_size <= 0:
        return "", ""
    effective_mime = content_type or "application/octet-stream"
    low_name = str(filename or "").lower()
    if (low_name.endswith((".opus", ".ogg", ".oga")) or effective_mime in {"audio/opus", "audio/webm", "application/octet-stream"}) and "audio" in (effective_mime + low_name):
        effective_mime = "audio/ogg"
    auth_h = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
    drive_url = "https://drive-g.kommo.com"
    sess_resp = requests.post(
        f"{drive_url}/v1.0/sessions",
        headers={**auth_h, "Content-Type": "application/json"},
        json={"file_name": filename, "file_size": file_size, "content_type": effective_mime},
        timeout=12,
    )
    if sess_resp.status_code != 200:
        logger.warning("Drive session failed: %s %s", sess_resp.status_code, sess_resp.text[:400])
        return "", ""
    sess_data = sess_resp.json()
    upload_url = sess_data.get("upload_url")
    max_part = int(sess_data.get("max_part_size") or 524288)
    offset = 0
    file_uuid = ""
    version_uuid = ""
    while offset < file_size:
        chunk = content[offset:offset + max_part]
        up_resp = requests.post(
            upload_url,
            headers={**auth_h, "Content-Type": "application/octet-stream"},
            data=chunk,
            timeout=20,
        )
        if up_resp.status_code != 200:
            logger.warning("Drive upload failed: %s %s", up_resp.status_code, up_resp.text[:400])
            return "", ""
        up_data = up_resp.json() if up_resp.content else {}
        if up_data.get("next_url"):
            upload_url = up_data["next_url"]
        parsed = _drive_uuids_from_payload(up_data)
        if parsed[0] and parsed[1]:
            file_uuid, version_uuid = parsed
        offset += max_part
    if not _is_drive_uuid(file_uuid) or not _is_drive_uuid(version_uuid):
        logger.warning("Drive upload finished without UUIDs file=%r version=%r", file_uuid, version_uuid)
        return "", ""
    return file_uuid, version_uuid


def _normalize_wa_number(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "994" + digits[1:]
    return digits


WA_CLOUD_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
# Both business numbers are official WABA. Rüfət keeps the env phone id;
# everyone else sends from the shared number.
WA_CLOUD_SENDER_DIGITS = re.sub(r"\D", "", os.environ.get("WHATSAPP_CLOUD_SENDER", RUFAT_WHATSAPP_NUMBER))
NIZAMI_WA_PHONE_NUMBER_ID = "1352169527974549"
_wa_send_phone = threading.local()
WA_WABA_ID = str(os.environ.get("WHATSAPP_WABA_ID") or "1603840074450579").strip()
WA_VERIFY_TOKEN = str(
    os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
    or os.environ.get("WHATSAPP_VERIFY_TOKEN")
    or "beintaskbot_webhook_verify_2026"
).strip()
WA_VERIFY_TOKENS = {
    token for token in (
        WA_VERIFY_TOKEN,
        "beintaskbot_webhook_verify_2026",
        "bein-wa-hook",
    ) if token
}


def _wa_cloud_credentials() -> tuple[str, str]:
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN") or os.environ.get("WHATSAPP_TOKEN") or ""
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or ""
    return str(token).strip(), str(phone_id).strip()


def _wa_phone_ids() -> dict[str, str]:
    ids = {re.sub(r"\D", "", str(NIZAMI_WHATSAPP_NUMBER)): NIZAMI_WA_PHONE_NUMBER_ID}
    rufat = re.sub(r"\D", "", str(RUFAT_WHATSAPP_NUMBER))
    _token, env_id = _wa_cloud_credentials()
    if rufat and env_id:
        ids[rufat] = env_id
    return ids


def _wa_phone_id_for_digits(sender_digits: str = "") -> str:
    ids = _wa_phone_ids()
    wanted = re.sub(r"\D", "", str(sender_digits or ""))
    rufat = re.sub(r"\D", "", str(RUFAT_WHATSAPP_NUMBER))
    if wanted and wanted in ids:
        return ids[wanted]
    if wanted and wanted == rufat:
        return ""
    return ids.get(re.sub(r"\D", "", str(NIZAMI_WHATSAPP_NUMBER)), "")


def _wa_active_phone_id() -> str:
    chosen = str(getattr(_wa_send_phone, "phone_id", "") or "").strip()
    if chosen:
        return chosen
    _token, phone_id = _wa_cloud_credentials()
    return phone_id or NIZAMI_WA_PHONE_NUMBER_ID


def _kommo_external_send_denied(error: str) -> bool:
    return "Sending to external chats" in str(error or "")


def _wa_cloud_configured() -> bool:
    token, _phone_id = _wa_cloud_credentials()
    return bool(token and _wa_phone_id_for_digits(""))


def _wa_cloud_ready(sender_digits: str = "") -> bool:
    token, _phone_id = _wa_cloud_credentials()
    if not token:
        return False
    return bool(_wa_phone_id_for_digits(sender_digits))


_WA_LAST_HOOK = {"at": 0, "fields": [], "in": 0, "lead": 0, "sub": ""}


def _wa_graph_get(path: str, params: dict | None = None):
    token, _phone_id = _wa_cloud_credentials()
    if not token:
        return {}
    try:
        resp = requests.get(
            f"https://graph.facebook.com/{WA_CLOUD_API_VERSION}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=12,
        )
        return resp.json() if resp.content else {}
    except Exception as exc:
        logger.warning("WhatsApp Graph GET %s failed: %s", path, exc)
        return {}


def _wa_ensure_subscribed() -> None:
    """Ask Meta to send live WABA inbound to this app, not only dashboard tests."""
    token, phone_id = _wa_cloud_credentials()
    if not token:
        return
    waba = str(WA_WABA_ID or os.environ.get("WHATSAPP_WABA_ID") or "").strip()
    if not waba and phone_id:
        data = _wa_graph_get(phone_id, {"fields": "whatsapp_business_account"})
        account = data.get("whatsapp_business_account") if isinstance(data, dict) else {}
        if isinstance(account, dict):
            waba = str(account.get("id") or "").strip()
    if not waba:
        logger.warning("WhatsApp WABA id missing; live inbound may stay on Kommo")
        _WA_LAST_HOOK["sub"] = "nowaba"
        return
    try:
        resp = requests.post(
            f"https://graph.facebook.com/{WA_CLOUD_API_VERSION}/{waba}/subscribed_apps",
            headers={"Authorization": f"Bearer {token}"},
            timeout=12,
        )
        listed = _wa_graph_get(f"{waba}/subscribed_apps")
        apps = listed.get("data") if isinstance(listed, dict) else []
        count = len(apps) if isinstance(apps, list) else 0
        _WA_LAST_HOOK["sub"] = f"{resp.status_code}/{count}"
        logger.info("WhatsApp subscribed_apps status=%s apps=%s", resp.status_code, count)
    except Exception as exc:
        _WA_LAST_HOOK["sub"] = "err"
        logger.warning("WhatsApp subscribe failed: %s", exc)


_WA_CLOUD_ERRORS = {
    131047: "24 saatlıq pəncərə bağlıdır. Müştəri yazana qədər yalnız şablon göndərilə bilər.",
    131026: "Nömrə WhatsApp mesajını qəbul etmir.",
    131051: "Bu mesaj tipi WhatsApp-da dəstəklənmir.",
    131052: "Fayl WhatsApp tərəfindən qəbul edilmədi.",
    133010: "WhatsApp nömrəsi qeydiyyatdan keçməyib.",
    190: "WhatsApp tokeni etibarsızdır. Yenilə.",
    368: "WhatsApp nömrəsi müvəqqəti bloklanıb.",
    80007: "WhatsApp limiti aşıldı, bir az sonra yenidən yoxla.",
}


def _wa_cloud_error_text(resp) -> str:
    try:
        payload = resp.json() or {}
    except Exception:
        return str(getattr(resp, "text", ""))[:240]
    error = payload.get("error") if isinstance(payload, dict) else {}
    if isinstance(error, dict):
        try:
            code = int(error.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        friendly = _WA_CLOUD_ERRORS.get(code)
        if friendly:
            return friendly
        detail = str(error.get("error_user_msg") or error.get("message") or "").strip()
        extra = error.get("error_data") if isinstance(error.get("error_data"), dict) else {}
        specifics = str((extra or {}).get("details") or "").strip()
        if specifics and specifics.lower() not in detail.lower():
            detail = f"{detail} {specifics}".strip()
        if detail:
            return detail[:400]
    return str(getattr(resp, "text", ""))[:240]


def _wa_cloud_post(body: dict, phone_id: str = "") -> tuple[bool, str, str]:
    """POST to the Cloud API messages endpoint; returns ok, error and wamid."""
    token, _env_phone = _wa_cloud_credentials()
    phone_id = str(phone_id or _wa_active_phone_id() or "").strip()
    if not token or not phone_id:
        return False, "WhatsApp Cloud API konfiqurasiya olunmayıb.", ""
    url = f"https://graph.facebook.com/{WA_CLOUD_API_VERSION}/{phone_id}/messages"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", **body},
            timeout=20,
        )
    except Exception as exc:
        logger.warning("WhatsApp Cloud send failed: %s", exc)
        return False, "WhatsApp API xətası.", ""
    if resp.status_code in {200, 201}:
        try:
            payload = resp.json() or {}
        except Exception:
            payload = {}
        messages = payload.get("messages") if isinstance(payload, dict) else []
        wamid = ""
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            wamid = str(messages[0].get("id") or "")
        return True, "", wamid
    detail = _wa_cloud_error_text(resp)
    logger.warning("WhatsApp Cloud status %s: %s", resp.status_code, detail)
    return False, detail or "WhatsApp mesajı göndərilmədi.", ""


def _wa_cloud_upload_media(content: bytes, filename: str, mime: str, phone_id: str = "") -> tuple[str, str]:
    """Upload media to Cloud API and return its media id."""
    token, _env_phone = _wa_cloud_credentials()
    phone_id = str(phone_id or _wa_active_phone_id() or "").strip()
    if not token or not phone_id:
        return "", "WhatsApp Cloud API konfiqurasiya olunmayıb."
    url = f"https://graph.facebook.com/{WA_CLOUD_API_VERSION}/{phone_id}/media"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename or "file", content, mime or "application/octet-stream")},
            timeout=60,
        )
    except Exception as exc:
        logger.warning("WhatsApp Cloud media upload failed: %s", exc)
        return "", "Fayl WhatsApp-a yüklənmədi."
    if resp.status_code in {200, 201}:
        try:
            media_id = str(((resp.json() or {}).get("id")) or "")
        except Exception:
            media_id = ""
        if media_id:
            try:
                if len(content) <= 12 * 1024 * 1024:
                    if len(_WA_MEDIA_BYTES) >= 40:
                        _WA_MEDIA_BYTES.pop(next(iter(_WA_MEDIA_BYTES)), None)
                    _WA_MEDIA_BYTES[media_id] = (content, mime)
            except Exception:
                pass
            return media_id, ""
    detail = _wa_cloud_error_text(resp)
    logger.warning("WhatsApp Cloud media status %s: %s", resp.status_code, detail)
    return "", detail or "Fayl WhatsApp-a yüklənmədi."


_WA_UPLOADED_MEDIA: dict[str, tuple[float, str]] = {}
_WA_UPLOADED_MEDIA_TTL = 6 * 24 * 3600.0


def _wa_media_id_for_link(link: str, kind: str) -> tuple[str, str]:
    """Download a template image and upload it, so WhatsApp can attach the file."""
    key = str(link or "").strip()
    now = _time_module.time()
    cached = _WA_UPLOADED_MEDIA.get(key)
    if cached and now - cached[0] < _WA_UPLOADED_MEDIA_TTL and cached[1]:
        return cached[1], ""
    try:
        resp = requests.get(key, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:
        logger.warning("WhatsApp media download failed: %s", exc)
        return "", "Şəkil yüklənmədi."
    raw = resp.content or b""
    if resp.status_code != 200 or not raw:
        logger.warning("WhatsApp media download status %s", resp.status_code)
        return "", "Şəkil yüklənmədi."
    if len(raw) > 5 * 1024 * 1024:
        return "", "Şəkil 5MB-dan böyükdür."
    mime = str(resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if kind == "video":
        if not mime.startswith("video/"):
            mime = "video/mp4"
        ext = "mp4"
    else:
        if raw[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        elif raw[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            mime = "image/webp"
        elif not mime.startswith("image/"):
            mime = "image/jpeg"
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
    media_id, error = _wa_cloud_upload_media(raw, f"card.{ext}", mime)
    if not media_id:
        return "", error or "Şəkil WhatsApp-a yüklənmədi."
    _WA_UPLOADED_MEDIA[key] = (now, media_id)
    return media_id, ""


def _is_ogg_bytes(raw: bytes) -> bool:
    return bool(raw) and raw[:4] == b"OggS"


def _ffmpeg_run(cmd: list[str], dst_path: str) -> bytes:
    result = subprocess.run(cmd, capture_output=True, timeout=90)
    if result.returncode == 0 and os.path.exists(dst_path) and os.path.getsize(dst_path) > 0:
        with open(dst_path, "rb") as handle:
            data = handle.read()
        if data:
            return data
    if result.stderr:
        logger.warning("ffmpeg voice convert failed: %s", result.stderr[:240])
    return b""


def _ffmpeg_voice_for_cloud(raw: bytes, filename: str) -> tuple[bytes, str, str, bool]:
    """Turn a browser recording into Cloud-ready audio. Prefer Ogg/Opus voice notes."""
    if _is_ogg_bytes(raw):
        return raw, "audio/ogg", "voice.ogg", True
    suffix = os.path.splitext(str(filename or ""))[1].lower()
    if suffix not in {".webm", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".mp3", ".wav"}:
        suffix = ".webm"
    src_path = ""
    ogg_path = ""
    m4a_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
            src.write(raw)
            src_path = src.name
        ogg_path = src_path + ".ogg"
        m4a_path = src_path + ".m4a"
        ogg_cmds = [
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-c:a", "copy", "-f", "ogg", ogg_path,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-ac", "1", "-ar", "48000",
                "-c:a", "libopus", "-b:a", "32k", "-application", "voip", "-f", "ogg", ogg_path,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "webm", "-i", src_path, "-vn", "-ac", "1", "-ar", "48000",
                "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", ogg_path,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-ac", "1", "-ar", "48000",
                "-c:a", "opus", "-b:a", "32k", "-f", "ogg", ogg_path,
            ],
        ]
        for cmd in ogg_cmds:
            data = _ffmpeg_run(cmd, ogg_path)
            if _is_ogg_bytes(data):
                return data, "audio/ogg", "voice.ogg", True
        m4a_cmds = [
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-ac", "1", "-ar", "44100",
                "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", "-f", "ipod", m4a_path,
            ],
        ]
        for cmd in m4a_cmds:
            data = _ffmpeg_run(cmd, m4a_path)
            if data:
                return data, "audio/mp4", "voice.m4a", False
        return b"", "", "", False
    except Exception as exc:
        logger.warning("ffmpeg voice convert error: %s", exc)
        return b"", "", "", False
    finally:
        for path in (src_path, ogg_path, m4a_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _ffmpeg_voice_for_kommo(raw: bytes, filename: str) -> tuple[bytes, str, str]:
    """Turn voice recording into WhatsApp-compatible PTT Ogg/Opus voice note for Kommo WhatsApp."""
    suffix = os.path.splitext(str(filename or ""))[1].lower()
    if suffix not in {".webm", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".mp3", ".wav"}:
        suffix = ".ogg" if _is_ogg_bytes(raw) else ".webm"
    src_path = ""
    ogg_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
            src.write(raw)
            src_path = src.name
        ogg_path = src_path + ".ogg"
        cmds = [
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-ac", "1", "-ar", "48000",
                "-c:a", "libopus", "-b:a", "32k", "-application", "voip", "-f", "ogg", ogg_path,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "webm", "-i", src_path, "-vn", "-ac", "1", "-ar", "48000",
                "-c:a", "libopus", "-b:a", "32k", "-application", "voip", "-f", "ogg", ogg_path,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-c:a", "copy", "-f", "ogg", ogg_path,
            ],
        ]
        for cmd in cmds:
            data = _ffmpeg_run(cmd, ogg_path)
            if data and len(data) > 64 and _is_ogg_bytes(data):
                return data, "audio/ogg", _generate_ptt_filename()
        return raw, "audio/ogg", _generate_ptt_filename()
    except Exception as exc:
        logger.warning("ffmpeg voice to opus convert error: %s", exc)
        return raw, "audio/ogg", _generate_ptt_filename()
    finally:
        for p in (src_path, ogg_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


def _generate_ptt_filename() -> str:
    try:
        now_str = _time_module.strftime("%Y%m%d", _time_module.gmtime())
        seq = random.randint(1, 9999)
        return f"PTT-{now_str}-WA{seq:04d}.ogg"
    except Exception:
        return "voice.ogg"


def _is_mp3_bytes(raw: bytes) -> bool:
    return bool(raw) and (raw.startswith(b"ID3") or (len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0))


def _ffmpeg_voice_to_mp3(raw: bytes, filename: str) -> tuple[bytes, str, str]:
    """Turn voice recording into MP3 for universal playback in WhatsApp, Kommo, and browsers."""
    if _is_mp3_bytes(raw):
        return raw, "audio/mpeg", "voice.mp3"
    suffix = os.path.splitext(str(filename or ""))[1].lower()
    if suffix not in {".webm", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".mp3", ".wav"}:
        suffix = ".webm"
    src_path = ""
    mp3_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
            src.write(raw)
            src_path = src.name
        mp3_path = src_path + ".mp3"
        cmds = [
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-ac", "1", "-ar", "44100",
                "-c:a", "libmp3lame", "-b:a", "64k", mp3_path,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "webm", "-i", src_path, "-vn", "-ac", "1", "-ar", "44100",
                "-c:a", "libmp3lame", "-b:a", "64k", mp3_path,
            ],
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-c:a", "libmp3lame", "-b:a", "64k", mp3_path,
            ],
        ]
        for cmd in cmds:
            data = _ffmpeg_run(cmd, mp3_path)
            if data and len(data) > 64 and _is_mp3_bytes(data):
                return data, "audio/mpeg", "voice.mp3"
        m4a_path = src_path + ".m4a"
        m4a_cmds = [
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", src_path, "-vn", "-ac", "1", "-ar", "44100",
                "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", "-f", "ipod", m4a_path,
            ],
        ]
        for cmd in m4a_cmds:
            data = _ffmpeg_run(cmd, m4a_path)
            if data and len(data) > 64:
                return data, "audio/mp4", "voice.m4a"
        return raw, "audio/mpeg", "voice.mp3"
    except Exception as exc:
        logger.warning("ffmpeg voice to mp3 convert error: %s", exc)
        return raw, "audio/mpeg", "voice.mp3"
    finally:
        for p in (src_path, mp3_path, mp3_path.replace(".mp3", ".m4a")):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


_OLD_OGG_MP3_CACHE: dict[str, bytes] = {}


def _wa_cloud_media_kind(filename: str, content_type: str) -> str:
    name = f"{filename} {content_type}".lower()
    if str(content_type or "").startswith("image/") or re.search(r"\.(png|jpe?g|webp)$", name):
        return "image"
    if _looks_voice_upload(filename, content_type):
        return "audio"
    if str(content_type or "").startswith("video/") or re.search(r"\.(mp4|mov|3gp)$", name):
        return "video"
    return "document"


def _wa_cloud_send_text(phone: str, text: str, reply_to: str = "", phone_id: str = "") -> tuple[bool, str, str]:
    to = _normalize_wa_number(phone)
    if len(to) < 8:
        return False, "WhatsApp nömrəsi tapılmadı.", ""
    body: dict = {"recipient_type": "individual", "to": to, "type": "text",
                  "text": {"body": text, "preview_url": True}}
    quoted = str(reply_to or "").strip()
    if quoted:
        body["context"] = {"message_id": quoted}
    return _wa_cloud_post(body, phone_id)


_WA_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")
_WA_TEMPLATE_NAME_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_WA_TEMPLATES_CACHE: dict = {"at": 0.0, "wall": 0.0, "rows": []}
_WA_TEMPLATES_TTL = 3600.0
_WA_TEMPLATES_FILE = "wa_templates.json"
_WA_HEADER_MEDIA = {"IMAGE": "image", "VIDEO": "video", "DOCUMENT": "document"}


def _wa_template_placeholders(text: str) -> tuple[int, list[str]]:
    raw = str(text or "")
    numbers = [int(value) for value in _WA_TEMPLATE_VAR_RE.findall(raw)]
    names: list[str] = []
    for name in _WA_TEMPLATE_NAME_RE.findall(raw):
        if name not in names:
            names.append(name)
    return (max(numbers) if numbers else 0), names


def _wa_template_button_rows(raw_buttons) -> list[dict]:
    rows: list[dict] = []
    for index, button in enumerate(raw_buttons or []):
        if not isinstance(button, dict):
            continue
        button_type = str(button.get("type") or "").upper()
        button_url = str(button.get("url") or "")
        url_count, url_names = _wa_template_placeholders(button_url)
        rows.append({
            "index": index,
            "type": button_type,
            "text": str(button.get("text") or ""),
            "vars": 1 if button_type == "COPY_CODE" else url_count,
            "names": url_names,
            "needs_value": button_type == "COPY_CODE" or url_count > 0 or bool(url_names),
        })
    return rows


def _wa_example_https(component: dict) -> str:
    example = component.get("example") if isinstance(component.get("example"), dict) else {}
    candidates: list[str] = []
    for key in ("header_handle", "header_url", "header_link"):
        value = example.get(key)
        if isinstance(value, list):
            candidates.extend(str(item) for item in value)
        elif isinstance(value, str):
            candidates.append(value)
    for item in candidates:
        link = item.strip()
        if link.startswith("https://"):
            return link[:1000]
    return ""


def _wa_template_card(card: dict, index: int) -> dict:
    header_format = ""
    header_link = ""
    body_text = ""
    buttons: list[dict] = []
    for component in card.get("components") or []:
        if not isinstance(component, dict):
            continue
        kind = str(component.get("type") or "").upper()
        if kind == "HEADER":
            header_format = str(component.get("format") or "IMAGE").upper()
            header_link = _wa_example_https(component)
        elif kind == "BODY":
            body_text = str(component.get("text") or "")
        elif kind == "BUTTONS":
            buttons = _wa_template_button_rows(component.get("buttons") or [])
    body_vars, body_names = _wa_template_placeholders(body_text)
    return {
        "index": index,
        "header_format": header_format,
        "header_link": header_link,
        "body": body_text,
        "body_vars": body_vars,
        "body_names": body_names,
        "buttons": buttons,
    }


def _wa_templates_from_disk() -> tuple[list[dict], float]:
    data = read_json(_WA_TEMPLATES_FILE) or {}
    if not isinstance(data, dict):
        return [], 0.0
    rows = data.get("rows")
    try:
        saved_at = float(data.get("at") or 0)
    except (TypeError, ValueError):
        saved_at = 0.0
    if not isinstance(rows, list):
        return [], 0.0
    return [row for row in rows if isinstance(row, dict)], saved_at


def _wa_templates_remember(rows: list[dict]) -> None:
    if not rows:
        return
    wall = _time_module.time()
    _WA_TEMPLATES_CACHE["rows"] = rows
    _WA_TEMPLATES_CACHE["at"] = _time_module.monotonic()
    _WA_TEMPLATES_CACHE["wall"] = wall
    write_json(_WA_TEMPLATES_FILE, {"at": wall, "rows": rows})


def _wa_templates_cached() -> tuple[list[dict], bool]:
    rows = _WA_TEMPLATES_CACHE.get("rows") or []
    wall = float(_WA_TEMPLATES_CACHE.get("wall") or 0)
    if not rows:
        rows, wall = _wa_templates_from_disk()
        if rows:
            _WA_TEMPLATES_CACHE["rows"] = rows
            _WA_TEMPLATES_CACHE["wall"] = wall
            _WA_TEMPLATES_CACHE["at"] = _time_module.monotonic()
    fresh = bool(rows) and (_time_module.time() - wall) < _WA_TEMPLATES_TTL
    return list(rows), fresh


def _wa_template_rows(force: bool = False) -> list[dict]:
    """Approved WABA templates, shaped for the app picker."""
    cached, fresh = _wa_templates_cached()
    if cached and fresh and not force:
        return cached
    waba = str(WA_WABA_ID or "").strip()
    if not waba:
        return cached
    fields = "name,language,status,category,parameter_format,components"
    payload = _wa_graph_get(f"{waba}/message_templates", {"limit": 200, "fields": fields})
    error = payload.get("error") if isinstance(payload, dict) else None
    error_text = str((error or {}).get("message") or "") if isinstance(error, dict) else ""
    if error and "parameter_format" in error_text:
        payload = _wa_graph_get(
            f"{waba}/message_templates",
            {"limit": 200, "fields": "name,language,status,category,components"},
        )
    rows: list[dict] = []
    for item in (payload or {}).get("data") or []:
        if not isinstance(item, dict) or str(item.get("status") or "").upper() != "APPROVED":
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        header_text = ""
        header_format = ""
        body_text = ""
        buttons: list[dict] = []
        cards: list[dict] = []
        parts: list[str] = []
        for component in item.get("components") or []:
            if not isinstance(component, dict):
                continue
            kind = str(component.get("type") or "").upper()
            part_format = str(component.get("format") or "")
            part_buttons = [
                str(button.get("type") or "")
                for button in (component.get("buttons") or [])
                if isinstance(button, dict)
            ]
            parts.append(kind + (":" + part_format if part_format else "") + (("[" + ",".join(part_buttons) + "]") if part_buttons else ""))
            if kind == "HEADER":
                header_format = str(component.get("format") or "TEXT").upper()
                if header_format == "TEXT":
                    header_text = str(component.get("text") or "")
            elif kind == "BODY":
                body_text = str(component.get("text") or "")
            elif kind == "BUTTONS":
                buttons = _wa_template_button_rows(component.get("buttons") or [])
            elif kind == "CAROUSEL":
                for index, card in enumerate(component.get("cards") or []):
                    if isinstance(card, dict):
                        cards.append(_wa_template_card(card, index))
                        parts[-1] = f"CAROUSEL:{len(cards)}:{cards[-1].get('header_format') or '-'}"
        header_vars, header_names = _wa_template_placeholders(header_text)
        body_vars, body_names = _wa_template_placeholders(body_text)
        parameter_format = str(item.get("parameter_format") or "").upper()
        if parameter_format not in {"NAMED", "POSITIONAL"}:
            parameter_format = "NAMED" if (header_names or body_names or any(btn.get("names") for btn in buttons)) else "POSITIONAL"
        rows.append({
            "name": name,
            "language": str(item.get("language") or ""),
            "category": str(item.get("category") or ""),
            "parameter_format": parameter_format,
            "header_format": header_format,
            "header": header_text,
            "header_vars": header_vars,
            "header_names": header_names,
            "body": body_text,
            "body_vars": body_vars,
            "body_names": body_names,
            "buttons": buttons,
            "cards": cards,
            "parts": parts,
        })
    rows.sort(key=lambda row: (row.get("name") or "", row.get("language") or ""))
    if rows:
        _wa_templates_remember(rows)
        return list(rows)
    return cached


def _wa_template_find(name: str, language: str) -> dict | None:
    wanted = str(name or "").strip()
    lang = str(language or "").strip()
    for row in _wa_template_rows(False):
        if row.get("name") != wanted:
            continue
        if lang and row.get("language") and row.get("language") != lang:
            continue
        return row
    return None


def _wa_text_parameters(values: list[str], names: list[str], limit: int) -> list[dict]:
    params: list[dict] = []
    if names:
        for index, param_name in enumerate(names):
            text = values[index] if index < len(values) else ""
            params.append({"type": "text", "parameter_name": param_name, "text": text[:limit]})
        return params
    for value in values:
        params.append({"type": "text", "text": str(value)[:limit]})
    return params


def _wa_template_components(spec: dict, values: dict) -> tuple[list[dict], str]:
    """Build Meta components that match the approved template."""
    components: list[dict] = []
    header_format = str(spec.get("header_format") or "").upper()
    if header_format == "LOCATION":
        return [], "Bu şablon ünvan başlığı tələb edir."
    media_key = _WA_HEADER_MEDIA.get(header_format)
    if media_key:
        link = str(values.get("header_media") or "").strip()
        if not link.startswith("https://"):
            label = {"image": "Şəkil", "video": "Video", "document": "Sənəd"}.get(media_key, "Fayl")
            return [], f"{label} linkini https:// ilə yazın."
        media = {"link": link[:1000]}
        if media_key == "document":
            filename = link.rsplit("/", 1)[-1].split("?", 1)[0][:80] or "file"
            media["filename"] = filename
        components.append({"type": "header", "parameters": [{"type": media_key, media_key: media}]})
    elif header_format == "TEXT":
        names = list(spec.get("header_names") or [])
        count = len(names) or int(spec.get("header_vars") or 0)
        header_values = [str(value).strip() for value in (values.get("header_params") or [])][:count]
        if count and (len(header_values) < count or any(not value for value in header_values)):
            return [], "Şablon dəyişənlərini doldurun."
        if count:
            components.append({"type": "header", "parameters": _wa_text_parameters(header_values, names, 200)})
    names = list(spec.get("body_names") or [])
    count = len(names) or int(spec.get("body_vars") or 0)
    body_values = [str(value).strip() for value in (values.get("body_params") or [])][:count]
    if count and (len(body_values) < count or any(not value for value in body_values)):
        return [], "Şablon dəyişənlərini doldurun."
    if count:
        components.append({"type": "body", "parameters": _wa_text_parameters(body_values, names, 900)})
    supplied = values.get("buttons") if isinstance(values.get("buttons"), dict) else {}
    for button in spec.get("buttons") or []:
        if not isinstance(button, dict) or not button.get("needs_value"):
            continue
        try:
            index = int(button.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        text = str(supplied.get(index) or "").strip()
        if not text:
            return [], "Düymə dəyərini yazın."
        button_type = str(button.get("type") or "").upper()
        if button_type == "COPY_CODE":
            if len(text) > 15:
                return [], "Kupon kodu 15 simvoldan uzun ola bilməz."
            parameter = {"type": "coupon_code", "coupon_code": text}
            sub_type = "copy_code"
        else:
            parameter = {"type": "text", "text": text[:200]}
            button_names = list(button.get("names") or [])
            if button_names:
                parameter["parameter_name"] = button_names[0]
            sub_type = "url"
        components.append({
            "type": "button",
            "sub_type": sub_type,
            "index": str(index),
            "parameters": [parameter],
        })
    carousel, carousel_error = _wa_carousel_component(spec.get("cards") or [], values.get("cards") or [])
    if carousel_error:
        return [], carousel_error
    if carousel:
        components.append(carousel)
    return components, ""


def _wa_card_button_components(buttons: list, supplied: dict) -> tuple[list[dict], str]:
    components: list[dict] = []
    for button in buttons or []:
        if not isinstance(button, dict):
            continue
        try:
            index = int(button.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        button_type = str(button.get("type") or "").upper()
        if button_type == "QUICK_REPLY":
            payload = str(button.get("text") or "ok").strip()[:128] or "ok"
            components.append({
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(index),
                "parameters": [{"type": "payload", "payload": payload}],
            })
            continue
        if not button.get("needs_value"):
            continue
        text = str(supplied.get(index) or "").strip()
        if not text:
            return [], "Düymə dəyərini yazın."
        if button_type == "COPY_CODE":
            if len(text) > 15:
                return [], "Kupon kodu 15 simvoldan uzun ola bilməz."
            parameter = {"type": "coupon_code", "coupon_code": text}
            sub_type = "copy_code"
        else:
            parameter = {"type": "text", "text": text[:200]}
            button_names = list(button.get("names") or [])
            if button_names:
                parameter["parameter_name"] = button_names[0]
            sub_type = "url"
        components.append({
            "type": "button",
            "sub_type": sub_type,
            "index": str(index),
            "parameters": [parameter],
        })
    return components, ""


def _wa_carousel_component(cards: list, supplied_cards: list) -> tuple[dict | None, str]:
    if not cards:
        return None, ""
    built: list[dict] = []
    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        supplied = supplied_cards[index] if index < len(supplied_cards) and isinstance(supplied_cards[index], dict) else {}
        card_components: list[dict] = []
        header_format = str(card.get("header_format") or "IMAGE").upper()
        media_key = _WA_HEADER_MEDIA.get(header_format)
        if media_key:
            link = str(supplied.get("header_media") or card.get("header_link") or card.get("header_media") or "").strip()
            if not link.startswith("https://"):
                return None, f"Kart {index + 1}: linki https:// ilə yazın."
            media_id, media_error = _wa_media_id_for_link(link, media_key)
            if not media_id:
                return None, f"Kart {index + 1}: {media_error}"
            card_components.append({
                "type": "header",
                "parameters": [{"type": media_key, media_key: {"id": media_id}}],
            })
        names = list(card.get("body_names") or [])
        count = len(names) or int(card.get("body_vars") or 0)
        body_values = [str(value).strip() for value in (supplied.get("body_params") or [])][:count]
        if count and (len(body_values) < count or any(not value for value in body_values)):
            return None, f"Kart {index + 1}: dəyişənləri doldurun."
        if count:
            card_components.append({"type": "body", "parameters": _wa_text_parameters(body_values, names, 900)})
        button_components, button_error = _wa_card_button_components(card.get("buttons") or [], supplied.get("buttons") or {})
        if button_error:
            return None, f"Kart {index + 1}: {button_error}"
        card_components.extend(button_components)
        built.append({"card_index": index, "components": card_components})
    if not built:
        return None, ""
    return {"type": "carousel", "cards": built}, ""


def _wa_cloud_send_template(phone: str, spec: dict, values: dict, phone_id: str = "") -> tuple[bool, str, str]:
    """Send an approved template; the only way to open a chat outside 24 hours."""
    to = _normalize_wa_number(phone)
    if len(to) < 8:
        return False, "WhatsApp nömrəsi tapılmadı.", ""
    template_name = str((spec or {}).get("name") or "").strip()
    if not template_name:
        return False, "Şablon seçilməyib.", ""
    _wa_send_phone.phone_id = str(phone_id or _wa_active_phone_id() or "")
    components, error = _wa_template_components(spec, values or {})
    if error:
        _wa_send_phone.phone_id = ""
        return False, error, ""
    template: dict = {
        "name": template_name,
        "language": {"code": str(spec.get("language") or "az").strip() or "az"},
    }
    if components:
        template["components"] = components
    logger.info(
        "WA template send name=%s lang=%s header=%s parts=%s",
        template_name,
        template["language"]["code"],
        spec.get("header_format") or "-",
        ",".join(str(part.get("type") or "") for part in components) or "-",
    )
    try:
        return _wa_cloud_post({
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": template,
        }, phone_id)
    finally:
        _wa_send_phone.phone_id = ""


def _wa_cloud_send_media(
    phone: str,
    kind: str,
    media_id: str,
    *,
    caption: str = "",
    filename: str = "",
    reply_to: str = "",
    voice: bool = False,
    phone_id: str = "",
) -> tuple[bool, str, str]:
    to = _normalize_wa_number(phone)
    if len(to) < 8:
        return False, "WhatsApp nömrəsi tapılmadı.", ""
    media: dict = {"id": media_id}
    if kind == "audio" and voice:
        media["voice"] = True
    if kind in {"image", "video", "document"} and caption:
        media["caption"] = caption
    if kind == "document" and filename:
        media["filename"] = filename
    body: dict = {"recipient_type": "individual", "to": to, "type": kind, kind: media}
    quoted = str(reply_to or "").strip()
    if quoted:
        body["context"] = {"message_id": quoted}
    return _wa_cloud_post(body, phone_id)


def _wa_cloud_send_reaction(phone: str, wamid: str, emoji: str, phone_id: str = "") -> tuple[bool, str]:
    to = _normalize_wa_number(phone)
    if len(to) < 8:
        return False, "WhatsApp nömrəsi tapılmadı."
    ok, error, _wamid = _wa_cloud_post({
        "recipient_type": "individual",
        "to": to,
        "type": "reaction",
        # An empty emoji removes the reaction.
        "reaction": {"message_id": str(wamid or ""), "emoji": str(emoji or "")},
    }, phone_id)
    return ok, error


def _wa_cloud_mark_read(wamid: str, typing: bool = False, phone_id: str = "") -> bool:
    body: dict = {"status": "read", "message_id": str(wamid or "")}
    if typing:
        body["typing_indicator"] = {"type": "text"}
    ok, _error, _wamid = _wa_cloud_post(body, phone_id)
    return ok


_WA_MEDIA_BYTES: dict[str, tuple[bytes, str]] = {}


def _wa_download_graph_media(media_id: str) -> tuple[bytes, str]:
    """Download one WABA attachment. The Graph URL expires, so the bytes are cached."""
    media_id = str(media_id or "").strip()
    cached = _WA_MEDIA_BYTES.get(media_id)
    if cached:
        return cached
    token, _phone_id = _wa_cloud_credentials()
    if not token or not media_id.isdigit():
        return b"", ""
    try:
        meta = requests.get(
            f"https://graph.facebook.com/{WA_CLOUD_API_VERSION}/{media_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except Exception as exc:
        logger.warning("WhatsApp media meta failed: %s", exc)
        return b"", ""
    if meta.status_code != 200:
        logger.warning("WhatsApp media meta status %s", meta.status_code)
        return b"", ""
    try:
        payload = meta.json() or {}
    except Exception:
        payload = {}
    url = str(payload.get("url") or "")
    mime = str(payload.get("mime_type") or "application/octet-stream").split(";")[0].strip()
    if not url:
        return b"", ""
    try:
        blob = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=40)
    except Exception as exc:
        logger.warning("WhatsApp media download failed: %s", exc)
        return b"", ""
    raw = blob.content or b""
    if blob.status_code != 200 or not raw:
        return b"", ""
    if len(raw) <= 12 * 1024 * 1024:
        if len(_WA_MEDIA_BYTES) >= 40:
            _WA_MEDIA_BYTES.pop(next(iter(_WA_MEDIA_BYTES)), None)
        _WA_MEDIA_BYTES[media_id] = (raw, mime)
    return raw, mime


async def handle_api_wa_media(request: web.Request) -> web.Response:
    media_id = str(request.match_info.get("media_id") or "").strip()
    if not media_id.isdigit():
        return web.Response(status=400, text="Invalid media")
    raw_chat_id = request.headers.get("X-TG-User-ID") or request.rel_url.query.get("uid") or ""
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return web.Response(status=401, text="Unauthorized")
    if not is_funnel_chat(chat_id) and not is_admin(chat_id):
        return web.Response(status=403, text="Forbidden")
    raw, mime = await asyncio.to_thread(_wa_download_graph_media, media_id)
    if not raw:
        return web.Response(status=404, text="Media not found")
    return web.Response(body=raw, content_type=mime or "application/octet-stream")


def _send_whatsapp_cloud_text(phone: str, text: str, reply_to: str = "") -> tuple[bool, str]:
    token, phone_id = _wa_cloud_credentials()
    if not token or not phone_id:
        return False, ""
    ok, error, _wamid = _wa_cloud_send_text(phone, text, reply_to)
    return ok, error


_WA_SENT_FILE = "wa_sent_messages.json"
_wa_sent_messages: dict | None = None
_WA_SENT_PER_LEAD = 300
_wa_sent_lock = threading.Lock()
_wa_sent_save_timer: threading.Timer | None = None


def _load_sent_messages() -> dict:
    """Load the Cloud API send log lazily so GitHub I/O cannot block startup."""
    global _wa_sent_messages
    if _wa_sent_messages is None:
        try:
            data = read_json(_WA_SENT_FILE) or {}
        except Exception as exc:
            logger.warning("Sent message store load failed: %s", exc)
            data = {}
        with _wa_sent_lock:
            if _wa_sent_messages is None:
                _wa_sent_messages = data if isinstance(data, dict) else {}
    return _wa_sent_messages


def _flush_sent_messages() -> None:
    global _wa_sent_save_timer
    with _wa_sent_lock:
        _wa_sent_save_timer = None
        snapshot = json.loads(json.dumps(_load_sent_messages(), ensure_ascii=False))
    try:
        write_json(_WA_SENT_FILE, snapshot)
    except Exception as exc:
        logger.warning("Sent message store failed: %s", exc)


def _schedule_sent_messages_save() -> None:
    """Persist in the background; every write_json is a commit on the data branch."""
    global _wa_sent_save_timer
    with _wa_sent_lock:
        if _wa_sent_save_timer is not None:
            return
        timer = threading.Timer(5.0, _flush_sent_messages)
        timer.daemon = True
        _wa_sent_save_timer = timer
    timer.start()


def _remember_sent_message(lead_id: int, item: dict) -> None:
    """Keep messages we sent through Cloud API; Kommo does not mirror them back."""
    key = str(int(lead_id or 0))
    if key == "0" or not isinstance(item, dict):
        return
    store = _load_sent_messages()
    with _wa_sent_lock:
        rows = store.get(key)
        if not isinstance(rows, list):
            rows = []
        rows.append(item)
        store[key] = rows[-_WA_SENT_PER_LEAD:]
    _schedule_sent_messages_save()
    try:
        _store_chat_tail(int(lead_id), [item])
        _invalidate_deal_chat_cache(int(lead_id))
    except Exception:
        pass


def _sent_messages_for_lead(lead_id: int) -> list[dict]:
    try:
        store = _load_sent_messages()
        with _wa_sent_lock:
            rows = store.get(str(int(lead_id or 0)))
            if not isinstance(rows, list):
                return []
            return [dict(row) for row in rows if isinstance(row, dict)]
    except Exception as exc:
        logger.warning("Sent message read failed: %s", exc)
        return []


_WA_REACTIONS_KEY = "reactions"


def _wamid_for_react(lead_id: int, *ids: str) -> str:
    wanted = [str(item or "").strip() for item in ids if str(item or "").strip()]
    for raw in wanted:
        if raw.lower().startswith("wamid"):
            return raw
    for row in _sent_messages_for_lead(lead_id):
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id") or "").strip()
        ext = str(row.get("external_id") or "").strip()
        msgid = str(row.get("msgid") or "").strip()
        if rid in wanted or ext in wanted or msgid in wanted:
            if ext.lower().startswith("wamid"):
                return ext
            if msgid.lower().startswith("wamid"):
                return msgid
    return ""


def _remember_reaction(lead_id: int, wamid: str, emoji: str) -> None:
    """Reactions we send are not echoed back, so keep them for the chat view."""
    key = str(int(lead_id or 0))
    target = str(wamid or "")
    if key == "0" or not target:
        return
    store = _load_sent_messages()
    with _wa_sent_lock:
        bucket = store.get(_WA_REACTIONS_KEY)
        if not isinstance(bucket, dict):
            bucket = {}
            store[_WA_REACTIONS_KEY] = bucket
        rows = bucket.get(key)
        if not isinstance(rows, dict):
            rows = {}
            bucket[key] = rows
        if emoji:
            rows[target] = str(emoji)
        else:
            rows.pop(target, None)
    _schedule_sent_messages_save()


def _reactions_for_lead(lead_id: int) -> dict:
    try:
        store = _load_sent_messages()
        with _wa_sent_lock:
            bucket = store.get(_WA_REACTIONS_KEY)
            if not isinstance(bucket, dict):
                return {}
            rows = bucket.get(str(int(lead_id or 0)))
            return dict(rows) if isinstance(rows, dict) else {}
    except Exception as exc:
        logger.warning("Reaction store read failed: %s", exc)
        return {}


def _update_sent_message(lead_id: int, wamid: str, **fields) -> None:
    wanted = str(wamid or "")
    changed = False
    store = _load_sent_messages()
    with _wa_sent_lock:
        rows = store.get(str(int(lead_id or 0)))
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and str(row.get("external_id") or "") == wanted:
                    row.update(fields)
                    changed = True
                    break
    if changed:
        _schedule_sent_messages_save()


def _wa_preview_cards(raw) -> list[dict]:
    """Keep a small, safe copy of carousel cards so the chat can draw them."""
    cards: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        image = str(item.get("image") or item.get("header_media") or "").strip()
        if not image.startswith("https://"):
            image = ""
        body = str(item.get("body") or "").strip()[:900]
        buttons: list[dict] = []
        for button in item.get("buttons") or []:
            if not isinstance(button, dict):
                continue
            label = str(button.get("text") or "").strip()[:80]
            if label:
                buttons.append({"text": label})
            if len(buttons) >= 3:
                break
        if not image and not body and not buttons:
            continue
        cards.append({"image": image[:1000], "body": body, "buttons": buttons})
        if len(cards) >= 10:
            break
    return cards


def _sent_message_item(
    *,
    wamid: str,
    text: str,
    author: str,
    channel: str = "whatsapp",
    message_type: str = "text",
    file_uuid: str = "",
    file_name: str = "",
    reply_id: str = "",
    reply_text: str = "",
    reply_author: str = "",
    incoming: bool = False,
    created_at: int = 0,
    phone: str = "",
    cards: list | None = None,
    media_url: str = "",
) -> dict:
    created = int(created_at or _time_module.time())
    gallery = _wa_preview_cards(cards)
    item = {
        "id": f"wa-{wamid or uuid.uuid4().hex}",
        "external_id": str(wamid or ""),
        "direction": "incoming" if incoming else "outgoing",
        "incoming": bool(incoming),
        "author": author or "",
        "phone": _wa_phone_key(phone) if phone else "",
        "text": text or "",
        "message_type": message_type,
        "created_at": created,
        "created": _deal_fmt_ts(created),
        "origin": channel,
        "channel": channel,
        "reply_to_message_id": str(reply_id or ""),
        "reply_to_text": str(reply_text or "")[:200],
        "reply_to_author": str(reply_author or "")[:80],
        "media_url": str(media_url or ""),
        "file_uuid": str(file_uuid or ""),
        "file_name": str(file_name or ""),
        "delivery_status": "" if incoming else "sent",
        "via_cloud": True,
    }
    if gallery:
        item["cards"] = gallery
        item["message_type"] = "carousel"
    return item


_WA_PHONE_LEADS_KEY = "_phone_leads"


def _wa_phone_key(phone: str) -> str:
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits.startswith("994") and len(digits) >= 12:
        return digits[:12]
    if digits.startswith("0") and len(digits) == 10:
        return "994" + digits[1:]
    if len(digits) == 9:
        return "994" + digits
    return digits


def _remember_phone_lead(phone: str, lead_id: int) -> None:
    key = _wa_phone_key(phone)
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return
    if not key or not lid:
        return
    store = _load_sent_messages()
    with _wa_sent_lock:
        idx = store.get(_WA_PHONE_LEADS_KEY)
        if not isinstance(idx, dict):
            idx = {}
            store[_WA_PHONE_LEADS_KEY] = idx
        idx[key] = lid
    _schedule_sent_messages_save()


def _lead_id_for_stored_phone(phone: str) -> int:
    key = _wa_phone_key(phone)
    if not key:
        return 0
    store = _load_sent_messages()
    with _wa_sent_lock:
        idx = store.get(_WA_PHONE_LEADS_KEY)
        if not isinstance(idx, dict):
            return 0
        try:
            return int(idx.get(key) or 0)
        except (TypeError, ValueError):
            return 0


def _cloud_last_for_lead(lead_id: int) -> tuple[str, int, bool]:
    rows = _sent_messages_for_lead(lead_id)
    if not rows:
        return "", 0, False
    incoming = [row for row in rows if row.get("incoming")]
    newest = max(rows, key=lambda row: int(row.get("created_at") or 0))
    preview_src = max(incoming, key=lambda row: int(row.get("created_at") or 0)) if incoming else newest
    return str(preview_src.get("text") or "").strip(), int(newest.get("created_at") or 0), True


def _index_deal_phones(deals: list) -> None:
    """Map contact phones to existing deals so Cloud incoming lands in that chat."""
    ranked: list[tuple[int, int, list[str]]] = []
    for deal in deals or []:
        if not isinstance(deal, dict):
            continue
        try:
            lid = int(deal.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if not lid:
            continue
        phones = []
        if isinstance(deal.get("phones"), list):
            phones.extend(str(phone) for phone in deal.get("phones") if phone)
        if deal.get("phone"):
            phones.append(str(deal.get("phone")))
        if not phones:
            continue
        try:
            updated = int(deal.get("updated_at") or deal.get("created_at") or 0)
        except (TypeError, ValueError):
            updated = 0
        ranked.append((updated, lid, phones))
    ranked.sort()
    for _updated, lid, phones in ranked:
        for phone in phones:
            _remember_phone_lead(phone, lid)


def _lead_ids_from_contact(contact: dict) -> list[int]:
    rows = []
    for linked in (contact.get("_embedded") or {}).get("leads") or []:
        if not isinstance(linked, dict):
            continue
        try:
            lid = int(linked.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if lid:
            rows.append(lid)
    return rows


def _lead_in_rufat_chats(lead: dict | None) -> bool:
    """Any Rüfət-funnel deal, including uğurlu/imtina — incoming must still land here."""
    if not isinstance(lead, dict) or not lead:
        return False
    return _lead_pipeline_id(lead) == int(RUFAT_PIPELINE_ID)


def _lead_open_in_rufat_chats(lead: dict | None) -> bool:
    """Cloud inbound prefers an open personal-funnel deal."""
    if not _lead_in_rufat_chats(lead):
        return False
    try:
        status_id = int((lead or {}).get("status_id") or 0)
    except (TypeError, ValueError):
        status_id = 0
    return status_id not in {142, 143}


def _pick_existing_lead(lead_ids: list[int]) -> int:
    """Attach a WhatsApp thread to the newest open deal, in any pipeline."""
    best_open = 0
    best_open_at = -1
    fallback = 0
    fallback_at = -1
    for lid in lead_ids:
        details = get_lead_details(lid) or {}
        if not details:
            continue
        try:
            updated = int(details.get("updated_at") or details.get("created_at") or 0)
        except (TypeError, ValueError):
            updated = 0
        try:
            status_id = int(details.get("status_id") or 0)
        except (TypeError, ValueError):
            status_id = 0
        if status_id not in {142, 143} and updated >= best_open_at:
            best_open = int(lid)
            best_open_at = updated
        if updated >= fallback_at:
            fallback = int(lid)
            fallback_at = updated
    return best_open or fallback


def _search_lead_ids_by_phone(phone: str) -> list[int]:
    key = _wa_phone_key(phone)
    variants = {phone, key, "+" + key if key else ""}
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) >= 9:
        variants.add(digits[-9:])
    found: list[int] = []
    seen: set[int] = set()
    for variant in variants:
        if not variant:
            continue
        try:
            resp = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/leads",
                headers=HEADERS,
                params={"query": variant, "limit": 10},
                timeout=8,
            )
        except Exception as exc:
            logger.warning("Lead phone search failed: %s", exc)
            continue
        if resp.status_code != 200:
            continue
        for lead in (resp.json().get("_embedded") or {}).get("leads") or []:
            if not isinstance(lead, dict):
                continue
            try:
                lid = int(lead.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if lid and lid not in seen:
                seen.add(lid)
                found.append(lid)
    return found


def _move_cloud_lead_messages(src_id: int, dst_id: int) -> None:
    src, dst = str(int(src_id or 0)), str(int(dst_id or 0))
    if src == "0" or dst == "0" or src == dst:
        return
    store = _load_sent_messages()
    with _wa_sent_lock:
        src_rows = store.get(src)
        if not isinstance(src_rows, list) or not src_rows:
            return
        dst_rows = store.get(dst)
        if not isinstance(dst_rows, list):
            dst_rows = []
        seen = {
            str(row.get("external_id") or "")
            for row in dst_rows
            if isinstance(row, dict) and row.get("external_id")
        }
        for row in src_rows:
            if not isinstance(row, dict):
                continue
            external = str(row.get("external_id") or "")
            if external and external in seen:
                continue
            dst_rows.append(row)
            if external:
                seen.add(external)
        store[dst] = dst_rows[-_WA_SENT_PER_LEAD:]
        store[src] = []
        bucket = store.get(_WA_REACTIONS_KEY)
        if isinstance(bucket, dict):
            extra = bucket.pop(src, None)
            if isinstance(extra, dict):
                current = bucket.get(dst)
                if not isinstance(current, dict):
                    current = {}
                current.update(extra)
                bucket[dst] = current
        idx = store.get(_WA_PHONE_LEADS_KEY)
        if isinstance(idx, dict):
            for phone, mapped in list(idx.items()):
                try:
                    if int(mapped) == int(src_id):
                        idx[phone] = int(dst_id)
                except (TypeError, ValueError):
                    continue
    _schedule_sent_messages_save()


def _phones_for_cloud_lead(lead_id: int, lead: dict | None = None) -> list[str]:
    phones: list[str] = []
    store = _load_sent_messages()
    with _wa_sent_lock:
        idx = store.get(_WA_PHONE_LEADS_KEY)
        if isinstance(idx, dict):
            for phone, mapped in idx.items():
                try:
                    if int(mapped) == int(lead_id) and phone:
                        phones.append(str(phone))
                except (TypeError, ValueError):
                    continue
        for row in store.get(str(int(lead_id or 0))) or []:
            if not isinstance(row, dict):
                continue
            phone = str(row.get("phone") or "").strip()
            if phone:
                phones.append(phone)
    if lead:
        try:
            _ids, lead_phones = _contact_ids_and_phones(lead)
        except Exception:
            lead_phones = []
        phones.extend(str(phone) for phone in lead_phones if phone)
    seen: set[str] = set()
    ordered: list[str] = []
    for phone in phones:
        key = _wa_phone_key(phone)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


_last_cloud_rehome_at = 0.0


def _rehome_orphaned_cloud_inbox() -> None:
    """Move Cloud threads off closed/foreign deals onto Rüfət's open funnel."""
    global _last_cloud_rehome_at
    now = _time_module.monotonic()
    if now - _last_cloud_rehome_at < 15:
        return
    _last_cloud_rehome_at = now
    store = _load_sent_messages()
    with _wa_sent_lock:
        keys = [key for key in store.keys() if str(key).isdigit()]
    for key in keys:
        try:
            src_id = int(key)
        except (TypeError, ValueError):
            continue
        rows = _sent_messages_for_lead(src_id)
        if not any(row.get("incoming") for row in rows):
            continue
        lead = get_lead_details(src_id)
        if _lead_in_rufat_chats(lead):
            continue
        target = 0
        name = str((lead or {}).get("name") or "").strip()
        for phone in _phones_for_cloud_lead(src_id, lead):
            target = _resolve_cloud_lead(phone, name, use_stored=False)
            if target and target != src_id:
                break
        if target and target != src_id:
            _move_cloud_lead_messages(src_id, target)
            if int(_WA_LAST_HOOK.get("lead") or 0) == src_id:
                _WA_LAST_HOOK["lead"] = int(target)
            logger.info("WhatsApp inbox rehomed lead=%s -> %s", src_id, target)


def _overview_deal_from_any_lead(lead: dict, preview: str, ts: int, channel: str = "", incoming_at: int = 0) -> dict:
    pipe = _lead_pipeline_id(lead)
    owner = None
    for cid in (RUFAT_CHAT_ID, ADMIN_CHAT_ID, HUSEYN_CHAT_ID, RASIM_CHAT_ID):
        candidate = get_funnel_owner(cid)
        if candidate and int(candidate.get("pipeline_id") or 0) == int(pipe or 0):
            owner = candidate
            break
    stages = (owner or {}).get("stages") or {}
    names = (owner or {}).get("stage_names") or {}
    status_to_key = {}
    for key, status_id in stages.items():
        try:
            status_to_key[int(status_id)] = key
        except (TypeError, ValueError):
            continue
    try:
        status_id = int(lead.get("status_id") or 0)
    except (TypeError, ValueError):
        status_id = 0
    try:
        lid = int(lead.get("id") or 0)
    except (TypeError, ValueError):
        lid = 0
    _ids, phones = _contact_ids_and_phones(lead)
    contact = get_contact_details(_ids[0]) if _ids else {}
    if not isinstance(contact, dict):
        contact = {}
    contact_name = str(contact.get("name") or lead.get("name") or "").strip() or (phones[0] if phones else "Müştəri")
    try:
        created = int(lead.get("created_at") or 0)
    except (TypeError, ValueError):
        created = 0
    try:
        updated = int(lead.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated = 0
    unread = incoming_at > 0
    return {
        "id": lid,
        "pipeline_id": pipe,
        "stage_key": status_to_key.get(status_id, ""),
        "stage_name": names.get(status_id, ""),
        "contact_name": contact_name,
        "phone": phones[0] if phones else "",
        "phones": phones,
        "contacts": [{"id": _ids[0], "name": contact_name, "phones": phones}] if _ids else [],
        "source": "",
        "menbe": "",
        "created_at": created,
        "updated_at": max(updated, int(ts or 0)),
        "chat_at": int(ts or 0),
        "last_note": "",
        "last_client_message": str(preview or ("Yeni mesaj" if unread else "Çat"))[:140],
        "last_incoming_at": int(incoming_at or 0),
        "last_outgoing_at": _cloud_last_outgoing_at(lid),
        "chat_channel": channel or "whatsapp",
        "contact_avatar": _first_avatar_url(lead, contact),
        "task_desc": "",
        "deadline": "",
        "deadline_ts": 0,
        "voice_url": f"/api/voice/{lid}" if lid and str(lid) in _voice_urls else "",
        "kommo_link": f"{KOMMO_BASE_URL}/leads/detail/{lid}",
        "tasks": [],
        "inbox_only": True,
    }


def _sovdelesmeler_chat_lead_ids() -> set[int]:
    """Every lead in Sövdələşmələr, including NÖMRƏ ALINIB."""
    allowed: set[int] = set()
    page = 1
    while page <= 20:
        try:
            resp = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/leads",
                headers=HEADERS,
                params={
                    "filter[pipeline_id]": int(SOVDELESMELER_PIPELINE_ID),
                    "limit": 250,
                    "page": page,
                },
                timeout=12,
            )
        except Exception as exc:
            logger.warning("Sövdələşmələr chat leads page %s failed: %s", page, exc)
            break
        if resp.status_code == 204:
            break
        if resp.status_code != 200:
            logger.warning("Sövdələşmələr chat leads page %s status %s", page, resp.status_code)
            break
        batch = (resp.json().get("_embedded") or {}).get("leads") or []
        if not batch:
            break
        for lead in batch:
            if not isinstance(lead, dict):
                continue
            try:
                lid = int(lead.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if lid:
                allowed.add(lid)
        if len(batch) < 250:
            break
        page += 1
    return allowed


def _inject_outside_funnel_talk_deals(
    deals: list,
    outside_by_lead: dict[int, dict],
    incoming_at_by_lead: dict[int, int],
    client_by_lead: dict[int, str],
    channel_by_lead: dict[int, str],
    avatar_by_lead: dict[int, str],
    talk_updated: dict[int, int],
) -> None:
    """Nizami also sees every Sövdələşmələr talk and every gray WhatsApp talk."""
    if not isinstance(deals, list) or not outside_by_lead:
        return
    seen = set()
    for deal in deals:
        try:
            seen.add(int(deal.get("id") or 0))
        except (TypeError, ValueError):
            continue
    ranked = []
    for lid, talk in outside_by_lead.items():
        try:
            lid_int = int(lid)
        except (TypeError, ValueError):
            continue
        if not lid_int or lid_int in seen:
            continue
        try:
            updated = int(talk.get("updated_at") or talk.get("created_at") or 0)
        except (TypeError, ValueError):
            updated = 0
        ranked.append((updated, lid_int, talk))
    ranked.sort(reverse=True)
    allowed = _sovdelesmeler_chat_lead_ids()
    added = 0
    for updated, lid, talk in ranked:
        if added >= 20:
            break
        channel = _talk_channel_key(talk)
        if lid in seen or (lid not in allowed and channel != "whatsapp"):
            continue
        lead = get_lead_details(lid)
        if not lead:
            continue
        if channel not in CHAT_CHANNEL_LABELS:
            continue
        unread_n = _talk_unread_size(talk)
        unread = unread_n > 0
        if unread and not _talk_last_looks_outgoing(talk) and not _cloud_outgoing_covers_talk(lid, updated):
            incoming_at = _stable_talk_incoming_at(lid, unread_n, updated)
        else:
            incoming_at = int(incoming_at_by_lead.get(lid) or 0)
        preview = client_by_lead.get(lid) or ("Yeni mesaj" if unread else "Çat")
        row = _overview_deal_from_any_lead(lead, preview, updated, channel, incoming_at)
        if avatar_by_lead.get(lid):
            row["contact_avatar"] = avatar_by_lead[lid]
        deals.append(row)
        seen.add(lid)
        added += 1


def _overview_deal_from_cloud_lead(lead: dict, preview: str, ts: int) -> dict:
    owner = get_funnel_owner(RUFAT_CHAT_ID) or {}
    stages = owner.get("stages") or {}
    names = owner.get("stage_names") or {}
    status_to_key = {}
    for key, status_id in stages.items():
        try:
            status_to_key[int(status_id)] = key
        except (TypeError, ValueError):
            continue
    try:
        status_id = int(lead.get("status_id") or 0)
    except (TypeError, ValueError):
        status_id = 0
    try:
        lid = int(lead.get("id") or 0)
    except (TypeError, ValueError):
        lid = 0
    _ids, phones = _contact_ids_and_phones(lead)
    contact_name = ""
    if _ids:
        full = get_contact_details(_ids[0]) or {}
        contact_name = str(full.get("name") or "").strip()
    contact_name = contact_name or str(lead.get("name") or "").strip() or (phones[0] if phones else "Müştəri")
    try:
        created = int(lead.get("created_at") or 0)
    except (TypeError, ValueError):
        created = 0
    try:
        updated = int(lead.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated = 0
    return {
        "id": lid,
        "pipeline_id": int(RUFAT_PIPELINE_ID),
        "stage_key": status_to_key.get(status_id, ""),
        "stage_name": names.get(status_id, ""),
        "contact_name": contact_name,
        "phone": phones[0] if phones else "",
        "phones": phones,
        "contacts": [{"id": _ids[0], "name": contact_name, "phones": phones}] if _ids else [],
        "source": "",
        "menbe": "",
        "created_at": created,
        "updated_at": max(updated, int(ts or 0)),
        "chat_at": int(ts or 0),
        "last_note": "",
        "last_client_message": str(preview or "")[:140],
        "last_incoming_at": int(ts or 0),
        "last_outgoing_at": _cloud_last_outgoing_at(lid),
        "chat_channel": "whatsapp",
        "task_desc": "",
        "deadline": "",
        "deadline_ts": 0,
        "voice_url": f"/api/voice/{lid}" if lid and str(lid) in _voice_urls else "",
        "kommo_link": f"{KOMMO_BASE_URL}/leads/detail/{lid}",
        "tasks": [],
    }


def _paint_cloud_inbox_deal(deal: dict) -> None:
    try:
        lid = int(deal.get("id") or 0)
    except (TypeError, ValueError):
        return
    preview, ts, has = _cloud_last_for_lead(lid)
    if not has:
        return
    incoming = [row for row in _sent_messages_for_lead(lid) if row.get("incoming")]
    if incoming:
        last_in = max(incoming, key=lambda row: int(row.get("created_at") or 0))
        text = str(last_in.get("text") or "").strip()
        if text:
            deal["last_client_message"] = text[:140]
        try:
            incoming_at = int(last_in.get("created_at") or 0)
        except (TypeError, ValueError):
            incoming_at = 0
        deal["last_incoming_at"] = incoming_at
    elif preview and not str(deal.get("last_client_message") or "").strip():
        deal["last_client_message"] = preview[:140]
    outgoing_at = _cloud_last_outgoing_at(lid)
    if outgoing_at:
        deal["last_outgoing_at"] = outgoing_at
    if not str(deal.get("chat_channel") or "").strip():
        deal["chat_channel"] = "whatsapp"
    stamps = [int(ts or 0), int(deal.get("last_incoming_at") or 0), int(deal.get("last_outgoing_at") or 0), int(deal.get("chat_at") or 0)]
    deal["chat_at"] = max(stamps)
    try:
        current = int(deal.get("updated_at") or 0)
    except (TypeError, ValueError):
        current = 0
    if ts > current:
        deal["updated_at"] = ts


def _patch_cloud_inbox_into_rufat_cache() -> None:
    """Put Cloud inbound on Rüfət's and Nizami's cached Çatlar without dropping the whole workspace."""
    for pid in (int(RUFAT_PIPELINE_ID), int(NIZAMI_PIPELINE_ID)):
        overview = _personal_overview_cache.get(pid)
        if not isinstance(overview, dict):
            continue
        deals = overview.get("deals")
        if not isinstance(deals, list):
            continue
        _apply_cloud_inbox_to_deals(deals, pid)
        _personal_overview_cache_at[pid] = _time_module.monotonic()


def _apply_cloud_inbox_to_deals(deals: list, pipeline_id: int = 0) -> None:
    if not isinstance(deals, list):
        return
    try:
        _index_deal_phones(deals)
        seen: set[int] = set()
        for deal in deals:
            if not isinstance(deal, dict):
                continue
            try:
                lid = int(deal.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if lid:
                seen.add(lid)
            _paint_cloud_inbox_deal(deal)
        if int(pipeline_id or 0) not in (int(RUFAT_PIPELINE_ID), int(NIZAMI_PIPELINE_ID)):
            return
        store = _load_sent_messages()
        with _wa_sent_lock:
            cloud_ids = [key for key in store.keys() if str(key).isdigit()]
        for key in cloud_ids:
            try:
                lid = int(key)
            except (TypeError, ValueError):
                continue
            if lid in seen:
                continue
            rows = _sent_messages_for_lead(lid)
            if not any(row.get("incoming") for row in rows):
                continue
            try:
                lead = get_lead_details(lid)
                lead_pipe = _lead_pipeline_id(lead)
                if int(pipeline_id) == int(RUFAT_PIPELINE_ID):
                    rufat_match = _lead_in_rufat_chats(lead) or any(
                        str(row.get("phone_number_id") or "") != str(NIZAMI_WA_PHONE_NUMBER_ID)
                        for row in rows
                    )
                    if not rufat_match and lead_pipe not in (int(RUFAT_PIPELINE_ID), int(SOVDELESMELER_PIPELINE_ID), 0):
                        continue
                elif int(pipeline_id) == int(NIZAMI_PIPELINE_ID):
                    nizami_match = (lead_pipe in (int(NIZAMI_PIPELINE_ID), int(SOVDELESMELER_PIPELINE_ID), 0)) or any(
                        str(row.get("phone_number_id") or "") == str(NIZAMI_WA_PHONE_NUMBER_ID)
                        for row in rows
                    )
                    if not nizami_match:
                        continue
                preview, ts, _has = _cloud_last_for_lead(lid)
                row = _overview_deal_from_cloud_lead(lead, preview, ts)
            except Exception as exc:
                logger.warning("Cloud inbox inject failed lead=%s: %s", lid, exc)
                continue
            if row.get("id"):
                _paint_cloud_inbox_deal(row)
                deals.append(row)
                seen.add(lid)
    except Exception as exc:
        logger.warning("Cloud inbox overlay failed: %s", exc)


def _wa_cloud_media_fields(message: dict) -> dict:
    """Graph media id served by this app, so the chat does not look in Kommo."""
    kind = str((message or {}).get("type") or "").lower()
    block_key = "audio" if kind in {"voice", "ptt"} else kind
    block = message.get(block_key) if isinstance(message.get(block_key), dict) else {}
    media_id = str(block.get("id") or "").strip()
    if not media_id or not media_id.isdigit():
        return {}
    mime = str(block.get("mime_type") or "").lower()
    filename = str(block.get("filename") or "").strip()
    if kind == "image":
        mtype = "picture"
        filename = filename or ("photo.png" if "png" in mime else "photo.jpg")
    elif kind == "sticker":
        mtype = "sticker"
        filename = filename or "sticker.webp"
    elif kind in {"audio", "voice", "ptt"}:
        mtype = "audio"
        filename = filename or "voice.ogg"
    elif kind == "video":
        mtype = "video"
        filename = filename or "video.mp4"
    elif kind == "document":
        mtype = "file"
        filename = filename or "file"
    else:
        return {}
    return {
        "message_type": mtype,
        "file_name": filename,
        "media_url": f"/api/wa/media/{media_id}",
    }


def _wa_incoming_preview(message: dict) -> tuple[str, str]:
    kind = str((message or {}).get("type") or "text").lower()
    if kind == "text":
        return str(((message.get("text") or {}) if isinstance(message.get("text"), dict) else {}).get("body") or "").strip(), "text"
    if kind == "image":
        cap = str(((message.get("image") or {}) if isinstance(message.get("image"), dict) else {}).get("caption") or "").strip()
        return cap or "📷 Şəkil", "picture"
    if kind in {"audio", "voice"}:
        return VOICE_CAPTION_TEXT, "audio"
    if kind == "video":
        cap = str(((message.get("video") or {}) if isinstance(message.get("video"), dict) else {}).get("caption") or "").strip()
        return cap or "🎬 Video", "video"
    if kind == "document":
        name = str(((message.get("document") or {}) if isinstance(message.get("document"), dict) else {}).get("filename") or "").strip()
        return name or "📎 Fayl", "file"
    if kind == "sticker":
        return "Sticker", "sticker"
    if kind == "location":
        return "📍 Məkan", "text"
    if kind in {"contacts", "contact"}:
        return "👤 Kontakt", "text"
    if kind == "button":
        text = str(((message.get("button") or {}) if isinstance(message.get("button"), dict) else {}).get("text") or "").strip()
        return text or "Düymə", "text"
    if kind == "interactive":
        return "Cavab", "text"
    if kind == "reaction":
        return "", "reaction"
    return kind or "Mesaj", kind or "text"


def _lead_id_from_overview_phone(phone: str) -> int:
    """Match a WhatsApp sender to a deal already loaded in memory. No Kommo request."""
    key = _wa_phone_key(phone)
    if not key:
        return 0
    for overview in _personal_overview_cache.values():
        deals = overview.get("deals") if isinstance(overview, dict) else None
        if not isinstance(deals, list):
            continue
        for deal in deals:
            if not isinstance(deal, dict):
                continue
            phones = []
            if deal.get("phone"):
                phones.append(deal.get("phone"))
            if isinstance(deal.get("phones"), list):
                phones.extend(deal.get("phones"))
            if not any(_wa_phone_key(item) == key for item in phones):
                continue
            try:
                lid = int(deal.get("id") or 0)
            except (TypeError, ValueError):
                lid = 0
            if lid:
                return lid
    return 0


def _resolve_cloud_lead(phone: str, contact_name: str, *, use_stored: bool = True, sender_phone_id: str = "") -> int:
    if use_stored:
        stored = _lead_id_for_stored_phone(phone)
        if stored:
            return stored
    cached_lead = _lead_id_from_overview_phone(phone)
    if cached_lead:
        _remember_phone_lead(phone, cached_lead)
        return cached_lead
    lead_ids: list[int] = []
    contacts = search_contact_by_phone(phone)
    name = str(contact_name or "").strip() or phone
    contact_id = 0
    if contacts:
        try:
            contact_id = int(contacts[0].get("id") or 0)
        except (TypeError, ValueError):
            contact_id = 0
    if contact_id:
        full = get_contact_details(contact_id) or {}
        name = str(full.get("name") or name).strip() or name
        lead_ids.extend(_lead_ids_from_contact(full))
    lead_ids.extend(_search_lead_ids_by_phone(phone))
    existing = _pick_existing_lead(list(dict.fromkeys(lead_ids)))
    if existing:
        _remember_phone_lead(phone, existing)
        return existing
    if not contact_id:
        created = create_contact_kommo(name, "+" + _wa_phone_key(phone) if _wa_phone_key(phone) else phone)
        try:
            contact_id = int(((created or {}).get("_embedded") or {}).get("contacts", [{}])[0].get("id") or 0)
        except (TypeError, ValueError):
            contact_id = 0
    if not contact_id:
        return 0
    target_pipe = int(NIZAMI_PIPELINE_ID) if str(sender_phone_id).strip() == str(NIZAMI_WA_PHONE_NUMBER_ID).strip() else int(RUFAT_PIPELINE_ID)
    route = personal_entry_stage(target_pipe)
    pipeline_id, status_id = route if route else (target_pipe, None)
    lead_id = create_lead_for_contact(contact_id, name, pipeline_id, status_id)
    if lead_id:
        _remember_phone_lead(phone, int(lead_id))
        try:
            _patch_cloud_inbox_into_rufat_cache()
        except Exception:
            pass
    return int(lead_id or 0)


def _already_have_wamid(lead_id: int, wamid: str) -> bool:
    wanted = str(wamid or "")
    if not wanted:
        return False
    return any(str(row.get("external_id") or "") == wanted for row in _sent_messages_for_lead(lead_id))


def _update_cloud_status(wamid: str, status: str) -> None:
    wanted = str(wamid or "").strip()
    mapped = {"sent": "sent", "delivered": "delivered", "read": "read", "failed": "failed"}.get(str(status or "").lower(), "")
    if not wanted or not mapped:
        return
    store = _load_sent_messages()
    changed = False
    touched_leads: list[int] = []
    with _wa_sent_lock:
        for key, rows in list(store.items()):
            if key in {_WA_REACTIONS_KEY, _WA_PHONE_LEADS_KEY} or not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_ext = str(row.get("external_id") or "").strip()
                row_id = str(row.get("id") or "").strip()
                match = (
                    row_ext == wanted
                    or row_id == wanted
                    or row_id == f"wa-{wanted}"
                    or row_id == f"sent-{wanted}"
                    or (wanted.startswith("wamid.") and row_ext == wanted[6:])
                    or (row_ext.startswith("wamid.") and row_ext[6:] == wanted)
                )
                if match:
                    if _delivery_rank(mapped) > _delivery_rank(str(row.get("delivery_status") or "")):
                        row["delivery_status"] = mapped
                        changed = True
                    try:
                        touched_leads.append(int(key))
                    except (TypeError, ValueError):
                        pass
                    break
    with _deal_chat_cache_lock:
        for lid, rows in list(_chat_open_preview.items()):
            for item in (rows or []):
                if not isinstance(item, dict) or item.get("incoming"):
                    continue
                i_ext = str(item.get("external_id") or "").strip()
                i_id = str(item.get("id") or "").strip()
                match = (
                    i_ext == wanted
                    or i_id == wanted
                    or i_id == f"wa-{wanted}"
                    or i_id == f"sent-{wanted}"
                    or (wanted.startswith("wamid.") and i_ext == wanted[6:])
                    or (i_ext.startswith("wamid.") and i_ext[6:] == wanted)
                )
                if match:
                    if _delivery_rank(mapped) > _delivery_rank(str(item.get("delivery_status") or "")):
                        item["delivery_status"] = mapped
                    if lid not in touched_leads:
                        touched_leads.append(lid)
    if changed:
        _schedule_sent_messages_save()
    for lid in touched_leads:
        _invalidate_deal_chat_cache(lid)
        _overlay_tail_delivery(lid)
        record_lead_pulse_event(lid, "deal_delivery", preview="", incoming_at=0)


def _ingest_cloud_incoming(value: dict) -> None:
    if not isinstance(value, dict):
        return
    contacts = {
        str(row.get("wa_id") or ""): str(((row.get("profile") or {}) if isinstance(row.get("profile"), dict) else {}).get("name") or "")
        for row in (value.get("contacts") or [])
        if isinstance(row, dict)
    }
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    phone_number_id = str(metadata.get("phone_number_id") or "").strip()
    for status in value.get("statuses") or []:
        if isinstance(status, dict):
            _update_cloud_status(status.get("id"), status.get("status"))
    for message in value.get("messages") or []:
        if not isinstance(message, dict):
            continue
        wamid = str(message.get("id") or "")
        phone = str(message.get("from") or "")
        if not phone:
            continue
        preview, kind = _wa_incoming_preview(message)
        reaction_emoji = ""
        reaction_target_mid = ""
        if kind == "reaction":
            reaction_data = message.get("reaction") if isinstance(message.get("reaction"), dict) else {}
            reaction_emoji = str(reaction_data.get("emoji") or "").strip()
            reaction_target_mid = str(reaction_data.get("message_id") or "").strip()
            preview = f"💬 Reaksiya: {reaction_emoji}" if reaction_emoji else "💬 Müştəri reaksiya bildirdi"
        name = contacts.get(_wa_phone_key(phone)) or contacts.get(phone) or ""
        try:
            created = int(message.get("timestamp") or 0)
        except (TypeError, ValueError):
            created = 0
        context = message.get("context") if isinstance(message.get("context"), dict) else {}
        lead_id = _resolve_cloud_lead(phone, name, sender_phone_id=phone_number_id)
        if not lead_id:
            logger.warning("WhatsApp incoming has no lead phone=%s", phone)
            continue
        if kind == "reaction" and reaction_emoji and reaction_target_mid:
            _remember_reaction(lead_id, reaction_target_mid, reaction_emoji)
        if wamid and _already_have_wamid(lead_id, wamid):
            continue
        media_fields = _wa_cloud_media_fields(message)
        target_pipe = int(NIZAMI_PIPELINE_ID) if str(phone_number_id).strip() == str(NIZAMI_WA_PHONE_NUMBER_ID).strip() else int(RUFAT_PIPELINE_ID)
        item = _sent_message_item(
            wamid=wamid,
            text=preview,
            author=name,
            message_type=str(media_fields.get("message_type") or kind),
            incoming=True,
            created_at=created,
            reply_id=reaction_target_mid or str(context.get("id") or ""),
            phone=phone,
            file_name=str(media_fields.get("file_name") or ""),
            media_url=str(media_fields.get("media_url") or ""),
        )
        item["phone_number_id"] = phone_number_id
        item["target_pipeline"] = target_pipe
        _remember_sent_message(lead_id, item)
        _append_chat_tail(lead_id, item)
        try:
            _apply_inbox_incoming(lead_id, preview, created or int(_time_module.time()), "whatsapp", contact_name=name, phone=phone)
            _patch_cloud_inbox_into_rufat_cache()
        except Exception:
            pass
        _invalidate_deal_chat_cache(lead_id)
        record_lead_pulse_event(
            lead_id,
            "incoming_message",
            preview=preview,
            incoming_at=created,
            channel="whatsapp",
            pipeline_id=target_pipe,
            contact_name=name,
            phone=phone,
        )
        _notify_cloud_chat_incoming(lead_id, name, preview, phone)
        logger.info("WhatsApp incoming lead=%s phone=%s type=%s", lead_id, phone, kind)
        _WA_LAST_HOOK["in"] = int(_WA_LAST_HOOK.get("in") or 0) + 1
        _WA_LAST_HOOK["lead"] = int(lead_id)


def _ingest_cloud_echoes(value: dict) -> None:
    """Messages sent from WhatsApp Business App are echoes, not inbound `messages`."""
    if not isinstance(value, dict):
        return
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    phone_number_id = str(metadata.get("phone_number_id") or "").strip()
    echoes = value.get("message_echoes") or value.get("messages") or []
    for message in echoes:
        if not isinstance(message, dict):
            continue
        wamid = str(message.get("id") or "")
        phone = str(message.get("to") or message.get("recipient") or "")
        if not phone:
            continue
        preview, kind = _wa_incoming_preview(message)
        if kind == "reaction" or not preview:
            continue
        try:
            created = int(message.get("timestamp") or 0)
        except (TypeError, ValueError):
            created = 0
        lead_id = _resolve_cloud_lead(phone, "", sender_phone_id=phone_number_id)
        if not lead_id:
            logger.warning("WhatsApp echo has no lead phone=%s", phone)
            continue
        if wamid and _already_have_wamid(lead_id, wamid):
            continue
        media_fields = _wa_cloud_media_fields(message)
        target_pipe = int(NIZAMI_PIPELINE_ID) if str(phone_number_id).strip() == str(NIZAMI_WA_PHONE_NUMBER_ID).strip() else int(RUFAT_PIPELINE_ID)
        item = _sent_message_item(
            wamid=wamid,
            text=preview,
            author="",
            message_type=str(media_fields.get("message_type") or kind),
            incoming=False,
            created_at=created,
            phone=phone,
            file_name=str(media_fields.get("file_name") or ""),
            media_url=str(media_fields.get("media_url") or ""),
        )
        item["phone_number_id"] = phone_number_id
        item["target_pipeline"] = target_pipe
        _remember_sent_message(lead_id, item)
        _append_chat_tail(lead_id, item)
        try:
            _patch_cloud_inbox_into_rufat_cache()
            record_lead_pulse_event(lead_id, "deal_update", preview=preview, channel="whatsapp", phone=phone, pipeline_id=target_pipe)
        except Exception:
            pass
        _invalidate_deal_chat_cache(lead_id)
        record_lead_pulse_event(lead_id, "deal_outgoing", preview=preview, incoming_at=0, channel="whatsapp", pipeline_id=target_pipe)
        logger.info("WhatsApp echo lead=%s phone=%s type=%s", lead_id, phone, kind)


def _process_whatsapp_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    fields = [
        str((change or {}).get("field") or "")
        for entry in (payload.get("entry") or [])
        if isinstance(entry, dict)
        for change in (entry.get("changes") or [])
        if isinstance(change, dict)
    ]
    _WA_LAST_HOOK["at"] = int(_time_module.time())
    _WA_LAST_HOOK["fields"] = fields
    try:
        store = _load_sent_messages()
        with _wa_sent_lock:
            store["_last_hook"] = {"at": _WA_LAST_HOOK["at"], "fields": fields}
        _schedule_sent_messages_save()
    except Exception:
        pass
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            field = str(change.get("field") or "")
            if field in {"smb_message_echoes", "smb_app_state_sync"} or value.get("message_echoes"):
                if field != "smb_app_state_sync":
                    _ingest_cloud_echoes(value)
                continue
            if field in {"messages", "history", ""} or value.get("messages") or value.get("statuses"):
                _ingest_cloud_incoming(value)


def _chat_item_key(item: dict) -> tuple:
    return (
        str(item.get("id") or ""),
        str(item.get("text") or ""),
        int(item.get("created_at") or 0),
        str(item.get("file_uuid") or ""),
    )


def _split_quote_prefix(text: str) -> tuple[str, str]:
    raw = str(text or "")
    stripped = raw.lstrip()
    if stripped.startswith("«") and "»" in stripped:
        quote, sep, rest = stripped.partition("»")
        body = rest.lstrip("\n").strip()
        preview = quote[1:].strip()
        if preview and body:
            return preview, body
    if not stripped.startswith(">"):
        return "", raw
    quote_lines: list[str] = []
    rest: list[str] = []
    for line in raw.splitlines():
        if not rest and line.lstrip().startswith(">"):
            quote_lines.append(line.lstrip()[1:].strip())
            continue
        if not rest and not line.strip():
            continue
        rest.append(line)
    body = "\n".join(rest).strip()
    quote = "\n".join(item for item in quote_lines if item).strip()
    if not quote or not body:
        return "", raw
    return quote, body


def _clean_body_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    quote, body = _split_quote_prefix(raw)
    return body.strip() if body else raw


def _delivery_rank(status: str) -> int:
    text = str(status or "").strip().lower()
    if text in {"error", "failed", "undelivered", "4", "-1"}:
        return 4
    if text in {"read", "seen", "viewed", "3"}:
        return 3
    if text in {"delivered", "1", "2"}:
        return 2
    if text in {"sent", "sending", "0"}:
        return 1
    return 0


def _normalized_channel(key: str) -> str:
    wanted = str(key or "").strip().lower()
    return wanted if wanted in CHAT_CHANNEL_LABELS else "whatsapp"


def _note_is_system_noise(text: str) -> bool:
    folded = str(text or "").casefold()
    return any(token in folded for token in (
        "агенты ии остановлены",
        "ai agents have been stopped",
        "цепочка",
        "не запустилась",
    ))


def _is_generic_placeholder_message(text: str) -> bool:
    t = str(text or "").strip().casefold()
    return t in {
        "whatsapp mesajı", "whatsapp mesaji", "mesaj",
        "telegram mesajı", "telegram mesaji",
        "instagram mesajı", "instagram mesaji",
        "tiktok mesajı", "tiktok mesaji",
        "waba mesajı", "waba mesaji",
    }


def _chat_item_from_note(note: dict, employee_name: str = "") -> dict | None:
    """Turn a Kommo chat-note into the same shape as a talk message."""
    if not isinstance(note, dict):
        return None
    ntype = str(note.get("type") or "").casefold()
    chat_types = {
        "incoming_chat_message", "outgoing_chat_message", "whatsapp", "waba",
        "facebook_message", "instagram_business", "chat", "call_in", "call_out",
    }
    if not note.get("is_chat") and ntype not in chat_types:
        return None
    text = str(note.get("text") or "").strip()
    if _note_is_system_noise(text):
        return None
    media = str(note.get("media_url") or "").strip()
    file_uuid = str(note.get("file_uuid") or "").strip()
    if _is_generic_placeholder_message(text) and not media and not file_uuid:
        return None
    is_media_notice = _is_media_notice_text(text)
    mtype = note.get("message_type") or "text"
    if is_media_notice:
        mtype = "picture"
        text = "" if (media or file_uuid) else "📷 Şəkil"
    if not text and not media and not file_uuid:
        return None
    incoming = bool(note.get("incoming")) or ntype.startswith("incoming")
    channel = _normalized_channel(note.get("channel"))
    author = str(note.get("author") or "")
    if incoming:
        author = "" if _is_generic_chat_author(author) else author
    elif channel == "whatsapp" or _is_generic_chat_author(author):
        author = employee_name or author
    msgid = str(note.get("msgid") or "").strip()
    return {
        "id": note.get("id"),
        "msgid": msgid,
        "external_id": msgid,
        "direction": "incoming" if incoming else "outgoing",
        "incoming": incoming,
        "author": author,
        "text": text,
        "message_type": mtype,
        "created_at": int(note.get("created_at") or 0),
        "created": note.get("created") or _deal_fmt_ts(note.get("created_at") or 0),
        "origin": channel,
        "channel": channel,
        "reply_to_message_id": "",
        "reply_to_text": "",
        "reply_to_author": "",
        "media_url": note.get("media_url") or "",
        "file_uuid": note.get("file_uuid") or "",
        "file_name": note.get("file_name") or "",
        "entity_type": note.get("entity_type") or "leads",
        "entity_id": note.get("entity_id") or "",
        "delivery_status": "" if incoming else "sent",
    }


def _load_kommo_chat_fallback(lid: int, contact_ids: list[int], employee_name: str = "") -> list[dict]:
    """When talks/messages is forbidden, Kommo still keeps the same history in notes and events."""
    rows: list[dict] = []
    for note in _fetch_entity_notes("leads", lid):
        item = _chat_item_from_note(note, employee_name)
        if item:
            rows.append(item)
    for contact_id in contact_ids:
        for note in _fetch_entity_notes("contacts", int(contact_id)):
            item = _chat_item_from_note(note, employee_name)
            if item:
                rows.append(item)
    events, _skipped = _fetch_chat_events(lid, contact_ids)
    for item in events:
        if not isinstance(item, dict):
            continue
        item["channel"] = _normalized_channel(item.get("channel") or _origin_channel_key(str(item.get("origin") or "")))
        if not item.get("incoming"):
            author = str(item.get("author") or "")
            if item["channel"] == "whatsapp" or _is_generic_chat_author(author):
                item["author"] = employee_name or author
        rows.append(item)
    for f in _fetch_entity_files_as_chat("leads", lid):
        rows.append(f)
    for cid in contact_ids:
        for f in _fetch_entity_files_as_chat("contacts", int(cid)):
            rows.append(f)
    return rows


def _photo_placeholder_text(text: str) -> bool:
    raw = str(text or "").strip().casefold()
    if _is_media_notice_text(raw):
        return True
    return raw in {"şəkil", "📷 şəkil", "sekil", "picture", "image", "sticker"}


def _candidate_is_image(cand: dict) -> bool:
    kind = str(cand.get("message_type") or "").lower()
    if kind in {"picture", "image", "sticker"}:
        return True
    if str(cand.get("content_type") or "").lower().startswith("image/"):
        return True
    return _looks_image_name(str(cand.get("file_name") or ""))


def _candidate_is_audio(cand: dict) -> bool:
    kind = str(cand.get("message_type") or "").lower()
    if kind in {"audio", "voice", "ptt"}:
        return True
    blob = f"{cand.get('file_name') or ''} {cand.get('media_url') or ''}"
    return _looks_audio_name(blob)


def _unix_seconds(value) -> int:
    try:
        stamp = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if stamp > 10_000_000_000:
        stamp //= 1000
    return stamp


def _deal_media_candidates(lid: int, contact_ids: list[int]) -> list[dict]:
    candidates: list[dict] = []
    seen: set[str] = set()

    def add(row: dict) -> None:
        if not isinstance(row, dict):
            return
        if not (str(row.get("media_url") or "").strip() or str(row.get("file_uuid") or "").strip()):
            return
        key = str(row.get("file_uuid") or row.get("media_url") or row.get("id") or "")
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append(row)

    for note in _fetch_entity_notes("leads", lid):
        add(note)
    for cid in contact_ids:
        for note in _fetch_entity_notes("contacts", int(cid)):
            add(note)
    for row in _fetch_entity_files_as_chat("leads", lid):
        add(row)
    for cid in contact_ids:
        for row in _fetch_entity_files_as_chat("contacts", int(cid)):
            add(row)
    return candidates


def _find_click_media(lid: int, contact_ids: list[int], created_at: int, kind: str, message_id: str) -> dict | None:
    """One file for the bubble the user tapped. Nearest Kommo file of that kind."""
    created_at = _unix_seconds(created_at)
    message_id = str(message_id or "").strip()
    if not created_at and not message_id:
        return None
    want_audio = str(kind or "").lower() in {"audio", "voice", "ptt"}
    best = None
    best_rank = None
    for cand in _deal_media_candidates(lid, contact_ids):
        is_audio = _candidate_is_audio(cand)
        if want_audio and not is_audio:
            continue
        if not want_audio and is_audio:
            continue
        ident = " ".join(
            str(cand.get(key) or "")
            for key in ("id", "msgid", "external_id", "file_uuid", "file_name", "text")
        )
        if message_id and message_id in ident:
            return cand
        cand_ts = _unix_seconds(cand.get("created_at"))
        if not created_at or not cand_ts:
            continue
        diff = abs(created_at - cand_ts)
        if diff > 86400:
            continue
        type_rank = 0 if (want_audio or _candidate_is_image(cand)) else 1
        rank = (type_rank, diff)
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = cand
    return best


def _link_missing_chat_media(chat: list[dict], lid: int, contact_ids: list[int]) -> None:
    needed = [
        item for item in chat
        if not str(item.get("media_url") or "").strip() and not str(item.get("file_uuid") or "").strip()
        and (
            _is_media_notice_text(str(item.get("text") or ""))
            or _photo_placeholder_text(str(item.get("text") or ""))
            or str(item.get("message_type") or "").lower() in {"picture", "image", "sticker", "audio", "video"}
        )
    ]
    if not needed:
        return

    candidates: list[dict] = []
    for note in _fetch_entity_notes("leads", lid):
        if (note.get("media_url") or note.get("file_uuid")) and note.get("message_type") in {"picture", "image", "sticker", "audio", "file", "video"}:
            candidates.append(note)
    for cid in contact_ids:
        for note in _fetch_entity_notes("contacts", int(cid)):
            if (note.get("media_url") or note.get("file_uuid")) and note.get("message_type") in {"picture", "image", "sticker", "audio", "file", "video"}:
                candidates.append(note)
    lead_files = _fetch_entity_files_as_chat("leads", lid)
    candidates.extend([f for f in lead_files if f.get("file_uuid") or f.get("media_url")])
    for cid in contact_ids:
        c_files = _fetch_entity_files_as_chat("contacts", int(cid))
        candidates.extend([f for f in c_files if f.get("file_uuid") or f.get("media_url")])

    for item in needed:
        text = str(item.get("text") or "")
        match = re.search(r'(?:ID(?:\s+сообщения)?|ID):\s*([A-Za-z0-9_-]+)', text, re.IGNORECASE)
        target_id = match.group(1).strip() if match else ""
        item_ts = int(item.get("created_at") or 0)

        best_cand = None
        best_diff = 99999999

        want_photo = _photo_placeholder_text(text) or str(item.get("message_type") or "").lower() in {"picture", "image", "sticker"}
        for cand in candidates:
            if want_photo and not _candidate_is_image(cand):
                continue
            cand_id = str(cand.get("id") or cand.get("msgid") or cand.get("external_id") or "").strip()
            cand_text = str(cand.get("text") or "")
            cand_name = str(cand.get("file_name") or "")
            if target_id and (target_id in cand_id or target_id in cand_text or target_id in cand_name):
                best_cand = cand
                break
            cand_ts = int(cand.get("created_at") or 0)
            diff = abs(item_ts - cand_ts) if item_ts and cand_ts else 999999
            if diff <= 600 and diff < best_diff:
                best_diff = diff
                best_cand = cand

        if best_cand:
            item["media_url"] = best_cand.get("media_url") or ""
            item["file_uuid"] = best_cand.get("file_uuid") or ""
            item["file_name"] = best_cand.get("file_name") or item.get("file_name") or ""
            item["message_type"] = "picture" if _candidate_is_image(best_cand) else (best_cand.get("message_type") or "picture")
            if _is_media_notice_text(text) or _photo_placeholder_text(text):
                item["text"] = ""


_WA_CHANNEL_CACHE: dict[int, tuple[float, list]] = {}


def _collect_deal_chat(
    lid: int,
    contact_ids: list[int],
    *,
    limit: int = 20,
    before: int = 0,
    channel: str = "whatsapp",
    sender_digits: str = "",
    employee_name: str = "",
    pages: int | None = None,
    link_media: bool = True,
) -> tuple[list[dict], bool, int, bool, list[dict], str]:
    """Load a merged timeline from all messenger talks; channel is the send target."""
    try:
        limit = max(1, min(int(limit or 20), 50))
    except (TypeError, ValueError):
        limit = 20
    try:
        before = int(before or 0)
    except (TypeError, ValueError):
        before = 0
    try:
        page_count = int(pages) if pages is not None else (3 if before else 1)
    except (TypeError, ValueError):
        page_count = 1
    page_count = max(1, min(page_count, 5))
    wanted = str(channel or "whatsapp").strip().lower() or "whatsapp"
    if wanted not in CHAT_CHANNEL_LABELS:
        wanted = "whatsapp"
    if wanted == "whatsapp":
        rows = _sent_messages_for_lead(lid)
        rows.sort(key=lambda item: int(item.get("created_at") or 0))
        if before:
            older = [item for item in rows if int(item.get("created_at") or 0) < before]
            page = older[-limit:] if older else []
            has_more = len(older) > limit
        else:
            page = rows[-limit:] if rows else []
            has_more = len(rows) > limit
        _apply_saved_replies(page)
        _overlay_sent_delivery(page, lid)
        reactions = _reactions_for_lead(lid)
        if reactions:
            for item in page:
                emoji = reactions.get(str(item.get("external_id") or "")) or reactions.get(str(item.get("id") or ""))
                if emoji:
                    item["my_reaction"] = emoji
        channels = [{
            "key": "whatsapp",
            "label": CHAT_CHANNEL_LABELS.get("whatsapp", "WhatsApp"),
            "talk_id": 0,
            "chat_id": "",
            "open": True,
            "sender_phone": _wa_display_number(sender_digits),
        }]
        return page, False, 0, has_more, channels, "whatsapp"
    chat: list[dict] = []
    seen_chat: set[tuple] = set()
    chat_blocked = False

    def _add_chat(item: dict | None) -> None:
        if not item:
            return
        key = _chat_item_key(item)
        if key in seen_chat:
            return
        item_text = str(item.get("text") or "").strip()
        item_clean_text = _clean_body_text(item_text)
        item_ts = int(item.get("created_at") or 0)
        item_dir = bool(item.get("incoming"))
        item_uuid = str(item.get("file_uuid") or "").strip()
        item_media = str(item.get("media_url") or "").strip()
        item_ext = str(item.get("external_id") or "").strip()
        item_id = str(item.get("id") or "").strip()
        item_is_stub = item_id.startswith(("sent-", "local-", "note-", "event-", "hook-"))

        for existing in chat:
            if bool(existing.get("incoming")) != item_dir:
                continue
            ex_id = str(existing.get("id") or "").strip()
            ex_ext = str(existing.get("external_id") or "").strip()
            ex_uuid = str(existing.get("file_uuid") or "").strip()
            ex_media = str(existing.get("media_url") or "").strip()
            ex_text = str(existing.get("text") or "").strip()
            ex_clean_text = _clean_body_text(ex_text)
            ex_ts = int(existing.get("created_at") or 0)
            ex_is_stub = ex_id.startswith(("sent-", "local-", "note-", "event-", "hook-"))

            same_ext = bool(item_ext and ex_ext and item_ext == ex_ext)
            same_file = bool((item_uuid and item_uuid == ex_uuid) or (item_media and item_media == ex_media))
            same_text = bool(
                (item_text and item_text == ex_text and abs(ex_ts - item_ts) <= 300)
                or (item_clean_text and item_clean_text == ex_clean_text and abs(ex_ts - item_ts) <= 300)
            )

            if same_ext or same_file or same_text:
                if ex_is_stub and not item_is_stub:
                    existing["id"] = item_id
                    if item_ext:
                        existing["external_id"] = item_ext
                    if item.get("talk_id"):
                        existing["talk_id"] = item["talk_id"]
                    if item_ts:
                        existing["created_at"] = item_ts
                    if item_text:
                        existing["text"] = item_text
                if item_uuid and not ex_uuid:
                    existing["file_uuid"] = item_uuid
                if item_media and not ex_media:
                    existing["media_url"] = item_media
                if item.get("message_type") and (not existing.get("message_type") or existing.get("message_type") == "text"):
                    existing["message_type"] = item.get("message_type")
                if item.get("cards") and not existing.get("cards"):
                    existing["cards"] = item.get("cards")
                if _delivery_rank(item.get("delivery_status")) > _delivery_rank(existing.get("delivery_status")):
                    existing["delivery_status"] = item.get("delivery_status")
                return

        seen_chat.add(key)
        chat.append(item)

    _channel_hit = _WA_CHANNEL_CACHE.get(int(lid)) if wanted == "whatsapp" else None
    if wanted == "whatsapp":
        talks = []
    else:
        talks = _fetch_talks(lid, contact_ids, include_contacts=True)
    channels = _channels_from_talks(talks, sender_digits)
    if not channels:
        ranked = _ranked_reply_talk_ids(talks)
        if ranked:
            fallback_talk = next((t for t in talks if _talk_id_of(t) == ranked[0]), {})
            fallback_key = _talk_channel_key(fallback_talk)
            if fallback_key not in CHAT_CHANNEL_LABELS:
                fallback_key = "whatsapp"
            channels = [{
                "key": fallback_key,
                "label": CHAT_CHANNEL_LABELS.get(fallback_key, "WhatsApp"),
                "talk_id": ranked[0],
                "chat_id": _talk_chat_id(fallback_talk),
                "open": True,
                "sender_phone": _wa_display_number(sender_digits) if fallback_key == "whatsapp" else "",
            }]
        else:
            channels = [{
                "key": "whatsapp",
                "label": CHAT_CHANNEL_LABELS.get("whatsapp", "WhatsApp"),
                "talk_id": 0,
                "chat_id": "",
                "open": True,
                "sender_phone": _wa_display_number(sender_digits),
            }]
    for row in channels:
        if row.get("key") == "whatsapp" and not str(row.get("sender_phone") or "").strip():
            row["sender_phone"] = _wa_display_number(sender_digits)
    if not any(str(row.get("key") or "") == "whatsapp" for row in channels):
        channels.insert(0, {
            "key": "whatsapp",
            "label": CHAT_CHANNEL_LABELS.get("whatsapp", "WhatsApp"),
            "talk_id": 0,
            "chat_id": "",
            "open": True,
            "sender_phone": _wa_display_number(sender_digits),
        })
    if wanted == "whatsapp" and not (_channel_hit and _time_module.monotonic() - _channel_hit[0] < 90):
        _WA_CHANNEL_CACHE[int(lid)] = (_time_module.monotonic(), list(channels))
    elif wanted == "whatsapp" and _channel_hit:
        channels = list(_channel_hit[1])
    reply_talk_id = next((int(row.get("talk_id") or 0) for row in channels if row.get("key") == wanted), 0)
    if not reply_talk_id and channels:
        reply_talk_id = int(channels[0].get("talk_id") or 0)
    if lid and reply_talk_id:
        _lead_open_talk[lid] = reply_talk_id
    pages = page_count
    page_limit = max(1, min(int(limit or 20), 50))
    talk_has_more = False
    target_rows = [row for row in channels if str(row.get("key") or "") == wanted]
    if not target_rows and channels:
        target_rows = [channels[0]]
    if wanted == "whatsapp":
        target_rows = []
    for row in target_rows:
        talk_id = int(row.get("talk_id") or 0)
        chat_ref = str(row.get("chat_id") or "")
        channel_key = str(row.get("key") or wanted)
        messages: list[dict] = []
        blocked = False
        maybe_more = False
        if talk_id:
            messages, blocked, maybe_more = _fetch_talk_messages(talk_id, pages=pages, page_limit=page_limit)
        talk_has_more = talk_has_more or maybe_more
        chat_blocked = chat_blocked or blocked
        if not messages and chat_ref:
            messages = _fetch_chat_history_by_chat_id(chat_ref)
        for message in messages:
            formatted = _format_chat_message(message, channel_key)
            if formatted:
                formatted["channel"] = channel_key
                formatted["talk_id"] = talk_id
                if not formatted.get("incoming"):
                    if not formatted.get("is_bot"):
                        author = str(formatted.get("author") or "")
                        if channel_key == "whatsapp" or _is_generic_chat_author(author):
                            formatted["author"] = employee_name or author
                _add_chat(formatted)
    if wanted != "whatsapp" and (not chat or chat_blocked):
        fallback_rows = _load_kommo_chat_fallback(lid, contact_ids, employee_name)
        for fb_item in fallback_rows:
            _add_chat(fb_item)
    with _deal_chat_cache_lock:
        preview_tail = list(_chat_open_preview.get(lid) or [])
    if wanted != "whatsapp":
        for tail_item in preview_tail:
            _add_chat(tail_item)
        if link_media:
            _link_missing_chat_media(chat, lid, contact_ids)
    for item in chat:
        txt = str(item.get("text") or "")
        if _message_is_voice(item) or _looks_audio_name(str(item.get("file_name") or "")) or _looks_audio_name(str(item.get("media_url") or "")):
            item["message_type"] = "audio"
            if _is_media_notice_text(txt) or txt.strip() in {"📷 Şəkil", "Şəkil"}:
                item["text"] = "Səs mesajı"
            continue
        if _is_media_notice_text(txt):
            item["message_type"] = "picture"
            if item.get("media_url") or item.get("file_uuid"):
                item["text"] = item.get("file_name") or ""
            else:
                item["text"] = "📷 Şəkil"
    # Cloud API sends are not returned by Kommo talks, so replay our own log and
    # drop the copies Kommo did mirror back.
    known_external = {str(item.get("external_id") or "") for item in chat if item.get("external_id")}
    outgoing_seen = [
        (str(item.get("text") or ""), int(item.get("created_at") or 0))
        for item in chat
        if not item.get("incoming")
    ]
    cloud_rows = _sent_messages_for_lead(lid) if wanted == "whatsapp" else []
    for item in cloud_rows:
        external = str(item.get("external_id") or "")
        cards = item.get("cards") if isinstance(item.get("cards"), list) else []
        text = str(item.get("text") or "")
        created = int(item.get("created_at") or 0)
        if cards:
            for existing in chat:
                if existing.get("incoming"):
                    continue
                same_ext = bool(external) and str(existing.get("external_id") or "") == external
                same_text = (
                    text
                    and str(existing.get("text") or "") == text
                    and abs(int(existing.get("created_at") or 0) - created) <= 180
                )
                if same_ext or same_text:
                    if not existing.get("cards"):
                        existing["cards"] = cards
                        existing["message_type"] = "carousel"
                    break
        if external and external in known_external:
            continue
        if text and any(
            text == other_text and abs(created - other_created) <= 180
            for other_text, other_created in outgoing_seen
        ):
            continue
        if not item.get("is_bot"):
            item.setdefault("author", employee_name or "")
        if _message_is_voice(item):
            chat[:] = [
                row for row in chat
                if not (
                    row.get("incoming")
                    and abs(int(row.get("created_at") or 0) - created) <= 180
                    and (
                        _is_media_notice_text(str(row.get("text") or ""))
                        or str(row.get("text") or "").strip() in {"📷 Şəkil", "Şəkil"}
                        or (
                            str(row.get("message_type") or "").lower() == "picture"
                            and not _looks_image_name(str(row.get("file_name") or ""))
                            and not _looks_image_name(str(row.get("media_url") or ""))
                        )
                    )
                )
            ]
        _add_chat(item)
    clean_chat: list[dict] = []
    for candidate in chat:
        txt = str(candidate.get("text") or "")
        if _is_generic_placeholder_message(txt) and not str(candidate.get("media_url") or "").strip() and not str(candidate.get("file_uuid") or "").strip():
            continue
        cand_dir = bool(candidate.get("incoming"))
        cand_text = txt.strip()
        cand_clean = _clean_body_text(cand_text)
        cand_ts = int(candidate.get("created_at") or 0)
        cand_ext = str(candidate.get("external_id") or "").strip()
        cand_uuid = str(candidate.get("file_uuid") or "").strip()
        cand_media = str(candidate.get("media_url") or "").strip()
        cand_id = str(candidate.get("id") or "").strip()
        cand_stub = cand_id.startswith(("sent-", "local-", "note-", "event-", "hook-"))

        dup = False
        for kept in clean_chat:
            if bool(kept.get("incoming")) != cand_dir:
                continue
            k_id = str(kept.get("id") or "").strip()
            k_ext = str(kept.get("external_id") or "").strip()
            k_uuid = str(kept.get("file_uuid") or "").strip()
            k_media = str(kept.get("media_url") or "").strip()
            k_text = str(kept.get("text") or "").strip()
            k_clean = _clean_body_text(k_text)
            k_ts = int(kept.get("created_at") or 0)
            k_stub = k_id.startswith(("sent-", "local-", "note-", "event-", "hook-"))

            if cand_id and k_id and cand_id == k_id:
                if _delivery_rank(candidate.get("delivery_status")) > _delivery_rank(kept.get("delivery_status")):
                    kept["delivery_status"] = candidate.get("delivery_status")
                dup = True
                break
            if cand_ext and k_ext and cand_ext == k_ext:
                if k_stub and not cand_stub:
                    kept["id"] = cand_id
                    if cand_ts:
                        kept["created_at"] = cand_ts
                if _delivery_rank(candidate.get("delivery_status")) > _delivery_rank(kept.get("delivery_status")):
                    kept["delivery_status"] = candidate.get("delivery_status")
                dup = True
                break
            if cand_uuid and k_uuid and cand_uuid == k_uuid:
                if _delivery_rank(candidate.get("delivery_status")) > _delivery_rank(kept.get("delivery_status")):
                    kept["delivery_status"] = candidate.get("delivery_status")
                dup = True
                break
            if cand_media and k_media and cand_media == k_media:
                if _delivery_rank(candidate.get("delivery_status")) > _delivery_rank(kept.get("delivery_status")):
                    kept["delivery_status"] = candidate.get("delivery_status")
                dup = True
                break
            same_c = (
                (cand_text and cand_text == k_text and abs(k_ts - cand_ts) <= 300)
                or (cand_clean and cand_clean == k_clean and abs(k_ts - cand_ts) <= 300)
            )
            if same_c:
                if k_stub and not cand_stub:
                    kept["id"] = cand_id
                    if cand_ext:
                        kept["external_id"] = cand_ext
                    if candidate.get("talk_id"):
                        kept["talk_id"] = candidate["talk_id"]
                    if cand_text:
                        kept["text"] = candidate.get("text")
                    if cand_ts:
                        kept["created_at"] = cand_ts
                if candidate.get("cards") and not kept.get("cards"):
                    kept["cards"] = candidate.get("cards")
                if _delivery_rank(candidate.get("delivery_status")) > _delivery_rank(kept.get("delivery_status")):
                    kept["delivery_status"] = candidate.get("delivery_status")
                dup = True
                break
        if not dup:
            clean_chat.append(candidate)
    chat = clean_chat
    chat.sort(key=lambda item: int(item.get("created_at") or 0))
    if before:
        older = [item for item in chat if int(item.get("created_at") or 0) < before]
        page = older[-limit:] if older else []
        has_more = len(older) > limit or (talk_has_more and len(page) >= limit)
    else:
        page = chat[-limit:] if chat else []
        has_more = talk_has_more or len(chat) > limit
    _apply_saved_replies(page)
    _overlay_sent_delivery(page, lid)
    reactions = _reactions_for_lead(lid)
    if reactions:
        for item in page:
            emoji = reactions.get(str(item.get("external_id") or "")) or reactions.get(str(item.get("id") or ""))
            if emoji:
                item["my_reaction"] = emoji
    return page, bool(chat_blocked and not page), int(reply_talk_id or 0), has_more, channels, wanted


def _message_is_voice(item: dict) -> bool:
    kind = str((item or {}).get("message_type") or "").strip().lower()
    name = str((item or {}).get("file_name") or "")
    return kind in {"audio", "voice", "ptt"} or _looks_audio_name(name)


def _overlay_sent_delivery(items: list, lead_id: int) -> None:
    sent = [
        row for row in _sent_messages_for_lead(lead_id)
        if isinstance(row, dict) and not row.get("incoming") and str(row.get("delivery_status") or "").strip()
    ]
    if not sent or not items:
        return
    for item in items:
        if not isinstance(item, dict) or item.get("incoming"):
            continue
        ext = str(item.get("external_id") or "")
        matched = None
        if ext:
            for row in sent:
                if str(row.get("external_id") or "") == ext:
                    matched = row
                    break
        if matched is None:
            try:
                created = int(item.get("created_at") or 0)
            except (TypeError, ValueError):
                created = 0
            text = str(item.get("text") or "")
            voice = _message_is_voice(item)
            candidates = []
            for row in sent:
                try:
                    row_created = int(row.get("created_at") or 0)
                except (TypeError, ValueError):
                    row_created = 0
                if not created or not row_created or abs(created - row_created) > 180:
                    continue
                if voice and _message_is_voice(row):
                    candidates.append(row)
                elif text:
                    row_txt = str(row.get("text") or "")
                    if text == row_txt or _clean_body_text(text) == _clean_body_text(row_txt):
                        candidates.append(row)
            if len(candidates) == 1:
                matched = candidates[0]
        if not matched:
            continue
        nxt = str(matched.get("delivery_status") or "")
        if nxt == "failed":
            nxt = "error"
        if _delivery_rank(nxt) > _delivery_rank(str(item.get("delivery_status") or "")):
            item["delivery_status"] = nxt


def _fetch_talk_messages(talk_id: int, pages: int = 1, page_limit: int = 50) -> tuple[list[dict], bool, bool]:
    rows: list[dict] = []
    blocked = False
    maybe_more = False
    try:
        page_limit = max(1, min(int(page_limit or 50), 250))
    except (TypeError, ValueError):
        page_limit = 50
    urls = (
        f"{KOMMO_BASE_URL}/api/v4/talks/{int(talk_id)}/messages",
        f"{KOMMO_BASE_URL}/ajax/v4/talks/{int(talk_id)}/messages",
        f"{KOMMO_BASE_URL}/ajax/v2/talks/{int(talk_id)}/messages",
    )
    working_url = None
    for url in urls:
        try:
            resp = _http.get(url, headers=HEADERS, params={"limit": page_limit, "page": 1, "order[created_at]": "desc"}, timeout=10)
        except Exception as exc:
            logger.warning("Deal talk %s %s failed: %s", talk_id, url, exc)
            continue
        if resp.status_code in {401, 402, 403, 429}:
            blocked = True
            logger.warning("Deal talk %s messages %s status %s", talk_id, url, resp.status_code)
            break
        if resp.status_code != 200:
            logger.warning("Deal talk %s messages %s status %s", talk_id, url, resp.status_code)
            continue
        payload = resp.json() if resp.content else {}
        messages = (payload.get("_embedded") or {}).get("messages") or payload.get("messages") or []
        if isinstance(messages, list):
            rows.extend(messages)
            working_url = url
            blocked = False
            maybe_more = len(messages) >= page_limit
            break
    if working_url and pages > 1:
        for page in range(2, pages + 1):
            try:
                resp = _http.get(working_url, headers=HEADERS, params={"limit": page_limit, "page": page, "order[created_at]": "desc"}, timeout=10)
            except Exception:
                break
            if resp.status_code != 200:
                break
            payload = resp.json() if resp.content else {}
            messages = (payload.get("_embedded") or {}).get("messages") or payload.get("messages") or []
            if not messages:
                maybe_more = False
                break
            rows.extend(messages)
            maybe_more = len(messages) >= page_limit
            if len(messages) < page_limit:
                break
    return rows, blocked, maybe_more


def _extract_messages_payload(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    messages = (payload.get("_embedded") or {}).get("messages") or payload.get("messages") or payload.get("items") or []
    return messages if isinstance(messages, list) else []


def _fetch_chat_history_by_chat_id(chat_id: str) -> list[dict]:
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return []
    urls = (
        f"https://amojo.kommo.com/v1/chats/{chat_id}/history",
        f"https://amojo.kommo.com/v2/chats/{chat_id}/history",
        f"{KOMMO_BASE_URL}/ajax/v4/chats/{chat_id}/history",
        f"{KOMMO_BASE_URL}/ajax/v2/chats/{chat_id}/history",
    )
    for url in urls:
        try:
            resp = requests.get(url, headers={"Authorization": f"Bearer {KOMMO_TOKEN}"}, params={"limit": 50, "offset": 0}, timeout=12)
        except Exception as exc:
            logger.warning("Chat history %s failed: %s", url, exc)
            continue
        if resp.status_code != 200:
            logger.warning("Chat history %s status %s", url, resp.status_code)
            continue
        try:
            payload = resp.json()
        except Exception:
            continue
        messages = _extract_messages_payload(payload)
        if messages:
            return messages
    return []


def _chat_delivery_status(message: dict, nested: dict, incoming: bool) -> str:
    if incoming:
        return ""
    raw = (
        nested.get("status")
        or nested.get("delivery_status")
        or nested.get("state")
        or nested.get("msgid_status")
        or message.get("status")
        or message.get("delivery_status")
        or message.get("state")
        or message.get("msgid_status")
    )
    if isinstance(raw, dict):
        raw = raw.get("type") or raw.get("name") or raw.get("code") or raw.get("id")
    text = str(raw or "").strip().lower()
    mapping = {
        "0": "sent",
        "sent": "sent",
        "sending": "sent",
        "1": "delivered",
        "delivered": "delivered",
        "deliver": "delivered",
        "2": "delivered",
        "3": "read",
        "read": "read",
        "seen": "read",
        "viewed": "read",
        "-1": "error",
        "4": "error",
        "error": "error",
        "failed": "error",
        "undelivered": "error",
    }
    if text in mapping:
        return mapping[text]
    if nested.get("read") or message.get("read") or nested.get("seen") or message.get("seen"):
        return "read"
    if nested.get("delivered") or message.get("delivered"):
        return "delivered"
    if "read" in text or "seen" in text:
        return "read"
    if "undeliver" in text or "error" in text or "fail" in text:
        return "error"
    if "deliver" in text:
        return "delivered"
    return "sent"


def _is_generic_chat_author(name: str) -> bool:
    folded = str(name or "").strip().casefold()
    if not folded:
        return True
    tokens = (
        "whatsapp", "waba", "instagram", "facebook", "messenger", "tiktok",
        "telegram", "viber", "amojo", "kommo", "menecer", "manager", "müştəri", "musteri",
    )
    if any(token in folded for token in tokens):
        return True
    return folded in {"client", "bot", "capi", "im", "wa", "fb"}


def _find_wamid(value, depth: int = 0) -> str:
    if depth > 5 or value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        return text if "wamid" in text.lower() else ""
    if isinstance(value, dict):
        for inner in value.values():
            found = _find_wamid(inner, depth + 1)
            if found:
                return found
    if isinstance(value, list):
        for inner in value[:12]:
            found = _find_wamid(inner, depth + 1)
            if found:
                return found
    return ""


def _chat_author_name(author: dict, message: dict, incoming: bool) -> str:
    name = str((author or {}).get("name") or "").strip()
    generic = _is_generic_chat_author(name)
    if incoming:
        return "" if generic else name
    if not generic:
        return name
    uid = (author or {}).get("id") or message.get("created_by") or message.get("created_by_id")
    mapped = KOMMO_USERS.get(uid, "") if uid not in (None, "") else ""
    return str(mapped or "").strip()


def _format_chat_message(message: dict, origin: str = "") -> dict | None:
    if not isinstance(message, dict):
        return None
    nested = message.get("message") if isinstance(message.get("message"), dict) else {}
    author = message.get("author") if isinstance(message.get("author"), dict) else {}
    if not author:
        author = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    attachment = message.get("attachment") if isinstance(message.get("attachment"), dict) else {}
    created = int(message.get("created_at") or message.get("timestamp") or nested.get("timestamp") or 0)
    media = _extract_media_url(attachment) or _extract_media_url(nested)
    if not media:
        media = _as_absolute_media_url(str(message.get("media") or message.get("link") or ""))
    file_uuid = _extract_file_uuid(attachment) or _extract_file_uuid(nested) or _extract_file_uuid(message)
    file_name = str(attachment.get("file_name") or nested.get("file_name") or message.get("file_name") or "").strip()
    message_type = str(nested.get("type") or message.get("message_type") or message.get("type") or "text")
    if message_type in {"incoming", "outgoing"}:
        message_type = str(nested.get("type") or "text")
    text = str(nested.get("text") or message.get("text") or "").strip()
    folded = text.casefold()
    if "агенты ии остановлены" in folded or "ai agents have been stopped" in folded:
        return None
    if _is_generic_placeholder_message(text) and not media and not file_uuid:
        return None
    if message_type in {"call", "call_in", "call_out"} or "call" in message_type:
        if _call_is_missed(nested, text) or _call_is_missed(message, text) or _call_is_missed(attachment, text):
            text = "Buraxılmış zəng"
            message_type = "call_missed"
        else:
            message_type = "audio" if media or file_uuid else message_type
    is_media_notice = _is_media_notice_text(text)
    audio_hint = message_type in {"voice", "audio", "ptt"} or _looks_audio_name(file_name)
    if is_media_notice and audio_hint:
        message_type = "audio"
        text = "Səs mesajı"
    elif is_media_notice:
        message_type = "picture"
        if media or file_uuid:
            text = file_name or ""
        else:
            text = "📷 Şəkil"
    elif not text:
        if message_type in {"voice", "audio"} or _looks_audio_name(file_name):
            text = "Səs mesajı"
            message_type = "audio" if message_type not in {"voice", "audio"} else message_type
        elif message_type == "picture" or _looks_image_name(file_name):
            text = file_name or "Şəkil"
            message_type = "picture"
        elif message_type in {"file", "video", "sticker"}:
            text = file_name or message_type
    elif _looks_image_name(file_name) and message_type in {"text", ""}:
        message_type = "picture"
    direction = str(message.get("type") or message.get("direction") or "")
    incoming = direction == "incoming" or str(author.get("type") or "") == "external"
    if direction not in {"incoming", "outgoing"} and str(author.get("client_id") or ""):
        incoming = True
    author_avatar = _as_absolute_media_url(str((author or {}).get("avatar") or (author or {}).get("photo") or (author or {}).get("icon") or ""))
    if incoming and _looks_profile_photo(file_name, message_type):
        message_type = "avatar"
        if text.casefold() in {"şəkil", "sekil", "picture", "sticker", "avatar"}:
            text = ""
        if not author_avatar:
            author_avatar = media
    author_type = str((author or {}).get("type") or "").strip().lower()
    raw_author_name = str((author or {}).get("name") or "").strip()
    is_bot = False
    bot_name = ""
    if author_type == "bot" or "salesbot" in raw_author_name.casefold() or "gpt" in raw_author_name.casefold():
        folded_bot = raw_author_name.casefold()
        transport_tokens = ("whatsapp", "waba", "telegram", "viber", "instagram", "facebook", "messenger", "tiktok")
        if not any(t in folded_bot for t in transport_tokens):
            is_bot = True
            bot_name = raw_author_name or "Salesbot"

    if not text and not media and not file_uuid and not author_avatar:
        reaction_obj = nested.get("reaction") or message.get("reaction") or {}
        if isinstance(reaction_obj, dict) and reaction_obj.get("emoji"):
            emoji = str(reaction_obj.get("emoji") or "").strip()
            text = f"💬 Reaksiya: {emoji}" if emoji else "💬 Müştəri reaksiya bildirdi"
            message_type = "reaction"
        elif incoming:
            text = "💬 Müştəri reaksiya bildirdi"
            message_type = "reaction"
        else:
            return None
    reply_src = nested.get("reply_to") or nested.get("replied_message") or message.get("reply_to")
    reply_id = ""
    reply_text = ""
    reply_author = ""
    if isinstance(reply_src, dict):
        reply_msg = reply_src.get("message") if isinstance(reply_src.get("message"), dict) else reply_src
        reply_id = str(
            reply_msg.get("id")
            or reply_msg.get("msgid")
            or reply_src.get("message_id")
            or reply_src.get("id")
            or ""
        ).strip()
        reply_text = str(reply_msg.get("text") or reply_src.get("text") or "").strip()[:200]
        sender = reply_msg.get("author") or reply_msg.get("sender") or reply_src.get("author") or reply_src.get("sender") or {}
        if isinstance(sender, dict):
            reply_author = str(sender.get("name") or "").strip()[:80]
    elif reply_src:
        reply_id = str(reply_src).strip()
    inline_quote, inline_body = _split_quote_prefix(text)
    if inline_quote:
        text = inline_body
        reply_text = reply_text or inline_quote
    msg_id, msgid, external_id = _chat_message_ids(message, nested)
    return {
        "id": msg_id or msgid,
        "msgid": msgid,
        "external_id": external_id,
        "direction": "incoming" if incoming else "outgoing",
        "incoming": incoming,
        "author": bot_name if is_bot else _chat_author_name(author, message, incoming),
        "is_bot": is_bot,
        "bot_name": bot_name,
        "text": text,
        "message_type": message_type,
        "created_at": created,
        "created": _deal_fmt_ts(created),
        "origin": origin or str(message.get("origin") or ""),
        "channel": _origin_channel_key(origin) or str(origin or "").strip().lower(),
        "reply_to_message_id": reply_id,
        "reply_to_text": reply_text,
        "reply_to_author": reply_author,
        "media_url": "" if message_type == "avatar" else (media if _is_allowed_media_url(media) else ""),
        "file_uuid": "" if message_type == "avatar" else file_uuid,
        "file_name": file_name,
        "author_avatar": author_avatar,
        "delivery_status": _chat_delivery_status(message, nested, incoming),
    }


def _fetch_chat_events(lead_id: int, contact_ids: list[int]) -> tuple[list[dict], bool]:
    rows: list[dict] = []
    skipped = False
    entities = [("lead", int(lead_id))] + [("contact", int(cid)) for cid in contact_ids]
    for entity_type, entity_id in entities:
        try:
            resp = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/events",
                headers=HEADERS,
                params={
                    "filter[entity]": entity_type,
                    "filter[entity_id]": entity_id,
                    "filter[type]": "incoming_chat_message,outgoing_chat_message,incoming_sms,outgoing_sms,incoming_call,outgoing_call",
                    "limit": 100,
                },
                timeout=12,
            )
        except Exception as exc:
            logger.warning("Deal chat events %s/%s failed: %s", entity_type, entity_id, exc)
            continue
        if resp.status_code != 200:
            logger.warning("Deal chat events %s/%s status %s", entity_type, entity_id, resp.status_code)
            continue
        for event in (resp.json().get("_embedded") or {}).get("events", []) or []:
            if not isinstance(event, dict):
                continue
            etype = str(event.get("type") or "")
            incoming = etype.startswith("incoming")
            after = event.get("value_after") or []
            payload = after[0] if after and isinstance(after[0], dict) else {}
            message = payload.get("message") if isinstance(payload.get("message"), dict) else payload
            text = _extract_nested_text(message)
            media = str(message.get("media") or message.get("link") or "").strip() if isinstance(message, dict) else ""
            file_uuid = _extract_file_uuid(message) if isinstance(message, dict) else ""
            call_event = "call" in etype
            if call_event and (not text or _call_is_missed(message if isinstance(message, dict) else {}, text) or _call_is_missed(payload, text)):
                text = "Buraxılmış zəng"
            origin = ""
            if isinstance(message, dict):
                origin = str(message.get("origin") or message.get("source") or "").strip()
            if not origin and isinstance(payload, dict):
                origin = str(payload.get("origin") or "").strip()
            channel = _origin_channel_key(origin) or _origin_channel_key(etype)
            if not text and not media and not file_uuid:
                skipped = True
                continue
            if _is_generic_placeholder_message(text) and not media and not file_uuid:
                skipped = True
                continue
            created = int(event.get("created_at") or 0)
            msgid = _first_msgid(message, payload, event) if isinstance(message, dict) else _first_msgid(payload, event)
            wamid = _first_wamid(message, payload, event)
            external = wamid or msgid or ""
            if isinstance(message, dict) and not external:
                external = str(message.get("id") or message.get("msgid") or "").strip()
            is_media_notice = _is_media_notice_text(text)
            mtype = "call_missed" if call_event or str(text).startswith("Buraxılmış") else ("picture" if is_media_notice else ("audio" if _looks_audio_name(text) else "text"))
            if is_media_notice:
                text = "" if (media or file_uuid) else "📷 Şəkil"
            rows.append({
                "id": event.get("id") or external,
                "msgid": msgid or wamid or external,
                "external_id": external,
                "direction": "incoming" if incoming else "outgoing",
                "incoming": incoming,
                "author": KOMMO_USERS.get(event.get("created_by") or event.get("created_by_id"), "") or "",
                "text": text,
                "message_type": mtype,
                "created_at": created,
                "created": _deal_fmt_ts(created),
                "origin": origin or etype,
                "channel": channel,
                "media_url": media if _is_allowed_media_url(media) else "",
                "file_uuid": file_uuid,
                "file_name": "",
                "delivery_status": "" if incoming else "sent",
            })
    return rows, skipped


_entity_files_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_entity_files_lock = threading.Lock()
_ENTITY_FILES_TTL = 90.0


def _fetch_entity_files_as_chat(entity_type: str, entity_id: int) -> list[dict]:
    eid = int(entity_id or 0)
    etype = str(entity_type or "leads")
    if not eid:
        return []
    cache_key = (etype, eid)
    now = _time_module.monotonic()
    with _entity_files_lock:
        cached = _entity_files_cache.get(cache_key)
        if cached and (now - cached[0] < _ENTITY_FILES_TTL):
            return list(cached[1])
    try:
        resp = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/{etype}/{eid}/files",
            headers=HEADERS,
            params={"limit": 50},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Deal files %s/%s failed: %s", etype, eid, exc)
        return []
    if resp.status_code != 200:
        return []
    rows = []
    for item in (resp.json().get("_embedded") or {}).get("files", []) or []:
        uuid = str(item.get("file_uuid") or item.get("uuid") or "").strip()
        name = str(item.get("name") or item.get("file_name") or "").strip()
        if not uuid:
            continue
        if not _looks_audio_name(name) and not name:
            # still include unnamed files as possible voice
            name = "Fayl"
        created = int(item.get("created_at") or 0)
        content_type = str(item.get("content_type") or item.get("type") or "").lower()
        is_img = _looks_image_name(name) or content_type.startswith("image/")
        is_aud = _looks_audio_name(name) or content_type.startswith("audio/")
        rows.append({
            "id": item.get("id") or uuid,
            "direction": "incoming",
            "incoming": True,
            "author": "",
            "text": "Səs mesajı" if is_aud else ("Şəkil" if is_img else name),
            "message_type": "audio" if is_aud else ("picture" if is_img else "file"),
            "created_at": created,
            "created": _deal_fmt_ts(created),
            "origin": "file",
            "media_url": "",
            "file_uuid": uuid,
            "file_name": name,
        })
    with _entity_files_lock:
        _entity_files_cache[cache_key] = (now, rows)
    return rows


def build_deal_view_payload(lead_id: int, lead: dict | None = None, *, require_personal: bool = True) -> dict | None:
    """Read-only snapshot of a deal for in-app and shared view."""
    lead = lead if isinstance(lead, dict) else get_lead_details(int(lead_id))
    if not lead:
        return None
    try:
        lid = int(lead.get("id") or lead_id)
        pipeline_id = int(lead.get("pipeline_id") or 0)
        status_id = int(lead.get("status_id") or 0)
    except (TypeError, ValueError):
        return None
    if require_personal and pipeline_id not in all_personal_pipeline_ids():
        return None
    stages, names, _ui = load_pipeline_stage_maps(pipeline_id)
    status_to_key = {int(sid): key for key, sid in stages.items()}
    lead_contacts = (lead.get("_embedded") or {}).get("contacts") or []
    contact_rows = []
    all_phones: list[str] = []
    contact_ids: list[int] = []
    contact_avatar = ""
    source = extract_menbe(lead)
    utm_blob = collect_utm_blob(lead)
    source_urls = collect_source_urls(lead)
    for linked in lead_contacts:
        if not isinstance(linked, dict):
            continue
        linked_id = linked.get("id")
        full = linked
        if str(linked_id).isdigit():
            fetched = get_contact_details(int(linked_id))
            if fetched:
                full = fetched
            contact_ids.append(int(linked_id))
        phones = _contact_phones(full)
        for phone in phones:
            if phone not in all_phones:
                all_phones.append(phone)
        contact_rows.append({"id": linked_id, "name": full.get("name", ""), "phones": phones})
        if not contact_avatar:
            contact_avatar = _first_avatar_url(full)
        source = extract_menbe(full) or source
        utm_blob = " ".join((utm_blob, collect_utm_blob(full)))
        for url in collect_source_urls(full):
            if url not in source_urls:
                source_urls.append(url)
    partner, utm = split_partner_and_utm(source, utm_blob, lid)
    for url in _SOURCE_URL_RE.findall(utm_blob or ""):
        if url not in source_urls:
            source_urls.append(url)
    contact_name = next((row.get("name") for row in contact_rows if row.get("name")), "")
    note_rows = _fetch_entity_notes("leads", lid)
    for contact_id in contact_ids:
        note_rows.extend(_fetch_entity_notes("contacts", contact_id))
    note_rows.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
    seen_notes: set[tuple] = set()
    unique_notes: list[dict] = []
    for note in note_rows:
        key = (note.get("text"), note.get("created_at"), note.get("type"))
        if key in seen_notes:
            continue
        seen_notes.add(key)
        unique_notes.append(note)
    notes = [
        item for item in unique_notes
        if (
            item.get("type") == "common"
            or item.get("type") in {"call_in", "call_out"}
            or not item.get("is_chat")
        ) and not _note_is_deleted(item)
    ]
    tasks = _fetch_open_tasks_for_entities([lid, *contact_ids])
    first_task = tasks[0] if tasks else {}
    last_note = next((item.get("text") for item in notes if item.get("text")), "")
    talks = _fetch_talks(lid, contact_ids)
    chat_channel = ""
    best_talk = -1
    for talk in talks or []:
        key = _talk_channel_key(talk)
        if key not in CHAT_CHANNEL_LABELS:
            continue
        try:
            updated = int(talk.get("updated_at") or talk.get("created_at") or 0)
        except (TypeError, ValueError):
            updated = 0
        if updated >= best_talk:
            best_talk = updated
            chat_channel = key
            contact_avatar = _first_avatar_url(talk) or contact_avatar
    funnel_owner_name = owner_name_for_pipeline(pipeline_id)
    responsible_name = funnel_owner_name or KOMMO_USERS.get(lead.get("responsible_user_id"), "") or ""
    payload = {
        "id": lid,
        "name": lead.get("name") or "",
        "pipeline_id": pipeline_id,
        "stage_key": status_to_key.get(status_id, ""),
        "stage_name": names.get(status_id, "Naməlum mərhələ"),
        "contact_name": contact_name,
        "responsible_name": responsible_name,
        "funnel_owner_name": funnel_owner_name,
        "phone": all_phones[0] if all_phones else "",
        "phones": all_phones,
        "contacts": contact_rows,
        "partner": partner,
        "utm": utm,
        "utm_tag": utm,
        "source": utm,
        "menbe": utm,
        "source_urls": source_urls,
        "created_at": lead.get("created_at", 0),
        "updated_at": lead.get("updated_at", 0),
        "last_note": last_note,
        "chat_channel": chat_channel,
        "contact_avatar": contact_avatar,
        "notes": notes,
        "task_desc": first_task.get("text") or "",
        "deadline": first_task.get("deadline") or "",
        "deadline_ts": int(first_task.get("complete_till") or 0),
        "tasks": tasks,
        "chat": [],
        "chat_blocked": False,
        "reply_talk_id": 0,
        "can_reply": bool(os.environ.get("WHATSAPP_ACCESS_TOKEN") or os.environ.get("WHATSAPP_TOKEN")),
        "bot_stopped": _is_lead_bot_stopped(lid),
        "voice_url": f"/api/voice/{lid}" if str(lid) in _voice_urls else "",
        "kommo_link": f"{KOMMO_BASE_URL}/leads/detail/{lid}",
    }
    visible_notes = [item for item in notes if str(item.get("text") or "").strip()]
    _remember_deal_side(lid, visible_notes[0] if visible_notes else None, first_task or None)
    return payload


async def handle_api_deal_view(request: web.Request) -> web.Response:
    raw_chat_id = (
        request.headers.get("X-TG-User-ID")
        or request.rel_url.query.get("uid")
        or ""
    )
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    try:
        lead_id = int(request.rel_url.query.get("lead_id") or 0)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "lead_id required"}, status=400)
    if not lead_id:
        return web.json_response({"success": False, "error": "lead_id required"}, status=400)
    if not is_funnel_chat(chat_id) and not is_admin(chat_id):
        return web.json_response({"success": False, "error": "Access denied"}, status=403)
    lead = get_lead_details(lead_id)
    if not lead:
        return web.json_response({"success": False, "error": "Sövdələşmə tapılmadı"}, status=404)
    if not _user_can_view_personal_lead(chat_id, lead):
        return web.json_response({"success": False, "error": "Access denied"}, status=403)
    deal = build_deal_view_payload(lead_id, lead, require_personal=not is_admin(chat_id))
    if not deal:
        return web.json_response({"success": False, "error": "Sövdələşmə tapılmadı"}, status=404)
    return web.json_response({"success": True, "deal": deal, "share_token": make_deal_share_token(lead_id)})


def _deal_request_user(request: web.Request):
    raw_chat_id = (
        request.headers.get("X-TG-User-ID")
        or request.rel_url.query.get("uid")
        or ""
    )
    try:
        return int(raw_chat_id)
    except (TypeError, ValueError):
        return None


def _authorized_deal_lead(chat_id: int, lead_id: int):
    if not is_funnel_chat(chat_id) and not is_admin(chat_id):
        return None, web.json_response({"success": False, "error": "Access denied"}, status=403)
    lead = get_lead_details(lead_id)
    if not lead:
        return None, web.json_response({"success": False, "error": "Sövdələşmə tapılmadı"}, status=404)
    if not _user_can_view_personal_lead(chat_id, lead):
        return None, web.json_response({"success": False, "error": "Access denied"}, status=403)
    return lead, None


_deal_chat_cache: dict[tuple, tuple[float, dict]] = {}
_chat_open_preview: dict[int, list] = {}
_deal_chat_cache_lock = threading.Lock()
_DEAL_CHAT_CACHE_TTL = 3.0
_chat_collect_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="deal-chat")


def _invalidate_deal_chat_cache(lead_id: int = 0) -> None:
    with _deal_chat_cache_lock:
        if not lead_id:
            _deal_chat_cache.clear()
            return
        try:
            wanted = int(lead_id)
        except (TypeError, ValueError):
            return
        for key in [item for item in _deal_chat_cache if item[0] == wanted]:
            _deal_chat_cache.pop(key, None)


_CHAT_TAIL_KEEP = 15
_chat_tail_warmed: set[int] = set()
_chat_tail_queue: list[int] = []
_chat_tail_queued: set[int] = set()
_chat_tail_warm_lock = threading.Lock()
_chat_tail_warm_task: asyncio.Task | None = None
_tail_refresh_pending: set[int] = set()
_chat_tail_inflight: set[int] = set()
_deal_side_cache: dict[int, dict] = {}


def _tail_identity(item: dict) -> tuple:
    external = str(item.get("external_id") or "").strip()
    if external:
        return ("ext", external)
    mid = str(item.get("id") or "").strip()
    if mid and not mid.startswith(("hook-", "sent-", "preview-", "local-")):
        return ("id", mid)
    try:
        created = int(item.get("created_at") or 0)
    except (TypeError, ValueError):
        created = 0
    return ("soft", bool(item.get("incoming")), str(item.get("text") or ""), created // 3)


def _merge_tail_rows(existing: list, extra: list) -> list:
    merged: list[dict] = []
    index: dict[tuple, int] = {}
    for item in list(existing or []) + list(extra or []):
        if not isinstance(item, dict):
            continue
        if _is_generic_placeholder_message(str(item.get("text") or "")) and not item.get("media_url") and not item.get("file_uuid"):
            continue
        key = _tail_identity(item)
        slot = index.get(key)
        if slot is not None:
            merged[slot] = item
            continue
        replaced = False
        text = str(item.get("text") or "").strip()
        try:
            created = int(item.get("created_at") or 0)
        except (TypeError, ValueError):
            created = 0
        incoming = bool(item.get("incoming"))
        if text:
            clean_item_text = _clean_body_text(text)
            for pos, old in enumerate(merged):
                old_id = str(old.get("id") or "")
                if not old_id.startswith(("hook-", "sent-", "local-", "preview-")):
                    continue
                if bool(old.get("incoming")) != incoming:
                    continue
                old_text = str(old.get("text") or "").strip()
                old_clean = _clean_body_text(old_text)
                if old_text != text and (not clean_item_text or not old_clean or old_clean != clean_item_text):
                    continue
                try:
                    old_created = int(old.get("created_at") or 0)
                except (TypeError, ValueError):
                    old_created = 0
                if abs(old_created - created) > 300:
                    continue
                merged[pos] = item
                index[key] = pos
                replaced = True
                break
        if replaced:
            continue
        index[key] = len(merged)
        merged.append(item)
    merged.sort(key=lambda row: int(row.get("created_at") or 0))
    return merged[-_CHAT_TAIL_KEEP:]


def _store_chat_tail(lead_id: int, rows: list) -> None:
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return
    if not lid:
        return
    with _deal_chat_cache_lock:
        current = list(_chat_open_preview.get(lid) or [])
        _chat_open_preview[lid] = _merge_tail_rows(current, rows)


def _append_chat_tail(lead_id: int, item: dict) -> None:
    if not isinstance(item, dict):
        return
    _store_chat_tail(lead_id, [item])


def _overlay_tail_delivery(lead_id: int) -> None:
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return
    with _deal_chat_cache_lock:
        rows = _chat_open_preview.get(lid)
    if rows:
        _overlay_sent_delivery(rows, lid)


def _capture_incoming_tail(
    lead_id: int,
    preview: str,
    created_at: int,
    origin: str,
    *,
    talk_id: int = 0,
    message_id: str = "",
    media_url: str = "",
    file_uuid: str = "",
    message_type: str = "",
) -> None:
    try:
        lid = int(lead_id)
        created = int(created_at or 0)
        talk = int(talk_id or 0)
    except (TypeError, ValueError):
        return
    if not lid:
        return
    channel = _origin_channel_key(origin) or "whatsapp"
    mid = str(message_id or "").strip() or f"hook-{lid}-{created}"
    raw_preview = str(preview or "").strip()
    is_media = _is_media_notice_text(raw_preview)
    mtype = message_type or ("picture" if (is_media or media_url or file_uuid) else "text")
    clean_text = ("📷 Şəkil" if not (media_url or file_uuid) else "") if is_media else raw_preview
    _append_chat_tail(lid, {
        "id": mid,
        "incoming": True,
        "direction": "incoming",
        "text": clean_text,
        "created_at": created or int(_time_module.time()),
        "message_type": mtype,
        "channel": channel,
        "origin": origin,
        "talk_id": talk,
        "author": "",
        "media_url": media_url,
        "file_uuid": file_uuid,
    })
    if talk:
        _lead_open_talk[lid] = talk
        _schedule_talk_tail_refresh(lid, talk, origin)


def _chat_tail_ids(deals: list) -> list[int]:
    ranked: list[tuple[int, int]] = []
    for deal in deals or []:
        if not isinstance(deal, dict):
            continue
        if not (
            deal.get("last_client_message")
            or deal.get("chat_channel")
            or deal.get("last_incoming_at")
            or deal.get("chat_at")
            or deal.get("inbox_only")
        ):
            continue
        try:
            lid = int(deal.get("id") or 0)
            stamp = int(deal.get("chat_at") or deal.get("last_incoming_at") or 0)
        except (TypeError, ValueError):
            continue
        if lid:
            ranked.append((stamp, lid))
    ranked.sort(reverse=True)
    seen: set[int] = set()
    ids: list[int] = []
    for _stamp, lid in ranked:
        if lid in seen:
            continue
        seen.add(lid)
        ids.append(lid)
        if len(ids) >= 150:
            break
    return ids


def _contact_ids_from_cached_deal(lead_id: int) -> list[int]:
    ids: list[int] = []
    for overview in _personal_overview_cache.values():
        if not isinstance(overview, dict):
            continue
        for deal in overview.get("deals") or []:
            if not isinstance(deal, dict) or int(deal.get("id") or 0) != int(lead_id):
                continue
            for contact in deal.get("contacts") or []:
                if not isinstance(contact, dict):
                    continue
                try:
                    cid = int(contact.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                if cid and cid not in ids:
                    ids.append(cid)
    return ids


def _remember_deal_side(lead_id: int, note: dict | None, task: dict | None) -> None:
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return
    if not lid:
        return
    _deal_side_cache[lid] = {
        "note": note if isinstance(note, dict) and str(note.get("text") or "").strip() else None,
        "task": task if isinstance(task, dict) and task.get("id") else None,
    }


def _forget_cached_note(note_id: int) -> None:
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return
    for side in _deal_side_cache.values():
        note = side.get("note") if isinstance(side, dict) else None
        if isinstance(note, dict) and int(note.get("id") or 0) == nid:
            side["note"] = None


def _load_deal_side(lead_id: int) -> None:
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return
    note = None
    try:
        resp = _http.get(
            f"{KOMMO_BASE_URL}/api/v4/leads/{lid}/notes",
            headers=HEADERS,
            params={"limit": 15, "order[created_at]": "desc"},
            timeout=10,
        )
        if resp.status_code == 200:
            for raw in ((resp.json() or {}).get("_embedded") or {}).get("notes") or []:
                formatted = _format_deal_note(raw, "leads")
                if not formatted or _note_is_deleted(formatted):
                    continue
                if formatted.get("type") in {"call_in", "call_out"} or formatted.get("is_chat"):
                    continue
                if str(formatted.get("text") or "").strip():
                    note = formatted
                    break
    except Exception as exc:
        logger.warning("Latest note failed lead=%s: %s", lid, exc)
    task = None
    try:
        tasks = _fetch_open_tasks_for_entities([lid])
        task = tasks[0] if tasks else None
    except Exception as exc:
        logger.warning("Latest task failed lead=%s: %s", lid, exc)
    _remember_deal_side(lid, note, task)


def _warm_one_chat_tail(lead_id: int) -> None:
    page, blocked, _talk, _more, _channels, _channel = _collect_deal_chat(
        int(lead_id),
        _contact_ids_from_cached_deal(lead_id),
        limit=_CHAT_TAIL_KEEP,
        link_media=False,
    )
    if page:
        _store_chat_tail(lead_id, page)
        _chat_tail_warmed.add(int(lead_id))
    elif not blocked:
        _chat_tail_warmed.add(int(lead_id))
    try:
        _load_deal_side(int(lead_id))
    except Exception as exc:
        logger.warning("Deal side warm failed lead=%s: %s", lead_id, exc)


async def _warm_chat_tail_loop() -> None:
    while True:
        lead_id = 0
        with _chat_tail_warm_lock:
            while _chat_tail_queue:
                candidate = _chat_tail_queue.pop(0)
                _chat_tail_queued.discard(candidate)
                if candidate in _chat_tail_inflight:
                    continue
                if (
                    candidate in _chat_tail_warmed
                    and _chat_open_preview.get(candidate)
                    and candidate in _deal_side_cache
                ):
                    continue
                _chat_tail_inflight.add(candidate)
                lead_id = candidate
                break
            if not lead_id and not _chat_tail_queue:
                return
        if not lead_id:
            await asyncio.sleep(0.3)
            continue
        try:
            if _chat_open_preview.get(lead_id) and lead_id in _chat_tail_warmed:
                await asyncio.to_thread(_load_deal_side, lead_id)
            else:
                await asyncio.to_thread(_warm_one_chat_tail, lead_id)
        except Exception as exc:
            logger.warning("Chat tail warm failed lead=%s: %s", lead_id, exc)
        finally:
            _chat_tail_inflight.discard(lead_id)
        await asyncio.sleep(2.0)


def _priority_chat_tail(lead_id: int) -> None:
    """Move one opened chat to the front of the single warm queue."""
    global _chat_tail_warm_task
    try:
        lid = int(lead_id)
    except (TypeError, ValueError):
        return
    has_tail = bool(_chat_open_preview.get(lid))
    has_side = lid in _deal_side_cache
    if not lid or (has_tail and has_side):
        return
    if lid in _chat_tail_warmed and has_side and not has_tail:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    with _chat_tail_warm_lock:
        if lid in _chat_tail_inflight:
            return
        _chat_tail_queue[:] = [item for item in _chat_tail_queue if item != lid]
        _chat_tail_queue.insert(0, lid)
        _chat_tail_queued.add(lid)
        if _chat_tail_warm_task is None or _chat_tail_warm_task.done():
            _chat_tail_warm_task = loop.create_task(_warm_chat_tail_loop())


def _schedule_chat_tail_warm(deals_or_ids) -> None:
    global _chat_tail_warm_task
    if deals_or_ids and isinstance(deals_or_ids[0], dict):
        lead_ids = _chat_tail_ids(deals_or_ids)
    else:
        lead_ids = []
        for item in deals_or_ids or []:
            try:
                lid = int(item)
            except (TypeError, ValueError):
                continue
            if lid:
                lead_ids.append(lid)
    if not lead_ids:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    start = False
    with _chat_tail_warm_lock:
        for lid in lead_ids:
            if lid in _chat_tail_warmed or lid in _chat_tail_queued:
                continue
            _chat_tail_queued.add(lid)
            _chat_tail_queue.append(lid)
            start = True
        if start and (_chat_tail_warm_task is None or _chat_tail_warm_task.done()):
            _chat_tail_warm_task = loop.create_task(_warm_chat_tail_loop())


async def _refresh_one_talk_tail(lead_id: int, talk_id: int, origin: str) -> None:
    channel = _origin_channel_key(origin) or "whatsapp"

    def _load() -> list:
        messages, _blocked, _more = _fetch_talk_messages(int(talk_id), pages=1, page_limit=_CHAT_TAIL_KEEP)
        rows = []
        for message in messages:
            formatted = _format_chat_message(message, channel)
            if not formatted:
                continue
            formatted["channel"] = channel
            formatted["talk_id"] = int(talk_id)
            rows.append(formatted)
        rows.sort(key=lambda item: int(item.get("created_at") or 0))
        return rows[-_CHAT_TAIL_KEEP:]

    try:
        rows = await asyncio.to_thread(_load)
        if rows:
            _store_chat_tail(lead_id, rows)
            _invalidate_deal_chat_cache(int(lead_id))
            record_lead_pulse_event(int(lead_id), "deal_update")
    except Exception as exc:
        logger.warning("Talk tail refresh failed lead=%s talk=%s: %s", lead_id, talk_id, exc)
    finally:
        _tail_refresh_pending.discard(int(lead_id))


def _schedule_talk_tail_refresh(lead_id: int, talk_id: int, origin: str) -> None:
    try:
        lid = int(lead_id)
        talk = int(talk_id)
    except (TypeError, ValueError):
        return
    if not lid or not talk or lid in _tail_refresh_pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _tail_refresh_pending.add(lid)
    loop.create_task(_refresh_one_talk_tail(lid, talk, origin))


def _lead_phones_fast(lead: dict) -> tuple[list[int], list[str]]:
    contact_ids = _lead_contact_ids(lead)
    phones: list[str] = []
    for linked in (lead.get("_embedded") or {}).get("contacts") or []:
        if not isinstance(linked, dict):
            continue
        for phone in _contact_phones(linked):
            if phone and phone not in phones:
                phones.append(phone)
    return contact_ids, phones


def _lead_contact_ids(lead: dict) -> list[int]:
    ids: list[int] = []
    for linked in (lead.get("_embedded") or {}).get("contacts") or []:
        if not isinstance(linked, dict):
            continue
        try:
            ids.append(int(linked.get("id")))
        except (TypeError, ValueError):
            continue
    return ids


def _contact_ids_and_phones(lead: dict) -> tuple[list[int], list[str]]:
    contact_ids: list[int] = []
    phones: list[str] = []

    def _add(raw) -> None:
        value = str(raw or "").strip()
        if value and value not in phones:
            phones.append(value)

    if isinstance(lead, dict):
        _add(lead.get("phone"))
        extra = lead.get("phones")
        if isinstance(extra, list):
            for item in extra:
                _add(item)
    for linked in (lead.get("_embedded") or {}).get("contacts") or []:
        if not isinstance(linked, dict):
            continue
        try:
            cid = int(linked.get("id"))
        except (TypeError, ValueError):
            cid = 0
        if cid:
            contact_ids.append(cid)
        for phone in _contact_phones(linked):
            _add(phone)
        if cid and not phones:
            full = get_contact_details(cid) or {}
            for phone in _contact_phones(full):
                _add(phone)
    return contact_ids, phones


async def handle_api_deal_chat(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    try:
        lead_id = int(request.rel_url.query.get("lead_id") or 0)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "lead_id required"}, status=400)
    if not lead_id:
        return web.json_response({"success": False, "error": "lead_id required"}, status=400)
    lead, err = _authorized_deal_lead(chat_id, lead_id)
    if err:
        return err
    contact_ids, phones = _lead_phones_fast(lead)
    for phone in phones:
        _remember_phone_lead(phone, int(lead.get("id") or lead_id))
    try:
        limit = int(request.rel_url.query.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    try:
        before = int(request.rel_url.query.get("before") or 0)
    except (TypeError, ValueError):
        before = 0
    channel = str(request.rel_url.query.get("channel") or "whatsapp").strip().lower()
    sender_digits = _hinted_wa_sender_digits(chat_id, request.rel_url.query.get("sender_phone"))
    if str(request.rel_url.query.get("preview") or "") == "1":
        remembered: list = []
        lid = int(lead.get("id") or lead_id)
        if channel == "whatsapp":
            rows = _sent_messages_for_lead(lid)
            rows.sort(key=lambda item: int(item.get("created_at") or 0))
            remembered = rows[-_CHAT_TAIL_KEEP:]
            _overlay_sent_delivery(remembered, lid)
            cloud_ready = _wa_cloud_ready(sender_digits)
            return web.json_response({
                "success": True,
                "preview": True,
                "chat": remembered,
                "has_more": len(rows) > _CHAT_TAIL_KEEP,
                "chat_blocked": False,
                "channel": channel,
                "channels": [{
                    "key": "whatsapp",
                    "label": CHAT_CHANNEL_LABELS.get("whatsapp", "WhatsApp"),
                    "talk_id": 0,
                    "chat_id": "",
                    "open": True,
                    "sender_phone": _wa_display_number(sender_digits),
                }],
                "reply_talk_id": 0,
                "can_reply": cloud_ready,
                "cloud_ready": cloud_ready,
                "bot_stopped": _is_lead_bot_stopped(lid),
                "last_note": (_deal_side_cache.get(lid) or {}).get("note"),
                "last_task": (_deal_side_cache.get(lid) or {}).get("task"),
                "side_ready": lid in _deal_side_cache,
            })
        with _deal_chat_cache_lock:
            remembered = list(_chat_open_preview.get(lid) or [])[-_CHAT_TAIL_KEEP:]
            if not remembered:
                for key, cached in _deal_chat_cache.items():
                    if not key or key[0] != lid:
                        continue
                    rows = (cached[1] or {}).get("chat") if isinstance(cached[1], dict) else []
                    if rows:
                        remembered = list(rows)[-_CHAT_TAIL_KEEP:]
                        break
        if not remembered or lid not in _deal_side_cache:
            _priority_chat_tail(lid)
        _overlay_sent_delivery(remembered, lid)
        return web.json_response({
            "success": True,
            "preview": True,
            "chat": remembered,
            "has_more": True,
            "chat_blocked": False,
            "channel": channel,
            "channels": [],
            "last_note": (_deal_side_cache.get(lid) or {}).get("note"),
            "last_task": (_deal_side_cache.get(lid) or {}).get("task"),
            "side_ready": lid in _deal_side_cache,
        })
    if str(request.rel_url.query.get("tail") or "") == "1":
        lid = int(lead.get("id") or lead_id)
        if channel == "whatsapp":
            rows = _sent_messages_for_lead(lid)
            rows.sort(key=lambda item: int(item.get("created_at") or 0))
            remembered = rows[-_CHAT_TAIL_KEEP:]
            _overlay_sent_delivery(remembered, lid)
            cloud_ready = _wa_cloud_ready(sender_digits)
            return web.json_response({
                "success": True,
                "tail": True,
                "chat": remembered,
                "has_more": len(rows) > _CHAT_TAIL_KEEP,
                "chat_blocked": False,
                "channel": channel,
                "channels": [],
                "reply_talk_id": 0,
                "can_reply": cloud_ready,
                "cloud_ready": cloud_ready,
                "bot_stopped": _is_lead_bot_stopped(lid),
                "last_note": (_deal_side_cache.get(lid) or {}).get("note"),
                "last_task": (_deal_side_cache.get(lid) or {}).get("task"),
                "side_ready": lid in _deal_side_cache,
            })
        talk_id = _lead_open_talk.get(lid) or 0
        if not talk_id:
            with _deal_chat_cache_lock:
                for m in reversed(_chat_open_preview.get(lid) or []):
                    if m.get("talk_id"):
                        try:
                            talk_id = int(m["talk_id"])
                        except (TypeError, ValueError):
                            talk_id = 0
                        if talk_id:
                            break
        if not talk_id:
            with _lead_talks_cache_lock:
                cached_talks = (_lead_talks_cache.get(lid) or (0, []))[1]
            if cached_talks:
                ranked = _ranked_reply_talk_ids(cached_talks)
                if ranked:
                    talk_id = ranked[0]
        now = _time_module.monotonic()
        last_check = _last_talk_tail_check.get(lid, 0)
        if talk_id and (now - last_check >= 2.0):
            _last_talk_tail_check[lid] = now
            _lead_open_talk[lid] = talk_id
            try:
                raw_msgs, _, _ = await asyncio.get_running_loop().run_in_executor(
                    _chat_collect_executor,
                    lambda: _fetch_talk_messages(talk_id, pages=1, page_limit=10),
                )
                if raw_msgs:
                    formatted_new: list[dict] = []
                    emp_name = employee_name_for_lead(lead)
                    for rm in raw_msgs:
                        f = _format_chat_message(rm, channel)
                        if f:
                            f["channel"] = channel
                            f["talk_id"] = talk_id
                            if not f.get("incoming") and not f.get("is_bot"):
                                author = str(f.get("author") or "")
                                if channel == "whatsapp" or _is_generic_chat_author(author):
                                    f["author"] = emp_name or author
                            formatted_new.append(f)
                    if formatted_new:
                        with _deal_chat_cache_lock:
                            cur_prev = list(_chat_open_preview.get(lid) or [])
                            merged_tail = _merge_tail_rows(cur_prev, formatted_new)
                            _chat_open_preview[lid] = merged_tail[-_CHAT_TAIL_KEEP:]
                        _invalidate_deal_chat_cache(lid)
            except Exception as exc:
                logger.warning("Tail talk check failed for lead %s: %s", lid, exc)
        elif not talk_id and (now - last_check >= 4.0):
            _last_talk_tail_check[lid] = now
            try:
                discovered_talks = await asyncio.get_running_loop().run_in_executor(
                    _chat_collect_executor,
                    lambda: _fetch_talks(lid, contact_ids, include_contacts=False),
                )
                if discovered_talks:
                    ranked = _ranked_reply_talk_ids(discovered_talks)
                    if ranked:
                        talk_id = ranked[0]
                        _lead_open_talk[lid] = talk_id
            except Exception:
                pass

        remembered: list = []
        with _deal_chat_cache_lock:
            remembered = list(_chat_open_preview.get(lid) or [])[-_CHAT_TAIL_KEEP:]
            if not remembered:
                for key, cached in _deal_chat_cache.items():
                    if not key or key[0] != lid:
                        continue
                    rows = (cached[1] or {}).get("chat") if isinstance(cached[1], dict) else []
                    if rows:
                        remembered = list(rows)[-_CHAT_TAIL_KEEP:]
                        break
        _overlay_sent_delivery(remembered, lid)
        cloud_ready = channel == "whatsapp" and _wa_cloud_ready(sender_digits)
        return web.json_response({
            "success": True,
            "tail": True,
            "chat": remembered,
            "has_more": True,
            "chat_blocked": False,
            "channel": channel,
            "channels": [],
            "reply_talk_id": talk_id or 0,
            "can_reply": bool(talk_id) or cloud_ready,
            "cloud_ready": cloud_ready,
            "bot_stopped": _is_lead_bot_stopped(lid),
            "last_note": (_deal_side_cache.get(lid) or {}).get("note"),
            "last_task": (_deal_side_cache.get(lid) or {}).get("task"),
            "side_ready": lid in _deal_side_cache,
        })
    if channel == "whatsapp":
        lid = int(lead.get("id") or lead_id)
        chat, chat_blocked, reply_talk_id, has_more, channels, channel = _collect_deal_chat(
            lid,
            contact_ids,
            limit=limit,
            before=before,
            channel="whatsapp",
            sender_digits=sender_digits,
            employee_name=employee_name_for_lead(lead),
        )
        cloud_ready = _wa_cloud_ready(sender_digits)
        return web.json_response({
            "success": True,
            "chat": chat,
            "chat_blocked": False,
            "reply_talk_id": 0,
            "can_reply": cloud_ready,
            "cloud_ready": cloud_ready,
            "has_more": has_more,
            "channels": channels,
            "channel": "whatsapp",
            "bot_stopped": _is_lead_bot_stopped(lid),
            "last_note": (_deal_side_cache.get(lid) or {}).get("note"),
            "last_task": (_deal_side_cache.get(lid) or {}).get("task"),
            "side_ready": lid in _deal_side_cache,
        })
    cache_key = (int(lead.get("id") or lead_id), channel, int(before or 0), str(sender_digits or ""), int(limit))
    now = _time_module.monotonic()
    with _deal_chat_cache_lock:
        cached = _deal_chat_cache.get(cache_key)
    if cached and now - cached[0] < _DEAL_CHAT_CACHE_TTL:
        return web.json_response(cached[1])
    employee_name = employee_name_for_lead(lead)
    chat, chat_blocked, reply_talk_id, has_more, channels, channel = await asyncio.get_running_loop().run_in_executor(
        _chat_collect_executor,
        lambda: _collect_deal_chat(
            int(lead.get("id") or lead_id),
            contact_ids,
            limit=limit,
            before=before,
            channel=channel,
            sender_digits=sender_digits,
            employee_name=employee_name,
        ),
    )
    cloud_ready = channel == "whatsapp" and _wa_cloud_ready(sender_digits)
    lid = int(lead.get("id") or lead_id)
    if reply_talk_id and lid:
        _lead_open_talk[lid] = int(reply_talk_id)
    payload = {
        "success": True,
        "chat": chat,
        "chat_blocked": chat_blocked,
        "reply_talk_id": reply_talk_id,
        "has_more": has_more,
        "channel": channel,
        "channels": channels,
        "sender_phone": _wa_display_number(sender_digits),
        "can_reply": bool(reply_talk_id) or cloud_ready,
        "cloud_ready": cloud_ready,
        "bot_stopped": _is_lead_bot_stopped(int(lead.get("id") or lead_id)),
    }
    with _deal_chat_cache_lock:
        _deal_chat_cache[cache_key] = (now, payload)
        if not before and chat:
            _chat_open_preview[int(lead.get("id") or lead_id)] = _merge_tail_rows(
                list(_chat_open_preview.get(int(lead.get("id") or lead_id)) or []),
                list(chat[-_CHAT_TAIL_KEEP:]),
            )
    return web.json_response(payload)


VOICE_CAPTION_TEXT = "🎤 Səs mesajı"


def _with_visible_quote(text: str, quote: str) -> str:
    preview = " ".join(str(quote or "").split())[:120]
    if not preview:
        return str(text or "").strip()
    body = str(text or "").strip()
    wrapped = f"«{preview}»"
    if not body:
        return wrapped
    if body.startswith("«") or preview in body:
        return body
    return f"{wrapped}\n{body}"


def _with_quiet_quote(text: str, quote: str) -> str:
    preview = " ".join(str(quote or "").split())[:120]
    body = str(text or "").strip()
    if not preview:
        return body
    if body.startswith(">") or preview in body:
        return body
    if not body:
        return f"> {preview}"
    return f"> {preview}\n{body}"


def _looks_voice_upload(filename: str, content_type: str) -> bool:
    return str(content_type or "").startswith("audio/") or bool(
        re.search(r"\.(ogg|oga|opus|mp3|m4a|wav|webm)$", str(filename or "").lower())
    )


def _attachment_kind(filename: str, content_type: str) -> str:
    # send_message allows file/video/picture only, so audio travels as a file.
    name = f"{filename} {content_type}".lower()
    if content_type.startswith("audio/") or re.search(r"\.(ogg|oga|opus|mp3|m4a|wav)$", name):
        return "file"
    if content_type.startswith("image/") or re.search(r"\.(png|jpe?g|gif|webp)$", name):
        return "picture"
    if content_type.startswith("video/") or re.search(r"\.(mp4|mov|webm)$", name):
        return "video"
    return "file"


_CHAT_REPLY_MEMORY: dict[str, dict] = {}


def _remember_chat_reply(message_id, reply_id: str, reply_text: str, reply_author: str) -> None:
    mid = str(message_id or "").strip()
    preview = str(reply_text or "").strip()[:200]
    if not mid or not (preview or reply_id):
        return
    if len(_CHAT_REPLY_MEMORY) > 2000:
        _CHAT_REPLY_MEMORY.pop(next(iter(_CHAT_REPLY_MEMORY)), None)
    _CHAT_REPLY_MEMORY[mid] = {
        "reply_to_message_id": str(reply_id or "").strip(),
        "reply_to_text": preview,
        "reply_to_author": str(reply_author or "").strip()[:80],
    }


def _apply_saved_replies(chat: list[dict]) -> list[dict]:
    if not _CHAT_REPLY_MEMORY:
        return chat
    by_id = {str(item.get("id") or ""): item for item in chat if item.get("id")}
    for item in chat:
        saved = _CHAT_REPLY_MEMORY.get(str(item.get("id") or ""))
        if saved:
            if not item.get("reply_to_text"):
                item["reply_to_text"] = saved.get("reply_to_text") or ""
            if not item.get("reply_to_author"):
                item["reply_to_author"] = saved.get("reply_to_author") or ""
            if not item.get("reply_to_message_id"):
                item["reply_to_message_id"] = saved.get("reply_to_message_id") or ""
        quoted_id = str(item.get("reply_to_message_id") or "").strip()
        source = by_id.get(quoted_id) if quoted_id else None
        if source and not item.get("reply_to_text"):
            item["reply_to_text"] = str(source.get("text") or "").strip()[:200]
            item["reply_to_author"] = str(source.get("author") or item.get("reply_to_author") or "").strip()[:80]
    return chat


def _stamp_sent_reply(chat: list[dict], text: str, reply_id: str, reply_text: str, reply_author: str) -> None:
    preview = str(reply_text or "").strip()[:200]
    if not preview and not reply_id:
        return
    wanted = str(text or "").strip()
    fallback = None
    for item in reversed(chat):
        if item.get("incoming") or item.get("is_comment"):
            continue
        if fallback is None:
            fallback = item
        if wanted and str(item.get("text") or "").strip() != wanted:
            continue
        fallback = item
        break
    if not fallback:
        return
    fallback["reply_to_message_id"] = reply_id or fallback.get("reply_to_message_id") or ""
    fallback["reply_to_text"] = preview or fallback.get("reply_to_text") or ""
    fallback["reply_to_author"] = reply_author or fallback.get("reply_to_author") or ""
    _remember_chat_reply(fallback.get("id"), fallback["reply_to_message_id"], fallback["reply_to_text"], fallback["reply_to_author"])


def _deliver_via_cloud(
    lead: dict,
    text: str,
    raw: bytes,
    filename: str,
    content_type: str,
    quote_id: str,
    sender_digits: str = "",
) -> tuple[bool, str, str, str, str]:
    """Send one message through WhatsApp Cloud API; returns ok, error, wamid, type, media_id."""
    phone_id = _wa_phone_id_for_digits(sender_digits)
    _wa_send_phone.phone_id = phone_id
    _ids, phones = _contact_ids_and_phones(lead)
    if not phones:
        _wa_send_phone.phone_id = ""
        return False, "Müştəri nömrəsi tapılmadı.", "", "text", ""
    kind = "text"
    media_id = ""
    voice = False
    if raw:
        kind = _wa_cloud_media_kind(filename, content_type)
        payload, mime, name = raw, content_type or "application/octet-stream", filename or "file"
        if kind == "audio":
            converted, conv_mime, conv_name, voice = _ffmpeg_voice_for_cloud(raw, filename)
            if not converted:
                _wa_send_phone.phone_id = ""
                return False, "Səs WhatsApp formatına çevrilmədi.", "", "audio", ""
            payload, mime, name = converted, conv_mime, conv_name
        media_id, upload_error = _wa_cloud_upload_media(payload, name, mime, phone_id)
        if not media_id:
            _wa_send_phone.phone_id = ""
            return False, upload_error or "Fayl WhatsApp-a yüklənmədi.", "", kind, ""
    last_error = ""
    for phone in phones:
        if kind == "text":
            ok, error, wamid = _wa_cloud_send_text(phone, text, quote_id, phone_id)
        else:
            ok, error, wamid = _wa_cloud_send_media(
                phone,
                kind,
                media_id,
                caption="" if voice else text,
                filename="" if voice else filename,
                reply_to=quote_id,
                voice=voice,
                phone_id=phone_id,
            )
            if ok and voice and text:
                _wa_cloud_send_text(phone, text)
        if ok:
            _wa_send_phone.phone_id = ""
            return True, "", wamid, kind, media_id
        if error:
            last_error = error
    _wa_send_phone.phone_id = ""
    return False, last_error or "WhatsApp mesajı göndərilmədi.", "", kind, media_id


async def handle_api_whatsapp_templates(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    if not is_funnel_chat(chat_id) and not is_admin(chat_id):
        return web.json_response({"success": False, "error": "Access denied"}, status=403)
    if not _wa_cloud_configured():
        return web.json_response({
            "success": True,
            "templates": [],
            "error": "WhatsApp Cloud API konfiqurasiya olunmayıb.",
        })
    force = str(request.rel_url.query.get("refresh") or "") == "1"
    cached, fresh = _wa_templates_cached()
    if cached and not force:
        if not fresh:
            asyncio.create_task(asyncio.to_thread(_wa_template_rows, True))
        return web.json_response({"success": True, "templates": cached})
    rows = await asyncio.to_thread(_wa_template_rows, force)
    return web.json_response({"success": True, "templates": rows})


async def handle_api_deal_chat_template(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    if not is_funnel_chat(chat_id) and not is_admin(chat_id):
        return web.json_response({"success": False, "error": "Access denied"}, status=403)
    if not _wa_cloud_configured():
        return web.json_response({"success": False, "error": "WhatsApp Cloud API konfiqurasiya olunmayıb."}, status=400)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    name = str(data.get("template") or "").strip()
    if not name:
        return web.json_response({"success": False, "error": "Şablon seçilməyib."}, status=400)
    language = str(data.get("language") or "az").strip() or "az"
    spec = await asyncio.to_thread(_wa_template_find, name, language)
    if not spec:
        return web.json_response({"success": False, "error": "Şablon tapılmadı."}, status=400)
    body_params = [str(value).strip() for value in (data.get("params") or [])]
    header_params = [str(value).strip() for value in (data.get("header_params") or [])]
    header_media = str(data.get("header_media") or "").strip()
    button_values: dict[int, str] = {}
    for item in data.get("buttons") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        button_values[index] = str(item.get("value") or "").strip()
    card_values: list[dict] = []
    for item in data.get("cards") or []:
        if not isinstance(item, dict):
            continue
        nested: dict[int, str] = {}
        for button in item.get("buttons") or []:
            if not isinstance(button, dict):
                continue
            try:
                button_index = int(button.get("index"))
            except (TypeError, ValueError):
                continue
            nested[button_index] = str(button.get("value") or "").strip()
        card_values.append({
            "header_media": str(item.get("header_media") or "").strip(),
            "body_params": [str(value).strip() for value in (item.get("params") or [])],
            "buttons": nested,
        })
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0
    phone = str(data.get("phone") or "").strip()
    lead = None
    if lead_id:
        lead, err = _authorized_deal_lead(chat_id, lead_id)
        if err:
            return err
        if not phone:
            _ids, phones = _contact_ids_and_phones(lead)
            phone = phones[0] if phones else ""
    elif phone:
        cached_lid = _lead_id_for_stored_phone(phone) or _lead_id_from_overview_phone(phone)
        if cached_lid:
            lead_id = cached_lid
            lead = get_lead_details(cached_lid)

    sender_digits = _hinted_wa_sender_digits(chat_id, data.get("sender_phone"))
    sender_phone_id = _wa_phone_id_for_digits(sender_digits) or _wa_phone_id_for_digits(_wa_sender_digits_for_chat(chat_id))

    if not _normalize_wa_number(phone):
        return web.json_response({"success": False, "error": "Nömrə tapılmadı."}, status=400)
    ok, error, wamid = await asyncio.to_thread(
        _wa_cloud_send_template,
        phone,
        spec,
        {
            "body_params": body_params,
            "header_params": header_params,
            "header_media": header_media,
            "buttons": button_values,
            "cards": card_values,
        },
        sender_phone_id,
    )
    if not ok:
        return web.json_response({"success": False, "error": error or "Şablon göndərilmədi."}, status=400)
    preview = str(data.get("preview") or "").strip() or name
    preview_cards = _wa_preview_cards(data.get("preview_cards"))
    lid = int((lead.get("id") if lead else 0) or lead_id)
    author = employee_name_for_lead(lead) if lead else ""
    if not author:
        for _nm, _cid in NAME_TO_CHAT.items():
            if _cid == chat_id and len(_nm) > 2:
                author = _nm
                break
    if not author:
        author = "Menecer"
    sent_item = _sent_message_item(wamid=wamid, text=preview, author=author, cards=preview_cards)
    tail = {
        "id": f"sent-{wamid or uuid.uuid4().hex}",
        "external_id": str(wamid or ""),
        "incoming": False,
        "direction": "outgoing",
        "text": preview,
        "created_at": int(_time_module.time()),
        "message_type": "carousel" if preview_cards else "text",
        "channel": "whatsapp",
        "author": author,
        "delivery_status": "sent",
    }
    if preview_cards:
        tail["cards"] = preview_cards
    if lid:
        _remember_sent_message(lid, sent_item)
        _append_chat_tail(lid, tail)
    elif phone:
        def _bg_link_template_lead():
            try:
                bg_lid = _resolve_cloud_lead(phone, "", sender_phone_id=sender_phone_id)
                if bg_lid:
                    _remember_phone_lead(phone, bg_lid)
                    _remember_sent_message(bg_lid, sent_item)
                    _append_chat_tail(bg_lid, tail)
                    _patch_cloud_inbox_into_rufat_cache()
            except Exception as e:
                logger.warning("Background template lead creation failed: %s", e)
        asyncio.create_task(asyncio.to_thread(_bg_link_template_lead))
    return web.json_response({
        "success": True,
        "lead_id": lid,
        "wamid": wamid,
        "phone": _wa_display_number(_normalize_wa_number(phone)),
    })


async def handle_api_deal_chat_send(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    text = ""
    channel = "whatsapp"
    lead_id = 0
    attachment = None
    upload_raw = b""
    upload_name = ""
    upload_type = ""
    reply_to_message_id = ""
    reply_external = ""
    reply_preview = ""
    reply_author = ""
    hinted_talk = 0
    hinted_sender = ""
    form_is_voice = False
    ctype = str(request.content_type or "")
    if ctype.startswith("multipart/"):
        form = await request.post()
        try:
            lead_id = int(form.get("lead_id") or 0)
        except (TypeError, ValueError):
            lead_id = 0
        text = str(form.get("text") or "").strip()
        channel = str(form.get("channel") or "whatsapp").strip().lower()
        form_is_voice = str(form.get("is_voice") or "").strip().lower() in {"1", "true", "yes"}
        reply_to_message_id = str(form.get("reply_to_message_id") or "").strip()
        reply_external = str(form.get("reply_external_id") or "").strip()
        reply_preview = str(form.get("reply_to_text") or "").strip()[:200]
        reply_author = str(form.get("reply_to_author") or "").strip()[:80]
        hinted_sender = str(form.get("sender_phone") or "")
        try:
            hinted_talk = int(form.get("talk_id") or 0)
        except (TypeError, ValueError):
            hinted_talk = 0
        uploaded = form.get("file")
        if uploaded is not None and getattr(uploaded, "file", None):
            upload_raw = uploaded.file.read()
            upload_name = str(getattr(uploaded, "filename", None) or "file")
            upload_type = str(getattr(uploaded, "content_type", None) or "application/octet-stream")
            if len(upload_raw) > 15 * 1024 * 1024:
                return web.json_response({"success": False, "error": "Fayl 15MB-dan böyükdür"}, status=400)
    else:
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        try:
            lead_id = int(data.get("lead_id") or 0)
        except (TypeError, ValueError):
            lead_id = 0
        text = str(data.get("text") or "").strip()
        channel = str(data.get("channel") or "whatsapp").strip().lower()
        form_is_voice = str(data.get("is_voice") or "").strip().lower() in {"1", "true", "yes"}
        reply_to_message_id = str(data.get("reply_to_message_id") or "").strip()
        reply_external = str(data.get("reply_external_id") or "").strip()
        reply_preview = str(data.get("reply_to_text") or "").strip()[:200]
        reply_author = str(data.get("reply_to_author") or "").strip()[:80]
        hinted_sender = str(data.get("sender_phone") or "")
        try:
            hinted_talk = int(data.get("talk_id") or 0)
        except (TypeError, ValueError):
            hinted_talk = 0
    if not lead_id:
        return web.json_response({"success": False, "error": "lead_id required"}, status=400)
    if not text and not upload_raw:
        return web.json_response({"success": False, "error": "Mesaj boş ola bilməz"}, status=400)
    if len(text) > 2000:
        return web.json_response({"success": False, "error": "Mesaj çox uzundur"}, status=400)
    lead, err = _authorized_deal_lead(chat_id, lead_id)
    if err:
        return err
    sender_digits = _hinted_wa_sender_digits(chat_id, hinted_sender)
    use_cloud = channel == "whatsapp" and _wa_cloud_ready(sender_digits)
    if use_cloud:
        reply_talk_id, reply_chat_id = 0, ""
    else:
        reply_talk_id, reply_chat_id = _resolve_channel_talk(lead, channel, sender_digits, hinted_talk)
    if not reply_talk_id and not use_cloud:
        return web.json_response({"success": False, "error": f"{CHAT_CHANNEL_LABELS.get(channel, channel)} çatı tapılmadı."}, status=400)
    wa_quote_id = _first_wamid(reply_external, reply_to_message_id)
    want_quote = bool(reply_to_message_id or reply_external or reply_preview)
    drive_uuid = ""
    drive_version = ""
    attachment = None
    is_voice = False
    if upload_raw:
        is_voice = form_is_voice or _looks_voice_upload(upload_name, upload_type)
        if not use_cloud:
            if is_voice:
                converted, conv_mime, conv_name = _ffmpeg_voice_for_kommo(upload_raw, upload_name)
                if converted:
                    upload_raw = converted
                    upload_type = conv_mime
                    upload_name = conv_name
            # Drive keeps a durable copy so the file stays playable inside the app.
            drive_uuid, drive_version = _upload_kommo_drive_bytes(upload_name, upload_raw, upload_type)
            if not drive_uuid or not drive_version:
                return web.json_response({"success": False, "error": "Fayl yüklənmədi"}, status=400)
            attachment = {
                "type": "voice" if is_voice else _attachment_kind(upload_name, upload_type),
                "drive_uuid": drive_uuid,
                "drive_version_uuid": drive_version,
                "is_voice": is_voice,
            }
    ok = False
    last_error = ""
    sent_wamid = ""
    sent_via_cloud = False
    sent_text = text
    sent_type = "text"
    sent_media_id = ""
    cloud_quote = wa_quote_id if str(wa_quote_id or "").lower().startswith("wamid") else ""
    if use_cloud:
        ok, last_error, sent_wamid, sent_type, sent_media_id = await asyncio.to_thread(
            _deliver_via_cloud,
            lead,
            text,
            upload_raw,
            upload_name,
            upload_type,
            cloud_quote,
            sender_digits,
        )
        sent_via_cloud = ok
        if not ok and cloud_quote and "24 saat" not in str(last_error or ""):
            ok, last_error, sent_wamid, sent_type, sent_media_id = await asyncio.to_thread(
                _deliver_via_cloud,
                lead,
                text,
                upload_raw,
                upload_name,
                upload_type,
                "",
                sender_digits,
            )
            sent_via_cloud = ok
    if not ok and reply_talk_id and channel != "whatsapp":
        kommo_text = text
        if upload_raw and not kommo_text:
            if is_voice:
                kommo_text = ""
            else:
                kommo_text = upload_name or "Fayl"
        quote_ids = _kommo_quote_ids(reply_external, reply_to_message_id)
        social_quote = channel in {"tiktok", "telegram", "instagram", "facebook"}
        if channel == "whatsapp":
            if is_voice:
                # In WhatsApp, audio with any text caption is sent as a file/document card.
                # Sending with empty text allows Kommo WhatsApp Lite to deliver it as a native PTT voice note.
                kommo_ok, kommo_error, _status = _send_kommo_talk_message(
                    reply_talk_id,
                    "",
                    attachment,
                )
                if kommo_ok:
                    ok = True
                    last_error = ""
                    sent_text = VOICE_CAPTION_TEXT
                    if text:
                        _send_kommo_talk_message(reply_talk_id, text)
                elif kommo_error:
                    last_error = kommo_error
            else:
                if reply_preview:
                    kommo_text = _with_quiet_quote(kommo_text, reply_preview)
                kommo_ok, kommo_error, _status = _send_kommo_talk_message(
                    reply_talk_id,
                    kommo_text,
                    attachment,
                )
                if kommo_ok:
                    ok = True
                    last_error = ""
                    sent_text = kommo_text
                elif kommo_error:
                    last_error = kommo_error
        else:
            for quote_id in quote_ids[:2]:
                kommo_ok, kommo_error, _status = _send_kommo_talk_message(
                    reply_talk_id,
                    kommo_text,
                    attachment,
                    reply_to=quote_id,
                    chat_id=reply_chat_id,
                    reply_text=reply_preview,
                    plain_fallback=False,
                )
                if kommo_ok:
                    ok = True
                    last_error = ""
                    sent_text = kommo_text
                    break
                if kommo_error and not last_error:
                    last_error = kommo_error
            if not ok:
                fallback_text = kommo_text
                if social_quote and reply_preview:
                    fallback_text = _with_visible_quote(kommo_text, reply_preview)
                kommo_ok, kommo_error, _status = _send_kommo_talk_message(
                    reply_talk_id,
                    fallback_text,
                    attachment,
                )
                if kommo_ok:
                    ok = True
                    last_error = ""
                    sent_text = fallback_text
                elif kommo_error and not last_error:
                    last_error = kommo_error
    if not ok:
        detail = last_error
        if not last_error or last_error.startswith("{") or "validation" in last_error.lower():
            last_error = "Fayl göndərilmədi." if upload_raw else "Mesaj göndərilmədi."
        return web.json_response({"success": False, "error": last_error, "detail": detail}, status=400)
    tail_text = text or _clean_body_text(sent_text) or (VOICE_CAPTION_TEXT if upload_raw and is_voice else upload_name or text)
    cloud_media_url = f"/api/wa/media/{sent_media_id}" if sent_media_id else ""
    if sent_via_cloud:
        # The app renders Kommo message types, so translate the Cloud API kind.
        local_type = {"image": "picture", "document": "file", "audio": "audio"}.get(sent_type, sent_type)
        _remember_sent_message(int(lead.get("id") or lead_id), _sent_message_item(
            wamid=sent_wamid,
            text=tail_text,
            author=employee_name_for_lead(lead),
            message_type=local_type,
            file_uuid=drive_uuid,
            media_url=cloud_media_url,
            file_name="" if local_type == "audio" else upload_name,
            reply_id=reply_to_message_id or wa_quote_id,
            reply_text=reply_preview,
            reply_author=reply_author,
        ))
    _append_chat_tail(int(lead.get("id") or lead_id), {
        "id": f"sent-{sent_wamid or uuid.uuid4().hex}",
        "external_id": str(sent_wamid or ""),
        "incoming": False,
        "direction": "outgoing",
        "text": tail_text,
        "created_at": int(_time_module.time()),
        "message_type": local_type if sent_via_cloud else ("audio" if upload_raw and is_voice else "text"),
        "channel": channel,
        "author": employee_name_for_lead(lead),
        "delivery_status": "sent",
        "file_name": "" if upload_raw and is_voice else upload_name,
        "file_uuid": drive_uuid,
        "media_url": cloud_media_url,
        "reply_to_message_id": reply_to_message_id or wa_quote_id,
        "reply_to_text": reply_preview,
        "reply_to_author": reply_author,
    })
    contact_ids = _lead_contact_ids(lead)
    chat, chat_blocked, reply_talk_id, has_more, channels, channel = _collect_deal_chat(
        int(lead.get("id") or lead_id),
        contact_ids,
        limit=20,
        channel=channel,
        sender_digits=sender_digits,
        employee_name=employee_name_for_lead(lead),
    )
    _apply_saved_replies(chat)
    _store_chat_tail(int(lead.get("id") or lead_id), chat)
    if reply_to_message_id:
        _stamp_sent_reply(chat, text, reply_to_message_id, reply_preview, reply_author)
    lid_int = int(lead.get("id") or lead_id)
    _invalidate_deal_chat_cache(lid_int)
    record_lead_pulse_event(lid_int, "deal_outgoing", preview=tail_text, incoming_at=0)
    return web.json_response({
        "success": True,
        "chat": chat,
        "chat_blocked": chat_blocked,
        "reply_talk_id": reply_talk_id,
        "has_more": has_more,
        "channel": channel,
        "channels": channels,
        "delivery_status": "sent",
        "media_url": cloud_media_url,
    })


def _send_gray_emoji_reply(chat_id: int, lead: dict, data: dict, emoji: str) -> tuple[bool, str]:
    """Gray WhatsApp cannot react; send the emoji as a quiet quoted reply."""
    channel = str(data.get("channel") or "whatsapp").strip().lower() or "whatsapp"
    if channel != "whatsapp":
        return False, ""
    try:
        hinted_talk = int(data.get("talk_id") or 0)
    except (TypeError, ValueError):
        hinted_talk = 0
    sender_digits = _hinted_wa_sender_digits(chat_id, data.get("sender_phone"))
    reply_talk_id, _chat = _resolve_channel_talk(lead, channel, sender_digits, hinted_talk)
    if not reply_talk_id:
        return False, ""
    preview = str(data.get("reply_to_text") or "").strip()
    text = _with_quiet_quote(str(emoji or "").strip(), preview)
    if not text:
        return False, ""
    ok, error, _status = _send_kommo_talk_message(reply_talk_id, text)
    return bool(ok), str(error or "")


async def handle_api_deal_chat_react(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0
    wamid = str(data.get("wamid") or data.get("external_id") or "").strip()
    message_id = str(data.get("message_id") or "").strip()
    react_id = wamid or message_id
    emoji = str(data.get("emoji") or "").strip()[:8]
    if not lead_id or not react_id:
        return web.json_response({"success": False, "error": "lead_id və mesaj id lazımdır"}, status=400)
    lead, err = _authorized_deal_lead(chat_id, lead_id)
    if err:
        return err
    lid = int(lead.get("id") or lead_id)
    resolved = _wamid_for_react(lid, wamid, message_id, react_id)
    token, _env_phone = _wa_cloud_credentials()
    last_error = ""
    sender_phone_id = _wa_phone_id_for_digits(_hinted_wa_sender_digits(chat_id, data.get("sender_phone")))
    if token and sender_phone_id and resolved.lower().startswith("wamid"):
        _ids, phones = _contact_ids_and_phones(lead)
        if not phones:
            return web.json_response({"success": False, "error": "Müştəri nömrəsi tapılmadı."}, status=400)
        for phone in phones:
            ok, error = await asyncio.to_thread(_wa_cloud_send_reaction, phone, resolved, emoji, sender_phone_id)
            if ok:
                _remember_reaction(lid, resolved, emoji)
                if message_id and message_id != resolved:
                    _remember_reaction(lid, message_id, emoji)
                if react_id and react_id != resolved:
                    _remember_reaction(lid, react_id, emoji)
                return web.json_response({"success": True, "emoji": emoji})
            if error:
                last_error = error
        _remember_reaction(lid, resolved, emoji)
        if message_id and message_id != resolved:
            _remember_reaction(lid, message_id, emoji)
        return web.json_response({"success": True, "emoji": emoji, "local": True, "warning": last_error})
    _remember_reaction(lid, react_id, emoji)
    if message_id and message_id != react_id:
        _remember_reaction(lid, message_id, emoji)
    return web.json_response({"success": True, "emoji": emoji, "local": True, "warning": last_error})


async def handle_api_deal_chat_read(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0
    wamid = str(data.get("external_id") or data.get("wamid") or "").strip()
    typing = bool(data.get("typing"))
    if not lead_id:
        return web.json_response({"success": True, "skipped": True})
    _record_user_seen(chat_id, lead_id)
    if not wamid:
        return web.json_response({"success": True, "seen_recorded": True})
    _lead, err = _authorized_deal_lead(chat_id, lead_id)
    if err:
        return err
    if not _wa_cloud_ready(_hinted_wa_sender_digits(chat_id, data.get("sender_phone"))):
        return web.json_response({"success": True, "skipped": True})
    ok = await asyncio.to_thread(
        _wa_cloud_mark_read,
        wamid,
        typing,
        _wa_phone_id_for_digits(_hinted_wa_sender_digits(chat_id, data.get("sender_phone"))),
    )
    return web.json_response({"success": bool(ok)})


async def handle_api_deal_chat_seen(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0
    if lead_id:
        _record_user_seen(chat_id, lead_id)
        return web.json_response({"success": True, "lead_id": lead_id})
    return web.json_response({"success": False, "error": "lead_id lazımdır"}, status=400)


_LEAD_BOT_FILE = "lead_bot_state.json"
_lead_bot_lock = threading.Lock()


def _is_lead_bot_stopped(lead_id: int) -> bool:
    try:
        data = read_json(_LEAD_BOT_FILE) or {}
        return bool(data.get(str(int(lead_id))))
    except Exception:
        return False


def _set_lead_bot_stopped(lead_id: int, stopped: bool) -> None:
    try:
        with _lead_bot_lock:
            data = read_json(_LEAD_BOT_FILE) or {}
            if not isinstance(data, dict):
                data = {}
            if stopped:
                data[str(int(lead_id))] = True
            else:
                data.pop(str(int(lead_id)), None)
            write_json(_LEAD_BOT_FILE, data)
    except Exception:
        pass


def _kommo_run_salesbot(lead_id: int, bot_id: int = 60151) -> tuple[bool, str]:
    url = f"{KOMMO_BASE_URL}/api/v4/bots/{int(bot_id)}/run"
    payload = {"entity_id": int(lead_id), "entity_type": "leads"}
    try:
        resp = _http.post(url, headers=HEADERS, json=payload, timeout=12)
        if resp.status_code in (200, 202, 204):
            _set_lead_bot_stopped(lead_id, False)
            return True, ""
        return False, f"Kommo status {resp.status_code}: {resp.text[:120]}"
    except Exception as exc:
        return False, str(exc)


def _kommo_stop_salesbot(lead_id: int, bot_id: int = 60151) -> tuple[bool, str]:
    url = f"{KOMMO_BASE_URL}/api/v4/bots/{int(bot_id)}/stop"
    payload = {"entity_id": int(lead_id), "entity_type": "leads"}
    try:
        resp = _http.post(url, headers=HEADERS, json=payload, timeout=12)
        if resp.status_code in (200, 202, 204):
            _set_lead_bot_stopped(lead_id, True)
            return True, ""
        return False, f"Kommo status {resp.status_code}: {resp.text[:120]}"
    except Exception as exc:
        return False, str(exc)


async def handle_api_deal_bot_run(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0
    if not lead_id:
        return web.json_response({"success": False, "error": "lead_id lazımdır"}, status=400)
    lead, err = _authorized_deal_lead(chat_id, lead_id)
    if err:
        return err
    bot_id = int(data.get("bot_id") or 60151)
    ok, err_msg = await asyncio.to_thread(_kommo_run_salesbot, lead_id, bot_id)
    return web.json_response({"success": ok, "bot_stopped": False, "error": err_msg if not ok else None})


async def handle_api_deal_bot_stop(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0
    if not lead_id:
        return web.json_response({"success": False, "error": "lead_id lazımdır"}, status=400)
    lead, err = _authorized_deal_lead(chat_id, lead_id)
    if err:
        return err
    bot_id = int(data.get("bot_id") or 60151)
    ok, err_msg = await asyncio.to_thread(_kommo_stop_salesbot, lead_id, bot_id)
    return web.json_response({"success": ok, "bot_stopped": True, "error": err_msg if not ok else None})


_CHAT_PINS_FILE = "chat_pins.json"
_chat_pins_lock = threading.Lock()


def _user_chat_pins(chat_id: int) -> dict:
    data = read_json(_CHAT_PINS_FILE) or {}
    row = data.get(str(chat_id)) if isinstance(data, dict) else {}
    return row if isinstance(row, dict) else {}


def _set_user_chat_pin(chat_id: int, lead_id: int, pin: dict | None) -> dict:
    with _chat_pins_lock:
        data = read_json(_CHAT_PINS_FILE) or {}
        if not isinstance(data, dict):
            data = {}
        user = data.get(str(chat_id)) or {}
        if not isinstance(user, dict):
            user = {}
        key = str(int(lead_id))
        if pin and pin.get("id"):
            user[key] = {
                "id": str(pin.get("id") or ""),
                "text": str(pin.get("text") or "")[:180],
                "author": str(pin.get("author") or "")[:80],
            }
        else:
            user.pop(key, None)
        data[str(chat_id)] = user
        write_json(_CHAT_PINS_FILE, data)
        return user


async def handle_api_deal_chat_pin(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    if request.method == "GET":
        return web.json_response({"success": True, "pins": _user_chat_pins(chat_id)})
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0
    if not lead_id:
        return web.json_response({"success": False, "error": "lead_id required"}, status=400)
    lead, err = _authorized_deal_lead(chat_id, lead_id)
    if err:
        return err
    pin = None
    if data.get("id"):
        pin = {"id": data.get("id"), "text": data.get("text") or "", "author": data.get("author") or ""}
    pins = await asyncio.to_thread(_set_user_chat_pin, chat_id, lead_id, pin)
    return web.json_response({"success": True, "pins": pins})


KOMMO_AI_AGENT_NAME = "Anar Vəliyev"


def _fold_az_name(value: str) -> str:
    table = str.maketrans("əıöüçşğƏİÖÜÇŞĞ", "eioucsgEIOUCSG")
    return " ".join(str(value or "").translate(table).casefold().split())


def _kommo_name_matches(left: str, right: str) -> bool:
    a = _fold_az_name(left)
    b = _fold_az_name(right)
    return bool(a and b and (a == b or a in b or b in a))


def _list_kommo_users() -> list[dict]:
    rows: list[dict] = []
    page = 1
    while page <= 5:
        try:
            resp = _http.get(
                f"{KOMMO_BASE_URL}/api/v4/users",
                headers=HEADERS,
                params={"limit": 250, "page": page},
                timeout=12,
            )
        except Exception as exc:
            logger.warning("Kommo users failed: %s", exc)
            break
        if resp.status_code != 200:
            break
        batch = (resp.json().get("_embedded") or {}).get("users") or []
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 250:
            break
        page += 1
    return rows


def _list_kommo_bots() -> list[dict]:
    rows: list[dict] = []
    for url in (f"{KOMMO_BASE_URL}/api/v4/bots", f"{KOMMO_BASE_URL}/ajax/v4/bots"):
        try:
            resp = _http.get(url, headers=HEADERS, params={"limit": 250}, timeout=12)
        except Exception as exc:
            logger.warning("Kommo bots failed: %s", exc)
            continue
        if resp.status_code != 200:
            continue
        try:
            payload = resp.json()
        except Exception:
            continue
        batch = (payload.get("_embedded") or {}).get("bots") or payload.get("bots") or []
        if isinstance(batch, list):
            rows.extend(item for item in batch if isinstance(item, dict))
        if rows:
            break
    return rows


def _find_named_kommo_user(name: str) -> dict | None:
    wanted = str(name or "").strip()
    if not wanted:
        return None
    for user in _list_kommo_users():
        label = " ".join(str(part or "") for part in (user.get("name"), user.get("full_name"), user.get("first_name"), user.get("last_name")))
        if _kommo_name_matches(wanted, label):
            return user
    return None


def _find_named_kommo_bot(name: str) -> dict | None:
    wanted = str(name or "").strip()
    if not wanted:
        return None
    for bot in _list_kommo_bots():
        label = str(bot.get("name") or bot.get("title") or "")
        if _kommo_name_matches(wanted, label):
            return bot
    return None


def _handoff_summary_to_anar(lead_id: int, summary: str) -> dict:
    note_text = f"AI kontekst — {KOMMO_AI_AGENT_NAME}\n\n{summary}".strip()
    note_ok = bool(add_note(int(lead_id), note_text, "leads"))
    user = _find_named_kommo_user(KOMMO_AI_AGENT_NAME)
    bot = None if user else _find_named_kommo_bot(KOMMO_AI_AGENT_NAME)
    handed = False
    if user and user.get("id"):
        try:
            uid = int(user.get("id"))
        except (TypeError, ValueError):
            uid = 0
        if uid:
            till = int(_time_module.time()) + 3600
            handed = bool(create_task(int(lead_id), note_text[:500], till, uid, "leads", 1, KOMMO_AI_AGENT_NAME))
    if bot and bot.get("id") and not handed:
        try:
            bid = int(bot.get("id"))
        except (TypeError, ValueError):
            bid = 0
        if bid:
            try:
                resp = _http.post(
                    f"{KOMMO_BASE_URL}/api/v4/bots/{bid}/run",
                    headers=HEADERS,
                    json={"entity_id": int(lead_id), "entity_type": "leads"},
                    timeout=12,
                )
                handed = resp.status_code in {200, 202}
            except Exception as exc:
                logger.warning("AI bot run failed: %s", exc)
    if note_ok:
        invalidate_rufat_overview_cache()
    return {
        "note_ok": note_ok,
        "handed": handed,
        "agent": (user or bot or {}).get("name") or KOMMO_AI_AGENT_NAME,
    }


def _fallback_chat_summary(history: str) -> str:
    lines = [row.strip() for row in str(history or "").splitlines() if row.strip()]
    tail = lines[-8:] if lines else []
    if not tail:
        return "Yazışma yoxdur."
    return "Son dialoq:\n" + "\n".join(tail)


async def handle_api_deal_chat_suggest(request: web.Request) -> web.Response:
    chat_id = _deal_request_user(request)
    if not chat_id:
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        lead_id = int(data.get("lead_id") or 0)
    except (TypeError, ValueError):
        lead_id = 0
    if not lead_id:
        return web.json_response({"success": False, "error": "lead_id required"}, status=400)
    lead, err = _authorized_deal_lead(chat_id, lead_id)
    if err:
        return err
    contacts = (lead.get("_embedded") or {}).get("contacts") or []
    contact_name = ""
    if contacts and isinstance(contacts[0], dict):
        contact_name = str(contacts[0].get("name") or "")
    history_rows = data.get("messages") if isinstance(data.get("messages"), list) else []
    if not history_rows:
        try:
            chat, *_rest = _collect_deal_chat(
                int(lead.get("id") or lead_id),
                _lead_contact_ids(lead),
                limit=30,
                channel=str(data.get("channel") or "whatsapp"),
                sender_digits=_hinted_wa_sender_digits(chat_id, str(data.get("sender_phone") or "")),
                employee_name=employee_name_for_lead(lead),
            )
            history_rows = chat
        except Exception as exc:
            logger.warning("AI history load failed: %s", exc)
            history_rows = []
    lines = []
    for item in history_rows[-30:]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        who = "Müştəri" if item.get("incoming") else "Menecer"
        lines.append(f"{who}: {text[:400]}")
    history = "\n".join(lines) or "Yazışma yoxdur."
    draft = str(data.get("draft") or "").strip()
    mode = str(data.get("mode") or "reply").strip().lower()
    if mode not in {"reply", "summary"}:
        mode = "reply"
    if mode == "reply":
        system = (
            "Sən Bein Systems satış menecerisən. Azərbaycan dilində qısa, təbii WhatsApp cavabı yaz. "
            "Məqsəd: söhbəti irəli aparmaq, etirazı yumşaq bağlamaq, növbəti addımı təklif etmək. "
            "Yalnız göndəriləcək mesajın mətnini qaytar. Dırnaq, başlıq və izah yazma."
        )
        user = (
            f"Müştəri: {contact_name or lead.get('name') or '—'}\n"
            f"Sövdələşmə: {lead.get('name') or '—'}\n"
            f"Son yazışma:\n{history}\n"
        )
        if draft:
            user += f"\nMenecerin qeydi: {draft}\n"
        user += "\nNövbəti cavabı yaz."

        def _ask_reply() -> str:
            resp = llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.45,
                max_tokens=350,
            )
            return str((resp.choices[0].message.content if resp.choices else "") or "").strip()

        try:
            suggestion = await asyncio.to_thread(_ask_reply)
        except Exception as exc:
            logger.error("Deal AI reply failed: %s", exc)
            return web.json_response({"success": False, "error": "AI cavab alınmadı."}, status=502)
        if not suggestion:
            return web.json_response({"success": False, "error": "AI boş cavab verdi."}, status=502)
        return web.json_response({"success": True, "mode": "reply", "text": suggestion})

    system = (
        "Sən CRM köməkçisisən. Dialoqu Azərbaycan dilində qısa xülasə et: məqsəd, razılaşma, "
        "açıq suallar və növbəti addım. 5-8 cümlə. Yalnız xülasəni yaz."
    )
    user = (
        f"Müştəri: {contact_name or lead.get('name') or '—'}\n"
        f"Sövdələşmə: {lead.get('name') or '—'}\n"
        f"Son yazışma:\n{history}\n"
    )
    if draft:
        user += f"\nMenecerin qeydi: {draft}\n"
    user += "\nXülasəni yaz."

    def _ask_summary() -> str:
        resp = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=500,
        )
        return str((resp.choices[0].message.content if resp.choices else "") or "").strip()

    summary = ""
    try:
        summary = await asyncio.to_thread(_ask_summary)
    except Exception as exc:
        logger.error("Deal AI summary failed: %s", exc)
    if not summary:
        summary = _fallback_chat_summary(history)
    try:
        handoff = await asyncio.to_thread(_handoff_summary_to_anar, int(lead.get("id") or lead_id), summary)
    except Exception as exc:
        logger.error("AI handoff failed: %s", exc)
        return web.json_response({"success": False, "error": "Xülasə Kommo-ya göndərilmədi."}, status=502)
    if not handoff.get("note_ok"):
        return web.json_response({"success": False, "error": "Xülasə Kommo-ya yazılmadı."}, status=502)
    return web.json_response({
        "success": True,
        "mode": "summary",
        "summary": summary,
        "agent": handoff.get("agent") or KOMMO_AI_AGENT_NAME,
        "handed": bool(handoff.get("handed")),
    })


async def handle_api_deal_public(request: web.Request) -> web.Response:
    lead_id = parse_deal_share_token(request.rel_url.query.get("k") or "")
    if not lead_id:
        return web.json_response({"success": False, "error": "Link etibarsızdır və ya müddəti bitib"}, status=403)
    lead = get_lead_details(int(lead_id))
    deal = build_deal_view_payload(lead_id, lead, require_personal=False)
    if not deal:
        return web.json_response({"success": False, "error": "Sövdələşmə tapılmadı"}, status=404)
    contact_ids = _lead_contact_ids(lead or {})
    chat, chat_blocked, _reply_talk_id, _has_more, _channels, _channel = _collect_deal_chat(
        int(lead_id),
        contact_ids,
        limit=50,
        pages=3,
        employee_name=employee_name_for_lead(lead),
    )
    deal["chat"] = chat
    deal["chat_blocked"] = chat_blocked
    deal.pop("kommo_link", None)
    deal.pop("can_reply", None)
    return web.json_response({"success": True, "deal": deal, "readonly": True})


def _media_bytes_response(request: web.Request, body: bytes, content_type: str) -> web.Response:
    data = body or b""
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "private, max-age=600",
        "Accept-Ranges": "bytes",
        "Content-Type": content_type or "application/octet-stream",
    }
    rng = str(request.headers.get("Range") or "")
    match = re.match(r"bytes=(\d*)-(\d*)", rng)
    if match and data:
        start = int(match.group(1) or 0)
        end = int(match.group(2) or (len(data) - 1))
        end = min(end, len(data) - 1)
        if start > end or start >= len(data):
            return web.Response(status=416, headers={"Content-Range": f"bytes */{len(data)}"})
        chunk = data[start:end + 1]
        headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
        headers["Content-Length"] = str(len(chunk))
        return web.Response(body=chunk, status=206, headers=headers)
    headers["Content-Length"] = str(len(data))
    return web.Response(body=data, headers=headers)


async def handle_api_deal_file(request: web.Request) -> web.Response:
    src = unquote(str(request.rel_url.query.get("src") or "").strip())
    file_uuid = str(request.rel_url.query.get("uuid") or "").strip()
    note_id = 0
    entity_id = 0
    try:
        note_id = int(request.rel_url.query.get("note_id") or 0)
    except (TypeError, ValueError):
        note_id = 0
    try:
        entity_id = int(request.rel_url.query.get("entity_id") or 0)
    except (TypeError, ValueError):
        entity_id = 0
    entity_type = str(request.rel_url.query.get("entity_type") or "").strip().lower()
    if entity_type.startswith("contact"):
        entity_type = "contacts"
    elif entity_type.startswith("lead"):
        entity_type = "leads"
    else:
        entity_type = ""
    token = request.rel_url.query.get("k") or ""
    lead = None
    if token:
        if not parse_deal_share_token(token):
            return web.Response(status=403, text="Forbidden")
    else:
        raw_chat_id = request.headers.get("X-TG-User-ID") or request.rel_url.query.get("uid") or ""
        try:
            chat_id = int(raw_chat_id)
            lead_id = int(request.rel_url.query.get("lead_id") or 0)
        except (TypeError, ValueError):
            return web.Response(status=401, text="Unauthorized")
        if not lead_id:
            return web.Response(status=400, text="lead_id required")
        lead = get_lead_details(lead_id)
        if not lead or not _user_can_view_personal_lead(chat_id, lead):
            return web.Response(status=403, text="Forbidden")
    from_note = False
    if note_id:
        raw_note = _find_raw_note(note_id, lead, entity_type, entity_id)
        if raw_note:
            note_src, note_uuid = _note_recording_src(raw_note)
            if note_src:
                src = note_src
                from_note = True
            if note_uuid and not file_uuid:
                file_uuid = note_uuid
    from_drive = False
    picked_name = ""
    if file_uuid and not src:
        src = _drive_file_download_url(file_uuid)
        from_drive = bool(src)
    if not src and not file_uuid and lead is not None:
        kind = str(request.rel_url.query.get("kind") or "").strip().lower()
        msg = str(request.rel_url.query.get("msg") or "").strip()
        try:
            at = int(request.rel_url.query.get("at") or 0)
        except (TypeError, ValueError):
            at = 0
        try:
            lookup_lead = int(request.rel_url.query.get("lead_id") or 0)
        except (TypeError, ValueError):
            lookup_lead = 0
        if lookup_lead and kind in {"picture", "image", "sticker", "audio", "voice", "ptt"} and (at or msg):
            contact_ids, _phones = _contact_ids_and_phones(lead)
            cand = _find_click_media(lookup_lead, contact_ids, at, kind, msg)
            if isinstance(cand, dict):
                picked_name = str(cand.get("file_name") or "")
                file_uuid = str(cand.get("file_uuid") or "").strip()
                src = str(cand.get("media_url") or "").strip()
                if file_uuid and (not src or not _is_allowed_media_url(src)):
                    src = _drive_file_download_url(file_uuid)
                    from_drive = bool(src)
    if not src or (not from_drive and not from_note and not _is_allowed_media_url(src)):
        return web.Response(status=400, text="Invalid media")
    try:
        headers = {"Authorization": f"Bearer {KOMMO_TOKEN}"} if _is_allowed_kommo_media_url(src) else {}
        audio_resp = requests.get(src, headers=headers, timeout=20, allow_redirects=True)
        if audio_resp.status_code != 200:
            audio_resp = requests.get(src, timeout=20, allow_redirects=True)
        if audio_resp.status_code != 200:
            return web.Response(status=404, text="Media not found")
        file_name = str(request.rel_url.query.get("name") or picked_name)
        content_type = _sniff_media_type(audio_resp.content, audio_resp.headers.get("Content-Type") or "", src, file_name)
        body_bytes = audio_resp.content
        if _is_ogg_bytes(body_bytes) or content_type in {"audio/ogg", "audio/opus"} or str(file_name or "").lower().endswith((".ogg", ".opus", ".oga")):
            cache_k = file_uuid or src
            if cache_k and cache_k in _OLD_OGG_MP3_CACHE:
                body_bytes = _OLD_OGG_MP3_CACHE[cache_k]
                content_type = "audio/mpeg"
            else:
                try:
                    mp3_data, mp3_mime, _ = _ffmpeg_voice_to_mp3(body_bytes, file_name or "voice.ogg")
                    if mp3_data and len(mp3_data) > 64:
                        body_bytes = mp3_data
                        content_type = mp3_mime
                        if cache_k:
                            if len(_OLD_OGG_MP3_CACHE) > 500:
                                _OLD_OGG_MP3_CACHE.pop(next(iter(_OLD_OGG_MP3_CACHE)), None)
                            _OLD_OGG_MP3_CACHE[cache_k] = mp3_data
                except Exception as _conv_err:
                    logger.warning("Old ogg/opus to mp3 on-the-fly convert failed: %s", _conv_err)
        return _media_bytes_response(request, body_bytes, content_type)
    except Exception as exc:
        logger.warning("Deal media proxy failed: %s", exc)
        return web.Response(status=502, text="Media fetch failed")


async def handle_api_rufat_overview(request: web.Request) -> web.Response:
    """Return the fully preloaded Rüfət workspace for instant stage switching."""
    raw_chat_id = (
        request.headers.get("X-TG-User-ID")
        or request.rel_url.query.get("uid")
        or request.rel_url.query.get("chat_id")
        or ""
    )
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    if not is_funnel_chat(chat_id):
        logger.warning("Personal funnel overview access denied for supplied user id")
        return web.json_response({"success": False, "error": "Access denied"}, status=403)
    force = str(request.rel_url.query.get("refresh") or "").lower() in {"1", "true", "yes"}
    try:
        overview = await get_rufat_overview(force=force, owner_chat_id=chat_id)
        owner = get_funnel_owner(chat_id)
        if owner:
            overview["user_name"] = owner["name"]
        if is_admin(chat_id):
            try:
                overview["pipelines"] = load_all_kommo_pipelines()
            except Exception as exc:
                logger.warning("Admin pipelines attach failed: %s", exc)
        return web.json_response({"success": True, **overview, "seen": _get_user_seen_map(chat_id), "is_admin": is_admin(chat_id)})
    except Exception as exc:
        logger.error("Rüfət overview error: %s", exc)
        payload = {"success": False, "error": "Kommo sorğusu uğursuz oldu."}
        if is_admin(chat_id):
            payload["detail"] = str(exc)[:240]
        return web.json_response(payload, status=502)


async def handle_api_notifications(request: web.Request) -> web.Response:
    """Return active tasks for the requesting user."""
    try:
        tg_user_id = request.headers.get("X-TG-User-ID", "")
        chat_id = int(tg_user_id) if tg_user_id else None
        if not chat_id:
            return web.json_response({"success": False, "error": "User not identified"}, status=401)
        if is_funnel_chat(chat_id) and not is_admin(chat_id):
            try:
                overview = await get_rufat_overview(owner_chat_id=chat_id)
            except Exception as exc:
                logger.error("Funnel notifications overview failed: %s", exc)
                return web.json_response({"success": False, "error": "Kommo sorğusu uğursuz oldu."}, status=502)
            return web.json_response({"success": True, "tasks": overview["tasks"], "is_admin": False,
                                      "user_name": overview["user_name"], "ui_stages": overview.get("ui_stages")})
        kommo_user_id = get_kommo_user_id_for_chat(chat_id)
        if not kommo_user_id:
            return web.json_response({"success": True, "tasks": []})
        # Get tasks for this user (incomplete).
        # Fetch active tasks once and filter locally. Nested Kommo task filters
        # can return an empty/invalid response; the previous code swallowed that
        # error and made every user see an empty task list.
        now = datetime.now(tz=BAKU_TZ)
        url = f"{KOMMO_BASE_URL}/api/v4/tasks"
        tasks_list = []
        # Employees retain visibility of their existing work as well as tasks
        # newly routed to Admin.
        allowed_responsible_ids = {10932455, 15532668, 15531960}
        # Kommo may serialize numeric IDs as strings in some responses.
        allowed_responsible_ids = {str(value) for value in allowed_responsible_ids}
        raw_tasks = []
        leads_contact_cache = {}
        task_priorities = read_json(_TASK_PRIORITIES_FILE) or {}
        if not isinstance(task_priorities, dict):
            task_priorities = {}
        try:
            params = {"filter[is_completed]": 0, "limit": 250}
            resp = _http.get(url, headers=HEADERS, params=params, timeout=10)
            if resp.status_code == 200:
                raw_tasks = resp.json().get("_embedded", {}).get("tasks", [])
                if not isinstance(raw_tasks, list):
                    raw_tasks = []
            else:
                logger.error("Notifications task fetch failed: status=%s body=%s", resp.status_code, resp.text[:300])
            if not is_rufat_chat(chat_id):
                raw_tasks = [
                    t for t in raw_tasks
                    if str(t.get("responsible_user_id", "")) in allowed_responsible_ids
                    and str(t.get("is_completed", False)).lower() not in {"true", "1", "yes"}
                ]
            _task_creators_cache = read_json(_TASK_CREATORS_FILE) or {}
            if is_rufat_chat(chat_id):
                raw_tasks = [t for t in raw_tasks if task_allowed_for_chat(t.get("id"), chat_id)]
            if raw_tasks:
                # Batch: collect unique contact entity_ids and fetch them in one request
                contact_ids = set()
                lead_ids = set()
                for t in raw_tasks:
                    eid = t.get("entity_id")
                    etype = t.get("entity_type", "contacts")
                    if eid:
                        if etype == "contacts":
                            contact_ids.add(eid)
                        else:
                            lead_ids.add(eid)
                # Batch fetch contacts
                contacts_cache = {}
                if contact_ids:
                    id_params = {f"filter[id][{i}]": cid for i, cid in enumerate(list(contact_ids)[:50])}
                    id_params["limit"] = 50
                    try:
                        cr = _http.get(f"{KOMMO_BASE_URL}/api/v4/contacts", headers=HEADERS, params=id_params, timeout=8)
                        if cr.status_code == 200:
                            for c in cr.json().get("_embedded", {}).get("contacts", []):
                                try:
                                    contacts_cache[int(c["id"])] = _contact_cache_entry(c)
                                except (KeyError, TypeError, ValueError):
                                    continue
                    except: pass
                # Fetch leads linked to contacts (to get stage)
                contact_lead_stage = {}  # {contact_id: stage_name}
                leads_pipeline_cache = {}  # {lead_id: pipeline_id}
                contact_pipeline_ids = {}  # {contact_id: set[pipeline_id]}
                _employee_funnels = employee_personal_pipeline_ids()
                if contact_ids:
                    try:
                        # Batch: get leads with contacts filter
                        cid_list = list(contact_ids)[:50]
                        lead_params_c = {f"filter[contacts][{i}]": cid for i, cid in enumerate(cid_list)}
                        lead_params_c["limit"] = 50
                        lead_params_c["with"] = "contacts"
                        lr = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads", headers=HEADERS, params=lead_params_c, timeout=8)
                        if lr.status_code == 200:
                            for ld in lr.json().get("_embedded", {}).get("leads", []):
                                st_name = get_pipeline_stages_for_chat(chat_id)[1].get(ld.get("status_id", 0), "")
                                try:
                                    _ld_id = int(ld["id"])
                                    _ld_pipe = _lead_pipeline_id(ld)
                                    leads_pipeline_cache[_ld_id] = _ld_pipe
                                except (KeyError, TypeError, ValueError):
                                    _ld_pipe = 0
                                for lc in ld.get("_embedded", {}).get("contacts", []):
                                    try:
                                        _cid = int(lc["id"])
                                    except (KeyError, TypeError, ValueError):
                                        continue
                                    if _cid in contact_ids and _cid not in contact_lead_stage:
                                        contact_lead_stage[_cid] = st_name
                                    if _ld_pipe:
                                        contact_pipeline_ids.setdefault(_cid, set()).add(_ld_pipe)
                    except: pass
                # Batch fetch leads (get first contact from each)
                leads_contact_cache = {}
                leads_stage_cache = {}  # {lead_id: status_id}
                if lead_ids:
                    lead_params = {f"filter[id][{i}]": lid for i, lid in enumerate(list(lead_ids)[:50])}
                    lead_params.update({"with": "contacts", "limit": 50})
                    try:
                        lr = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads", headers=HEADERS, params=lead_params, timeout=8)
                        if lr.status_code == 200:
                            for lead in lr.json().get("_embedded", {}).get("leads", []):
                                try:
                                    lid = int(lead["id"])
                                except (KeyError, TypeError, ValueError):
                                    continue
                                leads_stage_cache[lid] = lead.get("status_id", 0)
                                _ld_pipe = _lead_pipeline_id(lead)
                                if _ld_pipe:
                                    leads_pipeline_cache[lid] = _ld_pipe
                                emb_contacts = lead.get("_embedded", {}).get("contacts", [])
                                if emb_contacts:
                                    try:
                                        cid = int(emb_contacts[0]["id"])
                                    except (KeyError, TypeError, ValueError):
                                        continue
                                    leads_contact_cache[lid] = cid
                                    contact_ids.add(cid)
                                    if _ld_pipe:
                                        contact_pipeline_ids.setdefault(cid, set()).add(_ld_pipe)
                    except: pass
                    # Fetch any new contact_ids from leads
                    new_cids = set(leads_contact_cache.values()) - set(contacts_cache.keys())
                    if new_cids:
                        new_params = {f"filter[id][{i}]": cid for i, cid in enumerate(list(new_cids)[:50])}
                        new_params["limit"] = 50
                        try:
                            cr2 = _http.get(f"{KOMMO_BASE_URL}/api/v4/contacts", headers=HEADERS, params=new_params, timeout=8)
                            if cr2.status_code == 200:
                                for c in cr2.json().get("_embedded", {}).get("contacts", []):
                                    try:
                                        contacts_cache[int(c["id"])] = _contact_cache_entry(c)
                                    except (KeyError, TypeError, ValueError):
                                        continue
                        except: pass
                for t in raw_tasks:
                    # Skip "Cavab gözlənilir" task type
                    if t.get("task_type_id") == 4229224:
                        continue
                    # Skip "xatırlat muşt." tasks — they go to Gözləmə tab
                    if t.get("task_type_id") == XATIRLAT_TASK_TYPE_ID:
                        continue
                    deadline_ts = t.get("complete_till", 0)
                    deadline_dt = datetime.fromtimestamp(deadline_ts, tz=BAKU_TZ) if deadline_ts else None
                    is_overdue = deadline_dt < now if deadline_dt else False
                    if deadline_dt:
                        time_str = deadline_dt.strftime("%d.%m %H:%M")
                        if is_overdue:
                            diff = now - deadline_dt
                            hours = int(diff.total_seconds() // 3600)
                            if hours > 0:
                                time_str = f"{hours} saat gecikir"
                            else:
                                mins = int(diff.total_seconds() // 60)
                                time_str = f"{mins} d\u0259q gecikir"
                    else:
                        time_str = ""
                    try:
                        entity_id = int(t.get("entity_id"))
                    except (TypeError, ValueError):
                        entity_id = t.get("entity_id")
                    entity_type = t.get("entity_type", "contacts")
                    _task_pipes = _task_entity_pipeline_ids(
                        entity_type, entity_id, leads_pipeline_cache, contact_pipeline_ids
                    )
                    if _task_pipes & _employee_funnels and not is_admin(chat_id):
                        continue
                    contact_row = {}
                    if entity_type == "contacts":
                        contact_row = contacts_cache.get(entity_id) or {}
                    elif entity_type == "leads":
                        cid = leads_contact_cache.get(entity_id)
                        contact_row = contacts_cache.get(cid) or {}
                    contact_name = contact_row.get("name", "")
                    phone = contact_row.get("phone", "")
                    phones = list(contact_row.get("phones") or ([] if not phone else [phone]))
                    responsible_name = KOMMO_USERS.get(t.get("responsible_user_id"), "")
                    if entity_type == "leads":
                        kommo_link = f"https://texnikidestek50.kommo.com/leads/detail/{entity_id}"
                    else:
                        kommo_link = f"https://texnikidestek50.kommo.com/contacts/detail/{entity_id}"
                    # Extract assigneeName from marker
                    task_text = t.get("text", "")
                    _marker_match = re.match(r'^\[(Rüfət Həsənzadə|Soltan Abbasov|Hüseyn Səfərov|Nizami Qasımov|Rasim Əsgərov|Sərmayə Əhmədsoy|Asya Agayeva|Nuranə Şirinova|Texniki Dəstək|Texniki tapşırıq|Rüfət|Soltan|Hüseyn|Nizami|Rasim|Sərmayə|Asya|Nuranə|Texniki)(?::\d+)?\]\s*', task_text)
                    assignee_name_from_marker = _marker_match.group(1) if _marker_match else ""
                    _SHORT_TO_FULL = {'Rüfət':'Rüfət Həsənzadə','Soltan':'Soltan Abbasov','Hüseyn':'Hüseyn Səfərov','Nizami':'Nizami Qasımov','Rasim':'Rasim Əsgərov','Sərmayə':'Sərmayə Əhmədsoy','Asya':'Asya Agayeva','Nuranə':'Nuranə Şirinova','Texniki': TECHNICAL_SUPPORT_NAME}
                    if assignee_name_from_marker in _SHORT_TO_FULL:
                        assignee_name_from_marker = _SHORT_TO_FULL[assignee_name_from_marker]
                    # Fallback: determine assignee from Gözləmə columns. Admin Nizami
                    # owns that board — do not treat Soltan/Sərmayə columns as a
                    # different icraçı (that hid his funnel tasks after sync).
                    if not assignee_name_from_marker and entity_type == 'leads' and not is_admin(chat_id):
                        _lead_status = leads_stage_cache.get(entity_id, 0)
                        _STATUS_TO_NAME = {109988184: 'Rüfət Həsənzadə', 109988188: 'Soltan Abbasov', 109988192: 'Hüseyn Səfərov', 109988196: 'Nizami Qasımov', 109988200: 'Rasim Əsgərov', 109988204: 'Sərmayə Əhmədsoy', 109988208: 'Asya Agayeva', 109988212: 'Nuranə Şirinova'}
                        assignee_name_from_marker = _STATUS_TO_NAME.get(_lead_status, '')
                    if not assignee_name_from_marker and t.get("responsible_user_id") == 10932455:
                        assignee_name_from_marker = "Nizami Qas\u0131mov"
                    _TASK_TYPE_NAMES_NOTIF = {1: "Əlaqə saxla", 2: "Görüş", 3263995: "Təqdimat", 3263999: "Quraşdırma", 3267595: "Zəng et", 4229224: "Cavab gözlənilir", 4232112: "aktiv", 4232108: "Import", 4239844: "passiv"}
                    task_type_name = _TASK_TYPE_NAMES_NOTIF.get(t.get("task_type_id"), "")
                    # Fetch last note for this entity
                    last_note = ""
                    _note_entity_id = entity_id
                    _note_entity_type = entity_type
                    if _note_entity_type == "contacts" and entity_id in contacts_cache:
                        # Try to get lead for this contact to fetch lead notes
                        pass
                    try:
                        _note_url = f"{KOMMO_BASE_URL}/api/v4/{_note_entity_type}/{_note_entity_id}/notes"
                        _note_resp = _http.get(_note_url, headers=HEADERS, params={"limit": 1, "order[updated_at]": "desc", "filter[note_type]": "common"}, timeout=8)
                        if _note_resp.status_code == 200:
                            _notes_data = _note_resp.json().get("_embedded", {}).get("notes", [])
                            if _notes_data:
                                last_note = _notes_data[0].get("params", {}).get("text", "")
                    except:
                        pass
                    # Cavabdeh (created_by) - who created the task
                    _created_by_name = _task_creators_cache.get(str(t.get("id")), "")
                    if not _created_by_name:
                        _created_by_id = t.get("created_by")
                        if _created_by_id:
                            if _created_by_id == 10932455:
                                _created_by_name = "Nizami Qas\u0131mov"
                            elif _created_by_id == 15532668:
                                _created_by_name = ""  # Unknown employee via bot
                            else:
                                _created_by_name = KOMMO_USERS.get(_created_by_id, "")
                    # Extract price from [Price] or [Name:Price] marker
                    _price_m = re.match(r"^\[(?:[^:\]]*:)?(\d+(?:\.\d+)?)\]", t.get("text", ""))
                    _task_price = _price_m.group(1) if _price_m else ""
                    tasks_list.append({
                        "id": t.get("id"),
                        "title": "\u26a0\ufe0f Gecikmi\u015f tap\u015f\u0131r\u0131q" if is_overdue else "\ud83d\udccb Aktiv tap\u015f\u0131r\u0131q",
                        "desc": task_text,
                        "price": _task_price,
                        "time": time_str,
                        "is_overdue": is_overdue,
                        "entity_id": entity_id,
                        "entity_type": entity_type,
                        "task_id": t.get("id"),
                        "contact_name": contact_name,
                        "phone": phone,
                        "phones": phones,
                        "source": contact_row.get("source") or "",
                        "menbe": contact_row.get("source") or "",
                        "utm": contact_row.get("source") if contact_row.get("source") in MENBE_LABELS else "",
                        "utm_tag": contact_row.get("source") if contact_row.get("source") in MENBE_LABELS else "",
                        "partner": get_deal_partner(entity_id) if entity_type == "leads" else "",
                        "responsible": responsible_name,
                        "assigneeName": assignee_name_from_marker,
                        "kommo_link": kommo_link,
                        "complete_till": t.get("complete_till", 0),
                        "task_type_name": task_type_name,
                        "task_type_id": t.get("task_type_id", 1),
                        "last_note": last_note,
                        "priority": task_priorities.get(str(t.get("id")), task_priorities.get(t.get("id"), "")),
                        "stage_name": get_pipeline_stages_for_chat(chat_id)[1].get(leads_stage_cache.get(entity_id, 0), "") if entity_type == "leads" else contact_lead_stage.get(entity_id, ""),
                        "voice_url": f"/api/voice/{entity_id}" if str(entity_id) in _voice_urls else "",
                        "created_by": _created_by_name
                    })
                # Sort: overdue first
                tasks_list.sort(key=lambda x: (not x["is_overdue"], x["time"]))
        except Exception as e:
            logger.error(f"Notifications fetch error: {e}")
        # Gözləmə employees share Kommo licenses with funnel owners. Keep only
        # tasks marked for this person or sitting in their Gözləmə column.
        if kommo_user_id != 10932455:
            _user_status = TG_TO_STATUS_ID.get(chat_id)
            _employee_name = get_employee_name_by_chat_id(chat_id, "")
            _user_lead_ids = set()
            _stage_ok = False
            if _user_status:
                try:
                    _stage_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads",
                        headers=HEADERS, params={
                            "filter[statuses][0][pipeline_id]": GOZLEME_PIPELINE_ID,
                            "filter[statuses][0][status_id]": _user_status,
                            "limit": 250
                        }, timeout=10)
                    if _stage_resp.status_code == 200:
                        _stage_ok = True
                        for _l in _stage_resp.json().get('_embedded',{}).get('leads',[]):
                            try:
                                _user_lead_ids.add(int(_l['id']))
                            except (KeyError, TypeError, ValueError):
                                continue
                except Exception as _fe:
                    logger.error(f"Stage filter error: {_fe}")
            def _task_belongs_to_user(task_item):
                if _employee_name and task_item.get('assigneeName', '') == _employee_name:
                    return True
                if not _stage_ok:
                    return False
                try:
                    eid = int(task_item.get('entity_id'))
                except (TypeError, ValueError):
                    eid = task_item.get('entity_id')
                etype = _normalize_kommo_entity_type(task_item.get('entity_type', 'contacts'))
                if etype == 'leads' and eid in _user_lead_ids:
                    return True
                if etype == 'contacts':
                    for lid, cid in leads_contact_cache.items():
                        if cid == eid and lid in _user_lead_ids:
                            return True
                return False
            tasks_list = [t for t in tasks_list if _task_belongs_to_user(t)]
        user_display_name = get_employee_name_by_chat_id(chat_id, "")
        if is_admin(chat_id):
            funnel_overlay = []
            owners = [get_funnel_owner(cid) for cid in (RUFAT_CHAT_ID, HUSEYN_CHAT_ID, RASIM_CHAT_ID, ADMIN_CHAT_ID)]
            overviews = await asyncio.gather(*[
                get_rufat_overview(owner_chat_id=owner["chat_id"]) for owner in owners if owner
            ], return_exceptions=True)
            for overview in overviews:
                if not isinstance(overview, dict):
                    logger.error("Admin funnel overlay skipped: %s", overview)
                    continue
                owner_name = overview.get("user_name") or overview.get("funnel_owner") or ""
                for item in overview.get("tasks") or []:
                    if not item.get("assigneeName"):
                        item["assigneeName"] = owner_name
                    funnel_overlay.append(item)
            if funnel_overlay:
                by_id = {item.get("id"): item for item in tasks_list}
                for item in funnel_overlay:
                    by_id[item.get("id")] = item
                tasks_list = sorted(
                    by_id.values(),
                    key=lambda item: (not item.get("is_overdue"), item.get("complete_till") or 9999999999),
                )
        return web.json_response({"success": True, "tasks": tasks_list, "is_admin": kommo_user_id == 10932455, "user_name": user_display_name})
    except Exception as e:
        logger.error(f"API notifications error: {e}")
        return web.json_response({"success": False, "error": "Server xətası."}, status=500)

# ─── Gözləmə Pipeline Configuration ────────────────────────────────────────
GOZLEME_PIPELINE_ID = 14243944
XATIRLAT_TASK_TYPE_ID = 4239844  # xatırlat muşt.
TG_TO_STATUS_ID = {
    RUFAT_CHAT_ID: 109988184,   # Rüfət Həsənzadə
    7262243946: 109988188,   # Soltan Abbasov
    7329891614: 109988192,   # Hüseyn Səfərov
    1628569350: 109988196,   # Nizami Qasımov / Admin
    7920785774: 109988200,   # Rasim Əsgərov
    1289510272: 109988204,   # Sərmayə Əhmədsoy
    6596538872: 109988208,   # Asya Agayeva
    1142054888: 109988212,   # Nuranə Şirinova
}


async def handle_api_gozleme(request: web.Request) -> web.Response:
    """Return 'xatırlat müşt.' tasks with the SAME payload shape as /api/notifications.

    Admin (Nizami Qasımov) sees every stage of the Gözləmə pipeline; regular
    employees only see leads sitting in their own personal stage.
    """
    try:
        tg_user_id_str = request.headers.get("X-TG-User-ID", "")
        try:
            chat_id = int(tg_user_id_str)
        except (TypeError, ValueError):
            return web.json_response({"success": False, "error": "User not identified"}, status=401)

        kommo_user_id = get_kommo_user_id_for_chat(chat_id)
        is_admin = kommo_user_id == ADMIN_KOMMO_USER_ID or chat_id == ADMIN_CHAT_ID

        if is_funnel_chat(chat_id) and not is_admin:
            overview = await get_rufat_overview(owner_chat_id=chat_id)
            reminders = overview["gozleme"]
            return web.json_response({"success": True, "items": reminders, "tasks": reminders,
                                      "count": len(reminders), "is_admin": False,
                                      "user_name": overview["user_name"]})
        user_status_id = TG_TO_STATUS_ID.get(chat_id)
        if not is_admin and not user_status_id:
            return web.json_response({"success": True, "items": [], "tasks": [], "is_admin": False,
                                      "message": "Bu istifadəçi üçün mərhələ tapılmadı."})

        # ── Fetch leads of the Gözləmə pipeline ────────────────────────────
        leads_url = f"{KOMMO_BASE_URL}/api/v4/leads"
        leads_params = {
            "filter[pipeline_id]": GOZLEME_PIPELINE_ID,
            "with": "contacts",
            "limit": 250,
        }
        if not is_admin:
            leads_params["filter[statuses][0][pipeline_id]"] = GOZLEME_PIPELINE_ID
            leads_params["filter[statuses][0][status_id]"] = user_status_id
        try:
            leads_resp = _http.get(leads_url, headers=HEADERS, params=leads_params, timeout=10)
            if leads_resp.status_code != 200:
                logger.error(f"Gözləmə leads fetch failed: {leads_resp.status_code} {leads_resp.text[:200]}")
                return web.json_response({"success": False, "error": "Kommo sorğusu uğursuz oldu."}, status=502)
            leads = leads_resp.json().get("_embedded", {}).get("leads", [])
        except Exception as exc:
            logger.error(f"Gözləmə leads request error: {exc}")
            return web.json_response({"success": False, "error": "Kommo sorğusu uğursuz oldu."}, status=502)

        user_display_name = get_employee_name_by_chat_id(chat_id, "")
        if not leads:
            return web.json_response({"success": True, "items": [], "tasks": [],
                                      "is_admin": is_admin, "user_name": user_display_name})

        now_baku = datetime.now(tz=BAKU_TZ)
        task_priorities = read_json(_TASK_PRIORITIES_FILE) or {}
        if not isinstance(task_priorities, dict):
            task_priorities = {}
        _task_creators_cache = read_json(_TASK_CREATORS_FILE) or {}
        if not isinstance(_task_creators_cache, dict):
            _task_creators_cache = {}

        _STATUS_TO_NAME_GOZ = {
            109988184: 'Rüfət Həsənzadə', 109988188: 'Soltan Abbasov', 109988192: 'Hüseyn Səfərov',
            109988196: 'Nizami Qasımov', 109988200: 'Rasim Əsgərov', 109988204: 'Sərmayə Əhmədsoy',
            109988208: 'Asya Agayeva', 109988212: 'Nuranə Şirinova',
        }
        _SHORT_TO_FULL = {'Rüfət': 'Rüfət Həsənzadə', 'Soltan': 'Soltan Abbasov', 'Hüseyn': 'Hüseyn Səfərov',
                          'Nizami': 'Nizami Qasımov', 'Rasim': 'Rasim Əsgərov', 'Texniki': TECHNICAL_SUPPORT_NAME}
        _TASK_TYPE_NAMES_GOZ = {1: "Əlaqə saxla", 2: "Görüş", 3263995: "Təqdimat", 3263999: "Quraşdırma",
                                3267595: "Zəng et", 4229224: "Cavab gözlənilir", 4232112: "aktiv",
                                4232108: "Import", XATIRLAT_TASK_TYPE_ID: "passiv"}

        lead_map = {}          # {lead_id: lead}
        lead_contact_id = {}   # {lead_id: contact_id}
        contact_ids = set()
        for lead in leads:
            try:
                lid = int(lead["id"])
            except (KeyError, TypeError, ValueError):
                continue
            lead_map[lid] = lead
            emb_contacts = lead.get("_embedded", {}).get("contacts", []) or []
            if emb_contacts:
                try:
                    cid = int(emb_contacts[0].get("id"))
                except (TypeError, ValueError):
                    cid = None
                if cid:
                    lead_contact_id[lid] = cid
                    contact_ids.add(cid)

        # ── Batch fetch contacts (name + phone), 50 per request ────────────
        contacts_cache = {}
        cid_list = list(contact_ids)
        for chunk_start in range(0, len(cid_list), 50):
            chunk = cid_list[chunk_start:chunk_start + 50]
            id_params = {f"filter[id][{i}]": cid for i, cid in enumerate(chunk)}
            id_params["limit"] = 50
            try:
                cr = _http.get(f"{KOMMO_BASE_URL}/api/v4/contacts", headers=HEADERS, params=id_params, timeout=8)
                if cr.status_code == 200:
                    for c in cr.json().get("_embedded", {}).get("contacts", []):
                        try:
                            contacts_cache[int(c["id"])] = _contact_cache_entry(c)
                        except (KeyError, TypeError, ValueError):
                            continue
            except Exception:
                pass

        tasks_list = []
        for lead_id, lead in lead_map.items():
            # Fetch non-completed tasks for this lead
            try:
                tasks_resp = _http.get(
                    f"{KOMMO_BASE_URL}/api/v4/tasks",
                    headers=HEADERS,
                    params={
                        "filter[entity_id]": lead_id,
                        "filter[entity_type]": "leads",
                        "filter[is_completed]": 0,
                        "limit": 50,
                    },
                    timeout=8,
                )
                if tasks_resp.status_code != 200:
                    continue
                lead_tasks = tasks_resp.json().get("_embedded", {}).get("tasks", [])
            except Exception as exc:
                logger.warning(f"Gözləmə tasks fetch for lead {lead_id}: {exc}")
                continue

            lead_name = lead.get("name", "")
            lead_status_id = lead.get("status_id", 0)
            cid = lead_contact_id.get(lead_id)
            contact_row = contacts_cache.get(cid) or {}
            contact_name = contact_row.get("name", "")
            contact_phone = contact_row.get("phone", "")
            contact_phones = list(contact_row.get("phones") or ([] if not contact_phone else [contact_phone]))

            # Last note of the lead (same as Tapşırıqlar cards)
            last_note = ""
            try:
                _note_resp = _http.get(
                    f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}/notes",
                    headers=HEADERS,
                    params={"limit": 1, "order[updated_at]": "desc", "filter[note_type]": "common"},
                    timeout=8,
                )
                if _note_resp.status_code == 200:
                    _notes_data = _note_resp.json().get("_embedded", {}).get("notes", [])
                    if _notes_data:
                        last_note = _notes_data[0].get("params", {}).get("text", "")
            except Exception:
                pass

            for task in lead_tasks:
                if task.get("is_completed"):
                    continue
                # Gözləmə tab shows ONLY "xatırlat müşt." tasks
                if task.get("task_type_id") != XATIRLAT_TASK_TYPE_ID:
                    continue

                deadline_ts = task.get("complete_till", 0)
                deadline_dt = datetime.fromtimestamp(deadline_ts, tz=BAKU_TZ) if deadline_ts else None
                is_overdue = deadline_dt < now_baku if deadline_dt else False
                if deadline_dt:
                    time_str = deadline_dt.strftime("%d.%m %H:%M")
                    if is_overdue:
                        diff = now_baku - deadline_dt
                        hours = int(diff.total_seconds() // 3600)
                        if hours > 0:
                            time_str = f"{hours} saat gecikir"
                        else:
                            mins = int(diff.total_seconds() // 60)
                            time_str = f"{mins} d\u0259q gecikir"
                    deadline_str = deadline_dt.strftime("%d.%m.%Y %H:%M")
                else:
                    time_str = ""
                    deadline_str = ""

                task_text = task.get("text", "")
                _marker_match = re.match(
                    r'^\[(Rüfət Həsənzadə|Soltan Abbasov|Hüseyn Səfərov|Nizami Qasımov|Rasim Əsgərov|Sərmayə Əhmədsoy|Asya Agayeva|Nuranə Şirinova|Texniki Dəstək|Texniki tapşırıq|Rüfət|Soltan|Hüseyn|Nizami|Rasim|Texniki)(?::[\d.]+)?\]\s*',
                    task_text)
                assignee_name = _marker_match.group(1) if _marker_match else ""
                if assignee_name in _SHORT_TO_FULL:
                    assignee_name = _SHORT_TO_FULL[assignee_name]
                if not assignee_name:
                    assignee_name = _STATUS_TO_NAME_GOZ.get(lead_status_id, "")
                if not assignee_name and task.get("responsible_user_id") == ADMIN_KOMMO_USER_ID:
                    assignee_name = "Nizami Qas\u0131mov"

                # Cavabdeh (created_by)
                _created_by_name = _task_creators_cache.get(str(task.get("id")), "")
                if not _created_by_name:
                    _created_by_id = task.get("created_by")
                    if _created_by_id:
                        if _created_by_id == ADMIN_KOMMO_USER_ID:
                            _created_by_name = "Nizami Qas\u0131mov"
                        elif _created_by_id == 15532668:
                            _created_by_name = ""
                        else:
                            _created_by_name = KOMMO_USERS.get(_created_by_id, "")

                entity_id = task.get("entity_id") or lead_id
                tasks_list.append({
                    "id": task.get("id"),
                    "title": "\u26a0\ufe0f Gecikmi\u015f tap\u015f\u0131r\u0131q" if is_overdue else "\ud83d\udd14 G\u00f6zl\u0259m\u0259",
                    "desc": task_text,
                    "task_text": task_text,
                    "time": time_str,
                    "deadline": deadline_str,
                    "deadline_ts": deadline_ts,
                    "is_overdue": is_overdue,
                    "entity_id": entity_id,
                    "entity_type": task.get("entity_type", "leads"),
                    "task_id": task.get("id"),
                    "lead_id": lead_id,
                    "lead_name": lead_name,
                    "contact_name": contact_name,
                    "phone": contact_phone,
                    "phones": contact_phones,
                    "source": contact_row.get("source") or extract_menbe(lead),
                    "menbe": contact_row.get("source") or extract_menbe(lead),
                    "utm": (contact_row.get("source") or extract_menbe(lead)) if (contact_row.get("source") or extract_menbe(lead)) in MENBE_LABELS else menbe_from_utm(collect_utm_blob(lead)),
                    "utm_tag": (contact_row.get("source") or extract_menbe(lead)) if (contact_row.get("source") or extract_menbe(lead)) in MENBE_LABELS else menbe_from_utm(collect_utm_blob(lead)),
                    "partner": get_deal_partner(lead_id),
                    "responsible": KOMMO_USERS.get(task.get("responsible_user_id"), ""),
                    "assigneeName": assignee_name,
                    "assignee_name": assignee_name,
                    "kommo_link": f"https://texnikidestek50.kommo.com/leads/detail/{lead_id}",
                    "complete_till": deadline_ts,
                    "task_type_name": _TASK_TYPE_NAMES_GOZ.get(task.get("task_type_id"), "passiv"),
                    "task_type_id": task.get("task_type_id", XATIRLAT_TASK_TYPE_ID),
                    "last_note": last_note,
                    "priority": task_priorities.get(str(task.get("id")), task_priorities.get(task.get("id"), "")),
                    "stage_name": _STATUS_TO_NAME_GOZ.get(lead_status_id, "") or STAGE_NAMES.get(lead_status_id, ""),
                    "voice_url": f"/api/voice/{lead_id}" if str(lead_id) in _voice_urls else "",
                    "created_by": _created_by_name,
                })

        # Sort: overdue first, then by deadline ascending
        tasks_list.sort(key=lambda x: (not x["is_overdue"], x["deadline_ts"] or 9999999999))

        return web.json_response({
            "success": True,
            "items": tasks_list,
            "tasks": tasks_list,
            "is_admin": is_admin,
            "user_name": user_display_name,
        })
    except Exception as exc:
        logger.error(f"handle_api_gozleme error: {exc}")
        return web.json_response({"success": False, "error": "Server xətası."}, status=500)

async def handle_search_contacts(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        query = str(data.get("query") or data.get("phone") or data.get("q") or "").strip()
        digits = re.sub(r"[^\d]", "", query)
        if len(digits) < 7 and len(query) < 2:
            return web.json_response({"success": False, "error": "Axtarış çox qısadır"}, status=400)
        chat_id = _deal_request_user(request) or 0
        contacts = search_contacts_by_query(query)
        results = []
        for contact in contacts:
            name = str(contact.get("name") or "").strip()
            phone_val = _contact_phone_value(contact)
            picked = _pick_search_lead(contact, chat_id)
            leads = []
            if contact.get("_embedded", {}).get("leads"):
                for linked in contact["_embedded"]["leads"]:
                    if isinstance(linked, dict) and linked.get("id"):
                        leads.append({"id": linked.get("id")})
            results.append({
                "id": contact.get("id"),
                "name": name,
                "phone": phone_val,
                "lead_id": picked.get("lead_id") or 0,
                "lead_name": picked.get("lead_name") or "",
                "leads": leads,
            })
        return web.json_response({"success": True, "contacts": results})
    except Exception as e:
        logger.error(f"Search contacts API error: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def serve_webapp(request: web.Request) -> web.Response:
    """Serve the web app from either the deployment layout or the source layout.

    Railway deployments have used both ``docs/index.html`` and a flat
    ``index.html`` beside bot.py.  The old implementation only supported the
    former and consequently raised FileNotFoundError when the latter layout
    was deployed.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(base_dir, "docs", "index.html"),
        os.path.join(base_dir, "index.html"),
    )
    html_path = next((path for path in candidates if os.path.isfile(path)), None)
    if not html_path:
        logger.error("Web app index not found; checked: %s", ", ".join(candidates))
        return web.Response(status=404, text="Web app index not found")
    logger.info("Serving web app from %s", html_path)
    resp = web.FileResponse(html_path)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


async def serve_deal_page(request: web.Request) -> web.Response:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = (
        os.path.join(base_dir, "docs", "deal.html"),
        os.path.join(base_dir, "deal.html"),
    )
    html_path = next((path for path in candidates if os.path.isfile(path)), None)
    if not html_path:
        return web.Response(status=404, text="Deal page not found")
    return web.FileResponse(html_path)


async def serve_privacy_policy(request: web.Request) -> web.Response:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(base_dir)
    candidates = (
        os.path.join(base_dir, "docs", "privacy-policy.html"),
        os.path.join(base_dir, "privacy-policy.html"),
        os.path.join(parent_dir, "docs", "privacy-policy.html"),
        os.path.join(parent_dir, "privacy-policy.html"),
    )
    html_path = next((path for path in candidates if os.path.isfile(path)), None)
    if not html_path:
        return web.Response(status=404, text="Privacy policy not found")
    return web.FileResponse(html_path)

@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-TG-User-ID, Cache-Control'
    return resp

# ─── Web Push ─────────────────────────────────────────────────────────────────
async def handle_push_subscribe(request):
    """Save push subscription for a user."""
    data = await request.json()
    user_id = request.headers.get('X-TG-User-ID', '')
    sub = data.get('subscription')
    if user_id and sub:
        save_push_subscription(user_id, sub)
        logger.info(f"Push subscription saved for user {user_id}")
    return web.json_response({'success': True})

def _notify_cloud_chat_incoming(lead_id: int, name: str, preview: str, phone: str = "") -> None:
    title = str(name or "").strip()
    notice_phone = _wa_display_number(phone)
    target_uids: set[str] = set()

    for pid, def_uids in [
        (int(RUFAT_PIPELINE_ID), {str(RUFAT_CHAT_ID), *(str(cid) for cid in RUFAT_COMPAT_CHAT_IDS)}),
        (int(NIZAMI_PIPELINE_ID), {str(ADMIN_CHAT_ID)}),
    ]:
        overview = _personal_overview_cache.get(pid) or {}
        for deal in overview.get("deals") or []:
            try:
                if int(deal.get("id") or 0) != int(lead_id):
                    continue
            except (TypeError, ValueError):
                continue
            if not title:
                title = str(deal.get("contact_name") or "").strip()
            if not notice_phone:
                phones = deal.get("phones") if isinstance(deal.get("phones"), list) else []
                raw = next((str(item or "").strip() for item in phones if str(item or "").strip()), "")
                notice_phone = _wa_display_number(raw or deal.get("phone") or "")
            target_uids.update(def_uids)
            break

    if not target_uids:
        target_uids = {str(RUFAT_CHAT_ID), *(str(cid) for cid in RUFAT_COMPAT_CHAT_IDS), str(ADMIN_CHAT_ID)}

    title = (title or notice_phone or "WhatsApp")[:80]
    body = " ".join(str(preview or "Yeni mesaj").split())[:140] or "Yeni mesaj"
    if notice_phone and notice_phone != title:
        body = f"{notice_phone}\n{body}"
    url = f"#chat-{int(lead_id)}"
    for uid in target_uids:
        send_push_notification(uid, title, body, url, lead_id=int(lead_id))


def send_push_notification(user_id, title, body, url=None, urgent=False, lead_id=0):
    """Send push notification to a user if subscribed."""
    sub = get_push_subscription(str(user_id))
    if not sub:
        return
    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url or "/",
        "urgent": bool(urgent),
        "chat": bool(lead_id),
        "lead_id": int(lead_id or 0),
    })
    try:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
        logger.info(f"Push sent to {user_id}")
    except WebPushException as e:
        logger.warning(f"Push failed for {user_id}: {e}")
        if '410' in str(e) or '404' in str(e):
            remove_push_subscription(str(user_id))
    except Exception as e:
        logger.warning(f"Push error for {user_id}: {e}")

def send_push_to_admin(body, title="Bein Systems", url=None):
    """Send push notification to admin."""
    send_push_notification('1628569350', title, body, url)

def send_push_to_all_salary(title, body, url=None):
    """Send push to all salary employees."""
    for uid in [str(RUFAT_CHAT_ID),'7262243946','7329891614']:
        send_push_notification(uid, title, body, url)

KOMMO_DRIVE_URL = "https://drive-g.kommo.com"
_VOICE_URLS_FILE = "voice_urls.json"
_voice_urls = read_json(_VOICE_URLS_FILE) or {}  # {entity_id: {"url": download_url, "uuid": file_uuid}} or legacy str

async def handle_upload_voice(request: web.Request) -> web.Response:
    """Upload voice to Kommo Files API, attach to entity, return download URL."""
    try:
        import base64, tempfile, os as _os
        data = await request.json()
        entity_id = data.get("entity_id")
        entity_type = data.get("entity_type", "leads")
        audio_b64 = data.get("audio")  # base64 encoded audio
        filename = data.get("filename", "voice.ogg")
        if not entity_id or not audio_b64:
            return web.json_response({"success": False, "error": "entity_id and audio required"}, status=400)
        audio_bytes = base64.b64decode(audio_b64)
        file_size = len(audio_bytes)
        auth_h = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
        # Step 1: Create upload session
        sess_resp = requests.post(f"{KOMMO_DRIVE_URL}/v1.0/sessions",
            headers={**auth_h, "Content-Type": "application/json"},
            json={"file_name": filename, "file_size": file_size, "content_type": "audio/ogg"},
            timeout=10)
        if sess_resp.status_code != 200:
            return web.json_response({"success": False, "error": f"Session failed: {sess_resp.status_code}"}, status=500)
        sess_data = sess_resp.json()
        upload_url = sess_data["upload_url"]
        max_part = sess_data.get("max_part_size", 524288)
        # Step 2: Upload file parts
        offset = 0
        file_uuid = None
        version_uuid = ""
        download_url = None
        version_href = ""
        while offset < file_size:
            chunk = audio_bytes[offset:offset+max_part]
            up_resp = requests.post(upload_url,
                headers={**auth_h, "Content-Type": "application/octet-stream"},
                data=chunk, timeout=15)
            if up_resp.status_code != 200:
                return web.json_response({"success": False, "error": f"Upload failed: {up_resp.status_code}"}, status=500)
            up_data = up_resp.json()
            if "next_url" in up_data:
                upload_url = up_data["next_url"]
            if "uuid" in up_data:
                file_uuid, version_uuid = _drive_uuids_from_payload(up_data)
                download_url = up_data.get("_links", {}).get("download", {}).get("href", "")
                version_href = up_data.get("_links", {}).get("download_version", {}).get("href", "")
            offset += max_part
        if not file_uuid:
            return web.json_response({"success": False, "error": "No file UUID returned"}, status=500)
        # Step 3: Attach file to entity via main API domain (drive domain attach returns 404)
        attached = False
        try:
            attach_url = f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/files"
            att_resp = _http.put(attach_url, headers=HEADERS, json=[{"file_uuid": file_uuid}], timeout=10)
            logger.info(f"File attach {attach_url}: {att_resp.status_code}")
            attached = att_resp.status_code in (200, 201, 202)
        except Exception as _ae:
            logger.error(f"File attach error: {_ae}")
        if not attached:
            # Fallback: attach via note with note_type=file
            try:
                if not _is_drive_uuid(version_uuid):
                    version_ids = _DRIVE_UUID_RE.findall(str(version_href or ""))
                    version_uuid = version_ids[-1] if version_ids else ""
                file_note_payload = [{"note_type": "file", "params": {"file_uuid": file_uuid, "file_name": filename, "version_uuid": version_uuid}}]
                fn_resp = _http.post(f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes", headers=HEADERS, json=file_note_payload, timeout=10)
                logger.info(f"File note attach: {fn_resp.status_code} {fn_resp.text[:200]}")
                attached = fn_resp.status_code in (200, 201)
            except Exception as _fe:
                logger.error(f"File note attach error: {_fe}")
        # Step 4: Also add as note for visibility
        note_url = f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes"
        note_payload = [{"note_type": "common", "params": {"text": f"\ud83c\udf99 S\u0259s yaz\u0131s\u0131 ({file_size//1024}KB)"}}]
        _http.post(note_url, headers=HEADERS, json=note_payload, timeout=8)
        _voice_urls[str(entity_id)] = {"url": download_url or "", "uuid": file_uuid}
        write_json(_VOICE_URLS_FILE, _voice_urls)
        return web.json_response({"success": True, "message": "S\u0259s yaz\u0131s\u0131 \u0259lav\u0259 olundu", "download_url": download_url, "file_uuid": file_uuid})
    except Exception as e:
        logger.error(f"Upload voice error: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_voice_proxy(request: web.Request) -> web.Response:
    """Proxy voice download - fetches fresh URL from Kommo Drive using stored file_uuid."""
    entity_id = request.match_info.get("entity_id", "")
    voice_info = _voice_urls.get(entity_id)
    if not voice_info:
        return web.Response(status=404)
    # Support legacy format (plain string URL)
    if isinstance(voice_info, str):
        download_url = voice_info
    else:
        file_uuid = voice_info.get("uuid", "")
        download_url = voice_info.get("url", "")
        # Try to get fresh download link
        if file_uuid:
            try:
                auth_h = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
                info_resp = requests.get(f"{KOMMO_DRIVE_URL}/v1.0/files/{file_uuid}", headers=auth_h, timeout=8)
                if info_resp.status_code == 200:
                    dl = info_resp.json().get("_links", {}).get("download", {}).get("href", "")
                    if dl:
                        download_url = dl
            except:
                pass
    if not download_url:
        return web.Response(status=404)
    # Fetch and proxy the audio
    try:
        auth_h = {"Authorization": f"Bearer {KOMMO_TOKEN}"}
        audio_resp = requests.get(download_url, headers=auth_h, timeout=15)
        if audio_resp.status_code == 200:
            return _media_bytes_response(request, audio_resp.content, "audio/ogg")
    except:
        pass
    # Fallback: redirect to URL
    return web.HTTPFound(download_url)

async def handle_api_stages(request: web.Request) -> web.Response:
    chat_id = request.headers.get("X-TG-User-ID", "")
    _, names = get_pipeline_stages_for_chat(chat_id)
    return web.json_response({"pipeline_id": get_pipeline_id_for_chat(chat_id), "stages": {str(k): v for k, v in names.items()}})


async def handle_api_pipelines(request: web.Request) -> web.Response:
    raw_chat_id = request.headers.get("X-TG-User-ID") or request.rel_url.query.get("uid") or ""
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "User not identified"}, status=401)
    if not is_admin(chat_id):
        return web.json_response({"success": False, "error": "Access denied"}, status=403)
    try:
        pipelines = await asyncio.to_thread(load_all_kommo_pipelines)
    except Exception as exc:
        logger.warning("Admin pipelines failed: %s", exc)
        return web.json_response({"success": False, "error": "Hunilər yüklənmədi"}, status=502)
    return web.json_response({"success": True, "pipelines": pipelines})


async def handle_whatsapp_webhook(request: web.Request) -> web.Response:
    if request.method == "GET":
        mode = str(request.rel_url.query.get("hub.mode") or "")
        token = str(request.rel_url.query.get("hub.verify_token") or "")
        challenge = str(request.rel_url.query.get("hub.challenge") or "")
        if mode == "subscribe" and token and token in WA_VERIFY_TOKENS and challenge:
            return web.Response(text=challenge, content_type="text/plain")
        return web.Response(text="forbidden", status=403)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    asyncio.create_task(asyncio.to_thread(_process_whatsapp_payload, payload))
    return web.Response(text="ok")


async def start_webhook_server():
    try:
        await asyncio.to_thread(_wa_ensure_subscribed)
    except Exception as exc:
        logger.warning("WhatsApp subscribe on start failed: %s", exc)
    app_web = web.Application(middlewares=[cors_middleware])
    app_web.router.add_route('OPTIONS', '/api/action', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/notifications', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/samil/overview', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/pending_actions', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/pending_actions/resolve', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/pending_actions/delete', lambda r: web.Response())
    app_web.router.add_post("/webhook/kommo", handle_kommo_webhook)
    app_web.router.add_get("/api/chats/pulse", handle_api_chats_pulse)
    app_web.router.add_get("/webhook/whatsapp", handle_whatsapp_webhook)
    app_web.router.add_post("/webhook/whatsapp", handle_whatsapp_webhook)
    app_web.router.add_get("/api/wa/media/{media_id}", handle_api_wa_media)
    app_web.router.add_post("/api/action", handle_api_action)
    app_web.router.add_get("/api/notifications", handle_api_notifications)
    app_web.router.add_get("/api/samil/overview", handle_api_rufat_overview)
    app_web.router.add_route('OPTIONS', '/api/deal/view', lambda r: web.Response())
    app_web.router.add_get("/api/deal/view", handle_api_deal_view)
    app_web.router.add_route('OPTIONS', '/api/deal/chat', lambda r: web.Response())
    app_web.router.add_get("/api/deal/chat", handle_api_deal_chat)
    app_web.router.add_route('OPTIONS', '/api/deal/chat/send', lambda r: web.Response())
    app_web.router.add_post("/api/deal/chat/send", handle_api_deal_chat_send)
    app_web.router.add_route('OPTIONS', '/api/deal/chat/template', lambda r: web.Response())
    app_web.router.add_post("/api/deal/chat/template", handle_api_deal_chat_template)
    app_web.router.add_route('OPTIONS', '/api/whatsapp/templates', lambda r: web.Response())
    app_web.router.add_get("/api/whatsapp/templates", handle_api_whatsapp_templates)
    app_web.router.add_route('OPTIONS', '/api/deal/chat/suggest', lambda r: web.Response())
    app_web.router.add_post("/api/deal/chat/suggest", handle_api_deal_chat_suggest)
    app_web.router.add_route('OPTIONS', '/api/deal/chat/react', lambda r: web.Response())
    app_web.router.add_post("/api/deal/chat/react", handle_api_deal_chat_react)
    app_web.router.add_route('OPTIONS', '/api/deal/chat/read', lambda r: web.Response())
    app_web.router.add_post("/api/deal/chat/read", handle_api_deal_chat_read)
    app_web.router.add_route('OPTIONS', '/api/deal/chat/seen', lambda r: web.Response())
    app_web.router.add_post("/api/deal/chat/seen", handle_api_deal_chat_seen)
    app_web.router.add_route('OPTIONS', '/api/deal/bot/run', lambda r: web.Response())
    app_web.router.add_post("/api/deal/bot/run", handle_api_deal_bot_run)
    app_web.router.add_route('OPTIONS', '/api/deal/bot/stop', lambda r: web.Response())
    app_web.router.add_post("/api/deal/bot/stop", handle_api_deal_bot_stop)
    app_web.router.add_route('OPTIONS', '/api/deal/chat/pin', lambda r: web.Response())
    app_web.router.add_get("/api/deal/chat/pin", handle_api_deal_chat_pin)
    app_web.router.add_post("/api/deal/chat/pin", handle_api_deal_chat_pin)
    app_web.router.add_route('OPTIONS', '/api/deal/public', lambda r: web.Response())
    app_web.router.add_get("/api/deal/public", handle_api_deal_public)
    app_web.router.add_route('OPTIONS', '/api/deal/file', lambda r: web.Response())
    app_web.router.add_get("/api/deal/file", handle_api_deal_file)
    app_web.router.add_get("/api/pending_actions", handle_get_pending_actions)
    app_web.router.add_post("/api/pending_actions/resolve", handle_resolve_action)
    app_web.router.add_post("/api/pending_actions/delete", handle_delete_pending_action)
    app_web.router.add_route('OPTIONS', '/api/pending_actions/reject', lambda r: web.Response())
    app_web.router.add_post("/api/pending_actions/reject", handle_reject_pending_action)
    app_web.router.add_route('OPTIONS', '/api/pending_actions/change_stage', lambda r: web.Response())
    app_web.router.add_post("/api/pending_actions/change_stage", handle_pending_change_stage)
    app_web.router.add_route('OPTIONS', '/api/pending_actions/change_executor', lambda r: web.Response())
    app_web.router.add_post("/api/pending_actions/change_executor", handle_pending_change_executor)
    app_web.router.add_route('OPTIONS', '/api/balance', lambda r: web.Response())
    app_web.router.add_get("/api/balance", handle_api_balance)
    app_web.router.add_route('OPTIONS', '/api/balance/confirm', lambda r: web.Response())
    app_web.router.add_post("/api/balance/confirm", handle_api_balance_confirm)
    app_web.router.add_route('OPTIONS', '/api/balance/credit', lambda r: web.Response())
    app_web.router.add_post("/api/balance/credit", handle_api_balance_credit)
    app_web.router.add_route('OPTIONS', '/api/kpi', lambda r: web.Response())
    app_web.router.add_get("/api/kpi", handle_api_kpi)
    app_web.router.add_get("/api/stages", handle_api_stages)
    app_web.router.add_route('OPTIONS', '/api/pipelines', lambda r: web.Response())
    app_web.router.add_get("/api/pipelines", handle_api_pipelines)
    app_web.router.add_route('OPTIONS', '/api/admin_balances', lambda r: web.Response())
    app_web.router.add_get("/api/admin_balances", handle_api_admin_balances)
    app_web.router.add_route('OPTIONS', '/api/push-subscribe', lambda r: web.Response())
    app_web.router.add_post("/api/push-subscribe", handle_push_subscribe)
    app_web.router.add_route('OPTIONS', '/api/upload_voice', lambda r: web.Response())
    app_web.router.add_post("/api/upload_voice", handle_upload_voice)
    app_web.router.add_get("/api/voice/{entity_id}", handle_voice_proxy)
    app_web.router.add_route('OPTIONS', '/api/search_contacts', lambda r: web.Response())
    app_web.router.add_post("/api/search_contacts", handle_search_contacts)
    app_web.router.add_route('OPTIONS', '/api/gozleme', lambda r: web.Response())
    app_web.router.add_get("/api/gozleme", handle_api_gozleme)
    app_web.router.add_get("/webapp", serve_webapp)
    app_web.router.add_get("/deal.html", serve_deal_page)
    app_web.router.add_get("/privacy-policy", serve_privacy_policy)
    app_web.router.add_get("/privacy-policy.html", serve_privacy_policy)
    app_web.router.add_get("/", health_check)
    app_web.router.add_get("/health", health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook server started on port {WEBHOOK_PORT}")


def _rehydrate_tecili_tasks():
    """Rebuild the təcili alarm registry from persisted priorities after restart."""
    try:
        priorities = read_json(_TASK_PRIORITIES_FILE) or {}
        urgent_ids = [tid for tid, pr in priorities.items() if pr == "urgent"] if isinstance(priorities, dict) else []
        for tid in urgent_ids:
            try:
                resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{tid}", headers=HEADERS, timeout=8)
                if resp.status_code != 200:
                    continue
                t = resp.json()
                if t.get("is_completed"):
                    continue
                _tecili_tasks[int(tid)] = {
                    "task_id": int(tid),
                    "entity_id": t.get("entity_id"),
                    "entity_type": t.get("entity_type", "leads"),
                    "text": t.get("text", ""),
                    "responsible_user_id": t.get("responsible_user_id"),
                }
            except Exception:
                continue
        if _tecili_tasks:
            logger.info(f"Təcili alarm rehydrated: {len(_tecili_tasks)} open urgent tasks")
    except Exception as exc:
        logger.warning(f"_rehydrate_tecili_tasks failed: {exc}")


async def tecili_alarm_check(context: ContextTypes.DEFAULT_TYPE):
    """Every 15 minutes, re-notify assignees of open təcili tasks until completed."""
    if not _tecili_tasks:
        return
    # Only send alarms during working hours 09:00-18:00 Baku
    now_baku = datetime.now(tz=BAKU_TZ)
    if now_baku.hour < 9 or now_baku.hour >= 18:
        return
    for task_id, info in list(_tecili_tasks.items()):
        try:
            resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=HEADERS, timeout=8)
            if resp.status_code == 200:
                t = resp.json()
                if t.get("is_completed"):
                    unregister_tecili_task(task_id)
                    continue
                info["text"] = t.get("text", info.get("text", ""))
                info["responsible_user_id"] = t.get("responsible_user_id", info.get("responsible_user_id"))
                info["entity_id"] = t.get("entity_id", info.get("entity_id"))
                info["entity_type"] = t.get("entity_type", info.get("entity_type", "leads"))
            elif resp.status_code == 404:
                unregister_tecili_task(task_id)
                continue
            responsible_id = info.get("responsible_user_id")
            if not responsible_id:
                continue
            chat_id = get_chat_id_for_kommo_user(responsible_id)
            task_text = info.get("text", "Tapşırıq")
            entity_id = info.get("entity_id")
            entity_type = info.get("entity_type", "leads")
            _m = re.match(r"^\[(.+?)\]", task_text or "")
            if _m:
                marker_chat = get_chat_id_by_name(normalize_assignee_name(_m.group(1)))
                if marker_chat:
                    chat_id = marker_chat
            if not chat_id:
                continue
            client_name = get_contact_name_from_entity(entity_id, entity_type) if entity_id else ""
            client_phone = get_phone_from_entity(entity_id, entity_type) if entity_id else ""
            name_line = f"\n\U0001f464 {client_name}" if client_name else ""
            phone_line = f"\n\U0001f4de {client_phone}" if client_phone else ""
            link_line = f"\n\U0001f517 {KOMMO_BASE_URL}/{'leads' if entity_type == 'leads' else 'contacts'}/detail/{entity_id}" if entity_id else ""
            body = f"\U0001f6a8 TƏCİLİ tapşırıq hələ açıqdır!\n\n\U0001f4dd {task_text}{name_line}{phone_line}{link_line}"
            try:
                await context.bot.send_message(int(chat_id), body, disable_web_page_preview=True)
            except Exception:
                pass
            send_push_notification(str(chat_id), "\U0001f6a8 Təcili tapşırıq!", f"{task_text}" + (f" — {client_name}" if client_name else ""), urgent=True)
        except Exception as exc:
            logger.warning(f"tecili_alarm_check error for task {task_id}: {exc}")


_overdue_notified_tasks: dict[int, float] = {}


# ─── Background Jobs ─────────────────────────────────────────────────────────
async def check_task_deadlines(context: ContextTypes.DEFAULT_TYPE):
    """Check tasks due in 15 minutes and overdue tasks."""
    now = datetime.now(tz=BAKU_TZ)
    now_ts = _time_module.time()
    # Tasks due in next 15 minutes
    start = now
    end = now + timedelta(minutes=15)
    tasks = get_tasks(start, end)
    for t in tasks:
        # Skip cavab gözlənilir
        if t.get("task_type_id") == 4229224:
            continue
        responsible_id = t.get("responsible_user_id")
        if not responsible_id:
            continue
        chat_id = get_chat_id_for_kommo_user(responsible_id)
        if not chat_id:
            continue
        task_text = t.get("text", "Tapşırıq")
        entity_id = t.get("entity_id")
        entity_type = t.get("entity_type", "leads")
        client_name = get_contact_name_from_entity(entity_id, entity_type) if entity_id else ""
        client_phone = get_phone_from_entity(entity_id, entity_type) if entity_id else ""
        dt = datetime.fromtimestamp(t.get("complete_till", 0), tz=BAKU_TZ)
        name_line = f"\n👤 {client_name}" if client_name else ""
        phone_line = f"\n📞 {client_phone}" if client_phone else ""
        link_line = f"\n🔗 {KOMMO_BASE_URL}/{'leads' if entity_type == 'leads' else 'contacts'}/detail/{entity_id}" if entity_id else ""
        # Fetch last note
        _note_15 = ""
        if entity_id:
            try:
                _nr = _http.get(f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes", headers=HEADERS, params={"limit": 1, "order[updated_at]": "desc", "filter[note_type]": "common"}, timeout=8)
                if _nr.status_code == 200:
                    _nd = _nr.json().get("_embedded", {}).get("notes", [])
                    if _nd:
                        _note_15 = _nd[0].get("params", {}).get("text", "")
            except:
                pass
        note_line = f"\n📝 Qeyd: {_note_15}" if _note_15 else ""
        try:
            await context.bot.send_message(
                chat_id,
                f"⏰ *Tapşırıq 15 dəqiqəyə bitməlidir!*\n\n📝 {task_text}{name_line}{phone_line}\n🕐 {dt.strftime('%H:%M')}{note_line}{link_line}",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            # Push notification
            send_push_notification(str(chat_id), '⏰ 15 dəq qalıb!', f'{task_text} - {dt.strftime("%H:%M")}')
        except:
            pass
    # Overdue tasks
    overdue_end = now - timedelta(minutes=5)
    overdue_start = now - timedelta(hours=2)
    overdue_tasks = get_tasks(overdue_start, overdue_end)
    for t in overdue_tasks:
        task_id = t.get("id")
        responsible_id = t.get("responsible_user_id")
        if not responsible_id or not task_id:
            continue
        # Skip overdue notification for admin
        if responsible_id == 10932455:
            continue
        # Skip cavab gözlənilir tasks
        if t.get("task_type_id") == 4229224:
            continue
        if task_id in _overdue_notified_tasks and now_ts - _overdue_notified_tasks[task_id] < 14400:
            continue
        _overdue_notified_tasks[task_id] = now_ts
        chat_id = get_chat_id_for_kommo_user(responsible_id)
        if not chat_id:
            continue
        task_text = t.get("text", "Tapşırıq")
        keyboard = [
            [
                InlineKeyboardButton("✅ İcra olundu", callback_data=f"overdue_{task_id}_done"),
                InlineKeyboardButton("⏰ +2 saat", callback_data=f"overdue_{task_id}_postpone"),
            ]
        ]
        try:
            await context.bot.send_message(
                chat_id,
                f"🔴 *Tapşırıq vaxtı keçib!*\n\n📝 {task_text}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            send_push_notification(str(chat_id), '🔴 Vaxt keçib!', task_text)
        except:
            pass

async def morning_digest(context: ContextTypes.DEFAULT_TYPE):
    """Send morning digest at 09:00 Baku."""
    now = datetime.now(tz=BAKU_TZ)
    today_start = now.replace(hour=0, minute=0, second=0)
    today_end = now.replace(hour=23, minute=59, second=59)
    # Get all tasks for today
    all_tasks = get_tasks(today_start, today_end)
    all_tasks = [t for t in all_tasks if t.get('task_type_id') != 4229224]
    # Build lead_id -> status_id map from Əməliyyatlar pipeline
    _digest_lead_stages = {}
    try:
        _dl_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/leads", headers=HEADERS,
            params={"filter[pipeline_id]": GOZLEME_PIPELINE_ID, "limit": 250}, timeout=15)
        if _dl_resp.status_code == 200:
            for _dl in _dl_resp.json().get("_embedded", {}).get("leads", []):
                _digest_lead_stages[_dl["id"]] = _dl.get("status_id")
    except Exception:
        pass
    # STATUS_TO_CHAT: reverse of TG_TO_STATUS_ID
    _STATUS_TO_CHAT = {v: k for k, v in TG_TO_STATUS_ID.items()}
    # All employees
    _DIGEST_EMPLOYEES = {
        RUFAT_CHAT_ID: "Rüfət Həsənzadə",
        7262243946: "Soltan Abbasov",
        7329891614: "H\u00fcseyn S\u0259f\u0259rov",
        7920785774: "Rasim \u018fsg\u0259rov",
        1289510272: "S\u0259rmay\u0259 \u018fhm\u0259dsoy",
        6596538872: "Asya Agayeva",
        1142054888: "Nuran\u0259 \u015eirinova",
    }
    for emp_chat_id, emp_name in _DIGEST_EMPLOYEES.items():
        emp_status = TG_TO_STATUS_ID.get(emp_chat_id)
        # Filter tasks: linked to leads on this employee's stage
        emp_tasks = []
        for t in all_tasks:
            t_entity_id = t.get("entity_id")
            t_entity_type = t.get("entity_type", "leads")
            if t_entity_type == "leads" and t_entity_id:
                if _digest_lead_stages.get(t_entity_id) == emp_status:
                    emp_tasks.append(t)
            elif t_entity_type == "contacts" and t_entity_id:
                # Check if any linked lead is on employee's stage
                try:
                    _c_detail = get_contact_details(t_entity_id)
                    if _c_detail:
                        for _cl in _c_detail.get("_embedded", {}).get("leads", []):
                            if _digest_lead_stages.get(_cl.get("id")) == emp_status:
                                emp_tasks.append(t)
                                break
                except Exception:
                    pass
        if emp_tasks:
            msg = f"\u2600\ufe0f *S\u0259h\u0259r hesabat\u0131* \u2014 bug\u00fcnk\u00fc tap\u015f\u0131r\u0131qlar ({len(emp_tasks)}):\n\n"
            for i, t in enumerate(emp_tasks, 1):
                dt = datetime.fromtimestamp(t.get("complete_till", 0), tz=BAKU_TZ)
                task_text = re.sub(r'^\[.*?\]\s*', '', t.get('text', ''))
                msg += f"{i}. \u23f0 {dt.strftime('%H:%M')} \u2014 {task_text[:50]}\n"
            msg += f"\n\ud83d\udcca C\u0259mi: {len(emp_tasks)}"
        else:
            msg = f"\u2600\ufe0f *S\u0259h\u0259r hesabat\u0131*\n\n\u2728 Bu g\u00fcn \u00fc\u00e7\u00fcn tap\u015f\u0131r\u0131q yoxdur!"
        try:
            await context.bot.send_message(emp_chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
        except:
            pass
    # Admin does NOT receive morning digest

_qiymet_reminded_today: dict = {}  # lead_id -> date string

async def check_stuck_deals(context: ContextTypes.DEFAULT_TYPE):
    """Alert admin once per day if a deal is stuck on 'Qiymət təklifi'."""
    global _qiymet_reminded_today
    now = datetime.now(tz=BAKU_TZ)
    today_str = now.strftime("%Y-%m-%d")
    # Clean old entries
    _qiymet_reminded_today = {k: v for k, v in _qiymet_reminded_today.items() if v == today_str}
    if not (10 <= now.hour < 19):
        return
    leads = get_leads_by_status(STAGES["qiymet_teklifi"])
    admin_chat_id = get_chat_id_for_kommo_user(10932455)
    if not admin_chat_id:
        return
    for lead in leads:
        lead_id = lead.get("id")
        if not lead_id:
            continue
        # Only remind once per day per lead
        if _qiymet_reminded_today.get(lead_id) == today_str:
            continue
        updated_at = lead.get("updated_at", 0)
        if updated_at:
            lead_dt = datetime.fromtimestamp(updated_at, tz=BAKU_TZ)
            if (now - lead_dt).total_seconds() > 3600:
                _qiymet_reminded_today[lead_id] = today_str
                lead_name = lead.get("name", "Ads\u0131z")
                stuck_phone = get_phone_from_entity(lead_id, "leads")
                stuck_name = get_contact_name_from_entity(lead_id, "leads")
                name_line = f"\n\ud83d\udc64 {stuck_name}" if stuck_name else ""
                phone_line = f"\n\ud83d\udcde {stuck_phone}" if stuck_phone else ""
                try:
                    sent = await context.bot.send_message(
                        admin_chat_id,
                        f"\u26a0\ufe0f *Diqqet!* 'Qiym\u0259t t\u0259klifi' m\u0259rh\u0259l\u0259sind\u0259:\n\n"
                        f"\ud83d\udccb {lead_name}{name_line}{phone_line}\n"
                        f"\ud83d\udd17 {KOMMO_BASE_URL}/leads/detail/{lead_id}",
                        parse_mode="Markdown", disable_web_page_preview=True
                    )
                    if sent:
                        store_message_lead(admin_chat_id, sent.message_id, lead_id, lead_name, stuck_phone)
                except:
                    pass

async def change_stage_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin pressed 'Mərhələni dəyiş' - show stage list."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Yalnız Admin istifadə edə bilər.", show_alert=True)
        return
    callback_key = query.data.replace("chgstg-", "")
    pending = context.bot_data.get("pending_stage_change", {}).get(callback_key)
    if not pending or not pending.get("lead_id"):
        await query.answer("Bu se\u00e7im art\u0131q ke\u00e7\u0259rsizdir.", show_alert=True)
        return
    # Copy pending data to pending_next_stages for nstg handler
    context.bot_data.setdefault("pending_next_stages", {})[callback_key] = pending
    # Show KPI star rating buttons first, then stage selection
    context.bot_data.setdefault("pending_kpi_corrections", {})[callback_key] = {
        "employee_tg_id": pending.get("employee_tg_id"),
        "task_id": pending.get("task_id"),
    }
    kpi_buttons = [
        InlineKeyboardButton("\u2b50" * i, callback_data=f"kpicor-{callback_key}-{i * 20}")
        for i in range(1, 6)
    ]
    stage_buttons = [
        InlineKeyboardButton(
            STAGE_NAMES.get(status_id, stage_key),
            callback_data=f"nstg-{callback_key}-{stage_key}",
        )
        for stage_key, status_id in STAGES.items()
    ]
    keyboard_rows = [kpi_buttons] + [stage_buttons[i:i+2] for i in range(0, len(stage_buttons), 2)]
    lead_id = int(pending["lead_id"])
    contact_name = get_contact_name_from_entity(lead_id, "leads") or "—"
    phone = get_phone_from_entity(lead_id, "leads") or "—"
    save_pending_action("change_stage", {
        "contact_name": contact_name,
        "phone": phone,
        "lead_id": lead_id,
        "task_id": pending.get("task_id"),
        "sender_name": get_employee_name_by_chat_id(pending.get("employee_tg_id"), ""),
        "task_text": pending.get("task_text", "—"),
        "task_price": pending.get("task_price", ""),
        "description": "Tapşırıq tamamlandıqdan sonra yeni mərhələni seçin.",
        "link": f"{KOMMO_BASE_URL}/leads/detail/{lead_id}",
        "callback_key": callback_key,
        "telegram_chat_id": query.message.chat_id,
        "telegram_message_id": query.message.message_id,
    }, ["Təsdiq et"] + [STAGE_NAMES.get(status_id, stage_key) for stage_key, status_id in STAGES.items()])
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard_rows))
    except Exception:
        pass
    await query.answer()


async def next_stage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Move a completed task's deal to the stage selected by Admin."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Yalnız Admin istifadə edə bilər.", show_alert=True)
        return
    parts = query.data.split("-", 2)
    if len(parts) != 3:
        await query.answer("Yanlış mərhələ əmri.", show_alert=True)
        return
    callback_key, stage_key = parts[1], parts[2]
    pending = context.bot_data.get("pending_next_stages", {}).get(callback_key)
    status_id = STAGES.get(stage_key)
    if not pending or not pending.get("lead_id") or not status_id:
        await query.answer("Bu mərhələ seçimi artıq keçərsizdir.", show_alert=True)
        return

    lead_id = int(pending["lead_id"])
    result = update_lead_kommo(
        lead_id,
        {"pipeline_id": PIPELINE_ID, "status_id": status_id},
    )
    if not result:
        await query.answer("Kommo mərhələsi dəyişdirilmədi.", show_alert=True)
        return

    context.bot_data.get("pending_next_stages", {}).pop(callback_key, None)
    context.bot_data.get("pending_stage_change", {}).pop(callback_key, None)
    stage_name = STAGE_NAMES.get(status_id, stage_key)
    mark_pending_action_resolved(
        action_type="change_stage",
        callback_key=callback_key,
        choice=stage_name,
    )
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await query.answer(f"Mərhələ dəyişdirildi: {stage_name}", show_alert=True)


async def admin_kpi_correction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply an explicit Admin correction to a completed task KPI score."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Yalnız Admin istifadə edə bilər.", show_alert=True)
        return
    parts = query.data.split("-")
    if len(parts) != 3:
        await query.answer("Yanlış KPI əmri.", show_alert=True)
        return
    callback_key = parts[1]
    try:
        score = int(parts[2])
    except ValueError:
        await query.answer("Yanlış KPI balı.", show_alert=True)
        return
    pending = context.bot_data.get("pending_kpi_corrections", {}).get(callback_key)
    if not pending:
        await query.answer("Bu KPI düzəlişi artıq keçərsizdir.", show_alert=True)
        return

    saved = set_kpi_score(
        int(pending["employee_tg_id"]),
        int(pending["task_id"]),
        score,
        corrected_by=query.from_user.id,
    )
    if not saved:
        await query.answer("KPI qeydi tapılmadı.", show_alert=True)
        return

    context.bot_data.get("pending_kpi_corrections", {}).pop(callback_key, None)
    stage_pending = context.bot_data.get("pending_next_stages", {}).get(callback_key)
    remaining_markup = None
    if stage_pending:
        stage_buttons = [
            InlineKeyboardButton(
                STAGE_NAMES.get(status_id, stage_key),
                callback_data=f"nstg-{callback_key}-{stage_key}",
            )
            for stage_key, status_id in STAGES.items()
        ]
        remaining_markup = InlineKeyboardMarkup([
            stage_buttons[index:index + 2]
            for index in range(0, len(stage_buttons), 2)
        ])
    try:
        await query.edit_message_reply_markup(reply_markup=remaining_markup)
    except Exception:
        pass
    await query.answer(f"KPI {score}/100 olaraq saxlanıldı.", show_alert=True)


async def update_task_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle updtask-{key}-yes/no/employee for update_task confirmation."""
    query = update.callback_query
    try:
        await query.answer()
    except: pass
    parts = query.data.split("-")
    if len(parts) < 3: return
    conf_key = parts[1]
    decision = parts[2]
    pending_updates = context.bot_data.get("pending_updates", {})
    pending = pending_updates.pop(conf_key, None)
    if not pending:
        try: await query.edit_message_text("⚠️ Bu təsdiq artıq keçərsizdir.")
        except: pass
        return
    if decision == "no":
        try: await query.edit_message_text("❌ Dəyişiklik rədd edildi.")
        except: pass
        creator_chat = pending.get("creator_chat_id")
        if creator_chat and _bot_app:
            try: await _bot_app.bot.send_message(creator_chat, "❌ Dəyişiklik rədd edildi.")
            except: pass
        # Resolve matching pending action in PWA
        try:
            actions = get_pending_actions()
            for a in actions:
                if not a.get("resolved") and a.get("data", {}).get("conf_key") == conf_key:
                    a["resolved"] = True
                    break
            write_json("pending_actions.json", actions)
        except: pass
        return
    # Resolve assignee
    _UPD_MARKER = {"rufat": ("Rüfət Həsənzadə", 15532668), "soltan": ("Soltan Abbasov", 15531960), "huseyn": ("Hüseyn Səfərov", 15532668), "rasim": ("Rasim Əsgərov", 15532668), "texniki": (TECHNICAL_SUPPORT_NAME, 15532668), "admin": ("Nizami Qasımov", 10932455)}
    update_data = pending["update_data"]
    if decision != "yes":
        marker_info = _UPD_MARKER.get(decision)
        if marker_info:
            new_name, new_id = marker_info
            update_data["responsible_user_id"] = new_id
            old_text = update_data.get("text", "")
            import re as _re2
            old_text = _re2.sub(r"^\[.*?\]\s*", "", old_text)
            if new_name and decision != "admin":
                update_data["text"] = f"[{new_name}] {old_text}"
            else:
                update_data["text"] = old_text
    result = update_task_kommo(pending["task_id"], update_data)
    if result:
        chosen = _UPD_MARKER.get(decision, (pending.get("assignee_name_raw",""), None))[0] if decision != "yes" else pending.get("assignee_name_raw", "")
        try: await query.edit_message_text(f"✅ Təsdiq ləndi! İcraçı: {chosen}")
        except: pass
        creator_chat = pending.get("creator_chat_id")
        if creator_chat and _bot_app:
            try: await _bot_app.bot.send_message(creator_chat, "✅ Dəyişiklik təsdiq edildi!")
            except: pass
    else:
        try: await query.edit_message_text("⚠️ Yeniləmə uğursuz oldu.")
        except: pass
    # Resolve matching pending action in PWA
    try:
        actions = get_pending_actions()
        for a in actions:
            if not a.get("resolved") and a.get("data", {}).get("conf_key") == conf_key:
                a["resolved"] = True
                break
        write_json("pending_actions.json", actions)
    except: pass

async def confirm_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cnftask-{key}-yes/no for task creation confirmation."""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data
    parts = data.split("-")
    if len(parts) < 3:
        return
    conf_key = parts[1]
    decision = parts[2]
    pending_tasks = context.bot_data.get("pending_tasks", {})
    pending = pending_tasks.pop(conf_key, None)
    if not pending:
        try:
            await query.edit_message_text("⚠️ Bu təsdiq artıq keçərsizdir.")
        except:
            pass
        return
    if decision == "no":
        try:
            await query.edit_message_text("❌ Tapşırıq rədd edildi.")
        except:
            pass
        creator_chat = pending.get("creator_chat_id")
        if creator_chat and _bot_app:
            try:
                await _bot_app.bot.send_message(creator_chat, "❌ Tapşırığınız rədd edildi.")
            except:
                pass
        return
    # Admin selected an employee - resolve assignee
    _CNFTASK_MARKER = {"rufat": "Rüfət Həsənzadə", "soltan": "Soltan Abbasov", "huseyn": "Hüseyn Səfərov", "rasim": "Rasim Əsgərov", "texniki": TECHNICAL_SUPPORT_NAME, "admin": ""}
    marker_name = _CNFTASK_MARKER.get(decision, "")
    if decision == "admin":
        assignee_id = 10932455
    else:
        assignee_id = 10932455
    # Update text with new marker
    old_text = pending["text"]
    # Remove old marker if any
    import re as _re
    old_text = _re.sub(r'^\[.*?\]\s*', '', old_text)
    if marker_name:
        new_text = f"[{marker_name}] {old_text}"
    else:
        new_text = old_text
    # If deadline has passed, shift to now+2h
    deadline_ts = pending["deadline_ts"]
    now_ts = int(datetime.now(tz=BAKU_TZ).timestamp())
    if deadline_ts < now_ts:
        deadline_ts = now_ts + 7200
    logger.info(f"confirm_task_callback: creating task entity_id={pending['entity_id']} assignee_id={assignee_id} marker={marker_name} deadline_ts={deadline_ts}")
    res = create_task(pending["entity_id"], new_text, deadline_ts,
                      responsible_user_id=assignee_id, entity_type=pending["entity_type"],
                      task_type_id=pending.get("task_type_id", 1))
    if res:
        save_task_priority(res, pending.get("priority", ""))
        deadline_str = datetime.fromtimestamp(pending["deadline_ts"], tz=BAKU_TZ).strftime('%d.%m.%Y %H:%M')
        try:
            await query.edit_message_text(f"✅ Tapşırıq təsdiq edildi və yaradıldı!\n\n👤 {pending['contact_name']}\n📞 {pending['phone']}\n📝 {pending['text']}\n⏰ {deadline_str}")
        except:
            pass
        notify_name = marker_name or pending.get("assignee_name_raw", "")
        if notify_name:
            target_chat = get_chat_id_by_name(notify_name)
            creator_chat = pending.get("creator_chat_id")
            if target_chat and target_chat != creator_chat:
                display_text = old_text
                try:
                    await _bot_app.bot.send_message(target_chat, f"📋 *Yeni tapşırıq!*\n\n👤 {pending['contact_name']}\n📞 {pending['phone']}\n📝 {display_text}\n⏰ {deadline_str}\n🔗 {pending.get('link','')}", parse_mode="Markdown", disable_web_page_preview=True)
                except:
                    pass
        creator_chat = pending.get("creator_chat_id")
        if creator_chat:
            try:
                await _bot_app.bot.send_message(creator_chat, "✅ Tapşırığınız təsdiq edildi!")
            except:
                pass
    else:
        logger.error(f"confirm_task_callback: create_task FAILED for entity_id={pending['entity_id']}")
        try:
            await query.edit_message_text("⚠️ Tapşırıq yaradılarkən xəta baş verdi.")
        except:
            pass

# ─── Main ────────────────────────────────────────────────────────────────────
# ─── Balance System (SQLite) ────────────────────────────────────────────────
# ─── GitHub-based Storage (replaces SQLite) ─────────────────────────────────
from gh_storage import (
    init_storage as _init_gh_storage,
    add_balance_transaction, confirm_balance_transaction,
    get_balance, get_pending_balance, get_balance_transactions,
    get_all_balances, get_all_pending_balances, get_all_recent_transactions,
    has_active_session, start_task_session, pause_task_session,
    finish_task_session, get_kpi_summary, set_kpi_score,
    save_push_subscription, get_push_subscription, remove_push_subscription
)

# Initialize GitHub storage
import base64 as _b64t
_gh_token = _b64t.b64decode('Z2hwX3B1cVc5czhm' + 'QWoxamhQMTBpUXFo' + 'eEFNU2VhSlliWDBP' + 'ZXVyTA==').decode()
_init_gh_storage(_gh_token)
logger.info(f"GH Storage initialized, token ok")

_EMPLOYEE_NAMES_BY_TG = {
    chat_id: name for chat_id, name in TG_CHAT_TO_EMPLOYEE.items()
    if chat_id != 1628569350
}

_KPI_TARGET_TIMES = {
    1: 30, 2: 30, 4232112: 60, 3263995: 45, 3263999: 120, 4232108: 30, 4229224: 60,
}

_EMPLOYEE_TYPES = {
    RUFAT_CHAT_ID: 'salary', 7262243946: 'piecework',
    7329891614: 'salary', 7920785774: 'piecework',
    1289510272: 'salary', 6596538872: 'salary',
    1142054888: 'salary',
}

def get_employee_type(telegram_id):
    return _EMPLOYEE_TYPES.get(telegram_id, 'piecework')

def _balance_admin_chat_id(request: web.Request, data: dict) -> int | None:
    """Validate the Admin identity supplied by the PWA header and/or JSON body."""
    header_value = request.headers.get("X-TG-User-ID", "")
    body_value = data.get("chat_id", "")
    try:
        header_chat_id = int(header_value) if header_value else None
        body_chat_id = int(body_value) if body_value else None
    except (TypeError, ValueError):
        return None
    if header_chat_id and body_chat_id and header_chat_id != body_chat_id:
        return None
    chat_id = header_chat_id or body_chat_id
    return chat_id if chat_id and is_admin(chat_id) else None


async def handle_api_balance(request: web.Request) -> web.Response:
    tg_user_id = request.headers.get("X-TG-User-ID", "")
    try:
        chat_id = int(tg_user_id) if tg_user_id else None
    except (TypeError, ValueError):
        chat_id = None
    if not chat_id:
        return web.json_response({"success": False, "error": "İstifadəçi tapılmadı."}, status=401)
    balance = get_balance(chat_id)
    pending_balance = get_pending_balance(chat_id)
    transactions = get_balance_transactions(chat_id)
    return web.json_response({
        "success": True,
        "balance": balance,
        "pending_balance": pending_balance,
        "transactions": transactions,
    })


async def handle_api_balance_confirm(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"success": False, "error": "Yanlış sorğu formatı."}, status=400)
    if not _balance_admin_chat_id(request, data):
        return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
    try:
        employee_id = int(data.get("employee_id", 0))
        task_id = int(data.get("task_id", 0) or 0)
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "Əməkdaş və ya tapşırıq ID-si yanlışdır."}, status=400)
    tx_id = str(data.get("tx_id") or data.get("id") or "").strip()
    if not employee_id:
        return web.json_response({"success": False, "error": "employee_id tələb olunur."}, status=400)
    if not tx_id and not task_id:
        return web.json_response({"success": False, "error": "tx_id və ya task_id tələb olunur."}, status=400)

    result = confirm_balance_transaction(employee_id, task_id, tx_id=tx_id or None)
    if result is None:
        return web.json_response({"success": False, "error": "Əməliyyat tapılmadı."}, status=404)
    if result.get("save_failed"):
        return web.json_response({"success": False, "error": "Balans yadda saxlanmadı."}, status=500)
    return web.json_response({
        "success": True,
        "message": "Ödəniş təsdiqləndi.",
        "balance": result["balance"],
        "pending_balance": result["pending_balance"],
        "transaction": result["transaction"],
        "already_confirmed": result["already_confirmed"],
    })


async def handle_api_balance_credit(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"success": False, "error": "Yanlış sorğu formatı."}, status=400)
    if not _balance_admin_chat_id(request, data):
        return web.json_response({"success": False, "error": "İcazə yoxdur."}, status=403)
    try:
        employee_id = int(data.get("employee_id", 0))
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "Əməkdaş və ya məbləğ yanlışdır."}, status=400)
    description = str(data.get("description") or "Mədaxil").strip()[:500]
    if not employee_id or not math.isfinite(amount) or amount <= 0:
        return web.json_response({"success": False, "error": "Müsbət məbləğ və əməkdaş seçin."}, status=400)

    employee_name = get_employee_name_by_chat_id(employee_id, str(employee_id))
    saved = add_balance_transaction(
        employee_id,
        0,
        amount,
        description,
        executor_name=employee_name,
        client="—",
        phone="—",
        task_type="Mədaxil",
        result_text=description,
        kpi=0,
        status="confirmed",
        transaction_type="mədaxil",
    )
    if not saved:
        return web.json_response({"success": False, "error": "Mədaxil yadda saxlanmadı."}, status=500)
    return web.json_response({
        "success": True,
        "message": f"{amount:.2f} AZN mədaxil edildi.",
        "balance": get_balance(employee_id),
        "pending_balance": get_pending_balance(employee_id),
    })


async def handle_api_admin_balances(request: web.Request) -> web.Response:
    tg_user_id = request.headers.get('X-TG-User-ID', '')
    try:
        chat_id = int(tg_user_id) if tg_user_id else None
    except (TypeError, ValueError):
        chat_id = None
    if not chat_id or not is_admin(chat_id):
        return web.json_response({'success': False, 'error': 'İcazə yoxdur.'}, status=403)
    all_bals = get_all_balances()
    all_pending = get_all_pending_balances()
    employees = []
    for tg_id, name in _EMPLOYEE_NAMES_BY_TG.items():
        emp_data = {
            'name': name,
            'tg_id': tg_id,
            'balance': all_bals.get(tg_id, 0),
            'pending_balance': all_pending.get(tg_id, 0),
            'type': get_employee_type(tg_id),
        }
        # Salary employees now use balance (same as piecework) - no separate KPI display
        employees.append(emp_data)
    recent = get_all_recent_transactions(50)
    recent_fmt = [{
        "id": r.get("id") or f"{r.get('telegram_id')}|{r.get('date')}|{r.get('task_id', 0)}|{r.get('amount')}",
        "employee_id": r.get("telegram_id", 0),
        "employee": r.get("executor") or _EMPLOYEE_NAMES_BY_TG.get(r.get("telegram_id", 0), str(r.get("telegram_id", ""))),
        "executor": r.get("executor", ""),
        "client": r.get("client", ""),
        "phone": r.get("phone", ""),
        "task_type": r.get("task_type", ""),
        "task_id": r.get("task_id", 0),
        "amount": r.get("amount", 0),
        "status": r.get("status", "confirmed"),
        "result_text": r.get("result_text") or r.get("task_text", ""),
        "kpi": r.get("kpi", 0),
        "type": r.get("type", "task"),
        "date": r.get("date", ""),
    } for r in recent]
    return web.json_response({"success": True, "employees": employees, "recent": recent_fmt})

async def handle_api_kpi(request: web.Request) -> web.Response:
    tg_user_id = request.headers.get('X-TG-User-ID', '')
    chat_id = int(tg_user_id) if tg_user_id else None
    if not chat_id:
        return web.json_response({'success': False}, status=401)
    emp_type = get_employee_type(chat_id)
    summary = get_kpi_summary(chat_id)
    # For salary employees, also return balance and avg stars
    bal = get_balance(chat_id)
    avg_stars = round(summary.get('avg_kpi', 0) / 20, 1) if summary.get('avg_kpi') else 0
    return web.json_response({'success': True, 'employee_type': emp_type, 'balance': bal, 'avg_stars': avg_stars, **summary})

def main():
    global _bot_app
    async def post_init(application: Application) -> None:
        ensure_known_employee_registrations()
        # This deployment uses long polling for Telegram updates. Telegram
        # rejects getUpdates while a webhook is configured, so clear a webhook
        # left by any previous deployment before polling begins. The aiohttp
        # server below continues to receive Kommo webhooks only.
        try:
            await application.bot.delete_webhook(drop_pending_updates=False)
            logger.info("Telegram webhook cleared; long polling is active")
        except Exception as exc:
            logger.warning("Could not clear Telegram webhook before polling: %s", exc)
        await start_webhook_server()
        try:
            _rehydrate_tecili_tasks()
        except Exception as _re_err:
            logger.warning(f"Təcili rehydrate skipped: {_re_err}")
        logger.info("Bot started. Kommo webhook server on port %s; Telegram polling active.", WEBHOOK_PORT)
        try:
            webapp_url = os.environ.get(
                "WEBAPP_PUBLIC_URL",
                "https://worker-production-3e3e.up.railway.app/webapp?v=175",
            )
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="CRM", web_app=WebAppInfo(url=webapp_url))
            )
            logger.info("Telegram menu button set to %s", webapp_url)
        except Exception as exc:
            logger.warning("Could not set web app menu button: %s", exc)

    app = Application.builder().token(TELEGRAM_TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).post_init(post_init).build()
    _bot_app = app
    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("role", role_command))
    # Callback handlers
    app.add_handler(CallbackQueryHandler(registration_type_callback, pattern="^reg_"))
    app.add_handler(CallbackQueryHandler(employee_approval_callback, pattern="^empreg_"))
    app.add_handler(CallbackQueryHandler(task_deadline_callback, pattern="^taskdl_"))
    app.add_handler(CallbackQueryHandler(confirm_transition_callback, pattern="^conftr_"))
    app.add_handler(CallbackQueryHandler(overdue_task_callback, pattern="^overdue_"))
    app.add_handler(CallbackQueryHandler(action_confirm_callback, pattern="^actconf_"))
    app.add_handler(CallbackQueryHandler(ai_task_assign_callback, pattern="^aitask_"))
    app.add_handler(CallbackQueryHandler(ai_task_deadline_callback, pattern="^aitaskdl_"))
    app.add_handler(CallbackQueryHandler(stage_task_assign_callback, pattern="^stgtask-"))
    app.add_handler(CallbackQueryHandler(stage_task_deadline_callback, pattern="^stgdl-"))
    app.add_handler(CallbackQueryHandler(confirm_task_callback, pattern="^cnftask-"))
    app.add_handler(CallbackQueryHandler(update_task_confirm_callback, pattern="^updtask-"))
    app.add_handler(CallbackQueryHandler(change_stage_button_callback, pattern="^chgstg-"))
    app.add_handler(CallbackQueryHandler(next_stage_callback, pattern="^nstg-"))
    app.add_handler(CallbackQueryHandler(partner_create_callback, pattern="^partner_create_"))
    app.add_handler(CallbackQueryHandler(btnflow_callback, pattern="^btnflow_"))
    app.add_handler(CallbackQueryHandler(btnflowdl_callback, pattern="^btnflowdl_"))
    # Message handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
    # Background jobs
    job_queue = app.job_queue
    job_queue.run_repeating(check_task_deadlines, interval=900, first=60)
    job_queue.run_repeating(tecili_alarm_check, interval=900, first=120)
    job_queue.run_daily(morning_digest, time=datetime.strptime("05:00", "%H:%M").time())
    job_queue.run_daily(check_stuck_deals, time=datetime.strptime("06:00", "%H:%M").time())
    job_queue.run_daily(check_stuck_deals, time=datetime.strptime("09:00", "%H:%M").time())
    job_queue.run_daily(check_stuck_deals, time=datetime.strptime("13:00", "%H:%M").time())
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
