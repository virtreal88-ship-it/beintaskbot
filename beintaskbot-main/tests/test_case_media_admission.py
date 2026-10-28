"""One reviewed case interval, existing campaign consent and provider fences."""
import json
import uuid

import pytest

from test_knowledge_candidates import ready, candidate
from test_tenant_support_inbox import database, db, A, B, CA, AGENT, PROFILE
from tenant_linear_policy import TenantLinearError
import tenant_case_media_admission as admission
import tenant_knowledge_campaigns as campaigns
import tenant_knowledge_campaign_worker as worker
from tenant_media_evidence import source_hash


def _image(db, day):
    key=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','Synthetic attachment','image',%s::jsonb,%s::timestamptz)''',
      (A,key,'media-'+key,'connect:'+CA,json.dumps({'mediaId':key}),f'2024-01-{day:02d}T00:00:00Z'))
    return key


def _case(ready, days=(3,4)):
    db, _, _=ready
    card=candidate(ready)
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content']
    start,end=content['start_message_id'],content['end_message_id']
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-01-01T00:00:00Z' WHERE id=%s::uuid",(start,))
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-02-01T00:00:00Z' WHERE id=%s::uuid",(end,))
    ids=[_image(db,day) for day in days]
    db.commit()
    return db,card,ids


def _mark_pending_consent(db, candidate_id, ids):
    """Give fixtures positive evidence that no provider attempt was made."""
    import tenant_media_evidence as evidence
    import tenant_knowledge_candidates_store as store
    extraction_id=db.execute('SELECT extraction_id FROM saas_knowledge_candidates WHERE id=%s::uuid',(candidate_id,))[0]['extraction_id']
    extraction=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(extraction_id,))[0]
    refs=list(extraction.get('source_refs') or []);results=[]
    for message_id in ids:
        row=db.execute('SELECT external_id,body,raw,message_type FROM saas_crm_messages WHERE id=%s::uuid',(message_id,))[0]
        refs.append({'id':message_id,'channel_id':CA,'external_id':row['external_id'],
          'media_kind':row['message_type'],'media_version':evidence.media_version(row['raw']),
          'body_hash':store.digest(row['body']),'role':'customer'})
        # Persisted extractor format is unavailable+consent code; coverage
        # projects that to pending_consent for the owner UI.
        results.append({'id':message_id,'status':'unavailable','code':'vision_not_approved'})
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps(refs),json.dumps(results),extraction_id))
    db.commit()


@pytest.mark.parametrize('raw,verified',[
    ({'mediaId':'stable-file-id','fileLength':14},True),
    ({'file_uuid':'stable-file-id','file_size':14},True),
    ({'fileLength':14},False),
    ({'file_size':14,'timestamp':1700000000},False),
    ({'mediaId':'  ','fileLength':14},False),
])
def test_current_source_retry_requires_stable_media_identity(raw,verified):
    assert admission._has_current_media_version(raw) is verified


def test_two_appeals_same_dialog_and_fourteen_bounded_attachments(ready):
    db,card,inside=_case(ready,tuple(range(2,16)))
    before=_image(db,1)
    after=_image(db,20)
    # Make the latter genuinely outside the first case and inside a second
    # separately reviewed interval of this long lived conversation.
    db.execute("UPDATE saas_crm_messages SET happened_at='2023-12-31T00:00:00Z' WHERE id=%s::uuid",(before,))
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-03-01T00:00:00Z' WHERE id=%s::uuid",(after,))
    db.commit()
    with db.cursor() as cur:
        first=admission.plan(cur,PROFILE,card['id'])
    assert first['status']=='pending_consent'
    assert {x['id'] for x in first['media']}==set(inside)
    assert before not in {x['id'] for x in first['media']}
    assert after not in {x['id'] for x in first['media']}
    assert len(list(admission.routes(first,AGENT)))==14
    second_start,second_end=_image(db,21),_image(db,22)
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-03-02T00:00:00Z' WHERE id=%s::uuid",(second_start,))
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-03-03T00:00:00Z' WHERE id=%s::uuid",(second_end,))
    row=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]
    content=row['content'];content.update(start_message_id=after,end_message_id=second_end,
        source_message_ids=[after,second_start,second_end])
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb,version=version+1 WHERE id=%s::uuid',(json.dumps(content),card['id']))
    db.commit()
    with db.cursor() as cur:second=admission.plan(cur,PROFILE,card['id'])
    assert second['status']=='pending_consent'
    assert {x['id'] for x in second['media']}=={after,second_start,second_end}
    assert not set(inside)&{x['id'] for x in second['media']}


def test_case_campaign_owner_consent_batches_and_replay_fences(ready,monkeypatch):
    db,card,ids=_case(ready)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setattr(worker,'_connect',lambda:db)
    key=str(uuid.uuid4())
    data={'request_id':key,'case_id':card['id'],'channel_ids':[CA],
      'analysis_agent_id':AGENT,'max_calls':6,'max_tokens':48000,
      'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':4}
    provider=ready[1];calls_before=provider.call_count
    assert campaigns.prepare(PROFILE,data)==key
    assert campaigns.prepare(PROFILE,data)==key
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_sources')[0]['n']==0
    assert campaigns.claim(A,key) is None
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    plan=row['progress']
    confirmation={'expected_version':row['version'],'channel_ids':[CA],
      'max_calls':6,'max_tokens':48000,'confirm_external_processing':True,
      'media_policy':row['media_policy'],'case_id':card['id'],'case_plan_hash':plan['plan_hash']}
    with pytest.raises(TenantLinearError):
        campaigns.approve(PROFILE,key,{**confirmation,'case_plan_hash':'0'*64})
    assert campaigns.claim(A,key) is None
    campaigns.approve(PROFILE,key,confirmation)
    fragments=db.execute('SELECT * FROM saas_knowledge_campaign_fragments ORDER BY id')
    assert len(fragments)==2
    assert {f['route']['message_ids'][0] for f in fragments}==set(ids)
    assert all(len(f['route']['message_ids'])==1 for f in fragments)
    with db.cursor() as cur:
        same=campaigns.enqueue(cur,PROFILE,
          db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0],fragments[0]['route'])
    assert same==str(fragments[0]['id'])
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments')[0]['n']==2
    claimed=campaigns.claim(A,key)
    assert claimed and campaigns.claim(A,key) is None
    with db.cursor() as cur:assert worker.check_fence(cur,A,claimed['id']) is None
    db.execute("UPDATE saas_knowledge_campaigns SET status='stopped' WHERE id=%s::uuid",(key,));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError):worker.check_fence(cur,A,claimed['id'])


def test_missing_bounds_tenant_and_source_change_fail_closed(ready,monkeypatch):
    db,card,ids=_case(ready,(3,))
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):admission.plan(cur,{'tenant_id':B,'telegram_id':22},card['id'])
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content']
    content['end_message_id']=None
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),card['id']))
    db.commit()
    with db.cursor() as cur:assert admission.plan(cur,PROFILE,card['id'])['status']=='pending_boundaries'


def test_existing_case_snapshot_is_bounded_and_excludes_rejected(ready):
    db,card,media=_case(ready,(3,))
    with db.cursor() as cur:unknown=admission.plan_existing_cases(cur,PROFILE)
    assert unknown['eligible_count']==0 and unknown['unknown_media_count']==1
    _mark_pending_consent(db,card['id'],media)
    with db.cursor() as cur,pytest.raises(TenantLinearError):
        admission.plan_existing_cases(cur,{'tenant_id':A,'telegram_id':22})
    with db.cursor() as cur:
        snapshot=admission.plan_existing_cases(cur,PROFILE)
    assert snapshot['case_count']==1
    assert snapshot['eligible_count']==1
    assert snapshot['snapshot_hash']!=unknown['snapshot_hash']
    assert snapshot['channel_ids']==[CA]
    assert any(x['id']==media[0] for x in snapshot['cases'][0]['media'])
    db.execute("UPDATE saas_knowledge_candidates SET status='rejected' WHERE id=%s::uuid",(card['id'],));db.commit()
    with db.cursor() as cur:
        excluded=admission.plan_existing_cases(cur,PROFILE)
    assert excluded['case_count']==0 and excluded['eligible_count']==0


def test_existing_case_snapshot_reports_ambiguous_boundaries_without_routing(ready):
    db,card,_=_case(ready,(3,))
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content']
    content['end_message_id']=None
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb,version=version+1 WHERE id=%s::uuid',(json.dumps(content),card['id']));db.commit()
    with db.cursor() as cur:snapshot=admission.plan_existing_cases(cur,PROFILE)
    assert snapshot['case_count']==1 and snapshot['review_count']==1
    assert snapshot['eligible_count']==0 and snapshot['cases'][0]['status']=='pending_boundaries'


def test_case_interval_media_without_history_is_exposed_but_stays_unknown(ready):
    db,card,ids=_case(ready,(3,))
    with db.cursor() as cur:plan=admission.plan(cur,PROFILE,card['id'])
    item=next(media for media in plan['media'] if media['id']==ids[0])
    assert item['status']=='unknown'
    assert item['reconciliation']=={'state':'unknown','reason':'attempt_history_not_recorded','matching_runs':0}
    assert not list(admission.routes(plan,AGENT,allow_legacy_unknown=False))


def test_read_only_all_cases_preview_reports_zero_eligible_reconciliation(ready,monkeypatch):
    db,card,ids=_case(ready,(3,))
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    provider=ready[1];calls=provider.call_count
    audit_before=db.execute('SELECT count(*) AS n FROM saas_tenant_audit_events')[0]['n']
    preview=campaigns.preview_existing_cases(PROFILE)
    item=next(m for m in preview['cases'][0]['media'] if m['id']==ids[0])
    assert preview['eligible_count']==0 and preview['unknown_media_count']==1
    assert item['reconciliation']['reason']=='attempt_history_not_recorded'
    assert db.execute('SELECT count(*) AS n FROM saas_tenant_audit_events')[0]['n']==audit_before
    assert provider.call_count==calls
    with pytest.raises(TenantLinearError):campaigns.preview_existing_cases({'tenant_id':A,'telegram_id':22})


@pytest.mark.parametrize('unknown_kind',['provider_timeout','legacy_source_mismatch'])
def test_batch_never_routes_provider_unknown_or_unverified_legacy_source(ready,unknown_kind):
    db,card,media=_case(ready,(3,))
    _mark_pending_consent(db,card['id'],media)
    extraction_id=db.execute('SELECT extraction_id FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['extraction_id']
    if unknown_kind=='provider_timeout':
        db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
          (json.dumps([{'id':media[0],'status':'unknown','code':'provider_timeout'}]),extraction_id))
    else:
        refs=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(extraction_id,))[0]['source_refs']
        for ref in refs:
            if ref['id']==media[0]:ref['media_version']='0'*64
        db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE id=%s::uuid',
          (json.dumps(refs),extraction_id))
    db.commit()
    with db.cursor() as cur:snapshot=admission.plan_existing_cases(cur,PROFILE)
    assert snapshot['eligible_count']==0 and snapshot['unknown_media_count']==1
    assert not list(admission.existing_routes(snapshot,AGENT,'gpt-4.1'))


def test_owner_unknown_retry_fails_closed_when_only_size_metadata_is_available(ready):
    db,card,media=_case(ready,(3,))
    _mark_pending_consent(db,card['id'],media)
    extraction_id=db.execute('SELECT extraction_id FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['extraction_id']
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([{'id':media[0],'status':'unknown','code':'provider_timeout'}]),extraction_id))
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({'fileLength':1024}),media[0]))
    db.commit()
    with db.cursor() as cur:
        snapshot=admission.plan_existing_cases(cur,PROFILE,acknowledge_prior_unknown=True)
    item=next(m for c in snapshot['cases'] for m in c.get('media',[]) if m['id']==media[0])
    assert item['source_version_verified'] is False
    assert item.get('retry_authorized') is not True
    assert snapshot['retry_unknown_count']==0
    assert not any(route['message_ids']==[media[0]] for route in admission.existing_routes(snapshot,AGENT,'gpt-4.1'))


class _ReconciliationCursor:
    def __init__(self,runs,*,reservation=False,ledger=True):
        self.runs=runs;self.reservation=reservation;self.ledger=ledger;self.result=None
    def execute(self,query,params=None):
        if 'SELECT id::text AS id,status,source_refs,media_results' in query:self.result=self.runs
        elif 'to_regclass' in query:self.result={'table_name':'saas_ai_spend_reservations' if self.ledger else None}
        elif 'SELECT 1 FROM saas_ai_spend_reservations' in query:self.result={'found':1} if self.reservation else None
        else:raise AssertionError('unexpected reconciliation query: '+query)
    def fetchall(self):return self.result
    def fetchone(self):return self.result


def test_historical_reconciliation_only_accepts_exact_version_explicit_no_call():
    msg='00000000-0000-0000-0000-000000000111'
    ref={'id':msg,'external_id':'media-exact','body_hash':'b'*64,'media_kind':'image','media_version':'v1'}
    digest=source_hash(ref)
    run={'id':'00000000-0000-0000-0000-000000000222','status':'completed',
      'source_refs':[ref],'media_results':[{'id':msg,'status':'unavailable','code':'vision_not_approved'}]}
    result=admission._historical_attempt_reconciliation(_ReconciliationCursor([run]),A,msg,digest)
    assert result=={'state':'known_not_attempted','reason':'exact_version_explicit_consent_block','matching_runs':1}
    assert list(admission.routes({'media':[{'id':msg,'kind':'image','source_version':'v1','source_hash':digest,
      'at':'2024-01-01T00:00:00+00:00','period_end':'2024-01-01T00:00:00.000001+00:00',
      'status':'pending_consent','code':'verified_not_attempted','complete':False}],
      'channel_id':CA,'conversation_id':'client','case_id':'case','case_version':1,'plan_hash':'a'*64},AGENT))


def test_persisted_video_consent_receipt_does_not_hide_prior_asr_attempt():
    video={'media_kind':'video'}
    assert admission._explicit_no_media_call(video,{'status':'unavailable','code':'vision_not_approved','audio_status':'not_requested'})
    assert not admission._explicit_no_media_call(video,{'status':'unavailable','code':'vision_not_approved','audio_status':'processed','transcript':'sample'})
    assert not admission._explicit_no_media_call(video,{'status':'unavailable','code':'vision_not_approved'})
    assert not admission._explicit_no_media_call({'media_kind':'image'},{'status':'pending_consent','code':'vision_not_approved'})


@pytest.mark.parametrize(('runs','reservation','ledger','reason'),[
    ([],False,True,'attempt_history_not_recorded'),
    ([{'id':'run','status':'unknown','source_refs':[{'id':'m','external_id':'e','body_hash':'b'*64,'media_kind':'image','media_version':'v1'}],
       'media_results':[{'id':'m','status':'unknown','code':'provider_timeout'}]}],False,True,'prior_attempt_or_unresolved_result'),
    ([{'id':'run','status':'completed','source_refs':[{'id':'m','external_id':'e','body_hash':'b'*64,'media_kind':'image','media_version':'old'}],
       'media_results':[{'id':'m','status':'unavailable','code':'vision_not_approved'}]}],False,True,'historical_source_version_unverified'),
    ([{'id':'run','status':'completed','source_refs':[{'id':'m','external_id':'e','body_hash':'b'*64,'media_kind':'image','media_version':'v1'}],
       'media_results':[{'id':'m','status':'unavailable','code':'vision_not_approved'}]}],True,True,'media_reservation_present'),
    ([{'id':'run','status':'completed','source_refs':[{'id':'m','external_id':'e','body_hash':'b'*64,'media_kind':'image','media_version':'v1'}],
       'media_results':[{'id':'m','status':'unavailable','code':'vision_not_approved'}]}],False,False,'spend_ledger_unavailable'),
])
def test_historical_reconciliation_fail_closed_on_unknowns(runs,reservation,ledger,reason):
    ref={'id':'m','external_id':'e','body_hash':'b'*64,'media_kind':'image','media_version':'v1'}
    result=admission._historical_attempt_reconciliation(_ReconciliationCursor(runs,reservation=reservation,ledger=ledger),A,'m',source_hash(ref))
    assert result['state']=='unknown' and result['reason']==reason


def test_all_cases_campaign_is_unapproved_until_explicit_fixed_cap_grant(ready,monkeypatch):
    db,card,media=_case(ready,(3,))
    _mark_pending_consent(db,card['id'],media)
    provider=ready[1];calls_before=provider.call_count
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    key=str(uuid.uuid4())
    data={'request_id':key,'all_existing_cases':True,'channel_ids':[],
      'analysis_agent_id':AGENT,'max_calls':10000,'max_tokens':160000000,
      'max_cost_microusd':10000000,'video_model':'gpt-4.1',
      'allow_external_media':True,'allow_paid_asr':True,
      'max_main_calls':10000,'max_asr_calls':10000,'max_vision_calls':10000}
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-4.1':{'vision_low':8896}}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'gpt-4.1':{'input':2000000,'output':8000000}}))
    assert campaigns.prepare(PROFILE,data)==key
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    assert row['status']=='awaiting_budget' and not row['approved_at']
    assert row['max_cost_microusd']==10000000 and row['progress']['mode']=='case_media_batch'
    assert admission.grant_for_message(db.cursor(),A,media[0])=={'id':key,'status':'pending','approved':False}
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,))[0]['n']==0
    assert provider.call_count==calls_before
    import tenant_ai_spend_budget as spend
    spend.ensure_schema(db);db.commit()
    campaigns.control(PROFILE,key,row['version'],'stop')
    assert db.execute('SELECT status FROM saas_ai_media_grants WHERE campaign_id=%s::uuid',(key,))[0]['status']=='revoked'
    with db.cursor() as cur,pytest.raises(TenantLinearError,match='ai_media_grant_inactive'):
        spend.reserve_media(cur,A,'revoked:attempt','knowledge_vision','gpt-4.1','vision_low',1,campaign_id=key)
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE campaign_id=%s::uuid',(key,))[0]['n']==0


def test_all_cases_grant_requires_exact_owner_acceptance_then_stops_cleanly(ready,monkeypatch):
    db,card,media=_case(ready,(3,))
    db.execute("UPDATE saas_crm_messages SET message_type='video' WHERE id=%s::uuid",(media[0],));db.commit()
    _mark_pending_consent(db,card['id'],media)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL','test-model')
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS','["test-model"]')
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'test-model':{'input':10,'output':10},'gpt-4.1':{'input':2000000,'output':8000000}}))
    monkeypatch.setenv('AI_AGENTS_TRANSCRIPTION_MODEL','whisper-1')
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-4.1':{'vision_low':8896},'whisper-1':{'asr':400}}))
    import tenant_ai_agent_key_policy as key_policy
    monkeypatch.setattr(key_policy,'platform_model',lambda:'test-model')
    monkeypatch.setattr(key_policy,'choose_api_credential',lambda *args:('synthetic-key','platform'))
    key=str(uuid.uuid4());data={'request_id':key,'all_existing_cases':True,'channel_ids':[],
      'analysis_agent_id':AGENT,'max_calls':10000,'max_tokens':160000000,'max_cost_microusd':10000000,
      'video_model':'gpt-4.1','allow_external_media':True,'allow_paid_asr':True,
      'max_main_calls':10000,'max_asr_calls':10000,'max_vision_calls':10000}
    campaigns.prepare(PROFILE,data)
    with pytest.raises(TenantLinearError,match='single current-case media grant'):
        campaigns.prepare(PROFILE,{'request_id':str(uuid.uuid4()),'case_id':card['id'],
          'channel_ids':[CA],'analysis_agent_id':AGENT,'max_calls':4,'max_tokens':48000,
          'allow_external_media':True,'allow_paid_asr':False,'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':4})
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    consent={'expected_version':row['version'],'channel_ids':row['sources'],'max_calls':row['max_calls'],
      'max_tokens':row['max_tokens'],'confirm_external_processing':True,'media_policy':row['media_policy'],
      'all_cases_snapshot_hash':row['progress']['snapshot_hash'],'max_cost_microusd':10000000,'video_model':'gpt-4.1'}
    extraction_id=db.execute('SELECT extraction_id FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['extraction_id']
    prior_results=db.execute('SELECT media_results FROM saas_knowledge_extractions WHERE id=%s::uuid',(extraction_id,))[0]['media_results']
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([{'id':media[0],'status':'unknown','code':'provider_timeout'}]),extraction_id));db.commit()
    with pytest.raises(TenantLinearError,match='Current case snapshot changed'):
        campaigns.approve(PROFILE,key,consent)
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps(prior_results),extraction_id));db.commit()
    prior_raw=db.execute('SELECT raw FROM saas_crm_messages WHERE id=%s::uuid',(media[0],))[0]['raw']
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({**prior_raw,'mediaId':'different-version'}),media[0]));db.commit()
    with pytest.raises(TenantLinearError,match='Current case snapshot changed'):
        campaigns.approve(PROFILE,key,consent)
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',
      (json.dumps(prior_raw),media[0]));db.commit()
    with pytest.raises(TenantLinearError):campaigns.approve(PROFILE,key,{**consent,'max_cost_microusd':10_000_001})
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,))[0]['n']==0
    result=campaigns.approve(PROFILE,key,consent)
    assert result['campaign']['status']=='running'
    fragments=db.execute('SELECT * FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,))
    assert len(fragments)==1
    assert fragments[0]['route']['media_grant_id']==key and fragments[0]['route']['video_model']=='gpt-4.1'
    # Exercise the real worker→preflight→settlement path with mocked providers.
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_campaign_worker as campaign_worker
    import tenant_knowledge_media as media_service
    import tenant_ai_spend_budget as spend
    from unittest.mock import Mock
    monkeypatch.setattr(service,'_connect',lambda:db)
    monkeypatch.setattr(campaign_worker,'_connect',lambda:db)
    monkeypatch.setattr(spend,'_connect',lambda:db)
    monkeypatch.setattr(service,'platform_model_allowed',lambda _model:True)
    generation=Mock(return_value=(json.dumps({'cards':[]}),
      {'input_tokens':100,'output_tokens':30,'total_tokens':130}))
    monkeypatch.setattr(service,'generate',generation)
    observed={}
    def collect(_profile,item,check_before_ai=None):
        rows=db.execute('''SELECT kind,media_operation,model,reserved_microusd,grant_id,state
          FROM saas_ai_spend_reservations WHERE campaign_id=%s::uuid ORDER BY kind''',(key,))
        observed['reservations_before_media']=rows
        observed['video_model']=item['route'].get('video_model')
        # A case created after owner approval remains outside this immutable
        # snapshot and must not stop the already-approved fragment.
        source=db.execute('SELECT * FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]
        clone={**source['content'],'title':source['content']['title']+' later case'}
        import tenant_knowledge_candidates_store as candidate_store
        db.execute('''INSERT INTO saas_knowledge_candidates(tenant_id,id,extraction_id,content,content_hash,status,quality_flags)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s::jsonb,%s,'needs_clarification','{}'::jsonb)''',
          (A,str(uuid.uuid4()),source['extraction_id'],json.dumps(clone),candidate_store.digest(clone)))
        db.commit()
        video=next(ref for ref in item['refs'] if ref.get('media_kind')=='video')
        item.update(asr_attempted=True,asr_usage={'input_tokens':1,'output_tokens':0,'total_tokens':1},
          vision_attempts=1,vision_usage=[{'input_tokens':100,'output_tokens':20,'total_tokens':120}],
          media_results=[{'id':video['id'],'kind':'video','status':'partial','code':'video_observation',
            'observation':'Synthetic sampled frame; no confirmed diagnosis','visuals':[{'second':0}],
            'audio_status':'unavailable_or_error'}])
        return [],item['media_results']
    monkeypatch.setattr(media_service,'collect',collect)
    assert campaign_worker.tick() is True
    assert generation.call_count==1
    assert observed['video_model']=='gpt-4.1'
    reservations=observed['reservations_before_media']
    assert {(r['kind'],r['model'],str(r['grant_id']),r['state']) for r in reservations}=={
      ('live_case_extraction','test-model',key,'reserved'),
      ('knowledge_asr','whisper-1',key,'reserved'),
      ('knowledge_vision','gpt-4.1',key,'reserved')}
    assert all(r['reserved_microusd']>0 for r in reservations)
    assert {(r['kind'],r['state'],str(r['grant_id'])) for r in db.execute(
      'SELECT kind,state,grant_id FROM saas_ai_spend_reservations WHERE campaign_id=%s::uuid',(key,))}=={
      ('live_case_extraction','settled',key),('knowledge_asr','settled',key),('knowledge_vision','settled',key)}
    assert db.execute('SELECT status FROM saas_ai_media_grants WHERE campaign_id=%s::uuid',(key,))[0]['status']=='completed'
    with pytest.raises(TenantLinearError,match='cannot be reset or topped up'):
        campaigns.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4())})


def test_cached_visual_receipt_is_not_queued_again(ready,monkeypatch):
    import tenant_media_evidence as evidence
    import tenant_knowledge_candidates_store as store
    db,card,ids=_case(ready)
    evidence.create_schema(db)
    row=db.execute('SELECT external_id,body,raw FROM saas_crm_messages WHERE id=%s::uuid',(ids[0],))[0]
    ref={'id':ids[0],'channel_id':CA,'external_id':row['external_id'],
      'media_kind':'image','media_version':evidence.media_version(row['raw']),
      'body_hash':store.digest(row['body'])}
    with db.cursor() as cur:
        evidence.persist(cur,A,[ref],[{'id':ids[0],'status':'processed',
          'code':'vision_observation','observation':'Synthetic saved image observation'}])
    db.commit()
    with db.cursor() as cur:planned=admission.plan(cur,PROFILE,card['id'])
    assert planned['media'][0]['complete']
    assert [r['message_ids'] for r in admission.routes(planned,AGENT)]==[[ids[1]]]


def test_all_cases_reuses_exact_completed_media_and_routes_only_remaining(ready):
    import tenant_media_evidence as evidence
    import tenant_knowledge_candidates_store as store
    db,card,ids=_case(ready,(3,4))
    _mark_pending_consent(db,card['id'],ids)
    evidence.create_schema(db)
    row=db.execute('SELECT external_id,body,raw FROM saas_crm_messages WHERE id=%s::uuid',(ids[0],))[0]
    ref={'id':ids[0],'channel_id':CA,'external_id':row['external_id'],
      'media_kind':'image','media_version':evidence.media_version(row['raw']),
      'body_hash':store.digest(row['body'])}
    with db.cursor() as cur:
        evidence.persist(cur,A,[ref],[{'id':ids[0],'status':'processed',
          'code':'vision_observation','observation':'Synthetic exact saved observation'}])
    db.commit()
    with db.cursor() as cur:snapshot=admission.plan_existing_cases(cur,PROFILE)
    assert snapshot['reused_count']==1 and snapshot['eligible_count']==1
    assert [route['message_ids'] for route in admission.existing_routes(snapshot,AGENT)]==[[ids[1]]]


def test_all_cases_rejects_video_model_without_configured_tariff_before_grant(ready,monkeypatch):
    db,card,media=_case(ready,(3,))
    db.execute("UPDATE saas_crm_messages SET message_type='video' WHERE id=%s::uuid",(media[0],));db.commit()
    _mark_pending_consent(db,card['id'],media)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL','test-model')
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS','[\"test-model\"]')
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    data={'request_id':str(uuid.uuid4()),'all_existing_cases':True,'channel_ids':[],
      'analysis_agent_id':AGENT,'max_calls':10000,'max_tokens':160000000,'max_cost_microusd':10000000,
      'video_model':'gpt-4.1','allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':10000,'max_asr_calls':0,'max_vision_calls':10000}
    with pytest.raises(TenantLinearError,match='video_model_tariff_required'):
        campaigns.prepare(PROFILE,data)
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid',(A,))[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_ai_media_grants WHERE tenant_id=%s::uuid',(A,))[0]['n']==0


def test_all_cases_rejects_video_input_bound_before_prepare_and_rechecks_before_activation(ready,monkeypatch):
    db,card,media=_case(ready,(3,))
    provider=ready[1];calls_before=provider.call_count
    db.execute("UPDATE saas_crm_messages SET message_type='video' WHERE id=%s::uuid",(media[0],));db.commit()
    _mark_pending_consent(db,card['id'],media)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL','test-model')
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS','[\"test-model\"]')
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({
      'gpt-4.1':{'vision_low':8896},'gpt-4o-mini':{'vision_low':2500}}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({
      'test-model':{'input':10,'output':10},'gpt-4.1':{'input':2000000,'output':8000000},
      'gpt-4o-mini':{'input':150000,'output':600000}}))
    import tenant_ai_agent_key_policy as key_policy
    monkeypatch.setattr(key_policy,'platform_model',lambda:'test-model')
    monkeypatch.setattr(key_policy,'choose_api_credential',lambda *args:('synthetic-key','platform'))

    bad_key=str(uuid.uuid4())
    data={'request_id':bad_key,'all_existing_cases':True,'channel_ids':[],
      'analysis_agent_id':AGENT,'max_calls':10000,'max_tokens':160000000,
      'max_cost_microusd':10000000,'video_model':'gpt-4o-mini',
      'allow_external_media':True,'allow_paid_asr':True,
      'max_main_calls':10000,'max_asr_calls':10000,'max_vision_calls':10000}
    with pytest.raises(TenantLinearError,match='video_model_input_bound_exceeded'):
        campaigns.prepare(PROFILE,data)
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaigns WHERE id=%s::uuid',(bad_key,))[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_ai_media_grants WHERE campaign_id=%s::uuid',(bad_key,))[0]['n']==0

    # Defense in depth: even a changed/stale campaign model cannot activate a
    # grant or enqueue work after a model's cost bound has become invalid.
    key=str(uuid.uuid4());campaigns.prepare(PROFILE,{**data,'request_id':key,'video_model':'gpt-4.1'})
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    db.execute('UPDATE saas_knowledge_campaigns SET video_model=%s WHERE id=%s::uuid',('gpt-4o-mini',key));db.commit()
    consent={'expected_version':row['version'],'channel_ids':row['sources'],
      'max_calls':row['max_calls'],'max_tokens':row['max_tokens'],
      'confirm_external_processing':True,'media_policy':row['media_policy'],
      'all_cases_snapshot_hash':row['progress']['snapshot_hash'],
      'max_cost_microusd':10000000,'video_model':'gpt-4o-mini'}
    with pytest.raises(TenantLinearError,match='video_model_input_bound_exceeded'):
        campaigns.approve(PROFILE,key,consent)
    assert db.execute('SELECT status FROM saas_ai_media_grants WHERE campaign_id=%s::uuid',(key,))[0]['status']=='pending'
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,))[0]['n']==0
    assert provider.call_count==calls_before


def test_campaign_photo_model_migration_adds_column_to_existing_schema(ready):
    """Apply the additive migration to an isolated pre-change table shape."""
    db,_,_=_case(ready,(3,))
    db.execute('ALTER TABLE saas_knowledge_campaigns DROP COLUMN photo_model');db.commit()
    campaigns.create_schema(db);db.commit()
    assert db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='saas_knowledge_campaigns' AND column_name='photo_model'")


def test_printer_batch_acknowledges_one_current_source_retry_and_reuses_finished_photo(ready,monkeypatch):
    """End-to-end owner snapshot→$10 approval→worker, with providers mocked."""
    import tenant_media_evidence as evidence
    import tenant_knowledge_candidates_store as candidate_store
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_campaign_worker as campaign_worker
    import tenant_knowledge_media as media_service
    import tenant_ai_spend_budget as spend
    from unittest.mock import Mock

    db,card,ids=_case(ready,(3,4,5,6))
    _mark_pending_consent(db,card['id'],ids)
    extraction_id=db.execute('SELECT extraction_id FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['extraction_id']
    refs=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE id=%s::uuid',(extraction_id,))[0]['source_refs']
    # One prior attempt is genuinely unknown for the exact recorded version.
    results=[{'id':ids[0],'status':'unknown','code':'provider_timeout'}]
    results.extend({'id':message_id,'status':'unavailable','code':'vision_not_approved'} for message_id in ids[1:])
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',(json.dumps(results),extraction_id))
    # Production-shaped Connect message: loader-addressable provider message
    # UUID and conversation, but no separate mediaId/file_uuid in legacy raw.
    provider_message_id=str(uuid.uuid4())
    sparse_connect_raw={'id':provider_message_id,'channelId':CA,'conversationId':'client','type':'image'}
    db.execute('UPDATE saas_crm_messages SET external_id=%s,raw=%s::jsonb WHERE id=%s::uuid',
      ('connect:'+provider_message_id,json.dumps(sparse_connect_raw),ids[0]))
    db.commit()
    original_history=db.execute('SELECT source_refs,media_results FROM saas_knowledge_extractions WHERE id=%s::uuid',(extraction_id,))[0]
    # The fourth photo already has a completed receipt and must be reused.
    evidence.create_schema(db)
    finished=db.execute('SELECT external_id,body,raw FROM saas_crm_messages WHERE id=%s::uuid',(ids[3],))[0]
    finished_ref={'id':ids[3],'channel_id':CA,'external_id':finished['external_id'],'media_kind':'image',
      'media_version':evidence.media_version(finished['raw']),'body_hash':candidate_store.digest(finished['body'])}
    with db.cursor() as cur:evidence.persist(cur,A,[finished_ref],[{'id':ids[3],'status':'processed','code':'vision_observation','observation':'Synthetic saved label observation'}])

    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setattr(campaign_worker,'_connect',lambda:db)
    monkeypatch.setattr(service,'_connect',lambda:db)
    monkeypatch.setattr(spend,'_connect',lambda:db)
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL','test-model')
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS','["test-model"]')
    photo_model='gpt-4.1-2025-04-14'
    company_metadata={'enabled':True,'consent':True,'model':'test-model','photo_model':photo_model,
      'models':['test-model',photo_model],'daily_limit':50}
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata) VALUES(%s::uuid,'chat_ai','connected',%s,%s::jsonb) ON CONFLICT(tenant_id,provider) DO UPDATE SET secrets=EXCLUDED.secrets,metadata=EXCLUDED.metadata,status='connected'",
      (A,b'synthetic-company-key',json.dumps(company_metadata)))
    db.execute("UPDATE saas_ai_agents SET model='test-model' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));db.commit()
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({
      'test-model':{'input':10,'output':10},photo_model:{'input':2000000,'output':8000000}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'test-model':{'vision_low':2500}}))
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:10000000}))
    import tenant_ai_agent_key_policy as key_policy
    monkeypatch.setattr(key_policy,'platform_model',lambda:'test-model')
    monkeypatch.setattr(key_policy,'choose_api_credential',lambda *args:('synthetic-key','platform'))
    monkeypatch.setattr(service,'platform_model_allowed',lambda _model:True)
    class SyntheticCipher:
        def decrypt(self,_value):return json.dumps({'tenant_id':A,'provider':'chat_ai','api_key':'company-photo-key'}).encode()
    monkeypatch.setattr(service,'_fernet',lambda:SyntheticCipher())

    spend.ensure_schema(db)
    with db.cursor() as cur:
        spend.reserve_media(cur,A,'legacy-printer-unknown','knowledge_vision','test-model','vision_low',1)
    db.commit()
    spend.settle_media(A,'legacy-printer-unknown',operation='vision_low',attempted_units=1,reserved_units=1,unknown=True)
    old_hold=db.execute("SELECT id,reserved_microusd,state FROM saas_ai_spend_reservations WHERE call_key='legacy-printer-unknown'")[0]
    with db.cursor() as cur:default=admission.plan_existing_cases(cur,PROFILE)
    assert default['eligible_count']==2 and default['reused_count']==1 and default['unknown_media_count']==1
    with db.cursor() as cur:acknowledged=admission.plan_existing_cases(cur,PROFILE,acknowledge_prior_unknown=True)
    assert acknowledged['eligible_count']==3 and acknowledged['retry_unknown_count']==1 and acknowledged['reused_count']==1
    retry_media=next(m for m in acknowledged['cases'][0]['media'] if m['id']==ids[0])
    assert retry_media['source_version_verified'] is False
    assert retry_media['current_source_addressable'] is True and retry_media['retry_authorized'] is True
    assert retry_media['retry_source_basis']=='current_connect_message'
    planned_routes=list(admission.existing_routes(acknowledged,AGENT,'gpt-4.1',photo_model))
    assert len({route['message_ids'][0] for route in planned_routes})==3
    assert next(route for route in planned_routes if route['message_ids']==[ids[0]])['campaign_photo_model']==photo_model
    retry_route=next(route for route in planned_routes if route['message_ids']==[ids[0]])
    assert retry_route['owner_retry_unknown'] is True
    assert retry_route['owner_retry_basis']=='current_connect_message'

    generation=Mock(return_value=(json.dumps({'cards':[]}),{'input_tokens':20,'output_tokens':10,'total_tokens':30}))
    monkeypatch.setattr(service,'generate',generation)
    dispatched=[];collect_entries=[]
    def collect(_profile,item,check_before_ai=None):
        collect_entries.append(item)
        route=item['route'];message_id=item['refs'][0]['id']
        media_kind=item['refs'][0]['media_kind']
        assert media_kind=='image' and route.get('campaign_photo_model')==photo_model
        assert item['model']=='test-model' and item['photo_api_key']=='company-photo-key'
        spend_row=db.execute("SELECT model,kind,reserved_microusd,campaign_id FROM saas_ai_spend_reservations WHERE call_key=%s",
          ('extraction:'+str(item['id'])+':vision',))[0]
        assert spend_row['model']==photo_model and spend_row['kind']=='knowledge_photo_observation'
        assert spend_row['reserved_microusd']==8896
        calls=1
        assert spend_row['campaign_id']==campaign_id
        item['vision_attempts']=calls
        usage={'input_tokens':100,'output_tokens':20,'total_tokens':120}
        item['vision_usage']=[usage for _ in range(calls)]
        expected=next(m['source_version'] for m in acknowledged['cases'][0]['media'] if m['id']==message_id)
        dispatched.append((message_id,route.get('owner_retry_unknown') is True,item['refs'][0].get('media_version'),expected))
        return [],[{'id':message_id,'kind':media_kind,'status':'partial' if media_kind=='video' else 'processed',
          'code':'vision_observation','observation':'Synthetic observation'}]
    monkeypatch.setattr(media_service,'collect',collect)
    campaign_id=str(uuid.uuid4())
    data={'request_id':campaign_id,'all_existing_cases':True,'channel_ids':[],
      'analysis_agent_id':AGENT,'max_calls':10000,'max_tokens':160000000,'max_cost_microusd':10000000,
      'video_model':None,'acknowledge_prior_unknown':True,'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':10000,'max_asr_calls':0,'max_vision_calls':10000}
    assert campaigns.prepare(PROFILE,data)==campaign_id
    prepared=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign_id,))[0]
    assert prepared['photo_model']==photo_model and prepared['video_model'] is None
    assert prepared['progress']['retry_unknown_count']==1
    approved=campaigns.approve(PROFILE,campaign_id,{'expected_version':prepared['version'],'channel_ids':prepared['sources'],
      'max_calls':prepared['max_calls'],'max_tokens':prepared['max_tokens'],'confirm_external_processing':True,
      'media_policy':prepared['media_policy'],'all_cases_snapshot_hash':prepared['progress']['snapshot_hash'],
      'max_cost_microusd':10000000,'video_model':None,'photo_model':photo_model,'acknowledge_prior_unknown':True})
    assert approved['campaign']['status']=='running'
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(campaign_id,))[0]['n']==3
    for _ in range(3):assert campaign_worker.tick() is True
    assert {message_id for message_id,*_ in dispatched}==set(ids[:3])
    assert all(actual==expected for _,_,actual,expected in dispatched)
    assert sum(retry for _,retry,_,_ in dispatched)==1 and any(message_id==ids[0] and retry for message_id,retry,_,_ in dispatched)
    assert generation.call_count==3
    configured=db.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(A,))[0]['metadata']
    assert configured['model']=='test-model' and configured['photo_model']==photo_model
    assert db.execute('SELECT model FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]['model']=='test-model'
    hold_after=db.execute("SELECT id,reserved_microusd,state FROM saas_ai_spend_reservations WHERE call_key='legacy-printer-unknown'")[0]
    assert hold_after==old_hold and hold_after['state']=='unknown'
    unchanged_history=db.execute('SELECT source_refs,media_results FROM saas_knowledge_extractions WHERE id=%s::uuid',(extraction_id,))[0]
    assert unchanged_history==original_history,'the old extraction refs and claims remain immutable during a current-message retry'
    grant_reservations=db.execute('SELECT kind,state FROM saas_ai_spend_reservations WHERE grant_id=%s::uuid',(campaign_id,))
    assert len(grant_reservations)==6 and {r['kind'] for r in grant_reservations}=={
      'live_case_extraction','knowledge_photo_observation'}
    with pytest.raises(TenantLinearError,match='cannot be reset or topped up'):
        campaigns.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4())})


@pytest.mark.parametrize('change',[
    {'channel':'connect:'+str(uuid.uuid4())},
    {'raw':{'conversationId':'another-conversation'}},
    {'external_id':'connect:not-a-uuid'},
    {'message_type':'text'},
])
def test_current_connect_source_addressability_fails_closed_on_scope_mismatch(change):
    from tenant_case_media_admission import _current_source_addressable
    provider_message_id=str(uuid.uuid4())
    row={'id':str(uuid.uuid4()),'external_id':'connect:'+provider_message_id,
         'message_type':'image','channel':'connect:'+CA,
         'raw':{'conversationId':'client'}}
    assert _current_source_addressable(row,CA,'client') is True
    assert _current_source_addressable({**row,**change},CA,'client') is False
    assert _current_source_addressable(row,str(uuid.uuid4()),'client') is False
    assert _current_source_addressable(row,CA,'other') is False


def test_current_connect_retry_does_not_publish_under_unverified_metadata_version(ready):
    import tenant_media_evidence as evidence
    db,card,ids=_case(ready,(3,))
    evidence.create_schema(db)
    provider_message_id=str(uuid.uuid4())
    sparse_raw={'id':provider_message_id,'channelId':CA,'conversationId':'client','type':'image'}
    db.execute('UPDATE saas_crm_messages SET external_id=%s,raw=%s::jsonb WHERE id=%s::uuid',
      ('connect:'+provider_message_id,json.dumps(sparse_raw),ids[0]));db.commit()
    message=db.execute('SELECT external_id,body,raw FROM saas_crm_messages WHERE id=%s::uuid',(ids[0],))[0]
    from tenant_case_media_admission import _has_current_media_version
    assert _has_current_media_version(message['raw']) is False
    ref={'id':ids[0],'external_id':message['external_id'],'channel_id':CA,'media_kind':'image',
         'media_version':evidence.media_version(message['raw']),'body_hash':__import__('tenant_knowledge_candidates_store').digest(message['body'])}
    receipt={'id':ids[0],'kind':'image','status':'processed','code':'vision_observation',
             'observation':'Synthetic observation from an explicitly acknowledged current message'}
    with db.cursor() as cur:
        evidence.persist(cur,A,[ref],[receipt],skip_unverified_source_ids=[ids[0]])
    db.commit()
    assert evidence.reusable(PROFILE,{**ref,'lead_id':41}) is None


@pytest.mark.parametrize('mutation',['conversation_removed','message_deleted'])
def test_batch_worker_fence_rejects_source_change_or_deletion_after_approval(ready,monkeypatch,mutation):
    import tenant_ai_agent_key_policy as key_policy
    db,card,ids=_case(ready,(3,))
    _mark_pending_consent(db,card['id'],ids)
    extraction_id=db.execute('SELECT extraction_id FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['extraction_id']
    provider_message_id=str(uuid.uuid4())
    raw={'id':provider_message_id,'channelId':CA,'conversationId':'client','type':'image'}
    db.execute('UPDATE saas_crm_messages SET external_id=%s,raw=%s::jsonb WHERE id=%s::uuid',
      ('connect:'+provider_message_id,json.dumps(raw),ids[0]))
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE id=%s::uuid',
      (json.dumps([{'id':ids[0],'status':'unknown','code':'provider_timeout'}]),extraction_id));db.commit()
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL','test-model')
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS','["test-model"]')
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'test-model':{'input':10,'output':10}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'test-model':{'vision_low':2500}}))
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:10000000}))
    monkeypatch.setattr(key_policy,'platform_model',lambda:'test-model')
    monkeypatch.setattr(key_policy,'choose_api_credential',lambda *a:('synthetic-key','platform'))
    key=str(uuid.uuid4())
    data={'request_id':key,'all_existing_cases':True,'channel_ids':[],'analysis_agent_id':AGENT,
      'max_calls':10000,'max_tokens':160000000,'max_cost_microusd':10000000,'video_model':None,
      'acknowledge_prior_unknown':True,'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':10000,'max_asr_calls':0,'max_vision_calls':10000}
    campaigns.prepare(PROFILE,data)
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    campaigns.approve(PROFILE,key,{'expected_version':row['version'],'channel_ids':row['sources'],
      'max_calls':row['max_calls'],'max_tokens':row['max_tokens'],'confirm_external_processing':True,
      'media_policy':row['media_policy'],'all_cases_snapshot_hash':row['progress']['snapshot_hash'],
      'max_cost_microusd':10000000,'video_model':None,'acknowledge_prior_unknown':True})
    claimed=campaigns.claim(A,key)
    assert claimed
    if mutation=='conversation_removed':
        db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',
          (json.dumps({'id':provider_message_id,'channelId':CA,'type':'image'}),ids[0]))
    else:
        db.execute('DELETE FROM saas_crm_messages WHERE id=%s::uuid',(ids[0],))
    db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError,match='case snapshot or media source changed'):
        worker.check_fence(cur,A,claimed['id'])


def test_fixed_grant_over_cap_blocks_before_any_provider_call(ready,monkeypatch):
    db,card,media=_case(ready,(3,))
    db.execute("UPDATE saas_crm_messages SET message_type='video' WHERE id=%s::uuid",(media[0],));db.commit()
    _mark_pending_consent(db,card['id'],media)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL','test-model')
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS','["test-model"]')
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'test-model':{'input':10,'output':10},'gpt-4.1':{'input':2000000,'output':8000000}}))
    monkeypatch.setenv('AI_AGENTS_TRANSCRIPTION_MODEL','whisper-1')
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({
      'gpt-4.1':{'vision_low':6_000_000},'whisper-1':{'asr':400}}))
    import tenant_ai_agent_key_policy as key_policy
    import tenant_knowledge_campaign_worker as campaign_worker
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_media as media_service
    import tenant_ai_spend_budget as spend
    from unittest.mock import Mock
    monkeypatch.setattr(key_policy,'platform_model',lambda:'test-model')
    monkeypatch.setattr(key_policy,'choose_api_credential',lambda *args:('synthetic-key','platform'))
    monkeypatch.setattr(campaign_worker,'_connect',lambda:db)
    monkeypatch.setattr(service,'_connect',lambda:db)
    monkeypatch.setattr(service,'platform_model_allowed',lambda _model:True)
    monkeypatch.setattr(spend,'_connect',lambda:db)
    generation=Mock(return_value=(json.dumps({'cards':[]}),{'total_tokens':20}))
    collect=Mock(return_value=([],[]))
    monkeypatch.setattr(service,'generate',generation)
    monkeypatch.setattr(media_service,'collect',collect)
    campaign=str(uuid.uuid4())
    data={'request_id':campaign,'all_existing_cases':True,'channel_ids':[],
      'analysis_agent_id':AGENT,'max_calls':10000,'max_tokens':160000000,'max_cost_microusd':10000000,
      'video_model':'gpt-4.1','allow_external_media':True,'allow_paid_asr':True,
      'max_main_calls':10000,'max_asr_calls':10000,'max_vision_calls':10000}
    campaigns.prepare(PROFILE,data)
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign,))[0]
    campaigns.approve(PROFILE,campaign,{'expected_version':row['version'],'channel_ids':row['sources'],
      'max_calls':row['max_calls'],'max_tokens':row['max_tokens'],'confirm_external_processing':True,
      'media_policy':row['media_policy'],'all_cases_snapshot_hash':row['progress']['snapshot_hash'],
      'max_cost_microusd':10000000,'video_model':'gpt-4.1'})
    assert campaign_worker.tick() is True
    assert generation.call_count==0 and collect.call_count==0
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE grant_id=%s::uuid',(campaign,))[0]['n']==0
    assert db.execute('SELECT status FROM saas_knowledge_campaigns WHERE id=%s::uuid',(campaign,))[0]['status']=='paused'


def test_changed_media_and_unknown_hold_never_dispatch_again(ready,monkeypatch):
    db,card,ids=_case(ready,(3,))
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    key=str(uuid.uuid4())
    data={'request_id':key,'case_id':card['id'],'channel_ids':[CA],
      'analysis_agent_id':AGENT,'max_calls':3,'max_tokens':24000,
      'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':1,'max_asr_calls':0,'max_vision_calls':2}
    campaigns.prepare(PROFILE,data)
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    campaigns.approve(PROFILE,key,{'expected_version':row['version'],'channel_ids':[CA],
      'max_calls':3,'max_tokens':24000,'confirm_external_processing':True,
      'media_policy':row['media_policy'],'case_id':card['id'],
      'case_plan_hash':row['progress']['plan_hash']})
    claimed=campaigns.claim(A,key)
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({'mediaId':'replacement'}),ids[0]));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError,match='source changed'):
        worker.check_fence(cur,A,claimed['id'])
    db.execute("UPDATE saas_knowledge_campaign_fragments SET lease_until=now()-interval '1 second' WHERE id=%s::uuid",(claimed['id'],))
    db.commit()
    assert campaigns.claim(A,key) is None
    fragment=db.execute('SELECT status FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid',(claimed['id'],))[0]
    assert fragment['status']=='unknown'
    assert db.execute('SELECT reserved_calls FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]['reserved_calls']==3


def test_insufficient_case_budget_never_queues_first_attachment(ready,monkeypatch):
    db,card,_=_case(ready)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    key=str(uuid.uuid4())
    campaigns.prepare(PROFILE,{'request_id':key,'case_id':card['id'],'channel_ids':[CA],
      'analysis_agent_id':AGENT,'max_calls':3,'max_tokens':24000,
      'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':2})
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    with pytest.raises(TenantLinearError,match='Budget does not cover all case attachments'):
        campaigns.approve(PROFILE,key,{'expected_version':row['version'],'channel_ids':[CA],
          'max_calls':3,'max_tokens':24000,'confirm_external_processing':True,
          'media_policy':row['media_policy'],'case_id':card['id'],
          'case_plan_hash':row['progress']['plan_hash']})
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments')[0]['n']==0
    assert db.execute('SELECT status FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]['status']=='awaiting_budget'


def test_stopped_case_campaign_reports_cancelled_or_incomplete(ready,monkeypatch):
    db,card,_=_case(ready)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    key=str(uuid.uuid4())
    campaigns.prepare(PROFILE,{'request_id':key,'case_id':card['id'],'channel_ids':[CA],
      'analysis_agent_id':AGENT,'max_calls':6,'max_tokens':48000,
      'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':4})
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    campaigns.approve(PROFILE,key,{'expected_version':row['version'],'channel_ids':[CA],
      'max_calls':6,'max_tokens':48000,'confirm_external_processing':True,
      'media_policy':row['media_policy'],'case_id':card['id'],
      'case_plan_hash':row['progress']['plan_hash']})
    assert campaigns.get(PROFILE,key)['campaign']['progress']['phase']=='processing'
    version=db.execute('SELECT version FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]['version']
    campaigns.control(PROFILE,key,version,'stop')
    assert campaigns.get(PROFILE,key)['campaign']['progress']['phase']=='cancelled'
    db.execute("UPDATE saas_knowledge_campaign_fragments SET status='blocked',error_code='source_unavailable' WHERE campaign_id=%s::uuid",(key,))
    db.commit()
    assert campaigns.get(PROFILE,key)['campaign']['progress']['phase']=='incomplete'
