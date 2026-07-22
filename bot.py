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
import json
import math
import logging
import requests
import subprocess
import glob
import traceback
import asyncio
import uuid
import time as _time_module
from datetime import datetime, timedelta, timezone
from openai import OpenAI
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
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
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8770145286:AAHB60HD8L1bvMaWVys2OPduPrp_ppkxTXA")
KOMMO_TOKEN = os.environ.get("KOMMO_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImp0aSI6IjNjZDgwYzY0NzM2ODFlMDY4ZTliMTkzZWE2ZjM4NTQ1NGZlNzNkNjRlZjFkNDJiOWQ1ZjkxZDRiOTc0ZGY2MjIzODA0NTU1OWU2YjdkOTI3In0.eyJhdWQiOiJjMjFiNjBhOC00Y2I0LTRjYWQtOGU5NC03ZmI0NTIyMGU4OWMiLCJqdGkiOiIzY2Q4MGM2NDczNjgxZTA2OGU5YjE5M2VhNmYzODU0NTRmZTczZDY0ZWYxZDQyYjlkNWY5MWQ0Yjk3NGRmNjIyMzgwNDU1NTllNmI3ZDkyNyIsImlhdCI6MTc4MjkwNjc3MiwibmJmIjoxNzgyOTA2NzcyLCJleHAiOjE4NjE4MzM2MDAsInN1YiI6IjEwOTMyNDU1IiwiZ3JhbnRfdHlwZSI6IiIsImFjY291bnRfaWQiOjMyNTI0MzU5LCJiYXNlX2RvbWFpbiI6ImtvbW1vLmNvbSIsInZlcnNpb24iOjIsInNjb3BlcyI6WyJjcm0iLCJmaWxlcyIsImZpbGVzX2RlbGV0ZSIsIm5vdGlmaWNhdGlvbnMiLCJwdXNoX25vdGlmaWNhdGlvbnMiLCJ1c2Vyc19hY3RpdmF0ZSIsInVzZXJzX2FkZCIsInVzZXJzX2RlYWN0aXZhdGUiXSwiaGFzaF91dWlkIjoiMmJjODBmNTItNmRhMC00YTkyLWJkODMtZmUwYTVhZWQ3YTY2IiwiYXBpX2RvbWFpbiI6ImFwaS1nLmtvbW1vLmNvbSJ9.fUU7hoGZzSzS0gd5yXY26gut46gYjYDWvtQ1snGVgm2YU6D2FqpUH4U46ef36YHirRaas7DB6an5aPCKSzqXU5D7OLsFxhj_y3PASLE-b1-sDVXVFPO1HiW3EPn8CTn9IHxSt-MKBPjQs49a9ldV5kFRyLOdjr91IH3lHvmwp_qKgWIN3y5RD4ogwH755fpuXL3bMo-zwTc4_zx0FPj2mP8G0MsvwlvxKzlEXx7kZW5uQ8sXxDhHYTGn1bd5DWac-41MeNswGFTCgnHBITCQsSEOgedZb4EvfL9SXlNSJZpXU__khNg6YCC-slE3jZjXIWHXHFMdaUfX5I8IaPnQGA")
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
ADMIN_CHAT_ID = 1628569350
ADMIN_KOMMO_USER_ID = 10932455
TECHNICAL_SUPPORT_NAME = "Texniki Dəstək"
_UPD_MARKER = {"Şamil": ("Şamil Əliyev", 15532668), "Soltan": ("Soltan Abbasov", 15531960), "Hüseyn": ("Hüseyn Səfərov", 15532668), "Rasim": ("Rasim Əsgərov", 15532668), "Texniki": ("Texniki Dəstək", 15532668), "Özüm": ("Nizami Qasımov", 10932455)}

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
KOMMO_USERS = {
    10932455: "Nizami Qasımov",
    15531960: "Soltan Abbasov",
    15532668: "Sahə Meneceri",
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
    4232112: "Texniki tapşırıq",
    4232108: "Import",
}

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bot")

# ─── User Registration Storage ───────────────────────────────────────────────
USER_DB_FILE = os.environ.get("USER_DB_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json"))

def load_users() -> dict:
    if os.path.exists(USER_DB_FILE):
        with open(USER_DB_FILE, "r") as f:
            return json.load(f)
    return {}

def save_users(data: dict):
    with open(USER_DB_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_chat_id_for_kommo_user(kommo_user_id: int) -> int | None:
    users = load_users()
    for chat_id_str, info in users.items():
        if info.get("kommo_user_id") == kommo_user_id:
            return int(chat_id_str)
    return None

def get_kommo_user_id_for_chat(chat_id: int) -> int | None:
    users = load_users()
    info = users.get(str(chat_id))
    if info:
        return info.get("kommo_user_id")
    return None

def is_admin(chat_id: int) -> bool:
    """Return whether a Telegram chat belongs to the Admin account."""
    try:
        if int(chat_id) == ADMIN_CHAT_ID:
            return True
    except (TypeError, ValueError):
        pass
    return get_kommo_user_id_for_chat(chat_id) == ADMIN_KOMMO_USER_ID


_PENDING_ACTIONS_FILE = "pending_actions.json"
_TASK_PRIORITIES_FILE = "task_priorities.json"
_VALID_TASK_PRIORITIES = {"urgent", "medium", "low"}
_PENDING_EXECUTOR_NAMES = {
    "Şamil": "Şamil Əliyev",
    "Soltan": "Soltan Abbasov",
    "Hüseyn": "Hüseyn Səfərov",
    "Rasim": "Rasim Əsgərov",
    "Texniki": TECHNICAL_SUPPORT_NAME,
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
        responsible_user_id = 15532668
    return bool(create_task(
        int(lead_id),
        task_text,
        deadline_ts,
        responsible_user_id=responsible_user_id,
        entity_type="leads",
    ))


def resolve_pending_action(action_id: str, choice: str, kpi_score: int = 0) -> tuple[bool, str]:
    """Execute one persisted admin action and resolve it only after success."""
    actions = get_pending_actions()
    action = next((item for item in actions if item.get("id") == action_id), None)
    if not action:
        return False, "Sorğu tapılmadı."
    if action.get("resolved"):
        return False, "Sorğu artıq həll edilib."
    action_options = action.get("options") or []
    if choice not in action_options:
        if choice != "Təsdiq et":
            return False, "Yanlış seçim."

    action_type = action.get("type")
    action_data = action.get("data") or {}
    lead_id = action_data.get("lead_id")
    contact_name = action_data.get("contact_name") or "—"
    phone = action_data.get("phone") or "—"
    stage_name = action_data.get("stage_name") or "—"
    link = action_data.get("link") or (f"{KOMMO_BASE_URL}/leads/detail/{lead_id}" if lead_id else "")

    if action_type == "assign_executor":
        if choice == "Təsdiq et":
            result_message = "Təsdiq edildi."
            if not mark_pending_action_resolved(action_id=action_id, choice=choice):
                return False, "Sorğu bağlanmadı."
            _clear_runtime_pending_action(action)
            return True, result_message
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
                task_text = f"[{full_name}] {task_text}"
                responsible_user_id = 15532668
            # Use stored deadline or default 2h
            if action_data.get("deadline"):
                try:
                    deadline_ts = int(datetime.strptime(action_data["deadline"], "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ).timestamp())
                except:
                    deadline_ts = int((datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
            else:
                deadline_ts = int((datetime.now(tz=BAKU_TZ) + timedelta(hours=2)).timestamp())
            task_type_id = action_data.get("task_type_id")
            if not create_task(
                int(lead_id),
                task_text,
                deadline_ts,
                responsible_user_id=responsible_user_id,
                entity_type="leads",
                task_type_id=int(task_type_id) if task_type_id else None,
            ):
                return False, "Kommo-da tapşırıq yaradılmadı."
        result_message = "Sorğu ləğv edildi." if choice in ("Ləğv et", "Rədd et") else f"Tapşırıq {choice} üçün yaradıldı."

    elif action_type == "confirm_stage":
        if choice == "Təsdiq et":
            status_id = action_data.get("status_id")
            if not lead_id or not status_id:
                return False, "Mərhələ məlumatı natamamdır."
            if not update_lead_kommo(
                int(lead_id),
                {"status_id": int(status_id), "pipeline_id": PIPELINE_ID},
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
            _UPD_MARKER = {"Təsdiq et": None, "Şamil": ("Şamil Əliyev", 15532668), "Soltan": ("Soltan Abbasov", 15531960), "Hüseyn": ("Hüseyn Səfərov", 15532668), "Rasim": ("Rasim Əsgərov", 15532668), "Texniki": (TECHNICAL_SUPPORT_NAME, 15532668), "Özüm": ("Nizami Qasımov", 10932455)}
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
        # Təsdiq et = only confirm KPI, do NOT change stage (stage is changed separately via long press)
        if choice == "Təsdiq et":
            # Only apply KPI score, no stage change
            if kpi_score and action_data.get("task_id") and action_data.get("sender_name"):
                employee_tg_id = NAME_TO_CHAT.get(action_data["sender_name"])
                if employee_tg_id:
                    set_kpi_score(int(employee_tg_id), int(action_data["task_id"]), kpi_score, corrected_by=ADMIN_CHAT_ID)
            result_message = "Təsdiq edildi."
        else:
            # Explicit stage selection from long-press menu
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

    if not mark_pending_action_resolved(action_id=action_id, choice=choice):
        return False, "Əməliyyat icra olundu, lakin sorğu bağlanmadı."
    _clear_runtime_pending_action(action)
    _close_pending_telegram_message(
        action,
        f"✅ PWA-dan həll edildi: {choice}\n👤 {contact_name}\n📝 {result_message}\n"
        f"📞 {phone}\n⏰ {datetime.now(tz=BAKU_TZ).strftime('%d.%m.%Y %H:%M')}\n🔗 {link}",
    )
    return True, result_message


# Telegram identities are the source of truth for real employee names because
# several employees share the Sahə Meneceri Kommo user.
TG_CHAT_TO_EMPLOYEE = {
    1628569350: "Nizami Qasımov",
    7962757442: "Şamil Əliyev",
    7262243946: "Soltan Abbasov",
    7329891614: "Hüseyn Səfərov",
    7920785774: "Rasim Əsgərov",
    8835096199: TECHNICAL_SUPPORT_NAME,
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
    "Şamil Əliyev": 7962757442,
    "Soltan Abbasov": 7262243946,
    "Hüseyn Səfərov": 7329891614,
    "Nizami Qasımov": 1628569350,
    "Rasim Əsgərov": 7920785774,
    TECHNICAL_SUPPORT_NAME: 8835096199,
    # Keep historical task markers routable while writing the employee's real name.
    "Texniki tapşırıq": 8835096199,
    "Texniki": 8835096199,
    "Şamil": 7962757442,
    "Soltan": 7262243946,
    "Hüseyn": 7329891614,
    "Nizami": 1628569350,
    "Rasim": 7920785774,
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
_notified_task_webhooks: dict = {}  # {task_id: timestamp} - prevent duplicate webhook notifications
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

def get_contact_details(contact_id: int) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}"
    try:
        resp = _http.get(url, headers=HEADERS, params={"with": "leads"}, timeout=8)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Contact details error: {e}")
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
    entity_type = task_data.get("entity_type", "contacts")
    lead_id = entity_id if entity_type == "leads" else None
    contact = None

    if entity_id and entity_type == "contacts":
        contact = get_contact_details(int(entity_id))
        leads = (contact or {}).get("_embedded", {}).get("leads", [])
        if leads:
            lead_id = leads[0].get("id")
    elif lead_id:
        lead = get_lead_details(int(lead_id))
        contacts = (lead or {}).get("_embedded", {}).get("contacts", [])
        if contacts:
            contact = get_contact_details(int(contacts[0]["id"]))

    client_name = (contact or {}).get("name", "")
    phone = ""
    for custom_field in (contact or {}).get("custom_fields_values", []) or []:
        if custom_field.get("field_code") == "PHONE":
            values = custom_field.get("values", [])
            if values:
                phone = values[0].get("value", "")
                break

    return {
        "lead_id": int(lead_id) if lead_id else None,
        "client_name": client_name,
        "phone": phone,
        "link": f"{KOMMO_BASE_URL}/leads/detail/{lead_id}" if lead_id else "",
    }

def get_phone_from_entity(entity_id: int, entity_type: str) -> str:
    try:
        if entity_type == "leads":
            lead = get_lead_details(entity_id)
            if lead:
                contacts_emb = lead.get("_embedded", {}).get("contacts", [])
                if contacts_emb:
                    full_c = get_contact_details(contacts_emb[0]["id"])
                    if full_c:
                        for cf in (full_c.get("custom_fields_values") or []):
                            if cf.get("field_code") == "PHONE":
                                vals = cf.get("values", [])
                                if vals:
                                    return vals[0].get("value", "")
        elif entity_type == "contacts":
            full_c = get_contact_details(entity_id)
            if full_c:
                for cf in (full_c.get("custom_fields_values") or []):
                    if cf.get("field_code") == "PHONE":
                        vals = cf.get("values", [])
                        if vals:
                            return vals[0].get("value", "")
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

def create_task(entity_id: int, text: str, complete_till: int, responsible_user_id: int = None, entity_type: str = "contacts", task_type_id: int = 1) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks"
    payload = [{
        "text": text,
        "complete_till": complete_till,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "responsible_user_id": responsible_user_id or 10932455,
        "task_type_id": task_type_id,
    }]
    try:
        resp = _http.post(url, headers=HEADERS, json=payload, timeout=8)
        if resp.status_code in (200, 201):
            result = resp.json()
            try:
                created_id = result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
                if created_id:
                    import time as _time
                    _bot_created_tasks.add(int(created_id))
                    _bot_created_tasks_ts[int(created_id)] = _time.time()
                    if len(_bot_created_tasks) > 500:
                        oldest = next(iter(_bot_created_tasks))
                        _bot_created_tasks.discard(oldest)
                        _bot_created_tasks_ts.pop(oldest, None)
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

def update_lead_kommo(lead_id: int, data: dict) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}"
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

def get_leads_by_status(status_id: int) -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/leads"
    params = {"filter[statuses][0][pipeline_id]": PIPELINE_ID, "filter[statuses][0][status_id]": status_id, "limit": 50}
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
def fetch_partner_enums() -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/custom_fields"
    try:
        resp = _http.get(url, headers=HEADERS, timeout=8)
        if resp.status_code == 200:
            fields = resp.json().get("_embedded", {}).get("custom_fields", [])
            for f in fields:
                if f.get("id") == 2989615:  # Partnyor field
                    return f.get("enums", [])
    except:
        pass
    return []

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
            "assign_to": {"type": "string", "enum": ["shamil", "soltan", "admin"], "description": "Kimin üçün tapşırıq yaradılır"}
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

Komanda: Admin (Texniki Destek), Şamil Əliyev (satış), Soltan Abbasov (texnik).
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

def create_lead_for_contact(contact_id: int, contact_name: str) -> int | None:
    """Create a pipeline deal linked to a contact and return the new lead ID."""
    payload = [{
        "name": contact_name or str(contact_id),
        "_embedded": {"contacts": [{"id": int(contact_id)}]},
        "pipeline_id": PIPELINE_ID,
    }]
    headers = {
        "Authorization": f"Bearer {KOMMO_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        response = _http.post(
            f"https://texnikidestek50.kommo.com/api/v4/leads",
            headers=headers,
            json=payload,
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


def execute_tool_create_task(phone: str, text: str, date: str = None, time_str: str = None, assign_to: str = None, client_name: str = "") -> dict:
    """Resolve the task entity, creating a contact and/or linked deal when needed."""
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
    lead_id = leads[0].get("id") if leads else None
    if not lead_id:
        lead_id = create_lead_for_contact(contact_id, contact_name)
        if not lead_id:
            return {"success": False, "message": "❌ Müştəri üçün sövdələşmə yaradıla bilmədi."}
        logger.info("Auto-created lead %s for contact %s", lead_id, contact_id)

    entity_id = int(lead_id)
    entity_type = "leads"
    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
    assignee_map = {"shamil": 15532668, "soltan": 15531960, "huseyn": 15532668, "rasim": 15532668, "texniki": 15532668, "admin": 10932455, "sahe_meneceri": 15532668}
    assignee_id = assignee_map.get(assign_to, 10932455)
    assignee_name = KOMMO_USERS.get(assignee_id, "Admin")
    return {
        "success": True, "needs_deadline": True,
        "contact_id": contact_id, "contact_name": contact_name,
        "entity_id": entity_id, "entity_type": entity_type,
        "link": link, "task_text": text,
        "assignee_id": assignee_id, "assignee_name": assignee_name,
        "phone": phone, "date": date, "time": time_str
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
    """Returns result. Non-admin users need confirmation."""
    contacts = search_contact_by_phone(phone)
    if not contacts:
        return {"success": False, "message": f"❌ '{phone}' nömrəli müştəri tapılmadı."}
    contact = contacts[0]
    full_c = get_contact_details(contact["id"])
    leads = (full_c or {}).get("_embedded", {}).get("leads", [])
    if not leads:
        return {"success": False, "message": "❌ Müştərinin sövdələşməsi tapılmadı."}
    lead_id = leads[0]["id"]
    status_id = STAGES.get(stage)
    if not status_id:
        return {"success": False, "message": f"❌ Naməlum mərhələ: {stage}"}
    return {
        "success": True, "needs_confirmation": not is_admin(chat_id),
        "lead_id": lead_id, "status_id": status_id, "stage": stage,
        "contact_name": contact.get("name", "Adsız"), "phone": phone
    }

def execute_tool_complete_task(phone: str) -> str:
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
    deadline_ts = task.get("complete_till", 0)
    deadline_str = datetime.fromtimestamp(deadline_ts, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M") if deadline_ts else ""
    responsible_id = task.get("responsible_user_id", 0)
    responsible_name = KOMMO_USERS.get(responsible_id, "")
    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}" if lead_id else ""
    res = update_task_kommo(task["id"], {"is_completed": True, "result": {"text": "Tamamlandı"}})
    if res:
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
                                  responsible_user_id=result["assignee_id"], entity_type=result["entity_type"])
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
            update_lead_kommo(result["lead_id"], {"status_id": result["status_id"], "pipeline_id": PIPELINE_ID})
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
        assign_names = {"shamil": "Şamil Əliyev", "soltan": "Soltan Abbasov", "admin": "Admin"}
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
                # Notify admin
                admin_chat = get_chat_id_for_kommo_user(10932455)
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
                        notif_text = (f"✅ *{sender_name}* tapşırığı tamamladı:\n\n"
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
                InlineKeyboardButton("Şamil Əliyev", callback_data=f"aitask_{task_key}_shamil"),
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
            # Notify admin about any confirmed action
            admin_chat = get_chat_id_for_kommo_user(10932455)
            sender_name = KOMMO_USERS.get(get_kommo_user_id_for_chat(chat_id), "Əməkdaş")
            if admin_chat and admin_chat != chat_id:
                action_labels = {"complete_task": "✅ tamamladı", "add_note": "📝 qeyd əlavə etdi", "change_stage": "🔄 mərhələ dəyişdi", "create_task": "📋 tapşırıq yaratdı"}
                action_label = action_labels.get(fn_name, fn_name)
                # Get phone from args for reply context
                notif_phone = fn_args.get("phone", "")
                try:
                    sent_admin = await context.bot.send_message(
                        admin_chat,
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
                                store_message_lead(admin_chat, sent_admin.message_id, leads[0]["id"], c.get("name", ""), notif_phone)
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
    assignee_map = {"shamil": (15532668, "Sahə Meneceri"), "soltan": (15531960, "Soltan Abbasov"), "admin": (10932455, "Admin"), "sahe_meneceri": (15532668, "Sahə Meneceri")}
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
    result = create_task(entity_id, task_text, deadline_ts, responsible_user_id=assignee_id, entity_type=entity_type)
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
                         responsible_user_id=pending["assignee_id"], entity_type=pending["entity_type"])
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
        update_lead_kommo(lead_id, {"status_id": status_id, "pipeline_id": PIPELINE_ID})
        stage_display = STAGE_NAMES.get(status_id, stage)
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
                    create_task(lead_id, f"[{_marker}] {_task_text}", _deadline_ts, responsible_user_id=15532668, entity_type="leads")
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
    # All employees go to Sahə Meneceri with marker; admin goes to admin
    _ASSIGNEE_MARKER = {"shamil": "Şamil Əliyev", "soltan": "Soltan Abbasov", "huseyn": "Hüseyn Səfərov", "rasim": "Rasim Əsgərov", "texniki": TECHNICAL_SUPPORT_NAME, "admin": ""}
    marker_name = _ASSIGNEE_MARKER.get(assignee_key, "")
    if assignee_key == "admin":
        assignee_uid = 10932455
        assignee_name = "Nizami Qasımov"
    else:
        assignee_uid = 15532668
        assignee_name = marker_name or "Sahə Meneceri"
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
        "shamil": "Şamil", "soltan": "Soltan", "huseyn": "Hüseyn",
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
    _ASSIGNEE_MARKER_DL = {"shamil": "Şamil Əliyev", "soltan": "Soltan Abbasov", "huseyn": "Hüseyn Səfərov", "rasim": "Rasim Əsgərov", "texniki": TECHNICAL_SUPPORT_NAME, "admin": ""}
    marker_name = _ASSIGNEE_MARKER_DL.get(assignee_key, "")
    if assignee_key == "admin":
        assignee_uid = 10932455
        assignee_name = "Nizami Qasımov"
    else:
        assignee_uid = 15532668
        assignee_name = marker_name or "Sahə Meneceri"
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
                    InlineKeyboardButton("Şamil", callback_data=f"btnflow_{chat_id}_shamil"),
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
    assignee_map = {"shamil": (15532668, "Sahə Meneceri"), "soltan": (15531960, "Soltan Abbasov"), "admin": (10932455, "Admin"), "sahe_meneceri": (15532668, "Sahə Meneceri")}
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
            stage_display = STAGE_NAMES.get(result["status_id"], stage)
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
            update_lead_kommo(result["lead_id"], {"status_id": result["status_id"], "pipeline_id": PIPELINE_ID})
            stage_display = STAGE_NAMES.get(result["status_id"], stage)
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
        res = create_task(result["entity_id"], text, deadline_ts, responsible_user_id=result["assignee_id"], entity_type=result["entity_type"])
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
    created_by_raw = _get("created_by")
    task_type_id_raw = _get("task_type_id") or _get("task_type")
    # Task type names mapping
    _TASK_TYPE_NAMES = {
        1: "Əlaqə saxla", 2: "Görüş", 3263995: "Təqdimat",
        4187880: "Yeni", 3263999: "Quraşdırma", 3265439: "Tapşırıq",
        3267595: "Zəng et", 4229224: "Cavab gözlənilir",
        4232112: "Texniki tapşırıq", 4232108: "Import"
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
        # Suppress if bot created this task
        if tid in _bot_created_tasks:
            ts = _bot_created_tasks_ts.get(tid, 0)
            if _time.time() - ts < 120:
                logger.info(f"Webhook suppressed: bot-created task {tid}")
                return
            else:
                _bot_created_tasks.discard(tid)
                _bot_created_tasks_ts.pop(tid, None)
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

async def handle_kommo_webhook(request: web.Request) -> web.Response:
    """Handle incoming Kommo webhooks."""
    try:
        data = await request.post()
        data = dict(data)
        logger.info(f"Webhook received: {list(data.keys())[:10]}")
        # Detect event type
        is_task_event = any(k.startswith(("tasks[", "task[")) for k in data.keys())
        if is_task_event:
            await _handle_kommo_task_webhook(data)
            return web.Response(status=200, text="OK")
        # Lead status change
        lead_keys = [k for k in data.keys() if k.startswith("leads[status][0]")]
        if not lead_keys:
            return web.Response(status=200, text="OK")
        lead_id = data.get("leads[status][0][id]")
        old_status_id = data.get("leads[status][0][old_status_id]")
        new_status_id = data.get("leads[status][0][status_id]")
        pipeline_id = data.get("leads[status][0][pipeline_id]")
        if not lead_id or not new_status_id:
            return web.Response(status=200, text="OK")
        lead_id = int(lead_id)
        old_status_id = int(old_status_id) if old_status_id else 0
        new_status_id = int(new_status_id)
        pipeline_id = int(pipeline_id) if pipeline_id else 0
        if pipeline_id != PIPELINE_ID:
            return web.Response(status=200, text="OK")
        # Suppress webhook echo when bot itself changed the stage
        import time as _time
        if lead_id in _bot_changed_leads:
            if _time.time() - _bot_changed_leads[lead_id] < 120:
                logger.info(f"Webhook suppressed: bot-initiated stage change for lead {lead_id}")
                return web.Response(status=200, text="OK")
            else:
                del _bot_changed_leads[lead_id]
        # Deduplicate: same lead+stage within 60s = duplicate webhook
        import time as _time2
        _dedup_key = (lead_id, new_status_id)
        if _dedup_key in _webhook_stage_dedup and _time2.time() - _webhook_stage_dedup[_dedup_key] < 1800:
            logger.info(f"Webhook dedup: lead {lead_id} stage {new_status_id} already processed")
            return web.Response(status=200, text="OK")
        _webhook_stage_dedup[_dedup_key] = _time2.time()
        # Cleanup old dedup entries
        _cutoff = _time2.time() - 3600
        for k in list(_webhook_stage_dedup.keys()):
            if _webhook_stage_dedup[k] < _cutoff:
                del _webhook_stage_dedup[k]
        # Suppressed stages - no notification
        suppressed = {STAGES["imtina"], STAGES["danisiqlar"], STAGES["dusunur"]}
        if new_status_id in suppressed:
            return web.Response(status=200, text="OK")
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
            return web.Response(status=200, text="OK")
        # Qiymət təklifi - notification only (task creation is handled in API handler)
        if new_status_id == STAGES["qiymet_teklifi"]:
            msg = (f"💰 *Qiymət təklifi mərhələsinə keçdi:*\n\n"
                   f"👤 {contact_name}\n📞 {contact_phone}\n📋 {lead_name}\n🔗 {link}")
            try:
                sent = await _bot_app.bot.send_message(admin_chat, msg, parse_mode="Markdown", disable_web_page_preview=True)
                if sent:
                    store_message_lead(admin_chat, sent.message_id, lead_id, lead_name, contact_phone)
#                send_push_to_admin(f"Qiymət təklifi: {contact_name}", title="💰 Qiymət təklifi")
            except:
                pass
            return web.Response(status=200, text="OK")
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
                    InlineKeyboardButton("Şamil", callback_data=f"stgtask-{lead_id}-{stage_key}-shamil"),
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
            }, ["Şamil", "Soltan", "Hüseyn", "Rasim", "Texniki", "Özüm", "Ləğv et"])
            send_push_to_admin(f"Mərhələ dəyişdi: {stage_display} - {contact_name}", title="📋 İcraçı seçimi")
        else:
            # Plain notification
            msg = (f"🔄 *Mərhələ dəyişikliyi:*\n\n👤 {contact_name}\n📞 {contact_phone}\n"
                   f"📋 {lead_name}\n📌 {old_stage_name} → *{new_stage_name}*\n🔗 {link}")
            try:
                sent = await _bot_app.bot.send_message(admin_chat, msg, parse_mode="Markdown", disable_web_page_preview=True)
                if sent:
                    store_message_lead(admin_chat, sent.message_id, lead_id, lead_name, contact_phone)
#                send_push_to_admin(f"{contact_name}: {old_stage_name} → {new_stage_name}", title="🔄 Mərhələ dəyişdi")
            except:
                pass
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}\n{traceback.format_exc()}")
        return web.Response(status=200, text="OK")

async def health_check(request: web.Request) -> web.Response:
    return web.Response(status=200, text="Bot is running")


async def handle_get_pending_actions(request: web.Request) -> web.Response:
    chat_id = request.rel_url.query.get("chat_id") or request.headers.get("X-TG-User-ID", "")
    if not is_admin(chat_id):
        return web.json_response({"error": "Unauthorized"}, status=403)
    actions = [action for action in get_pending_actions() if not action.get("resolved")]
    # Inject voice_url for each action
    for a in actions:
        eid = str(a.get("data", {}).get("lead_id") or a.get("data", {}).get("entity_id") or "")
        if eid and eid in _voice_urls:
            a.setdefault("data", {})["voice_url"] = _voice_urls[eid]
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
    if not action_id or not choice:
        return web.json_response({"success": False, "message": "Sorğu v\u0259 se\u00e7im t\u0259l\u0259b olunur."}, status=400)
    user_agent = request.headers.get("User-Agent", "unknown")
    logger.info(f"RESOLVE_ACTION: id={action_id}, choice={choice}, chat_id={chat_id}, UA={user_agent[:80]}")
    success, message = resolve_pending_action(str(action_id), str(choice), kpi_score=int(kpi_score) if kpi_score else 0)
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
        update_data["responsible_user_id"] = 15532668
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
        return web.json_response({"success": False, "error": "İstifadəçi tapılmadı. Botda /start yazın."}, status=403)
    action = data.get("action", "")
    phone = data.get("phone", "")
    try:
        if action == "info":
            result = execute_tool_get_lead_info(phone)
            return web.json_response({"success": True, "message": result})
        elif action == "add_note":
            text = data.get("text", "")
            if not text:
                return web.json_response({"success": False, "error": "Qeyd m\u0259tni bo\u015fdur."})
            task_id_note = data.get("task_id")
            if task_id_note:
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
            result = execute_tool_add_note(phone, text) if (phone and not task_id_note) else "OK"
            # Subtask
            if data.get("create_subtask") and data.get("subtask_text"):
                st_result = execute_tool_create_task(phone, data["subtask_text"], None, None, "soltan")
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
                        [{"text": "\u015eamil", "callback_data": f"updtask-{found_conf_key}-shamil"}, {"text": "Soltan", "callback_data": f"updtask-{found_conf_key}-soltan"}],
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
                    }, ["Təsdiq et", "Şamil", "Soltan", "Hüseyn", "Rasim", "Texniki", "Özüm", "Rədd et"])
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
            # A shared Sahə Meneceri Kommo identity must never hide the real
            # employee in the marker or the admin confirmation.
            if not assignee_name_raw or assignee_name_raw == KOMMO_USERS.get(15532668):
                assignee_name_raw = creator_name or KOMMO_USERS.get(15532668, "Əməkdaş")
            # Routing: Nizami = admin's own tasks, everyone else = Sahə Meneceri
            if assignee_name_raw.lower() in ("nizami", "nizami qasımov"):
                assignee = "admin"
            else:
                assignee = "sahe_meneceri"
            # Prepend the real employee marker to the task text (with optional price).
            price_val = data.get("price", "").strip()
            if assignee_name_raw:
                if price_val:
                    text = f"[{assignee_name_raw}:{price_val}] {text}"
                else:
                    text = f"[{assignee_name_raw}] {text}"
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
                assignee_map_direct = {"shamil": 15532668, "soltan": 15531960, "huseyn": 15532668, "rasim": 15532668, "texniki": 15532668, "admin": 10932455, "sahe_meneceri": 15532668}
                assignee_id_direct = assignee_map_direct.get(assignee, 15532668)
                link_direct = f"{KOMMO_BASE_URL}/{direct_entity_type}/detail/{direct_entity_id}"
                logger.info(f"Subtask create: entity_id={direct_entity_id}, entity_type={direct_entity_type}, assignee={assignee}, assignee_id={assignee_id_direct}")
                result = {"success": True, "entity_id": direct_entity_id, "entity_type": direct_entity_type, "assignee_id": assignee_id_direct, "contact_name": "", "link": link_direct, "phone": phone, "assignee_name": assignee_name_raw or assignee}
            else:
                client_name_input = data.get("client_name", "").strip()
                result = execute_tool_create_task(phone, text, None, None, assignee, client_name=client_name_input)
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
                    [InlineKeyboardButton("Şamil", callback_data=f"cnftask-{conf_key}-shamil"), InlineKeyboardButton("Soltan", callback_data=f"cnftask-{conf_key}-soltan")],
                    [InlineKeyboardButton("Hüseyn", callback_data=f"cnftask-{conf_key}-huseyn"), InlineKeyboardButton("Rasim", callback_data=f"cnftask-{conf_key}-rasim")],
                    [InlineKeyboardButton("Texniki", callback_data=f"cnftask-{conf_key}-texniki"), InlineKeyboardButton("Özüm", callback_data=f"cnftask-{conf_key}-admin")],
                    [InlineKeyboardButton("❌ Rədd et", callback_data=f"cnftask-{conf_key}-no")],
                ])
                try:
                    await _bot_app.bot.send_message(admin_chat, f"📋 *{sender_name}* tapşırıq yaratmaq istəyir:\n\n👤 {result['contact_name']}\n📞 {phone}\n📝 {display_text}\n⏰ {deadline_dt.strftime('%d.%m.%Y %H:%M')}\n👤 {sender_name} → {assignee_name_raw}\n🔗 {result.get('link','')}", parse_mode="Markdown", disable_web_page_preview=True, reply_markup=kb)
                    send_push_to_admin(f"{sender_name} tapşırıq yaratmaq istəyir: {result['contact_name']}", title="📋 Yeni tapşırıq")
                except: pass
                save_pending_action("assign_executor", {
                    "contact_name": result['contact_name'],
                    "phone": phone,
                    "lead_id": result.get('entity_id'),
                    "task_text": display_text,
                    "deadline": deadline_dt.strftime('%d.%m.%Y %H:%M'),
                    "sender_name": sender_name,
                    "assignee_name_raw": assignee_name_raw,
                    "link": result.get('link', ''),
                    "conf_key": conf_key,
                }, ["Şamil", "Soltan", "Hüseyn", "Rasim", "Texniki", "Özüm", "Rədd et"])
                return web.json_response({"success": True, "message": "⏳ Tapşırıq təsdiq üçün göndərildi.", "entity_id": result.get('entity_id'), "entity_type": result.get('entity_type', 'leads')})
            # Admin creates directly
            logger.info(f"Admin creating task: entity_id={result['entity_id']}, type={result['entity_type']}, assignee_id={result['assignee_id']}, task_type={task_type_id}, text={text[:50]}")
            res = create_task(int(result["entity_id"]), text, deadline_ts, responsible_user_id=int(result["assignee_id"]), entity_type=result["entity_type"], task_type_id=task_type_id)
            logger.info(f"Create task result: {res}")
            if res:
                save_task_priority(res, priority)
                # Also add task text as a note on the entity
                try:
                    note_payload = [{"note_type": "common", "params": {"text": f"📝 Tapşırıq: {text}"}}]
                    nr = _http.post(f"{KOMMO_BASE_URL}/api/v4/{result['entity_type']}/{result['entity_id']}/notes", json=note_payload, timeout=5); logger.info(f"Note add: {nr.status_code} entity={result['entity_type']}/{result['entity_id']}")
                except Exception as _ne: logger.error(f"Note add failed: {_ne}")
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
                return web.json_response({"success": True, "message": msg, "link": result.get('link', ''), "entity_id": result.get('entity_id'), "entity_type": result.get('entity_type', 'leads')})
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
                stage_display = STAGE_NAMES.get(result["status_id"], stage)
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
            update_lead_kommo(result["lead_id"], {"status_id": result["status_id"], "pipeline_id": PIPELINE_ID})
            stage_display = STAGE_NAMES.get(result["status_id"], stage)
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
            # For now, create_deal = change stage to specified + optionally create subtask
            stage = data.get("stage", "yeni_sifaris")
            result = execute_tool_change_stage(phone, stage, chat_id)
            if not result.get("success"):
                return web.json_response({"success": False, "error": result.get("message", "Xəta")})
            if result.get("needs_confirmation"):
                return web.json_response({"success": False, "error": "Admin təsdiqi lazımdır. Botdan istifadə edin."})
            update_lead_kommo(result["lead_id"], {"status_id": result["status_id"], "pipeline_id": PIPELINE_ID})
            stage_display = STAGE_NAMES.get(result["status_id"], stage)
            # Subtask
            if data.get("create_subtask") and data.get("subtask_text"):
                st_result = execute_tool_create_task(phone, data["subtask_text"], None, None, "soltan")
                if isinstance(st_result, dict) and st_result.get("success"):
                    now = datetime.now(tz=BAKU_TZ)
                    deadline_dt = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                    create_task(st_result["entity_id"], data["subtask_text"], int(deadline_dt.timestamp()), responsible_user_id=st_result["assignee_id"], entity_type=st_result["entity_type"])
            msg = f"✅ Sifariş yaradıldı!\n👤 {result['contact_name']}\n📌 {stage_display}"
            link = f"{KOMMO_BASE_URL}/leads/detail/{result['lead_id']}"
            return web.json_response({"success": True, "message": msg, "link": link})
        elif action == "update_task":
            task_id = data.get("task_id")
            if not task_id:
                return web.json_response({"success": False, "error": "task_id yoxdur."})
            update_data = {}
            if data.get("text"):
                update_data["text"] = data["text"]
            # Handle assignee change
            assignee = data.get("assignee")
            assignee_name_raw = normalize_assignee_name(data.get("assigneeName", ""))
            if assignee:
                assignee_map = {"shamil": 15532668, "soltan": 15531960, "huseyn": 15532668, "rasim": 15532668, "texniki": 15532668, "admin": 10932455, "sahe_meneceri": 15532668}
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
            if not update_data and not edit_client_name:
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
            if not update_data:
                # Only client_name was changed, no task fields to update
                return web.json_response({"success": True, "message": "\u2705 M\u00fc\u015ft\u0259ri ad\u0131 yenil\u0259ndi!"})
            result = update_task_kommo(task_id, update_data)
            if result:
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
                return web.json_response({"success": True, "message": "\u2705 Tap\u015f\u0131r\u0131q yenil\u0259ndi!", "link": link})
            else:
                return web.json_response({"success": False, "error": "Yenil\u0259m\u0259 u\u011fursuz oldu."})
        elif action == "update_task_deadline":
            task_id = data.get("task_id")
            time_preset = data.get("time_preset", "+2h")
            reason = data.get("reason", "")
            if not task_id:
                return web.json_response({"success": False, "error": "task_id yoxdur."})
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
            task_id = data.get("task_id")
            if not task_id:
                return web.json_response({"success": False, "error": "task_id yoxdur."})
            try:
                task_resp = _http.get(f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}", headers=HEADERS, timeout=8)
                if task_resp.status_code != 200:
                    return web.json_response({"success": False, "error": "Tapşırıq Kommo-da tapılmadı."})
                task_data = task_resp.json()
            except Exception as exc:
                logger.error(f"complete_task task lookup error: {exc}")
                return web.json_response({"success": False, "error": "Tapşırıq məlumatı alınmadı."})

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

            result = update_task_kommo(
                task_id,
                {"is_completed": True, "result": {"text": task_result_text}},
            )
            stage_msg = ""
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
                        if stage_result.get("needs_confirmation"):
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
                        if is_admin(chat_id):
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
                        create_task(lead_id, followup_text, deadline_ts, responsible_user_id=15532668, entity_type="leads")
                    else:
                        create_task(lead_id, followup_text, deadline_ts, responsible_user_id=10932455, entity_type="leads")
                stage_msg += f"\n✅ Yeni tapşırıq: {_STAGE_TASK_TEXTS[new_stage]}"

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
                    price_match = re.match(r"^\[([^:\]]+)(?::(\d+(?:\.\d+)?))?\]\s*(.*)", task_text_full)
                    if get_employee_type(chat_id) == "piecework" and price_match and price_match.group(2):
                        amount = float(price_match.group(2))
                        if amount > 0:
                            add_balance_transaction(
                                chat_id,
                                int(task_id),
                                amount,
                                price_match.group(3),
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
                logger.info(f"complete_task notify: admin_chat={admin_chat}, contact={contact_name}")
                if admin_chat:
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
                    task_price_match = re.search(r":(\d+)\]", raw_task_text)
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

async def handle_api_notifications(request: web.Request) -> web.Response:
    """Return active tasks for the requesting user."""
    try:
        tg_user_id = request.headers.get("X-TG-User-ID", "")
        chat_id = int(tg_user_id) if tg_user_id else None
        if not chat_id:
            return web.json_response({"success": False, "error": "User not identified"}, status=401)
        kommo_user_id = get_kommo_user_id_for_chat(chat_id)
        if not kommo_user_id:
            return web.json_response({"success": True, "tasks": []})
        # Get tasks for this user (incomplete)
        now = datetime.now(tz=BAKU_TZ)
        # Always fetch tasks for Sahə Meneceri (15532668) - all employee tasks are there with markers
        # If admin requests, fetch BOTH admin's own tasks AND Sahə Meneceri tasks
        url = f"{KOMMO_BASE_URL}/api/v4/tasks"
        tasks_list = []
        fetch_ids = [15532668] if kommo_user_id != 10932455 else [10932455, 15532668]
        raw_tasks = []
        task_priorities = read_json(_TASK_PRIORITIES_FILE) or {}
        if not isinstance(task_priorities, dict):
            task_priorities = {}
        try:
            for fid in fetch_ids:
                params = {"filter[is_completed]": 0, "filter[responsible_user_id]": fid, "limit": 50}
                resp = _http.get(url, headers=HEADERS, params=params, timeout=8)
                if resp.status_code == 200:
                    raw_tasks.extend(resp.json().get("_embedded", {}).get("tasks", []))
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
                                phone_val = ""
                                for cf in (c.get("custom_fields_values") or []):
                                    if cf.get("field_code") == "PHONE":
                                        vals = cf.get("values", [])
                                        if vals: phone_val = vals[0].get("value", "")
                                contacts_cache[c["id"]] = {"name": c.get("name", ""), "phone": phone_val}
                    except: pass
                # Fetch leads linked to contacts (to get stage)
                contact_lead_stage = {}  # {contact_id: stage_name}
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
                                st_name = STAGE_NAMES.get(ld.get("status_id", 0), "")
                                for lc in ld.get("_embedded", {}).get("contacts", []):
                                    if lc["id"] in contact_ids and lc["id"] not in contact_lead_stage:
                                        contact_lead_stage[lc["id"]] = st_name
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
                                leads_stage_cache[lead["id"]] = lead.get("status_id", 0)
                                emb_contacts = lead.get("_embedded", {}).get("contacts", [])
                                if emb_contacts:
                                    cid = emb_contacts[0]["id"]
                                    leads_contact_cache[lead["id"]] = cid
                                    contact_ids.add(cid)
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
                                    phone_val = ""
                                    for cf in (c.get("custom_fields_values") or []):
                                        if cf.get("field_code") == "PHONE":
                                            vals = cf.get("values", [])
                                            if vals: phone_val = vals[0].get("value", "")
                                    contacts_cache[c["id"]] = {"name": c.get("name", ""), "phone": phone_val}
                        except: pass
                for t in raw_tasks:
                    # Skip "Cavab gözlənilir" task type
                    if t.get("task_type_id") == 4229224:
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
                    entity_id = t.get("entity_id")
                    entity_type = t.get("entity_type", "contacts")
                    # Resolve from cache
                    if entity_type == "contacts" and entity_id in contacts_cache:
                        contact_name = contacts_cache[entity_id]["name"]
                        phone = contacts_cache[entity_id]["phone"]
                    elif entity_type == "leads" and entity_id in leads_contact_cache:
                        cid = leads_contact_cache[entity_id]
                        contact_name = contacts_cache.get(cid, {}).get("name", "")
                        phone = contacts_cache.get(cid, {}).get("phone", "")
                    else:
                        contact_name = ""
                        phone = ""
                    responsible_name = KOMMO_USERS.get(t.get("responsible_user_id"), "")
                    if entity_type == "leads":
                        kommo_link = f"https://texnikidestek50.kommo.com/leads/detail/{entity_id}"
                    else:
                        kommo_link = f"https://texnikidestek50.kommo.com/contacts/detail/{entity_id}"
                    # Extract assigneeName from marker
                    task_text = t.get("text", "")
                    _marker_match = re.match(r'^\[(Şamil Əliyev|Soltan Abbasov|Hüseyn Səfərov|Nizami Qasımov|Rasim Əsgərov|Texniki Dəstək|Texniki tapşırıq|Şamil|Soltan|Hüseyn|Nizami|Rasim|Texniki)(?::\d+)?\]\s*', task_text)
                    assignee_name_from_marker = _marker_match.group(1) if _marker_match else ""
                    _SHORT_TO_FULL = {'Şamil':'Şamil Əliyev','Soltan':'Soltan Abbasov','Hüseyn':'Hüseyn Səfərov','Nizami':'Nizami Qasımov','Rasim':'Rasim Əsgərov','Texniki': TECHNICAL_SUPPORT_NAME}
                    if assignee_name_from_marker in _SHORT_TO_FULL:
                        assignee_name_from_marker = _SHORT_TO_FULL[assignee_name_from_marker]
                    if not assignee_name_from_marker and t.get("responsible_user_id") == 10932455:
                        assignee_name_from_marker = "Nizami Qas\u0131mov"
                    _TASK_TYPE_NAMES_NOTIF = {1: "Əlaqə saxla", 2: "Görüş", 3263995: "Təqdimat", 3263999: "Quraşdırma", 3267595: "Zəng et", 4229224: "Cavab gözlənilir", 4232112: "Texniki tapşırıq", 4232108: "Import"}
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
                    tasks_list.append({
                        "id": t.get("id"),
                        "title": "\u26a0\ufe0f Gecikmi\u015f tap\u015f\u0131r\u0131q" if is_overdue else "\ud83d\udccb Aktiv tap\u015f\u0131r\u0131q",
                        "desc": task_text,
                        "time": time_str,
                        "is_overdue": is_overdue,
                        "entity_id": entity_id,
                        "entity_type": entity_type,
                        "task_id": t.get("id"),
                        "contact_name": contact_name,
                        "phone": phone,
                        "responsible": responsible_name,
                        "assigneeName": assignee_name_from_marker,
                        "kommo_link": kommo_link,
                        "complete_till": t.get("complete_till", 0),
                        "task_type_name": task_type_name,
                        "task_type_id": t.get("task_type_id", 1),
                        "last_note": last_note,
                        "priority": task_priorities.get(str(t.get("id")), task_priorities.get(t.get("id"), "")),
                        "stage_name": STAGE_NAMES.get(leads_stage_cache.get(entity_id, 0), "") if entity_type == "leads" else contact_lead_stage.get(entity_id, ""),
                        "voice_url": _voice_urls.get(str(entity_id), "")
                    })
                # Sort: overdue first
                tasks_list.sort(key=lambda x: (not x["is_overdue"], x["time"]))
        except Exception as e:
            logger.error(f"Notifications fetch error: {e}")
        # Filter by marker for non-admin users
        if kommo_user_id != 10932455:
            # Find this user's name from NAME_TO_CHAT reverse lookup
            user_marker_name = None
            for name, cid in NAME_TO_CHAT.items():
                if cid == chat_id:
                    user_marker_name = name
                    break
            if user_marker_name:
                tasks_list = [t for t in tasks_list if t.get("assigneeName", "").lower() == user_marker_name.lower()]
        user_display_name = get_employee_name_by_chat_id(chat_id, "")
        return web.json_response({"success": True, "tasks": tasks_list, "is_admin": kommo_user_id == 10932455, "user_name": user_display_name})
    except Exception as e:
        logger.error(f"API notifications error: {e}")
        return web.json_response({"success": False, "error": "Server xətası."}, status=500)

async def serve_webapp(request: web.Request) -> web.Response:
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "index.html")
    return web.FileResponse(html_path)

@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-TG-User-ID'
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

def send_push_notification(user_id, title, body, url=None, urgent=False):
    """Send push notification to a user if subscribed."""
    sub = get_push_subscription(str(user_id))
    if not sub:
        return
    payload = json.dumps({'title': title, 'body': body, 'url': url or '/', 'urgent': bool(urgent)})
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
    for uid in ['7962757442','7262243946','7329891614']:
        send_push_notification(uid, title, body, url)

KOMMO_DRIVE_URL = "https://drive-g.kommo.com"
_VOICE_URLS_FILE = "voice_urls.json"
_voice_urls = read_json(_VOICE_URLS_FILE) or {}  # {entity_id: download_url}

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
                file_uuid = up_data["uuid"]
                download_url = up_data.get("_links", {}).get("download", {}).get("href", "")
                version_href = up_data.get("_links", {}).get("download_version", {}).get("href", "")
            offset += max_part
        if not file_uuid:
            return web.json_response({"success": False, "error": "No file UUID returned"}, status=500)
        # Step 3: Attach file to entity via main API domain (drive domain attach returns 404)
        attached = False
        try:
            attach_url = f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/files"
            att_resp = _http.post(attach_url, headers=HEADERS, json=[{"file_uuid": file_uuid}], timeout=10)
            logger.info(f"File attach {attach_url}: {att_resp.status_code}")
            attached = att_resp.status_code in (200, 201, 202)
        except Exception as _ae:
            logger.error(f"File attach error: {_ae}")
        if not attached:
            # Fallback: attach via note with note_type=file
            try:
                version_uuid = ""
                if version_href:
                    _parts = [p for p in version_href.split("/") if p]
                    version_uuid = _parts[-1] if _parts else ""
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
        _voice_urls[str(entity_id)] = download_url
        write_json(_VOICE_URLS_FILE, _voice_urls)
        return web.json_response({"success": True, "message": "S\u0259s yaz\u0131s\u0131 \u0259lav\u0259 olundu", "download_url": download_url, "file_uuid": file_uuid})
    except Exception as e:
        logger.error(f"Upload voice error: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def start_webhook_server():
    app_web = web.Application(middlewares=[cors_middleware])
    app_web.router.add_route('OPTIONS', '/api/action', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/notifications', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/pending_actions', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/pending_actions/resolve', lambda r: web.Response())
    app_web.router.add_route('OPTIONS', '/api/pending_actions/delete', lambda r: web.Response())
    app_web.router.add_post("/webhook/kommo", handle_kommo_webhook)
    app_web.router.add_post("/api/action", handle_api_action)
    app_web.router.add_get("/api/notifications", handle_api_notifications)
    app_web.router.add_get("/api/pending_actions", handle_get_pending_actions)
    app_web.router.add_post("/api/pending_actions/resolve", handle_resolve_action)
    app_web.router.add_post("/api/pending_actions/delete", handle_delete_pending_action)
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
    app_web.router.add_get("/api/stages", lambda r: web.json_response({"stages": {str(v): STAGE_NAMES.get(v, k) for k, v in STAGES.items()}}))
    app_web.router.add_route('OPTIONS', '/api/admin_balances', lambda r: web.Response())
    app_web.router.add_get("/api/admin_balances", handle_api_admin_balances)
    app_web.router.add_route('OPTIONS', '/api/push-subscribe', lambda r: web.Response())
    app_web.router.add_post("/api/push-subscribe", handle_push_subscribe)
    app_web.router.add_route('OPTIONS', '/api/upload_voice', lambda r: web.Response())
    app_web.router.add_post("/api/upload_voice", handle_upload_voice)
    app_web.router.add_get("/webapp", serve_webapp)
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


# ─── Background Jobs ─────────────────────────────────────────────────────────
async def check_task_deadlines(context: ContextTypes.DEFAULT_TYPE):
    """Check tasks due in 15 minutes and overdue tasks."""
    now = datetime.now(tz=BAKU_TZ)
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
    # Get all tasks for today (both admin's and Sahə Meneceri's)
    all_tasks = get_tasks(today_start, today_end)
    all_tasks = [t for t in all_tasks if t.get('task_type_id') != 4229224]
    # Employee name -> chat_id mapping
    _EMPLOYEE_NAMES = {
        7962757442: "\u015eamil \u018fliyev",
        7262243946: "Soltan Abbasov",
        7329891614: "H\u00fcseyn S\u0259f\u0259rov",
        7920785774: "Rasim \u018fsg\u0259rov",
    }
    # Send to each employee their tasks (by marker in text)
    for emp_chat_id, emp_name in _EMPLOYEE_NAMES.items():
        # Filter tasks by marker [Name] in text
        first_name = emp_name.split()[0]
        emp_tasks = [t for t in all_tasks if re.search(rf'\[{re.escape(emp_name)}(?::\d+)?\]', t.get('text', '')) or re.search(rf'\[{re.escape(first_name)}(?::\d+)?\]', t.get('text', ''))]
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
    _UPD_MARKER = {"shamil": ("Şamil Əliyev", 15532668), "soltan": ("Soltan Abbasov", 15531960), "huseyn": ("Hüseyn Səfərov", 15532668), "rasim": ("Rasim Əsgərov", 15532668), "texniki": (TECHNICAL_SUPPORT_NAME, 15532668), "admin": ("Nizami Qasımov", 10932455)}
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
    _CNFTASK_MARKER = {"shamil": "Şamil Əliyev", "soltan": "Soltan Abbasov", "huseyn": "Hüseyn Səfərov", "rasim": "Rasim Əsgərov", "texniki": TECHNICAL_SUPPORT_NAME, "admin": ""}
    marker_name = _CNFTASK_MARKER.get(decision, "")
    if decision == "admin":
        assignee_id = 10932455
    else:
        assignee_id = 15532668
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

# Initialize GitHub storage with token from git remote
import subprocess as _sp
_gh_token_match = _sp.run(['git', 'config', '--get', 'remote.origin.url'], capture_output=True, text=True)
_gh_token_url = _gh_token_match.stdout.strip()
_gh_token = _gh_token_url.split('//')[1].split('@')[0] if '@' in _gh_token_url else os.environ.get('GH_TOKEN', os.environ.get('GH_STORAGE_TOKEN', ''))
_init_gh_storage(_gh_token)
logger.info(f"GH Storage initialized, token length: {len(_gh_token)}, starts with: {_gh_token[:10] if _gh_token else 'EMPTY'}")

_EMPLOYEE_NAMES_BY_TG = {
    chat_id: name for chat_id, name in TG_CHAT_TO_EMPLOYEE.items()
    if chat_id != 1628569350
}

_KPI_TARGET_TIMES = {
    1: 30, 2: 30, 4232112: 60, 3263995: 45, 3263999: 120, 4232108: 30, 4229224: 60,
}

_EMPLOYEE_TYPES = {
    7962757442: 'salary', 7262243946: 'salary',
    7329891614: 'salary', 7920785774: 'piecework',
    8835096199: 'salary',
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
        task_id = int(data.get("task_id", 0))
    except (TypeError, ValueError):
        return web.json_response({"success": False, "error": "Əməkdaş və ya tapşırıq ID-si yanlışdır."}, status=400)
    if not employee_id or not task_id:
        return web.json_response({"success": False, "error": "employee_id və task_id tələb olunur."}, status=400)

    result = confirm_balance_transaction(employee_id, task_id)
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
        employees.append({
            'name': name,
            'tg_id': tg_id,
            'balance': all_bals.get(tg_id, 0),
            'pending_balance': all_pending.get(tg_id, 0),
        })
    recent = get_all_recent_transactions(50)
    recent_fmt = [{
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
    return web.json_response({'success': True, 'employee_type': emp_type, **summary})

def main():
    global _bot_app
    async def post_init(application: Application) -> None:
        await start_webhook_server()
        try:
            _rehydrate_tecili_tasks()
        except Exception as _re_err:
            logger.warning(f"Təcili rehydrate skipped: {_re_err}")
        logger.info(f"Bot started. Webhook on port {WEBHOOK_PORT}, Telegram polling active.")

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
