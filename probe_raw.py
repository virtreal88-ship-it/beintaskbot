import json
import os
import sys

os.environ.setdefault("TELEGRAM_TOKEN", "0:dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
sys.path.insert(0, "beintaskbot-main")

import bot

LEAD = 78012075
contact_id = 123068097

for label, url, params in [
    ("notes-lead", f"{bot.KOMMO_BASE_URL}/api/v4/leads/{LEAD}/notes", {"limit": 5, "page": 1}),
    ("notes-contact", f"{bot.KOMMO_BASE_URL}/api/v4/contacts/{contact_id}/notes", {"limit": 5, "page": 1}),
    ("events-lead", f"{bot.KOMMO_BASE_URL}/api/v4/events", {
        "filter[entity]": "lead",
        "filter[entity_id]": LEAD,
        "filter[type]": "incoming_chat_message,outgoing_chat_message,incoming_sms,outgoing_sms",
        "limit": 3,
    }),
    ("talks", f"{bot.KOMMO_BASE_URL}/api/v4/talks", {
        "filter[entity_id][]": LEAD,
        "filter[entity_type]": "lead",
        "limit": 5,
    }),
]:
    r = bot._http.get(url, headers=bot.HEADERS, params=params, timeout=15)
    print("\n====", label, r.status_code, "====")
    text = r.text[:2500]
    print(text)
