"""Current bytes are admitted only with positive saved no-call evidence."""
import hashlib
import json
import uuid
import pytest
from test_knowledge_candidates import ready
from test_case_media_admission import _case
from test_tenant_support_inbox import A,CA,AGENT,PROFILE,db,database
from test_connect_images import PNG
import tenant_knowledge_candidates_store as store
import tenant_knowledge_campaigns as campaigns
import tenant_case_current_media as current
import tenant_case_continuation_ui as owner
import tenant_case_media_extension as extension
import tenant_case_source_inventory as inventory
import tenant_ai_spend_budget as spend
from tenant_media_evidence import media_version
from tenant_linear_policy import TenantLinearError


def test_current_bytes_queue_and_dispatch_fences(ready,monkeypatch):
    db,case,ids=_case(ready,(3,));message=ids[0];key=case['id'];version=case['version'];campaign=str(uuid.uuid4())
    raw={'conversationId':'client'};external='connect:'+str(uuid.uuid4())
    db.execute('UPDATE saas_crm_messages SET external_id=%s,raw=%s::jsonb WHERE id=%s::uuid',(external,json.dumps(raw),message))
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(key,))[0]['content']
    content['source_message_ids'].append(message)
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),key))
    row=db.execute('SELECT body FROM saas_crm_messages WHERE id=%s::uuid',(message,))[0]
    ref={'id':message,'channel_id':CA,'external_id':external,'media_kind':'image','media_version':media_version(raw),'body_hash':store.digest(row['body']),'role':'customer'}
    old=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(case['extraction_id'],))[0]['source_refs']
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps(old+[ref]),json.dumps([{'id':message,'status':'unavailable','code':'vision_not_approved'}]),case['extraction_id']))
    campaigns.create_schema(db);spend.ensure_schema(db)
    policy={'allow_external_media':True,'allow_paid_asr':False,'max_main_calls':5,'max_asr_calls':0,'max_vision_calls':5}
    progress={'mode':'case_media_batch','case_plans':[{'case_id':key,'status':'pending_boundaries'}]}
    db.execute("""INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress,max_cost_microusd) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'paused',11,now(),100,1000000,%s::jsonb,%s::jsonb,10000000)""",(A,campaign,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps(progress)))
    db.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,campaign,campaign));db.commit()
    import tenant_platform
    monkeypatch.setattr(tenant_platform,'_connect',lambda:db);monkeypatch.setattr(owner,'_connect',lambda:db);monkeypatch.setattr(current,'_connect',lambda:db)
    monkeypatch.setattr(inventory,'_fetch_current',lambda *_:(PNG,'image/png'))
    refs=[{'case_id':key,'message_id':message,'expected_version':version}]
    with db.cursor() as cur:preview=extension.preview(cur,PROFILE,campaign,1,refs)
    assert preview['sources'][0]['extension_state']=='source_version_unverified'
    value=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
    assert value['current_sha256']==hashlib.sha256(PNG).hexdigest()
    assert value['attempt_reconciliation']['state']=='known_not_attempted'
    db.execute("UPDATE saas_knowledge_extractions SET status='running' WHERE id=%s::uuid",(case['extraction_id'],));db.commit()
    uncertain=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
    assert uncertain['attempt_reconciliation']['reason']=='prior_extraction_unfinished'
    db.execute("UPDATE saas_knowledge_extractions SET status='completed' WHERE id=%s::uuid",(case['extraction_id'],));db.commit()
    db.execute("INSERT INTO saas_connect_voice_receipts(tenant_id,channel_id,external_id,run_id,model,state) VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'fixture','processing')",(A,CA,external,str(uuid.uuid4())));db.commit()
    uncertain=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
    assert uncertain['attempt_reconciliation']['reason']=='prior_voice_receipt'
    db.execute('DELETE FROM saas_connect_voice_receipts WHERE tenant_id=%s::uuid AND external_id=%s',(A,external));db.commit()
    import tenant_knowledge_media_cache as cache
    with db.cursor() as cur:cache.schema(cur)
    db.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,metadata) VALUES(%s::uuid,'fixture-source','unknown',%s::uuid,%s::jsonb)",(A,str(uuid.uuid4()),json.dumps({'owner_retry_source':{'message_id':message}})));db.commit()
    uncertain=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
    assert uncertain['attempt_reconciliation']['reason']=='prior_source_cache'
    db.execute("DELETE FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND fingerprint='fixture-source'",(A,));db.commit()
    cached={'owner_retry_source':{'message_id':message,'channel_id':CA},'kind':'vision','sha256':value['current_sha256']}
    db.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,metadata,transcript) VALUES(%s::uuid,'fixture-source','completed',%s::uuid,%s::jsonb,'previous exact result')",(A,str(uuid.uuid4()),json.dumps(cached)));db.commit()
    reused=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
    assert reused['attempt_reconciliation']['reason']=='prior_source_cache'
    assert reused['saved_byte_results']==[{'status':'processed','text':'previous exact result'}]
    db.execute("DELETE FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND fingerprint='fixture-source'",(A,));db.commit()
    competing=str(uuid.uuid4())
    db.execute('INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb)',
      (A,campaign,competing,competing,json.dumps({'message_ids':[message]})));db.commit()
    blocked=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
    assert blocked['attempt_reconciliation']['reason']=='prior_fragment_exists'
    db.execute('DELETE FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid',(competing,));db.commit()
    args={'expected_version':1,'source_refs':refs,'snapshot_hash':preview['snapshot_hash'],'request_id':str(uuid.uuid4()),'confirm_exact_preview':True,
      'current_byte_sources':[{'case_id':key,'message_id':message,'case_version':version,'sha256':value['current_sha256']}]}
    result=owner.apply_owner(PROFILE,campaign,args)
    assert result['count']==1 and result['provider_calls_started'] is False
    saved=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign,))[0]
    fragment=db.execute('SELECT * FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(campaign,))[0]
    route=fragment['route'];assert route['case_source_sha256']==value['current_sha256']
    fence={**fragment,'campaign_id':campaign,'progress':saved['progress'],'approved_by':11,'photo_model':None}
    with db.cursor() as cur:assert extension.dispatch_fence(cur,A,fence)['message_id']==message
    # Newly discovered provider activity must invalidate a queued snapshot.
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([{'id':message,'status':'unknown','code':'media_result_unknown'}]),case['extraction_id']));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError):extension.dispatch_fence(cur,A,fence)
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([{'id':message,'status':'unavailable','code':'vision_not_approved'}]),case['extraction_id']));db.commit()
    # Real worker admission preserves the hash through the saved route.
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_campaign_worker as worker
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setattr(spend,'price',lambda model:(1,1))
    monkeypatch.setattr(spend,'media_price',lambda model,operation:1)
    db.execute("UPDATE saas_knowledge_campaigns SET status='running' WHERE id=%s::uuid",(campaign,));db.commit()
    claimed=campaigns.claim(A,campaign)
    with db.cursor() as cur:worker.check_fence(cur,A,fragment['id'])
    prepared=service.prepare(PROFILE,{**claimed['route'],'request_id':str(fragment['id'])})
    assert prepared['route']['case_source_sha256']==value['current_sha256']
    db.execute('UPDATE saas_knowledge_extractions SET status=%s,media_results=%s::jsonb WHERE id=%s::uuid',
      ('completed',json.dumps([{'id':message,'status':'processed','observations':[{'text':'visible printer'}]}]),fragment['id']))
    db.execute("UPDATE saas_knowledge_campaign_fragments SET status='completed',extraction_id=id WHERE id=%s::uuid",(fragment['id'],));db.commit()
    checked=current.verify_owner_source(PROFILE,campaign,{'expected_version':2,'case_id':key,'message_id':message,'case_version':version})
    assert checked['saved_byte_results']==[{'status':'processed','text':'visible printer'}]
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([{'id':message,'status':'processed','sha256':value['current_sha256'],'observation':'older exact bytes'}]),case['extraction_id']));db.commit()
    checked=current.verify_owner_source(PROFILE,campaign,{'expected_version':2,'case_id':key,'message_id':message,'case_version':version})
    assert {row['text'] for row in checked['saved_byte_results']}=={'visible printer','older exact bytes'}
    monkeypatch.setattr(inventory,'_fetch_current',lambda *_:(PNG+b'changed','image/png'))
    checked=current.verify_owner_source(PROFILE,campaign,{'expected_version':2,'case_id':key,'message_id':message,'case_version':version})
    assert checked['saved_byte_results']==[]
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps({'conversationId':'client','mediaId':'changed'}),message));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError):extension.dispatch_fence(cur,A,fence)


@pytest.mark.parametrize('old_ref_format',['missing','stale','stale_no_call'])
def test_legacy_incomplete_history_requires_owner_ack_and_blocks_recorded_attempt(ready,monkeypatch,old_ref_format):
    db,case,ids=_case(ready,(3,));message=ids[0];key=case['id'];version=case['version'];campaign=str(uuid.uuid4())
    external='connect:'+str(uuid.uuid4());raw={'conversationId':'client'}
    db.execute('UPDATE saas_crm_messages SET external_id=%s,raw=%s::jsonb WHERE id=%s::uuid',(external,json.dumps(raw),message))
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(key,))[0]['content'];content['source_message_ids'].append(message)
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),key))
    old=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(case['extraction_id'],))[0]['source_refs']
    ref={'id':message,'channel_id':CA,'lead_id':41,'role':'customer'}
    if old_ref_format in {'stale','stale_no_call'}:ref.update(external_id=external,media_kind='image',media_version='0'*64)
    prior_results=[{'id':message,'kind':'image','status':'unavailable','code':'vision_not_approved'}] if old_ref_format=='stale_no_call' else []
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE id=%s::uuid',(json.dumps(old+[ref]),json.dumps(prior_results),case['extraction_id']))
    campaigns.create_schema(db);spend.ensure_schema(db)
    policy={'allow_external_media':True,'allow_paid_asr':False,'max_main_calls':5,'max_asr_calls':0,'max_vision_calls':5}
    progress={'mode':'case_media_batch','case_plans':[{'case_id':key,'status':'pending_boundaries'}]}
    db.execute("""INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress,max_cost_microusd) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'paused',11,now(),100,1000000,%s::jsonb,%s::jsonb,10000000)""",(A,campaign,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps(progress)))
    db.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,campaign,campaign));db.commit()
    import tenant_platform
    monkeypatch.setattr(tenant_platform,'_connect',lambda:db);monkeypatch.setattr(owner,'_connect',lambda:db);monkeypatch.setattr(current,'_connect',lambda:db)
    monkeypatch.setattr(inventory,'_fetch_current',lambda *_:(PNG,'image/png'))
    refs=[{'case_id':key,'message_id':message,'expected_version':version}]
    with db.cursor() as cur:preview=extension.preview(cur,PROFILE,campaign,1,refs)
    value=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
    assert value['attempt_reconciliation']['state']=='incomplete_history_eligible'
    if old_ref_format=='stale_no_call':
        mismatch=[{'id':message,'kind':'video','status':'unavailable','code':'vision_not_approved'}]
        db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',(json.dumps(mismatch),case['extraction_id']));db.commit()
        blocked=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
        assert blocked['attempt_reconciliation']['state']=='unknown'
        wrong_reason=[{'id':message,'kind':'image','status':'unavailable','code':'asr_authorization_required'}]
        db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',(json.dumps(wrong_reason),case['extraction_id']));db.commit()
        blocked=current.verify_owner_source(PROFILE,campaign,{'expected_version':1,'case_id':key,'message_id':message,'case_version':version})
        assert blocked['attempt_reconciliation']['state']=='unknown'
        db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',(json.dumps(prior_results),case['extraction_id']));db.commit()
    args={'expected_version':1,'source_refs':refs,'snapshot_hash':preview['snapshot_hash'],'request_id':str(uuid.uuid4()),'confirm_exact_preview':True,
      'current_byte_sources':[{'case_id':key,'message_id':message,'case_version':version,'sha256':value['current_sha256']}]}
    with pytest.raises(TenantLinearError,match='unknown'):owner.apply_owner(PROFILE,campaign,args)
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',(json.dumps([{'id':message,'status':'unknown','code':'media_result_unknown'}]),case['extraction_id']));db.commit()
    with pytest.raises(TenantLinearError,match='unknown'):owner.apply_owner(PROFILE,campaign,{**args,'acknowledge_incomplete_history':True})
    db.execute("UPDATE saas_knowledge_extractions SET media_results='[]'::jsonb WHERE id=%s::uuid",(case['extraction_id'],))
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,%s,'test','vision',1,'unknown')",(A,str(uuid.uuid4()),'extraction:'+str(case['extraction_id'])+':vision'));db.commit()
    with pytest.raises(TenantLinearError,match='unknown'):owner.apply_owner(PROFILE,campaign,{**args,'acknowledge_incomplete_history':True})
    db.execute('DELETE FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s',(A,'extraction:'+str(case['extraction_id'])+':vision'))
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',(json.dumps(prior_results),case['extraction_id']));db.commit()
    result=owner.apply_owner(PROFILE,campaign,{**args,'acknowledge_incomplete_history':True})
    assert result['count']==1 and result['provider_calls_started'] is False
    saved=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign,))[0]
    fragment=db.execute('SELECT * FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(campaign,))[0]
    assert saved['progress']['case_extensions'][args['request_id']]['acknowledge_incomplete_history'] is True
    fence={**fragment,'campaign_id':campaign,'progress':saved['progress'],'approved_by':11,'photo_model':None}
    with db.cursor() as cur:assert extension.dispatch_fence(cur,A,fence)['state']=='incomplete_history_eligible'
    changed=json.loads(json.dumps(saved['progress']));changed['case_extensions'][args['request_id']]['acknowledge_incomplete_history']=False
    with db.cursor() as cur,pytest.raises(TenantLinearError):extension.dispatch_fence(cur,A,{**fence,'progress':changed})
    with pytest.raises(TenantLinearError):owner.apply_owner(PROFILE,campaign,{**args,'request_id':str(uuid.uuid4()),'expected_version':2,'acknowledge_incomplete_history':True})
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(campaign,))[0]['n']==1
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_campaign_worker as worker
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setattr(spend,'price',lambda model:(1,1));monkeypatch.setattr(spend,'media_price',lambda model,operation:1)
    db.execute("UPDATE saas_knowledge_campaigns SET status='running' WHERE id=%s::uuid",(campaign,));db.commit()
    claimed=campaigns.claim(A,campaign)
    with db.cursor() as cur:worker.check_fence(cur,A,fragment['id'])
    if old_ref_format=='stale_no_call':
        from unittest.mock import AsyncMock,Mock
        import tenant_knowledge_media as media
        import tenant_knowledge_media_cache as cache
        monkeypatch.setattr(spend,'_connect',lambda:db)
        monkeypatch.setattr(media,'load',lambda *args:{'provider':'connect','route':{'channel_id':CA,'conversation_id':'client'},'row':{'message_type':'image'}})
        monkeypatch.setattr(media,'download_media',AsyncMock(return_value=(PNG,'image/png')))
        monkeypatch.setattr(media,'supports_images',lambda model:True)
        def fake_observe(profile,item,ref,content,mime,origin,provider,check_before_ai=None):
            assert content==PNG and ref['id']==message
            item['vision_attempts']=1;item['vision_usage']=[{'input_tokens':1,'output_tokens':1}]
            return 'synthetic observation',{},True
        observed=Mock(side_effect=fake_observe);monkeypatch.setattr(cache,'observe_once',observed)
        response=service.analyze(PROFILE,{**claimed['route'],'request_id':str(fragment['id'])})
        assert observed.call_count==1, response
        ready[1].assert_called_once()
        campaigns.complete(claimed,response)
        latest=db.execute('SELECT version FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign,))[0]['version']
        result=current.verify_owner_source(PROFILE,campaign,{'expected_version':latest,'case_id':key,'message_id':message,'case_version':version})
        assert result['saved_byte_results']==[{'status':'processed','text':'synthetic observation'}]
        assert db.execute('SELECT cap_microusd FROM saas_ai_media_grants WHERE id=%s::uuid',(campaign,))[0]['cap_microusd']==10000000
    else:
        prepared=service.prepare(PROFILE,{**claimed['route'],'request_id':str(fragment['id'])})
        assert prepared['route']['case_source_sha256']==value['current_sha256']
