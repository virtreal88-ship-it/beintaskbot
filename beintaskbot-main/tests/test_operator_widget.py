"""Widget isolation, uncertain sends, database receipts and a loopback Socket.IO exchange."""
import json
import uuid
from socketserver import ThreadingMixIn
from threading import Thread
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler

import pytest
import socketio

import tenant_beinsystems_operator as operator
import tenant_operator_widget as widget
import tenant_operator_widget_transport as wire
from tenant_beinsystems_operator_access import widget_allowed
from test_tenant_support_inbox import A, B, db, database

CREDENTIALS = {"token": "synthetic", "generation": "one", "connection_instance_id": str(uuid.UUID(int=91))}
PROFILE = {"tenant_id": A, "telegram_id": 11, "role": "owner", "active": True, "tenant_status":"active"}
CHAT = {"id": 15, "client_id": "widget-123", "channel": "widget", "status": "open",
        "client_name": "Demo", "operator": None, "account": "synthetic-account"}


def test_rest_contract_and_request_context(monkeypatch):
    import asyncio
    import logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from tenant_beinsystems_operator_api import widget_handler

    def http(url, body, token, *, accept_json):
        assert url == operator.OPERATOR_API_BASE_URL + "/chat/getActiveChats"
        assert body == {} and token == "synthetic" and accept_json
        return json.dumps({"status":"success", "data":[CHAT]}).encode()
    monkeypatch.setattr(operator, "_post_read_with_requests", http)
    assert widget._post("/chat/getActiveChats", {}, CREDENTIALS) == [CHAT]

    seen = []
    def projected(member, data):
        seen.append(data)
        return {"chats":[]}
    monkeypatch.setattr(widget, "handle", projected)
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", A)
    async def run():
        app = web.Application()
        app.router.add_post("/widget", widget_handler(lambda request: PROFILE, "https://crm.invalid", logging.getLogger()))
        fields = {"expected_tenant_id": A, "expected_user_id": 11, "action":"list"}
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/widget", headers={"Origin":"https://other.invalid"}, json=fields)
            assert response.status == 403
            headers = {"Origin":"https://crm.invalid"}
            response = await client.post("/widget", headers=headers, json={**fields,"expected_tenant_id":B})
            assert response.status == 409
            response = await client.post("/widget", headers=headers, json=fields)
            assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
            assert await response.json() == {"success":True, "chats":[]}
    asyncio.run(run())
    assert seen == [{"action":"list"}]


def test_cross_channel_and_recycled_ids_never_route():
    mixed = [CHAT, {**CHAT, "id": 20, "channel": "whatsapp"}, {**CHAT, "id": 25, "client_id": "ok"}]
    assert list(widget._routes(mixed, CREDENTIALS)) == [25]
    assert widget._routes([CHAT, {**CHAT, "channel": None, "client_id": "other"}], CREDENTIALS) == {}
    assert widget._routes([{**CHAT, "channel": None}], CREDENTIALS) == {}
    _, revision = widget._routes([CHAT], CREDENTIALS)[15]
    for rows, creds in [([{**CHAT, "client_id": "replacement"}], CREDENTIALS),
                        ([CHAT], {**CREDENTIALS, "generation": "two"})]:
        with pytest.raises(operator.OperatorError):
            widget._route(widget._routes(rows, creds), 15, revision)


def test_permissions(monkeypatch):
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", A)
    staff = {**PROFILE, "role": "operator", "permissions": ["operator_api", "dialogs"]}
    assert widget_allowed(staff) and widget_allowed(staff, "update")
    assert not widget_allowed({**staff, "tenant_id": B})
    assert not widget_allowed({**staff, "permissions": ["dialogs"]})
    assert not widget_allowed({**staff, "active": False})
    viewer = {**staff, "permissions": ["dialogs", "operator_data:view"]}
    assert widget_allowed(viewer) and not widget_allowed(viewer, "update")
    monkeypatch.setattr("tenant_policy.TenantPolicy.action_scope",
                        lambda self, module, action: "self" if module == "dialogs" else None)
    assert not widget_allowed(staff)


def test_foreign_history_and_provider_assignment():
    rows = [{"id": 7, "sender_type": "client", "text": "ok", "chat_id": 15, "client_id": "a"}]
    assert len(widget._messages(rows * 2, 15, "a")) == 1
    for field, replacement in (("chat_id", 1), ("client_id", "b"), ("channel", "telegram")):
        with pytest.raises(operator.OperatorError):
            widget._messages([{**rows[0], field: replacement}], 15, "a")
    assert widget._can_reply(CHAT, None)
    assert widget._can_reply({**CHAT, "operator": "integration"}, "integration")
    assert not widget._can_reply({**CHAT, "operator": "other"}, "integration")
    assert not widget._can_reply({**CHAT, "status": "closed"}, "integration")


@pytest.mark.parametrize("initial_state", ["unknown", "pending"])
def test_widget_projection_and_durable_dispatch(db, monkeypatch, initial_state):
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", A)
    monkeypatch.setattr(operator, "_connect", lambda: db)
    monkeypatch.setattr(operator, "_ensure_schema", lambda conn: None)
    monkeypatch.setattr(operator, "_credentials", lambda tenant: CREDENTIALS)
    metadata = {k:v for k,v in CREDENTIALS.items() if k != "token"}
    db.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata)
                  VALUES(%s,'beinsystems_operator','connected',%s::jsonb)""", (A, json.dumps(metadata)))
    db.commit()
    routes = [CHAT.copy(), {**CHAT, "id": 5, "client_id": "tg", "channel": "telegram"}]
    rows = [{"id": 3, "text": "from widget", "sender_type": "client", "chat_id": 15}]
    monkeypatch.setattr(wire, "socket_block_reason", lambda credentials: "widget_socket_unavailable")
    sent = []
    def post(path, body, credentials):
        assert credentials == CREDENTIALS
        if path == "/chat/getActiveChats":
            assert body == {}
            return routes
        assert path == "/chat/getMessages"
        if "before_id" in body:
            assert body == {"chat_id": 15, "client_id": "widget-123", "limit": 50, "before_id":4}
            return [r for r in rows if r["id"] < body["before_id"]]
        assert body == {"chat_id": 15, "client_id": "widget-123", "limit": 50}
        return rows
    monkeypatch.setattr(widget, "_post", post)
    def dispatch(credentials, row, text, request_id):
        sent.append((row["id"], text))
        return {"state": initial_state}
    monkeypatch.setattr(wire, "send_reply", dispatch)
    result = widget.handle(PROFILE, {"action":"list"})
    assert [r["id"] for r in result["chats"]] == ["15"]
    route = {"chat_id": "15", "revision": result["chats"][0]["revision"]}
    page = widget.handle(PROFILE, {"action":"history", **route})
    assert page["chat"]["can_reply"] is False and page["chat"]["reply_in_operator"] is True
    key = str(uuid.uuid4())
    command = {"action": "reply", **route, "request_id": key, "text": "test reply"}
    assert widget.handle(PROFILE, command)["state"] == initial_state
    assert widget.handle(PROFILE, command)["state"] == initial_state
    assert widget.handle(PROFILE, {**command, "request_id": str(uuid.uuid4())})["state"] == initial_state
    with pytest.raises(operator.OperatorError):
        widget.handle(PROFILE, {**command, "text": "different"})
    assert sent == [(15, "test reply")]
    rows.append({"id": 4, "text": "test reply", "sender_type": "operator", "chat_id": 15, "temp_id": "temp-"+key})
    assert len(widget.handle(PROFILE, {"action": "history", **route})["messages"]) == 2
    older = widget.handle(PROFILE, {"action": "older", "before_id":"4", **route})
    assert [m["id"] for m in older["messages"]] == ["3"] and not older["has_older"]
    assert widget.handle(PROFILE, command) == {"state": "saved", "message_id": "4"}
    routes[0]["operator"] = "somebody-else"
    with pytest.raises(operator.OperatorError):
        widget.handle(PROFILE, {**command, "request_id": str(uuid.uuid4())})
    assert sent == [(15,"test reply")]
    db.execute("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=11",(A,))
    db.commit()
    with pytest.raises(operator.OperatorError):
        widget.handle(PROFILE, {"action": "list"})


def test_real_socket_protocol_on_loopback(monkeypatch):
    class Server(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    class Quiet(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    provider = socketio.Server(async_mode="threading", async_handlers=False, ping_interval=1, ping_timeout=2)
    events = []
    registered = set()

    @provider.event
    def connect(sid, environ, auth):
        return auth == {"role":"operator","token":"synthetic"}

    @provider.on("register_operator")
    def register(sid, data):
        assert data == {}
        registered.add(sid)

    @provider.on("operator_message")
    def reply(sid, data):
        if sid not in registered:
            return
        events.append(data)
        provider.emit("operator_message_saved", {"temp_id":"another", "message_id":100}, to=sid)
        provider.emit("operator_message_pending", {"temp_id":data["temp_id"]}, to=sid)
        provider.emit("message_persisted", {"client_id":data["client_id"], "temp_id":data["temp_id"], "message_id":37}, to=sid)

    with make_server("127.0.0.1", 0, socketio.WSGIApp(provider), server_class=Server, handler_class=Quiet) as http:
        thread = Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            monkeypatch.setattr(wire, "SOCKET_ORIGIN", f"http://127.0.0.1:{http.server_port}")
            request_id = str(uuid.uuid4())
            assert wire.socket_block_reason(CREDENTIALS) == ""
            assert wire.send_reply(CREDENTIALS, CHAT, "hello", request_id) == {"state": "saved", "message_id": "37"}
            assert events == [{"client_id":CHAT["client_id"], "client_name":"Demo","channel":"widget",
                               "text":"hello","temp_id":"temp-"+request_id,"account":"synthetic-account"}]
        finally:
            http.shutdown()
            thread.join(timeout=5)

@pytest.mark.parametrize(("events", "expected"), [
    ([("operator_message_pending", {}), ("operator_message_error", {"reason": "delivery_failed"})],
     {"state": "pending"}),
    ([("operator_message_error", {"reason": "chat_not_owned", "operator": "Demo"})],
     {"state": "rejected", "reason": "chat_not_owned", "operator": "Demo"}),
    ([("operator_message_error", {"reason": "claim_failed"})],
     {"state": "rejected", "reason": "claim_failed"}),
])
def test_provider_outcome_without_saved(monkeypatch, events, expected):
    from contextlib import contextmanager
    from unittest.mock import Mock
    client = Mock()
    handlers = {}
    client.on.side_effect = handlers.__setitem__
    emitted = []

    def emit(event, data):
        emitted.append(event)
        if event == "operator_message":
            for name, details in events:
                handlers[name]({"temp_id": data["temp_id"], **details})

    client.emit.side_effect = emit

    @contextmanager
    def socket(credentials):
        yield client

    monkeypatch.setattr(wire, "_socket", socket)
    monkeypatch.setattr(wire.Event, "wait", lambda self, timeout: self.is_set())
    assert wire.send_reply(CREDENTIALS, CHAT, "hello", "test") == expected
    assert emitted == ["register_operator", "operator_message"]


@pytest.mark.parametrize(("outcomes", "expected"), [
    (["ok"], ""),
    (["network", "ok"], ""),
    (["denied"], "widget_socket_auth_denied"),
    (["network", "network"], "widget_socket_unavailable"),
])
def test_realtime_access_uses_documented_transports_and_classifies_failure(monkeypatch, outcomes, expected):
    attempts = []

    class Client:
        connected = False

        def __init__(self, **options):
            assert options["websocket_extra_options"]["redirect_limit"] == 0
            assert options["reconnection"] is False

        def on(self, event, handler):
            assert event == "connect_error"
            self.error = handler

        def connect(self, url, *, transports, auth, wait_timeout):
            assert url == wire.SOCKET_ORIGIN and auth == {"role": "operator", "token": "synthetic"}
            mode = outcomes[len(attempts)]
            attempts.extend(transports)
            if mode == "ok":
                self.connected = True
                return
            if mode == "denied":
                self.error({"message": "Authentication required"})
            raise socketio.exceptions.ConnectionError("connection failed")

        def disconnect(self):
            self.connected = False

    monkeypatch.setattr(wire, "_availability", None)
    monkeypatch.setattr(wire.socketio, "Client", Client)
    assert wire.socket_block_reason(CREDENTIALS) == expected
    assert wire.socket_block_reason(CREDENTIALS) == expected
    assert attempts == ["websocket", "polling"][:len(outcomes)]
