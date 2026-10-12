from datetime import datetime, timezone

from legacy_document_storage import _cache, _load_file, _lock, _save_file

def has_active_session(telegram_id: int, task_id: int) -> bool:
    """Check if there's any session for this task."""
    filename = "kpi.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        key = f"{telegram_id}_{task_id}"
        sessions = data.get(key, {}).get("sessions", [])
    return len(sessions) > 0


def start_task_session(telegram_id: int, task_id: int) -> bool:
    """Start a new session. Returns False if already running."""
    filename = "kpi.json"
    _load_file(filename)
    with _lock:
        data = _cache.setdefault(filename, {})
        key = f"{telegram_id}_{task_id}"
        if key not in data:
            data[key] = {"sessions": []}
        sessions = data[key]["sessions"]
        # Check if there's an active (unfinished) session
        for s in sessions:
            if not s.get("end_time"):
                return False
        sessions.append({
            "start_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": None,
            "actual_minutes": None,
            "paused": False
        })
    _save_file(filename)
    return True


def pause_task_session(telegram_id: int, task_id: int, elapsed_seconds: int) -> bool:
    """Pause (stop) a running session."""
    filename = "kpi.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        key = f"{telegram_id}_{task_id}"
        if key not in data:
            return False
        sessions = data[key]["sessions"]
        # Find active session
        for s in sessions:
            if not s.get("end_time"):
                s["end_time"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                s["actual_minutes"] = round(elapsed_seconds / 60.0, 1)
                s["paused"] = True
                break
        else:
            return False
    _save_file(filename)
    return True


def finish_task_session(
    telegram_id: int,
    task_id: int,
    task_type_id: int,
    delay_reason: str = '',
    deadline_ts: int | None = None,
):
    """Finish all sessions and score 100 before/on deadline, otherwise 0."""
    from datetime import datetime as dt2
    filename = "kpi.json"
    _load_file(filename)

    _KPI_TARGET_TIMES = {
        1: 30, 2: 30, 4232112: 60, 3263995: 45, 3263999: 120, 4232108: 30, 4229224: 60,
    }

    with _lock:
        data = _cache.get(filename, {})
        key = f"{telegram_id}_{task_id}"
        if key not in data:
            return None
        sessions = data[key]["sessions"]
        # Close any active session
        for s in sessions:
            if not s.get("end_time"):
                start_dt = dt2.strptime(s["start_time"], "%Y-%m-%d %H:%M:%S")
                end_dt = dt2.utcnow()
                s["end_time"] = end_dt.strftime("%Y-%m-%d %H:%M:%S")
                s["actual_minutes"] = round((end_dt - start_dt).total_seconds() / 60.0, 1)
        # Sum all sessions
        actual_minutes = sum(s.get("actual_minutes", 0) or 0 for s in sessions)
        target_minutes = _KPI_TARGET_TIMES.get(task_type_id, 60)
        completed_at_ts = int(datetime.now(timezone.utc).timestamp())
        completed_before_deadline = bool(deadline_ts and completed_at_ts <= int(deadline_ts))
        kpi_score = 100 if completed_before_deadline else 0
        data[key]["kpi_score"] = kpi_score
        data[key]["target_minutes"] = target_minutes
        data[key]["actual_minutes"] = round(actual_minutes, 1)
        data[key]["deadline_ts"] = int(deadline_ts) if deadline_ts else None
        data[key]["completed_before_deadline"] = completed_before_deadline
        data[key]["delay_reason"] = delay_reason
        data[key]["completed_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    _save_file(filename)
    return {
        'kpi_score': kpi_score,
        'actual_minutes': round(actual_minutes, 1),
        'target_minutes': target_minutes,
        'deadline_ts': int(deadline_ts) if deadline_ts else None,
        'completed_before_deadline': completed_before_deadline,
        'needs_reason': not completed_before_deadline
    }


def set_kpi_score(telegram_id: int, task_id: int, score: int, corrected_by: int | None = None) -> bool:
    """Apply an admin correction to a stored KPI score."""
    filename = "kpi.json"
    _load_file(filename)
    normalized_score = max(0, min(100, int(score)))
    with _lock:
        data = _cache.get(filename, {})
        key = f"{telegram_id}_{task_id}"
        if key not in data:
            return False
        data[key]["kpi_score"] = normalized_score
        data[key]["manual_correction"] = True
        data[key]["corrected_by"] = corrected_by
        data[key]["corrected_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    return _save_file(filename)


def get_kpi_summary(telegram_id: int) -> dict:
    """Get KPI summary for an employee."""
    filename = "kpi.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
    prefix = f"{telegram_id}_"
    scores = []
    history = []
    for key, val in data.items():
        if key.startswith(prefix) and val.get("kpi_score") is not None:
            task_id = int(key.split("_")[1])
            scores.append(val["kpi_score"])
            history.append({
                "task_id": task_id,
                "kpi_score": val["kpi_score"],
                "actual": val.get("actual_minutes", 0),
                "target": val.get("target_minutes", 0),
                "date": val.get("completed_at", "")
            })
    history.sort(key=lambda x: x.get("date", ""), reverse=True)
    avg_kpi = round(sum(scores) / len(scores), 1) if scores else 0
    return {'avg_kpi': avg_kpi, 'total_tasks': len(scores), 'history': history[:20]}
