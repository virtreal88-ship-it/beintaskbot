"""Authenticated Socket.IO operations for the Operator support chat."""
from contextlib import contextmanager
import hashlib
from threading import Event, Lock
from time import monotonic

import socketio

from tenant_beinsystems_operator import OperatorError, _NoRedirectRequestsSession


SOCKET_ORIGIN = "https://operator.beinsystems.az"


@contextmanager
def _socket(credentials):
    with _NoRedirectRequestsSession() as http:
        http.trust_env = False
        client = socketio.Client(http_session=http, reconnection=False, request_timeout=5)
        try:
            # Both sites are fixed. The Socket.IO endpoint lives on the UI origin, not the REST origin.
            client.connect(SOCKET_ORIGIN, transports=["polling"],
                           auth={"role": "operator", "token": credentials["token"]}, wait_timeout=5)
        except (socketio.exceptions.SocketIOError, OSError, ValueError):
            raise OperatorError("Не удалось подключиться к чату Operator.", 502) from None
        try:
            yield client
        finally:
            if client.connected:
                client.disconnect()


_availability_lock = Lock()
_availability = None


def socket_available(credentials):
    """A valid REST credential can still be denied by chat. Probe authentication only."""
    global _availability
    key = (credentials["connection_instance_id"], credentials["generation"],
           hashlib.sha256(credentials["token"].encode()).digest())
    with _availability_lock:
        if _availability and _availability[0] == key and monotonic() < _availability[1]:
            return _availability[2]
        try:
            with _socket(credentials):
                ready = True
        except (OperatorError, socketio.exceptions.SocketIOError, OSError, ValueError):
            ready = False
        _availability = (key, monotonic() + 120, ready)
        return ready


def send_reply(credentials, row, text, request_id):
    from tenant_operator_widget import _positive
    temp_id = "temp-" + request_id
    result = {"state": "unknown"}
    finished = Event()
    emitted = False
    outcome_lock = Lock()

    def matches(event):
        return (isinstance(event, dict) and event.get("temp_id") == temp_id
                and event.get("client_id", row["client_id"]) == row["client_id"])

    def saved(event):
        nonlocal result
        if matches(event) and (mid := _positive(event.get("message_id"))):
            with outcome_lock:
                result = {"state": "saved", "message_id": str(mid)}
                finished.set()

    def delivered_pending(event):
        nonlocal result
        with outcome_lock:
            if matches(event) and result["state"] == "unknown":
                result = {"state": "pending"}

    def rejected(event):
        nonlocal result
        if not matches(event) or event.get("reason") not in ("chat_not_owned", "claim_failed", "delivery_failed"):
            return
        with outcome_lock:
            if result["state"] != "unknown":
                return
            result = {"state": "rejected", "reason": event["reason"]}
            owner = event.get("operator")
            if event["reason"] == "chat_not_owned" and isinstance(owner, str):
                result["operator"] = owner[:240]
            finished.set()

    try:
        with _socket(credentials) as client:
            client.on("operator_message_saved", saved)
            client.on("message_persisted", saved)
            client.on("operator_message_pending", delivered_pending)
            client.on("operator_message_error", rejected)
            # A pending event can mean already delivered. Wait for persistence and never resend on timeout.
            client.emit("register_operator", {})
            emitted = True
            client.emit("operator_message", {"client_id": row["client_id"], "channel": "widget",
                        "client_name": row.get("client_name") or row["client_id"], "account": row.get("account"),
                        "text": text, "temp_id": temp_id})
            finished.wait(8)
    except (OperatorError, socketio.exceptions.SocketIOError, OSError, ValueError):
        if not emitted:
            return {"state": "rejected"}
    return result
