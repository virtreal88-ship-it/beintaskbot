import os
import sys

os.environ.setdefault("TELEGRAM_TOKEN", "0:dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
sys.path.insert(0, "beintaskbot-main")

import bot

CHAT_ID = "b5774057-7600-4225-933b-71a1c50a2a0b"
TALK_ID = 10973

urls = [
    f"https://amojo.kommo.com/v1/chats/{CHAT_ID}/history",
    f"https://amojo.kommo.com/v2/chats/{CHAT_ID}/history",
    f"{bot.KOMMO_BASE_URL}/ajax/v4/chats/{CHAT_ID}/history",
    f"{bot.KOMMO_BASE_URL}/ajax/v2/chats/{CHAT_ID}/history",
    f"{bot.KOMMO_BASE_URL}/api/v4/chats/{CHAT_ID}/messages",
    f"{bot.KOMMO_BASE_URL}/ajax/v4/talks/{TALK_ID}/messages",
    f"{bot.KOMMO_BASE_URL}/api/v4/talks/{TALK_ID}",
    f"{bot.KOMMO_BASE_URL}/ajax/v4/chats/origin/waba/{CHAT_ID}",
]

for url in urls:
    try:
        r = bot.requests.get(url, headers={"Authorization": f"Bearer {bot.KOMMO_TOKEN}"}, params={"limit": 5}, timeout=12)
        print("\n====", r.status_code, url, "====")
        print(r.text[:800].replace("\n", " "))
    except Exception as exc:
        print("\n==== EXC", url, exc)
