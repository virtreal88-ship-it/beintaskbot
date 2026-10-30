"""Real PostgreSQL SQL/constraint tests, with the AI provider mocked (never production).
Setup: npm install --prefix .support-test-deps @electric-sql/pglite
Run: python -m pytest tests/test_tenant_support_inbox.py -q
"""
import json
from pathlib import Path
import re
import subprocess
import uuid
from unittest.mock import patch
import pytest
pg_errors=pytest.importorskip('psycopg.errors',reason='Real psycopg required for isolated SQL tests')
import tenant_ai_agents_schema as schema
import tenant_platform_schema as platform_schema
import tenant_chat_ai_schema as chat_schema
import tenant_ai_agent_actions as actions
import tenant_ai_agent_connect as connect_ai
import tenant_ai_agents_store as agents
import tenant_ai_agent_run_store as run_store
import tenant_support_inbox_store as store
import tenant_support_inbox_summary as summary
import tenant_ai_simulation_schema as simulation_schema
import tenant_ai_simulation_engine as simulation
import tenant_ai_group_coordination as group_coordination
import tenant_ai_dashboard as ai_dashboard
import tenant_ai_spend_budget as spend_budget
import tenant_internal_consultations as consultations
import tenant_member_store as member_store
import tenant_consultation_notification_worker as consultation_worker
import tenant_telegram_binding as telegram_binding
import tenant_notifications as tenant_notifications
from tenant_linear_policy import TenantLinearError

ROOT = Path(__file__).resolve().parents[1]
A,B,CA,CB,AGENT = [str(uuid.UUID(int=n)) for n in (101,102,201,202,301)]
PROFILE={'tenant_id':A,'telegram_id':11}


class Timestamp(str):
    def isoformat(self): return str(self)


class Database:
    def __init__(self):
        self.process=subprocess.Popen(['node',str(ROOT/'tests/support_pglite.cjs')],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        self.transaction=False
    def query(self,sql,params=()):
        counter=iter(range(1,1000))
        sql=re.sub(r'%s',lambda _:'$'+str(next(counter)),sql)
        def encode(v):
            if isinstance(v,(bytes,bytearray)): return {'__bytes':bytes(v).hex()}
            return str(v)
        self.process.stdin.write(json.dumps({'sql':sql,'params':params or []},default=encode)+'\n');self.process.stdin.flush()
        line=self.process.stdout.readline()
        if not line: raise RuntimeError(self.process.stderr.read())
        result=json.loads(line,object_hook=lambda v:bytes.fromhex(v['__bytes']) if '__bytes' in v else v)
        if 'error' in result:
            raise pg_errors.lookup(result['code'])(result['error']) if result.get('code') else RuntimeError(result['error'])
        return [{k: Timestamp(v) if k.endswith('_at') and isinstance(v,str) else v
                 for k,v in row.items()} for row in result['rows']]
    def execute(self,sql,params=()):
        if not self.transaction:
            self.query('BEGIN');self.transaction=True
        return self.query(sql,params)
    def __enter__(self): return self
    def __exit__(self,kind,*_):
        if kind:self.rollback()
        else:self.commit()
    def cursor(self):return Cursor(self)
    def commit(self):
        if self.transaction:self.query('COMMIT');self.transaction=False
    def rollback(self):
        if self.transaction:self.query('ROLLBACK');self.transaction=False
    def close(self):pass


class Cursor:
    def __init__(self,conn):self.connection=conn;self.rows=[]
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def execute(self,sql,params=None):self.rows=self.connection.execute(sql,params)
    def fetchall(self):rows,self.rows=self.rows,[];return rows
    def fetchone(self):return self.rows.pop(0) if self.rows else None


@pytest.fixture(scope='module')
def database():
    if not (ROOT/'.support-test-deps/node_modules/@electric-sql/pglite').exists():
        pytest.skip('Install the isolated PGlite test dependency; see module docstring')
    db=Database()
    platform_schema._schema_ready=False;schema._ready=False;chat_schema._ready=False
    schema.ensure_schema(db)
    chat_schema.ensure_schema(db)
    yield db
    db.process.stdin.close();db.process.wait(timeout=10)
    platform_schema._schema_ready=False;schema._ready=False;chat_schema._ready=False


@pytest.fixture
def db(database,monkeypatch):
    # This additive monetary ledger intentionally has no destructive production
    # cleanup path; isolate each PGlite fixture explicitly.
    database.execute('TRUNCATE saas_ai_spend_reservations');database.commit()
    database.execute('TRUNCATE saas_tenants CASCADE');database.commit()
    for module in (store,summary,actions,connect_ai,agents,run_store,simulation,ai_dashboard):monkeypatch.setattr(module,'_connect',lambda:database)
    for tenant,user,channel,lead in ((A,11,CA,41),(B,22,CB,42)):
        database.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Company','active',%s)",(tenant,tenant,user))
        database.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,%s,'owner','Owner')",(tenant,user))
        database.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'WhatsApp')",(tenant,channel))
        database.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'client',%s,'Client',now())",(tenant,channel,lead))
        database.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,%s,'m1',%s,'incoming','Internet is down')",(str(uuid.uuid4()),tenant,lead,'connect:'+channel))
    database.commit()
    yield database
    database.rollback()


def manual(**values):
    return store.create(PROFILE,{'request_id':str(uuid.uuid4()),'lead_id':41,**values})


def test_direct_login_employee_is_atomic_and_does_not_require_synthetic_email(db, monkeypatch):
    monkeypatch.setattr(member_store, '_connect', lambda: db)
    created=member_store.provision_employee_account(
        tenant_id=A,owner_id=11,first_name='Developer',last_name='One',login='developer.one',
        password='a-safe-initial-password',password_confirm='a-safe-initial-password',role='worker',
        permissions=['support'],email='',phone='')
    db.commit()
    row=db.execute('''SELECT u.email,u.force_password_change,m.permissions FROM saas_auth_users u
      JOIN saas_tenant_members m ON m.tenant_id=u.tenant_id AND m.telegram_id=u.actor_id
      WHERE u.actor_id=%s''',(created['actor_id'],))[0]
    assert row['email'] is None
    assert row['force_password_change'] is True
    assert row['permissions']==['support']
    with pytest.raises(Exception):
        member_store.provision_employee_account(
            tenant_id=A,owner_id=11,first_name='Developer',last_name='Two',login='developer.one',
            password='a-safe-initial-password',password_confirm='a-safe-initial-password',role='worker',
            permissions=[],email='',phone='')
    db.rollback()


def test_employee_rights_round_trip_preserves_hidden_and_legacy_grants(db, monkeypatch):
    """Real isolated PostgreSQL round-trip for the employee-editor payload."""
    monkeypatch.setattr(member_store, '_connect', lambda: db)
    db.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
      VALUES(%s::uuid,77,'operator','Legacy employee',%s::jsonb)''',
               (A,'["tasks","deals","customers","employees","finance","legacy:retired"]'))
    db.commit()
    member_store.upsert_member(tenant_id=A,owner_id=11,telegram_id=77,
        display_name='Legacy employee',role='operator',permissions=['tasks','deals','customers','dialogs','support'])
    db.commit()
    row=db.execute('SELECT role,permissions FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=77',(A,))[0]
    assert row['role']=='operator'
    assert set(row['permissions'])=={'tasks','deals','customers','dialogs','support','employees','finance','legacy:retired'}
    # The returned shape separates stored grants from policy-effective access.
    reopened=member_store.list_members(tenant_id=A,owner_id=11)
    target=next(item for item in reopened if item['telegram_id']==77)
    assert {'employees','finance','legacy:retired'} <= set(target['explicit_permissions'])
    assert target['legacy_permissions']==['legacy:retired']


def test_manual_ticket_survives_unavailable_ai(db):
    result=manual()
    assert result['ticket']['summary_state']=='unavailable'
    assert result['ticket']['description']=='Internet is down'
    assert len(result['messages'])==1
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_integrations WHERE provider='kommo'")[0]['n']==0
    db.commit()
    assert store.listing(PROFILE)['tickets'][0]['status']=='new'


def test_route_and_event_dedup_even_after_resolution(db):
    with patch.object(store,'summarize_created'):
        data={'request_id':str(uuid.uuid4()),'lead_id':41}
        first=store.create(PROFILE,data)['ticket']['id']
        assert store.create(PROFILE,data)['ticket']['id']==first
        assert manual()['ticket']['id']==first
        store.update(PROFILE,first,{'status':'resolved'})
        assert store.create(PROFILE,data)['ticket']['id']==first
        assert manual()['ticket']['id']!=first
        assert len(store.listing(PROFILE,status='all')['tickets'])==2


def test_cross_tenant_read_update_create_and_channel_are_denied(db):
    ticket=manual()['ticket']['id']
    foreign={'tenant_id':B,'telegram_id':22}
    with pytest.raises(TenantLinearError):store.detail(foreign,ticket)
    with pytest.raises(TenantLinearError):store.update(foreign,ticket,{'status':'resolved'})
    with pytest.raises(TenantLinearError):store.create(PROFILE,{'request_id':str(uuid.uuid4()),'lead_id':42})
    with pytest.raises(TenantLinearError):store.create(PROFILE,{'request_id':str(uuid.uuid4()),'channel_id':CB,'conversation_id':'client'})
    assert store.listing(foreign)['tickets']==[]
    assert store.detail(PROFILE,ticket)['ticket']['status']=='new'


def test_assignment_and_status_filters(db):
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,33,'admin','Support')",(A,))
    db.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members','{\"33\":{\"ai_agents_manage\":true}}'::jsonb)",(A,));db.commit()
    ticket=manual()['ticket']['id']
    store.update(PROFILE,ticket,{'status':'in_progress','assigned_to':33})
    assert store.listing(PROFILE,'in_progress','33')['tickets'][0]['id']==ticket
    assert store.listing(PROFILE,assignee='unassigned')['tickets']==[]
    with pytest.raises(TenantLinearError):store.update(PROFILE,ticket,{'assigned_to':22})
    store.update(PROFILE,ticket,{'status':'resolved'})
    assert store.listing(PROFILE)['tickets']==[]
    assert store.detail(PROFILE,ticket)['ticket']['resolved_at']


@pytest.mark.parametrize('case',['archived','suspended','inactive','worker','admin_without_grant'])
def test_live_access_checks(db,case):
    ticket=manual()['ticket']['id']
    if case in {'archived','suspended'}:db.execute('UPDATE saas_tenants SET status=%s WHERE id=%s::uuid',(case,A))
    elif case=='inactive':db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid',(A,))
    else:db.execute('UPDATE saas_tenant_members SET role=%s WHERE tenant_id=%s::uuid',('worker' if case=='worker' else 'admin',A))
    db.commit()
    for operation in (lambda:store.listing(PROFILE),lambda:store.detail(PROFILE,ticket),lambda:manual(),lambda:store.update(PROFILE,ticket,{'status':'resolved'})):
        with pytest.raises(TenantLinearError):operation()
    assert db.execute('SELECT status FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,))[0]['status']=='new'


def seed_job(db):
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled) VALUES(%s::uuid,%s::uuid,'Night support',11,TRUE)",(A,AGENT))
    db.execute("INSERT INTO saas_ai_agent_settings(tenant_id,enabled) VALUES(%s::uuid,TRUE)",(A,))
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider) VALUES(%s::uuid,'evt','connect')",(A,))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,agent_id,state) VALUES(%s::uuid,'connect','client',%s,%s::uuid,'active')",(A,CA,AGENT))
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,chat_id,channel_id,lead_id,event_fingerprint,talk_id,due_at) VALUES(%s::uuid,%s::uuid,'connect','client','client',%s,41,'evt','',now())",(A,AGENT,CA))
    db.commit()
    return {'tenant_id':A,'provider':'connect','agent_id':AGENT,'conversation_id':'client','chat_id':'client','channel_id':CA,'lead_id':41,'event_fingerprint':'evt','lease_token':str(uuid.uuid4()),'revision':1}


def test_active_manual_job_is_fenced_before_ai_reservation(db):
    job=seed_job(db)
    db.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    db.execute("UPDATE saas_ai_agent_jobs SET state='processing',lease_token=%s WHERE tenant_id=%s::uuid AND agent_id=%s::uuid",(job['lease_token'],A,AGENT))
    db.commit()
    config,reason=run_store.load_agent_config(job,with_block_reason=True)
    assert config is None
    assert reason=='manual_mode_requires_operator_request'


def test_handoff_creates_one_waiting_ticket_and_event_receipt(db):
    job=seed_job(db)
    with patch.object(store,'summarize_created'):
        assert actions.handoff(job,'Cannot fix internet')
        assert not actions.handoff(job,'Cannot fix internet')
    tickets=store.listing(PROFILE)['tickets']
    assert len(tickets)==1 and tickets[0]['status']=='waiting'
    assert tickets[0]['source']=='ai_handoff'
    assert tickets[0]['reason']=='Cannot fix internet'
    store.update(PROFILE,tickets[0]['id'],{'status':'resolved'})
    # Re-delivery of the exact event cannot reopen a resolved request.
    with db.cursor() as cur:
        receipt=store.handoff_ticket(cur,job,'Cannot fix internet')
    db.commit()
    assert receipt['id']==tickets[0]['id'] and not receipt['created']
    assert store.listing(PROFILE)['tickets']==[]


def test_reply_then_handoff_creates_ticket(db):
    job=seed_job(db)
    with patch.object(store,'summarize_created'):
        assert connect_ai.finish_sent_after_reply_handoff(job,{'run_id':str(uuid.uuid4())},'msg-sent')
    assert store.listing(PROFILE)['tickets'][0]['reason']=='trigger_reply_sent_then_handoff'


def test_manual_handoff_creates_ticket_and_pause_does_not(db):
    seed_job(db)
    agents.set_conversation_state(PROFILE,'client','paused',provider='connect',channel_id=CA)
    assert store.listing(PROFILE)['tickets']==[]
    agents.set_conversation_state(PROFILE,'client','human','Manual handoff','connect',CA)
    assert store.listing(PROFILE)['tickets'][0]['status']=='waiting'


def test_viewing_and_status_changes_do_not_resume_ai(db):
    seed_job(db)
    actions.handoff({'tenant_id':A,'chat_id':'client','provider':'connect','event_fingerprint':'evt','channel_id':CA,'lead_id':41},'Needs support')
    ticket=store.listing(PROFILE)['tickets'][0]['id']
    before=db.execute('SELECT * FROM saas_ai_conversation_state')[0];db.commit()
    store.detail(PROFILE,ticket);store.listing(PROFILE);store.update(PROFILE,ticket,{'status':'resolved'})
    after=db.execute('SELECT * FROM saas_ai_conversation_state')[0];db.commit()
    assert before==after


def test_history_pagination_stays_with_exact_channel(db):
    for i in range(110):
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming',%s,'2026-10-16 00:00:00Z')",(str(uuid.uuid4()),A,'message'+str(i),'connect:'+CA,str(i)))
    db.commit();first=manual();ticket=first['ticket']['id']
    ids=[];page=first
    while True:
        ids.extend(m['id'] for m in page['messages'])
        if not page['next_before']:break
        page=store.detail(PROFILE,ticket,page['next_before'])
    assert len(ids)==111 and len(set(ids))==111


def config_company(db,limit=50):
    from cryptography.fernet import Fernet
    cipher=Fernet(Fernet.generate_key())
    secret=cipher.encrypt(json.dumps({'tenant_id':A,'provider':'chat_ai','api_key':'test-key'}).encode())
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata) VALUES(%s::uuid,'chat_ai','connected',%s,%s::jsonb)",(A,secret,json.dumps({'enabled':True,'consent':True,'model':'test-model','daily_limit':limit})))
    db.commit()
    return cipher


def test_ai_summary_fields_and_shared_budget_receipt(db):
    cipher=config_company(db)
    output={'description':'Нет интернета','summary':'Клиент без связи','suggestions':'Перезагрузка','next_steps':'Проверить линию'}
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',return_value=(json.dumps(output),{'total_tokens':12})) as provider:
        result=manual()
    assert result['ticket']['summary_state']=='completed'
    for key,value in output.items():assert result['ticket'][key]==value
    assert provider.call_args.args[2]=='summary'
    assert db.execute('SELECT reserved_calls,status FROM saas_chat_ai_runs')[0]=={'reserved_calls':1,'status':'completed'}
    db.commit()


def test_ai_failure_keeps_ticket_and_consumes_reservation(db):
    cipher=config_company(db)
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',side_effect=RuntimeError('unavailable')):
        result=manual()
    assert result['ticket']['summary_state']=='unavailable'
    assert result['ticket']['description']=='Internet is down'
    assert db.execute('SELECT reserved_calls,status FROM saas_chat_ai_runs')[0]=={'reserved_calls':1,'status':'unknown'}
    db.commit()


def test_daily_budget_exhaustion_never_calls_provider(db):
    cipher=config_company(db,1)
    db.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls) VALUES(%s::uuid,%s::uuid,11,41,'x','v','unknown',1)",(A,str(uuid.uuid4())));db.commit()
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate') as provider:
        result=manual()
    provider.assert_not_called()
    assert result['ticket']['summary_state']=='unavailable'


def test_platform_summary_uses_existing_agent_run_and_token_accounting(db):
    seed_job(db)
    output={k:'Test' for k in ('description','summary','suggestions','next_steps')}
    with patch.object(summary,'choose_api_credential',return_value=('platform-test-key','platform')),patch.object(summary,'platform_model_allowed',return_value=True),patch.object(summary,'generate',return_value=(json.dumps(output),{'input_tokens':10,'output_tokens':5,'total_tokens':15})):
        result=manual()
    assert result['ticket']['summary_state']=='completed'
    assert db.execute('SELECT total_tokens FROM saas_ai_agent_token_usage')[0]['total_tokens']==15
    assert db.execute('SELECT state FROM saas_ai_agent_runs')[0]['state']=='completed';db.commit()


def test_database_constraints_block_cross_tenant_route_and_duplicate_open(db):
    manual()
    with pytest.raises(pg_errors.ForeignKeyViolation):
        with db:
            db.execute("INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source) VALUES(%s::uuid,%s::uuid,%s::uuid,'client','manual')",(A,str(uuid.uuid4()),CB))
    with pytest.raises(pg_errors.UniqueViolation):
        with db:
            db.execute("INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source) VALUES(%s::uuid,%s::uuid,%s::uuid,'client','manual')",(A,str(uuid.uuid4()),CA))


def test_ordinary_inbound_does_not_create_ticket(db,monkeypatch):
    seed_job(db)
    db.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_ai_agents SET mode='automatic' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA}]),A));db.commit()
    monkeypatch.setattr(connect_ai,'runtime_flag',lambda name:True)
    with patch.object(connect_ai,'allowed',return_value=True):
        assert connect_ai.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,message_id='ordinary',event_id='ordinary',text='Hello')==1
    assert store.listing(PROFILE)['tickets']==[]


def test_failed_route_check_rolls_back_handoff_and_creates_no_ticket(db):
    job=seed_job(db);job['channel_id']=CB
    with pytest.raises(TenantLinearError):actions.handoff(job,'Invalid channel')
    assert db.execute('SELECT state FROM saas_ai_conversation_state')[0]['state']=='active';db.commit()
    assert store.listing(PROFILE)['tickets']==[]


def test_reopening_conflicting_ticket_returns_409(db):
    first=manual()['ticket']['id'];store.update(PROFILE,first,{'status':'resolved'})
    manual()
    with pytest.raises(TenantLinearError) as caught:store.update(PROFILE,first,{'status':'waiting'})
    assert caught.value.status==409


def test_complete_ticket_is_idempotent_and_keeps_unconfirmed_outcome(db):
    request_id=str(uuid.uuid4())
    payload={'request_id':request_id,'lead_id':41,'channel_id':CA,'conversation_id':'client',
             'outcome':'recommendations_unknown','note':'Customer has not confirmed yet.','propose_knowledge':True}
    first=store.complete(PROFILE,payload)['ticket']
    second=store.complete(PROFILE,payload)['ticket']
    assert first['id']==second['id']
    assert first['status']=='resolved'
    assert first['completion_outcome']=='recommendations_unknown'
    assert first['completion_note']=='Customer has not confirmed yet.'
    assert first['knowledge_candidate_requested'] is True
    assert db.execute('SELECT count(*) AS n FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,))[0]['n']==1
    db.commit()


def test_complete_ticket_rejects_cross_tenant_and_idempotency_reuse(db):
    payload={'request_id':str(uuid.uuid4()),'lead_id':41,'channel_id':CA,'conversation_id':'client',
             'outcome':'resolved_confirmed','note':'','propose_knowledge':False}
    ticket=store.complete(PROFILE,payload)['ticket']
    with pytest.raises(TenantLinearError):store.complete({'tenant_id':B,'telegram_id':22},payload)
    altered={**payload,'outcome':'unresolved'}
    with pytest.raises(TenantLinearError):store.complete(PROFILE,altered)
    assert store.detail(PROFILE,ticket['id'])['ticket']['completion_outcome']=='resolved_confirmed'
    db.commit()


def test_ai_does_not_overwrite_employee_edits(db):
    cipher=config_company(db)
    output={k:'AI' for k in ('description','summary','suggestions','next_steps')}
    with patch.object(store,'summarize_created'):ticket=manual()['ticket']['id']
    def provider(*args,**kwargs):
        store.update(PROFILE,ticket,{'description':'Employee description','next_steps':'Check router'})
        return json.dumps(output),{}
    with patch.object(summary,'_fernet',return_value=cipher),patch.object(summary,'generate',side_effect=provider):
        summary.regenerate(PROFILE,ticket)
    result=store.detail(PROFILE,ticket)['ticket']
    assert result['description']=='Employee description' and result['next_steps']=='Check router'
    assert result['summary']=='AI'


def test_summary_platform_cap_never_calls_provider(db):
    seed_job(db)
    with patch.object(store,'summarize_created'):ticket=manual()['ticket']['id']
    run=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test','completed')",(A,run,AGENT))
    db.execute("INSERT INTO saas_ai_agent_token_usage(tenant_id,id,run_id,agent_id,model,total_tokens) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'test',10)",(A,str(uuid.uuid4()),run,AGENT));db.commit()
    with patch.object(summary,'choose_api_credential',return_value=('platform-key','platform')),patch.object(summary,'platform_model_allowed',return_value=True),patch.object(summary,'platform_token_limit',return_value=10),patch.object(summary,'generate') as provider:
        summary.regenerate(PROFILE,ticket)
    provider.assert_not_called()
    assert store.detail(PROFILE,ticket)['ticket']['summary_state']=='unavailable'


def test_http_origin_identity_and_tenant_scope(db):
    import asyncio
    import logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from tenant_support_inbox_api import handler
    async def scenario():
        current=dict(PROFILE)
        app=web.Application()
        member=lambda _request:current
        app.router.add_route('GET','/tickets',handler(member,'https://crm.test',logging.getLogger(__name__),'tickets'))
        app.router.add_route('POST','/tickets',handler(member,'https://crm.test',logging.getLogger(__name__),'tickets'))
        app.router.add_route('GET','/tickets/{ticket_id}',handler(member,'https://crm.test',logging.getLogger(__name__),'ticket'))
        app.router.add_route('PATCH','/tickets/{ticket_id}',handler(member,'https://crm.test',logging.getLogger(__name__),'ticket'))
        async with TestClient(TestServer(app)) as client:
            data={'request_id':str(uuid.uuid4()),'lead_id':41,'expected_tenant_id':A,'expected_user_id':11}
            assert (await client.post('/tickets',json=data)).status==403
            assert (await client.post('/tickets',headers={'Origin':'https://evil.test'},json=data)).status==403
            assert (await client.post('/tickets',headers={'Origin':'https://crm.test'},json={**data,'expected_tenant_id':B})).status==409
            response=await client.post('/tickets',headers={'Origin':'https://crm.test'},json=data)
            assert response.status==200
            ticket=(await response.json())['result']['ticket']['id']
            response=await client.get('/tickets/'+ticket)
            assert response.status==200 and response.headers['Cache-Control']=='no-store'
            current.update(tenant_id=B,telegram_id=22)
            assert (await client.get('/tickets/'+ticket)).status==404
            assert (await client.patch('/tickets/'+ticket,headers={'Origin':'https://crm.test'},json={'expected_tenant_id':B,'expected_user_id':22,'status':'resolved'})).status==404
    asyncio.run(scenario())


def test_handoff_trigger_still_records_ticket_if_operator_won_state_race(db):
    job=seed_job(db)
    db.execute("UPDATE saas_ai_conversation_state SET state='human',reason='manager_replied'");db.commit()
    with patch.object(store,'summarize_created'):
        connect_ai.finish_sent_after_reply_handoff(job,{'run_id':str(uuid.uuid4())},'msg-sent')
    assert len(store.listing(PROFILE)['tickets'])==1
    assert db.execute('SELECT reason FROM saas_ai_conversation_state')[0]['reason']=='manager_replied';db.commit()


def test_last_message_time_includes_outbound_messages(db):
    ticket=manual()['ticket']['id']
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,'later-outgoing',%s,'outgoing','Checking','2099-01-01T00:00:00Z')",(str(uuid.uuid4()),A,'connect:'+CA));db.commit()
    assert store.listing(PROFILE)['tickets'][0]['last_message_at'].startswith('2099-01-01')


def seed_simulation(db, *, enabled=True, channel=CA, conversation='client', lead_id=41, group=False):
    simulation_schema._ready=False
    simulation_schema.ensure_schema(db)
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected') ON CONFLICT DO NOTHING",(A,))
    db.execute("INSERT INTO saas_ai_agent_settings(tenant_id,enabled,emergency_stop,auto_send_enabled) VALUES(%s::uuid,TRUE,FALSE,FALSE) ON CONFLICT(tenant_id) DO UPDATE SET enabled=TRUE,emergency_stop=FALSE,auto_send_enabled=FALSE",(A,))
    db.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,created_by,enabled,mode,role,scopes,expert_policy,model,instructions) VALUES(%s::uuid,%s::uuid,'Simulation Primary',11,TRUE,'automatic','primary',%s::jsonb,'{}'::jsonb,'test-model','Answer from evidence only')",(AGENT,A,json.dumps([{'provider':'connect','channel_id':channel,'allow_group_messages':group}])))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,%s,'Simulation client',now()) ON CONFLICT DO NOTHING",(A,channel,conversation,lead_id))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','Client','Simulation question','text') ON CONFLICT DO NOTHING",(str(uuid.uuid4()),A,lead_id,'connect:'+('group-message' if group else 'simulation-message'),'connect:'+channel))
    db.execute("INSERT INTO saas_ai_simulation_configs(tenant_id,enabled,agent_id,channel_ids,conversation_ids,starts_at,token_budget,message_limit,max_parallel,max_calls_per_event,max_output_tokens) VALUES(%s::uuid,%s,%s::uuid,%s::jsonb,'[]'::jsonb,now()-interval '1 minute',20000,10,1,3,128)",(A,enabled,AGENT,json.dumps([channel])))
    row=db.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s ORDER BY created_at DESC LIMIT 1",(A,lead_id,'connect:'+channel))[0]
    db.commit()
    return str(row['id'])


def queue_simulation(db, *, tenant=A, channel=CA, source, event='new-message',conversation='client',lead_id=41):
    with db.cursor() as cur:
        result=simulation.enqueue_inbound(cur,tenant_id=tenant,channel_id=channel,
            conversation_id=conversation,lead_id=lead_id,source_message_id=source,
            source_event_id=event,occurred_at=None)
    db.commit()
    return result


def seed_simulation_run(db, *, outcome='virtual_reply', events=None, refs=None):
    source=seed_simulation(db)
    queue_id=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_ai_simulation_queue(id,tenant_id,channel_id,conversation_id,lead_id,source_message_id,
        source_event_id,event_fingerprint,state,completed_at)
        VALUES(%s::uuid,%s::uuid,%s::uuid,'client',41,%s::uuid,'demo-event','demo-fingerprint','completed',now())''',
        (queue_id,A,CA,source))
    run_id=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_ai_simulation_runs( id,tenant_id,queue_id,channel_id,conversation_id,source_message_id,
        primary_agent_id,outcome,virtual_reply,events,knowledge_refs,total_tokens)
        VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client',%s::uuid,%s::uuid,%s,'Virtual draft',%s::jsonb,%s::jsonb,17)''',
        (run_id,A,queue_id,CA,source,AGENT,outcome,json.dumps(events or []),json.dumps(refs or [])))
    db.commit()
    return run_id,source,queue_id


def test_simulation_schema_defaults_off_and_is_tenant_scoped(db,monkeypatch):
    source=seed_simulation(db,enabled=False)
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name!='AI_SIMULATION_ENABLED')
    assert simulation.enqueue_inbound(db,tenant_id=A,channel_id=CA,conversation_id='client',lead_id=41,
        source_message_id=source,source_event_id='new-message',occurred_at=None) is False
    assert db.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue')[0]['n']==0
    assert simulation.list_runs(PROFILE)==[]


def test_simulation_public_config_is_json_serializable_with_psycopg_uuids():
    from tenant_ai_simulation_engine import _public_config
    config=_public_config({'tenant_id':uuid.UUID(A),'agent_id':uuid.UUID(AGENT),'channel_ids':[CA],
        'conversation_ids':[],'enabled':False,'emergency_stop':False,'state':'stopped','token_budget':0,
        'message_limit':0,'max_parallel':1,'max_calls_per_event':3,'max_output_tokens':800,
        'starts_at':None,'ends_at':None,'updated_at':None})
    encoded=json.dumps(config)
    assert str(uuid.UUID(AGENT)) in encoded and 'tenant_id' not in config


def test_simulation_public_runs_are_json_serializable_with_psycopg_uuids():
    from tenant_ai_simulation_engine import _public_run
    item=_public_run({'id':uuid.uuid4(),'queue_id':uuid.uuid4(),'channel_id':uuid.uuid4(),
        'conversation_id':'client@g.us','primary_agent_id':uuid.UUID(AGENT),
        'recheck_of_run_id':uuid.uuid4(),'created_at':Timestamp('2026-10-22T12:00:00Z'),
        'outcome':'virtual_reply','events':[]})
    assert json.loads(json.dumps(item))['conversation_id']=='client@g.us'


def test_simulation_inbound_queue_dedupes_and_tenant_fences(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source)
    assert not queue_simulation(db,source=source)
    assert not queue_simulation(db,tenant=B,source=source,event='foreign-tenant')
    assert db.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid',(A,))[0]['n']==1
    assert db.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid',(B,))[0]['n']==0


def test_simulation_admission_enforces_message_limit_before_queueing(db,monkeypatch):
    first=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    db.execute("UPDATE saas_ai_simulation_configs SET message_limit=1 WHERE tenant_id=%s::uuid",(A,));db.commit()
    assert queue_simulation(db,source=first,event='first')
    second=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type) VALUES(%s::uuid,%s::uuid,41,'connect:second-message',%s,'incoming','Client','Another question','text')",
               (second,A,'connect:'+CA));db.commit()
    assert not queue_simulation(db,source=second,event='second')
    assert db.execute("SELECT count(*) AS n FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid",(A,))[0]['n']==1


def test_simulation_claims_only_one_message_per_dialog_at_a_time(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source,event='first')
    second=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type) VALUES(%s::uuid,%s::uuid,41,'connect:second-message',%s,'incoming','Client','One more question','text')",
               (second,A,'connect:'+CA));db.commit()
    assert queue_simulation(db,source=second,event='second')
    first_claim=simulation.claim_next()
    assert first_claim
    assert simulation.claim_next() is None
    simulation._complete(first_claim,'skipped',stop_reason='test_complete')
    assert simulation.claim_next()


def test_simulation_recovers_only_work_interrupted_before_provider(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source)
    first=simulation.claim_next();assert first and first['attempts']==1
    db.execute("UPDATE saas_ai_simulation_queue SET lease_until=now()-interval '1 second' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,first['id']));db.commit()
    recovered=simulation.claim_next()
    assert recovered and recovered['id']==first['id'] and recovered['attempts']==2
    assert recovered['provider_started_at'] is None


def test_simulation_accepts_manual_primary_without_changing_real_mode(db,monkeypatch):
    source=seed_simulation(db)
    _enable_spend_budget(db, monkeypatch)
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION', json.dumps({'test-model': {'input': 1_000_000, 'output': 2_000_000}}))
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_a,**_k: [])
    def credentials(*_args):
        db.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));db.commit()
        return 'mock-key','test-model','company',{}
    monkeypatch.setattr(simulation,'_provider_credentials',credentials)
    monkeypatch.setattr('tenant_chat_ai_provider.generate',lambda *_a,**_k: ('Virtual draft',{'total_tokens':7}))
    job=simulation.claim_next()
    assert simulation.process(job)
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='virtual_reply' and run['sent'] is False
    assert db.execute("SELECT mode FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))[0]['mode']=='manual'
    assert db.execute("SELECT kind,state FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid",(A,))[0] == {'kind':'simulation_primary','state':'settled'}


def test_simulation_executor_records_virtual_reply_and_has_no_live_effects(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_args,**_kwargs: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: ('mock-key','test-model','company',{}))
    calls=[]
    def fake_generate(*args,**kwargs):
        calls.append((args,kwargs))
        return 'Please share the order number so I can check this.',{'input_tokens':25,'output_tokens':12,'total_tokens':37}
    monkeypatch.setattr('tenant_chat_ai_provider.generate',fake_generate)
    import tenant_connect_transport as transport
    monkeypatch.setattr(transport,'send_ai_reply_if_active',lambda *_a,**_k: pytest.fail('Simulation attempted an external send'))
    before={table:db.execute(f'SELECT count(*) AS n FROM {table}')[0]['n'] for table in
            ('saas_crm_messages','saas_crm_deals','saas_crm_tasks','saas_support_tickets',
             'saas_tenant_notifications','saas_ai_agent_jobs','saas_ai_conversation_state','saas_ai_group_coordination')}
    job=simulation.claim_next()
    assert job and job['tenant_id']==A
    assert simulation.process(job)
    run=simulation.list_runs(PROFILE)[0]
    assert len(calls)==1,json.dumps(run,default=str)
    assert run['outcome']=='virtual_reply' and run['sent'] is False
    assert run['virtual_reply'].startswith('Please share')
    snapshot=db.execute('SELECT context_snapshot FROM saas_ai_simulation_runs WHERE id=%s::uuid',(run['id'],))[0]['context_snapshot']
    assert snapshot[-1]['role']=='client' and snapshot[-1]['author_type']=='customer'
    state=db.execute("SELECT session_started_at,virtual_state FROM saas_ai_simulation_dialog_state WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id='client'",(A,CA))[0]
    assert state['virtual_state']['sent'] is False and state['virtual_state']['last_reply']==run['virtual_reply']
    assert db.execute("SELECT count(*) AS n FROM saas_ai_simulation_dialog_state WHERE tenant_id=%s::uuid",(B,))[0]['n']==0
    after={table:db.execute(f'SELECT count(*) AS n FROM {table}')[0]['n'] for table in before}
    assert after==before
    assert db.execute("SELECT sum(actual_tokens) AS n FROM saas_ai_simulation_usage WHERE tenant_id=%s::uuid",(A,))[0]['n']==37


def test_simulation_next_synthetic_turn_receives_prior_virtual_state(db,monkeypatch):
    """Only the preceding virtual result in this simulation session is reused."""
    source=seed_simulation(db)
    _enable_spend_budget(db,monkeypatch)
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION', json.dumps({'test-model': {'input': 1_000_000, 'output': 2_000_000}}))
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_args,**_kwargs: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: ('mock-key','test-model','company',{}))
    prior_states=[]
    def fake_generate(*_args,**kwargs):
        prior_states.append(kwargs['extra_context']['prior_simulation_state'])
        return f'Synthetic draft {len(prior_states)}',{'total_tokens':7}
    monkeypatch.setattr('tenant_chat_ai_provider.generate',fake_generate)

    assert queue_simulation(db,source=source,event='synthetic-first')
    assert simulation.process(simulation.claim_next())
    second=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type) VALUES(%s::uuid,%s::uuid,41,'connect:synthetic-second',%s,'incoming','Synthetic client','Synthetic follow-up','text')",
               (second,A,'connect:'+CA));db.commit()
    assert queue_simulation(db,source=second,event='synthetic-second')
    assert simulation.process(simulation.claim_next())

    assert prior_states[0]=={}
    assert prior_states[1]=={'outcome':'virtual_reply','last_reply':'Synthetic draft 1',
                             'stop_reason':'','sent':False}
    state=db.execute("SELECT virtual_state FROM saas_ai_simulation_dialog_state WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id='client'",(A,CA))[0]['virtual_state']
    assert state['source_message_id']==second and state['last_reply']=='Synthetic draft 2'
    assert not db.execute("SELECT 1 FROM saas_ai_simulation_dialog_state WHERE tenant_id=%s::uuid",(B,))


@pytest.mark.parametrize('action,expected_outcome,event_type',[
    ('stop','virtual_stop',None),('handoff','virtual_stop','virtual_handoff'),
    ('create_task','virtual_stop','virtual_employee_request'),('clarify','virtual_reply',None),
])
def test_simulation_virtualizes_no_answer_action_without_live_side_effects(db,monkeypatch,action,expected_outcome,event_type):
    source=seed_simulation(db)
    policy={'enabled':True,'action':action,'fallback_action':'handoff','max_clarifications':1,'stop_reply':''}
    db.execute("UPDATE saas_ai_agents SET response_policy=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
               (json.dumps(policy),A,AGENT));db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_args,**_kwargs: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: pytest.fail('no-answer stop must not make an AI call'))
    monkeypatch.setattr('tenant_chat_ai_provider.generate',lambda *_a,**_k: pytest.fail('no-answer stop must not call the provider'))
    before={table:db.execute(f'SELECT count(*) AS n FROM {table}')[0]['n'] for table in
            ('saas_crm_messages','saas_crm_deals','saas_crm_tasks','saas_support_tickets',
             'saas_tenant_notifications','saas_ai_agent_jobs','saas_ai_conversation_state','saas_ai_group_coordination')}
    assert simulation.process(simulation.claim_next())
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']==expected_outcome and run['virtual_stop_reason']==f'no_answer_{action}'
    assert {'type':'virtual_no_answer_action','action':action,'reason':'missing_knowledge','executed':False} in run['events']
    if event_type: assert any(event.get('type')==event_type and event.get('executed') is False for event in run['events'])
    after={table:db.execute(f'SELECT count(*) AS n FROM {table}')[0]['n'] for table in before}
    assert after==before


def test_simulation_calls_only_explicitly_allowed_expert_and_accounts_tokens(db,monkeypatch):
    source=seed_simulation(db)
    expert=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,created_by,enabled,mode,role,scopes,expert_policy,model,instructions) VALUES(%s::uuid,%s::uuid,'Internal Expert',11,TRUE,'manual','expert','[]'::jsonb,%s::jsonb,'expert-test-model','Internal analysis only')",
               (expert,A,json.dumps({'internal':True,'allowed_primary_ids':[AGENT],'tools':[]})))
    db.execute("UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
               (json.dumps({'expert_agent_id':expert}),A,AGENT));db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    db.execute("INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,condition,actions,enabled) VALUES(%s::uuid,%s::uuid,%s::uuid,'Ask expert','phrase',%s::jsonb,'[\"call_expert\"]'::jsonb,TRUE)",
               (A,str(uuid.uuid4()),AGENT,json.dumps({'phrase':'Simulation','expert_agent_ids':[expert]})));db.commit()
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_a,**_k: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: ('mock-key','test-model','company',{}))
    calls=[]
    def fake_generate(_key,_model,mode,*_args,**_kwargs):
        calls.append(mode)
        if mode=='expert':
            return json.dumps({'cause':'unknown','evidence':[],'confidence':'low','safe_actions':[],
                'additional_information':'Check the order status.','requires_human':False,'ticket_summary':'New customer inquiry.'}),{'total_tokens':19}
        return 'I can check the order status for you.',{'total_tokens':31}
    monkeypatch.setattr('tenant_chat_ai_provider.generate',fake_generate)
    assert queue_simulation(db,source=source)
    assert simulation.process(simulation.claim_next())
    run=simulation.list_runs(PROFILE)[0]
    assert calls==['expert','reply'],json.dumps(run,default=str)
    assert run['outcome']=='virtual_reply' and len(run['expert_results'])==1
    assert db.execute("SELECT sum(actual_tokens) AS n FROM saas_ai_simulation_usage WHERE tenant_id=%s::uuid",(A,))[0]['n']==50


def test_simulation_executor_blocks_ambiguous_primary_before_provider(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source)
    second=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,created_by,enabled,mode,role,scopes,expert_policy,model) VALUES(%s::uuid,%s::uuid,'Second Primary',11,TRUE,'automatic','primary',%s::jsonb,'{}'::jsonb,'test-model')",(second,A,json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':False}])))
    db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: pytest.fail('Ambiguous simulation reached credentials'))
    job=simulation.claim_next()
    assert simulation.process(job)
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='blocked',json.dumps(run,default=str)
    assert run['virtual_stop_reason']=='primary_not_unique_or_scope_mismatch'


def test_simulation_enable_requires_explicit_start_confirmation(db,monkeypatch):
    seed_simulation(db,enabled=False)
    # A Simulation run is virtual: the primary remains Manual for real
    # conversations and does not acquire an Active-mode send capability.
    db.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));db.commit()
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected') ON CONFLICT DO NOTHING",(A,));db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    base={'enabled':True,'agent_id':AGENT,'channel_ids':[CA],'conversation_ids':[],
          'token_budget':10000,'message_limit':10,'max_parallel':1,'max_calls_per_event':2,
          'max_output_tokens':128}
    with pytest.raises(TenantLinearError):simulation.save_config(PROFILE,base)
    with pytest.raises(TenantLinearError):simulation.save_config(PROFILE,{**base,'enabled':'true','confirm_start':True})
    saved=simulation.save_config(PROFILE,{**base,'confirm_start':True})
    assert saved['enabled'] is True and saved['starts_at']
    assert saved['runtime_enabled'] is True
    assert db.execute("SELECT mode FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))[0]['mode']=='manual'
    foreign=simulation.read_config({'tenant_id':B,'telegram_id':22})
    assert foreign['enabled'] is False and foreign['agent_id'] is None and foreign['channel_ids']==[]


def test_simulation_api_requires_same_origin_and_explicit_tenant(db,monkeypatch):
    import asyncio,logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from tenant_ai_simulation_api import handler
    seed_simulation(db,enabled=False)
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected') ON CONFLICT DO NOTHING",(A,));db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    async def scenario():
        app=web.Application();app.router.add_route('GET','/settings',handler(lambda _r:PROFILE,'https://crm.test',logging.getLogger('test'),'settings'))
        app.router.add_route('PATCH','/settings',handler(lambda _r:PROFILE,'https://crm.test',logging.getLogger('test'),'settings'))
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/settings')).status==200
            payload={'expected_tenant_id':A,'expected_user_id':11,'enabled':True,'confirm_start':True,
                'agent_id':AGENT,'channel_ids':[CA],'conversation_ids':[],'token_budget':10000,'message_limit':5,
                'max_parallel':1,'max_calls_per_event':2,'max_output_tokens':128}
            assert (await client.patch('/settings',json=payload)).status==403
            assert (await client.patch('/settings',headers={'Origin':'https://crm.test'},json={**payload,'expected_tenant_id':B})).status==409
            response=await client.patch('/settings',headers={'Origin':'https://crm.test'},json=payload)
            assert response.status==200 and (await response.json())['result']['enabled'] is True
    asyncio.run(scenario())


def test_simulation_emergency_stop_fences_queued_and_pre_provider_work(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    assert queue_simulation(db,source=source)
    saved=simulation.save_config(PROFILE,{'emergency_stop':True})
    assert saved['emergency_stop'] is True
    assert db.execute("SELECT state,error_code FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid",(A,))[0]=={'state':'skipped','error_code':'emergency_stop'}
    assert simulation.claim_next() is None


def test_simulation_unknown_provider_outcome_is_not_retried(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    assert queue_simulation(db,source=source)
    queue=db.execute('SELECT id FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid',(A,))[0]['id']
    db.execute("UPDATE saas_ai_simulation_queue SET state='processing',lease_token=%s::uuid,lease_until=now()-interval '1 second',provider_started_at=now()-interval '4 minutes' WHERE id=%s::uuid",(str(uuid.uuid4()),queue))
    db.execute("INSERT INTO saas_ai_simulation_usage(tenant_id,queue_id,call_no,agent_id,call_kind,reserved_tokens,state) VALUES(%s::uuid,%s::uuid,1,%s::uuid,'primary',100,'reserved')",(A,queue,AGENT));db.commit()
    assert simulation.claim_next() is None
    assert db.execute("SELECT state,error_code FROM saas_ai_simulation_queue WHERE id=%s::uuid",(queue,))[0]=={'state':'unknown','error_code':'provider_result_unknown'}
    assert db.execute("SELECT state FROM saas_ai_simulation_usage WHERE queue_id=%s::uuid",(queue,))[0]['state']=='unknown'


def test_simulation_skips_event_already_claimed_by_active_mode(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    assert queue_simulation(db,source=source)
    fingerprint=simulation._event_fingerprint('new-message')
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,event_type,payload,state) VALUES(%s::uuid,%s,'connect','incoming_message','{}'::jsonb,'completed')",(A,fingerprint))
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,chat_id,channel_id,lead_id,event_fingerprint,talk_id,due_at,state) VALUES(%s::uuid,%s::uuid,'connect','client','client',%s,41,%s,'',now(),'sent')",(A,AGENT,CA,fingerprint));db.commit()
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: pytest.fail('Active-owned event must not call provider again'))
    job=simulation.claim_next()
    assert simulation.process(job)
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='skipped' and run['virtual_stop_reason']=='active_mode_already_processed'


def test_simulation_does_not_overlap_active_work_on_same_dialog(db,monkeypatch):
    source=seed_simulation(db)
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source)
    active_fingerprint=simulation._event_fingerprint('other-active-event')
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,event_type,payload,state) VALUES(%s::uuid,%s,'connect','incoming_message','{}'::jsonb,'processing')",(A,active_fingerprint))
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,chat_id,channel_id,lead_id,event_fingerprint,talk_id,due_at,state) VALUES(%s::uuid,%s::uuid,'connect','client','client',%s,41,%s,'',now(),'processing')",(A,AGENT,CA,active_fingerprint));db.commit()
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_a,**_k: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: ('mock-key','test-model','company',{}))
    monkeypatch.setattr('tenant_chat_ai_provider.generate',lambda *_a,**_k: pytest.fail('Concurrent Active dialog reached provider'))
    assert simulation.process(simulation.claim_next())
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='blocked' and run['virtual_stop_reason']=='simulation_stopped_before_provider'


def test_simulation_respects_legacy_channel_quarantine(db,monkeypatch):
    source=seed_simulation(db)
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state,reason,revision) VALUES(%s::uuid,'connect','','client',%s::uuid,'active','legacy',1)",(A,AGENT));db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source)
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: pytest.fail('Ambiguous legacy route reached credentials'))
    assert simulation.process(simulation.claim_next())
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='blocked' and run['virtual_stop_reason']=='legacy_channel_ambiguous'


def test_simulation_marks_unavailable_audio_with_specific_reason_without_provider_call(db,monkeypatch):
    source=seed_simulation(db)
    db.execute("UPDATE saas_crm_messages SET body='',message_type='audio' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,source));db.commit()
    db.execute('''INSERT INTO saas_ai_conversation_state
        (tenant_id,provider,channel_id,conversation_id,agent_id,state,reason,revision)
        VALUES(%s::uuid,'connect',%s,'client',%s::uuid,'human','manager_replied',1)''',(A,CA,AGENT));db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    assert queue_simulation(db,source=source)
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: pytest.fail('Unavailable media reached provider'))
    job=simulation.claim_next()
    assert simulation.process(job)
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='unsupported'
    assert run['virtual_stop_reason']=='simulation_audio_attachment_missing'
    assert {'type':'simulation_unsupported','reason':'media_context_unavailable',
            'detail':'simulation_audio_attachment_missing','message_type':'audio','executed':False} in run['events']
    assert {'type':'active_mode_stop_observed','reason':'conversation_human_or_paused',
            'live_state':'human','executed':False} in run['events']


@pytest.mark.parametrize('message_type,body,reason',[
    ('audio','','simulation_audio_attachment_missing'),
    ('image','','simulation_image_attachment_missing'),
    ('document','','simulation_document_attachment_missing'),
    ('sticker','','simulation_media_type_unsupported'),
])
def test_simulation_media_context_is_persisted_or_explicitly_unavailable(message_type,body,reason):
    text,event=simulation._source_media_context({'message_type':message_type,'body':body})
    assert text=='' and event['detail']==reason and event['executed'] is False
    text,event=simulation._source_media_context({'message_type':message_type,'body':'Saved safe context'})
    assert text=='Saved safe context'
    assert event=={'type':'media_context_available','message_type':message_type,
                   'source':'persisted_message_text','executed':False}


@pytest.mark.parametrize('source,reason',[
    ({'message_type':'audio','body':'','raw':{'voice_transcription':{'status':'failed'},'media_url':'https://private.example/a'}},'simulation_audio_transcription_failed'),
    ({'message_type':'audio','body':'','raw':{'voice_transcription':{'status':'completed'},'media_url':'https://private.example/a'}},'simulation_audio_transcript_empty'),
    ({'message_type':'audio','body':'','raw':{'media_url':'https://private.example/a'}},'simulation_audio_transcription_not_requested'),
    ({'message_type':'image','body':'','raw':{'media_status':'expired','media_url':'https://private.example/a'}},'simulation_media_url_expired'),
    ({'message_type':'image','body':'','raw':{'media_url':'https://private.example/a'}},'simulation_image_analysis_not_requested'),
])
def test_simulation_media_reason_uses_only_persisted_metadata(source,reason):
    text,event=simulation._source_media_context(source)
    assert text=='' and event['detail']==reason and event['executed'] is False


@pytest.mark.parametrize('message_type,body',[
    ('text','Müştəri yenidən yazıb: vəziyyəti yoxlayın.'),
    ('image','Müştərinin şəkil üçün əlavə etdiyi izah.'),
    ('audio','Müştəri səs yazısında vəziyyətin yoxlanmasını istəyir.'),
])
def test_simulation_observes_human_pause_and_keeps_virtual_processing(db,monkeypatch,message_type,body):
    """A prior staff reply stops Active, but never prevents virtual observation."""
    source=seed_simulation(db)
    db.execute('''INSERT INTO saas_crm_messages
        (id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type,happened_at)
        VALUES(%s::uuid,%s::uuid,41,'connect:staff-reply',%s,'outgoing','Operator','Baxıram.', 'text',now()-interval '1 minute')''',
        (str(uuid.uuid4()),A,'connect:'+CA))
    db.execute("UPDATE saas_crm_messages SET body=%s,message_type=%s WHERE tenant_id=%s::uuid AND id=%s::uuid",
               (body,message_type,A,source))
    db.execute('''INSERT INTO saas_ai_conversation_state
        (tenant_id,provider,channel_id,conversation_id,agent_id,state,reason,revision)
        VALUES(%s::uuid,'connect',%s,'client',%s::uuid,'human','manager_replied',7)''',(A,CA,AGENT))
    db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda _name: True)
    assert queue_simulation(db,source=source,event='customer-follow-up-'+message_type)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_args,**_kwargs: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: ('mock-key','test-model','company',{}))
    provider_calls=[]
    monkeypatch.setattr('tenant_chat_ai_provider.generate',
        lambda *_args,**_kwargs: (provider_calls.append(message_type) or 'Virtual follow-up',
                                   {'input_tokens':11,'output_tokens':7,'total_tokens':18}))
    before=db.execute("SELECT state,reason,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert simulation.process(simulation.claim_next())
    run=simulation.list_runs(PROFILE)[0]
    assert provider_calls==[message_type]
    assert run['outcome']=='virtual_reply' and run['sent'] is False
    assert {'type':'active_mode_stop_observed','reason':'conversation_human_or_paused',
            'live_state':'human','executed':False} in run['events']
    after=db.execute("SELECT state,reason,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert after==before
    assert db.execute("SELECT count(*) AS n FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))[0]['n']==0


def test_simulation_group_requires_live_group_authority_without_mutating_it(db,monkeypatch):
    group='12345@g.us';source=seed_simulation(db,conversation=group,lead_id=43,group=True)
    db.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,assignments,reason) VALUES(%s::uuid,%s,'[]'::jsonb,'group_assignment_required') ON CONFLICT DO NOTHING",(A,group));db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    assert queue_simulation(db,source=source,conversation=group,lead_id=43)
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: pytest.fail('Paused group reached provider'))
    job=simulation.claim_next()
    assert simulation.process(job)
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='virtual_stop' and run['virtual_stop_reason']=='group_binding_paused'
    assert run['events'][-1]=={'type':'virtual_stop','reason':'group_binding_paused',
                                'policy':'group_binding_required','executed':False}
    assert db.execute("SELECT state,assignments FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND group_jid=%s",(A,group))[0]['state']=='paused'


def test_simulation_group_jid_classification_does_not_treat_lid_as_a_group():
    assert group_coordination.group_jid('12345-67890@g.us')=='12345-67890@g.us'
    assert group_coordination.group_jid('12345@lid') is None


def test_simulation_group_active_binding_is_tenant_channel_and_primary_specific(db,monkeypatch):
    group='12345-67890@g.us'; source=seed_simulation(db,conversation=group,lead_id=43,group=True)
    # A valid Primary binding in another channel is never an implicit route.
    db.execute("""INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,assignments,reason)
      VALUES(%s::uuid,%s,'active',%s::jsonb,'operator_agent_assigned')""",
      (A,group,json.dumps([{'agent_id':AGENT,'channel_id':CB}])))
    db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    assert queue_simulation(db,source=source,conversation=group,lead_id=43)
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: pytest.fail('Mismatched group route reached provider'))
    assert simulation.process(simulation.claim_next())
    run=simulation.list_runs(PROFILE)[0]
    assert run['virtual_stop_reason']=='group_primary_channel_mismatch'
    assert db.execute("SELECT assignments FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND group_jid=%s",(A,group))[0]['assignments'][0]['channel_id']==CB


def test_simulation_group_active_explicit_primary_can_run_without_mutating_binding(db,monkeypatch):
    group='12345-67891@g.us'; source=seed_simulation(db,conversation=group,lead_id=43,group=True)
    assignment=[{'agent_id':AGENT,'channel_id':CA,'priority':0}]
    db.execute("""INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,assignments,reason)
      VALUES(%s::uuid,%s,'active',%s::jsonb,'operator_agent_assigned')""",(A,group,json.dumps(assignment)))
    db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    assert queue_simulation(db,source=source,conversation=group,lead_id=43)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_args,**_kwargs: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: ('mock-key','test-model','company',{}))
    monkeypatch.setattr('tenant_chat_ai_provider.generate',lambda *_args,**_kwargs: ('Virtual group draft',{'input_tokens':11,'output_tokens':7,'total_tokens':18}))
    assert simulation.process(simulation.claim_next())
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='virtual_reply' and run['sent'] is False
    assert db.execute("SELECT state,assignments FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND group_jid=%s",(A,group))[0]=={'state':'active','assignments':assignment}


def test_simulation_token_budget_blocks_provider_call(db,monkeypatch):
    source=seed_simulation(db)
    db.execute("UPDATE saas_ai_simulation_configs SET token_budget=100 WHERE tenant_id=%s::uuid",(A,));db.commit()
    monkeypatch.setattr(simulation,'runtime_flag',lambda name: name in {'AI_SIMULATION_ENABLED','AI_AGENTS_ENABLED'})
    assert queue_simulation(db,source=source)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_: [])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *_a,**_k: [])
    monkeypatch.setattr(simulation,'_provider_credentials',lambda *_: ('mock-key','test-model','company',{}))
    monkeypatch.setattr('tenant_chat_ai_provider.generate',lambda *_a,**_k: pytest.fail('Budget exhaustion reached paid provider'))
    job=simulation.claim_next();assert simulation.process(job)
    run=simulation.list_runs(PROFILE)[0]
    assert run['outcome']=='blocked' and run['virtual_stop_reason']=='simulation_budget_exhausted'
    assert db.execute("SELECT count(*) AS n FROM saas_ai_simulation_usage WHERE state IN ('reserved','succeeded','unknown')")[0]['n']==0


def test_simulation_executor_has_no_live_side_effect_imports():
    import ast
    tree=ast.parse((ROOT/'tenant_ai_simulation_engine.py').read_text(encoding='utf-8'))
    imports={node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)}
    imports.update(alias.name for node in ast.walk(tree) if isinstance(node,ast.Import) for alias in node.names)
    assert not any(name in imports for name in {'tenant_ai_agent_worker','tenant_connect_transport','tenant_chat_send_api','tenant_linear_tasks_api','tenant_ai_agent_actions.handoff'})
    calls={node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)}
    assert not calls.intersection({'send','send_ai_reply','send_ai_reply_if_active','handoff','execute_matching_actions','create_task','move_stage','update_phone'})


def test_ai_dashboard_is_tenant_scoped_and_uses_recorded_values(db,monkeypatch):
    run_id,_source,_queue=seed_simulation_run(db,events=[{'type':'trigger_matched','trigger_id':'t1'}])
    fingerprint='case-extraction-not-active-job'
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,event_type,payload,state) VALUES(%s::uuid,%s,'connect','knowledge_extraction','{}'::jsonb,'completed')",(A,fingerprint))
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,result) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,1,'local-preview','completed','{}'::jsonb)",(A,str(uuid.uuid4()),AGENT,fingerprint));db.commit()
    board=ai_dashboard.dashboard(PROFILE)
    assert [agent['id'] for agent in board['agents']]==[AGENT]
    assert board['simulation']['runs']==1 and board['simulation']['tokens']==17
    assert board['simulation']['trigger_matches']==1
    assert board['agents'][0]['active_runs']==0  # unrelated AI runs are not Active worker jobs
    assert all(item['tenant_id']==A for item in ai_dashboard.events(PROFILE,mode='simulation'))
    detail=ai_dashboard.simulation_run(PROFILE,run_id)
    assert detail['sent'] is False and 'tenant_id' not in detail
    json.dumps(detail)
    foreign={**PROFILE,'tenant_id':B,'telegram_id':22}
    assert ai_dashboard.events(foreign,mode='simulation')==[]
    with pytest.raises(TenantLinearError): ai_dashboard.simulation_run(foreign,run_id)


def test_ai_dashboard_issues_are_evidence_based_and_reviewable(db):
    run_id,_source,_queue=seed_simulation_run(db,events=[{'type':'expert_unavailable','reason':'expert_scope_denied'}],refs=[{'source_id':'synthetic-source'}])
    issues=ai_dashboard.issues(PROFILE)
    assert len(issues)==1 and issues[0]['category']=='expert_unavailable'
    assert issues[0]['status']=='new'
    result=ai_dashboard.review_issue(PROFILE,run_id,'expert_unavailable','in_review')
    assert result['status']=='in_review'
    assert ai_dashboard.issues(PROFILE)[0]['status']=='in_review'
    with pytest.raises(TenantLinearError): ai_dashboard.review_issue(PROFILE,run_id,'provider_result_unknown','confirmed')


def test_ai_dashboard_marks_possible_trigger_miss_as_review_prompt(db):
    run_id,_source,_queue=seed_simulation_run(db,events=[{'type':'trigger_checked','enabled_count':2,
        'matched':False,'possible_topic':'price'}],refs=[{'source_id':'demo-source'}])
    issues=ai_dashboard.issues(PROFILE)
    assert len(issues)==1 and issues[0]['category']=='possible_trigger_miss'
    assert 'not proof' in issues[0]['suggested_action'].lower()
    assert ai_dashboard.review_issue(PROFILE,run_id,'possible_trigger_miss','confirmed')['status']=='confirmed'


def test_ai_dashboard_recheck_is_tenant_scoped_and_idempotent(db,monkeypatch):
    run_id,_source,_queue=seed_simulation_run(db)
    monkeypatch.setattr('tenant_ai_agent_runtime_flags.enabled',lambda _name:True)
    first=ai_dashboard.enqueue_recheck(PROFILE,run_id,'request-123')
    second=ai_dashboard.enqueue_recheck(PROFILE,run_id,'request-123')
    assert first['queued'] and not first['idempotent']
    assert second['idempotent'] and first['queue_id']==second['queue_id']
    assert db.execute("SELECT count(*) AS n FROM saas_ai_simulation_queue WHERE tenant_id=%s::uuid AND recheck_of_run_id=%s::uuid",(A,run_id))[0]['n']==1
    foreign={**PROFILE,'tenant_id':B,'telegram_id':22}
    with pytest.raises(TenantLinearError): ai_dashboard.enqueue_recheck(foreign,run_id,'other-tenant')



def _enable_spend_budget(db, monkeypatch, cap=5_000_000):
    monkeypatch.setattr(spend_budget, '_connect', lambda: db)
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD', json.dumps({A: cap, B: cap}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION', json.dumps({
        'priced-model': {'input': 1_000_000, 'output': 2_000_000},
    }))
    spend_budget.ensure_schema(db)
    db.commit()


def test_shared_spend_reservation_is_atomic_idempotent_and_tenant_scoped(db, monkeypatch):
    _enable_spend_budget(db, monkeypatch)
    with db.cursor() as cur:
        one = spend_budget.reserve(cur, A, 'simulation:one', 'simulation_primary', 'priced-model', 1_000_000, 1_000_000)
        assert one
        # Retries for the same webhook/call key neither consume nor reserve twice.
        assert spend_budget.reserve(cur, A, 'simulation:one', 'simulation_primary', 'priced-model', 1_000_000, 1_000_000) == one
    db.commit()
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError) as exhausted:
            spend_budget.reserve(cur, A, 'expert:two', 'expert', 'priced-model', 1_000_000, 1_000_000)
        assert exhausted.value.status == 429
        # A different tenant receives its own cap, never A's reservation.
        assert spend_budget.reserve(cur, B, 'expert:two', 'expert', 'priced-model', 1_000_000, 1_000_000)
    db.commit()
    spend_budget.settle(A, 'simulation:one', {'input_tokens': 100, 'output_tokens': 100, 'total_tokens': 200})
    with db.cursor() as cur:
        assert spend_budget.reserve(cur, A, 'extraction:three', 'live_case_extraction', 'priced-model', 1_000_000, 1_000_000)
    db.commit()


def test_shared_spend_budget_fails_closed_and_retains_unknown_provider_result(db, monkeypatch):
    _enable_spend_budget(db, monkeypatch)
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError) as unknown_price:
            spend_budget.reserve(cur, A, 'active:missing-price', 'active_primary', 'unpriced-model', 1, 1)
        assert unknown_price.value.status == 409
        spend_budget.reserve(cur, A, 'active:ambiguous', 'active_primary', 'priced-model', 1_000_000, 1_000_000)
    db.commit()
    spend_budget.settle(A, 'active:ambiguous', {}, unknown=True)
    row = db.execute("SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key='active:ambiguous'", (A,))[0]
    assert row['state'] == 'unknown' and row['actual_microusd'] is None and row['reserved_microusd'] == 3_000_000
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):
            spend_budget.reserve(cur, A, 'simulation:cannot-fit', 'simulation_primary', 'priced-model', 1_000_000, 1_000_000)


def test_shared_spend_budget_rejects_provider_usage_above_preflight_upper_bound(db, monkeypatch):
    _enable_spend_budget(db, monkeypatch)
    with db.cursor() as cur:
        spend_budget.reserve(cur, A, 'expert:bounded', 'expert', 'priced-model', 10, 10)
    db.commit()
    # A later receipt beyond the preflight cap is ambiguous: retain, don't undercharge.
    spend_budget.settle(A, 'expert:bounded', {'input_tokens': 100, 'output_tokens': 100, 'total_tokens': 200})
    row = db.execute("SELECT state FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key='expert:bounded'", (A,))[0]
    assert row['state'] == 'unknown'


def test_internal_consultation_is_tenant_scoped_idempotent_and_never_sends_customer_message(db, monkeypatch):
    monkeypatch.setattr(consultations, '_connect', lambda: db)
    monkeypatch.setattr(consultations, '_hours', lambda: False)
    ticket=manual()['ticket']['id']
    payload={'case_id':ticket,'recipient_id':11,'reason':'Проверить кассу','evidence':{'message_id':'m1'},'request_key':'consultation-once'}
    first=consultations.create(PROFILE,payload)
    second=consultations.create(PROFILE,payload)
    assert first['created'] and not second['created']
    assert first['status']=='queued_after_hours' and first['customer_message_sent'] is False
    rows=consultations.listing(PROFILE,ticket)['consultations']
    assert len(rows)==1 and rows[0]['recipient_id']==11
    answer=consultations.reply(PROFILE,first['id'],{'response':'Нужны логи кассы','kind':'diagnosis'})
    assert answer['safe_customer_draft'] and answer['publication_requires_approval']
    foreign={'tenant_id':B,'telegram_id':22}
    assert consultations.listing(foreign,ticket)['consultations']==[]
    with pytest.raises(TenantLinearError): consultations.reply(foreign,first['id'],{'response':'x','kind':'diagnosis'})


def test_after_hours_ai_consultation_waits_for_in_hours_final_authorization(db, monkeypatch):
    monkeypatch.setattr(consultations, '_connect', lambda: db)
    monkeypatch.setattr(consultation_worker, '_connect', lambda: db)
    monkeypatch.setattr(consultations, '_hours', lambda: False)
    profile={'tenant_id':A,'telegram_id':11,'active':True,'role':'owner','permissions':['support'],'workflow':{}}
    monkeypatch.setattr(member_store, 'member', lambda *_: profile)
    monkeypatch.setattr(telegram_binding, 'binding_snapshot', lambda **_: {'chat_id':777,'verified_at':'2026-10-25T08:00:00+00:00'})
    monkeypatch.setattr(tenant_notifications, 'notification_channels', lambda *_, **__: ['telegram'])
    ticket=manual()['ticket']['id']
    job={'tenant_id':A,'agent_id':AGENT,'provider':'connect','channel_id':CA,'conversation_id':'client','event_fingerprint':'webhook-after-hours'}
    db.execute('INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid)',(A,'handoff:webhook-after-hours',ticket));db.commit()
    receipt=consultations.create_from_ai(job,recipient_id=11,reason='ai_developer_consultation:missing_knowledge',evidence={})
    assert receipt['status']=='queued_after_hours' and receipt['delivery']=='telegram_pending'
    queued=db.execute('''SELECT status,chat_id,binding_verified_at FROM saas_consultation_telegram_deliveries
      WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid''',(A,receipt['id']))[0]
    assert queued['status']=='pending' and queued['chat_id']==777
    monkeypatch.setattr(consultation_worker, '_hours', lambda: False)
    assert consultation_worker.claim_delivery() is None
    monkeypatch.setattr(consultation_worker, '_hours', lambda: True)
    monkeypatch.setattr(consultation_worker, 'member', lambda *_: profile)
    monkeypatch.setattr(consultation_worker, 'binding_snapshot', lambda **_: {'chat_id':777,'verified_at':'2026-10-25T08:00:00+00:00'})
    monkeypatch.setattr(consultation_worker, 'notification_channels', lambda *_, **__: ['telegram'])
    claimed=consultation_worker.claim_delivery()
    assert claimed and claimed['consultation_id']==receipt['id']
    assert consultation_worker._current_delivery_permission(claimed)
    row=db.execute('SELECT status FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,receipt['id']))[0]
    assert row['status']=='waiting_specialist'


def test_ai_consultation_uses_handoff_case_notifies_recipient_and_never_sends_customer_message(db, monkeypatch):
    monkeypatch.setattr(consultations, '_connect', lambda: db)
    monkeypatch.setattr(consultations, '_hours', lambda: False)
    ticket=manual()['ticket']['id']
    job={'tenant_id':A,'agent_id':AGENT,'provider':'connect','channel_id':CA,'conversation_id':'client','event_fingerprint':'webhook-consult-1'}
    db.execute('INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid)',(A,'handoff:webhook-consult-1',ticket));db.commit()
    first=consultations.create_from_ai(job,recipient_id=11,reason='ai_developer_consultation:missing_knowledge',evidence={'source_external_id':'m1'})
    second=consultations.create_from_ai(job,recipient_id=11,reason='ai_developer_consultation:missing_knowledge',evidence={'source_external_id':'m1'})
    assert first['created'] and first['customer_message_sent'] is False
    assert not second['created'] and second['id']==first['id']
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_notifications WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))[0]['n']==1
    row=db.execute("SELECT delivery_state,status,ai_run_id FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,first['id']))[0]
    assert row['delivery_state']=='internal_inbox' and row['status']=='queued_after_hours' and row['ai_run_id'] is None
    consultations.reply(PROFILE,first['id'],{'response':'Исправлено в версии 2.4','kind':'deployed_fix'})
    assert db.execute('SELECT knowledge_candidate_requested FROM saas_support_tickets WHERE tenant_id=%s::uuid',(A,))[0]['knowledge_candidate_requested'] is True


def test_ai_consultation_does_not_select_newer_unrelated_ticket(db, monkeypatch):
    monkeypatch.setattr(consultations, '_connect', lambda: db)
    old=manual()['ticket']['id']
    job={'tenant_id':A,'agent_id':AGENT,'provider':'connect','channel_id':CA,'conversation_id':'client','event_fingerprint':'old-event'}
    db.execute('INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid)',(A,'handoff:old-event',old))
    db.execute("UPDATE saas_support_tickets SET status='resolved' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,old));db.commit()
    new=manual()['ticket']['id']
    assert new != old
    result=consultations.create_from_ai(job,recipient_id=11,reason='test',evidence={})
    assert result == {'created':False,'reason':'support_case_missing'}
    assert db.execute('SELECT count(*) AS n FROM saas_internal_consultations WHERE tenant_id=%s::uuid',(A,))[0]['n']==0


def test_ai_consultation_requires_same_tenant_and_route_handoff_event(db, monkeypatch):
    monkeypatch.setattr(consultations, '_connect', lambda: db)
    ticket=manual()['ticket']['id']
    db.execute('INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid)',(A,'handoff:correct-event',ticket));db.commit()
    job={'tenant_id':A,'agent_id':AGENT,'provider':'connect','channel_id':CA,'conversation_id':'client','event_fingerprint':'missing-event'}
    for invalid in (job, {**job,'event_fingerprint':'correct-event','channel_id':CB},
                    {**job,'event_fingerprint':'correct-event','conversation_id':'another'}):
        assert consultations.create_from_ai(invalid,recipient_id=11,reason='test',evidence={}) == {'created':False,'reason':'support_case_missing'}
