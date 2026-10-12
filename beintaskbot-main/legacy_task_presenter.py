"""Pure formatting helpers for legacy Kommo task cards."""
from __future__ import annotations

import re
from datetime import datetime


_ASSIGNEE_MARKER_RE = re.compile(
    r"^\[(Rüfət Həsənzadə|Soltan Abbasov|Hüseyn Səfərov|Nizami Qasımov|"
    r"Rasim Əsgərov|Sərmayə Əhmədsoy|Asya Agayeva|Nuranə Şirinova|"
    r"Texniki Dəstək|Texniki tapşırıq|Rüfət|Soltan|Hüseyn|Nizami|Rasim|"
    r"Sərmayə|Asya|Nuranə|Texniki)(?::\d+)?\]\s*"
)
_PRICE_MARKER_RE = re.compile(r"^\[(?:[^:\]]*:)?(\d+(?:\.\d+)?)\]")

_SHORT_TO_FULL = {
    "Rüfət": "Rüfət Həsənzadə",
    "Soltan": "Soltan Abbasov",
    "Hüseyn": "Hüseyn Səfərov",
    "Nizami": "Nizami Qasımov",
    "Rasim": "Rasim Əsgərov",
    "Sərmayə": "Sərmayə Əhmədsoy",
    "Asya": "Asya Agayeva",
    "Nuranə": "Nuranə Şirinova",
    "Texniki": "Texniki Dəstək",
}

TASK_TYPE_NAMES = {
    1: "Əlaqə saxla",
    2: "Görüş",
    3263995: "Təqdimat",
    3263999: "Quraşdırma",
    3267595: "Zəng et",
    4229224: "Cavab gözlənilir",
    4232112: "aktiv",
    4232108: "Import",
    4239844: "passiv",
}


def assignee_from_marker(task_text: str) -> str:
    match = _ASSIGNEE_MARKER_RE.match(str(task_text or ""))
    if not match:
        return ""
    name = match.group(1)
    return _SHORT_TO_FULL.get(name, name)


def price_from_marker(task_text: str) -> str:
    match = _PRICE_MARKER_RE.match(str(task_text or ""))
    return match.group(1) if match else ""


def deadline_view(deadline_ts: int, now: datetime) -> tuple[datetime | None, bool, str]:
    if not deadline_ts:
        return None, False, ""
    deadline = datetime.fromtimestamp(int(deadline_ts), tz=now.tzinfo)
    overdue = deadline < now
    if not overdue:
        return deadline, False, deadline.strftime("%d.%m %H:%M")
    seconds = max(0, int((now - deadline).total_seconds()))
    hours = seconds // 3600
    if hours:
        return deadline, True, f"{hours} saat gecikir"
    return deadline, True, f"{seconds // 60} dəq gecikir"
