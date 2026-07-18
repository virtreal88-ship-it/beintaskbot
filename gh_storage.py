"""
GitHub-based JSON storage for balance and KPI data.
Stores data in JSON files on a 'data' branch to avoid triggering redeploys.
"""
import json
import base64
import logging
import threading
import requests
from datetime import datetime, timezone

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


def read_json(filename: str):
    """Read an arbitrary JSON document from the GitHub-backed data branch."""
    data = _load_file(filename)
    # Return a detached value so callers cannot mutate the shared cache without
    # going through write_json().
    return json.loads(json.dumps(data, ensure_ascii=False))


def write_json(filename: str, data) -> bool:
    """Persist an arbitrary JSON-serializable document to the data branch."""
    detached = json.loads(json.dumps(data, ensure_ascii=False))
    with _lock:
        _cache[filename] = detached
    return _save_file(filename)


# ─── Balance Functions ────────────────────────────────────────────────────────

def _transaction_status(transaction: dict) -> str:
    """Treat legacy transactions without a status as already confirmed."""
    status = str(transaction.get("status") or "confirmed").lower()
    return status if status in {"pending", "confirmed"} else "confirmed"


def _transaction_total(transactions: list, status: str) -> float:
    return round(sum(
        float(transaction.get("amount", 0) or 0)
        for transaction in transactions
        if _transaction_status(transaction) == status
    ), 2)


def _ensure_balance_account(data: dict, key: str) -> dict:
    """Normalize a balance account while preserving all legacy transactions."""
    account = data.setdefault(key, {})
    transactions = account.setdefault("transactions", [])
    if not isinstance(transactions, list):
        transactions = []
        account["transactions"] = transactions
    for transaction in transactions:
        transaction.setdefault("status", "confirmed")
        transaction.setdefault("phone", "")
        transaction.setdefault("result_text", transaction.get("task_text", ""))
        transaction.setdefault("kpi", 0)
    account["balance"] = _transaction_total(transactions, "confirmed")
    return account


def add_balance_transaction(
    telegram_id: int,
    task_id: int,
    amount: float,
    task_text: str,
    executor_name: str = "",
    client: str = "",
    task_type: str = "",
    phone: str = "",
    result_text: str = "",
    kpi: int = 0,
    status: str = "confirmed",
    transaction_type: str = "task",
):
    """Add a pending or confirmed transaction with complete business context."""
    filename = "balance.json"
    _load_file(filename)
    normalized_status = status if status in {"pending", "confirmed"} else "confirmed"
    normalized_task_id = int(task_id or 0)
    with _lock:
        data = _cache.setdefault(filename, {})
        account = _ensure_balance_account(data, str(telegram_id))
        if normalized_task_id and transaction_type == "task":
            duplicate = next((
                item for item in account["transactions"]
                if int(item.get("task_id", 0) or 0) == normalized_task_id
                and item.get("type", "task") == "task"
            ), None)
            if duplicate is not None:
                return True
        transaction = {
            "task_id": normalized_task_id,
            "executor": executor_name,
            "client": client,
            "phone": phone,
            "task_type": task_type,
            "amount": float(amount),
            "status": normalized_status,
            "task_text": task_text,
            "result_text": result_text or task_text,
            "kpi": int(kpi or 0),
            "type": transaction_type,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        account["transactions"].append(transaction)
        account["balance"] = _transaction_total(account["transactions"], "confirmed")
    if _save_file(filename):
        return True
    with _lock:
        if transaction in account["transactions"]:
            account["transactions"].remove(transaction)
        account["balance"] = _transaction_total(account["transactions"], "confirmed")
    return False


def confirm_balance_transaction(telegram_id: int, task_id: int):
    """Confirm a pending task transaction and return its updated account totals."""
    filename = "balance.json"
    _load_file(filename)
    changed = False
    normalized_task_id = int(task_id)
    with _lock:
        data = _cache.setdefault(filename, {})
        account = _ensure_balance_account(data, str(telegram_id))
        matching = [
            item for item in reversed(account["transactions"])
            if int(item.get("task_id", 0) or 0) == normalized_task_id
        ]
        transaction = next((
            item for item in matching if _transaction_status(item) == "pending"
        ), matching[0] if matching else None)
        if transaction is None:
            return None
        if _transaction_status(transaction) == "pending":
            transaction["status"] = "confirmed"
            changed = True
        account["balance"] = _transaction_total(account["transactions"], "confirmed")
        result = {
            "transaction": dict(transaction),
            "balance": account["balance"],
            "pending_balance": _transaction_total(account["transactions"], "pending"),
            "already_confirmed": not changed,
        }
    if changed and not _save_file(filename):
        with _lock:
            transaction["status"] = "pending"
            account["balance"] = _transaction_total(account["transactions"], "confirmed")
            result.update({
                "transaction": dict(transaction),
                "balance": account["balance"],
                "pending_balance": _transaction_total(account["transactions"], "pending"),
                "save_failed": True,
            })
        return result
    return result


def get_balance(telegram_id: int) -> float:
    """Get the sum of confirmed transactions for a user."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        account = _ensure_balance_account(data, str(telegram_id))
        return account["balance"]


def get_pending_balance(telegram_id: int) -> float:
    """Get the sum of pending transactions for a user."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        transactions = _ensure_balance_account(data, str(telegram_id))["transactions"]
        return _transaction_total(transactions, "pending")


def get_balance_transactions(telegram_id: int, limit: int = 50) -> list:
    """Get recent normalized transactions for a user."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        transactions = _ensure_balance_account(data, str(telegram_id))["transactions"]
        return [dict(item) for item in reversed(transactions[-limit:])]


def get_all_balances() -> dict:
    """Get confirmed balances for all employees."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        return {
            int(key): _ensure_balance_account(data, key)["balance"]
            for key in data
        }


def get_all_pending_balances() -> dict:
    """Get pending balances for all employees."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        return {
            int(key): _transaction_total(
                _ensure_balance_account(data, key)["transactions"], "pending"
            )
            for key in data
        }


def get_all_recent_transactions(limit: int = 30) -> list:
    """Get recent normalized transactions across all employees."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        all_transactions = []
        for key in data:
            account = _ensure_balance_account(data, key)
            for transaction in account["transactions"]:
                all_transactions.append({**transaction, "telegram_id": int(key)})
    all_transactions.sort(key=lambda item: item.get("date", ""), reverse=True)
    return all_transactions[:limit]


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

# ─── Push Subscriptions ──────────────────────────────────────────────────────
_PUSH_FILE = "push_subscriptions.json"

def load_push_subscriptions() -> dict:
    """Load all push subscriptions from data branch. Returns {user_id: subscription_info}."""
    return _load_file(_PUSH_FILE) or {}

def save_push_subscription(user_id: str, subscription: dict):
    """Save a push subscription for a user."""
    data = _load_file(_PUSH_FILE)
    data[str(user_id)] = subscription
    with _lock:
        _cache[_PUSH_FILE] = data
    _save_file(_PUSH_FILE)

def get_push_subscription(user_id: str) -> dict | None:
    """Get push subscription for a specific user."""
    data = _load_file(_PUSH_FILE)
    return data.get(str(user_id))

def remove_push_subscription(user_id: str):
    """Remove a push subscription (e.g. when expired)."""
    data = _load_file(_PUSH_FILE)
    if str(user_id) in data:
        del data[str(user_id)]
        with _lock:
            _cache[_PUSH_FILE] = data
        _save_file(_PUSH_FILE)
