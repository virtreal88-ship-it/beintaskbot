"""Independent native PostgreSQL connections; disposable localhost database only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, PROFILE
from test_call_recordings import pilot, test_duration_constraint_upgrade_is_idempotent
from test_browser_calls import gateway, command, snapshot, calls, TenantLinearError
from test_call_sources import managed, sources, provider, CONVERSATION, A, CA


@pytest.mark.parametrize('competitor', ['audio', 'sync'])
def test_heartbeat_cannot_deadlock_with_call_row_then_dialog_authorization(managed,monkeypatch,competitor):
    """Real row/advisory locks reproduce the production heartbeat inversion.

    Park audio/sync after it has locked the call row, before dialog authority.
    An unfenced heartbeat then owns the dialog and waits on that row: PostgreSQL
    detects a cycle. The gateway fence keeps it outside this lock sequence.
    """
    import uuid
    import tenant_platform
    import tenant_dialog_lifecycle as lifecycle
    from tenant_dialog_rollout import transition
    db,key,cfg=managed
    cmd=command(media='websocket');started=calls.action(PROFILE,cmd)
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    active=provider('connected',CONVERSATION,'outbound','lock-order-provider')
    calls.sync(cfg,snapshot(3,active=[active],results=[{'id':work['id'],'ok':True,'provider_id':active['provider_id']}]))
    with db.cursor() as cur:
        lifecycle.migrate(cur);transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    db.execute("INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id,revision) VALUES(%s,%s,%s,%s,'accepted',11,1)",(A,CA,CONVERSATION,str(uuid.uuid4())));db.commit()
    def connect():
        return psycopg.connect(db.dsn,row_factory=dict_row,options='-c statement_timeout=8000')
    for module in (calls,sources,tenant_platform):monkeypatch.setattr(module,'_connect',connect)
    # Config discovery is independent of the transaction under examination.
    monkeypatch.setattr(calls,'configurations',lambda *a,**k:[cfg])
    row_locked=Event();resume=Event();heartbeat_authorized=Event()
    original=calls.authorize
    def authority(*args,**kwargs):
        if current_thread().name.startswith('competitor'):
            row_locked.set();assert resume.wait(5)
        result=original(*args,**kwargs)
        if current_thread().name.startswith('heartbeat'):heartbeat_authorized.set()
        return result
    monkeypatch.setattr(calls,'authorize',authority)
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='competitor') as other, ThreadPoolExecutor(max_workers=1,thread_name_prefix='heartbeat') as heart:
        future=other.submit(sources.audio_authority,PROFILE,started['id'],cmd['request_id']) if competitor=='audio' else other.submit(calls.sync,cfg,snapshot(4,active=[active]))
        try:
            assert row_locked.wait(5)
            beat=heart.submit(calls.status,PROFILE,cmd['request_id'])
            # Before the fix the heartbeat enters authorize while the other
            # transaction owns the call row. With the fix it waits at the fence.
            entered=heartbeat_authorized.wait(.5)
        finally:resume.set()
        result=future.result(timeout=10);status=beat.result(timeout=10)
    assert not entered
    assert status['calls'][0]['state']=='connected'
    if competitor=='audio':assert result[1:]==(active['provider_id'],started['id'])
    assert db.execute('SELECT count(*) AS n FROM saas_browser_calls')[0]['n']==1;db.commit()


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


def test_unanswered_native_inbound_call_is_persisted_as_idempotent_missed_dialog_event(managed):
    db,key,cfg=managed
    calls.sync(cfg,snapshot(2,active=[provider()]))
    incoming=calls.status(PROFILE)['calls'][0]
    assert incoming['state']=='incoming'

    calls.sync(cfg,snapshot(3,ended=[provider('ended')]))
    rows=db.execute('''SELECT id,direction,channel,message_type,raw FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND external_id=%s''',(A,'browser-call-missed:'+incoming['id']))
    assert rows==[{'id':uuid.uuid5(uuid.NAMESPACE_URL,'beintaskbot:browser-call-missed:'+incoming['id']),
      'direction':'system','channel':'connect:'+CA,'message_type':'call_missed',
      'raw':{'isVideo':False,'call_source':'whatsapp_calls'}}]
    db.commit()

    # Replayed snapshots may repeat the same ended call; timeline identity is stable.
    calls.sync(cfg,snapshot(4,ended=[provider('ended')]))
    assert db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s",
      (A,'browser-call-missed:'+incoming['id']))==[{'n':1}]
    db.commit()


def test_claimed_inbound_call_does_not_create_missed_dialog_event(managed):
    db,key,cfg=managed
    calls.sync(cfg,snapshot(2,active=[provider()]))
    incoming=calls.status(PROFILE)['calls'][0]
    reject=command('reject',call_id=incoming['id'],channel_id=incoming['channel_id'],
      conversation_id=incoming['conversation_id'],lead_id=incoming['lead_id'])
    calls.action(PROFILE,reject)
    calls.sync(cfg,snapshot(3,ended=[provider('ended')]))
    assert db.execute("SELECT id FROM saas_crm_messages WHERE message_type='call_missed'")==[]
    db.commit()
