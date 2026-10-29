"""Scoped daily approval does not reset shared usage or alter other callers."""
import json
import uuid
import pytest
from test_knowledge_candidates import ready
from test_knowledge_campaigns import campaign, row, add
from test_tenant_support_inbox import database, db, A, B, CA, AGENT, PROFILE
from tenant_linear_policy import TenantLinearError
import tenant_case_daily_ceiling as ceiling
import tenant_knowledge_campaigns as campaigns


def test_owner_approval_scoped_to_original_grant(campaign,monkeypatch):
    d,provider,_,key=campaign
    monkeypatch.setattr(ceiling,'_connect',lambda:d)
    progress={'mode':'case_media_batch'}
    d.execute("UPDATE saas_knowledge_campaigns SET status='paused',approved_at=now(),approved_by=11,max_cost_microusd=10000000,progress=%s::jsonb WHERE id=%s::uuid",(json.dumps(progress),key))
    d.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,key,key))
    d.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata) VALUES(%s::uuid,'chat_ai','connected',%s::jsonb)",(A,json.dumps({'daily_limit':50})));d.commit()
    blocked=add(d,key)
    d.execute("UPDATE saas_knowledge_campaign_fragments SET status='blocked',error_code='limit_exceeded' WHERE id=%s::uuid",(blocked,))
    d.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,reserved_calls) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test','completed',48)",(A,str(uuid.uuid4()),AGENT));d.commit()
    with d.cursor() as cur:assert ceiling.effective(cur,A,key,50)==50
    with pytest.raises(TenantLinearError):ceiling.approve({'tenant_id':B,'telegram_id':22},key,1)
    with pytest.raises(TenantLinearError):ceiling.approve(PROFILE,key,99)
    assert ceiling.approve(PROFILE,key,1)=={'daily_call_ceiling':150}
    other=str(uuid.uuid4())
    d.execute("INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress,max_cost_microusd) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'stopped',11,now(),100,1000000,%s::jsonb,%s::jsonb,10000000)",(A,other,json.dumps([CA]),AGENT,json.dumps({}),json.dumps({'mode':'case_media_batch'})))
    with d.cursor() as cur:
        assert ceiling.effective(cur,A,key,50)==150
        assert ceiling.effective(cur,A,other,50)==50
        assert ceiling.effective(cur,B,key,50)==50
        assert ceiling.effective(cur,A,key,49)==49
        assert ceiling.effective(cur,A,None,50)==50
    saved=row(d,key);assert saved['status']=='paused' and saved['version']==2
    assert campaigns.get(PROFILE,key)['campaign']['limit_diagnostics']['utc_daily_ai_call_limit']==150
    assert d.execute('SELECT status FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid',(blocked,))[0]['status']=='blocked'
    assert saved['progress']['daily_call_ceiling']['owner_id']=='11'
    assert d.execute("SELECT action FROM saas_tenant_audit_events WHERE entity_id=%s AND action='knowledge_campaign_daily_ceiling_approved'",(key,))
    d.execute("UPDATE saas_ai_media_grants SET status='completed' WHERE id=%s::uuid",(key,));d.commit()
    with d.cursor() as cur:assert ceiling.effective(cur,A,key,50)==50
    provider.assert_not_called()


def test_scoped_ceiling_applies_at_real_admission_not_to_other_calls(campaign,monkeypatch):
    import tenant_knowledge_candidates_service as service
    import tenant_ai_spend_budget as spend
    import tenant_knowledge_campaign_worker as worker
    monkeypatch.setattr(worker,'check_fence',lambda *_:None)
    d,provider,_,key=campaign
    monkeypatch.setattr(ceiling,'_connect',lambda:d)
    monkeypatch.setattr(spend,'_connect',lambda:d)
    monkeypatch.setattr(spend,'price',lambda *_:(1,1))
    monkeypatch.setattr(spend,'media_price',lambda *_:1)
    policy={'allow_external_media':True,'allow_paid_asr':False,'allow_vision':True,'max_main_calls':100,'max_asr_calls':0,'max_vision_calls':100}
    d.execute("UPDATE saas_knowledge_campaigns SET status='paused',approved_at=now(),approved_by=11,max_cost_microusd=10000000,max_calls=100,max_tokens=10000000,media_policy=%s::jsonb,progress=%s::jsonb WHERE id=%s::uuid",(json.dumps(policy),json.dumps({'mode':'case_media_batch'}),key))
    spend.ensure_schema(d)
    d.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,key,key))
    d.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata) VALUES(%s::uuid,'chat_ai','connected',%s::jsonb)",(A,json.dumps({'daily_limit':50})))
    prior_run=str(uuid.uuid4())
    d.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,reserved_calls) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test','completed',48)",(A,prior_run,AGENT));d.commit()
    fragment=add(d,key)
    from datetime import datetime,timedelta,timezone
    now=datetime.now(timezone.utc)
    d.execute("UPDATE saas_knowledge_campaign_fragments SET route=route||%s::jsonb WHERE id=%s::uuid",(json.dumps({'analysis_agent_id':AGENT,'period_start':(now-timedelta(days=1)).isoformat(),'period_end':(now+timedelta(days=1)).isoformat()}),fragment));d.commit()
    campaigns.control(PROFILE,key,1,'resume');claimed=campaigns.claim(A,key)
    data={**claimed['route'],'request_id':str(claimed['id'])}
    with pytest.raises(TenantLinearError,match='Дневной лимит'):service.prepare(PROFILE,data)
    d.execute("UPDATE saas_knowledge_campaigns SET status='paused' WHERE id=%s::uuid",(key,))
    d.execute("UPDATE saas_knowledge_campaign_fragments SET status='pending',lease_token=NULL,lease_until=NULL WHERE id=%s::uuid",(fragment,));d.commit()
    assert ceiling.approve(PROFILE,key,2)['daily_call_ceiling']==150
    ordinary={'request_id':str(uuid.uuid4()),'provider':'connect','channel_id':CA,'conversation_id':'client','analysis_agent_id':AGENT,'allow_external_media':True,'allow_vision':True}
    with pytest.raises(TenantLinearError,match='Дневной лимит'):service.prepare(PROFILE,ordinary)
    campaigns.control(PROFILE,key,3,'resume');claimed=campaigns.claim(A,key)
    data={**claimed['route'],'request_id':str(claimed['id'])}
    d.execute('UPDATE saas_ai_agent_runs SET reserved_calls=149 WHERE id=%s::uuid',(prior_run,));d.commit()
    with pytest.raises(TenantLinearError,match='Дневной лимит'):service.prepare(PROFILE,data)
    d.execute('UPDATE saas_ai_agent_runs SET reserved_calls=48 WHERE id=%s::uuid',(prior_run,));d.commit()
    hold=str(uuid.uuid4())
    d.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,grant_id,state) VALUES(%s::uuid,%s::uuid,'old-unknown','test','vision',10000000,%s::uuid,'unknown')",(A,hold,key));d.commit()
    with pytest.raises(TenantLinearError,match='grant_budget_exhausted'):service.prepare(PROFILE,data)
    d.execute('UPDATE saas_ai_spend_reservations SET reserved_microusd=100 WHERE id=%s::uuid',(hold,));d.commit()
    item=service.prepare(PROFILE,data)
    assert item['id']==str(fragment)
    assert d.execute("SELECT reserved_calls FROM saas_ai_agent_runs WHERE id=%s::uuid",(fragment,))[0]['reserved_calls']==3
    assert row(d,key)['progress']['daily_call_ceiling']['ceiling']==150
    assert d.execute('SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE id=%s::uuid',(hold,))[0]=={'state':'unknown','reserved_microusd':100}
    provider.assert_not_called()
