"""Store and select explicitly approved AI reply examples."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable


_FILE = "ai_reply_examples.json"
_LIMIT = 300
_lock = threading.Lock()
_read_json: Callable[[str], object] = lambda _name: []
_write_json: Callable[[str, object], bool] = lambda _name, _value: False


def configure_ai_reply_examples(*, read_json: Callable, write_json: Callable) -> None:
    global _read_json, _write_json
    _read_json = read_json
    _write_json = write_json


def _ai_reply_example_scope(lead: dict) -> str:
    try:
        pipeline_id = int(lead.get("pipeline_id") or 0)
    except (TypeError, ValueError):
        pipeline_id = 0
    try:
        status_id = int(lead.get("status_id") or 0)
    except (TypeError, ValueError):
        status_id = 0
    return f"{pipeline_id}:{status_id}"


def _ai_reply_tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[\wəıöüçşğ]{3,}", str(text or "").casefold())
        if token not in {"musteri", "müştəri", "menecer", "salam", "ucun", "üçün"}
    }


def _select_ai_reply_examples(lead: dict, history: str, draft: str = "") -> list[dict]:
    data = _read_json(_FILE) or []
    rows = data if isinstance(data, list) else []
    scope = _ai_reply_example_scope(lead)
    requested = _ai_reply_tokens(f"{history}\n{draft}")
    ranked: list[tuple[int, int, dict]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        reply = str(row.get("reply") or "").strip()
        context = str(row.get("context") or "").strip()
        if not reply or not context:
            continue
        overlap = len(requested.intersection(_ai_reply_tokens(context)))
        if not overlap:
            continue
        score = (20 if str(row.get("scope") or "") == scope else 0) + overlap
        try:
            saved_at = int(row.get("saved_at") or 0)
        except (TypeError, ValueError):
            saved_at = 0
        ranked.append((score, saved_at, {"context": context[:700], "reply": reply[:700]}))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:4]]


def save_ai_reply_example(row: dict) -> tuple[bool, bool]:
    """Return (saved, duplicate) while keeping the durable file bounded."""
    with _lock:
        rows = _read_json(_FILE) or []
        rows = rows if isinstance(rows, list) else []
        duplicate = next(
            (
                item for item in rows
                if isinstance(item, dict)
                and item.get("lead_id") == row.get("lead_id")
                and item.get("reply") == row.get("reply")
            ),
            None,
        )
        if duplicate:
            return False, True
        rows.append(dict(row))
        return bool(_write_json(_FILE, rows[-_LIMIT:])), False
