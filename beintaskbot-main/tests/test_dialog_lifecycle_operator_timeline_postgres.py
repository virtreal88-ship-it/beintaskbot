"""Durable native lifecycle history, with disposable PostgreSQL only."""
import json
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_pglite import episodes, payload
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db
from tenant_linear_policy import TenantLinearError


def test_timeline_is_actual_exact_and_does_not_change_message_pages(episodes, monkeypatch):
    import tenant_dialog_lifecycle as lifecycle
    import tenant_operator_dialogs as operator
    monkeypatch.setattr(operator, "_connect", lambda: psycopg.connect(episodes.dsn, row_factory=dict_row))
    worker = {"tenant_id":A,"telegram_id":33}
    accept=payload("accept",0)
    first=payload("transfer",1,target_employee_id=33)
    second=payload("transfer",2,target_employee_id=11)
    repeated=lifecycle.mutate(PROFILE,accept)
    assert lifecycle.mutate(PROFILE,accept)==repeated
    repeated=lifecycle.mutate(PROFILE,first)
    lifecycle.mutate(worker,second)
    assert lifecycle.mutate(PROFILE,first)==repeated
    lifecycle.mutate(PROFILE,payload("close",3))
    lifecycle.mutate(PROFILE,payload("start",4))
    # Keep the committed actions, but place them deterministically on opposite
    # sides of the normal 50-message page and its cursor.
    audit=episodes.execute("""SELECT id::text,action,payload->>'revision' AS revision
      FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND entity_type='dialog_episode'""",(A,))
    audit_ids={(row["action"],row["revision"]):row["id"] for row in audit}
    assert len(audit_ids)==len(audit)==5
    for key,when in {( "dialog_accept","1"):"2030-01-01",("dialog_transfer","2"):"2030-03-10T12:00:00Z",
                     ("dialog_transfer","3"):"2030-04-10",("dialog_start","5"):"2030-04-11"}.items():
        episodes.execute("UPDATE saas_tenant_audit_events SET created_at=%s WHERE id=%s::uuid",(when,audit_ids[key]))
    # Make the fallback observable without inventing a person.
    episodes.execute("UPDATE saas_tenant_members SET display_name='' WHERE tenant_id=%s::uuid AND telegram_id=33",(A,))
    for i in range(60):
        row_id=str(uuid.uuid4())
        episodes.execute("""INSERT INTO saas_crm_messages
          (id,tenant_id,kommo_lead_id,external_id,channel,direction,body,created_at,happened_at)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','Synthetic page message',
            '2030-03-01'::timestamptz+(%s*interval '1 day'),
            '2030-03-01'::timestamptz+(%s*interval '1 day'))""",(row_id,A,row_id,'connect:'+CA,i,i))
    # Other tenant and exact route decoys are both real-shaped ledger rows,
    # but none belongs to the requested channel/conversation/tenant.
    for tenant,channel,conversation in ((A,CB,'client'),(A,CA,'other'),(B,CA,'client')):
        episodes.execute("""INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,
          entity_type,entity_id,payload,created_at) VALUES(%s::uuid,%s::uuid,11,'dialog_accept',
          'dialog_episode',%s,%s::jsonb,'2030-05-08')""",
          (str(uuid.uuid4()),tenant,str(uuid.uuid4()),json.dumps({"channel_id":channel,"conversation_id":conversation,"operator_id":11})))
    episodes.commit()
    def read(**opts):
        return operator.history(PROFILE,'connect',41,CA,'client',**opts)
    ordinary=read()
    assert "operator_events" not in ordinary
    latest=read(include_operator_events=True)
    assert latest["messages"]==ordinary["messages"]
    assert latest["next_before"]==ordinary["next_before"]
    assert latest["read_through_message_id"]==ordinary["read_through_message_id"]
    assert latest["operator_events_revision"]==5
    assert [event["id"] for event in latest["operator_events"]]==[audit_ids["dialog_transfer","3"],audit_ids["dialog_start","5"]]
    assert [event["kind"] for event in latest["operator_events"]]==["transfer","start"]
    assert all(event["employee_name"]=="Owner" for event in latest["operator_events"])
    assert len(latest["messages"])==50
    older=read(include_operator_events=True,before=latest["next_before"])
    assert [event["id"] for event in older["operator_events"]]==[audit_ids["dialog_accept","1"],audit_ids["dialog_transfer","2"]]
    assert [event["kind"] for event in older["operator_events"]]==["accept","transfer"]
    assert [event["employee_name"] for event in older["operator_events"]]==["Owner",""]
    assert all(set(event)=={"id","kind","at","employee_name"} for event in older["operator_events"])
    assert older["next_before"] is None
    assert {m["id"] for m in older["messages"]}.isdisjoint({m["id"] for m in latest["messages"]})
    assert "operator_events" not in read(include_operator_events=True,operator_events_revision=5)
    assert "operator_events" not in operator.history(PROFILE,'connect',41,include_operator_events=True)
    assert "operator_events" not in read(include_operator_events=True,anchor=latest["messages"][0]["id"])
    with pytest.raises(TenantLinearError):
        operator.history(worker,'connect',41,CA,'client',include_operator_events=True)
    with pytest.raises(TenantLinearError):
        operator.history(PROFILE,'connect',41,CB,'client',include_operator_events=True)


@pytest.mark.parametrize("lock_budget,error_type", [("0", "QueryCanceled"), ("25ms", "LockNotAvailable")])
def test_optional_audit_timeout_preserves_history_and_cursor(episodes, monkeypatch, lock_budget, error_type):
    import tenant_dialog_lifecycle as lifecycle
    import tenant_dialog_operator_timeline as timeline
    import tenant_operator_dialogs as operator
    def connect():
        conn=psycopg.connect(episodes.dsn,row_factory=dict_row)
        conn.execute("SELECT set_config('lock_timeout',%s,false)",(lock_budget,))
        conn.commit()
        return conn
    monkeypatch.setattr(operator, "_connect", connect)
    lifecycle.mutate(PROFILE,payload())
    from collections import OrderedDict
    now=[100.0]
    monkeypatch.setattr(timeline,"monotonic",lambda:now[0])
    monkeypatch.setattr(timeline,"_cooldown",OrderedDict())
    original=timeline._read_window
    attempts=[]
    def locked_query(conn,cur,*args,**kwargs):
        attempts.append(True)
        if len(attempts)>1:
            return original(conn,cur,*args,**kwargs)
        # Force one timeout in the disposable DB. A new inbound revision
        # during cooldown must return real messages without trying the ledger.
        with psycopg.connect(episodes.dsn,row_factory=dict_row) as other:
            other.execute("LOCK saas_tenant_audit_events IN ACCESS EXCLUSIVE MODE")
            try:
                return original(conn,cur,*args,**kwargs)
            except psycopg.Error as error:
                assert type(error).__name__==error_type
                raise
    monkeypatch.setattr(timeline,"_read_window",locked_query)
    def history():
        return operator.history(PROFILE,'connect',41,CA,'client',include_operator_events=True)
    result=history()
    assert result['messages'] and 'operator_events' not in result
    assert result['read_through_message_id']
    assert 12000<=result['operator_events_retry_after_ms']<=12001
    episodes.execute("UPDATE saas_dialog_workflows SET revision=revision+1 WHERE tenant_id=%s::uuid AND channel_id=%s::uuid",(A,CA))
    episodes.commit()
    now[0]+=2
    skipped=history()
    assert len(attempts)==1 and 'operator_events' not in skipped
    assert skipped['messages']==result['messages'] and skipped['read_through_message_id']==result['read_through_message_id']
    assert 10000<=skipped['operator_events_retry_after_ms']<=10001
    now[0]+=11
    retry=history()
    assert len(attempts)==2 and [e['kind'] for e in retry['operator_events']]==['accept']
    assert 'operator_events_retry_after_ms' not in retry


def test_group_notes_remain_exact_to_selected_source_even_with_union_message_cursor(episodes, monkeypatch):
    import tenant_dialog_lifecycle as lifecycle
    import tenant_operator_dialogs as operator
    monkeypatch.setattr(operator, "_connect", lambda: psycopg.connect(episodes.dsn, row_factory=dict_row))
    group='120363000000000101@g.us'
    alternate='00000000-0000-4000-8000-000000000203'
    episodes.execute("UPDATE saas_connect_conversations SET conversation_id=%s,conversation_kind='group' WHERE tenant_id=%s::uuid AND lead_id=41",(group,A))
    episodes.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Synthetic second source')",(A,alternate))
    episodes.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,
        display_name,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,43,'Synthetic group','group')""",(A,alternate,group))
    episodes.commit()
    for lead,channel in ((41,CA),(43,alternate)):
        lifecycle.mutate(PROFILE,{**payload('accept',0),'lead_id':lead,'channel_id':channel,'conversation_id':group})
    grouped={row["channel"]:row["id"] for row in episodes.execute("""SELECT id::text,payload->>'channel_id' AS channel
      FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='dialog_accept'""",(A,))}
    for event in grouped.values():
        episodes.execute("UPDATE saas_tenant_audit_events SET created_at='2030-01-01' WHERE id=%s::uuid",(event,))
    for i in range(51):
        row_id=str(uuid.uuid4())
        episodes.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,
          created_at,happened_at) VALUES(%s::uuid,%s::uuid,43,%s,%s,'incoming','Synthetic other-source message',
          '2030-03-01'::timestamptz+(%s*interval '1 minute'),'2030-03-01'::timestamptz+(%s*interval '1 minute'))""",
          (row_id,A,row_id,'connect:'+alternate,i,i))
    episodes.commit()
    first=operator.history(PROFILE,'connect',41,CA,group,include_operator_events=True)
    assert first['dialog']['customer_kind']=='group'
    assert len(first['messages'])==50 and first['operator_events']==[]
    assert all(message['source_channel_id']==alternate for message in first['messages'])
    primary=operator.history(PROFILE,'connect',41,CA,group,before=first['next_before'],include_operator_events=True)
    alternate_page=operator.history(PROFILE,'connect',43,alternate,group,before=first['next_before'],include_operator_events=True)
    assert [e['id'] for e in primary['operator_events']]==[grouped[CA]]
    assert [e['id'] for e in alternate_page['operator_events']]==[grouped[alternate]]
    with pytest.raises(TenantLinearError):
        operator.history(PROFILE,'connect',41,alternate,group,include_operator_events=True)


def test_dense_equal_time_window_is_stably_limited_and_reports_omissions(episodes, monkeypatch):
    import tenant_dialog_lifecycle as lifecycle
    import tenant_operator_dialogs as operator
    from tenant_dialog_operator_timeline import _PAGE_LIMIT
    monkeypatch.setattr(operator, "_connect", lambda: psycopg.connect(episodes.dsn, row_factory=dict_row))
    lifecycle.mutate(PROFILE,payload())
    ids=[str(uuid.UUID(int=9000+i)) for i in range(2*_PAGE_LIMIT+5)]
    args=(A,json.dumps({"channel_id":CA,"conversation_id":"client","operator_id":11}),ids)
    episodes.execute("""INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,
      entity_type,entity_id,payload,created_at) SELECT id,%s::uuid,11,'dialog_transfer','dialog_episode',
      'synthetic-dense',%s::jsonb,'2030-10-01'::timestamptz FROM unnest(%s::uuid[]) AS id""",args)
    episodes.commit()
    def read(**opts):
        return operator.history(PROFILE,'connect',41,CA,'client',**opts)
    base=read()
    for _ in range(2):
        result=read(include_operator_events=True)
        assert [e['id'] for e in result['operator_events']]==ids[-_PAGE_LIMIT:]
        assert result['operator_events_omitted_at'].startswith('2030-10-01T00:00:00')
        assert result['messages']==base['messages'] and result['next_before']==base['next_before']
        assert result['read_through_message_id']==base['read_through_message_id']


def test_concurrent_probe_pressure_releases_optional_slots_and_bounds_retry_registry(monkeypatch):
    from collections import OrderedDict
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from psycopg.errors import QueryCanceled
    import tenant_dialog_operator_timeline as timeline

    registry=OrderedDict()
    active=set()
    now=[100.0]
    monkeypatch.setattr(timeline,"_cooldown",registry)
    monkeypatch.setattr(timeline,"_active",active)
    monkeypatch.setattr(timeline,"_MAX_ACTIVE",2)
    monkeypatch.setattr(timeline,"_MAX_COOLDOWNS",3)
    monkeypatch.setattr(timeline,"monotonic",lambda:now[0])
    started={name:Event() for name in ("busy-timeout","busy-bug")}
    release=Event()
    calls=[]
    def fake_window(_conn,_cur,_tenant,_channel,conversation,**_window):
        calls.append(conversation)
        if conversation in started and not release.is_set():
            started[conversation].set()
            assert release.wait(3)
            if conversation=="busy-timeout":
                raise QueryCanceled("test-only timeout")
            raise ValueError("test-only unexpected error")
        if conversation.startswith("noisy-"):
            raise QueryCanceled("test-only timeout")
        return {"operator_events":[]}

    monkeypatch.setattr(timeline,"_read_window",fake_window)
    def read(route):
        return timeline.page(None,None,A,CA,route)
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs={name:workers.submit(read,name) for name in started}
        try:
            assert all(event.wait(3) for event in started.values())
            assert len(active)==2
            for name in ["busy-timeout","busy-bug","other-route","other-route","another-route"]:
                assert "operator_events_retry_after_ms" in read(name)
            assert set(calls)==set(started) and len(calls)==2
        finally:
            release.set()
        assert jobs["busy-timeout"].result(timeout=3)["operator_events_retry_after_ms"]>=12000
        with pytest.raises(ValueError,match="unexpected error"):
            jobs["busy-bug"].result(timeout=3)
    assert not active
    assert "operator_events_retry_after_ms" in read("busy-timeout")
    assert read("busy-bug")==read("other-route")=={"operator_events":[]}
    assert not active

    for i in range(6):
        assert "operator_events_retry_after_ms" in read(f"noisy-{i}")
        assert len(registry)<=3
    assert [key[-1] for key in registry]==["noisy-3","noisy-4","noisy-5"]
    now[0]+=13
    assert read("unrelated-route")=={"operator_events":[]}
    assert not active and not registry, "expired routes must be pruned without revisiting them"


def test_compact_case_wins_over_timeline_flags_and_never_enriches(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock,Mock
    import tenant_operator_api as api
    import tenant_message_delivery as delivery

    compact={"dialog":{"provider":"connect","lead_id":41,"channel_id":CA,"conversation_id":"client","support_case":None}}
    ordinary={"dialog":{"provider":"connect","lead_id":41,"channel_id":CA,"conversation_id":"client"},
              "messages":[],"next_before":None,"operator_events":[],"operator_events_revision":2}
    read=Mock(side_effect=lambda *args,**flags:compact if flags.get("support_case_only") else ordinary)
    enrich=AsyncMock(side_effect=lambda _profile,result:result)
    monkeypatch.setattr(api,"history",read)
    monkeypatch.setattr(delivery,"enrich_history",enrich)
    endpoint=api.handler(lambda _req:PROFILE,"https://timeline.test",Mock(),"history")
    query={"provider":"connect","lead_id":"41","channel_id":CA,"conversation_id":"client",
           "operator_events":"1","operator_events_revision":"2","expected_tenant_id":A,"expected_user_id":"11"}
    def get(**fields):
        return asyncio.run(endpoint(SimpleNamespace(method='GET',query={**query,**fields},headers={})))

    case=get(support_case_only="1",before="ignored",anchor="ignored")
    assert case.status==200 and json.loads(case.body)["result"]==compact
    read.assert_called_once_with(PROFILE,"connect","41",CA,"client",None,None,support_case_only=True)
    enrich.assert_not_called()
    read.reset_mock()

    page=get()
    assert page.status==200 and json.loads(page.body)["result"]==ordinary
    read.assert_called_once_with(PROFILE,"connect","41",CA,"client",None,None,
                                 include_operator_events=True,operator_events_revision="2")
    enrich.assert_awaited_once_with(PROFILE,ordinary)
    read.reset_mock()

    denied=get(expected_user_id="33")
    assert denied.status==409
    read.assert_not_called()
