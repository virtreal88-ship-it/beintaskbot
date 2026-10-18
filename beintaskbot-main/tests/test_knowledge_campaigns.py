"""Campaign reservations against disposable PostgreSQL; no provider calls."""
import uuid
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
    data={'request_id':str(uuid.uuid4()),'channel_ids':[CA],'analysis_agent_id':AGENT,'max_calls':2,'max_tokens':2*MAX_TOKENS}
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
    # Test-only authorization. Production has no approval/start endpoint yet.
    d.execute("UPDATE saas_knowledge_campaigns SET status='running',approved_at=now(),approved_by=11 WHERE id=%s::uuid",(key,));d.commit()

def test_prepare_is_unapproved_and_idempotent(campaign):
    d,provider,data,key=campaign
    assert campaigns.prepare(PROFILE,data)==key
    add(d,key)
    assert campaigns.claim(A,key) is None
    assert row(d,key)['reserved_calls']==0
    provider.assert_not_called()
    with pytest.raises(TenantLinearError):campaigns.control(PROFILE,key,1,'resume')

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
    assert value['cost'] is None and value['media_processed'] is None
    assert value['media_unavailable'] is None and value['processed_messages'] is None
    with pytest.raises(TenantLinearError):campaigns.control({'tenant_id':B,'telegram_id':22},key,1,'stop')
    assert campaigns.get({'tenant_id':B,'telegram_id':22},key)['campaign'] is None

def test_additive_schema_preserves_cards_agents_and_sessions(campaign):
    d,_,_,key=campaign
    before=d.execute('SELECT id,enabled,mode,scopes FROM saas_ai_agents ORDER BY id')
    campaigns.create_schema(d);campaigns.create_schema(d);d.commit()
    assert d.execute('SELECT id,enabled,mode,scopes FROM saas_ai_agents ORDER BY id')==before
    assert row(d,key)['status']=='awaiting_budget'
