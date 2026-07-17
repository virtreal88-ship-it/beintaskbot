"""
GitHub-based JSON storage for balance and KPI data.
Stores data in JSON files on a 'data' branch to avoid triggering redeploys.
"""
import json
import base64
import logging
import threading
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

_GH_TOKEN = None
_GH_REPO = "virtreal88-ship-it/beintaskbot"
_GH_BRANCH = "data"
_GH_API = "https://api.github.com"

# In-memory cache
_cache = {}
_cache_sha = {}
_lock = threading.Lock()


def init_storage(token: str):
    global _GH_TOKEN
    _GH_TOKEN = token
    # Always refresh persisted data on startup, even if the cache was populated earlier.
    _load_file("balance.json", force=True)
    _load_file("kpi.json", force=True)


def _headers():
    headers = {"Accept": "application/vnd.github.v3+json"}
    if _GH_TOKEN:
        headers["Authorization"] = f"token {_GH_TOKEN}"
    return headers


def _load_file(filename: str, force: bool = False) -> dict:
    """Load a JSON file from GitHub data branch into cache."""
    with _lock:
        if not force and filename in _cache:
            return _cache[filename]
    try:
        r = requests.get(
            f"{_GH_API}/repos/{_GH_REPO}/contents/{filename}?ref={_GH_BRANCH}",
            headers=_headers(), timeout=15
        )
        if r.status_code == 200:
            content = base64.b64decode(r.json()["content"]).decode("utf-8")
            data = json.loads(content)
            with _lock:
                _cache[filename] = data
                _cache_sha[filename] = r.json()["sha"]
            return data
        else:
            with _lock:
                if filename not in _cache:
                    _cache[filename] = {}
                _cache_sha[filename] = None
            return _cache.get(filename, {})
    except Exception as e:
        logger.error(f"gh_storage load {filename}: {e}")
        with _lock:
            if filename not in _cache:
                _cache[filename] = {}
                _cache_sha[filename] = None
        return _cache.get(filename, {})


def _save_file(filename: str):
    """Save cached data to GitHub with retry on SHA conflict."""
    with _lock:
        data = _cache.get(filename, {})
    # Always fetch fresh SHA before saving to avoid conflicts after redeploy
    logger.info(f"gh_storage _save_file({filename}): starting save...")
    try:
        r_sha = requests.get(
            f"{_GH_API}/repos/{_GH_REPO}/contents/{filename}?ref={_GH_BRANCH}",
            headers=_headers(), timeout=15
        )
        if r_sha.status_code == 200:
            sha = r_sha.json()["sha"]
            with _lock:
                _cache_sha[filename] = sha
        else:
            sha = None
            with _lock:
                _cache_sha[filename] = None
    except:
        sha = _cache_sha.get(filename)
    content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()
    payload = {
        "message": f"Update {filename}",
        "content": content,
        "branch": _GH_BRANCH
    }
    if sha:
        payload["sha"] = sha

    for attempt in range(3):
        try:
            r = requests.put(
                f"{_GH_API}/repos/{_GH_REPO}/contents/{filename}",
                headers=_headers(), json=payload, timeout=15
            )
            if r.status_code in (200, 201):
                logger.info(f"gh_storage _save_file({filename}): SUCCESS")
                with _lock:
                    _cache_sha[filename] = r.json()["content"]["sha"]
                return True
            elif r.status_code in (409, 422):
                # SHA conflict or file exists without sha: refetch and retry.
                logger.warning(
                    f"gh_storage save {filename}: {r.status_code}, "
                    f"refetching SHA (attempt {attempt + 1})"
                )
                try:
                    r2 = requests.get(
                        f"{_GH_API}/repos/{_GH_REPO}/contents/{filename}?ref={_GH_BRANCH}",
                        headers=_headers(), timeout=15
                    )
                    if r2.status_code == 200:
                        new_sha = r2.json()["sha"]
                        with _lock:
                            _cache_sha[filename] = new_sha
                        payload["sha"] = new_sha
                    else:
                        payload.pop("sha", None)
                        with _lock:
                            _cache_sha[filename] = None
                except Exception as e2:
                    logger.error(f"gh_storage refetch SHA error: {e2}")
                continue
            else:
                logger.error(f"gh_storage save {filename}: {r.status_code} {r.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"gh_storage save {filename} error: {e}")
            return False
    return False


# ─── Balance Functions ────────────────────────────────────────────────────────

def add_balance_transaction(telegram_id: int, task_id: int, amount: float, task_text: str):
    """Add a transaction to local cache and persist to GitHub."""
    filename = "balance.json"
    with _lock:
        data = _cache.setdefault(filename, {})
        key = str(telegram_id)
        if key not in data:
            data[key] = {"transactions": []}
        data[key]["transactions"].append({
            "task_id": task_id,
            "amount": amount,
            "task_text": task_text,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    _save_file(filename)


def get_balance(telegram_id: int) -> float:
    """Get total balance for a user."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        key = str(telegram_id)
        txns = data.get(key, {}).get("transactions", [])
    return sum(t["amount"] for t in txns)


def get_balance_transactions(telegram_id: int, limit: int = 50) -> list:
    """Get recent transactions for a user."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        key = str(telegram_id)
        txns = data.get(key, {}).get("transactions", [])
    return list(reversed(txns[-limit:]))


def get_all_balances() -> dict:
    """Get balances for all employees."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
    result = {}
    for key, val in data.items():
        txns = val.get("transactions", [])
        result[int(key)] = sum(t["amount"] for t in txns)
    return result


def get_all_recent_transactions(limit: int = 30) -> list:
    """Get recent transactions across all employees."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
    all_txns = []
    for key, val in data.items():
        for t in val.get("transactions", []):
            all_txns.append({**t, "telegram_id": int(key)})
    all_txns.sort(key=lambda x: x.get("date", ""), reverse=True)
    return all_txns[:limit]


# ─── KPI Functions ────────────────────────────────────────────────────────────

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


def finish_task_session(telegram_id: int, task_id: int, task_type_id: int, delay_reason: str = ''):
    """Finish all sessions for a task and calculate KPI."""
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
        if actual_minutes == 0:
            return None
        target_minutes = _KPI_TARGET_TIMES.get(task_type_id, 60)
        if actual_minutes <= target_minutes:
            kpi_score = min(100, (target_minutes / max(actual_minutes, 1)) * 80)
        else:
            kpi_score = max(0, 80 - ((actual_minutes - target_minutes) / target_minutes) * 60)
        kpi_score = round(kpi_score, 1)
        data[key]["kpi_score"] = kpi_score
        data[key]["target_minutes"] = target_minutes
        data[key]["actual_minutes"] = round(actual_minutes, 1)
        data[key]["delay_reason"] = delay_reason
        data[key]["completed_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    _save_file(filename)
    return {
        'kpi_score': kpi_score,
        'actual_minutes': round(actual_minutes, 1),
        'target_minutes': target_minutes,
        'needs_reason': actual_minutes > target_minutes * 1.2
    }


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
