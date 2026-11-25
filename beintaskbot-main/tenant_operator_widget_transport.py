"""Authenticated Socket.IO operations observed in Operator's support chat client."""
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
    result = ("unknown", None)
    finished = Event()
    emitted = False
    pending = Event()

    def matches(event):
        return (isinstance(event, dict) and event.get("temp_id") == temp_id
                and event.get("client_id", row["client_id"]) == row["client_id"])

    def saved(event):
        nonlocal result
        if matches(event) and (mid := _positive(event.get("message_id"))):
            result = ("saved", str(mid))
            finished.set()

    def delivered_pending(event):
        if matches(event):
            pending.set()

    def rejected(event):
        nonlocal result
        if (matches(event) and not finished.is_set() and not pending.is_set()
                and (event.get("operator") or event.get("reason") == "delivery_failed")):
            result = ("rejected", None)
            finished.set()

    try:
        with _socket(credentials) as client:
            client.on("operator_message_saved", saved)
            client.on("message_persisted", saved)
            client.on("operator_message_pending", delivered_pending)
            client.on("operator_message_error", rejected)
            # A pending event can mean already delivered. Wait for persistence and never resend on timeout.
            emitted = True
            client.emit("operator_message", {"client_id": row["client_id"], "channel": "widget",
                        "client_name": row.get("client_name"), "account": row.get("account"),
                        "text": text, "temp_id": temp_id})
            finished.wait(8)
    except (OperatorError, socketio.exceptions.SocketIOError, OSError, ValueError):
        if not emitted:
            return "rejected", None
    return result
