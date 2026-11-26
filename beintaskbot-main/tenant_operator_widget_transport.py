"""Authenticated Socket.IO operations for the Operator support chat."""
from contextlib import contextmanager
import hashlib
import logging
from threading import Event, Lock
from time import monotonic

import socketio

from tenant_beinsystems_operator import OperatorError, _NoRedirectRequestsSession


SOCKET_ORIGIN = "https://operator-realtime.beinsystems.az"


@contextmanager
def _socket(credentials, *, origin=None):
    origin = SOCKET_ORIGIN if origin is None else origin
    with _NoRedirectRequestsSession() as http:
        http.trust_env = False
        for transport in ("websocket", "polling"):
            denied = False

            def connect_error(detail=None):
                nonlocal denied
                message = detail.get("message") if isinstance(detail, dict) else detail
                denied = isinstance(message, str) and message.strip().lower() in {
                    "authentication required", "unauthorized", "invalid token", "token expired", "forbidden"
                }

            client = socketio.Client(http_session=http, reconnection=False, request_timeout=5,
                                     websocket_extra_options={"redirect_limit": 0})
            client.on("connect_error", connect_error)
            try:
                client.connect(origin, transports=[transport], wait_timeout=5,
                               auth={"role": "operator", "token": credentials["token"]})
                break
            except (socketio.exceptions.SocketIOError, OSError, ValueError):
                if denied:
                    raise OperatorError("Operator отклонил авторизацию чата.", 401) from None
        else:
            raise OperatorError("Не удалось подключиться к чату Operator.", 502)
        try:
            yield client
        finally:
            if client.connected:
                client.disconnect()


_availability_lock = Lock()
_availability = None


def socket_block_reason(credentials):
    """A valid REST credential can still be denied by chat. Probe authentication only."""
    global _availability
    key = (credentials["connection_instance_id"], credentials["generation"],
           hashlib.sha256(credentials["token"].encode()).digest())
    with _availability_lock:
        if _availability and _availability[0] == key and monotonic() < _availability[1]:
            return _availability[2]
        try:
            with _socket(credentials):
                reason = ""
        except OperatorError as exc:
            reason = "widget_socket_auth_denied" if exc.status == 401 else "widget_socket_unavailable"
        if not _availability or _availability[0] != key or _availability[2] != reason:
            logging.getLogger(__name__).info("Operator widget realtime check: %s", reason or "connected")
        _availability = (key, monotonic() + (30 if reason else 120), reason)
        return reason


def probe_realtime_origin(credentials):
    """Check the send endpoint without registering or emitting a message."""
    try:
        with _socket(credentials, origin=SOCKET_ORIGIN):
            outcome = "connected"
    except OperatorError as exc:
        outcome = "auth_denied" if exc.status == 401 else "unavailable"
    logging.getLogger(__name__).info("Operator realtime auth probe: %s", outcome)
    return outcome


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
            registration = ({"name": credentials["widget_login"], "role": "operator"}
                            if credentials.get("widget_login") else {})
            client.emit("register_operator", registration)
            emitted = True
            client.emit("operator_message", {"client_id": row["client_id"], "channel": "widget",
                        "client_name": row.get("client_name") or row["client_id"], "account": row.get("account"),
                        "text": text, "temp_id": temp_id})
            finished.wait(8)
    except (OperatorError, socketio.exceptions.SocketIOError, OSError, ValueError):
        if not emitted:
            return {"state": "rejected"}
    return result
