import json
import requests

BASE = "https://worker-production-3e3e.up.railway.app"
ADMIN = "1628569350"
RUFAT = "6824377548"


def get(path, uid, timeout=90):
    r = requests.get(
        BASE + path,
        headers={"X-TG-User-ID": uid},
        timeout=timeout,
    )
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:240]}
    return r.status_code, data


print("--- notifications admin ---")
code, data = get("/api/notifications", ADMIN)
print("status", code, "success", data.get("success"), "tasks", len(data.get("tasks") or []), "error", data.get("error", ""), "user", data.get("user_name"))

print("--- overview rufat ---")
code, data = get("/api/samil/overview", RUFAT)
print("status", code, "success", data.get("success"), "deals", len(data.get("deals") or []), "tasks", len(data.get("tasks") or []), "error", data.get("error", ""))
deals = data.get("deals") or []
lead_id = deals[0]["id"] if deals else 75319315
print("sample lead", lead_id, deals[0].get("contact_name") if deals else "")

print("--- overview admin ---")
code, adata = get("/api/samil/overview", ADMIN)
print("status", code, "success", adata.get("success"), "deals", len(adata.get("deals") or []), "error", adata.get("error", ""))

print("--- chat rufat ---")
code, chat = get(f"/api/deal/chat?lead_id={lead_id}&limit=10", RUFAT)
print(
    "status", code,
    "success", chat.get("success"),
    "chat", len(chat.get("chat") or []),
    "blocked", chat.get("chat_blocked"),
    "channels", [c.get("key") for c in (chat.get("channels") or [])],
    "can_reply", chat.get("can_reply"),
    "cloud_ready", chat.get("cloud_ready"),
    "error", chat.get("error", ""),
)

print("--- react probe rufat ---")
r = requests.post(
    BASE + "/api/deal/chat/react",
    headers={"Content-Type": "application/json", "X-TG-User-ID": RUFAT},
    json={"lead_id": lead_id, "external_id": "wamid.probe", "emoji": "👍"},
    timeout=30,
)
try:
    payload = r.json()
except Exception:
    payload = {"raw": r.text[:240]}
print("status", r.status_code, "success", payload.get("success"), "error", payload.get("error", ""))
