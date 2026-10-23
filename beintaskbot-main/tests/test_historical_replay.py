"""Native isolated PostgreSQL + mocked provider; never uses production credentials."""
import json
import uuid
from unittest.mock import Mock
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,seed_simulation,A,B,CA,AGENT,PROFILE
import tenant_ai_historical_replay as replay
import tenant_ai_simulation_engine as simulation
import tenant_ai_spend_budget as money
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def ready(db,monkeypatch):
    seed_simulation(db)
    monkeypatch.setattr(replay,'_connect',lambda:db)
    monkeypatch.setattr(money,'_connect',lambda:db)
    monkeypatch.setattr(replay,'PILOT_TENANT',A)
    monkeypatch.setattr(replay,'PILOT_CHANNEL',CA)
    monkeypatch.setattr(replay,'PILOT_AGENT',AGENT)
    monkeypatch.setattr(replay,'_provider_credentials',lambda *a:('synthetic-key',replay.MODEL,'platform',{}))
    monkeypatch.setattr(replay,'knowledge_context',lambda *a,**k:[])
    monkeypatch.setenv('AI_SIMULATION_ENABLED','true');monkeypatch.setenv('AI_AGENTS_ENABLED','true')
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:5_000_000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({replay.MODEL:{'input':2_000_000,'output':8_000_000}}))
    db.execute("UPDATE saas_ai_agents SET mode='manual',model=%s,response_policy='{}'::jsonb WHERE tenant_id=%s::uuid",(replay.MODEL,A))
    db.execute("UPDATE saas_ai_simulation_configs SET channel_ids=%s::jsonb,agent_id=%s::uuid WHERE tenant_id=%s::uuid",(json.dumps([CA]),AGENT,A))
    ids=[]
    for n in range(5):
        lead=100+n;conversation='99450123456'+str(n)+'@s.whatsapp.net';mid=str(uuid.uuid4());ids.append(mid)
        db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id)
          VALUES(%s::uuid,%s::uuid,%s,%s)''',(A,CA,conversation,lead))
        db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at)
          VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s,'text',clock_timestamp())''',
          (mid,A,lead,'connect:'+mid,'connect:'+CA,'ERP question '+str(n)))
    db.commit()
    return db,ids


def test_five_replays_share_ledger_and_leave_business_and_queue_unchanged(ready,monkeypatch):
    db,ids=ready
    tables=('saas_ai_simulation_queue','saas_ai_simulation_runs','saas_crm_messages','saas_crm_tasks',
            'saas_crm_deals','saas_ai_conversation_state','saas_support_tickets')
    before={table:db.execute('SELECT row_to_json(t) AS row FROM '+table+' t')[0:] for table in tables};db.commit()
    calls=[]
    def provider(key,model,mode,lines,*args,**kwargs):
        assert key=='synthetic-key' and model==replay.MODEL and kwargs['images']==[]
        assert kwargs['max_output_tokens']==800 and kwargs['extra_context']['external_tools']==[]
        assert lines[-1]['role']=='client'
        # The durable money reservation exists before crossing the boundary.
        assert db.execute("SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE state='reserved'")[0]['n']==1
        db.commit();calls.append(lines)
        return 'Virtual reply',{'input_tokens':100,'output_tokens':20,'total_tokens':120}
    monkeypatch.setattr(replay,'generate',provider)
    assert replay.preflight(PROFILE,ids)['remaining_microusd']==5_000_000
    results=[replay.one(PROFILE,mid) for mid in ids]
    assert len(calls)==5 and all(row['outcome']=='virtual_reply' and not row['sent'] for row in results)
    assert replay.preflight(PROFILE,ids)['remaining_microusd']==5_000_000-5*960
    for table in tables:assert db.execute('SELECT row_to_json(t) AS row FROM '+table+' t')==before[table]
    assert db.execute('SELECT mode FROM saas_ai_agents WHERE id=%s::uuid',(AGENT,))[0]['mode']=='manual'
    db.commit()
    with pytest.raises(TenantLinearError,match='already_attempted'):replay.one(PROFILE,ids[0])
    assert len(calls)==5


@pytest.mark.parametrize('case',['missing_price','exhausted','provider_unknown','settlement_unknown','disabled','foreign'])
def test_replay_fails_closed_and_never_automatically_retries(ready,monkeypatch,case):
    db,ids=ready;provider=Mock(return_value=('Virtual',{'total_tokens':120}));monkeypatch.setattr(replay,'generate',provider)
    if case=='missing_price':monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION','{}')
    if case=='exhausted':
        with db.cursor() as cur:money.reserve(cur,A,'existing','simulation_primary',replay.MODEL,total_upper=625000)
        db.commit()
    if case=='provider_unknown':provider.side_effect=TimeoutError('synthetic timeout')
    if case=='settlement_unknown':provider.return_value=('Virtual',None)
    if case=='disabled':monkeypatch.setenv('AI_SIMULATION_ENABLED','false')
    profile={'tenant_id':B,'telegram_id':22} if case=='foreign' else PROFILE
    with pytest.raises((TenantLinearError,TimeoutError)):replay.one(profile,ids[0])
    assert provider.call_count==(1 if case in {'provider_unknown','settlement_unknown'} else 0)
    if provider.call_count:
        assert db.execute("SELECT state FROM saas_ai_spend_reservations WHERE call_key LIKE 'historical-replay:%%'")[0]['state']=='unknown'
        db.commit()
        with pytest.raises(TenantLinearError,match='already_attempted'):replay.one(PROFILE,ids[0])
        assert provider.call_count==1


def test_cutoff_excludes_future_and_ambiguous_time_ties_and_keeps_latest_question(ready,monkeypatch):
    db,ids=ready;mid=ids[0]
    for n in range(28):
        db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at,created_at)
          SELECT %s::uuid,tenant_id,kommo_lead_id,%s,channel,'outgoing',%s,happened_at-interval '1 second',created_at-interval '1 second'
          FROM saas_crm_messages WHERE id=%s::uuid''',(str(uuid.uuid4()),'old-'+str(n),'x'*3000,mid))
    for suffix in ('future','equal'):
        db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at,created_at)
          SELECT %s::uuid,tenant_id,kommo_lead_id,%s,channel,'outgoing','HIDDEN FUTURE ANSWER',
          happened_at+%s::interval,created_at FROM saas_crm_messages WHERE id=%s::uuid''',
          (str(uuid.uuid4()),suffix,'1 second' if suffix=='future' else '0 seconds',mid))
    db.commit()
    with db.cursor() as cur:row,job,lines=replay.source(cur,A,mid)
    assert lines[-1]['text']=='ERP question 0' and 'HIDDEN FUTURE ANSWER' not in str(lines)
    assert len(lines)<30
    db.commit()


def test_authorized_expert_and_primary_reserve_separately_in_the_same_ledger(ready,monkeypatch):
    db,ids=ready;expert=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,role,mode,model,instructions)
      VALUES(%s::uuid,%s::uuid,'Synthetic internal expert',11,TRUE,'expert','manual',%s,'Use supplied evidence')''',
      (A,expert,replay.MODEL))
    db.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps({'expert_agent_ids':[expert],'tools':['instruction_library'],'max_calls':2,
        'max_tokens':12000,'max_context_chars':6000,'timeout_seconds':20}),A,AGENT))
    db.execute('''INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,condition,actions,enabled)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'Expert test','new_inquiry','{}'::jsonb,'["call_expert"]'::jsonb,TRUE)''',
      (A,str(uuid.uuid4()),AGENT));db.commit()
    advice={'cause':'Needs investigation','evidence':[],'confidence':'low','safe_actions':[],
        'additional_information':'Error screenshot','requires_human':True,'ticket_summary':'Synthetic case'}
    provider=Mock(side_effect=[(json.dumps(advice),{'total_tokens':50}),('Please share a screenshot.',{'total_tokens':60})])
    monkeypatch.setattr(replay,'generate',provider)
    result=replay.one(PROFILE,ids[0])
    assert provider.call_count==2 and len(result['expert_results'])==1
    assert {row['kind'] for row in db.execute('SELECT kind FROM saas_ai_spend_reservations')}=={'simulation_replay_expert','simulation_replay_reply'}
    assert db.execute('SELECT count(*) AS n FROM saas_ai_simulation_queue')[0]['n']==0
    db.commit()


@pytest.mark.parametrize('kind',['group','lid','employee','identity_conflict','old'])
def test_unverified_or_employee_or_old_sources_are_never_submitted(ready,monkeypatch,kind):
    db,ids=ready;provider=Mock();monkeypatch.setattr(replay,'generate',provider)
    if kind in {'group','lid'}:
        db.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s::uuid AND lead_id=100',
            ('123456@g.us' if kind=='group' else '123456789@lid',A))
    elif kind=='employee':db.execute("UPDATE saas_crm_messages SET raw='{\"verified_employee_id\":11}'::jsonb WHERE id=%s::uuid",(ids[0],))
    elif kind=='identity_conflict':
        db.execute("UPDATE saas_crm_messages SET raw=jsonb_build_object('conversationIdentityConflict',true,'conversationId','994501234560@s.whatsapp.net') WHERE id=%s::uuid",(ids[0],))
    else:db.execute("UPDATE saas_crm_messages SET happened_at=now()-interval '8 days' WHERE id=%s::uuid",(ids[0],))
    db.commit()
    with pytest.raises(TenantLinearError):replay.one(PROFILE,ids[0])
    provider.assert_not_called()


def test_concurrent_same_source_cannot_cross_provider_boundary_twice(ready,monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    db,ids=ready
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(replay,'_connect',factory);monkeypatch.setattr(money,'_connect',factory)
    count=[];lock=Lock()
    def provider(*args,**kwargs):
        with lock:count.append(1)
        return 'Virtual',{'total_tokens':100}
    monkeypatch.setattr(replay,'generate',provider)
    def invoke():
        try:return replay.one(PROFILE,ids[0])['outcome']
        except TenantLinearError as exc:return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:invoke(),range(2)))
    assert count==[1] and results.count('virtual_reply')==1
    assert any('already_attempted' in result for result in results)


def test_settlement_database_error_preserves_reservation_and_stops_retry(ready,monkeypatch):
    db,ids=ready;provider=Mock(return_value=('Virtual',{'total_tokens':100}))
    monkeypatch.setattr(replay,'generate',provider)
    monkeypatch.setattr(replay,'settle',Mock(side_effect=RuntimeError('synthetic settlement outage')))
    with pytest.raises(RuntimeError):replay.one(PROFILE,ids[0])
    assert db.execute('SELECT state FROM saas_ai_spend_reservations')[0]['state']=='reserved';db.commit()
    with pytest.raises(TenantLinearError,match='already_attempted'):replay.one(PROFILE,ids[0])
    assert provider.call_count==1
