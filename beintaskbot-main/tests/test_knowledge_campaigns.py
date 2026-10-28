"""Campaign reservations against disposable PostgreSQL; no provider calls."""
import uuid
import json
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE
from test_knowledge_candidates import ready
from tenant_linear_policy import TenantLinearError
import tenant_knowledge_campaigns as campaigns
from tenant_knowledge_candidates_policy import MAX_TOKENS

@pytest.fixture
def campaign(ready,monkeypatch):
    d,provider,_=ready
    monkeypatch.setattr(campaigns,'_connect',lambda:d)
    import tenant_knowledge_quality as quality
    monkeypatch.setattr(quality,'_connect',lambda:d)
    data={'request_id':str(uuid.uuid4()),'channel_ids':[CA],'analysis_agent_id':AGENT,'max_calls':2,'max_tokens':2*(MAX_TOKENS+8000)}
    key=campaigns.prepare(PROFILE,data)
    return d,provider,data,key

def row(d,key):
    return d.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,key))[0]

def add(d,key,n=0):
    value={'provider':'connect','channel_id':CA,'conversation_id':'client','history_mode':'channel','period_start':'2026-10-01T00:00:00Z','period_end':'2026-10-02T00:00:00Z'}
    if n:value['before']=str(uuid.UUID(int=900+n))
    with d.cursor() as cur: result=campaigns.enqueue(cur,PROFILE,row(d,key),value)
    d.commit();return result

def approve_fixture(d,key):
    # Test-only shortcut; explicit production approval is tested separately.
    d.execute("UPDATE saas_knowledge_campaigns SET status='running',approved_at=now(),approved_by=11 WHERE id=%s::uuid",(key,));d.commit()


def test_paused_campaign_exposes_safe_current_quota_without_changing_state(campaign):
    import tenant_knowledge_campaign_worker as worker
    d,provider,_,key=campaign;fragment=add(d,key)
    d.execute("UPDATE saas_knowledge_campaigns SET status='paused' WHERE id=%s::uuid",(key,))
    d.execute("UPDATE saas_knowledge_campaign_fragments SET status='blocked',error_code='limit_exceeded',result=%s::jsonb WHERE id=%s::uuid",(json.dumps({'safe_limit_reason':'company_daily_ai_call_limit'}),fragment))
    d.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata) VALUES(%s::uuid,'chat_ai','connected',%s::jsonb)",(A,json.dumps({'daily_limit':1,'api_key':'PRIVATE-DO-NOT-RETURN'})));d.commit()
    d.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test','completed')",(A,str(uuid.uuid4()),AGENT));d.commit()
    before=row(d,key)
    diagnostics=campaigns.get(PROFILE,key)['campaign']['limit_diagnostics']
    assert diagnostics['current_blocker']=='company_daily_ai_call_limit'
    assert diagnostics['last_limit_error']=='company_daily_ai_call_limit'
    assert diagnostics['utc_daily_ai_call_limit']==1
    assert diagnostics['basis']=='current_snapshot_not_historical_error'
    assert 'PRIVATE-DO-NOT-RETURN' not in json.dumps(diagnostics)
    assert row(d,key)==before
    assert campaigns.get({'tenant_id':B,'telegram_id':22},key)['campaign'] is None
    assert worker._safe_limit_reason(TenantLinearError('private sensitive SQL',429))=='other_limit'
    assert worker._safe_limit_reason(TenantLinearError('ai_spend_budget_exhausted',429))=='tenant_spend_limit'
    provider.assert_not_called()


def test_worker_records_allowlisted_limit_reason_without_raw_exception(campaign,monkeypatch):
    import tenant_knowledge_campaign_worker as worker
    d,provider,_,key=campaign;fragment=add(d,key);approve_fixture(d,key)
    d.execute('UPDATE saas_knowledge_campaigns SET progress=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({'mode':'case_media_batch'}),key));d.commit()
    monkeypatch.setattr(worker,'_connect',lambda:d)
    monkeypatch.setattr(worker,'check_fence',lambda *a:None)
    def fail(*a,**kw):raise TenantLinearError('Дневной лимит AI компании исчерпан.',429)
    monkeypatch.setattr(worker,'analyze',fail)
    assert worker.tick() is True
    saved=d.execute('SELECT status,error_code,result FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid',(fragment,))[0]
    assert saved=={'status':'blocked','error_code':'limit_exceeded','result':{'safe_limit_reason':'company_daily_ai_call_limit'}}
    assert row(d,key)['status']=='paused'
    provider.assert_not_called()


def test_recovery_skips_quarantined_head_but_never_retries_unknown(campaign,monkeypatch):
    import tenant_case_media_recovery as recovery
    d,provider,_,key=campaign
    blocked_id=str(uuid.UUID(int=1));safe_id=str(uuid.UUID(int=2));unknown_id=str(uuid.UUID(int=3))
    blocked_source=str(uuid.uuid4());safe_source=str(uuid.uuid4());parent=str(uuid.uuid4())
    base={'provider':'connect','channel_id':CA,'conversation_id':'client','history_mode':'channel',
      'period_start':'2026-10-01T00:00:00Z','period_end':'2026-10-02T00:00:00Z',
      'source_version':'a'*64,'retry_of':parent,'owner_retry_unknown':True}
    for fragment,source,route,status in [
      (blocked_id,'blocked',{**base,'message_ids':[blocked_source]},'pending'),
      (safe_id,'safe',{**base,'message_ids':[safe_source]},'pending'),
      (unknown_id,'unknown',{'channel_id':CA,'message_ids':[blocked_source]},'unknown')]:
        d.execute('''INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route,status,created_at)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb,%s,'2026-01-01T00:00:00Z')''',
          (A,key,fragment,source,json.dumps(route),status))
    d.execute('UPDATE saas_knowledge_campaigns SET progress=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({'mode':'case_media_batch','failed_media_recovery':{'parents':{blocked_id:parent,safe_id:parent}}}),key));d.commit()
    monkeypatch.setattr(recovery,'verified_recovery_parent',lambda *args:{'id':parent})
    approve_fixture(d,key)
    claimed=campaigns.claim(A,key)
    assert str(claimed['id'])==safe_id
    states={str(r['id']):r['status'] for r in d.execute('SELECT id,status FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,))}
    assert states=={blocked_id:'pending',safe_id:'running',unknown_id:'unknown'}
    assert row(d,key)['reserved_calls']==1
    d.execute("UPDATE saas_knowledge_campaign_fragments SET status='completed' WHERE id=%s::uuid",(safe_id,))
    d.execute("UPDATE saas_knowledge_campaign_fragments SET route='{}'::jsonb WHERE id=%s::uuid",(unknown_id,));d.commit()
    assert campaigns.claim(A,key) is None
    assert row(d,key)['reserved_calls']==1
    assert d.execute('SELECT status FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid',(blocked_id,))[0]['status']=='pending'
    provider.assert_not_called()


def test_recovery_scan_reaches_work_after_one_full_blocked_page(campaign,monkeypatch):
    d,provider,_,key=campaign
    for n in range(1,103):
        value={'provider':'connect','channel_id':CA,'conversation_id':'client','history_mode':'channel',
          'period_start':'2026-10-01T00:00:00Z','period_end':'2026-10-02T00:00:00Z'}
        d.execute('''INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route,status,created_at)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb,'pending','2026-01-01T00:00:00Z')''',
          (A,key,str(uuid.UUID(int=n)),str(n),json.dumps(value)))
    d.execute('UPDATE saas_knowledge_campaigns SET progress=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({'mode':'case_media_batch','failed_media_recovery':{'parents':{}}}),key));d.commit()
    approve_fixture(d,key)
    monkeypatch.setattr(campaigns,'_unknown_conflicts',lambda cur,tenant,fragment,c: str(fragment['id'])!=str(uuid.UUID(int=102)))
    claimed=campaigns.claim(A,key)
    assert str(claimed['id'])==str(uuid.UUID(int=102))
    assert row(d,key)['reserved_calls']==1
    provider.assert_not_called()

def test_prepare_is_unapproved_and_idempotent(campaign):
    d,provider,data,key=campaign
    assert campaigns.prepare(PROFILE,data)==key
    add(d,key)
    assert campaigns.claim(A,key) is None
    assert row(d,key)['reserved_calls']==0
    provider.assert_not_called()
    with pytest.raises(TenantLinearError):campaigns.control(PROFILE,key,1,'resume')


def test_fixed_money_grant_hard_cap_and_unknown_hold(campaign,monkeypatch):
    import tenant_ai_spend_budget as money
    d,_,_,key=campaign
    monkeypatch.setattr(money,'_connect',lambda:d)
    monkeypatch.delenv('AI_TENANT_SPEND_LIMITS_MICROUSD',raising=False)
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'priced-model':{'input':12,'output':12}}))
    money.ensure_schema(d)
    d.execute("UPDATE saas_knowledge_campaigns SET status='running',approved_at=now(),approved_by=11,max_cost_microusd=10000000 WHERE id=%s::uuid",(key,));d.commit()
    d.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,key,key));d.commit()
    with d.cursor() as cur:
        first=money.reserve(cur,A,'grant:first','fixture','priced-model',total_upper=833_329_166_667,campaign_id=key)
        assert money.reserve(cur,A,'grant:first','fixture','priced-model',total_upper=833_329_166_667,campaign_id=key)==first
    d.commit()
    money.settle(A,'grant:first',{},unknown=True,campaign_id=key)
    with d.cursor() as cur:
        with pytest.raises(TenantLinearError,match='ai_media_grant_budget_exhausted'):
            money.reserve(cur,A,'grant:second','fixture','priced-model',total_upper=5_000_000,campaign_id=key)
    d.rollback()
    assert d.execute("SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE call_key='grant:first'")[0]=={'state':'unknown','reserved_microusd':9_999_951}

def test_conflicting_prepare_and_cross_tenant_sources(campaign):
    _,_,data,_=campaign
    with pytest.raises(TenantLinearError):campaigns.prepare(PROFILE,{**data,'max_calls':3})
    with pytest.raises(TenantLinearError):campaigns.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'channel_ids':[CB]})

def test_enqueue_replay_reservation_and_single_inflight(campaign):
    d,_,_,key=campaign
    first=add(d,key);assert add(d,key)==first
    add(d,key,1);approve_fixture(d,key)
    claim=campaigns.claim(A,key)
    assert claim and campaigns.claim(A,key) is None
    assert row(d,key)['reserved_calls']==1
    assert row(d,key)['reserved_tokens']==MAX_TOKENS

def test_restart_unknown_never_replays_or_refunds(campaign):
    d,provider,_,key=campaign
    add(d,key);add(d,key,1);approve_fixture(d,key);campaigns.claim(A,key)
    d.execute("UPDATE saas_knowledge_campaign_fragments SET lease_until=now()-interval '1 second'");d.commit()
    assert campaigns.claim(A,key) is None
    assert d.execute("SELECT status FROM saas_knowledge_campaign_fragments WHERE lease_token IS NOT NULL")[0]['status']=='unknown'
    assert row(d,key)['reserved_calls']==1
    provider.assert_not_called()

def test_pause_resume_stop_versions_and_isolation(campaign):
    d,_,_,key=campaign
    add(d,key);approve_fixture(d,key)
    with pytest.raises(TenantLinearError):campaigns.control({'tenant_id':B,'telegram_id':22},key,1,'stop')
    campaigns.control(PROFILE,key,1,'pause')
    assert campaigns.claim(A,key) is None
    with pytest.raises(TenantLinearError):campaigns.control(PROFILE,key,1,'resume')
    campaigns.control(PROFILE,key,2,'resume')
    campaigns.control(PROFILE,key,3,'stop')
    assert campaigns.claim(A,key) is None
    assert len(d.execute('SELECT id FROM saas_knowledge_campaign_fragments'))==1

def test_budget_exhaustion_stops_before_call(campaign):
    d,provider,_,key=campaign
    add(d,key);approve_fixture(d,key)
    d.execute('UPDATE saas_knowledge_campaigns SET reserved_tokens=max_tokens WHERE id=%s::uuid',(key,));d.commit()
    assert campaigns.claim(A,key) is None
    assert row(d,key)['status']=='budget_exhausted'
    provider.assert_not_called()

def test_completion_records_partial_media_and_fences_stale_worker(campaign):
    d,_,_,key=campaign
    add(d,key);approve_fixture(d,key);claimed=campaigns.claim(A,key)
    d.execute("UPDATE saas_crm_messages SET happened_at='2026-10-01T01:00:00Z'");d.commit()
    from tenant_knowledge_candidates_service import analyze
    result=analyze(PROFILE,{**claimed['route'],'request_id':str(claimed['id']),'analysis_agent_id':AGENT})
    result['request']['media_results']=[{'status':'unavailable','code':'expired'}]
    assert campaigns.complete(claimed,result)
    assert not campaigns.complete(claimed,result)
    record=d.execute('SELECT status FROM saas_knowledge_campaign_fragments')[0]
    assert record['status']=='partial'
    assert not d.execute('SELECT id FROM saas_ai_knowledge_sources')

def test_progress_never_invents_media_or_currency_counts(campaign):
    _,_,_,key=campaign
    value=campaigns.get(PROFILE,key)['campaign']
    assert value['cost'] is None and value['media_processed']==0
    assert value['media_unavailable']==0 and value['processed_messages']==0
    with pytest.raises(TenantLinearError):campaigns.control({'tenant_id':B,'telegram_id':22},key,1,'stop')
    assert campaigns.get({'tenant_id':B,'telegram_id':22},key)['campaign'] is None

def test_additive_schema_preserves_cards_agents_and_sessions(campaign):
    d,_,_,key=campaign
    before=d.execute('SELECT id,enabled,mode,scopes FROM saas_ai_agents ORDER BY id')
    campaigns.create_schema(d);campaigns.create_schema(d);d.commit()
    assert d.execute('SELECT id,enabled,mode,scopes FROM saas_ai_agents ORDER BY id')==before
    assert row(d,key)['status']=='awaiting_budget'

def test_full_history_no_age_cutoff_and_bounded_windows(campaign):
    import tenant_knowledge_campaign_discovery as discovery
    d,provider,_,key=campaign
    d.execute("UPDATE saas_crm_messages SET happened_at='2010-01-01T00:00:00Z' WHERE external_id='m1' AND tenant_id=%s::uuid",(A,))
    d.execute("UPDATE saas_crm_messages SET happened_at='2010-03-15T00:00:00Z' WHERE external_id LIKE 'response-%%'");d.commit()
    with d.cursor() as cur:discovery.seed(cur,PROFILE,row(d,key))
    d.commit()
    for _ in range(4):
        with d.cursor() as cur:discovery.discover(cur,PROFILE,row(d,key))
        d.commit()
    fragments=d.execute('SELECT route FROM saas_knowledge_campaign_fragments ORDER BY route->>\'period_start\'')
    assert len(fragments)==3
    from tenant_knowledge_candidates_policy import moment
    assert fragments[0]['route']['period_start'].startswith('2010-01-01')
    for fragment in fragments:
        route=fragment['route'];assert (moment(route['period_end'])-moment(route['period_start'])).days<=30
    assert fragments[0]['route']['period_end']==fragments[1]['route']['period_start']
    provider.assert_not_called()

def test_only_explicit_completed_tickets_after_delay(campaign):
    import tenant_knowledge_campaign_discovery as discovery
    d,provider,_,key=campaign
    ticket=str(uuid.uuid4())
    d.execute("INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,status,created_at,updated_at) VALUES(%s::uuid,%s::uuid,%s::uuid,'client','manual','in_progress',now()-interval '2 days',now()-interval '1 hour')",(A,ticket,CA));d.commit()
    with d.cursor() as cur:discovery.discover_resolved(cur,PROFILE,row(d,key))
    assert not d.execute('SELECT * FROM saas_knowledge_campaign_sources')
    d.execute("UPDATE saas_support_tickets SET status='resolved',resolved_at=now()-interval '1 hour'")
    d.execute("UPDATE saas_crm_messages SET created_at=now()-interval '2 hours'");d.commit()
    with d.cursor() as cur:discovery.discover_resolved(cur,PROFILE,row(d,key))
    d.commit()
    with d.cursor() as cur:discovery.discover_resolved(cur,PROFILE,row(d,key))
    assert len(d.execute('SELECT * FROM saas_knowledge_campaign_sources'))==1
    provider.assert_not_called()

def test_asr_cache_survives_fragment_replay_and_keeps_unknown_blocked(campaign,monkeypatch):
    import tenant_knowledge_media_cache as cache
    from unittest.mock import Mock
    d,_,_,_=campaign;monkeypatch.setattr(cache,'_connect',lambda:d)
    item={'tenant':A,'key':'mock','redaction_names':[]}
    invoke=Mock(return_value=('Synthetic transcript',{'total_tokens':20}))
    assert cache.transcribe_once(PROFILE,item,'model',(b'synthetic','wav','audio/wav'),invoke)[2]
    assert not cache.transcribe_once(PROFILE,item,'model',(b'synthetic','wav','audio/wav'),invoke)[2]
    assert invoke.call_count==1
    invoke.side_effect=TimeoutError()
    with pytest.raises(TimeoutError):cache.transcribe_once(PROFILE,item,'model',(b'unknown','wav','audio/wav'),invoke)
    with pytest.raises(TenantLinearError):cache.transcribe_once(PROFILE,item,'model',(b'unknown','wav','audio/wav'),invoke)
    assert invoke.call_count==2
    with pytest.raises(TenantLinearError):cache.transcribe_once({'tenant_id':B,'telegram_id':22},item,'model',(b'synthetic','wav','audio/wav'),invoke)

def test_explicit_approval_and_background_tick_no_delivery(campaign,monkeypatch):
    import tenant_knowledge_campaign_worker as worker
    d,provider,data,key=campaign;monkeypatch.setattr(worker,'_connect',lambda:d)
    with pytest.raises(TenantLinearError):campaigns.approve(PROFILE,key,{'expected_version':1})
    approved=campaigns.approve(PROFILE,key,{'expected_version':1,'channel_ids':[CA],'max_calls':data['max_calls'],'max_tokens':data['max_tokens'],'confirm_external_processing':True,'media_policy':row(d,key)['media_policy']})
    assert approved['campaign']['status']=='running'
    worker.tick();worker.tick()
    assert provider.call_count==1
    result=campaigns.get(PROFILE,key)['campaign']
    assert result['processed_messages']==3 and result['cards']>0, d.execute('SELECT status,error_code,jsonb_array_length(source_refs) AS n FROM saas_knowledge_extractions')
    assert not d.execute('SELECT id FROM saas_ai_knowledge_sources')
    assert not d.execute('SELECT id FROM saas_ai_agent_drafts')

def test_recovery_reuses_finished_receipt_without_another_call(campaign,monkeypatch):
    import tenant_knowledge_campaign_worker as worker
    import tenant_knowledge_candidates_service as service
    d,provider,_,key=campaign;monkeypatch.setattr(worker,'_connect',lambda:d)
    d.execute("UPDATE saas_crm_messages SET happened_at='2026-10-01T01:00:00Z'");d.commit()
    add(d,key);approve_fixture(d,key);claimed=campaigns.claim(A,key)
    service.analyze(PROFILE,{**claimed['route'],'request_id':str(claimed['id']),'analysis_agent_id':AGENT})
    d.execute("UPDATE saas_knowledge_campaign_fragments SET lease_until=now()-interval '1 second'");d.commit()
    worker.reconcile(PROFILE,key);worker.reconcile(PROFILE,key)
    assert provider.call_count==1
    assert d.execute('SELECT status FROM saas_knowledge_campaign_fragments')[0]['status']=='completed'

def test_published_knowledge_uses_virtual_test_and_shared_budget(campaign,monkeypatch):
    import tenant_knowledge_campaign_worker as worker
    import tenant_knowledge_quality as quality
    import tenant_knowledge_candidates_store as candidates
    import tenant_ai_testing_store as testing
    import tenant_ai_testing_service as virtual
    from unittest.mock import Mock
    d,_,_,key=campaign
    monkeypatch.setattr(worker,'_connect',lambda:d);monkeypatch.setattr(testing,'_connect',lambda:d)
    for name,value in [('runtime_flag',lambda _:True),('platform_service_ready',lambda:True),('platform_model',lambda:'fixture'),('platform_model_allowed',lambda _:True),('platform_token_limit',lambda _:0),('choose_api_credential',lambda *a:('mock','platform'))]:monkeypatch.setattr(testing,name,value)
    generation=Mock(return_value=('Проверить соединение Повторить синхронизацию',{'total_tokens':30}))
    monkeypatch.setattr(virtual,'generate',generation)
    d.execute('UPDATE saas_knowledge_campaigns SET max_calls=4,max_tokens=48000');d.commit()
    approve_fixture(d,key);worker.tick();worker.tick()
    card=candidates.listing(PROFILE,{})['candidates'][0]
    assert quality.tick(PROFILE,key) is False
    edited=candidates.mutate(PROFILE,card['id'],{'expected_version':card['version'],
        'work_status':'resolved','outcome':{'status':'confirmed','text':'Клиент подтвердил, что всё работает.',
            'message_ids':[card['end_message_id']]}},'edit')['candidate']
    assert edited['outcome']['status']=='confirmed'
    candidates.mutate(PROFILE,card['id'],{'expected_version':edited['version'],'agent_ids':[AGENT]},'approve')
    assert quality.tick(PROFILE,key)
    assert not quality.tick(PROFILE,key)
    assert generation.call_count==1
    d.execute("UPDATE saas_knowledge_quality_checks SET state='running',retrieval_state='pending',response_state='pending'");d.commit()
    quality.reconcile(PROFILE,key)
    assert not quality.tick(PROFILE,key)
    assert generation.call_count==1
    check=d.execute('SELECT state,retrieval_state,response_state FROM saas_knowledge_quality_checks')[0]
    assert check=={'state':'completed','retrieval_state':'found','response_state':'exact_supported'}
    assert row(d,key)['reserved_calls']==2
    assert not d.execute('SELECT * FROM saas_ai_agent_drafts')


def test_quality_never_labels_missing_sources_or_extra_steps_as_supported():
    from tenant_knowledge_quality import verdict
    assert verdict({'state':'completed','response':'Approved'},'source',['Approved'])==('missing_source','not_checked')
    turn={'state':'completed','trace':{'knowledge':[{'source_id':'source'}]},'response':'Approved and delete all files'}
    assert verdict(turn,'source',['Approved'])==('found','requires_review')
    assert verdict({**turn,'response':'Approved'},'source',['Approved','Second step'])==('found','requires_review')


def test_budget_renewal_keeps_progress_and_requires_explicit_consent(campaign):
    d,_,data,key=campaign
    d.execute("UPDATE saas_knowledge_campaigns SET status='budget_exhausted',progress='{"+'"phase":"history"'+"}'::jsonb");d.commit()
    approval={'expected_version':1,'channel_ids':[CA],'max_calls':4,'max_tokens':72000,'confirm_external_processing':False,'media_policy':row(d,key)['media_policy']}
    with pytest.raises(TenantLinearError):campaigns.approve(PROFILE,key,approval)
    result=campaigns.approve(PROFILE,key,{**approval,'confirm_external_processing':True,'media_policy':row(d,key)['media_policy']})['campaign']
    assert result['status']=='running' and result['progress']['phase']=='waiting_completion'

@pytest.mark.parametrize('kinds,expected',[(['image']*10,2),(['audio']*5,1),(['document']*4,1),(['video','voice'],1)])
def test_campaign_media_pages_do_not_strand_older_attachments(kinds,expected):
    from tenant_knowledge_campaign_discovery import bound_context
    item={'refs':[{'id':str(i),'media_kind':kind} for i,kind in enumerate(kinds)],'messages':[{'text':str(i)} for i in range(len(kinds))],'next_before':None}
    bound_context(item)
    assert len(item['refs'])==expected
    assert item['next_before']==item['refs'][0]['id']
    assert item['messages'][0]['text']==item['refs'][0]['id']

def test_group_inventory_keeps_only_selected_channels(campaign):
    import tenant_knowledge_campaign_discovery as discovery
    from test_knowledge_candidates import GROUP
    d,provider,_,key=campaign;second=str(uuid.uuid4())
    d.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s::uuid',(GROUP,A))
    d.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')",(A,second))
    d.execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,43)',(A,second,GROUP))
    d.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,43,'copy',%s,'incoming','Synthetic group message')",(A,str(uuid.uuid4()),'connect:'+second));d.commit()
    with d.cursor() as cur:discovery.seed(cur,PROFILE,row(d,key))
    d.commit()
    assert row(d,key)['progress']['stored_message_copies']==3
    import json
    d.execute('UPDATE saas_knowledge_campaigns SET sources=%s::jsonb',(json.dumps([CA,second]),));d.commit()
    with d.cursor() as cur:discovery.seed(cur,PROFILE,row(d,key))
    d.commit()
    planned=d.execute('SELECT history_mode FROM saas_knowledge_campaign_sources')
    assert planned==[{'history_mode':'unified_group'}]
    assert row(d,key)['progress']['source_count']==1
    provider.assert_not_called()


def test_inventory_keyset_reads_more_than_one_page(campaign):
    import tenant_knowledge_campaign_discovery as discovery
    d,provider,_,key=campaign
    for n in range(101):
        d.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,%s)",(A,CA,'history-'+str(n),1000+n))
        d.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','Synthetic')",(A,str(uuid.uuid4()),1000+n,'history-'+str(n),'connect:'+CA))
    d.commit()
    with d.cursor() as cur:rows=discovery.inventory(cur,PROFILE,[CA])
    assert len(rows)==102 and len({r['conversation_id'] for r in rows})==102
    provider.assert_not_called()
