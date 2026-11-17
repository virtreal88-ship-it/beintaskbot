"""Independent native PostgreSQL connections; disposable localhost database only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, PROFILE
from test_call_recordings import pilot
from test_browser_calls import gateway, command, snapshot, calls, TenantLinearError
from test_call_sources import managed, sources, provider, CONVERSATION, A, CA


def test_simultaneous_claims_and_cancelled_start(gateway,monkeypatch):
    db,key,cfg=gateway
    monkeypatch.setattr(calls,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    def race(commands):
        gate=Barrier(len(commands))
        def act(cmd):
            gate.wait(10)
            try:return calls.action(PROFILE,cmd)
            except TenantLinearError as error:
                assert error.status==409
                return None
        with ThreadPoolExecutor(max_workers=len(commands)) as pool:return list(pool.map(act,commands))
    cmd=command();values=race([cmd,cmd]);assert values[0]['id']==values[1]['id']
    assert len(db.execute('SELECT id FROM saas_browser_calls'))==1;db.commit()
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    calls.sync(cfg,snapshot(3,results=[{'id':work['id'],'ok':False,'not_started':True,'error_code':'busy'}]))
    cmd=command();race([cmd,{**cmd,'op':'end'}])
    # Either cancellation wins before insertion, or the sole row is ending.
    rows=db.execute('SELECT state FROM saas_browser_calls WHERE client_request=%s::uuid',(cmd['request_id'],));db.commit()
    assert not rows or rows==[{'state':'ending'}]
    try:calls.action(PROFILE,cmd)
    except TenantLinearError:pass
    else:raise AssertionError('Cancelled request restarted')


def test_two_employees_cannot_both_accept_new_incoming_after_close(managed,monkeypatch):
    import uuid
    import tenant_platform
    import tenant_dialog_lifecycle as lifecycle
    from tenant_dialog_rollout import transition
    from test_tenant_operator_workspace import employee
    db,key,cfg=managed;person=employee(db,['dialogs'])
    with db.cursor() as cur:
        lifecycle.migrate(cur);transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    db.execute("INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id,revision,closed_at) VALUES(%s,%s,%s,%s,'closed',11,1,now()-interval '1 minute')",(A,CA,CONVERSATION,str(uuid.uuid4())));db.commit()
    connect=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(calls,'_connect',connect);monkeypatch.setattr(sources,'_connect',connect);monkeypatch.setattr(tenant_platform,'_connect',connect)
    calls.sync(cfg,snapshot(2,active=[provider(peer=CONVERSATION)]))
    incoming=calls.status(PROFILE)['calls'][0]
    gate=Barrier(2)
    def accept(profile):
        gate.wait(10)
        try:return calls.action(profile,command('accept',call_id=incoming['id'],media='websocket'))
        except TenantLinearError as error:
            assert error.status in (404,409)
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(accept,[PROFILE,person]))
    assert len([r for r in results if r])==1
    row=db.execute('SELECT actor_id,state FROM saas_browser_calls WHERE id=%s',(incoming['id'],))[0]
    workflow=db.execute('SELECT operator_id,state FROM saas_dialog_workflows')[0]
    assert row['state']=='starting' and workflow=={'operator_id':row['actor_id'],'state':'accepted'}
    assert len(db.execute("SELECT id FROM saas_browser_call_commands WHERE op='accept'"))==1
    assert len(db.execute('SELECT id FROM saas_dialog_episodes'))==1;db.commit()
