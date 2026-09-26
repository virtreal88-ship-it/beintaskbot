import time
import requests

BASE = "https://worker-production-3e3e.up.railway.app"
A = {"X-TG-User-ID": "1628569350"}
R = {"X-TG-User-ID": "6824377548"}

print("gozleme admin")
r = requests.get(BASE + "/api/gozleme", headers=A, timeout=40)
print(r.status_code, r.text[:250])

print("overview rufat retry")
t0 = time.time()
r = requests.get(BASE + "/api/samil/overview", headers=R, timeout=90)
print("elapsed", round(time.time() - t0, 1), r.status_code, r.text[:250])

print("notifications admin retry")
r = requests.get(BASE + "/api/notifications", headers=A, timeout=90)
d = r.json()
print(r.status_code, "tasks", len(d.get("tasks") or []), "err", d.get("error"))
