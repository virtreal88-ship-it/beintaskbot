from datetime import datetime
import uuid

from legacy_document_storage import _cache, _load_file, _lock, _save_file

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
    source_key: str = "",
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
        # Non-task operations (for example, a hot order) may be retried after a
        # network error. A stable source key makes that retry safe and avoids
        # crediting the same work twice.
        normalized_source_key = str(source_key or "").strip()
        if normalized_source_key:
            duplicate = next((
                item for item in account["transactions"]
                if str(item.get("source_key") or "") == normalized_source_key
            ), None)
            if duplicate is not None:
                return True
        transaction = {
            "id": uuid.uuid4().hex,
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
            "source_key": normalized_source_key,
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


def confirm_balance_transaction(telegram_id: int, task_id: int = 0, tx_id: str | None = None):
    """Confirm a pending transaction by id, else by task_id."""
    filename = "balance.json"
    _load_file(filename)
    changed = False
    normalized_task_id = int(task_id or 0)
    wanted_id = str(tx_id or "").strip()
    with _lock:
        data = _cache.setdefault(filename, {})
        account = _ensure_balance_account(data, str(telegram_id))
        matching = list(reversed(account["transactions"]))
        transaction = None
        if wanted_id:
            transaction = next((item for item in matching if str(item.get("id") or "") == wanted_id), None)
            if transaction is None:
                for item in matching:
                    synthetic = f"{telegram_id}|{item.get('date')}|{item.get('task_id', 0)}|{item.get('amount')}"
                    if synthetic == wanted_id:
                        transaction = item
                        break
        if transaction is None and normalized_task_id:
            by_task = [
                item for item in matching
                if int(item.get("task_id", 0) or 0) == normalized_task_id
            ]
            transaction = next((
                item for item in by_task if _transaction_status(item) == "pending"
            ), by_task[0] if by_task else None)
        if transaction is None and not wanted_id and not normalized_task_id:
            transaction = next((
                item for item in matching if _transaction_status(item) == "pending"
            ), None)
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
        result["save_failed"] = True
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


def get_balance_transaction_count(telegram_id: int) -> int:
    """Return the ledger size without copying or serializing every row."""
    filename = "balance.json"
    _load_file(filename)
    with _lock:
        data = _cache.get(filename, {})
        return len(_ensure_balance_account(data, str(telegram_id))["transactions"])


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
