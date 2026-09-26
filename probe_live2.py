import requests

BASE = "https://worker-production-3e3e.up.railway.app"
headers_r = {"X-TG-User-ID": "6824377548"}
headers_a = {"X-TG-User-ID": "1628569350"}

for name, url, headers in [
    ("stages rufat", "/api/stages", headers_r),
    ("notifications rufat", "/api/notifications", headers_r),
    ("deal view 78012075 rufat", "/api/deal/view?lead_id=78012075", headers_r),
    ("deal chat 78012075 rufat", "/api/deal/chat?lead_id=78012075&limit=5", headers_r),
    ("deal view 78012075 admin", "/api/deal/view?lead_id=78012075", headers_a),
    ("deal chat 78012075 admin", "/api/deal/chat?lead_id=78012075&limit=5", headers_a),
    ("react empty", None, None),
]:
    if name == "react empty":
        r = requests.post(
            BASE + "/api/deal/chat/react",
            headers={"Content-Type": "application/json", "X-TG-User-ID": "6824377548"},
            json={"lead_id": 78012075, "external_id": "wamid.HBgMODE.probe", "emoji": "👍"},
            timeout=25,
        )
        print(name, r.status_code, r.text[:300])
        continue
    r = requests.get(BASE + url, headers=headers, timeout=40)
    print(name, r.status_code, r.text[:280].replace("\n", " "))
