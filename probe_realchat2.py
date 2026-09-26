import os
import sys
from collections import Counter

os.environ.setdefault("TELEGRAM_TOKEN", "0:dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
sys.path.insert(0, "beintaskbot-main")

import bot

LEAD = 75319315
lead = bot.get_lead_details(LEAD)
cids = bot._lead_contact_ids(lead) if lead else []
print("lead pipeline", lead.get("pipeline_id") if lead else None, "contacts", cids)

for cid in cids[:2]:
    r = bot._http.get(
        f"{bot.KOMMO_BASE_URL}/api/v4/contacts/{cid}/notes",
        headers=bot.HEADERS,
        params={"limit": 50, "page": 1, "order[created_at]": "desc"},
        timeout=15,
    )
    notes = (r.json().get("_embedded") or {}).get("notes") or [] if r.status_code == 200 else []
    print("contact notes", cid, r.status_code, len(notes), Counter(n.get("note_type") for n in notes))
    for n in notes[:8]:
        p = n.get("params") or {}
        print(" ", n.get("note_type"), list(p.keys())[:15], repr(str(p.get("text") or "")[:90]))

r = bot._http.get(
    f"{bot.KOMMO_BASE_URL}/api/v4/events",
    headers=bot.HEADERS,
    params={
        "filter[entity]": "lead",
        "filter[entity_id]": LEAD,
        "limit": 20,
    },
    timeout=15,
)
print("events", r.status_code, len(r.content or b""))
if r.status_code == 200:
    events = (r.json().get("_embedded") or {}).get("events") or []
    print("count", len(events), Counter(e.get("type") for e in events))
    for e in events[:8]:
        after = e.get("value_after") or []
        print(" ", e.get("type"), e.get("created_at"), str(after)[:180])

talks = bot._fetch_talks(LEAD, cids)
print("talks", len(talks))
for t in talks:
    print(" ", bot._talk_id_of(t), t.get("origin"), t.get("chat_id"), t.get("source_id"))
    msgs, blocked, more = bot._fetch_talk_messages(bot._talk_id_of(t), pages=1, page_limit=5)
    print("   messages", len(msgs), "blocked", blocked)
