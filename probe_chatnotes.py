import os
import sys

os.environ.setdefault("TELEGRAM_TOKEN", "0:dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
sys.path.insert(0, "beintaskbot-main")

import bot

params = {
    "limit": 10,
    "page": 1,
    "order[updated_at]": "desc",
    "filter[note_type][]": ["incoming_chat_message", "outgoing_chat_message"],
}
r = bot._http.get(f"{bot.KOMMO_BASE_URL}/api/v4/leads/notes", headers=bot.HEADERS, params=params, timeout=15)
print("leads/notes", r.status_code)
payload = r.json() if r.content else {}
notes = (payload.get("_embedded") or {}).get("notes") or []
print("count", len(notes))
for n in notes[:5]:
    params_n = n.get("params") or {}
    print({
        "id": n.get("id"),
        "entity_id": n.get("entity_id"),
        "type": n.get("note_type"),
        "created": n.get("created_at"),
        "keys": list(params_n.keys())[:20],
        "text": str(params_n.get("text") or "")[:80],
        "service": params_n.get("service"),
        "origin": params_n.get("origin"),
    })
