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
        assert preview['budget']=={'cap_microusd':10000000,'settled_microusd':150,'held_microusd':200,'available_microusd':9999640,'prior_unknown_holds_microusd':10,'prior_settled_microusd':0,'released_counted_microusd':0}
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
    # Old extractions recorded an internal UUID and channel without the later
    # attachment fields. Verify the actual route, do not infer historical bytes.
    legacy={'id':message,'channel_id':oldchannel,'body_hash':_body_hash(''),'text_limit':68}
    unrelated=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,raw) VALUES(%s::uuid,%s::uuid,308,%s,%s,'incoming','image','',%s::jsonb)",(unrelated,A,'connect:'+str(uuid.uuid4()),'connect:'+oldchannel,json.dumps(raw)))
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([legacy,{'id':unrelated,'channel_id':oldchannel}]),case['extraction_id']));db.commit()
    with db.cursor() as cur:
        actual=inventory.inspect(cur,PROFILE,key,message,version,campaign)
        assert actual['legacy_source_reference'] is True and actual['channel_id']==oldchannel
        assert actual['kind']=='image' and actual['current_source_addressable'] is True
        assert actual['current_bytes_checked'] is False and actual['dispatch_authorized'] is False
        state=extension.preview(cur,PROFILE,campaign,1,[{'case_id':key,'message_id':message,'expected_version':version}])
        assert state['sources'][0]['extension_state']=='previous_outcome_unknown'
        with pytest.raises(TenantLinearError) as outside:inventory.inspect(cur,PROFILE,key,unrelated,version,campaign)
        assert outside.value.status==403
    # Owner's existing case-bound read fetches the current asset through the
    # protected loader even though the old extraction lacks a media version.
    # It never asserts old-byte equality or starts an AI call.
    import tenant_case_current_media as current_media
    import tenant_chat_attachment_store,tenant_chat_attachment_api,tenant_connect_media,tenant_connect_transport
    from cryptography.fernet import Fernet
    cipher=Fernet(Fernet.generate_key())
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)",(A,cipher.encrypt(b'{"api_key":"synthetic-key"}')));db.commit()
    db.execute("UPDATE saas_connect_channels SET active=FALSE,status='disconnected' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,oldchannel));db.commit()
    with monkeypatch.context() as mp:
        mp.setattr(current_media,'_connect',lambda:db)
        mp.setattr(tenant_chat_attachment_store,'_connect',lambda:db)
        mp.setattr(tenant_connect_transport,'_fernet',lambda:cipher)
        real_load=tenant_chat_attachment_store.load
        item=real_load(PROFILE,308,external,channel_id=oldchannel,conversation_id=group)
        loader=Mock(wraps=real_load);mp.setattr(tenant_chat_attachment_store,'load',loader)
        mp.setattr(tenant_chat_attachment_api,'admit',lambda _:None)
        fetch=AsyncMock(return_value=(b'\x89PNG\r\n\x1a\ncurrent-image','image/png'));mp.setattr(tenant_connect_media,'download_media',fetch)
        request={'expected_version':1,'case_id':key,'message_id':message,'case_version':version}
        checked=current_media.verify_owner_source(PROFILE,campaign,request)
        assert checked['current_sha256']==hashlib.sha256(b'\x89PNG\r\n\x1a\ncurrent-image').hexdigest()
        assert checked['mime_type']=='image/png' and checked['content_signature_checked'] is True and checked['previous_bytes_verified'] is False and checked['provider_ai_calls']==0
        assert loader.call_count==2 and fetch.await_count==1
        for _ in range(3):assert current_media._READ_SLOTS.acquire(blocking=False)
        try:
            with pytest.raises(TenantLinearError) as busy:current_media.verify_owner_source(PROFILE,campaign,request)
            assert busy.value.status==429 and busy.value.code=='owner_read_busy' and fetch.await_count==1
        finally:
            for _ in range(3):current_media._READ_SLOTS.release()
        with pytest.raises(TenantLinearError):current_media.verify_owner_source(PROFILE,campaign,{**request,'message_id':unrelated})
        with pytest.raises(TenantLinearError):current_media.verify_owner_source({'tenant_id':B,'telegram_id':22},campaign,request)
        with pytest.raises(TenantLinearError):current_media.verify_owner_source(PROFILE,campaign,{**request,'case_version':version+1})
        fetch.side_effect=TenantLinearError('sensitive provider detail',502)
        with pytest.raises(TenantLinearError) as failed:current_media.verify_owner_source(PROFILE,campaign,request)
        assert failed.value.code=='connect_fetch_failed' and 'sensitive' not in str(failed.value)
        fetch.side_effect=TenantLinearError('sensitive provider path',404)
        with pytest.raises(TenantLinearError) as missing:current_media.verify_owner_source(PROFILE,campaign,request)
        assert missing.value.status==404 and missing.value.code=='connect_source_unavailable' and 'sensitive' not in str(missing.value)
        denied=TenantLinearError('provider secret',502);denied.code='connect_access_denied';fetch.side_effect=denied
        with pytest.raises(TenantLinearError) as denied_read:current_media.verify_owner_source(PROFILE,campaign,request)
        assert denied_read.value.status==403 and denied_read.value.code=='connect_access_denied' and 'secret' not in str(denied_read.value)
        fetch.side_effect=None;fetch.return_value=(b'not-a-real-image','image/png')
        with pytest.raises(TenantLinearError) as invalid:current_media.verify_owner_source(PROFILE,campaign,request)
        assert invalid.value.status==415 and invalid.value.code=='connect_format_unsupported'
        async def changed(*args):
            db.execute('UPDATE saas_knowledge_candidates SET version=version+1 WHERE id=%s::uuid',(key,));db.commit()
            return b'\x89PNG\r\n\x1a\ncurrent-image','image/png'
        fetch.side_effect=changed
        with pytest.raises(TenantLinearError):current_media.verify_owner_source(PROFILE,campaign,request)
        db.execute('UPDATE saas_knowledge_candidates SET version=version-1 WHERE id=%s::uuid',(key,));db.commit()
        fetch.side_effect=None;loader.side_effect=[item,{**item,'row':{'id':str(uuid.uuid4())}}]
        with pytest.raises(TenantLinearError):current_media.verify_owner_source(PROFILE,campaign,request)
        assert db.execute("SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE call_key='b'")[0]=={'state':'unknown','reserved_microusd':200}
    db.execute("UPDATE saas_connect_channels SET active=TRUE,status='connected' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,oldchannel));db.commit()
    for altered in ({**legacy,'external_id':'wrong'}, {**legacy,'media_kind':'video'}, {**legacy,'channel_id':CA}):
        db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([altered]),case['extraction_id']));db.commit()
        with db.cursor() as cur,pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,key,message,version,campaign)
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps([ref]),case['extraction_id']));db.commit()
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
    import tenant_case_continuation_ui as owner_ui
    monkeypatch.setattr(owner_ui,'_connect',lambda:db)
    owner_preview=owner_ui.preview_owner(PROFILE,campaign,1)
    assert owner_preview['source_refs']==source_args and owner_preview['snapshot_hash']==proposed['snapshot_hash']
    with pytest.raises(TenantLinearError):owner_ui.preview_owner({'tenant_id':B,'telegram_id':22},campaign,1)
    with pytest.raises(TenantLinearError):owner_ui.preview_owner(PROFILE,campaign,2)
    req=str(uuid.uuid4())
    data={'expected_version':1,'source_refs':source_args,'snapshot_hash':proposed['snapshot_hash'],'request_id':req,'confirm_exact_preview':True}
    with pytest.raises(TenantLinearError):owner_ui.apply_owner(PROFILE,campaign,{**data,'confirm_exact_preview':False})
    with pytest.raises(TenantLinearError):owner_ui.apply_owner(PROFILE,campaign,{**data,'source_refs':[{**source_args[0],'case_id':str(uuid.uuid4())}]})
    db.execute('UPDATE saas_ai_media_grants SET approved_by=22 WHERE id=%s::uuid',(campaign,));db.commit()
    with pytest.raises(TenantLinearError):owner_ui.preview_owner(PROFILE,campaign,1)
    with pytest.raises(TenantLinearError):owner_ui.apply_owner(PROFILE,campaign,data)
    db.execute('UPDATE saas_ai_media_grants SET approved_by=11 WHERE id=%s::uuid',(campaign,));db.commit()
    applied=owner_ui.apply_owner(PROFILE,campaign,data)
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


def test_verified_case_interval_allows_only_matching_current_channel_media(ready,monkeypatch):
    db,provider,_=ready;case=candidate(ready);key=case['id'];version=case['version'];campaign=str(uuid.uuid4())
    run=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(case['extraction_id'],))[0]
    refs=run['source_refs'];assert len(refs)>=2
    ordered=db.execute("SELECT id::text AS id,COALESCE(happened_at,created_at) AS at FROM saas_crm_messages WHERE id=ANY(%s::uuid[]) ORDER BY COALESCE(happened_at,created_at),id",([r['id'] for r in refs],))
    start,end=ordered[0]['id'],ordered[-1]['id'];inside=str(uuid.uuid4());outside=str(uuid.uuid4())
    for selected,interval in ((inside,"interval '0.1 second'"),(outside,"interval '-1 second'")):
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,raw,happened_at) SELECT %s::uuid,%s::uuid,41,%s,%s,'incoming','image','',%s::jsonb,COALESCE(happened_at,created_at)+"+interval+" FROM saas_crm_messages WHERE id=%s::uuid",(selected,A,'connect:'+str(uuid.uuid4()),'connect:'+CA,json.dumps({'conversationId':'client','mediaId':'stable'}),start))
    new_refs=list(refs)
    for selected in (inside,outside):
        row=db.execute('SELECT external_id FROM saas_crm_messages WHERE id=%s::uuid',(selected,))[0]
        new_refs.append({'id':selected,'channel_id':CA,'external_id':row['external_id'],'media_kind':'image'})
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps(new_refs),case['extraction_id']))
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(key,))[0]['content']
    content.update(start_message_id=start,end_message_id=end,source_message_ids=[r['id'] for r in refs])
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),key))
    campaigns.create_schema(db)
    policy={'allow_external_media':True,'allow_paid_asr':False,'max_main_calls':1,'max_asr_calls':0,'max_vision_calls':2}
    progress={'mode':'case_media_batch','case_plans':[{'case_id':key,'status':'pending_boundaries'}]}
    db.execute("""INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress,max_cost_microusd) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'paused',11,now(),10,100000,%s::jsonb,%s::jsonb,10000000)""",(A,campaign,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps(progress)))
    db.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,campaign,campaign));db.commit()
    spending.ensure_schema(db);db.commit()
    with db.cursor() as cur:
        checked=inventory.inspect(cur,PROFILE,key,inside,version,campaign)
        assert checked['message_id']==inside and checked['grant_scope_match'] is False and checked['dispatch_authorized'] is False
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,key,outside,version,campaign)
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,key,inside,version+1,campaign)
        with pytest.raises(TenantLinearError):inventory.inspect(cur,{'tenant_id':B,'telegram_id':22},key,inside,version,campaign)
    import tenant_case_continuation_ui as owner_ui
    monkeypatch.setattr(owner_ui,'_connect',lambda:db)
    result=owner_ui.preview_owner(PROFILE,campaign,1)
    assert any(row['message_id']==inside for row in result['sources'])
    assert any(row['message_id']==outside and row['reason']=='outside_verified_case' for row in result['blocked'])
    assert all(row['extension_state']!='known_not_attempted' for row in result['sources'])
    content['start_message_id']=None
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),key));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,key,inside,version,campaign)
    provider.assert_called_once()
