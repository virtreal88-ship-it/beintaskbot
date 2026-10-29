"""Selected case boundaries, existing receipts, no remote media/model calls."""
import copy,json,uuid
import pytest
from unittest.mock import AsyncMock
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE
from test_knowledge_candidates import ready,candidate
from test_knowledge_media import context
import tenant_knowledge_candidates_store as store
import tenant_knowledge_media_coverage as coverage
import tenant_media_evidence as evidence
import tenant_knowledge_media as media


def test_fourteen_inside_case_not_full_multiyear_dialog(ready,monkeypatch):
    conn,_,_=ready
    ids=[str(uuid.uuid4()) for _ in range(18)]
    for index,key in enumerate(ids):
        conn.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','Synthetic image caption','image',%s::timestamptz)''',
          (A,key,'connect:'+key,'connect:'+CA,('2020-01-01' if index==0 else '2025-01-01' if index==17 else '2024-01-'+str(index).zfill(2))+'T00:00:00Z'))
    evidence.create_schema(conn);conn.commit()
    route={'provider':'connect','channel_id':CA,'conversation_id':'client'}
    run={'route':route,'status':'completed','source_refs':[], 'media_results':[]}
    def project(start,end):
        with conn.cursor() as cur:return store.case_media_coverage(cur,A,run,{'lead_id':41},{'start_message_id':start,'end_message_id':end})
    value=project(ids[1],ids[14])
    assert {r['id'] for r in value['media_results']}==set(ids[1:15])
    assert value['status']=='partial' and value['media_coverage']['total']==14
    assert all(r['status']=='unknown' for r in value['media_results'])
    later=project(ids[15],ids[16])
    assert {r['id'] for r in later['media_results']}==set(ids[15:17])
    assert not set(r['id'] for r in later['media_results'])&set(r['id'] for r in value['media_results'])
    missing=project(None,None)
    assert missing['media_coverage']['boundary_status']=='uncertain' and not missing['media_results']
    foreign=project(str(uuid.uuid4()),ids[14])
    assert foreign['media_coverage']['boundary_status']=='uncertain'
    # A same-route/key from another tenant cannot become an anchor or receipt.
    with conn.cursor() as cur:
        result=store.case_media_coverage(cur,B,run,{'lead_id':42},{'start_message_id':ids[1],'end_message_id':ids[14]})
    assert result['media_coverage']['boundary_status']=='uncertain' and not result['media_results']


def test_fourteen_cached_attachments_reuse_existing_chunker_without_provider(context,monkeypatch):
    from tenant_knowledge_campaign_discovery import bound_context
    item,add,download=context
    for _ in range(14):add()
    monkeypatch.setattr(evidence,'reusable',lambda p,r:{'status':'processed','code':'vision_observation','coverage':{'complete':True},'result':{'observation':'Synthetic saved fact'}})
    pending=copy.deepcopy(item);seen=[];pages=0
    while pending['refs']:
        page=copy.deepcopy(pending);bound_context(page)
        assert len(page['refs'])<=2
        images,results=media.collect({'tenant_id':A},page)
        assert not images and all(r['reused'] for r in results)
        seen.extend(r['id'] for r in results);pages+=1
        keep=len(pending['refs'])-len(page['refs'])
        pending['refs']=pending['refs'][:keep];pending['messages']=pending['messages'][:keep]
    assert pages==7 and len(set(seen))==14
    download.assert_not_called()


def test_states_consent_unknown_partial_and_no_false_complete():
    refs=[{'id':str(i),'media_kind':kind,'channel_id':CA} for i,kind in enumerate(['image','image','image','video','document','image'])]
    receipts=[{'id':'0','status':'processed','code':'vision_observation','reused':True},
      {'id':'1','status':'unavailable','code':'external_media_permission_required'},
      {'id':'2','status':'error','code':'media_processing_failed'},
      {'id':'3','status':'error','code':'media_processing_failed','audio_status':'processed','failure_stage':'visual_analysis'},
      {'id':'4','status':'unknown','code':'provider_result_unknown'}]
    result=coverage.project(refs,receipts,'completed')
    assert result['status']=='partial' and not result['media_coverage']['complete']
    assert result['media_coverage']['counts']['pending_consent']==1
    assert result['media_results'][3]['audio_status']=='processed' and not result['media_results'][3]['complete']
    assert result['media_results'][4]['status']=='unknown' and result['media_results'][5]['code']=='status_not_recorded'
    assert coverage.project([],[],'completed')['status']=='completed'
    assert coverage.project(None,[],'completed')['status']=='partial'


def test_content_gates_and_wrong_channel_receipt():
    refs=[{'id':'one','media_kind':'image','channel_id':CA}]
    receipts=[{'id':'one','status':'processed','code':'vision_observation','channel_id':CB,'observation':'private'}]
    assert coverage.project(refs,receipts,'completed')['media_results'][0]['status']=='unknown'
    receipts[0]['channel_id']=CA
    assert 'result' not in coverage.project(refs,receipts,'completed')['media_results'][0]
    assert coverage.project(refs,receipts,'completed',include_content=True)['media_results'][0]['result']['observation']=='private'


def test_uncertain_transcript_candidate_is_content_only_and_never_a_confirmed_transcript():
    ref={'id':'video','media_kind':'video','channel_id':CA}
    receipt={'id':'video','channel_id':CA,'status':'partial','code':'video_observation',
      'audio_status':'uncertain','transcript_candidate':'redacted synthetic words',
      'transcript_candidate_status':'uncertain_unverified'}
    hidden=coverage.project([ref],[receipt],'completed')['media_results'][0]
    assert hidden['status']=='partial' and hidden['audio_status']=='uncertain' and 'result' not in hidden
    visible=coverage.project([ref],[receipt],'completed',include_content=True)['media_results'][0]
    assert visible['result']=={'transcript_candidate':'redacted synthetic words',
      'transcript_candidate_status':'uncertain_unverified'}
    assert 'transcript' not in visible['result']


@pytest.mark.parametrize('audio',['unknown','uncertain','no_speech_detected','unavailable_or_error','processed'])
def test_video_audio_outcomes_remain_distinct_and_safe(audio):
    ref={'id':'video','media_kind':'video','channel_id':CA}
    receipt={'id':'video','channel_id':CA,'status':'partial','code':'vision_observation','audio_status':audio,
      'audio_reason':'speech_confidence_unavailable','audio_failure_code':'provider_http_403_model_access'}
    result=coverage.project([ref],[receipt],'completed')['media_results'][0]
    assert result['audio_status']==audio and result['visual_status']=='partial' and not result['complete']
    assert result['audio_reason']=='speech_confidence_unavailable' and result['audio_failure_code']=='provider_http_403_model_access'
    receipt.update(audio_reason='unsafe secret',audio_failure_code='unsafe secret')
    result=coverage.project([ref],[receipt],'completed')['media_results'][0]
    assert 'audio_reason' not in result and 'audio_failure_code' not in result


@pytest.mark.parametrize('legacy',[
    {'code':'vision_supplied'}, {'code':'vision_observation'},
    {'code':'vision_observation','observation':'  '},
    {'code':'vision_observation','observations':[{'text':''}]},
    {'code':'vision_supplied','transcript':'Caption is not a visual result'},
])
def test_processed_image_without_saved_visual_result_is_not_complete(legacy):
    receipt={'id':'photo','status':'processed','coverage':{'complete':True},**legacy}
    before=copy.deepcopy(receipt)
    result=coverage.project([{'id':'photo','media_kind':'image'}],[receipt],'completed')
    assert result['status']=='partial' and not result['media_coverage']['complete']
    assert result['media_results'][0]['status']=='partial' and not result['media_results'][0]['complete']
    assert receipt==before  # Do not rewrite the original execution receipt.


def test_reused_projection_metadata_requires_a_real_saved_visual_result():
    ref={'id':'photo','media_kind':'image'}
    for payload,complete in [({},False),({'transcript':'audio only'},False),
                             ({'observations':[{'text':'visible technical error'}]},True)]:
        receipt=evidence.apply_reusable({'text':''},{'status':'processed','code':'vision_observation',
            'coverage':{'complete':True},'result':payload})
        result=coverage.project([ref],[{'id':'photo',**receipt}],'completed')
        assert result['media_coverage']['complete'] is complete
        assert 'result' not in result['media_results'][0]


@pytest.mark.parametrize('reuse_style',['legacy_stub','copied_observation','copied_observations'])
def test_case_detail_uses_validated_boundaries_and_version_receipt(ready,monkeypatch,reuse_style):
    conn,_,_=ready;card=candidate(ready);content=conn.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content'];key=content['start_message_id']
    conn.execute("UPDATE saas_crm_messages SET message_type='image',raw=%s::jsonb WHERE id=%s::uuid",(json.dumps({'mediaId':'one'}),key));conn.commit()
    evidence.create_schema(conn)
    row=conn.execute('SELECT external_id,body,raw FROM saas_crm_messages WHERE id=%s::uuid',(key,))[0]
    ref={'id':key,'channel_id':CA,'external_id':row['external_id'],'media_kind':'image','media_version':evidence.media_version(row['raw']),'body_hash':store.digest(row['body']),'role':'customer'}
    with conn.cursor() as cur:evidence.persist(cur,A,[ref],[{'id':key,'status':'processed','code':'vision_observation','observation':'Saved synthetic technical fact'}])
    run=conn.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(card['extraction_id'],))[0]
    for source in run['source_refs']:
        if source['id']==key:source.update(ref)
    stub={'id':key,'status':'processed','code':'vision_observation','coverage':{'complete':True},'reused':True,'_reused_payload':True,'has_visual_result':True}
    if reuse_style!='legacy_stub':
        payload={'observation':'Saved synthetic technical fact'} if reuse_style=='copied_observation' else {'observations':[{'text':'Saved synthetic technical fact'}]}
        stub={'id':key,**evidence.apply_reusable({'text':''},{'status':'processed','code':'vision_observation','coverage':{'complete':True},'result':payload})}
        assert evidence.has_visual_result(stub)  # Reach the new copied-payload branch.
    conn.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps(run['source_refs']),json.dumps([stub]),card['extraction_id']))
    conn.commit()
    first=store.detail(PROFILE,card['id'])['candidate']
    assert first['media_coverage']['scope']=='case_interval'
    assert first['media_processing'][0]['reused'] and first['media_processing'][0]['complete']
    assert 'Saved synthetic technical fact' in json.dumps(first['media_processing'])
    with conn.cursor() as cur:
        current=store.extraction_row(cur,A,card['extraction_id'])
        metadata=store.case_media_coverage(cur,A,current,{'lead_id':41},content)
    assert metadata['media_coverage']['complete'] and 'result' not in metadata['media_results'][0]
    second=store.detail(PROFILE,card['id'])['candidate'];assert second['media_processing']==first['media_processing']
    conn.execute('DELETE FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid',(A,));conn.commit()
    detail=store.detail(PROFILE,card['id']);withdrawn=detail['candidate']
    assert not withdrawn['media_coverage']['complete'] and 'result' not in withdrawn['media_processing'][0]
    assert withdrawn['media_processing'][0]['code']=='shared_media_result_unavailable'
    assert 'Saved synthetic technical fact' not in json.dumps(detail)
    import tenant_knowledge_improve as improve
    import tenant_knowledge_candidates_service as service
    from tenant_linear_policy import TenantLinearError
    from unittest.mock import Mock
    prepare=Mock();monkeypatch.setattr(service,'prepare',prepare)
    with pytest.raises(TenantLinearError,match='case_media_incomplete'):
        improve.improve(PROFILE,card['id'],{'request_id':str(uuid.uuid4()),'expected_version':card['version']})
    prepare.assert_not_called()
    from test_ai_learning_mcp import _setup
    import tenant_ai_learning_mcp as mcp
    import tenant_ai_administrator_read as admin
    _setup(monkeypatch,conn);cfg=mcp._require_cfg();cfg['actor_id']=11;cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    with conn.cursor() as cur:read=admin.read(cur,cfg,'cases_read',{'id':card['id'],'include_content':True})['items'][0]
    assert not read['media_coverage']['complete'] and 'Saved synthetic technical fact' not in json.dumps(read)
    with conn.cursor() as cur:evidence.persist(cur,A,[ref],[{'id':key,'status':'processed','code':'vision_observation','observation':'Saved synthetic technical fact'}])
    conn.commit()
    conn.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid",(json.dumps({'mediaId':'replaced'}),key));conn.commit()
    # The extraction source-version guard may reject the changed source itself;
    # neither path can expose the old receipt as current evidence.
    with conn.cursor() as cur:
        run=store.extraction_row(cur,A,card['extraction_id'])
        result=store.case_media_coverage(cur,A,run,{'lead_id':41},content)
    assert not result['media_coverage']['complete'] and result['media_results'][0]['status']=='unknown'


def test_mcp_fragment_metadata_and_redacted_results_respect_existing_scopes(ready,monkeypatch):
    from test_ai_learning_mcp import _setup
    import tenant_ai_learning_mcp as mcp
    import tenant_ai_administrator_read as admin
    from tenant_linear_policy import TenantLinearError
    conn,_,_=ready;value=candidate(ready);key=value['extraction_id']
    row=conn.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(key,))[0]
    ref=row['source_refs'][0];ref['media_kind']='image'
    receipt={'id':ref['id'],'channel_id':CA,'status':'processed','code':'vision_observation','observation':'error xyz https://private.invalid/secret'}
    conn.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([ref]),json.dumps([receipt]),key));conn.commit()
    _setup(monkeypatch,conn);cfg=mcp._require_cfg();cfg['granted_scopes']=[admin.READ_SCOPE]
    with conn.cursor() as cur:meta=admin.read(cur,cfg,'case_evidence_read',{'id':key})['items'][0]
    assert meta['media_coverage']['scope']=='extraction_fragment' and meta['media_coverage']['total']==1
    assert 'source_refs' not in meta and 'result' not in meta['media_results'][0]
    with conn.cursor() as cur:
        with pytest.raises(TenantLinearError):admin.read(cur,cfg,'case_evidence_read',{'id':key,'include_content':True})
    cfg['granted_scopes'].append(admin.CONTENT_SCOPE)
    with conn.cursor() as cur:full=admin.read(cur,cfg,'case_evidence_read',{'id':key,'include_content':True})['items'][0]
    assert 'private.invalid' not in json.dumps(full) and 'result' in full['media_results'][0]
    cfg['AI_LEARNING_MCP_TENANT_ID']=B
    with conn.cursor() as cur:foreign=admin.read(cur,cfg,'case_evidence_read',{'id':key})
    assert foreign['items']==[]


def test_actual_unknown_visual_cache_does_not_repeat_provider(ready,monkeypatch):
    from unittest.mock import Mock
    import tenant_knowledge_media_cache as cache
    import tenant_knowledge_candidates_service as service
    from tenant_linear_policy import TenantLinearError
    conn,_,_=ready
    monkeypatch.setattr(cache,'_connect',lambda:conn)
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    ref={'id':'one','channel_id':CA}
    item={'tenant':A,'key':'synthetic','model':'synthetic-model','refs':[ref],
      'route':{'allow_external_media':True,'allow_vision':True},'redaction_names':[]}
    provider=Mock(side_effect=TimeoutError('synthetic unknown response'))
    with pytest.raises(TimeoutError) as first:cache.observe_once(PROFILE,item,ref,b'synthetic','image/png',{},provider)
    assert first.value.media_outcome_unknown
    with pytest.raises(TenantLinearError) as second:cache.observe_once(PROFILE,item,ref,b'synthetic','image/png',{},provider)
    assert second.value.media_outcome_unknown and provider.call_count==1


def test_collector_keeps_unknown_distinct_from_error(context,monkeypatch):
    import tenant_knowledge_media_cache as cache
    from tenant_linear_policy import TenantLinearError
    item,add,_=context;add();item['route'].update(allow_external_media=True,allow_vision=True)
    monkeypatch.setattr(evidence,'reusable',lambda *a:None)
    unknown=TenantLinearError('synthetic unknown',409);unknown.media_outcome_unknown=True
    def observe(*a,**k):raise unknown
    monkeypatch.setattr(cache,'observe_once',observe)
    receipts=media.collect({},item)[1]
    assert receipts[0]['status']=='unknown' and receipts[0]['code']=='media_result_unknown'


def test_partial_live_receipt_is_blocked_not_rescheduled(monkeypatch):
    import tenant_knowledge_live_queue as live
    from unittest.mock import Mock
    item={'tenant_id':A,'channel_id':CA,'conversation_id':'client','reviewer_id':11,
      'last_message_id':str(uuid.uuid4()),'last_message_at':'2026-10-26T00:00:00+00:00','generation':1}
    monkeypatch.setattr(live,'extraction_enabled',lambda:True)
    monkeypatch.setattr(live,'require_extraction_enabled',lambda *a:None)
    monkeypatch.setattr(live,'pilot_scope',lambda:None)
    monkeypatch.setattr(live,'claim',lambda:item)
    analyze=Mock(return_value={'request':{'id':'synthetic','status':'partial'}})
    finish=Mock();monkeypatch.setattr(live,'analyze',analyze);monkeypatch.setattr(live,'finish',finish)
    assert live.tick()
    finish.assert_called_once_with(item,live._request_id(item),'media_incomplete')
    analyze.assert_called_once()


def test_png_to_vision_receipt_case_evidence_and_mcp_with_real_reserves(ready,monkeypatch):
    from unittest.mock import Mock,AsyncMock
    from test_connect_images import PNG
    from test_ai_learning_mcp import _setup
    import tenant_ai_learning_mcp as mcp
    import tenant_ai_administrator_read as admin
    import tenant_chat_ai_provider as provider
    import tenant_knowledge_media_cache as cache
    import tenant_knowledge_candidates_service as service
    import tenant_ai_spend_budget as budget
    conn,generation,data=ready;card=candidate(ready)
    content=conn.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content'];key=content['start_message_id']
    conn.execute("UPDATE saas_crm_messages SET message_type='image',raw=%s::jsonb WHERE id=%s::uuid",(json.dumps({'mediaId':'synthetic-png'}),key))
    model='gpt-4.1-2025-04-14';conn.execute('UPDATE saas_ai_agents SET model=%s WHERE tenant_id=%s::uuid',(model,A));conn.commit()
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:5_000_000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({model:{'input':1_000_000,'output':2_000_000}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({model:{'vision_low':400}}))
    for module in (cache,evidence,budget):monkeypatch.setattr(module,'_connect',lambda:conn)
    current={'provider':'connect','route':{'channel_id':CA,'conversation_id':'client'},'row':{'message_type':'image'}}
    monkeypatch.setattr(media,'load',lambda *a:current)
    download=AsyncMock(return_value=(PNG,'image/png'));monkeypatch.setattr(media,'download_media',download)
    def vision(*args,**kwargs):
        assert kwargs['images'][0]['content']==PNG
        assert args[2]=='knowledge_visual_observation'
        rows=conn.execute("SELECT state FROM saas_ai_spend_reservations WHERE kind='knowledge_vision'")
        assert rows and rows[0]['state']=='reserved';conn.commit()
        return 'visible error 742',{'input_tokens':10,'output_tokens':5,'total_tokens':15}
    invoke=Mock(side_effect=vision);monkeypatch.setattr(provider,'generate',invoke)
    generation.return_value=(json.dumps({'cards':[]}),{'input_tokens':20,'output_tokens':10,'total_tokens':30})
    # Use the actual approved campaign and lease, not flags that forge consent.
    import tenant_knowledge_campaigns as campaigns
    from datetime import timedelta
    from tenant_knowledge_candidates_policy import moment
    monkeypatch.setattr(campaigns,'_connect',lambda:conn)
    campaign_id=campaigns.prepare(PROFILE,{'request_id':str(uuid.uuid4()),'channel_ids':[CA],
      'analysis_agent_id':data['analysis_agent_id'],'max_calls':3,'max_tokens':32000,
      'allow_external_media':True,'allow_paid_asr':False,'max_main_calls':1,'max_asr_calls':0,'max_vision_calls':2})
    campaign=campaigns.get(PROFILE,campaign_id)['campaign']
    campaigns.approve(PROFILE,campaign_id,{'expected_version':campaign['version'],'channel_ids':[CA],
      'max_calls':3,'max_tokens':32000,'confirm_external_processing':True,'media_policy':campaign['media_policy']})
    at=conn.execute('SELECT COALESCE(happened_at,created_at) AS at FROM saas_crm_messages WHERE id=%s::uuid',(key,))[0]['at'];conn.commit()
    record=conn.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign_id,))[0]
    with conn.cursor() as cur:campaigns.enqueue(cur,PROFILE,record,{'provider':'connect','channel_id':CA,
      'conversation_id':'client','history_mode':'channel','message_ids':[key],
      'period_start':moment(at).isoformat(),'period_end':(moment(at)+timedelta(microseconds=1)).isoformat()})
    conn.commit();claimed=campaigns.claim(A,campaign_id)
    selected={**claimed['route'],'request_id':str(claimed['id'])}
    result=service.analyze(PROFILE,selected)
    assert campaigns.complete(claimed,result)
    assert result['request']['status']=='completed'
    assert invoke.call_count==1 and download.await_count==1
    # The extracted fact entered model evidence, not the caption.
    assert 'visible error 742' in json.dumps(generation.call_args.args[3])
    opened=store.detail(PROFILE,card['id'])['candidate']
    assert 'visible error 742' in json.dumps(opened['media_processing'])
    _setup(monkeypatch,conn);cfg=mcp._require_cfg();cfg['actor_id']=11;cfg['granted_scopes']=[admin.READ_SCOPE,admin.CONTENT_SCOPE]
    with conn.cursor() as cur:read=admin.read(cur,cfg,'cases_read',{'id':card['id'],'include_content':True})['items'][0]
    assert read['media_coverage']['scope']=='case_interval' and read['media_coverage']['complete']
    assert 'visible error 742' in json.dumps(read['media_processing'])
    again=service.analyze(PROFILE,selected)
    assert again['request']['id']==result['request']['id']
    assert invoke.call_count==1 and download.await_count==1


def test_improve_cannot_pay_for_caption_only_and_never_widens_case(ready,monkeypatch):
    import tenant_knowledge_improve as improve
    import tenant_knowledge_candidates_service as service
    from tenant_linear_policy import TenantLinearError
    from unittest.mock import Mock
    conn,generation,_=ready;card=candidate(ready)
    row=conn.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content']
    conn.execute("UPDATE saas_crm_messages SET message_type='image' WHERE id=%s::uuid",(row['start_message_id'],));conn.commit()
    before=generation.call_count
    prepare=Mock();monkeypatch.setattr(service,'prepare',prepare)
    with pytest.raises(TenantLinearError,match='case_media_incomplete'):
        improve.improve(PROFILE,card['id'],{'request_id':str(uuid.uuid4()),'expected_version':card['version']})
    prepare.assert_not_called();assert generation.call_count==before


def test_improve_uses_saved_photo_and_excludes_same_timestamp_neighbors(ready,monkeypatch):
    import tenant_knowledge_improve as improve
    import tenant_knowledge_candidates_service as service
    conn,generation,_=ready;card=candidate(ready)
    content=conn.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content']
    start=content['start_message_id'];end=content['end_message_id']
    row=conn.execute('SELECT * FROM saas_crm_messages WHERE id=%s::uuid',(start,))[0]
    conn.execute("UPDATE saas_crm_messages SET message_type='image',raw=%s::jsonb WHERE id=%s::uuid",(json.dumps({'mediaId':'synthetic'}),start))
    # Adjacent requests can share an endpoint timestamp; ID order still fences them.
    for key,anchor in [('00000000-0000-0000-0000-000000000001',start),('ffffffff-ffff-ffff-ffff-ffffffffffff',end)]:
        conn.execute('''INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at)
          SELECT tenant_id,%s::uuid,kommo_lead_id,%s,channel,direction,'OUTSIDE_OTHER_REQUEST','text',COALESCE(happened_at,created_at)
          FROM saas_crm_messages WHERE id=%s::uuid''',(key,key,anchor))
    evidence.create_schema(conn)
    ref={'id':start,'channel_id':CA,'external_id':row['external_id'],'media_kind':'image','media_version':evidence.media_version({'mediaId':'synthetic'}),'body_hash':store.digest(row['body'])}
    with conn.cursor() as cur:evidence.persist(cur,A,[ref],[{'id':start,'status':'processed','code':'vision_observation','observation':'VISIBLE_TECHNICAL_742'}])
    run=conn.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(card['extraction_id'],))[0]
    for source in run['source_refs']:
        if source['id']==start:source.update(ref)
    conn.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps(run['source_refs']),json.dumps([{'id':start,'status':'processed','code':'vision_observation','coverage':{'complete':True},'reused':True}]),card['extraction_id']))
    conn.commit()
    class Inspected(Exception):pass
    def inspect(profile,payload,review):
        snapshot=review['snapshot']
        assert 'VISIBLE_TECHNICAL_742' in json.dumps(snapshot['messages'])
        assert 'OUTSIDE_OTHER_REQUEST' not in json.dumps(snapshot['messages'])
        assert start in {r['id'] for r in snapshot['refs']} and end in {r['id'] for r in snapshot['refs']}
        raise Inspected()
    monkeypatch.setattr(service,'prepare',inspect)
    before=generation.call_count
    with pytest.raises(Inspected):improve.improve(PROFILE,card['id'],{'request_id':str(uuid.uuid4()),'expected_version':card['version']})
    assert generation.call_count==before
