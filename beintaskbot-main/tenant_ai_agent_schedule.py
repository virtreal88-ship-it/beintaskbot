"""Small, deterministic schedule helpers shared by AI send paths.

``days`` describes the day on which a shift starts.  This matters for an
overnight shift: a Monday 22:00--06:00 shift is still open on Tuesday at
02:00, even when Tuesday itself is not selected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _values(schedule: dict) -> tuple[ZoneInfo, set[int], int, int] | None:
    if not isinstance(schedule, dict) or not schedule:
        return None
    zone = ZoneInfo(str(schedule.get('timezone') or 'Asia/Baku'))
    raw_days = schedule.get('days') or range(7)
    days = {int(day) for day in raw_days}
    start_h, start_m = (int(value) for value in str(schedule.get('start') or '00:00').split(':'))
    end_h, end_m = (int(value) for value in str(schedule.get('end') or '23:59').split(':'))
    return zone, days, start_h * 60 + start_m, end_h * 60 + end_m


def is_open(schedule: dict, *, now: datetime | None = None) -> bool:
    """Return whether a validated agent schedule admits work at ``now``.

    An empty schedule deliberately means always open.  Persisted schedules
    pass policy validation; this helper only handles their calendar semantics.
    """
    values = _values(schedule)
    if values is None:
        return True
    zone, days, start, end = values
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    minute = local.hour * 60 + local.minute
    if end > start:
        return local.weekday() in days and start <= minute < end
    # Overnight: the shift belongs to its start day.  At 02:00 on Tuesday,
    # check Monday's selected 22:00--06:00 shift before Tuesday's next start.
    if local.weekday() in days and minute >= start:
        return True
    previous_day = (local.weekday() - 1) % 7
    return previous_day in days and minute < end


def next_open(schedule: dict, *, now: datetime | None = None) -> datetime | None:
    """Return the next UTC shift start, or ``None`` when work is allowed now."""
    values = _values(schedule)
    if values is None:
        return None
    zone, days, start, _end = values
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    if is_open(schedule, now=local):
        return None
    start_hour, start_minute = divmod(start, 60)
    # A full week plus the current day is sufficient for a non-empty weekday
    # set.  ``candidate > local`` prevents immediately re-opening a shift that
    # has already ended today.
    for offset in range(8):
        day = local.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        candidate = datetime(day.year, day.month, day.day, start_hour, start_minute, tzinfo=zone)
        if candidate > local:
            return candidate.astimezone(timezone.utc)
    return None
