"""Case-bound historical source inspection; no provider, production or mutations."""
import hashlib
import json
import uuid
import pytest
from unittest.mock import AsyncMock,Mock
from test_knowledge_candidates import ready,candidate
from test_tenant_support_inbox import A,B,CA,AGENT,PROFILE,db,database
from tenant_linear_policy import TenantLinearError
import tenant_case_source_inventory as inventory
import tenant_knowledge_campaigns as campaigns
import tenant_ai_learning_mcp as mcp
import tenant_case_media_extension as extension
import tenant_ai_spend_budget as spending
from tenant_media_evidence import media_version,source_hash,_body_hash


def test_continuation_scope_requires_media_scope_and_preserves_consent_description():
    with pytest.raises(TenantLinearError):
        mcp._requested_scopes(f'{mcp.TOOL_SCOPE} {mcp.SUPPORT_MEDIA_EXTEND_SCOPE}')
    scopes=mcp._requested_scopes(f'{mcp.TOOL_SCOPE} {mcp.SUPPORT_MEDIA_SCOPE} {mcp.SUPPORT_CASE_SCOPE} {mcp.SUPPORT_MEDIA_EXTEND_SCOPE}')
    description,_=mcp._consent_text(scopes)
    assert 'permanently delete' in description and 'proven not yet dispatched' in description
    definition=next(d for d in mcp._support_media_definitions() if d['name']=='support_media_case_extension_apply')
    assert definition['securitySchemes'][0]['scopes']==[mcp.SUPPORT_MEDIA_SCOPE,mcp.SUPPORT_MEDIA_EXTEND_SCOPE]


def test_historical_group_source_is_case_and_grant_bound(ready,monkeypatch):
    db,provider,_=ready;case=candidate(ready);key=case['id'];version=case['version']
    oldchannel=str(uuid.uuid4());message=str(uuid.uuid4());external='connect:'+str(uuid.uuid4());campaign=str(uuid.uuid4());group='120363430088113382@g.us'
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'historical')",(A,oldchannel))
    for channel,lead in ((CA,309),(oldchannel,308)):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,%s,'group')",(A,channel,group,lead))
    raw={'conversationId':group,'mediaId':'stable','fileLength':12}
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,raw)
      VALUES(%s::uuid,%s::uuid,308,%s,%s,'incoming','image','',%s::jsonb)""",(message,A,external,'connect:'+oldchannel,json.dumps(raw)))
    stored=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(key,))[0]['content'];stored['source_message_ids']=[message]
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(stored),key))
    ref={'id':message,'channel_id':oldchannel,'external_id':external,'media_kind':'image'}
    db.execute('UPDATE saas_knowledge_extractions SET route=%s::jsonb,source_refs=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({'provider':'connect','channel_id':CA,'conversation_id':group}),json.dumps([ref]),case['extraction_id']))
    campaigns.create_schema(db)
    policy={'allow_external_media':True,'allow_paid_asr':False,'max_main_calls':1,'max_asr_calls':0,'max_vision_calls':2}
    snapshot={'mode':'case_media_batch','case_plans':[{'case_id':key,'status':'pending_boundaries'}]}
    db.execute("""INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress)
      VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'paused',11,now(),10,100000,%s::jsonb,%s::jsonb)""",
      (A,campaign,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps(snapshot)))
    db.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,campaign,campaign));db.commit()
    spending.ensure_schema(db)
    db.execute('UPDATE saas_knowledge_campaigns SET max_cost_microusd=10000000 WHERE id=%s::uuid',(campaign,))
    for call,state,amount,actual in [('a','settled',100,100),('b','unknown',200,None)]:
        db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,actual_microusd,grant_id,state) VALUES(%s::uuid,%s::uuid,%s,'test','vision',%s,%s,%s::uuid,%s)",(A,str(uuid.uuid4()),call,amount,actual,campaign,state))
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,actual_microusd,grant_id,state) VALUES(%s::uuid,%s::uuid,'settled-no-actual','test','vision',50,NULL,%s::uuid,'settled')",(A,str(uuid.uuid4()),campaign))
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,'prior-hold','test','vision',10,'unknown')",(A,str(uuid.uuid4())))
    db.commit()
    with db.cursor() as cur:
        out=inventory.inspect(cur,PROFILE,key,message,version,campaign)
        assert out['channel_id']==oldchannel and out['lead_id']==308
        assert out['grant_scope_match'] is False and out['dispatch_authorized'] is False and out['current_bytes_checked'] is False
        preview=extension.preview(cur,PROFILE,campaign,1,[{'case_id':key,'message_id':message,'expected_version':version}])
        assert preview['sources'][0]['extension_state']=='previous_outcome_unknown'
        assert preview['budget']=={'cap_microusd':10000000,'settled_microusd':150,'held_microusd':200,'available_microusd':9999640,'prior_unknown_holds_microusd':10,'prior_settled_microusd':0}
        assert preview['execution_authorized'] is False
        with pytest.raises(TenantLinearError):extension.preview(cur,PROFILE,campaign,2,[{'case_id':key,'message_id':message,'expected_version':version}])
        cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.SUPPORT_MEDIA_SCOPE]}
        args={'case_id':key,'message_id':message,'campaign_id':campaign,'expected_version':version}
        assert mcp._tools(cur,cfg,'support_media_case_source_inspect',args)['channel_id']==oldchannel
        extension_args={'campaign_id':campaign,'expected_version':1,'sources':[{'case_id':key,'message_id':message,'expected_version':version}]}
        assert mcp._tools(cur,cfg,'support_media_case_extension_preview',extension_args)['execution_authorized'] is False
        with pytest.raises(TenantLinearError):mcp._tools(cur,{**cfg,'granted_scopes':[]},'support_media_case_extension_preview',extension_args)
        apply_args={**extension_args,'snapshot_hash':'a'*64,'request_id':str(uuid.uuid4()),'confirm_exact_preview':True}
        with pytest.raises(TenantLinearError) as denied:mcp._tools(cur,cfg,'support_media_case_extension_apply',apply_args)
        assert denied.value.status==403
        batch={'campaign_id':campaign,'sources':[{'case_id':key,'message_id':message,'expected_version':version},
          {'case_id':key,'message_id':str(uuid.uuid4()),'expected_version':version}]}
        rows=mcp._tools(cur,cfg,'support_media_case_sources_inspect',batch)['sources']
        assert len(rows)==2 and rows[0]['status']=='ok' and rows[0]['channel_id']==oldchannel
        assert rows[1]=={'status':'error','code':'source_scope_denied'}
        with pytest.raises(TenantLinearError):mcp._tools(cur,cfg,'support_media_case_sources_inspect',{'campaign_id':campaign,'sources':batch['sources']*13})
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,key,message,version+1,campaign)
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,key,str(uuid.uuid4()),version,campaign)
        with pytest.raises(TenantLinearError):inventory.inspect(cur,{'tenant_id':B,'telegram_id':22},key,message,version,campaign)
        with pytest.raises(TenantLinearError):mcp._tools(cur,{'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[]},'support_media_case_source_inspect',{})
        args={'case_id':key,'message_id':message,'campaign_id':campaign,'expected_version':version,'include_content':True}
        with pytest.raises(TenantLinearError):mcp._tools(cur,{'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.SUPPORT_MEDIA_SCOPE]},'support_media_case_source_verify',args)
    version_hash=media_version(raw);hashed=source_hash({'external_id':external,'body_hash':_body_hash(''),'media_kind':'image','media_version':version_hash})
    snapshot['case_plans']=[{'case_id':key,'case_version':version,'channel_id':oldchannel,'media':[{'id':message,'source_version':version_hash,'source_hash':hashed}]}]
    db.execute('UPDATE saas_knowledge_campaigns SET sources=%s::jsonb,progress=%s::jsonb WHERE id=%s::uuid',(json.dumps([oldchannel]),json.dumps(snapshot),campaign));db.commit()
    with db.cursor() as cur:assert inventory.inspect(cur,PROFILE,key,message,version,campaign)['grant_scope_match'] is True
    stored['source_message_ids']=[str(uuid.uuid4())]
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(stored),key))
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([]),case['extraction_id']));db.commit()
    with db.cursor() as cur:assert inventory.inspect(cur,PROFILE,key,message,version,campaign)['grant_scope_match'] is True
    stored['source_message_ids']=[message]
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(stored),key))
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([ref]),case['extraction_id']));db.commit()
    import tenant_platform,tenant_chat_attachment_store,tenant_chat_attachment_api,tenant_connect_media,tenant_knowledge_media_cache
    monkeypatch.setattr(tenant_platform,'_connect',lambda:db)
    item={'row':{'id':message},'route':{'channel_id':oldchannel,'conversation_id':group}}
    loader=Mock(return_value=item);monkeypatch.setattr(tenant_chat_attachment_store,'load',loader)
    monkeypatch.setattr(tenant_chat_attachment_api,'admit',lambda _:None)
    downloader=AsyncMock(return_value=(b'verified-image','image/png'));monkeypatch.setattr(tenant_connect_media,'download_media',downloader)
    with db.cursor() as cur:tenant_knowledge_media_cache.schema(cur)
    metadata={'owner_retry_grant':campaign,'kind':'vision','sha256':hashlib.sha256(b'verified-image').hexdigest(),'mime':'image/png','owner_retry_source':{'message_id':message,'external_id':external,'channel_id':oldchannel}}
    db.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,transcript,metadata) VALUES(%s::uuid,%s,'completed',%s::uuid,'observed label',%s::jsonb)",(A,'f'*64,str(uuid.uuid4()),json.dumps(metadata)));db.commit()
    verified=inventory.verify(PROFILE,key,message,version,campaign,include_content=True)
    assert verified['source_available'] and verified['current_bytes_checked']
    assert verified['verified_vision_observations']==['observed label'] and verified['dispatch_authorized'] is False
    assert downloader.await_count==1 and loader.call_count==2
    monkeypatch.setattr(mcp,'_connect',lambda:db)
    args={'case_id':key,'message_id':message,'campaign_id':campaign,'expected_version':version,'include_content':True}
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'actor_id':11,'granted_scopes':[mcp.SUPPORT_MEDIA_SCOPE,mcp.SUPPORT_CASE_SCOPE]}
    assert mcp._tools(None,cfg,'support_media_case_source_verify',args)['verified_vision_observations']==['observed label']
    downloader.return_value=(b'changed-image','image/png')
    assert inventory.verify(PROFILE,key,message,version,campaign)['verified_vision_result_count']==0
    loader.side_effect=[item,{**item,'row':{'id':str(uuid.uuid4())}}]
    with pytest.raises(TenantLinearError):inventory.verify(PROFILE,key,message,version,campaign)
    loader.side_effect=None
    before_calls=downloader.await_count
    def limited(_):raise TenantLinearError('limited',429)
    monkeypatch.setattr(tenant_chat_attachment_api,'admit',limited)
    with pytest.raises(TenantLinearError):inventory.verify(PROFILE,key,message,version,campaign)
    assert downloader.await_count==before_calls
    monkeypatch.setattr(tenant_chat_attachment_api,'admit',lambda _:None)
    async def revoke_during_download(*_args,**_kwargs):
        db.execute("UPDATE saas_ai_media_grants SET status='revoked' WHERE id=%s::uuid",(campaign,));db.commit()
        return b'verified-image','image/png'
    downloader.side_effect=revoke_during_download
    with pytest.raises(TenantLinearError):inventory.verify(PROFILE,key,message,version,campaign)
    downloader.side_effect=None
    db.execute("UPDATE saas_ai_media_grants SET status='active' WHERE id=%s::uuid",(campaign,));db.commit()
    from tenant_knowledge_candidates_store import digest
    audio=b'voice bytes';audio_hash=source_hash({'external_id':external,'body_hash':_body_hash(''),'media_kind':'audio','media_version':version_hash})
    db.execute("UPDATE saas_crm_messages SET message_type='audio' WHERE id=%s::uuid",(message,))
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([{**ref,'media_kind':'audio'}]),case['extraction_id']))
    snapshot['case_plans'][0]['media'][0]['source_hash']=audio_hash
    db.execute('UPDATE saas_knowledge_campaigns SET progress=%s::jsonb WHERE id=%s::uuid',(json.dumps(snapshot),campaign))
    asr_fingerprint=digest({'sha256':hashlib.sha256(audio).hexdigest(),'model':'test-asr','format':'audio/ogg'})
    source_meta={'owner_retry_grant':campaign,'owner_retry_component':'asr','artifact_fingerprint':asr_fingerprint,'owner_retry_source':{'message_id':message,'external_id':external,'channel_id':oldchannel}}
    artifact_meta={'owner_retry_component':'asr','artifact_fingerprint':asr_fingerprint,'kind':'asr','model':'test-asr','owner_retry_artifact':True}
    for fingerprint,meta in (('a'*64,source_meta),('b'*64,artifact_meta)):
        db.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,transcript,metadata) VALUES(%s::uuid,%s,'completed',%s::uuid,'saved voice',%s::jsonb)",(A,fingerprint,str(uuid.uuid4()),json.dumps(meta)))
    db.commit();downloader.return_value=(audio,'audio/ogg')
    assert inventory.verify(PROFILE,key,message,version,campaign,include_content=True)['verified_asr_transcripts']==['saved voice']
    downloader.return_value=(b'changed voice','audio/ogg')
    assert inventory.verify(PROFILE,key,message,version,campaign)['verified_asr_result_count']==0
    db.execute("UPDATE saas_crm_messages SET message_type='image' WHERE id=%s::uuid",(message,))
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([ref]),case['extraction_id']))
    snapshot['case_plans'][0]['media'][0]['source_hash']=hashed
    db.execute('UPDATE saas_knowledge_campaigns SET progress=%s::jsonb WHERE id=%s::uuid',(json.dumps(snapshot),campaign));db.commit()
    db.execute("UPDATE saas_ai_media_grants SET status='revoked' WHERE id=%s::uuid",(campaign,));db.commit()
    with db.cursor() as cur:assert inventory.inspect(cur,PROFILE,key,message,version,campaign)['grant_scope_match'] is False
    before=downloader.await_count
    with pytest.raises(TenantLinearError):inventory.verify(PROFILE,key,message,version,campaign)
    assert downloader.await_count==before
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([{**ref,'channel_id':CA}]),case['extraction_id']));db.commit()
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,key,message,version,campaign)
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([ref]),case['extraction_id']));db.commit()
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps({**raw,'conversationId':'other@g.us'}),message));db.commit()
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,key,message,version,campaign)
    provider.assert_called_once()

    # Extend only after the exact prior record proves no media dispatch.
    # The source stays paused and all original grant usage/holds are retained.
    prior_ref={**ref,'body_hash':_body_hash(''),'media_version':version_hash}
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps(raw),message))
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([prior_ref]),json.dumps([{'id':message,'status':'unavailable','code':'vision_not_approved'}]),case['extraction_id']))
    snapshot={'mode':'case_media_batch','case_plans':[{'case_id':key,'status':'pending_boundaries'}]}
    db.execute("UPDATE saas_knowledge_campaigns SET sources=%s::jsonb,progress=%s::jsonb,status='paused' WHERE id=%s::uuid",(json.dumps([CA]),json.dumps(snapshot),campaign))
    db.execute("UPDATE saas_ai_media_grants SET status='active' WHERE id=%s::uuid",(campaign,));db.commit()
    source_args=[{'case_id':key,'message_id':message,'expected_version':version}]
    with db.cursor() as cur:proposed=extension.preview(cur,PROFILE,campaign,1,source_args)
    assert proposed['sources'][0]['extension_state']=='known_not_attempted'
    req=str(uuid.uuid4())
    applied=extension.apply(PROFILE,campaign,1,source_args,proposed['snapshot_hash'],req)
    assert applied=={'replayed':False,'count':1,'status':'paused','provider_calls_started':False}
    assert extension.apply(PROFILE,campaign,1,source_args,proposed['snapshot_hash'],req)['replayed'] is True
    campaign_row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign,))[0]
    fragment=db.execute('SELECT * FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(campaign,))[0]
    assert campaign_row['status']=='paused' and campaign_row['max_cost_microusd']==10000000
    assert fragment['status']=='pending' and fragment['route']['approved_channel_ids']==sorted([CA,oldchannel])
    with db.cursor() as cur:
        assert extension.dispatch_fence(cur,A,{'id':fragment['id'],'campaign_id':campaign,'progress':campaign_row['progress'],
          'route':fragment['route'],'approved_by':11,'photo_model':campaign_row.get('photo_model')})['message_id']==message
        altered=dict(fragment['route'],case_plan_hash='0'*64)
        with pytest.raises(TenantLinearError):
            extension.dispatch_fence(cur,A,{'id':fragment['id'],'campaign_id':campaign,'progress':campaign_row['progress'],
              'route':altered,'approved_by':11,'photo_model':campaign_row.get('photo_model')})
        from tenant_case_media_admission import grant_for_message
        assert grant_for_message(cur,A,message)['id']==campaign
    assert db.execute("SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE call_key='b'")[0]=={'state':'unknown','reserved_microusd':200}
    provider.assert_called_once()
    # A real claim goes through the worker fence and stops if source identity changes.
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    db.execute("UPDATE saas_knowledge_campaigns SET status='running' WHERE id=%s::uuid",(campaign,));db.commit()
    claimed=campaigns.claim(A,campaign)
    assert claimed and str(claimed['id'])==str(fragment['id'])
    import tenant_knowledge_campaign_worker as worker
    with db.cursor() as cur:worker.check_fence(cur,A,fragment['id'])
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([{'id':message,'status':'unknown','code':'provider_timeout'}]),case['extraction_id']));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError):worker.check_fence(cur,A,fragment['id'])
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([{'id':message,'status':'unavailable','code':'vision_not_approved'}]),case['extraction_id']));db.commit()
    import tenant_knowledge_candidates_service as service
    monkeypatch.setattr(spending,'price',lambda model:(1,1))
    monkeypatch.setattr(spending,'media_price',lambda model,operation:1)
    prepared=service.prepare(PROFILE,{**claimed['route'],'request_id':str(fragment['id'])})
    assert prepared['id']==str(fragment['id'])
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({**raw,'mediaId':'changed'}),message));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError):worker.check_fence(cur,A,fragment['id'])
