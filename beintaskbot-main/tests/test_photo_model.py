"""Synthetic exact-photo selection and model-access recovery; never real I/O."""
import json
import uuid
from unittest.mock import Mock,AsyncMock
import pytest
pytest_plugins=('test_tenant_support_inbox',)
from test_tenant_support_inbox import db,A,B,CA,CB,AGENT,PROFILE,config_company
from test_single_photo_retry import prior,prepare_and_approve,complete
from test_connect_images import PNG
import tenant_ai_single_message_extraction as consent
import tenant_ai_knowledge_extraction_preflight as preflight
import tenant_ai_photo_model as photo
import tenant_knowledge_candidates_service as service
import tenant_knowledge_campaigns as campaigns
import tenant_knowledge_campaign_worker as worker
import tenant_knowledge_media as media
import tenant_chat_ai_provider as provider
RESPONSES_GENERATE=provider.generate
import tenant_media_evidence as evidence
import tenant_ai_administrator_read as admin
from tenant_linear_policy import TenantLinearError


def calls(monkeypatch,*,failure=None):
    current={'provider':'connect','route':{'channel_id':CA,'conversation_id':'client'},'row':{'message_type':'image'}}
    monkeypatch.setattr(media,'load',lambda *a:current)
    download=AsyncMock(return_value=(PNG,'image/png'));monkeypatch.setattr(media,'download_media',download)
    vision=Mock(side_effect=failure,return_value=('Visible synthetic quantity: 7',{'input_tokens':100,'output_tokens':10,'total_tokens':110}))
    main=Mock(side_effect=AssertionError('No text generation'))
    monkeypatch.setattr(provider,'generate',vision);monkeypatch.setattr(service,'generate',main)
    return download,vision,main


@pytest.fixture
def recovery(prior,monkeypatch):
    database,cfg,args,fp,ref=prior
    # Create and actually dispatch the original one-use retry with a mock 403.
    _,child=prepare_and_approve(prior)
    claimed=campaigns.claim(A,child['campaign_id']);assert claimed
    calls(monkeypatch,failure=provider.ChatAIProviderError(403,'model_access'))
    response=service.analyze(PROFILE,{**claimed['route'],'request_id':str(claimed['id'])})
    assert response['request']['status']=='unknown'
    assert response['request']['media_results'][0]['failure_code']=='provider_http_403_model_access'
    campaigns.complete(claimed,response)
    database.execute("UPDATE saas_knowledge_extractions SET deadline=now()-interval '1 second' WHERE id=%s::uuid",(child['fragment_id'],))
    cipher=config_company(database)
    monkeypatch.setattr(service,'_fernet',lambda:cipher)
    database.execute("UPDATE saas_tenant_integrations SET metadata=%s::jsonb WHERE tenant_id=%s::uuid AND provider='chat_ai'",(json.dumps({'enabled':True,'consent':True,'model':'fixture-model','models':['fixture-model',*photo.MODELS],'daily_limit':50}),A))
    monkeypatch.setenv('AI_AGENTS_VISION_MODELS','fixture-model,gpt-4.1,gpt-4.1-2025-04-14')
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'fixture-model':{'input':1,'output':1},**{m:{'input':2000000,'output':8000000} for m in photo.MODELS}}))
    database.commit()
    args={**args,'retry_of':str(child['fragment_id']),'request_id':str(uuid.uuid4()),'photo_model':'gpt-4.1'}
    return database,cfg,args,fp,ref


def run(recovery,monkeypatch):
    database,cfg,args,_,_=recovery
    mocks=calls(monkeypatch)
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    result=complete(database,cfg,prepared,acknowledge_prior_unknown=True)
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    return prepared,result,claim,response,mocks


def test_recovery_one_vision_company_key_receipt_read_no_text_or_old_hold_changes(recovery,monkeypatch):
    database,cfg,args,fp,ref=recovery
    old=database.execute('SELECT * FROM saas_ai_spend_reservations ORDER BY call_key')
    previous=database.execute('SELECT * FROM saas_knowledge_extractions ORDER BY id')
    caches=database.execute('SELECT * FROM saas_knowledge_media_cache ORDER BY fingerprint')
    agents=database.execute('SELECT * FROM saas_ai_agents ORDER BY id')
    prepared,result,claim,response,(download,vision,main)=run(recovery,monkeypatch)
    assert prepared['exact_preview']['photo_only'] and prepared['exact_preview']['photo_model']=='gpt-4.1'
    assert prepared['exact_preview']['maximum_additional_microusd']==8896
    assert claim['reservation']=={'main':0,'asr':0,'vision':1,'calls':1,'tokens':2648}
    assert response['request']['status']=='completed'
    assert vision.call_count==download.await_count==1;main.assert_not_called()
    assert vision.call_args.args[:2]==('test-key','gpt-4.1')
    assert vision.call_args.kwargs['max_output_tokens']==600
    assert vision.call_args.kwargs['images'][0]['detail']=='low'
    assert vision.call_args.args[3]==[]  # no customer dialogue sent
    visible=admin.read(database.cursor(),{**cfg,'granted_scopes':[admin.READ_SCOPE,admin.CONTENT_SCOPE]},'case_evidence_read',{'id':str(claim['id']),'include_content':True})['items'][0]
    assert visible['media_results'][0]['result']['observations'][0]['text']=='Visible synthetic quantity: 7'
    assert campaigns.complete(claim,response)
    assert complete(database,cfg,prepared,acknowledge_prior_unknown=True)['replayed']
    again=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert again['request']['id']==response['request']['id'] and vision.call_count==1
    assert database.execute('SELECT * FROM saas_ai_agents ORDER BY id')==agents
    for oldrow in old:assert database.execute('SELECT * FROM saas_ai_spend_reservations WHERE id=%s::uuid',(oldrow['id'],))[0]==oldrow
    for oldrow in previous:assert database.execute('SELECT * FROM saas_knowledge_extractions WHERE id=%s::uuid',(oldrow['id'],))[0]==oldrow
    for oldrow in caches:assert database.execute('SELECT * FROM saas_knowledge_media_cache WHERE fingerprint=%s',(oldrow['fingerprint'],))[0]==oldrow
    money=database.execute("SELECT * FROM saas_ai_spend_reservations WHERE call_key LIKE %s",('extraction:'+str(claim['id'])+'%',))
    assert len(money)==1 and money[0]['state']=='settled' and money[0]['model']=='gpt-4.1'
    assert money[0]['reserved_microusd']==8896 and money[0]['actual_microusd']==280
    assert evidence.reusable(PROFILE,ref)['result']['observations'][0]['text']=='Visible synthetic quantity: 7'
    assert database.execute('SELECT count(*) AS n FROM saas_knowledge_candidates')[0]['n']==0


@pytest.mark.parametrize('change',['tenant','token','revoke','source','price','budget','model','kind','parent_code','root_cache','root_evidence'])
def test_exact_photo_fences_before_enqueue(recovery,change,monkeypatch):
    database,cfg,args,fp,_=recovery
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    if change=='tenant':cfg={**cfg,'AI_LEARNING_MCP_TENANT_ID':B,'actor_id':22,'AI_LEARNING_MCP_CHANNEL_ID':CB}
    if change=='token':database.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now()')
    if change=='revoke':database.execute("UPDATE saas_ai_single_message_extraction_consents SET state='revoked' WHERE id=%s::uuid",(prepared['consent_id'],))
    if change=='source':database.execute("UPDATE saas_crm_messages SET external_id='different' WHERE id=%s::uuid",(args['message_id'],))
    if change=='price':monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'gpt-4.1':{'input':150000,'output':600000}}))
    if change=='budget':monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:8896}))
    if change=='model':database.execute("UPDATE saas_ai_single_message_extraction_consents SET photo_model='gpt-4.1-2025-04-14' WHERE id=%s::uuid",(prepared['consent_id'],))
    if change=='kind':database.execute("UPDATE saas_crm_messages SET message_type='video' WHERE id=%s::uuid",(args['message_id'],))
    if change=='parent_code':database.execute("UPDATE saas_knowledge_extractions SET media_results=jsonb_set(media_results,'{0,failure_code}','\"provider_timeout\"') WHERE id=%s::uuid",(args['retry_of'],))
    if change=='root_cache':database.execute('DELETE FROM saas_knowledge_media_cache WHERE fingerprint=%s',(fp,))
    if change=='root_evidence':database.execute("UPDATE saas_knowledge_extractions SET source_refs='[]'::jsonb WHERE route->>'retry_of' IS NULL")
    database.commit()
    with pytest.raises(TenantLinearError):complete(database,cfg,prepared,acknowledge_prior_unknown=True)
    database.rollback()
    assert database.execute("SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE route ? 'photo_model'")[0]['n']==0


@pytest.mark.parametrize('change',['token','price','source','revoke','cap','allowlist'])
def test_photo_revocation_after_enqueue_before_provider(recovery,change,monkeypatch):
    database,cfg,args,_,_=recovery
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    result=complete(database,cfg,prepared,acknowledge_prior_unknown=True)
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    if change=='token':database.execute('UPDATE saas_ai_learning_mcp_tokens SET revoked_at=now()')
    if change=='price':monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION','{}')
    if change=='source':database.execute("UPDATE saas_crm_messages SET body='changed' WHERE id=%s::uuid",(args['message_id'],))
    if change=='revoke':database.execute("UPDATE saas_ai_single_message_extraction_consents SET state='revoked' WHERE id=%s::uuid",(result['consent_id'],))
    if change=='cap':monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD','{}')
    if change=='allowlist':database.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,'{models}','[]') WHERE provider='chat_ai'")
    database.commit()
    with pytest.raises(TenantLinearError):service.prepare(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    database.rollback()
    assert database.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE model=%s',('gpt-4.1',))[0]['n']==0


def test_failed_recovery_keeps_new_hold_and_cannot_repeat_chain(recovery,monkeypatch):
    database,cfg,args,_,_=recovery
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    result=complete(database,cfg,prepared,acknowledge_prior_unknown=True);claim=campaigns.claim(A,result['campaign_id'])
    download,vision,main=calls(monkeypatch,failure=provider.ChatAIProviderError(403,'model_access'))
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert response['request']['status']=='unknown';campaigns.complete(claim,response)
    database.execute("UPDATE saas_knowledge_extractions SET deadline=now()-interval '1 second' WHERE id=%s::uuid",(result['fragment_id'],));database.commit()
    with pytest.raises(TenantLinearError):consent.prepare(database.cursor(),cfg,{**args,'retry_of':str(result['fragment_id']),'request_id':str(uuid.uuid4())})
    database.rollback()
    hold=database.execute("SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE model='gpt-4.1'")[0]
    assert hold=={'state':'unknown','reserved_microusd':8896};assert vision.call_count==1;main.assert_not_called()


def test_unapproved_model_override_is_not_a_new_unknown_bypass(recovery):
    _,_,args,_,_=recovery
    with pytest.raises(TenantLinearError):service.prepare(PROFILE,{'request_id':str(uuid.uuid4()),'photo_model':'gpt-4.1','channel_id':CA,'conversation_id':'client','analysis_agent_id':AGENT,'message_ids':[args['message_id']]})


def test_selected_dated_model_and_preflight_report_exact_choice(recovery,monkeypatch):
    database,cfg,args,fp,ref=recovery;args={**args,'photo_model':'gpt-4.1-2025-04-14'}
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    result=complete(database,cfg,prepared,acknowledge_prior_unknown=True)
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    info=preflight.preflight(database.cursor(),cfg,{k:args[k] for k in ('message_id','channel_id','lead_id')}|{'consent_id':prepared['consent_id']})
    assert info['model_id']=='gpt-4.1-2025-04-14' and info['credential_source']=='company' and info['ready']


def test_new_image_photo_mode_reuses_known_receipt_without_download_or_provider(recovery,monkeypatch):
    database,cfg,args,_,_=recovery
    from test_knowledge_extraction_preflight import _source
    from tenant_knowledge_candidates_store import digest
    message=_source(database)
    database.execute("UPDATE saas_crm_messages SET message_type='image' WHERE id=%s::uuid",(message,));database.commit()
    data={'message_id':message,'channel_id':CA,'lead_id':41,'photo_model':'gpt-4.1'}
    source=preflight._source(database.cursor(),cfg,{k:data[k] for k in ('message_id','channel_id','lead_id')})
    ref={'id':message,'channel_id':CA,'lead_id':41,'external_id':source['external_id'],'media_kind':'image','media_version':source['version'],'body_hash':digest('redacted')}
    evidence.create_schema(database)
    evidence.persist(database.cursor(),A,[ref],[{'id':message,'status':'processed','code':'vision_observation','observation':'Saved visible quantity: 7'}]);database.commit()
    prepared=consent.prepare(database.cursor(),cfg,data);database.commit()
    result=complete(database,cfg,prepared)
    claim=campaigns.claim(A,result['campaign_id']);assert claim
    download,vision,main=calls(monkeypatch)
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert response['request']['status']=='completed'
    # The public coverage projection intentionally omits content; use its scoped content reader.
    visible=admin.read(database.cursor(),{**cfg,'granted_scopes':[admin.READ_SCOPE,admin.CONTENT_SCOPE]},'case_evidence_read',{'id':str(claim['id']),'include_content':True})['items'][0]
    assert visible['media_results'][0]['result']['observation']=='Saved visible quantity: 7'
    download.assert_not_awaited();vision.assert_not_called();main.assert_not_called()
    assert database.execute("SELECT state,actual_microusd FROM saas_ai_spend_reservations WHERE model='gpt-4.1'")==[{'state':'released','actual_microusd':0}]


@pytest.mark.parametrize('failure',['missing_price','mini_price','disabled_vision','nonlisted','platform_only'])
def test_no_photo_consent_without_exact_company_model_and_tariff(recovery,monkeypatch,failure):
    database,cfg,args,_,_=recovery
    if failure=='missing_price':monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION','{}')
    if failure=='mini_price':monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'gpt-4.1':{'input':150000,'output':600000}}))
    if failure=='disabled_vision':monkeypatch.setenv('AI_AGENTS_VISION_MODELS','fixture-model')
    if failure=='nonlisted':database.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,'{models}','[]') WHERE provider='chat_ai'")
    if failure=='platform_only':database.execute("DELETE FROM saas_tenant_integrations WHERE provider='chat_ai'")
    database.commit()
    with pytest.raises(TenantLinearError):consent.prepare(database.cursor(),cfg,args)
    database.rollback()
    assert database.execute("SELECT count(*) AS n FROM saas_ai_single_message_extraction_consents WHERE message_id=%s::uuid AND photo_model<>''",(args['message_id'],))[0]['n']==0


def test_changed_download_bytes_never_reaches_provider(recovery,monkeypatch):
    database,cfg,args,_,_=recovery
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    result=complete(database,cfg,prepared,acknowledge_prior_unknown=True);claim=campaigns.claim(A,result['campaign_id'])
    download,vision,main=calls(monkeypatch)
    download.return_value=(PNG+b'changed','image/png')
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert response['request']['status']!='completed'
    vision.assert_not_called();main.assert_not_called()
    assert database.execute("SELECT state,actual_microusd FROM saas_ai_spend_reservations WHERE model='gpt-4.1'")==[{'state':'released','actual_microusd':0}]


def test_another_message_same_unknown_bytes_does_not_bypass_recovery(recovery,monkeypatch):
    database,cfg,args,_,_=recovery
    from test_knowledge_extraction_preflight import _source
    message=_source(database)
    database.execute("UPDATE saas_crm_messages SET message_type='image' WHERE id=%s::uuid",(message,));database.commit()
    prepared=consent.prepare(database.cursor(),cfg,{'message_id':message,'channel_id':CA,'lead_id':41,'photo_model':'gpt-4.1'});database.commit()
    result=complete(database,cfg,prepared);claim=campaigns.claim(A,result['campaign_id'])
    download,vision,main=calls(monkeypatch)
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert response['request']['status']!='completed'
    vision.assert_not_called();main.assert_not_called()
    assert database.execute("SELECT state,actual_microusd FROM saas_ai_spend_reservations WHERE model='gpt-4.1'")==[{'state':'released','actual_microusd':0}]


def test_photo_uses_existing_responses_wire_contract(recovery,monkeypatch):
    database,cfg,args,_,_=recovery
    # Mock HTTP itself, not the existing vision/provider implementation.
    actual_generate=RESPONSES_GENERATE
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    result=complete(database,cfg,prepared,acknowledge_prior_unknown=True);claim=campaigns.claim(A,result['campaign_id'])
    download,_,main=calls(monkeypatch)
    monkeypatch.setattr(provider,'generate',actual_generate)
    http=Mock(return_value=Mock(status_code=200,json=Mock(return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Synthetic visible amount: 7'}]}],'usage':{'input_tokens':100,'output_tokens':10,'total_tokens':110}})))
    monkeypatch.setattr(provider.requests,'post',http)
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert response['request']['status']=='completed' and http.call_count==1
    assert http.call_args.args==('https://api.openai.com/v1/responses',)
    payload=http.call_args.kwargs['json']
    assert payload['model']=='gpt-4.1' and payload['store'] is False and payload['max_output_tokens']==600
    assert payload['input'][0]['content'][1]['type']=='input_image'
    assert payload['input'][0]['content'][1]['detail']=='low'
    assert json.loads(payload['input'][0]['content'][0]['text'])['conversation']==[]
    assert http.call_args.kwargs['allow_redirects'] is False
    main.assert_not_called()
