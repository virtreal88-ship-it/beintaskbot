#!/usr/bin/env python3
"""
Telegram Bot with Kommo CRM Integration + AI + Full Automation
- AI-driven pipeline stage transitions
- Role-based user registration
- Background notifications (task deadlines, morning digest, stuck deals)
- Voice message transcription
- Conversation context memory
- Multi-variant phone search
- Azerbaijani interface
- Kommo webhook endpoint for stage change notifications
"""

import os
import re
import json
import logging
import requests
import subprocess
import glob
import traceback
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from openai import OpenAI
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from aiohttp import web

# ─── Configuration ───────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8770145286:AAHB60HD8L1bvMaWVys2OPduPrp_ppkxTXA")
KOMMO_TOKEN = os.environ.get("KOMMO_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImp0aSI6IjNjZDgwYzY0NzM2ODFlMDY4ZTliMTkzZWE2ZjM4NTQ1NGZlNzNkNjRlZjFkNDJiOWQ1ZjkxZDRiOTc0ZGY2MjIzODA0NTU1OWU2YjdkOTI3In0.eyJhdWQiOiJjMjFiNjBhOC00Y2I0LTRjYWQtOGU5NC03ZmI0NTIyMGU4OWMiLCJqdGkiOiIzY2Q4MGM2NDczNjgxZTA2OGU5YjE5M2VhNmYzODU0NTRmZTczZDY0ZWYxZDQyYjlkNWY5MWQ0Yjk3NGRmNjIyMzgwNDU1NTllNmI3ZDkyNyIsImlhdCI6MTc4MjkwNjc3MiwibmJmIjoxNzgyOTA2NzcyLCJleHAiOjE4NjE4MzM2MDAsInN1YiI6IjEwOTMyNDU1IiwiZ3JhbnRfdHlwZSI6IiIsImFjY291bnRfaWQiOjMyNTI0MzU5LCJiYXNlX2RvbWFpbiI6ImtvbW1vLmNvbSIsInZlcnNpb24iOjIsInNjb3BlcyI6WyJjcm0iLCJmaWxlcyIsImZpbGVzX2RlbGV0ZSIsIm5vdGlmaWNhdGlvbnMiLCJwdXNoX25vdGlmaWNhdGlvbnMiLCJ1c2Vyc19hY3RpdmF0ZSIsInVzZXJzX2FkZCIsInVzZXJzX2RlYWN0aXZhdGUiXSwiaGFzaF91dWlkIjoiMmJjODBmNTItNmRhMC00YTkyLWJkODMtZmUwYTVhZWQ3YTY2IiwiYXBpX2RvbWFpbiI6ImFwaS1nLmtvbW1vLmNvbSJ9.fUU7hoGZzSzS0gd5yXY26gut46gYjYDWvtQ1snGVgm2YU6D2FqpUH4U46ef36YHirRaas7DB6an5aPCKSzqXU5D7OLsFxhj_y3PASLE-b1-sDVXVFPO1HiW3EPn8CTn9IHxSt-MKBPjQs49a9ldV5kFRyLOdjr91IH3lHvmwp_qKgWIN3y5RD4ogwH755fpuXL3bMo-zwTc4_zx0FPj2mP8G0MsvwlvxKzlEXx7kZW5uQ8sXxDhHYTGn1bd5DWac-41MeNswGFTCgnHBITCQsSEOgedZb4EvfL9SXlNSJZpXU__khNg6YCC-slE3jZjXIWHXHFMdaUfX5I8IaPnQGA")
KOMMO_DOMAIN = "texnikidestek50.kommo.com"
KOMMO_BASE_URL = f"https://{KOMMO_DOMAIN}"

# Timezone: Asia/Baku (UTC+4)
BAKU_TZ = timezone(timedelta(hours=4))

# LLM Model
LLM_MODEL = "gpt-5"

# HTTP Port for webhook
WEBHOOK_PORT = int(os.environ.get("PORT", 8080))

# ─── Pipeline & Users Configuration ─────────────────────────────────────────

PIPELINE_ID = 8329347  # Sövdələşmələr

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
    "cavab_gozlenilir": 108537976,
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
    108537976: "cavab gözlənilir",
    142: "uğurlu sifariş",
    143: "imtina olundu",
}

KOMMO_USERS = {
    10932455: "Texniki Destek",
    15531960: "Soltan Abbasov",
    15532668: "Şamil Əliyev",
}

# ─── User Registration Storage ───────────────────────────────────────────────

USER_DB_FILE = os.environ.get("USER_DB_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json"))

def load_users() -> dict:
    if os.path.exists(USER_DB_FILE):
        with open(USER_DB_FILE, "r") as f:
            return json.load(f)
    # Try to load from bundled default users.json next to the script
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json")
    if default_path != USER_DB_FILE and os.path.exists(default_path):
        with open(default_path, "r") as f:
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
    return info.get("kommo_user_id") if info else None

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── OpenAI Client ───────────────────────────────────────────────────────────

llm_client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY", "sk-C7kqpsGHciC9Mf9oA63xvy"),
    base_url=os.environ.get("OPENAI_API_BASE", "https://api.manus.im/api/llm-proxy/v1"),
)

# ─── Conversation Context Storage ────────────────────────────────────────────

user_context: dict[int, dict] = {}

# State for overdue task result collection
# Maps chat_id -> {"task_id": ..., "task_text": ..., "entity_id": ..., "entity_type": ...}
_pending_task_result: dict[int, dict] = {}

# ─── Flood Control State ─────────────────────────────────────────────────────

import time as _time_module
_BOT_START_TIME = _time_module.time()  # epoch seconds when module loaded
_sent_deadline_notifications: set[int] = set()  # task IDs already notified

def get_ctx(chat_id: int) -> dict:
    if chat_id not in user_context:
        user_context[chat_id] = {
            "last_phone": None,
            "last_contact_id": None,
            "last_contact_name": None,
            "last_lead_id": None,
            "last_task_id": None,
            "last_task_date": None,
            "last_task_time": None,
            "pending_action": None,
            "pending_params": {},
            "pending_missing": None,
        }
    return user_context[chat_id]

def set_last_contact(chat_id: int, phone: str, contact_id: int, name: str, lead_id: int = None):
    ctx = get_ctx(chat_id)
    ctx["last_phone"] = phone
    ctx["last_contact_id"] = contact_id
    ctx["last_contact_name"] = name
    if lead_id:
        ctx["last_lead_id"] = lead_id

def set_last_task(chat_id: int, task_id: int, date_str: str, time_str: str):
    ctx = get_ctx(chat_id)
    ctx["last_task_id"] = task_id
    ctx["last_task_date"] = date_str
    ctx["last_task_time"] = time_str

def set_pending(chat_id: int, action: str, params: dict, missing: str = None):
    ctx = get_ctx(chat_id)
    ctx["pending_action"] = action
    ctx["pending_params"] = params
    ctx["pending_missing"] = missing

def clear_pending(chat_id: int):
    ctx = get_ctx(chat_id)
    ctx["pending_action"] = None
    ctx["pending_params"] = {}
    ctx["pending_missing"] = None

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
    if any(w in text_lower for w in ["bugün", "bu gün", "today", "сегодня"]):
        return now.strftime("%d.%m.%Y")
    if any(w in text_lower for w in ["sabah", "tomorrow", "завтра"]):
        return (now + timedelta(days=1)).strftime("%d.%m.%Y")
    if any(w in text_lower for w in ["birisi gün", "birisigün", "послезавтра"]):
        return (now + timedelta(days=2)).strftime("%d.%m.%Y")
    days_az = [
        ["bazar ertəsi", "bazarertəsi", "понедельник"],
        ["çərşənbə axşamı", "çərşənbəaxşamı", "вторник"],
        ["çərşənbə", "среда"],
        ["cümə axşamı", "cüməaxşamı", "четверг"],
        ["cümə", "пятница"],
        ["şənbə", "суббота"],
        ["bazar", "воскресенье"],
    ]
    for i, forms in enumerate(days_az):
        if any(f in text_lower for f in forms):
            days_ahead = (i - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (now + timedelta(days=days_ahead)).strftime("%d.%m.%Y")
    return None

def resolve_time_from_text(text: str) -> str | None:
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        return m.group(0)
    return None

# ─── LLM System Prompt ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sən Bein Systems şirkətinin CRM botunun AI assistentisən. Sənin vəzifən istifadəçinin mesajını DƏQİQ analiz etmək, BÜTÜN ayrı-ayrı tapşırıqları/niyyətləri ayırd etmək və hər birini ayrıca action olaraq qaytarmaqdır.

BÜTÜN cavabların Azərbaycan dilində olmalıdır. İstifadəçi Azərbaycan və ya Rus dilində yaza/danışa bilər — hər ikisini anla.

Cari tarix və vaxt: {current_datetime} (saat qurşağı Asia/Baku, UTC+4)
{context_block}

═══ KOMANDA ═══
- Admin / Texniki Destek (ID 10932455) — şirkətin rəhbəri, texniki işlər, baqlar, feature-lər, qeyri-müəyyən tapşırıqlar
- Şamil Əliyev (ID 15532668) — satış meneceri, müştəri ilə əlaqə, təqdimat, görüş
- Soltan Abbasov (ID 15531960) — texnik, quraşdırma, montaj, ofis işçilərinin nəzarəti

═══ ƏN VACİB QAYDALAR ═══

🔴 QAYDA 1: MESAJI HISSƏLƏRƏ BÖL
Bir mesajda bir neçə müxtəlif mövzu ola bilər. HƏR BİRİNİ ayrı action et.
Misal: "Menyu yazıldı. Əvvəlki tapşırıqları bağla. Şəkillər qalıb. QR menyu yüklənmir. Sifariş funksiyası lazımdır."
→ 5 ayrı action: add_note + complete_tasks + create_task + create_task(high) + create_task

🔴 QAYDA 2: add_note vs create_task
- add_note = YALNIZ keçmişdə baş vermiş hadisəni qeyd etmək ("menyu yazıldı", "müştəri ilə danışdıq", "ödəniş alındı")
- create_task = HƏR HANSI gələcəkdə görüləcək İŞ ("şəkillər qalıb" → "şəkilləri yüklə", "zəng et", "get", "düzəlt", "hazırla", "göndər")
- Baq/xəta/problem → create_task (urgency="high")
- Feature/yeni funksiya → create_task
- ŞÜBHƏLİ HALLARDA → create_task (tapşırıq artıq olmaz, qeyd isə itirilə bilər)

🔴 QAYDA 3: automation_transition DUBLIKAT YARATMA
automation_transition özü tapşırıq yaradır. Əgər automation_transition istifadə edirsənsə — ƏLAVƏ create_task QOYMA!
Misal: "təqdimat lazımdır" → YALNIZ 1 action: automation_transition(trigger=need_presentation)
SƏHV: automation_transition + create_task("təqdimat et") ← BU DUBLIKATDIR!

🔴 QAYDA 4: PHONE SAHƏSI
- Kontekstdə "Sonuncu tapılan müştəri" varsa → HƏR action üçün phone sahəsinə həmin nömrəni yaz
- "ona", "bu müştəriyə", "bu müştəridə", müştəri adı → kontekstdəki nömrə
- Phone-u YALNIZ rəqəmlərlə yaz (994...)
- Phone null QOYMA əgər kontekstdə müştəri var!
- HƏÇ VAXT nömrə sorşma (ask_clarification) əgər kontekstdə müştəri var! Birbaşa kontekstdəki nömrəni istifadə et.

🔴 QAYDA 5: KİMƏ TƏYİN ETMƏK (assign_to)
- İstifadəçi özü haqqında: "mən gedəcəm", "mənə lazımdır" → assign_to = istifadəçinin öz ID-si
- Baq/xəta → assign_to = 10932455, urgency = "high"
- Feature request → assign_to = 10932455
- Kim edəcəyi bəlli deyil → assign_to = 10932455
- "Şamil getsin" → assign_to = 15532668
- "Soltan qursun" → assign_to = 15531960

🔴 QAYDA 6: KONTEKSTDƏN ƏMƏLİYYAT — SUAL VERMƏ!
- Əgər istifadəçiyə tapşırıq verilmişdi (kontekstdə müştəri var) və o yazır ki "təqdimat olundu/keçdi/etdim/göstərdim" → automation_transition(trigger=presentation_done)
- Əgər istifadəçiyə quraşdırma tapşırığı verilmişdi və o yazır ki "quraşdırdım/bitdi/qurdum" → automation_transition(trigger=installation_done)
- Əgər istifadəçi yazır "satdım/aldı" → automation_transition(trigger=sold)
- Əgər istifadəçi yazır "görüş təyin olundu/görüşdüm" → automation_transition(trigger=meeting_set)
- Əgər istifadəçi yazır "imtina etdi/istəmir" → automation_transition(trigger=refused)
- Əgər istifadəçi yazır "qiymət göndərdim/qiymət verdim" → automation_transition(trigger=send_price)
- SUAL VERMƏ əgər nə etmək lazım olduğu açıqdırsa! Birbaşa hərəkət et.
- "Nə etmək istəyirsiniz?" və ya "tapşırıq yaradım yoxsa mərhələ dəyişim?" kimi suallar QADAĞANDIR əgər kontekstdən məlum olur ki nə etmək lazımdır.
- Əgər istifadəçi işin NƏTİCƏSİNİ bildirir ("etdim", "bitdi", "olundu") → bu automation_transition-dır, create_task DEYİL!
- HƏR ZAMAN kontekstdəki müştərinin phone-unu istifadə et, nömrə soruşma!

═══ REAL MİSALLAR ═══

Misal 1 (Şamil göndərir): "Müştəriyə getdim, təlimat verdim. Menyu yazılıb, şəkillər qalıb. QR menyu mobilə yüklənmir, ağ ekran qalır. Həm də ofisiant sifariş funksiyası lazımdır."
Cavab:
{{"actions": [
  {{"action": "add_note", "phone": "994503203209", "text": "Müştəriyə gedildi, təlimat verildi. Menyu yazılıb."}},
  {{"action": "create_task", "phone": "994503203209", "date": "07.07.2026", "time": "10:00", "text": "Menyu üçün şəkilləri yükləmək", "assign_to": 10932455, "urgency": "normal"}},
  {{"action": "create_task", "phone": "994503203209", "date": "06.07.2026", "time": "10:00", "text": "BAQ: QR menyu mobilə yüklənmir, ağ ekran görünür. Təcili düzəltmək lazımdır.", "assign_to": 10932455, "urgency": "high"}},
  {{"action": "create_task", "phone": "994503203209", "date": null, "time": "10:00", "text": "Feature request: QR menyuya ofisiant vasitəsilə sifariş funksiyası əlavə etmək", "assign_to": 10932455, "urgency": "normal"}}
]}}

Misal 2 (Admin göndərir): "Bu müştəriyə təqdimat lazımdır"
Cavab:
{{"actions": [{{"action": "automation_transition", "phone": "994503203209", "trigger": "need_presentation"}}]}}

Misal 3 (Şamil göndərir): "Sabah müştəriyə yenidən gedəcəm, əlavə təlimat verəcəm"
Cavab:
{{"actions": [{{"action": "create_task", "phone": "994503203209", "date": "07.07.2026", "time": "10:00", "text": "Müştəriyə yenidən getmək, əlavə təlimat vermək", "assign_to": 15532668, "urgency": "normal"}}]}}

Misal 4: "Əvvəlki tapşırıqları bağla"
Cavab:
{{"actions": [{{"action": "complete_tasks", "phone": "994503203209"}}]}}

Misal 5 (Admin göndərir): "Satdım bu müştərini"
Cavab:
{{"actions": [{{"action": "automation_transition", "phone": "994503203209", "trigger": "sold"}}]}}

Misal 6 (Soltan göndərir, kontekstdə müştəri var): "Təqdimatı etdim"
Cavab:
{{"actions": [{{"action": "automation_transition", "phone": "994503203209", "trigger": "presentation_done"}}]}}

Misal 7 (Soltan göndərir, kontekstdə müştəri var): "Quraşdırma bitdi"
Cavab:
{{"actions": [{{"action": "automation_transition", "phone": "994503203209", "trigger": "installation_done"}}]}}

Misal 8 (Şamil göndərir, kontekstdə müştəri var): "Müştəri almaq istəyir"
Cavab:
{{"actions": [{{"action": "automation_transition", "phone": "994503203209", "trigger": "new_order"}}]}}

═══ HƏRƏKƏTLƏRİN SİYAHISI ═══

1. find_contact — Müştərini telefon nömrəsi ilə tapmaq. Params: phone
2. create_task — Tapşırıq yaratmaq. Params: phone, date(DD.MM.YYYY), time(HH:MM), text, assign_to, urgency("normal"/"high")
3. add_note — Keçmiş hadisəni qeyd etmək. Params: phone, text
4. show_tasks_today — Bugünkü tapşırıqlar
5. show_tasks_tomorrow — Sabahkı tapşırıqlar
6. show_customer_tasks — Müştərinin tapşırıqları. Params: phone, date("today"/"tomorrow"/"all")
7. show_lead — Sövdələşmə linki. Params: phone
8. update_fields — Sövdələşmə sahələrini yeniləmək. Params: phone, fields
9. update_task — Sonuncu tapşırığı yeniləmək. Params: date, time, text
10. update_lead — Mərhələni dəyişmək. Params: text
11. update_contact — Kontakt məlumatlarını dəyişmək. Params: fields
12. automation_transition — Sövdələşmə keçidi. Params: phone, trigger
    Trigger-lər: new_order, meeting_set, sold, thinking, no_answer, refused, need_presentation, presentation_done, internal_discussion, discussion_done, installation_done, send_price
13. complete_tasks — Müştərinin tapşırıqlarını bağlamaq. Params: phone
14. ask_clarification — Sual vermək. Params: reply_text
15. unknown — Anlaşılmadı

═══ ÇIXIŞ FORMATI ═══
YALNIZ JSON qaytar, başqa heç nə yazma:
{{"actions": [{{"action": "...", "phone": "...", "date": "DD.MM.YYYY", "time": "HH:MM", "text": "...", "fields": {{}}, "trigger": "...", "assign_to": null, "urgency": "normal", "reply_text": "..."}}]}}

Bütün sahələr nullable. Hətta 1 action olsa belə — "actions" massivində qaytar."""

RESPONSE_FORMAT = {"type": "json_object"}

def parse_user_intent(user_message: str, chat_id: int) -> dict:
    now = datetime.now(tz=BAKU_TZ)
    current_dt = now.strftime("%d.%m.%Y %H:%M (%A)")
    ctx = get_ctx(chat_id)
    sender_kommo_id = get_kommo_user_id_for_chat(chat_id)
    sender_name = KOMMO_USERS.get(sender_kommo_id, "İstifadəçi") if sender_kommo_id else "İstifadəçi"
    context_parts = []
    context_parts.append(f"Mesaj göndərən: {sender_name} (Kommo ID: {sender_kommo_id})")
    if ctx["last_contact_name"] and ctx["last_phone"]:
        context_parts.append(
            f"Sonuncu tapılan müştəri: {ctx['last_contact_name']} (telefon: {ctx['last_phone']}). "
            f"Əvəzliklr ('ona', 'bu müştəriyə') → phone={ctx['last_phone']}"
        )
    if ctx["last_task_id"]:
        context_parts.append(f"Sonuncu tapşırıq: ID={ctx['last_task_id']}, {ctx['last_task_date']} {ctx['last_task_time']}")
    if ctx["last_lead_id"]:
        context_parts.append(f"Sonuncu sövdələşmə: ID={ctx['last_lead_id']}")
    context_block = "\n".join(context_parts) if context_parts else "Kontekst boşdur."

    try:
        logger.info(f"AI input from {sender_name} (chat={chat_id}), context phone={ctx.get('last_phone')}, lead={ctx.get('last_lead_id')}")
        logger.info(f"AI input text: {user_message[:300]}")
        response = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT.format(current_datetime=current_dt, context_block=context_block)},
                {"role": "user", "content": user_message},
            ],
            response_format=RESPONSE_FORMAT,
            timeout=60,
        )
        raw_content = response.choices[0].message.content
        logger.info(f"AI response: {raw_content[:500]}")
        result = json.loads(raw_content)
        # Support both old format {"action": ...} and new format {"actions": [...]}
        if "actions" not in result and "action" in result:
            # Old format - wrap in actions array
            result = {"actions": [result]}
        elif "actions" not in result:
            result = {"actions": [{"action": "unknown"}]}
        logger.info(f"Parsed {len(result['actions'])} actions: {[a.get('action') for a in result['actions']]}")
        return result
    except Exception as e:
        logger.error(f"AI parsing error: {e}\n{traceback.format_exc()}")
        return {"actions": [{"action": "unknown"}]}

# ─── Kommo API Helpers ───────────────────────────────────────────────────────

HEADERS = {
    "Authorization": f"Bearer {KOMMO_TOKEN}",
    "Content-Type": "application/json",
}

def search_contact_by_phone(phone: str) -> list:
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return []
    variants = []
    if len(digits) >= 9:
        variants.append(digits[-9:])
    if len(digits) >= 7:
        variants.append(digits[-7:])
    if len(digits) >= 9:
        variants.append(f"+994{digits[-9:]}")
    variants.append(digits)
    seen = set()
    unique = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    for q in unique:
        url = f"{KOMMO_BASE_URL}/api/v4/contacts"
        try:
            resp = requests.get(url, headers=HEADERS, params={"query": q}, timeout=15)
            logger.info(f"Search [{q}]: {resp.status_code}")
            if resp.status_code == 200:
                contacts = resp.json().get("_embedded", {}).get("contacts", [])
                if contacts:
                    return contacts
        except Exception as e:
            logger.error(f"Search error [{q}]: {e}")
    return []

def get_contact_details(contact_id: int) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}?with=leads"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Contact details error: {e}")
    return None

def get_lead_details(lead_id: int) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}"
    try:
        resp = requests.get(url, headers=HEADERS, params={"with": "contacts"}, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Lead details error: {e}")
    return None

def get_contact_notes(contact_id: int) -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}/notes"
    try:
        resp = requests.get(url, headers=HEADERS, params={"limit": 5, "order[created_at]": "desc"}, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("notes", [])
    except:
        pass
    return []

def get_entity_tasks(entity_id: int, entity_type: str) -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks"
    params = {"filter[entity_id]": entity_id, "filter[entity_type]": entity_type, "filter[is_completed]": 0}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
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
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("tasks", [])
    except:
        pass
    return []

def get_all_incomplete_tasks() -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks"
    params = {"filter[is_completed]": 0, "limit": 250}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("tasks", [])
    except:
        pass
    return []

def create_task(entity_id: int, text: str, complete_till: int, responsible_user_id: int = None, entity_type: str = "contacts") -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks"
    payload = [{
        "text": text,
        "complete_till": complete_till,
        "entity_id": entity_id,
        "entity_type": entity_type,
        "responsible_user_id": responsible_user_id or 10932455,
    }]
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json()
    except Exception as e:
        logger.error(f"Create task error: {e}")
    return None

def add_note(entity_id: int, text: str, entity_type: str = "contacts") -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/{entity_type}/{entity_id}/notes"
    payload = [{"note_type": "common", "params": {"text": text}}]
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=15)
        if resp.status_code in (200, 201):
            return resp.json()
    except Exception as e:
        logger.error(f"Add note error: {e}")
    return None

def update_lead_kommo(lead_id: int, data: dict) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/leads/{lead_id}"
    try:
        resp = requests.patch(url, headers=HEADERS, json=data, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Update lead error: {e}")
    return None

def update_task_kommo(task_id: int, data: dict) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/tasks/{task_id}"
    try:
        resp = requests.patch(url, headers=HEADERS, json=data, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Update task error: {e}")
    return None

def update_contact_kommo(contact_id: int, data: dict) -> dict | None:
    url = f"{KOMMO_BASE_URL}/api/v4/contacts/{contact_id}"
    try:
        resp = requests.patch(url, headers=HEADERS, json=data, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Update contact error: {e}")
    return None

def get_lead_custom_fields() -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/leads/custom_fields"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("custom_fields", [])
    except:
        pass
    return []

def get_user_name(user_id: int) -> str:
    return KOMMO_USERS.get(user_id, f"User {user_id}")

def get_leads_by_status(status_id: int) -> list:
    url = f"{KOMMO_BASE_URL}/api/v4/leads"
    params = {"filter[statuses][0][pipeline_id]": PIPELINE_ID, "filter[statuses][0][status_id]": status_id, "limit": 50}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("_embedded", {}).get("leads", [])
    except:
        pass
    return []

def fuzzy_match_field(name: str, fields: list) -> int | None:
    name_lower = name.lower().strip()
    for f in fields:
        f_name = f.get("name", "")
        if not f_name:
            continue
        if f_name.lower() == name_lower:
            return f.get("id")
    for f in fields:
        f_name = f.get("name", "")
        if not f_name:
            continue
        if name_lower in f_name.lower() or f_name.lower() in name_lower:
            return f.get("id")
    return None

# ─── Format Contact Info ─────────────────────────────────────────────────────

def format_contact_info(contact: dict, notes: list = None, tasks: list = None) -> str:
    name = contact.get("name") or contact.get("first_name") or "Adsız"
    created_at = contact.get("created_at", 0)
    created_str = datetime.fromtimestamp(created_at, tz=BAKU_TZ).strftime("%d.%m.%Y") if created_at else "—"
    responsible_id = contact.get("responsible_user_id")
    manager_name = get_user_name(responsible_id) if responsible_id else "—"

    phones, emails, other_fields = [], [], []
    for field in (contact.get("custom_fields_values") or []):
        field_name = field.get("field_name", "Sahə")
        code = field.get("field_code", "")
        vals = field.get("values", [])
        values_str = ", ".join([str(v.get("value", "")) for v in vals if v.get("value")])
        if not values_str:
            continue
        if code == "PHONE":
            phones.append(values_str)
        elif code == "EMAIL":
            emails.append(values_str)
        else:
            other_fields.append(f"{field_name}: {values_str}")

    leads = contact.get("_embedded", {}).get("leads", [])

    msg = f"👤 *{name}*\n"
    msg += f"📅 Yaradılıb: {created_str}\n"
    msg += f"👤 Menecer: {manager_name}\n\n"
    if phones:
        msg += f"📞 Telefon: {', '.join(phones)}\n"
    if emails:
        msg += f"📧 Email: {', '.join(emails)}\n"
    if other_fields:
        msg += "\n⚙️ *Əlavə sahələr:*\n"
        for fs in other_fields:
            msg += f"  • {fs}\n"
    if leads:
        msg += "\n📋 *Sövdələşmələr:*\n"
        for lead_data in leads[:5]:
            lead_id = lead_data.get("id")
            lead = get_lead_details(lead_id)
            if lead:
                lead_name = lead.get("name", "Adsız")
                lead_price = lead.get("price", 0)
                status_id = lead.get("status_id")
                stage_name = STAGE_NAMES.get(status_id, f"ID:{status_id}")
                msg += f"  • {lead_name} | {lead_price}₼\n"
                msg += f"    Mərhələ: {stage_name}\n"
                msg += f"    🔗 {KOMMO_BASE_URL}/leads/detail/{lead_id}\n"
    if tasks:
        msg += "\n📌 *Aktiv tapşırıqlar:*\n"
        for task in tasks[:5]:
            t_text = task.get("text", "Təsvirsiz")
            till = task.get("complete_till", 0)
            dt = datetime.fromtimestamp(till, tz=BAKU_TZ).strftime("%d.%m.%Y %H:%M")
            msg += f"  • ⏰ {dt} — {t_text}\n"
    if notes:
        msg += "\n📝 *Son qeydlər:*\n"
        for note in (notes or [])[:5]:
            params = note.get("params", {})
            note_text = params.get("text", "") if params else ""
            if note_text:
                if len(note_text) > 100:
                    note_text = note_text[:100] + "..."
                created = note.get("created_at", 0)
                dt = datetime.fromtimestamp(created, tz=BAKU_TZ).strftime("%d.%m.%Y")
                msg += f"  • [{dt}] {note_text}\n"
    return msg

# ─── Automation Transitions ──────────────────────────────────────────────────

async def execute_automation_transition(update: Update, phone: str, trigger: str, chat_id: int):
    """Execute automated pipeline transition based on trigger."""
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return

    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    full_contact = get_contact_details(contact["id"])
    leads = (full_contact or {}).get("_embedded", {}).get("leads", [])

    if not leads:
        await update.message.reply_text(f"❌ *{contact_name}* müştərisinin sövdələşməsi yoxdur.", parse_mode="Markdown")
        return

    lead_id = leads[0]["id"]
    set_last_contact(chat_id, phone, contact["id"], contact_name, lead_id=lead_id)

    now = datetime.now(tz=BAKU_TZ)
    actions_taken = []

    if trigger == "new_order":
        # Move to "yeni sifariş", assign to Şamil, create task
        update_lead_kommo(lead_id, {"status_id": STAGES["yeni_sifaris"], "pipeline_id": PIPELINE_ID, "responsible_user_id": 15532668})
        task_time = (now + timedelta(hours=1)).replace(minute=0, second=0)
        create_task(lead_id, "Müştəri ilə əlaqə saxla", int(task_time.timestamp()), responsible_user_id=15532668, entity_type="leads")
        actions_taken.append(f"✅ Sövdələşmə *'yeni sifariş'* mərhələsinə keçirildi.")
        actions_taken.append(f"👤 Məsul: Şamil Əliyev")
        actions_taken.append(f"📋 Tapşırıq yaradıldı: _Müştəri ilə əlaqə saxla_")

    elif trigger == "meeting_set":
        update_lead_kommo(lead_id, {"status_id": STAGES["gorus"], "pipeline_id": PIPELINE_ID})
        actions_taken.append(f"✅ Sövdələşmə *'görüş'* mərhələsinə keçirildi.")

    elif trigger == "sold":
        # Move to "quraşdırma", assign to Soltan, create task
        update_lead_kommo(lead_id, {"status_id": STAGES["qurashdirma"], "pipeline_id": PIPELINE_ID, "responsible_user_id": 15531960})
        task_time = (now + timedelta(hours=2)).replace(minute=0, second=0)
        create_task(lead_id, "Müştəri ilə əlaqə saxla, quraşdırma vaxtını təyin et", int(task_time.timestamp()), responsible_user_id=15531960, entity_type="leads")
        actions_taken.append(f"✅ Sövdələşmə *'quraşdırma'* mərhələsinə keçirildi.")
        actions_taken.append(f"👤 Məsul: Soltan Abbasov")
        actions_taken.append(f"📋 Tapşırıq yaradıldı: _Müştəri ilə əlaqə saxla, quraşdırma vaxtını təyin et_")

    elif trigger == "thinking":
        # Stay on current stage, create follow-up task in 3 days
        task_time = (now + timedelta(days=3)).replace(hour=10, minute=0, second=0)
        create_task(lead_id, "Follow-up: müştəri ilə əlaqə saxla", int(task_time.timestamp()), entity_type="leads")
        actions_taken.append(f"📋 3 gün sonra follow-up tapşırığı yaradıldı.")
        actions_taken.append(f"ℹ️ Sövdələşmə cari mərhələdə qaldı.")

    elif trigger == "no_answer":
        update_lead_kommo(lead_id, {"status_id": STAGES["cavab_gozlenilir"], "pipeline_id": PIPELINE_ID})
        task_time = (now + timedelta(days=5)).replace(hour=10, minute=0, second=0)
        create_task(lead_id, "Son cəhd — əlaqə saxla və ya bağla", int(task_time.timestamp()), entity_type="leads")
        actions_taken.append(f"✅ Sövdələşmə *'cavab gözlənilir'* mərhələsinə keçirildi.")
        actions_taken.append(f"📋 5 gün sonra tapşırıq: _Son cəhd — əlaqə saxla və ya bağla_")

    elif trigger == "refused":
        update_lead_kommo(lead_id, {"status_id": STAGES["imtina"], "pipeline_id": PIPELINE_ID})
        actions_taken.append(f"✅ Sövdələşmə *'imtina olundu'* mərhələsinə keçirildi.")

    elif trigger == "need_presentation":
        await ask_presentation_assignee(update, lead_id)
        return  # Stop here, callback will handle the rest

    elif trigger == "presentation_done":
        update_lead_kommo(lead_id, {"status_id": STAGES["teqdimat_olundu"], "pipeline_id": PIPELINE_ID})
        actions_taken.append(f"✅ Sövdələşmə *'Təqdimat olundu'* mərhələsinə keçirildi.")

    elif trigger == "internal_discussion":
        update_lead_kommo(lead_id, {"status_id": STAGES["daxili_muzakire"], "pipeline_id": PIPELINE_ID})
        actions_taken.append(f"✅ Sövdələşmə *'daxili müzakirə'* mərhələsinə keçirildi.")

    elif trigger == "discussion_done":
        update_lead_kommo(lead_id, {"status_id": STAGES["gorus"], "pipeline_id": PIPELINE_ID})
        actions_taken.append(f"✅ Müzakirə bitdi. Sövdələşmə *'görüş'* mərhələsinə qaytarıldı.")

    elif trigger == "installation_done":
        update_lead_kommo(lead_id, {"status_id": STAGES["ugurlu"], "pipeline_id": PIPELINE_ID})
        actions_taken.append(f"✅ Sövdələşmə *'uğurlu sifariş'* mərhələsinə keçirildi. 🎉")

    elif trigger == "send_price":
        update_lead_kommo(lead_id, {"status_id": STAGES["qiymet_teklifi"], "pipeline_id": PIPELINE_ID})
        actions_taken.append(f"✅ Sövdələşmə *'Qiymət təklifi'* mərhələsinə keçirildi.")

    else:
        actions_taken.append(f"⚠️ Naməlum trigger: {trigger}")

    # Add note about the transition
    note_text = f"Avtomatik keçid ({trigger}): {' | '.join(actions_taken)}"
    add_note(lead_id, note_text, "leads")

    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
    msg = f"🔄 *{contact_name}* üçün avtomatik əməliyyat:\n\n" + "\n".join(actions_taken) + f"\n\n🔗 {link}"
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

# ─── Action Executors ────────────────────────────────────────────────────────

async def execute_find_contact(update: Update, phone: str, chat_id: int = None):
    await update.message.reply_text("🔍 Müştəri axtarılır...")
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return
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
        msg = format_contact_info(full_contact, notes, tasks)
        try:
            await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception:
            # Fallback without Markdown if parsing fails
            await update.message.reply_text(msg, disable_web_page_preview=True)
        if chat_id is not None:
            contact_name = full_contact.get("name") or "Adsız"
            lead_id = leads[0]["id"] if leads else None
            set_last_contact(chat_id, phone, contact["id"], contact_name, lead_id=lead_id)

async def execute_create_task(update: Update, phone: str, date_str: str, time_str: str, task_text: str, chat_id: int = None):
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ)
        complete_till = int(dt.timestamp())
    except ValueError:
        await update.message.reply_text("⚠️ Tarix/vaxt formatı yanlışdır. İstifadə edin: DD.MM.YYYY HH:MM")
        return
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    # Get lead for link
    full_c = get_contact_details(contact["id"])
    c_leads = (full_c or {}).get("_embedded", {}).get("leads", [])
    c_lead_id = c_leads[0]["id"] if c_leads else None
    c_link = f"{KOMMO_BASE_URL}/leads/detail/{c_lead_id}" if c_lead_id else f"{KOMMO_BASE_URL}/contacts/detail/{contact['id']}"
    # Assign task to the user who creates it
    creator_kommo_id = get_kommo_user_id_for_chat(chat_id) if chat_id else None
    result = create_task(contact["id"], task_text, complete_till, responsible_user_id=creator_kommo_id)
    if result:
        await update.message.reply_text(
            f"✅ Tapşırıq yaradıldı!\n\n👤 Müştəri: {contact_name}\n📅 Tarix: {date_str} {time_str}\n📝 Mətn: {task_text}\n\n🔗 {c_link}",
            disable_web_page_preview=True
        )
        if chat_id is not None:
            task_id = result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
            if task_id:
                set_last_task(chat_id, task_id, date_str, time_str)
            set_last_contact(chat_id, phone, contact["id"], contact_name)
            # Notify Admin about tasks created by other users
            sender_kommo_id = get_kommo_user_id_for_chat(chat_id)
            if sender_kommo_id and sender_kommo_id != 10932455:
                admin_chat = get_chat_id_for_kommo_user(10932455)
                sender_name = KOMMO_USERS.get(sender_kommo_id, "Əməkdaş")
                if admin_chat:
                    try:
                        await update.get_bot().send_message(
                            admin_chat,
                            f"📢 *{sender_name}* yeni tapşırıq yaratdı:\n\n"
                            f"👤 Müştəri: {contact_name}\n"
                            f"📅 Tarix: {date_str} {time_str}\n"
                            f"📝 {task_text}\n\n🔗 {c_link}",
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                    except:
                        pass
    else:
        await update.message.reply_text("❌ Tapşırıq yaradılarkən xəta baş verdi.")

async def execute_add_note(update: Update, phone: str, note_text: str, chat_id: int = None):
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    result = add_note(contact["id"], note_text)
    if result:
        await update.message.reply_text(f"✅ Qeyd əlavə edildi!\n\n👤 Müştəri: {contact_name}\n📝 Mətn: {note_text}")
        if chat_id is not None:
            set_last_contact(chat_id, phone, contact["id"], contact_name)
    else:
        await update.message.reply_text("❌ Qeyd əlavə edilərkən xəta baş verdi.")

async def execute_show_tasks(update: Update, day: str = "today"):
    now = datetime.now(tz=BAKU_TZ)
    if day == "tomorrow":
        target_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        label = "sabah üçün"
    else:
        target_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = "bu gün üçün"
    target_end = target_start + timedelta(days=1)
    await update.message.reply_text(f"📅 Tapşırıqlar yüklənir ({label})...")
    
    # Get tasks for the user's Kommo ID if registered
    chat_id = update.message.chat_id
    kommo_uid = get_kommo_user_id_for_chat(chat_id)
    tasks = get_tasks(target_start, target_end, responsible_id=kommo_uid)
    
    if not tasks:
        await update.message.reply_text(f"✨ {label.capitalize()} tapşırıq yoxdur!")
        return
    msg = f"📅 *Tapşırıqlar {label} ({target_start.strftime('%d.%m.%Y')}):*\n\n"
    for i, task in enumerate(tasks, 1):
        t_text = task.get("text", "Təsvirsiz")
        dt = datetime.fromtimestamp(task.get("complete_till", 0), tz=BAKU_TZ)
        t_entity_id = task.get("entity_id")
        t_entity_type = task.get("entity_type", "leads")
        if t_entity_id and t_entity_type == "leads":
            t_link = f"\n🔗 {KOMMO_BASE_URL}/leads/detail/{t_entity_id}"
        elif t_entity_id and t_entity_type == "contacts":
            t_link = f"\n🔗 {KOMMO_BASE_URL}/contacts/detail/{t_entity_id}"
        else:
            t_link = ""
        msg += f"{i}. ⏰ {dt.strftime('%H:%M')} — {t_text}{t_link}\n"
    msg += f"\n📊 Cəmi: {len(tasks)}"
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

async def execute_create_task_with_assign(update: Update, phone: str, date_str: str, time_str: str, task_text: str, chat_id: int, assign_to: int = None, urgency: str = "normal"):
    """Create task with explicit assignment and urgency."""
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ)
        complete_till = int(dt.timestamp())
    except ValueError:
        await update.message.reply_text("⚠️ Tarix/vaxt formatı yanlışdır.")
        return
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    full_c = get_contact_details(contact["id"])
    c_leads = (full_c or {}).get("_embedded", {}).get("leads", [])
    c_lead_id = c_leads[0]["id"] if c_leads else None
    c_link = f"{KOMMO_BASE_URL}/leads/detail/{c_lead_id}" if c_lead_id else f"{KOMMO_BASE_URL}/contacts/detail/{contact['id']}"

    # Determine responsible user
    if assign_to:
        responsible_id = assign_to
    else:
        # Default: assign to the sender
        responsible_id = get_kommo_user_id_for_chat(chat_id) if chat_id else 10932455

    responsible_name = KOMMO_USERS.get(responsible_id, "")
    urgency_mark = "🔴 TƏCİLİ! " if urgency == "high" else ""

    result = create_task(contact["id"], task_text, complete_till, responsible_user_id=responsible_id)
    if result:
        msg = (
            f"✅ {urgency_mark}Tapşırıq yaradıldı!\n\n"
            f"👤 Müştəri: {contact_name}\n"
            f"📅 Tarix: {date_str} {time_str}\n"
            f"📝 Mətn: {task_text}\n"
            f"👤 Məsul: {responsible_name}\n\n🔗 {c_link}"
        )
        await update.message.reply_text(msg, disable_web_page_preview=True)
        if chat_id is not None:
            task_id = result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
            if task_id:
                set_last_task(chat_id, task_id, date_str, time_str)
            set_last_contact(chat_id, phone, contact["id"], contact_name, lead_id=c_lead_id)
        # Notify assigned user if different from sender
        sender_kommo_id = get_kommo_user_id_for_chat(chat_id) if chat_id else None
        if responsible_id != sender_kommo_id:
            assigned_chat = get_chat_id_for_kommo_user(responsible_id)
            sender_name = KOMMO_USERS.get(sender_kommo_id, "Əməkdaş") if sender_kommo_id else "Bot"
            if assigned_chat:
                try:
                    await update.get_bot().send_message(
                        assigned_chat,
                        f"📢 {urgency_mark}*{sender_name}* sizin üçün tapşırıq yaratdı:\n\n"
                        f"👤 Müştəri: {contact_name}\n"
                        f"📅 Tarix: {date_str} {time_str}\n"
                        f"📝 {task_text}\n\n🔗 {c_link}",
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                except:
                    pass
        # Notify Admin if sender is not admin
        if sender_kommo_id and sender_kommo_id != 10932455:
            admin_chat = get_chat_id_for_kommo_user(10932455)
            sender_name = KOMMO_USERS.get(sender_kommo_id, "Əməkdaş")
            if admin_chat:
                try:
                    await update.get_bot().send_message(
                        admin_chat,
                        f"📢 {urgency_mark}*{sender_name}* tapşırıq yaratdı:\n\n"
                        f"👤 Müştəri: {contact_name}\n"
                        f"📅 Tarix: {date_str} {time_str}\n"
                        f"📝 {task_text}\n"
                        f"👤 Məsul: {responsible_name}\n\n🔗 {c_link}",
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                except:
                    pass
    else:
        await update.message.reply_text("❌ Tapşırıq yaradılarkən xəta baş verdi.")

async def execute_complete_tasks(update: Update, phone: str, chat_id: int = None):
    """Complete all open tasks for a customer."""
    if not phone:
        await update.message.reply_text("⚠️ Müştəri nömrəsi lazımdır.")
        return
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    full_contact = get_contact_details(contact["id"])
    leads = (full_contact or {}).get("_embedded", {}).get("leads", [])

    # Get all tasks for this contact and leads
    all_tasks = get_entity_tasks(contact["id"], "contacts")
    for lead in leads:
        lead_tasks = get_entity_tasks(lead["id"], "leads")
        all_tasks.extend(lead_tasks)

    if not all_tasks:
        await update.message.reply_text(f"✨ *{contact_name}* müştərisi üçün açıq tapşırıq yoxdur.", parse_mode="Markdown")
        return

    completed = 0
    for task in all_tasks:
        task_id = task.get("id")
        if task_id:
            res = update_task_kommo(task_id, {"is_completed": True})
            if res:
                completed += 1

    c_leads = (full_contact or {}).get("_embedded", {}).get("leads", [])
    c_lead_id = c_leads[0]["id"] if c_leads else None
    c_link = f"{KOMMO_BASE_URL}/leads/detail/{c_lead_id}" if c_lead_id else f"{KOMMO_BASE_URL}/contacts/detail/{contact['id']}"
    await update.message.reply_text(
        f"✅ *{contact_name}* müştərisi üçün {completed} tapşırıq tamamlandı.\n\n🔗 {c_link}",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    # Notify Admin
    sender_kommo_id = get_kommo_user_id_for_chat(chat_id) if chat_id else None
    if sender_kommo_id and sender_kommo_id != 10932455:
        admin_chat = get_chat_id_for_kommo_user(10932455)
        sender_name = KOMMO_USERS.get(sender_kommo_id, "Əməkdaş")
        if admin_chat:
            try:
                await update.get_bot().send_message(
                    admin_chat,
                    f"📢 *{sender_name}* {completed} tapşırığı tamamladı:\n\n"
                    f"👤 Müştəri: {contact_name}\n\n🔗 {c_link}",
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            except:
                pass

async def execute_show_customer_tasks(update: Update, phone: str, day_filter: str = "all", chat_id: int = None):
    """Show tasks for a specific customer (contact/lead)."""
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    full_contact = get_contact_details(contact["id"])
    leads = (full_contact or {}).get("_embedded", {}).get("leads", [])

    # Collect tasks from contact and all leads
    all_tasks = get_entity_tasks(contact["id"], "contacts")
    for lead in leads:
        lead_tasks = get_entity_tasks(lead["id"], "leads")
        all_tasks.extend(lead_tasks)

    now = datetime.now(tz=BAKU_TZ)
    if day_filter == "today":
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        all_tasks = [t for t in all_tasks if day_start.timestamp() <= t.get("complete_till", 0) < day_end.timestamp()]
        label = "bu gün üçün"
    elif day_filter == "tomorrow":
        day_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        all_tasks = [t for t in all_tasks if day_start.timestamp() <= t.get("complete_till", 0) < day_end.timestamp()]
        label = "sabah üçün"
    else:
        label = "bütün"

    if not all_tasks:
        await update.message.reply_text(f"✨ *{contact_name}* müştərisi üçün {label} tapşırıq yoxdur!", parse_mode="Markdown")
        return

    # Sort by time
    all_tasks.sort(key=lambda t: t.get("complete_till", 0))
    msg = f"📅 *{contact_name}* — tapşırıqlar ({label}):*\n\n"
    for i, task in enumerate(all_tasks, 1):
        t_text = task.get("text", "Təsvirsiz")
        till = task.get("complete_till", 0)
        dt = datetime.fromtimestamp(till, tz=BAKU_TZ)
        msg += f"{i}. ⏰ {dt.strftime('%d.%m %H:%M')} — {t_text}\n"
    msg += f"\n📊 Cəmi: {len(all_tasks)}"
    await update.message.reply_text(msg, parse_mode="Markdown")
    if chat_id is not None:
        lead_id = leads[0]["id"] if leads else None
        set_last_contact(chat_id, phone, contact["id"], contact_name, lead_id=lead_id)

async def execute_show_lead(update: Update, phone: str, chat_id: int = None):
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    full_contact = get_contact_details(contact["id"])
    leads = (full_contact or {}).get("_embedded", {}).get("leads", [])
    if not leads:
        await update.message.reply_text(f"❌ *{contact_name}* müştərisinin sövdələşməsi yoxdur.", parse_mode="Markdown")
        return
    msg = f"🔗 *{contact_name} — sövdələşmələr:*\n\n"
    for ld in leads[:5]:
        lead = get_lead_details(ld.get("id"))
        if lead:
            msg += f"• {lead.get('name', 'Adsız')}\n  🔗 {KOMMO_BASE_URL}/leads/detail/{lead.get('id')}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
    if chat_id is not None:
        set_last_contact(chat_id, phone, contact["id"], contact_name, lead_id=leads[0]["id"] if leads else None)

async def execute_update_fields(update: Update, phone: str, fields_to_update: dict, chat_id: int = None):
    await update.message.reply_text("⚙️ Sorğu emal olunur...")
    contacts = search_contact_by_phone(phone)
    if not contacts:
        await update.message.reply_text(f"❌ `{phone}` nömrəli müştəri tapılmadı.", parse_mode="Markdown")
        return
    contact = contacts[0]
    full_contact = get_contact_details(contact["id"])
    leads = (full_contact or {}).get("_embedded", {}).get("leads", [])
    info_parts = [f"{k}: {v}" for k, v in fields_to_update.items()]
    note_text = "AI Yeniləmə:\n" + "\n".join(info_parts)
    entity_id = contact["id"]
    entity_type = "contacts"
    lead_id = None
    if leads:
        lead_id = leads[0]["id"]
        entity_id = lead_id
        entity_type = "leads"
    add_note(entity_id, note_text, entity_type)
    if not leads:
        await update.message.reply_text(f"ℹ️ Sövdələşmə yoxdur. Məlumat qeydlərə yazıldı.")
        return
    available_fields = get_lead_custom_fields()
    update_data = {"custom_fields_values": []}
    matched = []
    for f_name, f_val in fields_to_update.items():
        if f_name.lower() in ["цена", "qiymət", "price"]:
            try:
                update_data["price"] = int(re.sub(r"\D", "", str(f_val)))
                matched.append(f"Qiymət: {f_val}")
                continue
            except:
                pass
        field_id = fuzzy_match_field(f_name, available_fields)
        if field_id:
            update_data["custom_fields_values"].append({"field_id": field_id, "values": [{"value": f_val}]})
            matched.append(f"{f_name}: {f_val}")
    if update_data["custom_fields_values"] or "price" in update_data:
        update_lead_kommo(lead_id, update_data)
        report = "\n".join([f"• {f}" for f in matched])
        await update.message.reply_text(f"✅ Sahələr yeniləndi!\n\n{report}\n\n(Məlumat həmçinin qeydlərə yazıldı)")
    else:
        await update.message.reply_text("ℹ️ CRM sahələri tapılmadı, məlumat qeydlərə yazıldı.")

async def execute_update_task(update: Update, chat_id: int, date_str: str = None, time_str: str = None, text: str = None):
    ctx = get_ctx(chat_id)
    task_id = ctx.get("last_task_id")
    if not task_id:
        await update.message.reply_text("⚠️ Yeniləmək üçün sonuncu tapşırıq tapılmadı.")
        return
    update_data = {}
    if text:
        update_data["text"] = text
    if date_str or time_str:
        d = date_str or ctx.get("last_task_date") or datetime.now(tz=BAKU_TZ).strftime("%d.%m.%Y")
        t = time_str or ctx.get("last_task_time") or "10:00"
        try:
            dt = datetime.strptime(f"{d} {t}", "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ)
            update_data["complete_till"] = int(dt.timestamp())
            ctx["last_task_date"] = d
            ctx["last_task_time"] = t
        except:
            await update.message.reply_text("⚠️ Tarix/vaxt formatı yanlışdır.")
            return
    if update_data:
        res = update_task_kommo(task_id, update_data)
        if res:
            await update.message.reply_text("✅ Tapşırıq yeniləndi!")
        else:
            await update.message.reply_text("❌ Tapşırığı yeniləyərkən xəta baş verdi.")
    else:
        await update.message.reply_text("⚠️ Yeniləmək üçün məlumat yoxdur.")

async def execute_update_lead_stage(update: Update, chat_id: int, status_name: str):
    ctx = get_ctx(chat_id)
    lead_id = ctx.get("last_lead_id")
    if not lead_id:
        await update.message.reply_text("⚠️ Yeniləmək üçün sonuncu sövdələşmə tapılmadı.")
        return
    # Find matching stage
    name_lower = status_name.lower().strip()
    matched_id = None
    matched_name = None
    for sid, sname in STAGE_NAMES.items():
        if sname.lower() == name_lower or name_lower in sname.lower() or sname.lower() in name_lower:
            matched_id = sid
            matched_name = sname
            break
    if matched_id:
        if matched_id == STAGES["teqdimat"]:
            await ask_presentation_assignee(update, lead_id)
        else:
            res = update_lead_kommo(lead_id, {"status_id": matched_id, "pipeline_id": PIPELINE_ID})
            if res:
                await update.message.reply_text(f"✅ Mərhələ dəyişdirildi: *{matched_name}*", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Mərhələni dəyişərkən xəta baş verdi.")
    else:
        await update.message.reply_text(f"⚠️ '{status_name}' adlı mərhələ tapılmadı.")

async def execute_update_contact(update: Update, chat_id: int, fields: dict):
    ctx = get_ctx(chat_id)
    contact_id = ctx.get("last_contact_id")
    if not contact_id:
        await update.message.reply_text("⚠️ Yeniləmək üçün sonuncu kontakt tapılmadı.")
        return
    update_data = {}
    if "name" in fields:
        update_data["name"] = fields["name"]
    custom_fields = []
    for k, v in fields.items():
        if k.lower() in ["name", "first_name", "ad"]:
            update_data["name"] = v
            continue
        code = None
        if k.lower() in ["phone", "telefon", "tel"]:
            code = "PHONE"
        elif k.lower() in ["email", "e-mail", "poçt"]:
            code = "EMAIL"
        if code:
            custom_fields.append({"field_code": code, "values": [{"value": v}]})
    if custom_fields:
        update_data["custom_fields_values"] = custom_fields
    if update_data:
        res = update_contact_kommo(contact_id, update_data)
        if res:
            await update.message.reply_text("✅ Kontakt yeniləndi!")
        else:
            await update.message.reply_text("❌ Kontaktı yeniləyərkən xəta baş verdi.")
    else:
        await update.message.reply_text("⚠️ Yeniləmək üçün məlumat yoxdur.")

# ─── Process Text Intent ─────────────────────────────────────────────────────

async def process_text_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    chat_id = update.message.chat_id
    ctx = get_ctx(chat_id)

    # Check if user is providing task result text (overdue task flow)
    if chat_id in _pending_task_result:
        pending = _pending_task_result.pop(chat_id)
        task_id = pending["task_id"]
        task_text = pending["task_text"]
        entity_id = pending.get("entity_id")
        entity_type = pending.get("entity_type", "leads")
        # Close task in Kommo with result as comment
        result_text = user_text.strip()
        update_task_kommo(task_id, {"is_completed": True, "result": {"text": result_text}})
        # Add note with the result
        if entity_id:
            add_note(entity_id, f"Tapşırıq bağlandı: {task_text}\nNəticə: {result_text}", entity_type)
        # Build link
        link = ""
        if entity_type == "leads" and entity_id:
            link = f"\n\n🔗 {KOMMO_BASE_URL}/leads/detail/{entity_id}"
        elif entity_type == "contacts" and entity_id:
            link = f"\n\n🔗 {KOMMO_BASE_URL}/contacts/detail/{entity_id}"
        await update.message.reply_text(
            f"✅ Tapşırıq bağlandı!\n\n📝 {task_text}\n💬 Nəticə: {result_text}{link}",
            disable_web_page_preview=True
        )
        # Notify Admin if not admin
        sender_kommo_id = get_kommo_user_id_for_chat(chat_id)
        if sender_kommo_id and sender_kommo_id != 10932455:
            admin_chat = get_chat_id_for_kommo_user(10932455)
            sender_name = KOMMO_USERS.get(sender_kommo_id, "Əməkdaş")
            if admin_chat:
                try:
                    await context.bot.send_message(
                        admin_chat,
                        f"📢 *{sender_name}* tapşırığı bağladı:\n\n"
                        f"📝 {task_text}\n💬 Nəticə: {result_text}{link}",
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                except:
                    pass
        return

    # Handle pending actions first
    if ctx["pending_action"] and ctx["pending_missing"]:
        missing = ctx["pending_missing"]
        params = ctx["pending_params"]

        if missing == "phone":
            digits = re.sub(r"\D", "", user_text)
            if digits and len(digits) >= 7:
                params["phone"] = digits
                # Check if we still need more params
                action = ctx["pending_action"]
                clear_pending(chat_id)
                if action == "create_task":
                    if not params.get("text"):
                        set_pending(chat_id, "create_task", params, "text")
                        await update.message.reply_text("📝 Tapşırığın mətnini göstərin:")
                        return
                    if not params.get("date"):
                        set_pending(chat_id, "create_task", params, "date")
                        await update.message.reply_text("📅 Tarixi göstərin (məs: sabah, 10.07.2026):")
                        return
                    await execute_create_task(update, params["phone"], params.get("date"), params.get("time", "10:00"), params["text"], chat_id)
                elif action == "add_note":
                    if not params.get("text"):
                        set_pending(chat_id, "add_note", params, "text")
                        await update.message.reply_text("📝 Qeyd mətnini göstərin:")
                        return
                    await execute_add_note(update, params["phone"], params["text"], chat_id)
                elif action == "find_contact":
                    await execute_find_contact(update, params["phone"], chat_id)
                return
            else:
                await update.message.reply_text("⚠️ Düzgün telefon nömrəsi göstərin:")
                return

        elif missing == "text":
            params["text"] = user_text
            action = ctx["pending_action"]
            clear_pending(chat_id)
            if action == "create_task":
                if not params.get("date"):
                    set_pending(chat_id, "create_task", params, "date")
                    await update.message.reply_text("📅 Tarixi göstərin (məs: sabah, 10.07.2026):")
                    return
                await execute_create_task(update, params["phone"], params["date"], params.get("time", "10:00"), params["text"], chat_id)
            elif action == "add_note":
                await execute_add_note(update, params["phone"], params["text"], chat_id)
            return

        elif missing == "date":
            resolved = resolve_date_from_text(user_text)
            if resolved:
                params["date"] = resolved
                time_in_text = resolve_time_from_text(user_text)
                if time_in_text:
                    params["time"] = time_in_text
                action = ctx["pending_action"]
                clear_pending(chat_id)
                if action == "create_task":
                    await execute_create_task(update, params["phone"], params["date"], params.get("time", "10:00"), params["text"], chat_id)
                return
            else:
                await update.message.reply_text("⚠️ Tarixi anlamadım. Yenidən göstərin (məs: sabah, 10.07.2026):")
                return

    # Parse intent via LLM (now returns {"actions": [...]})
    parsed = parse_user_intent(user_text, chat_id)
    actions_list = parsed.get("actions", [{"action": "unknown"}])

    # Process each action sequentially
    for intent in actions_list:
        await dispatch_single_action(update, context, intent, chat_id, ctx)

async def dispatch_single_action(update: Update, context: ContextTypes.DEFAULT_TYPE, intent: dict, chat_id: int, ctx: dict):
    """Dispatch a single parsed action."""
    action = intent.get("action", "unknown")
    phone = intent.get("phone")
    reply_text = intent.get("reply_text")
    assign_to = intent.get("assign_to")
    urgency = intent.get("urgency", "normal")

    # Use context phone if not provided
    if not phone and action in ["find_contact", "create_task", "add_note", "show_lead", "update_fields", "automation_transition", "complete_tasks", "show_customer_tasks"]:
        phone = ctx.get("last_phone")

    if action == "find_contact":
        if phone:
            await execute_find_contact(update, phone, chat_id)
        else:
            await update.message.reply_text("📞 Müştərinin telefon nömrəsini göstərin:")

    elif action == "create_task":
        date_str = intent.get("date")
        time_str = intent.get("time") or "10:00"
        task_text = intent.get("text")
        if not phone:
            await update.message.reply_text(f"⚠️ Tapşırıq üçün müştəri nömrəsi lazımdır: _{task_text}_", parse_mode="Markdown")
        elif not task_text:
            await update.message.reply_text("⚠️ Tapşırığın mətni boşdur.")
        else:
            if not date_str:
                date_str = datetime.now(tz=BAKU_TZ).strftime("%d.%m.%Y")
            # If sender is Admin → always ask who to assign
            sender_kommo_id = get_kommo_user_id_for_chat(chat_id)
            if sender_kommo_id == 10932455:
                # Admin: show assignment buttons
                await ask_task_assignee(update, context, phone, date_str, time_str, task_text, urgency)
            else:
                await execute_create_task_with_assign(update, phone, date_str, time_str, task_text, chat_id, assign_to, urgency)

    elif action == "add_note":
        note_text = intent.get("text")
        if phone and note_text:
            await execute_add_note(update, phone, note_text, chat_id)
        else:
            await update.message.reply_text("⚠️ Qeyd üçün məlumat çatışmayır.")

    elif action == "show_tasks_today":
        await execute_show_tasks(update, "today")

    elif action == "show_tasks_tomorrow":
        await execute_show_tasks(update, "tomorrow")

    elif action == "show_customer_tasks":
        c_phone = phone or ctx.get("last_phone")
        if c_phone:
            await execute_show_customer_tasks(update, c_phone, intent.get("date", "all"), chat_id)
        else:
            await update.message.reply_text("📞 Müştərinin telefon nömrəsini göstərin:")

    elif action == "show_lead":
        if phone:
            await execute_show_lead(update, phone, chat_id)
        else:
            await update.message.reply_text("📞 Müştərinin telefon nömrəsini göstərin:")

    elif action == "update_fields":
        fields = intent.get("fields")
        if phone and fields:
            await execute_update_fields(update, phone, fields, chat_id)
        else:
            await update.message.reply_text("⚠️ Sahələri yeniləmək üçün məlumat çatışmayır.")

    elif action == "update_task":
        date_str = intent.get("date")
        time_str = intent.get("time")
        task_text = intent.get("text")
        await execute_update_task(update, chat_id, date_str, time_str, task_text)

    elif action == "update_lead":
        status_name = intent.get("text")
        if status_name:
            await execute_update_lead_stage(update, chat_id, status_name)
        else:
            await update.message.reply_text("⚠️ Yeni mərhələnin adını göstərin.")

    elif action == "update_contact":
        fields = intent.get("fields")
        if fields:
            await execute_update_contact(update, chat_id, fields)
        else:
            await update.message.reply_text("⚠️ Kontakt məlumatları çatışmayır.")

    elif action == "automation_transition":
        trigger = intent.get("trigger")
        if phone and trigger:
            sender_kommo_id = get_kommo_user_id_for_chat(chat_id)
            if sender_kommo_id != 10932455:
                # Non-admin: send to Admin for confirmation
                await send_admin_confirmation(update, context, phone, trigger, chat_id, sender_kommo_id)
            else:
                # Admin: execute directly
                await execute_automation_transition(update, phone, trigger, chat_id)
        elif not phone:
            await update.message.reply_text("⚠️ Müştərinin telefon nömrəsini göstərin.")
        else:
            await update.message.reply_text("⚠️ Keçid növünü müəyyən edə bilmədim.")

    elif action == "complete_tasks":
        await execute_complete_tasks(update, phone, chat_id)

    elif action == "ask_clarification":
        question = reply_text or "🤔 Zəhmət olmasa daha ətraflı izah edin."
        await update.message.reply_text(question)

    else:
        await update.message.reply_text(
            reply_text or "🤔 Sorğunu tam anlamadım. Zəhmət olmasa yenidən formalaşdırın."
        )

# ─── Bot Handlers ────────────────────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    users = load_users()
    if str(chat_id) in users:
        role = users[str(chat_id)].get("role", "")
        welcome = (
            f"🤖 *Kommo CRM Bot + AI*\n\n"
            f"Salam! Rolunuz: *{role}*\n\n"
            f"💬 Mənə mətn yazın, səsli mesaj göndərin və ya əmrlərdən istifadə edin.\n\n"
            f"📋 *Əmrlər:*\n"
            f"/find — Müştəri axtar\n"
            f"/task — Tapşırıq yarat\n"
            f"/note — Qeyd əlavə et\n"
            f"/tasks — Bugünkü tapşırıqlar\n"
            f"/tomorrow — Sabahkı tapşırıqlar\n"
            f"/lead — Sövdələşməyə link\n"
            f"/role — Rolu dəyiş"
        )
        await update.message.reply_text(welcome, parse_mode="Markdown")
    else:
        # Registration
        keyboard = [
            [InlineKeyboardButton("👑 Admin (Texniki Destek)", callback_data="role_admin")],
            [InlineKeyboardButton("💼 Satış meneceri (Şamil Əliyev)", callback_data="role_sales")],
            [InlineKeyboardButton("🔧 Texnik (Soltan Abbasov)", callback_data="role_tech")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🤖 *Kommo CRM Bot + AI*\n\n"
            "Salam! Zəhmət olmasa rolunuzu seçin:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def role_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👑 Admin (Texniki Destek)", callback_data="role_admin")],
        [InlineKeyboardButton("💼 Satış meneceri (Şamil Əliyev)", callback_data="role_sales")],
        [InlineKeyboardButton("🔧 Texnik (Soltan Abbasov)", callback_data="role_tech")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Yeni rolunuzu seçin:", reply_markup=reply_markup)

async def role_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    users = load_users()
    if data == "role_admin":
        users[str(chat_id)] = {"kommo_user_id": 10932455, "role": "Admin (Texniki Destek)", "name": "Texniki Destek"}
    elif data == "role_sales":
        users[str(chat_id)] = {"kommo_user_id": 15532668, "role": "Satış meneceri", "name": "Şamil Əliyev"}
    elif data == "role_tech":
        users[str(chat_id)] = {"kommo_user_id": 15531960, "role": "Texnik", "name": "Soltan Abbasov"}
    save_users(users)

    role = users[str(chat_id)]["role"]
    await query.edit_message_text(
        f"✅ Rolunuz qeydə alındı: *{role}*\n\n"
        f"💬 İndi mənə mətn yazın, səsli mesaj göndərin və ya əmrlərdən istifadə edin.\n\n"
        f"📋 *Əmrlər:*\n"
        f"/find — Müştəri axtar\n"
        f"/task — Tapşırıq yarat\n"
        f"/note — Qeyd əlavə et\n"
        f"/tasks — Bugünkü tapşırıqlar\n"
        f"/tomorrow — Sabahkı tapşırıqlar\n"
        f"/lead — Sövdələşməyə link",
        parse_mode="Markdown"
    )

async def send_admin_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, phone: str, trigger: str, sender_chat_id: int, sender_kommo_id: int):
    """Send automation transition to Admin for confirmation."""
    conf_key = str(uuid.uuid4())[:8]
    context.bot_data[f"confirm_{conf_key}"] = {
        "phone": phone, "trigger": trigger,
        "sender_chat_id": sender_chat_id, "sender_kommo_id": sender_kommo_id
    }
    sender_name = KOMMO_USERS.get(sender_kommo_id, "Əəməkdaş")
    
    TRIGGER_NAMES = {
        "new_order": "Yeni sifariş (müştəri almaq istəyir)",
        "meeting_set": "Görüş təyin olundu",
        "sold": "Satıldı → Quraşdirma",
        "thinking": "Düşünür (follow-up 3 gün)",
        "no_answer": "Cavab vermir",
        "refused": "İmtina etdi",
        "need_presentation": "Təqdimat lazımdır",
        "presentation_done": "Təqdimat olundu",
        "internal_discussion": "Daxili müzakirə",
        "discussion_done": "Müzakirə bitdi",
        "installation_done": "Quraşdirma bitdi",
        "send_price": "Qiymət təklifi göndər",
    }
    trigger_desc = TRIGGER_NAMES.get(trigger, trigger)
    
    # Find contact name and lead link
    contacts = search_contact_by_phone(phone)
    contact_name = contacts[0].get("name", "Adsız") if contacts else phone
    conf_link = ""
    if contacts:
        full_c = get_contact_details(contacts[0]["id"])
        c_leads = (full_c or {}).get("_embedded", {}).get("leads", [])
        c_lead_id = c_leads[0]["id"] if c_leads else None
        if c_lead_id:
            conf_link = f"\n\n🔗 {KOMMO_BASE_URL}/leads/detail/{c_lead_id}"
        else:
            conf_link = f"\n\n🔗 {KOMMO_BASE_URL}/contacts/detail/{contacts[0]['id']}"
        # Store link in pending data for use after confirmation
        context.bot_data[f"confirm_{conf_key}"]["lead_link"] = conf_link
    
    # Notify sender that it's sent for approval
    await update.message.reply_text(
        f"📤 Sorğunuz Admin-ə təsdiq üçün göndərildi:\n"
        f"👤 Müştəri: {contact_name}\n"
        f"🔄 Əməliyyat: {trigger_desc}{conf_link}",
        disable_web_page_preview=True
    )
    
    # Send to Admin with buttons
    admin_chat = get_chat_id_for_kommo_user(10932455)
    if admin_chat:
        keyboard = [
            [InlineKeyboardButton("✅ Təsdiq et", callback_data=f"conftr_{conf_key}_yes")],
            [InlineKeyboardButton("❌ Rədd et", callback_data=f"conftr_{conf_key}_no")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await context.bot.send_message(
                admin_chat,
                f"🔔 *{sender_name}* keçid etmək istəyir:\n\n"
                f"👤 Müştəri: {contact_name}\n"
                f"🔄 Əməliyyat: *{trigger_desc}*\n\n"
                f"Təsdiq edirsiniz?{conf_link}",
                reply_markup=reply_markup,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except Exception:
            try:
                await context.bot.send_message(
                    admin_chat,
                    f"🔔 {sender_name} keçid etmək istəyir:\n\n"
                    f"👤 Müştəri: {contact_name}\n"
                    f"🔄 Əməliyyat: {trigger_desc}\n\n"
                    f"Təsdiq edirsiniz?{conf_link}",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
            except:
                pass

async def confirm_transition_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Admin confirmation/rejection of automation transition."""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # conftr_{conf_key}_yes/no
    parts = data.split("_")
    conf_key = parts[1]
    decision = parts[2]  # yes or no
    
    pending = context.bot_data.get(f"confirm_{conf_key}")
    if not pending:
        try:
            await query.edit_message_text("⚠️ Məlumat tapılmadı (vaxt keçib).")
        except:
            pass
        return
    
    phone = pending["phone"]
    trigger = pending["trigger"]
    sender_chat_id = pending["sender_chat_id"]
    sender_kommo_id = pending["sender_kommo_id"]
    sender_name = KOMMO_USERS.get(sender_kommo_id, "Əməkdaş")
    
    del context.bot_data[f"confirm_{conf_key}"]
    
    if decision == "yes":
        # Execute the transition
        contacts = search_contact_by_phone(phone)
        contact_name = contacts[0].get("name", "Adsız") if contacts else phone
        
        full_contact = get_contact_details(contacts[0]["id"]) if contacts else None
        leads = (full_contact or {}).get("_embedded", {}).get("leads", []) if full_contact else []
        
        if leads:
            lead_id = leads[0]["id"]
            now = datetime.now(tz=BAKU_TZ)
            result_msg = f"✅ Təsdiqləndi: {trigger}\n👤 Müştəri: {contact_name}"
            
            # Execute the trigger
            if trigger == "new_order":
                update_lead_kommo(lead_id, {"status_id": STAGES["yeni_sifaris"], "pipeline_id": PIPELINE_ID, "responsible_user_id": 15532668})
                task_time = (now + timedelta(hours=1)).replace(minute=0, second=0)
                create_task(lead_id, "Müştəri ilə əlaqə saxla", int(task_time.timestamp()), responsible_user_id=15532668, entity_type="leads")
                result_msg += "\n🔄 Mərhələ: yeni sifariş\n👤 Məsul: Şamil Əliyev"
            elif trigger == "sold":
                update_lead_kommo(lead_id, {"status_id": STAGES["qurashdirma"], "pipeline_id": PIPELINE_ID, "responsible_user_id": 15531960})
                task_time = (now + timedelta(hours=2)).replace(minute=0, second=0)
                create_task(lead_id, "Quraşdırma vaxtını təyin et", int(task_time.timestamp()), responsible_user_id=15531960, entity_type="leads")
                result_msg += "\n🔄 Mərhələ: quraşdırma\n👤 Məsul: Soltan Abbasov"
            elif trigger == "need_presentation":
                update_lead_kommo(lead_id, {"status_id": STAGES["teqdimat"], "pipeline_id": PIPELINE_ID})
                result_msg += "\n🔄 Mərhələ: təqdimat"
            elif trigger == "thinking":
                task_time = (now + timedelta(days=3)).replace(hour=10, minute=0, second=0)
                create_task(lead_id, "Follow-up: müştəri ilə əlaqə saxla", int(task_time.timestamp()), entity_type="leads")
                result_msg += "\n📋 3 gün sonra follow-up"
            elif trigger == "refused":
                update_lead_kommo(lead_id, {"status_id": STAGES["imtina"], "pipeline_id": PIPELINE_ID})
                result_msg += "\n🔄 Mərhələ: imtina"
            elif trigger == "send_price":
                update_lead_kommo(lead_id, {"status_id": STAGES["qiymet_teklifi"], "pipeline_id": PIPELINE_ID})
                result_msg += "\n🔄 Mərhələ: qiymət təklifi"
            elif trigger == "presentation_done":
                update_lead_kommo(lead_id, {"status_id": STAGES["teqdimat_olundu"], "pipeline_id": PIPELINE_ID})
                result_msg += "\n🔄 Mərhələ: təqdimat olundu"
            elif trigger == "installation_done":
                update_lead_kommo(lead_id, {"status_id": STAGES["ugurlu"], "pipeline_id": PIPELINE_ID})
                result_msg += "\n🔄 Mərhələ: uğurlu sifariş 🎉"
            elif trigger == "meeting_set":
                update_lead_kommo(lead_id, {"status_id": STAGES["gorus"], "pipeline_id": PIPELINE_ID})
                result_msg += "\n🔄 Mərhələ: görüş"
            elif trigger == "no_answer":
                update_lead_kommo(lead_id, {"status_id": STAGES["cavab_gozlenilir"], "pipeline_id": PIPELINE_ID})
                result_msg += "\n🔄 Mərhələ: cavab gözlənilir"
            else:
                result_msg += f"\n🔄 Trigger: {trigger}"
            
            link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
            result_msg += f"\n\n🔗 {link}"
            
            try:
                await query.edit_message_text(result_msg, disable_web_page_preview=True)
            except:
                pass
            
            # Notify sender
            try:
                await context.bot.send_message(
                    sender_chat_id,
                    f"✅ Admin sorğunuzu təsdiqlədi!\n{result_msg}",
                    disable_web_page_preview=True
                )
            except:
                pass
        else:
            try:
                await query.edit_message_text("❌ Müştərinin sövdələşməsi tapılmadı.")
            except:
                pass
    else:
        # Rejected
        # Retrieve stored link if available
        rej_link = pending.get("lead_link", "")
        try:
            await query.edit_message_text(f"❌ Rədd edildi: {trigger}{rej_link}", disable_web_page_preview=True)
        except:
            pass
        # Notify sender
        try:
            await context.bot.send_message(
                sender_chat_id,
                f"❌ Admin sorğunuzu rədd etdi.\n🔄 Əməliyyat: {trigger}{rej_link}",
                disable_web_page_preview=True
            )
        except:
            pass

async def ask_task_assignee(update: Update, context: ContextTypes.DEFAULT_TYPE, phone: str, date_str: str, time_str: str, task_text: str, urgency: str = "normal"):
    """Ask Admin who to assign the task to."""
    urgency_mark = "🔴 TƏCİLİ! " if urgency == "high" else ""
    task_key = str(uuid.uuid4())[:8]
    context.bot_data[f"pending_task_{task_key}"] = {
        "phone": phone, "date": date_str, "time": time_str,
        "text": task_text, "urgency": urgency, "chat_id": update.message.chat_id
    }
    keyboard = [
        [InlineKeyboardButton("Şamil Əliyev", callback_data=f"taskasgn_{task_key}_15532668")],
        [InlineKeyboardButton("Soltan Abbasov", callback_data=f"taskasgn_{task_key}_15531960")],
        [InlineKeyboardButton("Özüm (Admin)", callback_data=f"taskasgn_{task_key}_10932455")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"{urgency_mark}📋 *Tapşırıq:* {task_text}\n📅 {date_str} {time_str}\n\nKimə təyin edim?",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def task_assign_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle task assignment button press."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data  # taskasgn_{task_key}_{user_id}
    parts = data.split("_")
    task_key = parts[1]
    user_id = int(parts[2])
    
    pending = context.bot_data.get(f"pending_task_{task_key}")
    if not pending:
        try:
            await query.edit_message_text("⚠️ Tapşırıq məlumatı tapılmadı (vaxt keçib).")
        except:
            pass
        return
    
    phone = pending["phone"]
    date_str = pending["date"]
    time_str = pending["time"]
    task_text = pending["text"]
    urgency = pending["urgency"]
    chat_id = pending["chat_id"]
    
    # Remove pending data
    del context.bot_data[f"pending_task_{task_key}"]
    
    # Create the task
    urgency_mark = "🔴 TƏCİLİ! " if urgency == "high" else ""
    contacts = search_contact_by_phone(phone)
    if not contacts:
        try:
            await query.edit_message_text(f"❌ Müştəri tapılmadı: {phone}")
        except:
            pass
        return
    contact = contacts[0]
    contact_name = contact.get("name", "Adsız")
    full_c = get_contact_details(contact["id"])
    c_leads = (full_c or {}).get("_embedded", {}).get("leads", [])
    c_lead_id = c_leads[0]["id"] if c_leads else None
    c_link = f"{KOMMO_BASE_URL}/leads/detail/{c_lead_id}" if c_lead_id else f"{KOMMO_BASE_URL}/contacts/detail/{contact['id']}"
    
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M").replace(tzinfo=BAKU_TZ)
        complete_till = int(dt.timestamp())
    except ValueError:
        try:
            await query.edit_message_text("⚠️ Tarix formatı xətası.")
        except:
            pass
        return
    
    responsible_name = KOMMO_USERS.get(user_id, "")
    result = create_task(contact["id"], task_text, complete_till, responsible_user_id=user_id)
    if result:
        msg = (
            f"✅ {urgency_mark}Tapşırıq yaradıldı!\n\n"
            f"👤 Müştəri: {contact_name}\n"
            f"📅 Tarix: {date_str} {time_str}\n"
            f"📝 Mətn: {task_text}\n"
            f"👤 Məsul: {responsible_name}\n\n🔗 {c_link}"
        )
        try:
            await query.edit_message_text(msg, disable_web_page_preview=True)
        except:
            try:
                await context.bot.send_message(chat_id, msg, disable_web_page_preview=True)
            except:
                pass
        # Notify assigned user if not Admin
        if user_id != 10932455:
            assigned_chat = get_chat_id_for_kommo_user(user_id)
            if assigned_chat:
                try:
                    await context.bot.send_message(
                        assigned_chat,
                        f"📢 {urgency_mark}Admin sizin üçün tapşırıq yaratdı:\n\n"
                        f"👤 Müştəri: {contact_name}\n"
                        f"📅 Tarix: {date_str} {time_str}\n"
                        f"📝 {task_text}\n\n🔗 {c_link}",
                        disable_web_page_preview=True
                    )
                    # Set context for assigned user so they can reply about this client
                    set_last_contact(assigned_chat, phone, contact["id"], contact_name, lead_id=c_lead_id)
                except:
                    pass
        # Store context
        task_id = result.get("_embedded", {}).get("tasks", [{}])[0].get("id")
        if task_id:
            set_last_task(chat_id, task_id, date_str, time_str)
        set_last_contact(chat_id, phone, contact["id"], contact_name, lead_id=c_lead_id)
    else:
        try:
            await query.edit_message_text("❌ Tapşırıq yaradılarkən xəta baş verdi.")
        except:
            pass

async def ask_presentation_assignee(update: Update, lead_id: int):
    keyboard = [
        [InlineKeyboardButton("Şamil Əliyev", callback_data=f"pres_{lead_id}_15532668")],
        [InlineKeyboardButton("Soltan Abbasov", callback_data=f"pres_{lead_id}_15531960")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Kim təqdimat edəcək — Şamil yoxsa Soltan?", reply_markup=reply_markup)

async def presentation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass  # Timeout on answer is not critical
    data = query.data  # pres_{lead_id}_{user_id}
    _, lead_id, user_id = data.split("_")
    lead_id = int(lead_id)
    user_id = int(user_id)
    user_name = KOMMO_USERS.get(user_id, "Məsul şəxs")

    # Update lead
    update_lead_kommo(lead_id, {"status_id": STAGES["teqdimat"], "pipeline_id": PIPELINE_ID, "responsible_user_id": user_id})
    
    # Create task
    now = datetime.now(tz=BAKU_TZ)
    task_time = (now + timedelta(hours=1)).replace(minute=0, second=0)
    create_task(lead_id, "Müştəri ilə əlaqə saxla, təqdimat vaxtını təyin et", int(task_time.timestamp()), responsible_user_id=user_id, entity_type="leads")
    
    # Add note
    add_note(lead_id, f"Sövdələşmə 'Təqdimat' mərhələsinə keçirildi. Məsul: {user_name}. Tapşırıq yaradıldı.", "leads")

    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
    try:
        await query.edit_message_text(
            f"✅ Sövdələşmə *'Təqdimat'* mərhələsinə keçirildi.\n"
            f"👤 *{user_name}* üçün tapşırıq yaradıldı: _Müştəri ilə əlaqə saxla, təqdimat vaxtını təyin et_\n\n🔗 {link}",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    except Exception:
        # Fallback: send new message if edit fails
        try:
            await context.bot.send_message(
                query.message.chat_id,
                f"✅ Sövdələşmə 'Təqdimat' mərhələsinə keçirildi.\n"
                f"{user_name} üçün tapşırıq yaradıldı: Müştəri ilə əlaqə saxla, təqdimat vaxtını təyin et\n\n{link}",
                disable_web_page_preview=True
            )
        except:
            pass
    # Notify Admin
    admin_chat = get_chat_id_for_kommo_user(10932455)
    sender_chat = query.message.chat_id
    if admin_chat and admin_chat != sender_chat:
        try:
            await context.bot.send_message(
                admin_chat,
                f"📢 Təqdimat təyin edildi:\n"
                f"👤 Məsul: {user_name}\n"
                f"🔗 {link}",
                disable_web_page_preview=True
            )
        except:
            pass
    # Notify the assigned user and set their context
    assigned_chat = get_chat_id_for_kommo_user(user_id)
    if assigned_chat and assigned_chat != sender_chat:
        # Get contact info for context
        lead_details = get_lead_details(lead_id)
        contact_id = None
        contact_name = "Müştəri"
        contact_phone = None
        if lead_details:
            contacts_embedded = lead_details.get("_embedded", {}).get("contacts", [])
            if contacts_embedded:
                contact_id = contacts_embedded[0]["id"]
                full_c = get_contact_details(contact_id)
                if full_c:
                    contact_name = full_c.get("name", "Müştəri")
                    # Extract phone
                    for cf in full_c.get("custom_fields_values", []):
                        if cf.get("field_code") == "PHONE":
                            contact_phone = cf["values"][0]["value"]
                            break
        # Set context for assigned user BEFORE sending message (so it's always set)
        if contact_id and contact_phone:
            set_last_contact(assigned_chat, contact_phone, contact_id, contact_name, lead_id=lead_id)
            logger.info(f"Context set for assigned user chat={assigned_chat}: phone={contact_phone}, contact={contact_name}, lead={lead_id}")
        else:
            logger.warning(f"Could not set context for assigned user chat={assigned_chat}: contact_id={contact_id}, phone={contact_phone}, lead_details={lead_details is not None}")
        try:
            await context.bot.send_message(
                assigned_chat,
                f"📊 *Yeni təqdimat tapşırığı!*\n\n"
                f"👤 Müştəri: {contact_name}\n"
                f"Müştəri ilə əlaqə saxla, təqdimat vaxtını təyin et.\n\n🔗 {link}",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except:
            pass

# ─── Overdue Task Callback ───────────────────────────────────────────────────

async def overdue_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle overdue task buttons: İcra olundu / İmtina."""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # overdue_{task_id}_{action}
    parts = data.split("_", 2)
    task_id = int(parts[1])
    # action is "done" or "reject"
    
    chat_id = query.message.chat_id
    
    # Get task info from bot_data
    task_info = context.bot_data.get(f"overdue_task_{task_id}", {})
    task_text = task_info.get("text", "Tapşırıq")
    entity_id = task_info.get("entity_id")
    entity_type = task_info.get("entity_type", "leads")
    
    # Ask user for result text
    _pending_task_result[chat_id] = {
        "task_id": task_id,
        "task_text": task_text,
        "entity_id": entity_id,
        "entity_type": entity_type,
    }
    
    # Edit message to show that user needs to provide result
    try:
        await query.edit_message_text(
            f"{query.message.text}\n\n✏️ Tapşırığın nəticəsini yazın:",
            disable_web_page_preview=True
        )
    except:
        try:
            await context.bot.send_message(
                chat_id,
                "✏️ Tapşırığın nəticəsini yazın:"
            )
        except:
            pass

# ─── Kommo Webhook Callback for Stage Changes ───────────────────────────────

async def webhook_stage_notification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle webhook notification buttons: Göndərildi / Təqdimat olundu / Əlaqə saxlanıldı."""
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    data = query.data  # whstage_{lead_id}_{action}
    parts = data.split("_", 2)
    lead_id = int(parts[1])
    action_type = parts[2]  # "sent", "presented", "contacted"
    
    now = datetime.now(tz=BAKU_TZ)
    link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
    
    if action_type == "sent":
        # "Göndərildi" — move to "Cavab gözlənilir" + reminder 2 days
        update_lead_kommo(lead_id, {"status_id": STAGES["cavab_gozlenilir"], "pipeline_id": PIPELINE_ID})
        reminder_time = (now + timedelta(days=2)).replace(hour=10, minute=0, second=0)
        create_task(lead_id, "Müştəri ilə yenidən əlaqə saxla", int(reminder_time.timestamp()), responsible_user_id=10932455, entity_type="leads")
        add_note(lead_id, "Qiymət təklifi göndərildi. Admin tərəfindən 'Cavab gözlənilir' mərhələsinə keçirildi.", "leads")
        result_text = "✅ Qiymət göndərildi! Sövdələşmə 'Cavab gözlənilir' mərhələsinə keçirildi.\n2 gün sonra xatırlatma yaradıldı."
    elif action_type == "presented":
        # "Təqdimat olundu" — move to "Təqdimat olundu" + reminder 1 day
        update_lead_kommo(lead_id, {"status_id": STAGES["teqdimat_olundu"], "pipeline_id": PIPELINE_ID})
        reminder_time = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0)
        create_task(lead_id, "Müştəri ilə yenidən əlaqə saxla", int(reminder_time.timestamp()), responsible_user_id=10932455, entity_type="leads")
        add_note(lead_id, "Təqdimat olundu. Admin tərəfindən 'Təqdimat olundu' mərhələsinə keçirildi.", "leads")
        result_text = "✅ Təqdimat qeydə alındı! Sövdələşmə 'Təqdimat olundu' mərhələsinə keçirildi.\n1 gün sonra xatırlatma yaradıldı."
    elif action_type == "contacted":
        # "Əlaqə saxlanıldı" — move to "Cavab gözlənilir" + reminder 2 days
        update_lead_kommo(lead_id, {"status_id": STAGES["cavab_gozlenilir"], "pipeline_id": PIPELINE_ID})
        reminder_time = (now + timedelta(days=2)).replace(hour=10, minute=0, second=0)
        create_task(lead_id, "Müştəri ilə yenidən əlaqə saxla", int(reminder_time.timestamp()), responsible_user_id=10932455, entity_type="leads")
        add_note(lead_id, "Əlaqə saxlanıldı. Admin tərəfindən 'Cavab gözlənilir' mərhələsinə keçirildi.", "leads")
        result_text = "✅ Əlaqə qeydə alındı! Sövdələşmə 'Cavab gözlənilir' mərhələsinə keçirildi.\n2 gün sonra xatırlatma yaradıldı."
    else:
        result_text = "⚠️ Naməlum əməliyyat."
    
    try:
        await query.edit_message_text(
            f"{result_text}\n\n🔗 {link}",
            disable_web_page_preview=True
        )
    except:
        try:
            await context.bot.send_message(
                query.message.chat_id,
                f"{result_text}\n\n🔗 {link}",
                disable_web_page_preview=True
            )
        except:
            pass

# ─── Kommo Webhook Handler ───────────────────────────────────────────────────

# Global reference to the Telegram bot application (set in main)
_bot_app: Application = None

async def handle_kommo_webhook(request: web.Request) -> web.Response:
    """Handle incoming Kommo CRM webhook for lead status changes."""
    try:
        # Kommo sends form-encoded data
        data = await request.post()
        logger.info(f"Kommo webhook received: {dict(data)}")
        
        # Extract lead status change fields
        # Kommo sends: leads[status][0][id], leads[status][0][status_id], etc.
        lead_id_raw = data.get("leads[status][0][id]")
        new_status_id_raw = data.get("leads[status][0][status_id]")
        old_status_id_raw = data.get("leads[status][0][old_status_id]")
        pipeline_id_raw = data.get("leads[status][0][pipeline_id]")
        
        if not lead_id_raw or not new_status_id_raw:
            return web.Response(status=200, text="OK")
        
        lead_id = int(lead_id_raw)
        new_status_id = int(new_status_id_raw)
        old_status_id = int(old_status_id_raw) if old_status_id_raw else None
        pipeline_id = int(pipeline_id_raw) if pipeline_id_raw else None
        
        logger.info(f"Lead {lead_id}: {old_status_id} -> {new_status_id} (pipeline {pipeline_id})")
        
        # Only process our pipeline
        if pipeline_id and pipeline_id != PIPELINE_ID:
            return web.Response(status=200, text="OK")
        
        # Only process transitions FROM Nerazobrannoye
        if old_status_id != STAGES["nerazobrannoye"]:
            return web.Response(status=200, text="OK")
        
        # Check if it's one of the target stages
        if new_status_id not in (STAGES["qiymet_teklifi"], STAGES["teqdimat"], STAGES["yeni_sifaris"]):
            return web.Response(status=200, text="OK")
        
        # Get lead details
        lead_details = get_lead_details(lead_id)
        if not lead_details:
            logger.warning(f"Could not get lead details for {lead_id}")
            return web.Response(status=200, text="OK")
        
        lead_name = lead_details.get("name", "Adsız sövdələşmə")
        link = f"{KOMMO_BASE_URL}/leads/detail/{lead_id}"
        
        # Get contact info
        contact_name = "Adsız müştəri"
        contact_phone = ""
        contacts_embedded = lead_details.get("_embedded", {}).get("contacts", [])
        if contacts_embedded:
            contact_id = contacts_embedded[0]["id"]
            full_contact = get_contact_details(contact_id)
            if full_contact:
                contact_name = full_contact.get("name", "Adsız müştəri")
                for cf in (full_contact.get("custom_fields_values") or []):
                    if cf.get("field_code") == "PHONE":
                        vals = cf.get("values", [])
                        if vals:
                            contact_phone = vals[0].get("value", "")
                        break
        
        stage_name = STAGE_NAMES.get(new_status_id, f"ID:{new_status_id}")
        
        # Find Admin chat_id
        admin_chat = get_chat_id_for_kommo_user(10932455)
        if not admin_chat or not _bot_app:
            logger.warning("Admin chat not found or bot app not initialized")
            return web.Response(status=200, text="OK")
        
        # Build notification message
        msg = (
            f"🔔 *Nerazobrannoye-dan yeni keçid!*\n\n"
            f"👤 Müştəri: {contact_name}\n"
            f"📞 Telefon: {contact_phone}\n"
            f"📋 Sövdələşmə: {lead_name}\n"
            f"➡️ Mərhələ: *{stage_name}*\n\n"
            f"🔗 {link}"
        )
        
        # Determine button based on target stage
        if new_status_id == STAGES["qiymet_teklifi"]:
            # A) Qiymət təklifi → button "Göndərildi"
            keyboard = [[InlineKeyboardButton("✅ Göndərildi", callback_data=f"whstage_{lead_id}_sent")]]
        elif new_status_id == STAGES["teqdimat"]:
            # B) Təqdimat → button "Təqdimat olundu"
            keyboard = [[InlineKeyboardButton("✅ Təqdimat olundu", callback_data=f"whstage_{lead_id}_presented")]]
        elif new_status_id == STAGES["yeni_sifaris"]:
            # C) Yeni sifariş → button "Əlaqə saxlanıldı"
            keyboard = [[InlineKeyboardButton("✅ Əlaqə saxlanıldı", callback_data=f"whstage_{lead_id}_contacted")]]
        else:
            keyboard = []
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        # Store task info for potential use in callback
        _bot_app.bot_data[f"overdue_task_{lead_id}"] = {
            "text": f"Sövdələşmə: {lead_name}",
            "entity_id": lead_id,
            "entity_type": "leads",
        }
        
        # Send notification to Admin
        try:
            await _bot_app.bot.send_message(
                admin_chat,
                msg,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            logger.info(f"Webhook notification sent to admin for lead {lead_id} -> stage {new_status_id}")
        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")
        
        return web.Response(status=200, text="OK")
    
    except Exception as e:
        logger.error(f"Webhook handler error: {e}\n{traceback.format_exc()}")
        return web.Response(status=200, text="OK")

async def health_check(request: web.Request) -> web.Response:
    """Simple health check endpoint."""
    return web.Response(status=200, text="Bot is running")

async def start_webhook_server():
    """Start the aiohttp webhook server."""
    app_web = web.Application()
    app_web.router.add_post("/webhook/kommo", handle_kommo_webhook)
    app_web.router.add_get("/", health_check)
    app_web.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT)
    await site.start()
    logger.info(f"Webhook server started on port {WEBHOOK_PORT}")
    logger.info(f"Kommo webhook endpoint: POST /webhook/kommo")

# ─── Free Text and Voice Handlers ────────────────────────────────────────────

async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    if update.message.chat.type in ("group", "supergroup"):
        bot_username = context.bot.username
        if not bot_username or f"@{bot_username}" not in user_text:
            return
        user_text = user_text.replace(f"@{bot_username}", "").strip()
    if not user_text:
        return
    await process_text_intent(update, context, user_text)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return
    voice = update.message.voice
    file_id = voice.file_id
    # Check file size - Telegram bot API limit is 20MB
    file_size = voice.file_size or 0
    if file_size > 20 * 1024 * 1024:
        await update.message.reply_text("❌ Səsli mesaj çox böyükdür (20MB-dan çox). Daha qısa mesaj göndərin.")
        return
    status_msg = await update.message.reply_text("🎙 Səsli mesaj emal olunur...")
    try:
        new_file = await context.bot.get_file(file_id)
        ogg_path = f"/tmp/{file_id}.ogg"
        mp3_path = f"/tmp/{file_id}.mp3"
        # Download with retry (up to 3 attempts)
        downloaded = False
        for attempt in range(3):
            try:
                await new_file.download_to_drive(ogg_path)
                # Verify file was downloaded
                if os.path.exists(ogg_path) and os.path.getsize(ogg_path) > 0:
                    downloaded = True
                    break
            except Exception as dl_err:
                logger.warning(f"Voice download attempt {attempt+1} failed: {dl_err}")
                await asyncio.sleep(2)
        if not downloaded:
            raise Exception("Faylı yükləmək mümkün olmadı (3 cəhddən sonra)")
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
        # Cleanup
        for fp in [ogg_path, mp3_path, latest_file]:
            try:
                os.remove(fp)
            except:
                pass
        if not transcribed_text:
            await status_msg.edit_text("❌ Səs tanınmadı.")
            return
        logger.info(f"Voice transcription ({len(transcribed_text)} chars): {transcribed_text[:300]}")
        try:
            await status_msg.edit_text(f"📝 Tanınan mətn: _{transcribed_text[:500]}_", parse_mode="Markdown")
        except Exception as edit_err:
            logger.warning(f"edit_text failed: {edit_err}")
        await process_text_intent(update, context, transcribed_text)
    except Exception as e:
        logger.error(f"Voice error: {e}\n{traceback.format_exc()}")
        try:
            await status_msg.edit_text(f"❌ Audio emalında xəta.\nXəta: {str(e)[:200]}\n\nZəhmət olmasa mətn olaraq yazın.")
        except:
            pass

# ─── Background Jobs ─────────────────────────────────────────────────────────

async def check_task_deadlines(context: ContextTypes.DEFAULT_TYPE):
    """Check tasks due in 15 minutes and notify responsible users.
    Also check overdue tasks and send notifications with action buttons.
    
    Flood-control measures:
    - Skip first 2 minutes after bot startup to avoid mass-sending on restart
    - Deduplicate: each task_id is notified only once per process lifetime
    - Max 5 notifications per cycle (rest deferred to next cycle)
    - 1-second sleep between messages to respect Telegram rate limits
    """
    # ── Flood guard: skip first 2 minutes after startup ──
    if _time_module.time() - _BOT_START_TIME < 120:
        logger.info("check_task_deadlines: skipping (startup grace period)")
        return

    now = datetime.now(tz=BAKU_TZ)
    window_start = now
    window_end = now + timedelta(minutes=15)
    tasks = get_all_incomplete_tasks()
    
    notifications_sent = 0
    MAX_NOTIFICATIONS_PER_CYCLE = 5

    for task in tasks:
        if notifications_sent >= MAX_NOTIFICATIONS_PER_CYCLE:
            logger.info("check_task_deadlines: hit max notifications per cycle (5), deferring rest")
            break

        till = task.get("complete_till", 0)
        task_dt = datetime.fromtimestamp(till, tz=BAKU_TZ)
        responsible_id = task.get("responsible_user_id")
        chat_id = get_chat_id_for_kommo_user(responsible_id)
        text = task.get("text", "Təsvirsiz")
        time_str = task_dt.strftime("%H:%M %d.%m.%Y")
        entity_id = task.get("entity_id")
        entity_type = task.get("entity_type", "")
        task_id = task.get("id")
        
        # ── Deduplication: skip if already notified ──
        if task_id and task_id in _sent_deadline_notifications:
            continue

        if entity_type == "leads" and entity_id:
            task_link = f"{KOMMO_BASE_URL}/leads/detail/{entity_id}"
        elif entity_type == "contacts" and entity_id:
            task_link = f"{KOMMO_BASE_URL}/contacts/detail/{entity_id}"
        else:
            task_link = ""
        link_line = f"\n\n🔗 {task_link}" if task_link else ""
        
        # ── Upcoming (within 15 min): simple reminder ──
        if window_start <= task_dt <= window_end:
            if chat_id:
                try:
                    await context.bot.send_message(
                        chat_id,
                        f"⏰ *Xatırlatma!* Tapşırığın vaxtı yaxınlaşır:\n\n"
                        f"📝 {text}\n⏰ {time_str}{link_line}",
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                    notifications_sent += 1
                    if task_id:
                        _sent_deadline_notifications.add(task_id)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Notification error: {e}")
                # Notify Admin about reminders sent to other users
                if responsible_id != 10932455:
                    admin_chat = get_chat_id_for_kommo_user(10932455)
                    responsible_name = KOMMO_USERS.get(responsible_id, "")
                    if admin_chat:
                        try:
                            await context.bot.send_message(
                                admin_chat,
                                f"🔔 *{responsible_name}* üçün xatırlatma göndərildi:\n\n"
                                f"📝 {text}\n⏰ {time_str}{link_line}",
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
                            await asyncio.sleep(1)
                        except:
                            pass
        
        # ── Overdue: send to responsible with İcra olundu / İmtina buttons ──
        elif task_dt < window_start and task_id:
            if chat_id:
                # Store task info for callback
                context.bot_data[f"overdue_task_{task_id}"] = {
                    "text": text,
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                }
                keyboard = [
                    [
                        InlineKeyboardButton("✅ İcra olundu", callback_data=f"overdue_{task_id}_done"),
                        InlineKeyboardButton("❌ İmtina", callback_data=f"overdue_{task_id}_reject"),
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                try:
                    await context.bot.send_message(
                        chat_id,
                        f"⚠️ *Gecikmiş tapşırıq!*\n\n"
                        f"📝 {text}\n⏰ Son tarix: {time_str}{link_line}",
                        parse_mode="Markdown",
                        reply_markup=reply_markup,
                        disable_web_page_preview=True
                    )
                    notifications_sent += 1
                    _sent_deadline_notifications.add(task_id)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Overdue notification error: {e}")
                # Notify Admin if responsible is not Admin
                if responsible_id != 10932455:
                    admin_chat = get_chat_id_for_kommo_user(10932455)
                    responsible_name = KOMMO_USERS.get(responsible_id, "")
                    if admin_chat:
                        try:
                            await context.bot.send_message(
                                admin_chat,
                                f"⚠️ *{responsible_name}* üçün gecikmiş tapşırıq bildirişi göndərildi:\n\n"
                                f"📝 {text}\n⏰ Son tarix: {time_str}{link_line}",
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
                            await asyncio.sleep(1)
                        except:
                            pass

async def morning_digest(context: ContextTypes.DEFAULT_TYPE):
    """Send morning digest at 9:00 Baku time."""
    now = datetime.now(tz=BAKU_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    users = load_users()

    for chat_id_str, info in users.items():
        chat_id = int(chat_id_str)
        kommo_uid = info.get("kommo_user_id")

        if kommo_uid == 10932455:
            # Admin digest: full overview
            msg = "☀️ *Səhər hesabatı (Admin):*\n\n"
            # Stage counts
            msg += "📊 *Huni vəziyyəti:*\n"
            for stage_id, stage_name in STAGE_NAMES.items():
                if stage_id in (142, 143):
                    continue
                leads = get_leads_by_status(stage_id)
                if leads:
                    msg += f"  \u2022 {stage_name}: {len(leads)} sövdələşmə\n"
            # Overdue tasks with links
            all_tasks = get_all_incomplete_tasks()
            overdue = [t for t in all_tasks if t.get("complete_till", 0) < int(now.timestamp())]
            if overdue:
                msg += f"\n⚠️ *Gecikmiş tapşırıqlar:* {len(overdue)}\n"
                for t in overdue[:5]:
                    t_entity_id = t.get("entity_id")
                    t_entity_type = t.get("entity_type", "leads")
                    if t_entity_id and t_entity_type == "leads":
                        t_link = f" 🔗 {KOMMO_BASE_URL}/leads/detail/{t_entity_id}"
                    elif t_entity_id and t_entity_type == "contacts":
                        t_link = f" 🔗 {KOMMO_BASE_URL}/contacts/detail/{t_entity_id}"
                    else:
                        t_link = ""
                    msg += f"  \u2022 {t.get('text', 'Təsvirsiz')}{t_link}\n"
            # Today tasks with links
            today_tasks = get_tasks(today_start, today_end)
            msg += f"\n📅 *Bugünkü tapşırıqlar:* {len(today_tasks)}\n"
            for t in today_tasks[:10]:
                dt = datetime.fromtimestamp(t.get("complete_till", 0), tz=BAKU_TZ)
                t_entity_id = t.get("entity_id")
                t_entity_type = t.get("entity_type", "leads")
                if t_entity_id and t_entity_type == "leads":
                    t_link = f" 🔗 {KOMMO_BASE_URL}/leads/detail/{t_entity_id}"
                elif t_entity_id and t_entity_type == "contacts":
                    t_link = f" 🔗 {KOMMO_BASE_URL}/contacts/detail/{t_entity_id}"
                else:
                    t_link = ""
                msg += f"  \u2022 ⏰ {dt.strftime('%H:%M')} \u2014 {t.get('text', 'Təsvirsiz')}{t_link}\n"
            try:
                await context.bot.send_message(chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
            except:
                pass
        else:
            # Regular user: their tasks today with links
            tasks = get_tasks(today_start, today_end, responsible_id=kommo_uid)
            if tasks:
                msg = f"☀️ *Səhər hesabatı ({info.get('name', '')})* \u2014 bugünkü tapşırıqlar:\n\n"
                for i, t in enumerate(tasks, 1):
                    dt = datetime.fromtimestamp(t.get("complete_till", 0), tz=BAKU_TZ)
                    t_entity_id = t.get("entity_id")
                    t_entity_type = t.get("entity_type", "leads")
                    if t_entity_id and t_entity_type == "leads":
                        t_link = f"\n   🔗 {KOMMO_BASE_URL}/leads/detail/{t_entity_id}"
                    elif t_entity_id and t_entity_type == "contacts":
                        t_link = f"\n   🔗 {KOMMO_BASE_URL}/contacts/detail/{t_entity_id}"
                    else:
                        t_link = ""
                    msg += f"{i}. ⏰ {dt.strftime('%H:%M')} \u2014 {t.get('text', 'Təsvirsiz')}{t_link}\n"
                msg += f"\n📊 Cəmi: {len(tasks)}"
            else:
                msg = f"☀️ *Səhər hesabatı ({info.get('name', '')})*\n\n✨ Bu gün üçün tapşırıq yoxdur!"
            try:
                await context.bot.send_message(chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
            except:
                pass

async def check_stuck_deals(context: ContextTypes.DEFAULT_TYPE):
    """Alert admin if a deal is stuck on 'Qiymət təklifi' for more than 1 hour."""
    now = datetime.now(tz=BAKU_TZ)
    leads = get_leads_by_status(STAGES["qiymet_teklifi"])
    admin_chat_id = get_chat_id_for_kommo_user(10932455)
    if not admin_chat_id:
        return
    for lead in leads:
        updated_at = lead.get("updated_at", 0)
        if updated_at:
            lead_dt = datetime.fromtimestamp(updated_at, tz=BAKU_TZ)
            if (now - lead_dt).total_seconds() > 3600:
                lead_name = lead.get("name", "Adsız")
                lead_id = lead.get("id")
                try:
                    await context.bot.send_message(
                        admin_chat_id,
                        f"⚠️ *Diqqət!* Sövdələşmə 1 saatdan çox 'Qiymət təklifi' mərhələsindədir:\n\n"
                        f"📋 {lead_name}\n🔗 {KOMMO_BASE_URL}/leads/detail/{lead_id}",
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                except:
                    pass

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    global _bot_app

    # Start webhook server inside PTB's event loop via post_init
    async def post_init(application: Application) -> None:
        await start_webhook_server()
        logger.info(f"Bot started. Webhook on port {WEBHOOK_PORT}, Telegram polling active.")

    app = Application.builder().token(TELEGRAM_TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).post_init(post_init).build()
    _bot_app = app

    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("role", role_command))
    app.add_handler(CommandHandler("find", lambda u, c: execute_find_contact(u, c.args[0], chat_id=u.message.chat_id) if c.args else u.message.reply_text("⚠️ Telefon nömrəsini göstərin.")))
    app.add_handler(CommandHandler("task", lambda u, c: execute_create_task(u, c.args[0], c.args[1], c.args[2], " ".join(c.args[3:]), chat_id=u.message.chat_id) if len(c.args) >= 4 else u.message.reply_text("⚠️ Format: /task <tel> <tarix> <vaxt> <mətn>")))
    app.add_handler(CommandHandler("note", lambda u, c: execute_add_note(u, c.args[0], " ".join(c.args[1:]), chat_id=u.message.chat_id) if len(c.args) >= 2 else u.message.reply_text("⚠️ Format: /note <tel> <mətn>")))
    app.add_handler(CommandHandler("tasks", lambda u, c: execute_show_tasks(u, "today")))
    app.add_handler(CommandHandler("tomorrow", lambda u, c: execute_show_tasks(u, "tomorrow")))
    app.add_handler(CommandHandler("lead", lambda u, c: execute_show_lead(u, c.args[0], chat_id=u.message.chat_id) if c.args else u.message.reply_text("⚠️ Telefon nömrəsini göstərin.")))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(role_callback, pattern="^role_"))
    app.add_handler(CallbackQueryHandler(presentation_callback, pattern="^pres_"))
    app.add_handler(CallbackQueryHandler(task_assign_callback, pattern="^taskasgn_"))
    app.add_handler(CallbackQueryHandler(confirm_transition_callback, pattern="^conftr_"))
    app.add_handler(CallbackQueryHandler(overdue_task_callback, pattern="^overdue_"))
    app.add_handler(CallbackQueryHandler(webhook_stage_notification_callback, pattern="^whstage_"))

    # Message handlers
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))

    # Background jobs
    job_queue = app.job_queue
    # Check task deadlines every 15 minutes
    job_queue.run_repeating(check_task_deadlines, interval=900, first=60)
    # Morning digest at 9:00 Baku time (5:00 UTC)
    job_queue.run_daily(morning_digest, time=datetime.strptime("05:00", "%H:%M").time())
    # Check stuck deals every 30 minutes
    job_queue.run_repeating(check_stuck_deals, interval=1800, first=120)

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
