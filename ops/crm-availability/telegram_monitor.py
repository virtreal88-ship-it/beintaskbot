"""External CRM availability monitor. All output is safe for public Actions logs."""
import http.client
import json
import os
import sys
import time
from urllib.parse import urlencode

REPO = "virtreal88-ship-it/beintaskbot"
WORKFLOW = "crm-telegram-uptime.yml"
MESSAGES = {
    "down": "CRM недоступна. Внешняя проверка crm.pro.az/health дважды завершилась ошибкой с интервалом в минуту.",
    "up": "CRM восстановилась. Внешняя проверка crm.pro.az/health снова получила ответ Worker.",
}


def healthy():
    conn = http.client.HTTPSConnection("crm.pro.az", timeout=5)
    try:
        conn.request("HEAD", "/health", headers={"Cache-Control": "no-cache"})
        response = conn.getresponse()
        return response.status == 200 and response.getheader("X-Beintaskbot-Health") == "worker"
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def sample():
    if healthy():
        return "up"
    time.sleep(60)
    return "up" if healthy() else "down"


def api(host, path, *, headers, data=None):
    conn = http.client.HTTPSConnection(host, timeout=10)
    try:
        conn.request("POST" if data is not None else "GET", path, data, headers)
        response = conn.getresponse()
        if response.status != 200:
            raise RuntimeError(f"{host} returned status {response.status}")
        return json.loads(response.read())
    finally:
        conn.close()


def github(path):
    return api("api.github.com", f"/repos/{REPO}/actions/{path}", headers={
        "User-Agent": "beintaskbot-availability", "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json",
    })


def previous_state():
    # Only trusted completed default-branch monitor runs can mark a state.
    # An interrupted run or failed Telegram API call cannot acknowledge a change.
    candidates = []
    for event in ("schedule", "workflow_dispatch"):
        params = urlencode(dict(branch="main", status="success", event=event, per_page=1))
        runs = github(f"workflows/{WORKFLOW}/runs?{params}")["workflow_runs"]
        candidates.extend(runs)
    if not candidates:
        return None
    latest = max(candidates, key=lambda run: (run["updated_at"], run["id"]))
    jobs = github(f"runs/{latest['id']}/jobs")["jobs"]
    for job in jobs:
        if job["name"] == "Monitor" and job["conclusion"] == "success":
            for step in job["steps"]:
                if step["conclusion"] == "success" and step["name"] in ("Checkpoint up", "Checkpoint down"):
                    return step["name"].split()[1]
    raise RuntimeError("Missing checkpoint in the previous successful monitor run")


def telegram(method, fields):
    data = urlencode({"chat_id": os.environ["CRM_CHAT_ID"], **fields}).encode()
    result = api("api.telegram.org", "/bot" + os.environ["CRM_BOT_TOKEN"] + "/" + method,
                 headers={"Content-Type": "application/x-www-form-urlencoded"}, data=data)
    if not result.get("ok"):
        raise RuntimeError("Telegram did not acknowledge request")
    return result["result"]


def publish(state, previous):
    if previous is None:
        chat = telegram("getChat", {})
        if chat.get("type") != "private" or str(chat.get("id")) != os.environ["CRM_CHAT_ID"]:
            raise RuntimeError("Private recipient verification failed")
    if state != previous and (previous is not None or state == "down"):
        telegram("sendMessage", {"text": MESSAGES[state]})


if __name__ == "__main__":
    try:
        state = sample()
        publish(state, previous_state())
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"state={state}\n")
    except Exception as exc:
        # Never serialize exceptions, URLs, response bodies, chat IDs or credentials.
        print(f"Availability monitor failed ({type(exc).__name__})", file=sys.stderr)
        sys.exit(1)
