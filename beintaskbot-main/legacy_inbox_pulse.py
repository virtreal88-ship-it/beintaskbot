"""Thread-safe bounded event stream for the legacy customer inbox."""

from __future__ import annotations

import threading


_revision = 0
_events: list[dict] = []
_lock = threading.Lock()
_LIMIT = 150


def append_inbox_event(event: dict) -> dict:
    global _revision
    row = dict(event or {})
    with _lock:
        _revision += 1
        row["rev"] = _revision
        _events.append(row)
        if len(_events) > _LIMIT:
            del _events[:-_LIMIT]
    return dict(row)


def inbox_events_since(revision: int = 0) -> tuple[int, list[dict]]:
    try:
        after = max(0, int(revision or 0))
    except (TypeError, ValueError):
        after = 0
    with _lock:
        return _revision, [dict(row) for row in _events if int(row.get("rev") or 0) > after]


def clear_inbox_events() -> None:
    global _revision
    with _lock:
        _revision = 0
        _events.clear()
