"""Local PostgreSQL/WASM only; every generation call is a mock."""
import json
from unittest.mock import Mock
import pytest
from test_knowledge_candidates import ready
from test_tenant_support_inbox import database,db,A,B,PROFILE,CA
import tenant_operator_case_submission as submission
import tenant_knowledge_candidates_service as service
import tenant_ai_spend_budget as budget
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def failed(ready,monkeypatch):
    conn,generation,_=ready
    monkeypatch.setattr(submission,'_connect',lambda:conn)
    monkeypatch.setattr(budget,'_connect',lambda:conn)
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1000000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'test-model':{'input':1000000,'output':1000000}}))
    preview=submission.preview(PROFILE,{'channel_id':CA,'conversation_id':'client'})
    data={'channel_id':CA,'conversation_id':'client','message_ids':[m['id'] for m in preview['messages']],
          **{k:preview['agent'][k] for k in ('agent_id','agent_version','assignment_revision')}}
    original=service.select_language
    monkeypatch.setattr(service,'select_language',Mock(side_effect=TypeError('synthetic preflight')))
    result=submission.submit(PROFILE,data)
    assert result['request']['execution_status']=='failed'
    assert result['preflight_retry_available'] is True
    generation.assert_not_called()
    monkeypatch.setattr(service,'select_language',original)
    return conn,generation,data,result['request']['id']

def test_explicit_retry_creates_one_successor_and_normal_resubmit_does_not(failed):
    conn,generation,data,parent=failed
    duplicate=submission.submit(PROFILE,data)
    assert duplicate['request']['id']==parent and duplicate['already_submitted']
    generation.assert_not_called()
    command={**data,'retry_request_id':parent}
    first=submission.submit(PROFILE,command)
    second=submission.submit(PROFILE,command)
    assert first['request']['id']!=parent
    assert first['request']['execution_status']=='completed'
    assert first['preflight_retry_available'] is False
    assert second['request']['id']==first['request']['id'] and second['already_submitted']
    assert submission.submit(PROFILE,data)['request']['id']==first['request']['id']
    generation.assert_called_once()
    rows=conn.execute('SELECT route,status FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid',(A,))
    assert len(rows)==2
    child=next(row for row in rows if row['route'].get('retry_of'))
    assert child['route']['retry_of']==parent
    assert all(child['route'][flag] is False for flag in ('allow_external_media','allow_paid_asr','allow_vision'))

@pytest.mark.parametrize('change',[
    "UPDATE saas_ai_agent_runs SET state='unknown' WHERE id=%s::uuid",
    "UPDATE saas_ai_agent_runs SET state='completed' WHERE id=%s::uuid",
    "UPDATE saas_ai_agent_runs SET generated_at=now() WHERE id=%s::uuid",
    "UPDATE saas_ai_agent_runs SET error_code='provider_timeout' WHERE id=%s::uuid",
    "UPDATE saas_knowledge_extractions SET status='unknown' WHERE id=%s::uuid",
    "UPDATE saas_knowledge_extractions SET error_code='media_processing_failed' WHERE id=%s::uuid",
    "UPDATE saas_ai_agent_token_usage SET total_tokens=1 WHERE run_id=%s::uuid",
    "UPDATE saas_ai_agent_token_usage SET input_tokens=1 WHERE run_id=%s::uuid",
    "UPDATE saas_ai_agent_token_usage SET output_tokens=1 WHERE run_id=%s::uuid",
    "UPDATE saas_ai_agent_token_usage SET reserved_tokens=1 WHERE run_id=%s::uuid",
    "DELETE FROM saas_ai_agent_token_usage WHERE run_id=%s::uuid",
    "UPDATE saas_knowledge_extractions SET route=route || '{\"allow_vision\":true}'::jsonb WHERE id=%s::uuid",
])
def test_any_ambiguous_dispatch_or_permission_state_fails_closed(failed,change):
    conn,generation,data,parent=failed
    conn.execute(change,(parent,));conn.commit()
    with pytest.raises(TenantLinearError):submission.submit(PROFILE,{**data,'retry_request_id':parent})
    generation.assert_not_called()
    assert len(conn.execute('SELECT id FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid',(A,)))==1

@pytest.mark.parametrize('state,actual',[('reserved',None),('unknown',None),('settled',0),('released',None),('released',1)])
def test_requires_committed_zero_cost_release(failed,state,actual):
    conn,generation,data,parent=failed
    conn.execute('UPDATE saas_ai_spend_reservations SET state=%s,actual_microusd=%s WHERE tenant_id=%s::uuid',(state,actual,A));conn.commit()
    assert submission.submit(PROFILE,data)['preflight_retry_available'] is False
    with pytest.raises(TenantLinearError):submission.submit(PROFILE,{**data,'retry_request_id':parent})
    generation.assert_not_called()

def test_missing_historical_spend_receipt_is_not_inferred_from_current_cap(failed,monkeypatch):
    conn,generation,data,parent=failed
    conn.execute('DELETE FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid',(A,));conn.commit()
    monkeypatch.delenv('AI_TENANT_SPEND_LIMITS_MICROUSD')
    assert submission.submit(PROFILE,data)['preflight_retry_available'] is False
    with pytest.raises(TenantLinearError):submission.submit(PROFILE,{**data,'retry_request_id':parent})
    generation.assert_not_called()

@pytest.mark.parametrize('kind',['tenant','selection','source','assignment','agent','permission'])
def test_retry_revalidates_exact_authorized_source(failed,kind):
    conn,generation,data,parent=failed;profile=PROFILE
    if kind=='tenant':profile={'tenant_id':B,'telegram_id':22}
    if kind=='selection':data={**data,'message_ids':data['message_ids'][1:]}
    if kind=='source':conn.execute("UPDATE saas_crm_messages SET body='changed' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,data['message_ids'][0]))
    if kind=='assignment':conn.execute('UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE tenant_id=%s::uuid',(A,))
    if kind=='agent':conn.execute('UPDATE saas_ai_agents SET version=version+1 WHERE tenant_id=%s::uuid',(A,))
    if kind=='permission':conn.execute("UPDATE saas_tenant_members SET role='worker',permissions='[]'::jsonb WHERE tenant_id=%s::uuid",(A,))
    conn.commit()
    with pytest.raises(TenantLinearError):submission.submit(profile,{**data,'retry_request_id':parent})
    generation.assert_not_called()

@pytest.mark.parametrize('change',[
    "UPDATE saas_ai_spend_reservations SET call_key='unrelated' WHERE tenant_id=%s::uuid",
    "UPDATE saas_ai_spend_reservations SET completed_at=NULL WHERE tenant_id=%s::uuid",
    "UPDATE saas_ai_spend_reservations SET model='other-model' WHERE tenant_id=%s::uuid",
    "UPDATE saas_ai_spend_reservations SET kind='other-operation' WHERE tenant_id=%s::uuid",
    "UPDATE saas_ai_spend_reservations SET grant_id='00000000-0000-0000-0000-000000000001'::uuid WHERE tenant_id=%s::uuid",
])
def test_release_receipt_must_match_exact_call_and_scope(failed,change):
    conn,generation,data,parent=failed
    conn.execute(change,(A,));conn.commit()
    with pytest.raises(TenantLinearError):submission.submit(PROFILE,{**data,'retry_request_id':parent})
    generation.assert_not_called()
