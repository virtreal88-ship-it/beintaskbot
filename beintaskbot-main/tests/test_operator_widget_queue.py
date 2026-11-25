"""Real SQL integration of the inbox lifecycle with a synthetic Operator route."""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import tenant_beinsystems_operator as op
import tenant_dialog_internal_comments as comments
import tenant_dialog_lifecycle as lifecycle
import tenant_operator_widget as widget
import tenant_operator_widget_queue as queue
import tenant_operator_widget_sync as sync
from tenant_linear_policy import TenantLinearError
from test_operator_widget import CHAT, CREDENTIALS, PROFILE
from test_tenant_support_inbox import A, B, db, database


def test_queue_lifecycle_replay_filter_and_delivery_boundary(db, monkeypatch):
    monkeypatch.setenv("BEINSYSTEMS_OPERATOR_TENANT_ID", A)
    monkeypatch.setenv("BEINSYSTEMS_WIDGET_QUEUE_ENABLED", "true")
    monkeypatch.setattr(op, "_connect", lambda: db)
    monkeypatch.setattr(op, "_credentials", lambda tenant: CREDENTIALS)
    import tenant_operator_widget_transport as transport

    monkeypatch.setattr(transport, "socket_block_reason", lambda _: "widget_socket_unavailable")
    cur = db.cursor()
    lifecycle.migrate(cur)
    comments.provision_schema(cur)
    cur.execute(
        """INSERT INTO saas_dialog_rollout(tenant_id,mode,activated_at,admission_enabled,generation)
        VALUES(%s,'active',now(),true,1)""",
        (A,),
    )
    cur.execute(
        """INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata)
        VALUES(%s,'beinsystems_operator','connected',%s::jsonb)""",
        (A, json.dumps({k: v for k, v in CREDENTIALS.items() if k != "token"})),
    )
    routes = widget._routes([CHAT], CREDENTIALS)
    sync.snapshot(cur, A, CREDENTIALS, routes, "integration")
    key = (A, CREDENTIALS["connection_instance_id"], 15, widget._hash([CHAT["client_id"]]))
    params = dict(provider=sync.PROVIDER, channel_id=key[1], lead_id=key[2], conversation_id=key[3])

    def send(mid, at):
        cur.execute("SELECT * FROM saas_widget_dialogs WHERE " + sync.KEY, key)
        sync.ingest(
            cur,
            cur.fetchone(),
            [
                {
                    "id": str(mid),
                    "name": "",
                    "sender": "client",
                    "text": "test",
                    "created_at": at.isoformat(),
                    "attachment": False,
                }
            ],
            False,
        )
        db.commit()

    start = datetime.now(timezone.utc)
    send(1, start - timedelta(days=2))

    def page():
        return queue.history(PROFILE, params)

    def change(action, route=None, **extra):
        return queue.mutate(
            PROFILE,
            {
                **params,
                "action": action,
                "request_id": str(uuid.uuid4()),
                "expected_revision": (route or page()["dialog"])["lifecycle"]["revision"],
                **extra,
            },
        )

    with pytest.raises(TenantLinearError):
        page()
    send(2, start + timedelta(seconds=1))
    send(2, start + timedelta(seconds=1))
    page1 = page()
    assert len(page1["messages"]) == 1
    assert page1["dialog"]["lifecycle"]["state"] == "waiting"
    _, _, policy = queue.access(cur, PROFILE)
    rows, channel = queue.cards(cur, policy, "active", queue.guard(cur, A))
    assert [(c["lead_id"], c["unread_count"]) for c in rows] == [(15, 1)]
    assert channel == {"id": "operator_widget:" + key[1], "name": "Чат сайта BeinSystems"}
    # Exercise the normal listing and its channel filter, including pagination and counters.
    import tenant_operator_dialogs as inbox

    monkeypatch.setattr(inbox, "_connect", lambda: db)
    regular = inbox.listing(PROFILE, channel=channel["id"])
    assert channel in regular["channels"]
    assert [(r["provider"], r["lead_id"]) for r in regular["dialogs"]] == [(sync.PROVIDER, 15)]
    account = CHAT["account"]
    assert regular["dialogs"][0]["dialog_company_name"] == account
    assert [r["lead_id"] for r in inbox.listing(PROFILE, search=account)["dialogs"]] == [15]
    company = str(uuid.uuid4())
    cur.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name) VALUES(%s,%s,'Confirmed Co')", (company, A))
    cur.execute("""INSERT INTO saas_beinsystems_operator_links
        (id,tenant_id,connection_generation,connection_instance_id,local_entity_type,local_entity_id,
         external_entity_type,external_id,verification_basis,evidence_summary,verified_by)
        VALUES(%s,%s,%s,%s,'company',%s,'account',%s,'manual','Verified in test',11)""",
        (str(uuid.uuid4()), A, CREDENTIALS["generation"], key[1], company, account))
    db.commit()
    linked = page()["dialog"]
    assert (linked["dialog_company_name"], linked["crm_company_id"], linked["operator_account"]) == (
        "Confirmed Co", company, account)
    assert [r["lead_id"] for r in inbox.listing(PROFILE, search="Confirmed Co")["dialogs"]] == [15]
    # Same route without new activity: replace or remove only the source assertion.
    for changed, expected in [("other-account", "other-account"), (None, None), (["invalid"], None), (account, "Confirmed Co")]:
        sync.snapshot(cur, A, CREDENTIALS, widget._routes([{**CHAT, "account": changed}], CREDENTIALS), "integration")
        db.commit()
        assert page()["dialog"]["dialog_company_name"] == expected
        assert page()["dialog"]["lifecycle"]["state"] == "waiting"
    with pytest.raises(TenantLinearError):
        queue.history({"tenant_id": B, "telegram_id": 22}, params)
    change("accept")
    assert page()["dialog"]["send_block_reason"] == "widget_socket_unavailable"
    import tenant_platform

    monkeypatch.setattr(tenant_platform, "_connect", lambda: db)
    note = {**params, "request_id": str(uuid.uuid4()), "note": "internal only"}
    assert comments.save(PROFILE, note) == comments.save(PROFILE, note)
    assert [c["note"] for c in page()["internal_comments"]] == ["internal only"]
    assert not any(m["body"] == "internal only" for m in page()["messages"])
    cur.execute(
        """INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s,33,'operator','Agent','["dialogs","operator_data","operator_api"]'::jsonb)""",
        (A,),
    )
    db.commit()
    transfer = {
        **params,
        "action": "transfer",
        "request_id": str(uuid.uuid4()),
        "target_employee_id": 33,
        "expected_revision": page()["dialog"]["lifecycle"]["revision"],
    }
    receipt = lifecycle.mutate(PROFILE, transfer)
    assert receipt["lifecycle"]["operator_id"] == 33
    assert lifecycle.mutate(PROFILE, transfer) == receipt
    # The retained #960 reader also respects a newly accepted employee's ownership.
    monkeypatch.setattr(
        widget, "_post", lambda path, body, creds: [CHAT] if path.endswith("getActiveChats") else []
    )
    assert widget.handle(PROFILE, {"action": "list"})["chats"] == []
    with pytest.raises(op.OperatorError):
        widget.handle(PROFILE, {"action": "history", "chat_id": "15", "revision": routes[15][1]})
    with pytest.raises(TenantLinearError):
        page()
    recipient = {"tenant_id": A, "telegram_id": 33}
    owned = queue.history(recipient, params)
    assert owned["internal_comments"][0]["note"] == "internal only"
    assert owned["dialog"]["crm_company_id"] is None
    assert owned["dialog"]["dialog_company_name"] == account
    cur.execute("UPDATE saas_beinsystems_operator_links SET revoked_at=now() WHERE tenant_id=%s AND local_entity_id=%s", (A, company))
    db.commit()
    lifecycle.mutate(
        recipient,
        {
            **transfer,
            "target_employee_id": 11,
            "request_id": str(uuid.uuid4()),
            "expected_revision": owned["dialog"]["lifecycle"]["revision"],
        },
    )
    queue.read(PROFILE, {**params, "message_id": "2"})
    assert queue.cards(cur, policy, "active", queue.guard(cur, A))[0][0]["unread_count"] == 0
    change("dismiss", individual_no_reply=True, message_id="2")
    assert page()["dialog"]["lifecycle"]["state"] == "accepted"
    assert page()["dialog"]["lifecycle"]["no_reply_needed"]
    change("close")
    send(2, start + timedelta(seconds=1))
    assert page()["dialog"]["lifecycle"]["state"] == "closed"
    send(3, start + timedelta(seconds=5))
    assert page()["dialog"]["lifecycle"]["state"] == "waiting"
    assert [m["id"] for m in page()["messages"]] == ["2", "3"]
    assert len(queue.cards(cur, policy, "active", queue.guard(cur, A))[0]) == 1
    with pytest.raises(TenantLinearError):
        queue.history(PROFILE, {**params, "conversation_id": "other"})


def test_worker_skips_dormant_history_and_resumes_new_pages(db, monkeypatch):
    monkeypatch.setattr(op, "_connect", lambda: db)
    monkeypatch.setattr(op, "_credentials", lambda tenant: CREDENTIALS)
    cur = db.cursor()
    cur.execute(
        """INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata)
        VALUES(%s,'beinsystems_operator','connected',%s::jsonb)""",
        (A, json.dumps({k: v for k, v in CREDENTIALS.items() if k != "token"})),
    )
    db.commit()
    ids = list(range(1, 77))
    start = datetime.now(timezone.utc)

    updated = start - timedelta(days=1)
    calls = []

    def upstream(path, data, credentials):
        if path == "/chat/getActiveChats":
            return [{**CHAT, "updated_at": updated.isoformat()}]
        calls.append(data.copy())
        assert path == "/chat/getMessages" and data["chat_id"] == 15
        chosen = [mid for mid in ids if mid < data.get("before_id", 10**6)][-sync.PAGE :]
        return [
            {
                "id": mid,
                "chat_id": 15,
                "client_id": CHAT["client_id"],
                "sender_type": "client",
                "text": "test",
                "created_at": (start + timedelta(days=1 if mid >= 77 else -1)).isoformat(),
            }
            for mid in chosen
        ]

    monkeypatch.setattr(sync, "_post", upstream)
    sync.sync_once(A)
    assert calls == []
    cur.execute("SELECT count(*) AS n FROM saas_widget_dialogs WHERE tenant_id=%s", (A,))
    assert cur.fetchone()["n"] == 0
    updated = start + timedelta(days=1)
    ids.extend(range(77, 77 + sync.PAGE + 2))  # Multiple new pages after activation.
    sync.sync_once(A)
    ids.append(ids[-1] + 1)  # A new arrival during pagination is picked up after restart.
    for _ in range(4):
        sync.sync_once(A)
    cur.execute(
        "SELECT count(*) AS total,count(*) FILTER(WHERE fresh) AS unread FROM saas_widget_messages WHERE tenant_id=%s",
        (A,),
    )
    assert cur.fetchone() == {"total": sync.PAGE + 3, "unread": sync.PAGE + 3}
    assert not any(req.get("before_id", 77) < 77 for req in calls)
    cur.execute("SELECT state,latest_in,high_id FROM saas_widget_dialogs WHERE tenant_id=%s", (A,))
    assert cur.fetchone() == {"state": "waiting", "latest_in": ids[-1], "high_id": ids[-1]}
