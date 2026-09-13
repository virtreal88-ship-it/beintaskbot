import re, requests, json

with open('bot.py') as f:
    content = f.read()
m = re.search(r'KOMMO_TOKEN = os.environ.get\("KOMMO_TOKEN", "([^"]+)"\)', content)
TOKEN = m.group(1)
BASE = "https://texnikidestek50.kommo.com"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Get tasks
resp = requests.get(f"{BASE}/api/v4/tasks", headers=HEADERS, params={"filter[is_completed]": 0, "filter[responsible_user_id]": 10932455, "limit": 3}, timeout=15)
print(f"Tasks status: {resp.status_code}")
if resp.status_code == 200:
    tasks = resp.json().get("_embedded", {}).get("tasks", [])
    for t in tasks[:3]:
        print(f"  Task {t['id']}: entity_type={t.get('entity_type')}, entity_id={t.get('entity_id')}, text={t.get('text','')[:40]}")
        # Get contact phone
        eid = t.get("entity_id")
        etype = t.get("entity_type")
        if etype == "contacts":
            cr = requests.get(f"{BASE}/api/v4/contacts/{eid}", headers=HEADERS, timeout=15)
            if cr.status_code == 200:
                c = cr.json()
                phone = ""
                for cf in (c.get("custom_fields_values") or []):
                    if cf.get("field_code") == "PHONE":
                        vals = cf.get("values", [])
                        if vals: phone = vals[0].get("value", "")
                print(f"    -> Contact: {c.get('name')}, phone: {phone}")
        elif etype == "leads":
            lr = requests.get(f"{BASE}/api/v4/leads/{eid}", headers=HEADERS, params={"with": "contacts"}, timeout=15)
            if lr.status_code == 200:
                lead = lr.json()
                contacts = lead.get("_embedded", {}).get("contacts", [])
                if contacts:
                    cid = contacts[0]["id"]
                    cr = requests.get(f"{BASE}/api/v4/contacts/{cid}", headers=HEADERS, timeout=15)
                    if cr.status_code == 200:
                        c = cr.json()
                        phone = ""
                        for cf in (c.get("custom_fields_values") or []):
                            if cf.get("field_code") == "PHONE":
                                vals = cf.get("values", [])
                                if vals: phone = vals[0].get("value", "")
                        print(f"    -> Lead contact: {c.get('name')}, phone: {phone}")
else:
    print(resp.text[:200])
