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
import tenant_support_inbox_store as store
import tenant_support_inbox_summary as summary
from tenant_linear_policy import TenantLinearError

ROOT = Path(__file__).resolve().parents[1]
A,B,CA,CB,AGENT = [str(uuid.UUID(int=n)) for n in (101,102,201,202,301)]
PROFILE={'tenant_id':A,'telegram_id':11}


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
        return result['rows']
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
    database.execute('TRUNCATE saas_tenants CASCADE');database.commit()
    for module in (store,summary,actions,connect_ai,agents):monkeypatch.setattr(module,'_connect',lambda:database)
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
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,agent_id,state) VALUES(%s::uuid,'connect','client',%s::uuid,'active')",(A,AGENT))
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,chat_id,channel_id,lead_id,event_fingerprint,talk_id,due_at) VALUES(%s::uuid,%s::uuid,'connect','client','client',%s,41,'evt','',now())",(A,AGENT,CA))
    db.commit()
    return {'tenant_id':A,'provider':'connect','agent_id':AGENT,'conversation_id':'client','chat_id':'client','channel_id':CA,'lead_id':41,'event_fingerprint':'evt','lease_token':str(uuid.uuid4()),'revision':1}


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


def test_ordinary_inbound_does_not_create_ticket(db):
    seed_job(db)
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA}]),A));db.commit()
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


def test_accepted_manual_draft_handoff_creates_ticket(db):
    seed_job(db);run,draft=str(uuid.uuid4()),str(uuid.uuid4())
    db.execute("UPDATE saas_tenant_webhook_events SET payload=%s::jsonb WHERE tenant_id=%s::uuid",(json.dumps({'channel_id':CA}),A))
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test','completed')",(A,run,AGENT))
    db.execute("INSERT INTO saas_ai_agent_drafts(tenant_id,id,agent_id,run_id,lead_id,talk_id,chat_id,source_message_id,source_fingerprint,draft,handoff_after_send,state) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,41,'','client','m1','evt','Please wait',TRUE,'sending')",(A,draft,AGENT,run));db.commit()
    with patch.object(store,'summarize_created'):
        assert agents.finish_draft_send(A,draft,'accepted')=='approved'
    assert store.listing(PROFILE)['tickets'][0]['reason']=='trigger_reply_sent_then_handoff'


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
