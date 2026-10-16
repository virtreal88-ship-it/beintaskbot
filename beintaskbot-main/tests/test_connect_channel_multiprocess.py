"""Optional real PostgreSQL multi-process races, restricted to disposable localhost DBs.
BEINTASKBOT_TEST_POSTGRES_DSN=host=127.0.0.1 port=55439 user=postgres dbname=postgres
"""
import json
import multiprocessing
import os
import time
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_tenant_support_inbox import db, A, CA, CB, PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels
import tenant_ai_agents_schema as schema
import tenant_platform_schema as platform_schema
import tenant_chat_ai_schema as chat_schema

class RealDatabase:
    def __init__(self,dsn):self.dsn=dsn;self.conn=psycopg.connect(dsn,row_factory=dict_row)
    def execute(self,sql,params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql,params)
            return cur.fetchall() if cur.description else []
    def cursor(self):return self.conn.cursor()
    def commit(self):self.conn.commit()
    def rollback(self):self.conn.rollback()
    def close(self):pass
    def __enter__(self):return self
    def __exit__(self,kind,*_):self.rollback() if kind else self.commit()

@pytest.fixture(scope='module')
def database():
    dsn=os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not dsn:pytest.skip('Set a disposable localhost PostgreSQL DSN for multiprocess races')
    settings=conninfo_to_dict(dsn)
    if settings.get('host') not in {'127.0.0.1','localhost','::1'}:raise RuntimeError('Local test server only')
    name='test_channel_'+uuid.uuid4().hex
    with psycopg.connect(dsn,autocommit=True) as admin:admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
    db=RealDatabase(make_conninfo(dsn,dbname=name))
    platform_schema._schema_ready=False;schema._ready=False;chat_schema._ready=False
    schema.ensure_schema(db);chat_schema.ensure_schema(db)
    db.execute('CREATE TABLE test_external_sends(channel TEXT,run_id TEXT)');db.commit()
    yield db
    db.conn.close()
    with psycopg.connect(dsn,autocommit=True) as admin:admin.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(psycopg.sql.Identifier(name)))
    platform_schema._schema_ready=False;schema._ready=False;chat_schema._ready=False


def child(dsn,kind,payload,barrier,results,cipher_key=None):
    # Each process has its own connection pool and module state, like workers.
    import tenant_ai_agent_connect as connect
    import tenant_connect_transport as transport
    import tenant_ai_agent_actions as actions
    import tenant_ai_agent_run_store as runs
    import tenant_support_inbox_store as inbox
    from cryptography.fernet import Fernet
    schema._ready=True
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    for module in (connect,transport,actions,inbox,runs):module._connect=factory
    inbox.summarize_created=lambda *a:None
    os.environ['AI_AGENTS_ENABLED']='true'
    if cipher_key:transport._fernet=lambda:Fernet(cipher_key)
    def send(route,text,run):
        with factory() as conn:conn.execute('INSERT INTO test_external_sends VALUES(%s,%s)',(route['channel_id'],run))
        return str(uuid.uuid4())
    transport.send_ai_reply=send
    try:
        barrier.wait(20)
        if kind=='manual':value=connect.mark_operator_reply(**payload)
        elif kind=='send':value=transport.send_ai_reply_if_active(**payload)
        elif kind=='enqueue':value=connect.enqueue(**payload)
        elif kind=='handoff':value=actions.handoff(payload,'needs operator')
        elif kind=='claim':value=runs.claim_next()
        elif kind=='webhook':
            transport._verify_signature=lambda *a:True
            value=transport.ingest(json.dumps(payload).encode(),'mock-signature',payload)
        results.put(('ok',value))
    except Exception as error:results.put(('error',type(error).__name__,str(error)))


def race(db,operations,cipher_key=None):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(len(operations));results=ctx.Queue()
    processes=[ctx.Process(target=child,args=(db.dsn,kind,payload,barrier,results,cipher_key)) for kind,payload in operations]
    for process in processes:process.start()
    try:
        values=[results.get(timeout=35) for _ in processes]
        for process in processes:process.join(timeout=10);assert process.exitcode==0
        assert all(v[0]=='ok' for v in values),values
        return [v[1] for v in values]
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join(timeout=5)


def test_concurrent_duplicate_enqueue_preserves_other_channel(channels):
    f=channels;db=f['db']
    mid=str(uuid.uuid4())
    payload=dict(tenant=A,channel_id=CA,conversation_id='client',lead_id=41,message_id=mid,event_id=mid,text='one')
    other={**payload,'channel_id':CB,'lead_id':43,'message_id':str(uuid.uuid4()),'event_id':'other'}
    assert sorted(race(db,[('enqueue',payload),('enqueue',payload),('enqueue',other)]))==[0,1,1]
    assert len(db.execute("SELECT * FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND state='queued'",(A,)))==2
    db.commit()


def test_concurrent_handoffs_create_one_ticket_per_channel(channels):
    f=channels;db=f['db'];f['incoming'](CA);f['incoming'](CB)
    jobs=db.execute('SELECT * FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid',(A,));db.commit()
    # JSON-normalize UUID fields for process payloads, preserving other data.
    jobs=json.loads(json.dumps(jobs,default=str))
    result=race(db,[('handoff',jobs[0]),('handoff',jobs[0]),('handoff',jobs[1])])
    assert sorted(result)==[False,True,True]
    assert len(db.execute('SELECT * FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,)))==2;db.commit()


def prepare_send(f,channel):
    from cryptography.fernet import Fernet
    db=f['db'];cipher_key=Fernet.generate_key();cipher=Fernet(cipher_key)
    db.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='connect'",(cipher.encrypt(b'{"api_key":"local-mock"}'),A))
    row=db.execute("SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s",(A,channel))[0];db.commit()
    return dict(tenant=A,lead_id=41 if channel==CA else 43,channel_id=channel,conversation_id='client',text='mock',run_id=str(uuid.uuid4()),expected_revision=row['revision']),cipher_key


def test_racing_senders_cannot_send_twice(channels):
    f=channels;f['incoming'](CA);payload,key=prepare_send(f,CA)
    values=race(f['db'],[('send',payload),('send',payload)],key)
    assert sum(v is not None for v in values)==1
    assert len(f['db'].execute('SELECT * FROM test_external_sends WHERE run_id=%s',(payload['run_id'],)))==1;f['db'].commit()


def test_takeover_on_a_does_not_block_send_on_b(channels):
    f=channels;f['incoming'](CA);f['incoming'](CB);payload,key=prepare_send(f,CB)
    race(f['db'],[('send',payload),('manual',dict(tenant=A,lead_id=41,channel_id=CA,conversation_id='client'))],key)
    states={r['channel_id']:r['state'] for r in f['db'].execute('SELECT * FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid',(A,))};f['db'].commit()
    assert states=={CA:'human',CB:'active'}
    assert len(f['db'].execute('SELECT * FROM test_external_sends WHERE run_id=%s',(payload['run_id'],)))==1;f['db'].commit()


def test_committed_takeover_wins_against_waiting_sender(channels):
    import tenant_ai_agent_connect as connect
    f=channels;db=f['db'];f['incoming'](CA);payload,key=prepare_send(f,CA)
    # Keep the operator transaction uncommitted until the independent sender waits.
    with psycopg.connect(db.dsn,row_factory=dict_row) as held:
        connect.mark_operator_reply(A,41,CA,'client',connection=held)
        ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(1);results=ctx.Queue()
        process=ctx.Process(target=child,args=(db.dsn,'send',payload,barrier,results,key));process.start()
        try:
            deadline=time.monotonic()+15;blocked=False
            while time.monotonic()<deadline:
                with psycopg.connect(db.dsn,autocommit=True) as monitor:
                    blocked=monitor.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND pid<>pg_backend_pid())").fetchone()[0]
                if blocked:break
                time.sleep(.05)
            assert blocked,'Independent sender never reached the lock'
            held.commit()
            assert results.get(timeout=20)==('ok',None)
            process.join(timeout=10);assert process.exitcode==0
        finally:
            if process.is_alive():process.terminate();process.join(timeout=5)
    assert not db.execute('SELECT * FROM test_external_sends WHERE run_id=%s',(payload['run_id'],));db.commit()


def test_concurrent_claims_have_distinct_channel_leases(channels):
    f=channels;db=f['db'];f['incoming'](CA);f['incoming'](CB)
    values=race(db,[('claim',{}),('claim',{}),('claim',{})])
    claimed=[v for v in values if v]
    assert len(claimed)==2 and {j['channel_id'] for j in claimed}=={CA,CB}
    assert len({j['lease_token'] for j in claimed})==2


def test_concurrent_signed_webhook_retries_do_not_duplicate_messages(channels):
    f=channels;db=f['db'];f['incoming'](CA)
    _,key=prepare_send(f,CA)
    event={'id':str(uuid.uuid4()),'type':'message.inbound.received','data':{'id':str(uuid.uuid4()),'channelId':CA,'conversationId':'client','type':'text','text':'mock one'}}
    other={**event,'id':str(uuid.uuid4()),'data':{**event['data'],'id':str(uuid.uuid4()),'channelId':CB}}
    assert all(race(db,[('webhook',event),('webhook',event),('webhook',other)],key))
    assert db.execute("SELECT count(*) n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id IN (%s,%s)",(A,'connect:'+event['data']['id'],'connect:'+other['data']['id']))[0]['n']==2;db.commit()


@pytest.mark.parametrize("operation", ["webhook", "manual_outbound"])
def test_cold_start_never_holds_connect_ddl_while_waiting_for_ai_schema(database, monkeypatch, operation):
    """Reproduce the Python-lock/Postgres-lock cycle observed during rollout."""
    import threading
    import tenant_connect_transport as transport
    factory=lambda:psycopg.connect(database.dsn,row_factory=dict_row,
                                  options="-c statement_timeout=10000 -c lock_timeout=1500")
    monkeypatch.setattr(transport, '_connect', factory)
    original=schema.ensure_schema
    waiting=threading.Event()
    finished=threading.Event()
    errors=[]
    def observed(conn):
        waiting.set()
        return original(conn)
    monkeypatch.setattr(schema, 'ensure_schema', observed)
    monkeypatch.setattr(schema, '_ready', False)
    def invoke():
        try:
            if operation=='webhook':
                transport.ingest(b'{}','invalid',{'id':'startup-lock-probe',
                    'type':'message.inbound.received','data':{'channelId':CA,'conversationId':'startup-probe'}})
            else:
                transport.record_outbound(A,999999,{'channel_id':CA,'conversation_id':'startup-probe'},
                                          'test only','startup-lock-probe')
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            finished.set()
    thread=threading.Thread(target=invoke,daemon=True)
    schema._lock.acquire()
    try:
        thread.start()
        assert waiting.wait(5), 'Request did not reach AI schema initialization'
        # The startup thread owns the Python AI schema lock and next needs
        # Connect DDL. The concurrent request must not already own that DDL.
        with factory() as startup:
            transport._schema(startup)
    finally:
        schema._lock.release()
        thread.join(15)
    assert finished.is_set(), 'Cold-start request deadlocked'
    assert errors, 'Synthetic request must be rejected without storing customer data'
