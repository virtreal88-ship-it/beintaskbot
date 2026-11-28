"""Independent-connection inbox races on disposable localhost PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db
from test_call_recordings import pilot
from test_browser_calls import gateway
from test_call_sources import managed
from test_whatsapp_native_inbox import capture, delivery, store
import tenant_whatsapp_native_inbox as inbox


def test_browser_message_revision_is_fenced_by_tenant_and_active_source(db,monkeypatch):
    import tenant_dialog_message_revision as updates
    import tenant_operator_dialogs as dialogs
    def connect():
        return psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(updates,'_connect',connect)
    monkeypatch.setattr(dialogs,'_connect',connect)
    first=updates.read_revision(PROFILE)
    assert dialogs.listing(PROFILE,include_message_revision=True)['message_revision']==first['revision']
    assert updates.read_revision(PROFILE,(CA,'client'))=={**first,'selected_latest':True}
    db.execute("UPDATE saas_connect_conversations SET updated_at=now()+interval '1 hour' WHERE tenant_id=%s",(B,))
    db.commit()
    assert updates.read_revision(PROFILE)==first
    assert updates.read_revision(PROFILE,(CB,'client'))=={**first,'selected_latest':False}
    db.execute("UPDATE saas_connect_conversations SET updated_at=now()+interval '1 minute' WHERE tenant_id=%s",(A,))
    db.commit()
    assert updates.read_revision(PROFILE)!=first
    db.execute("UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s AND connect_channel_id=%s",(A,CA))
    db.commit()
    assert updates.read_revision(PROFILE)=={'revision':'0'}


def test_two_workers_acknowledge_one_committed_event(capture,monkeypatch):
    db,_,cfg=capture
    import tenant_browser_calls as calls
    monkeypatch.setattr(calls,'gateway_auth',lambda _:cfg)
    monkeypatch.setattr(inbox,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row,options='-c statement_timeout=8000'))
    gate=Barrier(2)
    def receive():
        gate.wait(5)
        return store(delivery())
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures=[workers.submit(receive) for _ in range(2)]
        receipts=[f.result(timeout=12) for f in futures]
    assert receipts[0]==receipts[1]
    assert len(db.execute('SELECT event_id FROM saas_whatsapp_native_inbox'))==1
    assert db.execute('SELECT stored_sequence FROM saas_whatsapp_capture_generations')[0]['stored_sequence']==2
    db.commit()


def test_conflicting_concurrent_payloads_have_one_winner(capture,monkeypatch):
    from tenant_linear_policy import TenantLinearError
    db,_,cfg=capture
    import tenant_browser_calls as calls
    monkeypatch.setattr(calls,'gateway_auth',lambda _:cfg)
    monkeypatch.setattr(inbox,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row,options='-c statement_timeout=8000'))
    gate=Barrier(2)
    def receive(text):
        value=delivery();value['event']['payload']['text']=text
        gate.wait(5)
        try:return store(value)['state']
        except TenantLinearError as error:return error.status
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures=[workers.submit(receive,text) for text in ('synthetic A','synthetic B')]
        results=[f.result(timeout=12) for f in futures]
    assert results.count('stored')==1 and results.count(409)==1
    assert len(db.execute('SELECT event_id FROM saas_whatsapp_native_inbox'))==1
    db.commit()
