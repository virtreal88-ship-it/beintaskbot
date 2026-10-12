from legacy_document_storage import _cache, _load_file, _lock, _save_file

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
