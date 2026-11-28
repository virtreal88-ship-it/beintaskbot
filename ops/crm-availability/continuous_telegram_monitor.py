"""Continuous external observer for CRM. Run on an independent always-on host."""
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

import telegram_monitor

INTERVAL = 30
CONFIRM_SECONDS = 60
MAX_SAMPLE_GAP = 45


class Observer:
    def __init__(self, db, send, *, clock=time.monotonic):
        self.db, self.send, self.clock = db, send, clock
        self.last_sample = self.bad_since = None
        self.db.execute("""CREATE TABLE IF NOT EXISTS checkpoint(
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          state TEXT NOT NULL CHECK(state IN ('up','down_pending','down_unknown','down_delivered')))""")
        self.db.execute("INSERT OR IGNORE INTO checkpoint VALUES (1,'up')")
        self.db.commit()

    def _state(self):
        return self.db.execute("SELECT state FROM checkpoint").fetchone()[0]

    def _transition(self, before, after):
        with self.db:
            return self.db.execute(
                "UPDATE checkpoint SET state=? WHERE state=?",(after,before)).rowcount == 1

    def sample(self, healthy):
        now = self.clock()
        if self.last_sample is not None and now - self.last_sample > MAX_SAMPLE_GAP:
            self.bad_since = None
        self.last_sample = now
        if healthy:
            self.bad_since = None
            state = self._state()
            if state != 'up' and self._transition(state, 'up') and state == 'down_delivered':
                return self._notify("up")
            return
        if self.bad_since is None:
            self.bad_since = now
        if now - self.bad_since < CONFIRM_SECONDS:
            return
        if self._state() == 'up':
            self._transition('up', 'down_pending')
        # Commit uncertainty before the network call; never resend the same incident
        # on HTTP timeout, non-200, or unclean process restart.
        if self._transition('down_pending', 'down_unknown') and self._notify("down"):
            self._transition('down_unknown', 'down_delivered')

    def _notify(self, status):
        try:
            self.send(status)
            print(f"CRM availability transition acknowledged: {status}", flush=True)
            return True
        except Exception:
            print(f"CRM availability notification not acknowledged: {status}", file=sys.stderr, flush=True)
            return False


def initialize_credentials():
    root = Path(os.environ["CREDENTIALS_DIRECTORY"])
    chat_id = (root / "telegram-chat-id").read_text(encoding="utf-8").strip()
    token = (root / "telegram-bot-token").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,17}", chat_id) or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("Invalid private chat configuration")
    os.environ.update(CRM_CHAT_ID=chat_id, CRM_BOT_TOKEN=token)
    # Revalidate on every restart and before each alert; no group destination.
    verify_recipient()


def verify_recipient():
    chat = telegram_monitor.telegram("getChat", {})
    if chat.get("type") != "private" or str(chat.get("id")) != os.environ["CRM_CHAT_ID"]:
        raise ValueError("Destination is not the configured private chat")


def send(state):
    verify_recipient()
    messages = {
        "down": "CRM недоступна. Независимая проверка crm.pro.az/health не получила подтверждение Worker в течение минуты.",
        "up": "CRM снова доступна. Независимая проверка crm.pro.az/health получила ответ Worker.",
    }
    response = telegram_monitor.telegram("sendMessage", {"text": messages[state]})
    if not isinstance(response.get("message_id"), int):
        raise RuntimeError("No Telegram message acknowledgement")


def main(*, probe=telegram_monitor.healthy, clock=time.monotonic, sleep=time.sleep):
    initialize_credentials()
    state_dir = Path(os.environ["STATE_DIRECTORY"])
    with sqlite3.connect(state_dir / "monitor.sqlite", timeout=5) as db:
        monitor = Observer(db, send, clock=clock)
        while True:
            started = clock()
            monitor.sample(probe())
            sleep(max(0, INTERVAL - (clock() - started)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never leak response bodies, chat IDs, or URLs containing bot tokens.
        print(f"CRM observer stopped: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
