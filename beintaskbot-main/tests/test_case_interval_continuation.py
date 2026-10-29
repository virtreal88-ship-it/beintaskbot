"""Saved case intervals include omitted media without rewriting old extraction refs."""
import json
import uuid
from unittest.mock import AsyncMock, Mock
import pytest
from test_knowledge_candidates import ready
from test_case_media_admission import _case, _image
from test_tenant_support_inbox import A, B, CA, AGENT, PROFILE, db, database
from test_connect_images import PNG
from tenant_linear_policy import TenantLinearError
import tenant_platform
import tenant_case_continuation_ui as owner
import tenant_case_current_media as current
import tenant_case_source_inventory as inventory
import tenant_knowledge_campaigns as campaigns
import tenant_ai_spend_budget as spend
import tenant_knowledge_candidates_service as service
import tenant_knowledge_media as media
import tenant_knowledge_media_cache as cache


@pytest.mark.parametrize('kind',['image','audio','video'])
def test_bounded_omitted_source_continues_exactly_once(ready,monkeypatch,kind,tmp_path):
    db,case,ids=_case(ready,(3,));message=ids[0];case_id=case['id'];version=case['version'];campaign=str(uuid.uuid4())
    external='connect:'+str(uuid.uuid4());raw={'conversationId':'client'}
    db.execute('UPDATE saas_crm_messages SET external_id=%s,raw=%s::jsonb,message_type=%s WHERE id=%s::uuid',(external,json.dumps(raw),kind,message))
    old=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(case['extraction_id'],))[0]['source_refs']
    assert message not in [str(ref['id']) for ref in old]
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(case_id,))[0]['content']
    content['source_message_ids']=[content['start_message_id'],content['end_message_id']]
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),case_id))
    outside=_image(db,4);db.execute("UPDATE saas_crm_messages SET happened_at='2024-03-01' WHERE id=%s::uuid",(outside,))
    other_channel=str(uuid.uuid4());foreign=_image(db,5)
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'other')",(A,other_channel))
    db.execute('UPDATE saas_crm_messages SET channel=%s WHERE id=%s::uuid',('connect:'+other_channel,foreign))
    campaigns.create_schema(db);spend.ensure_schema(db)
    with db.cursor() as cur:cache.schema(cur)
    policy={'allow_external_media':True,'allow_paid_asr':True,'max_main_calls':5,'max_asr_calls':5,'max_vision_calls':5}
    progress={'mode':'case_media_batch','case_plans':[{'case_id':case_id,'status':'pending_boundaries'}]}
    db.execute("INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress,max_cost_microusd) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'paused',11,now(),100,1000000,%s::jsonb,%s::jsonb,10000000)",(A,campaign,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps(progress)))
    db.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,campaign,campaign))
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,'old-hold','test','vision',100,'unknown')",(A,str(uuid.uuid4())));db.commit()
    for module in (tenant_platform,owner,current,campaigns,cache,spend):monkeypatch.setattr(module,'_connect',lambda:db)
    data=PNG if kind=='image' else b'synthetic audio';mime='image/png' if kind=='image' else 'audio/ogg'
    if kind=='video':
        import subprocess,shutil
        if not shutil.which('ffmpeg'):pytest.skip('ffmpeg required')
        clip=tmp_path/'synthetic.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc=size=96x96:rate=4',
          '-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-t','2','-c:v','mpeg4','-c:a','aac','-threads','1',str(clip)],check=True)
        data=clip.read_bytes();mime='video/mp4'
    monkeypatch.setattr(inventory,'_fetch_current',lambda *_:(data,mime))
    preview=owner.preview_owner(PROFILE,campaign,1)
    assert any(x['message_id']==message and x['case_id']==case_id for x in preview['sources']),preview
    assert not any(x.get('message_id')==outside for x in preview['sources'])
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,case_id,outside,version,campaign)
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,case_id,foreign,version,campaign)
        with pytest.raises(TenantLinearError):inventory.inspect(cur,{'tenant_id':B,'telegram_id':22},case_id,message,version,campaign)
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,case_id,message,version+1,campaign)
    checked=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':case_id,'message_id':message,'case_version':version})
    assert checked['attempt_reconciliation']['reason']=='attempt_history_not_recorded'
    assert checked['continuation_supported'] and checked['uncertain_retry_available']
    assert next(x for x in preview['sources'] if x['message_id']==message)['extension_state']=='previous_outcome_unknown'
    if kind=='audio':
        db.execute("INSERT INTO saas_connect_voice_receipts(tenant_id,channel_id,external_id,run_id,model,state) VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'fixture','processing')",(A,CA,external,str(uuid.uuid4())));db.commit()
        blocked=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':case_id,'message_id':message,'case_version':version})
        assert not blocked['uncertain_retry_available']
        db.execute('DELETE FROM saas_connect_voice_receipts WHERE tenant_id=%s::uuid AND external_id=%s',(A,external));db.commit()
        db.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,metadata) VALUES(%s::uuid,'legacy-asr-active','running',%s::uuid,'{}'::jsonb)",(A,str(uuid.uuid4())));db.commit()
        assert not current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':case_id,'message_id':message,'case_version':version})['uncertain_retry_available']
        db.execute("DELETE FROM saas_knowledge_media_cache WHERE fingerprint='legacy-asr-active'");db.commit()
    args={'expected_version':1,'source_refs':preview['source_refs'],'snapshot_hash':preview['snapshot_hash'],'request_id':str(uuid.uuid4()),'confirm_exact_preview':True,
      'current_byte_sources':[{'case_id':case_id,'message_id':message,'case_version':version,'sha256':checked['current_sha256']}],'acknowledge_unknown_selected':True}
    with pytest.raises(TenantLinearError):owner.apply_owner(PROFILE,campaign,{**args,'acknowledge_unknown_selected':False})
    assert owner.apply_owner(PROFILE,campaign,args)['count']==1
    assert db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(case['extraction_id'],))[0]['source_refs']==old
    monkeypatch.setattr(spend,'price',lambda *_:(1,1));monkeypatch.setattr(spend,'media_price',lambda *_:1)
    monkeypatch.setattr(media,'load',lambda *a:{'provider':'connect','route':{'channel_id':CA,'conversation_id':'client'},'row':{'message_type':kind}})
    monkeypatch.setattr(media,'download_media',AsyncMock(return_value=(data,mime)));monkeypatch.setattr(media,'supports_images',lambda *_:True)
    import tenant_chat_ai_provider as provider
    if kind=='image':
        call=Mock(return_value=('synthetic image observation',{'input_tokens':1,'output_tokens':1}));monkeypatch.setattr(provider,'generate',call)
    elif kind=='audio':
        import tenant_connect_voice as voice
        monkeypatch.setattr(voice,'validate_audio',lambda *a,**k:(data,'ogg',mime));monkeypatch.setattr(voice,'transcription_model',lambda *_:'synthetic-asr')
        call=Mock(return_value=('synthetic transcript',{'input_tokens':1,'output_tokens':1}));monkeypatch.setattr(provider,'transcribe',call)
    else:
        import tenant_connect_voice as voice
        import tenant_ai_video_model as video_model
        import tenant_case_uncertain_retry as retry
        import hashlib
        excluded=str(uuid.uuid4());pending_id=str(uuid.uuid4());unknown_id=str(uuid.uuid4())
        monkeypatch.setattr(retry,'_EXCLUDED_UNKNOWN_VIDEO_HASH',hashlib.sha256(excluded.encode()).hexdigest())
        with db.cursor() as cur:assert not retry.eligible(cur,A,{'kind':'video','message_id':excluded},checked['current_sha256'])
        route={'channel_id':CA,'message_ids':[excluded],'source_version':'a'*64}
        for fragment,status in ((pending_id,'pending'),(unknown_id,'unknown')):
            db.execute("INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route,status,created_at) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb,%s,'2020-01-01')",(A,campaign,fragment,fragment,json.dumps(route),status))
        progress=db.execute('SELECT progress FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign,))[0]['progress'];progress['failed_media_recovery']={'parents':{}}
        db.execute('UPDATE saas_knowledge_campaigns SET progress=%s::jsonb WHERE id=%s::uuid',(json.dumps(progress),campaign));db.commit()
        monkeypatch.setattr(voice,'transcription_model',lambda *_:'synthetic-asr')
        monkeypatch.setattr(video_model,'require_video_tariff',lambda *_:None)
        call=Mock(return_value=('synthetic frame observation',{'input_tokens':1,'output_tokens':1}));monkeypatch.setattr(provider,'generate',call)
        audio_call=Mock(return_value=('synthetic video transcript',{'input_tokens':1,'output_tokens':1},{'speech_state':'confident','language':'az'}));monkeypatch.setattr(provider,'transcribe_video',audio_call)
    campaigns.control(PROFILE,campaign,2,'resume');claimed=campaigns.claim(A,campaign)
    if kind=='video':assert str(claimed['id']) not in {pending_id,unknown_id}
    response=service.analyze(PROFILE,{**claimed['route'],'request_id':str(claimed['id'])});campaigns.complete(claimed,response)
    assert call.call_count==(2 if kind=='video' else 1)
    if kind=='video':assert audio_call.call_count==1
    if kind=='video':
        states={str(row['id']):row['status'] for row in db.execute('SELECT id,status FROM saas_knowledge_campaign_fragments WHERE id IN (%s::uuid,%s::uuid)',(pending_id,unknown_id))}
        assert states=={pending_id:'pending',unknown_id:'unknown'}
    latest=db.execute('SELECT version FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign,))[0]['version']
    result=current.verify_owner_source(PROFILE,campaign,{'expected_version':latest,'case_id':case_id,'message_id':message,'case_version':version})
    assert ('synthetic frame observation' if kind=='video' else 'synthetic image observation' if kind=='image' else 'synthetic transcript') in result['saved_byte_results'][0]['text']
    if kind=='video':assert 'synthetic video transcript' in result['saved_byte_results'][0]['text']
    db.execute('UPDATE saas_knowledge_candidates SET version=version+1 WHERE id=%s::uuid',(case_id,));db.commit()
    moved=current.verify_owner_source(PROFILE,campaign,{'expected_version':latest,'case_id':case_id,'message_id':message,'case_version':version+1})
    assert moved['saved_byte_results'][0]['text']==result['saved_byte_results'][0]['text']
    if kind=='audio':
        db.execute("UPDATE saas_knowledge_extractions SET media_results='[]'::jsonb WHERE id=%s::uuid",(claimed['id'],));db.commit()
        cached=current.verify_owner_source(PROFILE,campaign,{'expected_version':latest,'case_id':case_id,'message_id':message,'case_version':version+1})
        assert cached['saved_byte_results'][0]['text']=='synthetic transcript'
        assert cached['uncertain_retry_available'] is False
        assert call.call_count==1
    assert db.execute("SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE call_key='old-hold'")[0]=={'state':'unknown','reserved_microusd':100}
    assert db.execute('SELECT cap_microusd FROM saas_ai_media_grants WHERE id=%s::uuid',(campaign,))[0]['cap_microusd']==10000000


def test_reviewed_photo_and_new_audio_endpoint_remain_in_verified_interval(ready,monkeypatch):
    import tenant_knowledge_candidates_store as store
    from tenant_media_evidence import media_version
    db,case,ids=_case(ready,(3,4));photo,audio=ids;case_id=case['id'];campaign=str(uuid.uuid4())
    audio_external='connect:'+str(uuid.uuid4())
    db.execute("UPDATE saas_crm_messages SET message_type='audio',external_id=%s,raw=%s::jsonb WHERE id=%s::uuid",(audio_external,json.dumps({'conversationId':'client'}),audio))
    photo_raw={'mediaId':'new-photo','conversationId':'client'}
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps(photo_raw),photo))
    old=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(case['extraction_id'],))[0]['source_refs']
    photo_row=db.execute('SELECT external_id,body FROM saas_crm_messages WHERE id=%s::uuid',(photo,))[0]
    old_photo={'id':photo,'channel_id':CA,'lead_id':41,'external_id':photo_row['external_id'],'body_hash':store.digest(photo_row['body']),
               'media_kind':'image','media_version':'0'*64,'role':'customer','text_limit':2000}
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',(json.dumps(old+[old_photo]),case['extraction_id']))
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(case_id,))[0]['content']
    text_id=content['start_message_id'];content['source_message_ids']=[text_id,photo]
    db.execute('UPDATE saas_crm_messages SET happened_at=%s::timestamptz WHERE id=%s::uuid',('2024-01-03T00:00:01Z',text_id))
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),case_id));db.commit()
    reviewed=store.mutate(PROFILE,case_id,{'expected_version':case['version'],'message_id':photo,
        'review_note':'Reviewed the current attachment as a separate source.','acknowledge_new_source':True},'source_review')
    assert reviewed['candidate']['version']==case['version']+1
    bounded=store.mutate(PROFILE,case_id,{'expected_version':case['version']+1,'start_message_id':photo,'end_message_id':audio},'boundaries')
    assert bounded['candidate']['version']==case['version']+2
    assert bounded['candidate']['media_coverage']['boundary_status']=='verified'
    assert bounded['candidate']['source_claims_unverified'] is True
    assert bounded['candidate']['outcome']['status']=='unconfirmed'
    assert {x['id'] for x in bounded['candidate']['media_processing']} >= {photo,audio}
    campaigns.create_schema(db);spend.ensure_schema(db)
    policy={'allow_external_media':True,'allow_paid_asr':True,'max_main_calls':5,'max_asr_calls':5,'max_vision_calls':5}
    progress={'mode':'case_media_batch','case_plans':[{'case_id':case_id,'status':'pending_boundaries'}]}
    db.execute("INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress,max_cost_microusd) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'paused',11,now(),100,1000000,%s::jsonb,%s::jsonb,10000000)",(A,campaign,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps(progress)))
    db.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,campaign,campaign));db.commit()
    monkeypatch.setattr(owner,'_connect',lambda:db)
    with db.cursor() as cur:assert inventory.inspect(cur,PROFILE,case_id,audio,case['version']+2,campaign)['interval_only'] is True
    preview=owner.preview_owner(PROFILE,campaign,1)
    assert any(x['message_id']==audio and x['case_id']==case_id for x in preview['sources'])
    assert db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(case['extraction_id'],))[0]['source_refs']==old+[old_photo]


def test_group_cross_channel_endpoints_are_not_treated_as_verified_without_proof(ready):
    import tenant_knowledge_candidates_store as store
    db,case,ids=_case(ready,(3,));case_id=case['id'];campaign=str(uuid.uuid4());other=str(uuid.uuid4())
    group='120363000000000000@g.us';foreign=str(uuid.uuid4());start=str(uuid.uuid4())
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'historical')",(A,other))
    db.execute('UPDATE saas_crm_messages SET kommo_lead_id=410 WHERE tenant_id=%s::uuid AND kommo_lead_id=41',(A,))
    for channel,lead in ((CA,410),(other,411)):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,%s,'group')",(A,channel,group,lead))
    for message,at in ((start,'2024-01-02T00:00:00Z'),(foreign,'2024-01-04T00:00:00Z')):
        db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,411,%s,%s,'incoming','','image',%s::timestamptz)",(A,message,'connect:'+str(uuid.uuid4()),'connect:'+other,at))
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(case_id,))[0]['content']
    content['start_message_id']=start
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),case_id))
    route={'provider':'connect','channel_id':CA,'conversation_id':group,'approved_channel_ids':[CA,other]}
    db.execute('UPDATE saas_knowledge_extractions SET route=%s::jsonb WHERE id=%s::uuid',(json.dumps(route),case['extraction_id']))
    campaigns.create_schema(db)
    db.execute("INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress,max_cost_microusd) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'paused',11,now(),100,1000000,'{}'::jsonb,%s::jsonb,10000000)",(A,campaign,json.dumps([CA,other]),AGENT,json.dumps({'mode':'case_media_batch','case_plans':[{'case_id':case_id}]})))
    db.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,campaign,campaign));db.commit()
    with db.cursor() as cur:
        run=store.extraction_row(cur,A,case['extraction_id']);chosen=store.assert_route(cur,A,store.authorize(cur,PROFILE)[2],'connect',group,CA)
        assert store.case_media_coverage(cur,A,run,chosen,content)['media_coverage']['boundary_status']=='uncertain'
        with pytest.raises(TenantLinearError):inventory.inspect(cur,PROFILE,case_id,foreign,case['version'],campaign)


def test_video_frame_unknown_cannot_be_claimed_again(ready):
    from unittest.mock import Mock
    from tenant_knowledge_media_cache import _batch_retry_fingerprint, _claim_batch_retry
    db,case,ids=_case(ready,(3,));message=ids[0];grant=str(uuid.uuid4());external='connect:'+str(uuid.uuid4())
    ref={'id':message,'channel_id':CA,'external_id':external,'media_kind':'video','media_version':'a'*64}
    item={'route':{'owner_retry_unknown':True,'media_grant_id':grant,'message_ids':[message],'channel_id':CA,'source_version':'a'*64}}
    with db.cursor() as cur:cache.schema(cur)
    first=_batch_retry_fingerprint(item,'b'*64,'vision-frame:0',ref)
    second=_batch_retry_fingerprint(item,'c'*64,'vision-frame:1',ref)
    assert first[0]!=second[0]
    meta={'owner_retry_component':'vision','owner_retry_source':second[2],'artifact_fingerprint':'c'*64}
    token=str(uuid.uuid4())
    with db.cursor() as cur:assert _claim_batch_retry(cur,A,second[0],second[1],token,meta,meta)[0]=='claimed'
    db.execute("UPDATE saas_knowledge_media_cache SET state='unknown' WHERE tenant_id=%s::uuid AND fingerprint IN (%s,%s)",(A,second[0],second[1]));db.commit()
    external_call=Mock()
    with pytest.raises(TenantLinearError):
        with db.cursor() as cur:
            _claim_batch_retry(cur,A,second[0],second[1],str(uuid.uuid4()),meta,meta)
            external_call()
    external_call.assert_not_called()
