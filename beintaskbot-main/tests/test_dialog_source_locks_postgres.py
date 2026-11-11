"""Two real localhost connections; simulated callbacks, never a real send."""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,CA
from test_tenant_operator_workspace import operator_db
from test_dialog_source_scopes import scoped,grants,D1,D2
from tenant_linear_policy import TenantLinearError
import tenant_chat_send_store as sender


def factory(db):
    return lambda:psycopg.connect(db.dsn,row_factory=dict_row,
        options='-c lock_timeout=4000 -c statement_timeout=6000')


def policy_write(conn,view='none',update='none'):
    # Exact writer order, including first members-policy row insertion.
    conn.execute('SELECT id FROM saas_tenants WHERE id=%s FOR UPDATE',(A,))
    conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,json.dumps({'33':{'access_scopes':{'dialogs':{'view':view,'update':update}}}})))


def waiting(db,pid):
    deadline=time.monotonic()+3
    with factory(db)() as conn:
        while time.monotonic()<deadline:
            row=conn.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s',(pid,)).fetchone()
            if row and row['wait_event_type']=='Lock':return
            time.sleep(.02)
    raise AssertionError('Competing transaction did not wait for the grant lock')


def reserved(db,person):
    item=sender.reserve(person,{'lead_id':41,'request_id':str(uuid.uuid4()),'text':'Synthetic only'})
    _,credentials=sender.mark_sending(item,{'origin':'connect'})
    return item,credentials


def test_first_activation_committed_before_dispatch_blocks_provider(scoped,monkeypatch):
    db,person=scoped
    item,credentials=reserved(db,person)
    connect=factory(db);pids=[]
    def send_connect():
        conn=connect();pids.append(conn.info.backend_pid);return conn
    monkeypatch.setattr(sender,'_connect',send_connect)
    callback=Mock()
    with connect() as writer:
        writer.execute('SELECT id FROM saas_tenants WHERE id=%s FOR UPDATE',(A,))
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(sender.dispatch_guarded,item,credentials,callback)
            deadline=time.monotonic()+2
            while not pids and time.monotonic()<deadline:time.sleep(.01)
            waiting(db,pids[0]);callback.assert_not_called()
            policy_write(writer);writer.commit()
            with pytest.raises(TenantLinearError):future.result(timeout=4)
    callback.assert_not_called()


@pytest.mark.parametrize('mutation',['scope','department','binding','inactive'])
def test_provider_boundary_serializes_revocation_and_releases_on_timeout(scoped,monkeypatch,mutation):
    db,person=scoped;grants(db,33,'department','department')
    item,credentials=reserved(db,person);connect=factory(db)
    monkeypatch.setattr(sender,'_connect',connect)
    entered=threading.Event();release=threading.Event();calls=[];pids=[]
    def callback(*_):
        calls.append(1);entered.set()
        assert release.wait(3)
        raise TimeoutError('Synthetic bounded provider timeout')
    def revoke():
        with connect() as conn:
            pids.append(conn.info.backend_pid)
            if mutation=='scope':policy_write(conn)
            elif mutation=='department':conn.execute('UPDATE saas_tenant_members SET department_id=%s WHERE tenant_id=%s AND telegram_id=34',(D2,A))
            elif mutation=='binding':conn.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s AND source_id=%s',(A,CA))
            else:conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=33',(A,))
    with ThreadPoolExecutor(2) as pool:
        send=pool.submit(sender.dispatch_guarded,item,credentials,callback)
        assert entered.wait(3)
        write=pool.submit(revoke)
        deadline=time.monotonic()+2
        while not pids and time.monotonic()<deadline:time.sleep(.01)
        try:
            waiting(db,pids[0]);assert not write.done()
        finally:release.set()
        with pytest.raises(TimeoutError):send.result(timeout=4)
        write.result(timeout=4)
    assert calls==[1]
    after=Mock()
    with pytest.raises(TenantLinearError):sender.dispatch_guarded(item,credentials,after)
    after.assert_not_called()


def notice_item(db,monkeypatch):
    import tenant_crm_notice_outbox as outbox
    import tenant_crm_notice_store as notices
    from tenant_crm_notice_schema import ensure_schema
    ensure_schema(db)
    raw={'channelId':CA,'conversationId':'client'}
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s AND external_id='m1'",(json.dumps(raw),A))
    event=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_notice_events(id,tenant_id,event,kind,source_key,version,payload) VALUES(%s,%s,'incoming_message','message','m1','synthetic','{\"connection\":\"\"}')",(event,A))
    db.execute("INSERT INTO saas_crm_notice_deliveries(tenant_id,event_id,recipient_id,channel,status) VALUES(%s,%s,33,'telegram','sending')",(A,event));db.commit()
    with db.cursor() as cur:row=notices.resource(cur,A,'message','m1')
    db.commit()
    monkeypatch.setattr(notices,'eligible',lambda *a,**k:True)
    monkeypatch.setattr(outbox,'_connect',factory(db))
    return outbox,dict(tenant_id=A,event_id=event,recipient_id=33,channel='telegram',endpoint_hash='',
      event='incoming_message',kind='message',source_key='m1',payload={'connection':''},resource=row,send=True)


def test_notice_claim_then_first_activation_never_sends(scoped,monkeypatch):
    db,person=scoped;outbox,item=notice_item(db,monkeypatch)
    with factory(db)() as conn:policy_write(conn)
    callback=Mock();assert outbox.dispatch_guarded(item,callback) is None
    callback.assert_not_called()
    assert db.execute('SELECT status FROM saas_crm_notice_deliveries WHERE event_id=%s',(item['event_id'],))[0]['status']=='skipped'


def test_notice_send_holds_first_activation_until_provider_returns(scoped,monkeypatch):
    db,person=scoped;outbox,item=notice_item(db,monkeypatch)
    entered=threading.Event();release=threading.Event();pids=[]
    def callback():entered.set();assert release.wait(3);return 'synthetic acceptance'
    def revoke():
        with factory(db)() as conn:pids.append(conn.info.backend_pid);policy_write(conn)
    with ThreadPoolExecutor(2) as pool:
        send=pool.submit(outbox.dispatch_guarded,item,callback);assert entered.wait(3)
        writer=pool.submit(revoke)
        deadline=time.monotonic()+2
        while not pids and time.monotonic()<deadline:time.sleep(.01)
        try:waiting(db,pids[0]);assert not writer.done()
        finally:release.set()
        assert send.result(timeout=4)=='synthetic acceptance';writer.result(timeout=4)
