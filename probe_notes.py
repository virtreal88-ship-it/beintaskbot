import os
import sys

os.environ.setdefault("TELEGRAM_TOKEN", "0:dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
sys.path.insert(0, "beintaskbot-main")

import bot

LEAD = 78012075
lead, err = bot._authorized_deal_lead(6824377548, LEAD)
cids = bot._lead_contact_ids(lead)
print("contacts", cids)
notes = bot._fetch_entity_notes("leads", LEAD)
print("lead notes", len(notes), "chat", sum(1 for n in notes if n.get("is_chat")))
for n in notes[:8]:
    print(" ", n.get("type"), n.get("is_chat"), n.get("channel"), repr(str(n.get("text"))[:70]))
for cid in cids:
    cnotes = bot._fetch_entity_notes("contacts", cid)
    print("contact", cid, "notes", len(cnotes), "chat", sum(1 for n in cnotes if n.get("is_chat")))
    for n in cnotes[:6]:
        print(" ", n.get("type"), n.get("is_chat"), repr(str(n.get("text"))[:70]))
events, skipped = bot._fetch_chat_events(LEAD, cids)
print("events", len(events), "skipped", skipped)
for e in events[:6]:
    print(" ", e.get("direction"), e.get("origin"), repr(str(e.get("text"))[:70]))
