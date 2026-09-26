import os
import sys
from collections import Counter

os.environ.setdefault("TELEGRAM_TOKEN", "0:dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
sys.path.insert(0, "beintaskbot-main")

import bot

LEAD = 75319315
r = bot._http.get(
    f"{bot.KOMMO_BASE_URL}/api/v4/leads/{LEAD}/notes",
    headers=bot.HEADERS,
    params={"limit": 50, "page": 1, "order[created_at]": "desc"},
    timeout=15,
)
print("status", r.status_code, "len", len(r.content or b""))
if r.status_code == 200:
    notes = (r.json().get("_embedded") or {}).get("notes") or []
    print("notes", len(notes), "types", Counter(n.get("note_type") for n in notes))
    for n in notes[:12]:
        p = n.get("params") or {}
        print(n.get("note_type"), n.get("created_at"), list(p.keys())[:12], repr(str(p.get("text") or "")[:90]))

lead, err = bot._authorized_deal_lead(6824377548, LEAD)
print("authorized", bool(lead), err)
if lead:
    cids = bot._lead_contact_ids(lead)
    print("contacts", cids)
    chat, blocked, talk_id, has_more, channels, channel = bot._collect_deal_chat(
        LEAD, cids, limit=20,
        sender_digits=bot._wa_sender_digits_for_chat(6824377548),
        employee_name=bot.employee_name_for_lead(lead),
    )
    print("chat", len(chat), "blocked", blocked, "channels", [c.get("key") for c in channels], "talk", talk_id)
    for row in chat[-8:]:
        print(" ", row.get("channel"), row.get("direction"), repr(str(row.get("text"))[:80]))
