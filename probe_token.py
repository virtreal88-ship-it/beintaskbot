import base64
import datetime
import json
import re

src = open("beintaskbot-main/bot.py", encoding="utf-8").read()
token = re.search(r'KOMMO_TOKEN = os\.environ\.get\("KOMMO_TOKEN", "([^"]+)"\)', src).group(1)
payload = token.split(".")[1]
payload += "=" * (-len(payload) % 4)
data = json.loads(base64.urlsafe_b64decode(payload))
print("scopes:", data.get("scopes"))
for key in ("iat", "exp"):
    print(key, datetime.datetime.fromtimestamp(data[key]).isoformat())
print("account_id", data.get("account_id"), "sub", data.get("sub"))
