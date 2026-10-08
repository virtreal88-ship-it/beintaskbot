"""Choose a bounded read window; only completed runs advance the watermark."""
from typing import NamedTuple

OVERLAP_SECONDS = 300
SETTLE_SECONDS = 30
FULL_SCAN_SECONDS = 24 * 60 * 60


class SyncWindow(NamedTuple):
    start: int
    end: int
    watermark: int
    last_full: int


def choose_window(previous: dict | None, now: int, *, force_full: bool = False) -> SyncWindow:
    row = previous or {}
    watermark = max(0, int(row.get('watermark') or 0))
    last_full = max(0, int(row.get('last_full_sync') or 0))
    end = max(0, int(now) - SETTLE_SECONDS)
    full = force_full or not watermark or not last_full or now - last_full >= FULL_SCAN_SECONDS or watermark > end
    return SyncWindow(0 if full else max(0, watermark - OVERLAP_SECONDS), end, watermark, last_full)


def window_params(row: dict) -> dict[str, int]:
    # Old in-flight jobs have zero bounds: keep their original full-read
    # contract until completion. Their next run establishes a real watermark.
    end = int(row.get('window_to') or 0)
    if not end:
        return {}
    result = {'filter[updated_at][to]': end}
    start = int(row.get('window_from') or 0)
    if start:
        result['filter[updated_at][from]'] = start
    return result
