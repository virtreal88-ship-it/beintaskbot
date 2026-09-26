import os
import sys
from collections import Counter

os.environ.setdefault("TELEGRAM_TOKEN", "0:dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
sys.path.insert(0, "beintaskbot-main")

import bot

types = Counter()
samples = []
page = 1
while page <= 6:
    r = bot._http.get(
        f"{bot.KOMMO_BASE_URL}/api/v4/leads/notes",
        headers=bot.HEADERS,
        params={"limit": 250, "page": page, "order[updated_at]": "desc"},
        timeout=20,
    )
    print("page", page, r.status_code)
    if r.status_code != 200:
        print(r.text[:200])
        break
    notes = (r.json().get("_embedded") or {}).get("notes") or []
    if not notes:
        break
    for n in notes:
        nt = n.get("note_type")
        types[nt] += 1
        if nt in {"incoming_chat_message", "outgoing_chat_message", "instagram_business", "facebook_message", "whatsapp"} and len(samples) < 8:
            p = n.get("params") or {}
            samples.append({
                "entity_id": n.get("entity_id"),
                "type": nt,
                "keys": list(p.keys()),
                "text": str(p.get("text") or "")[:100],
            })
    if len(notes) < 250:
        break
    page += 1
print("types", types)
print("samples", samples)
