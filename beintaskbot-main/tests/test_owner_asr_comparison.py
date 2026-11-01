"""Synthetic approved scope + isolated PostgreSQL/WASM; never real provider calls."""
import hashlib
import io
import json
import threading
import wave
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,seed_job
import tenant_owner_asr_comparison as flow
import tenant_ai_spend_budget as budget
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def ready(db,monkeypatch):
    seed_job(db)
    source={'channel_id':CA,'conversation_id':'client','message_id':'m1'}
    monkeypatch.setattr(flow,'APPROVED_SCOPE',flow.scope_digest(A,source))
    monkeypatch.setattr(flow,'_connect',lambda:db)
    monkeypatch.setattr(budget,'_connect',lambda:db)
    monkeypatch.setattr('tenant_ai_agent_runtime_flags.enabled',lambda _:True)
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1_000_000}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-4o-mini-transcribe':{'asr':30000}}))
    metadata={'enabled':True,'consent':True,'audio_enabled':True,'daily_limit':20,'copilot_daily_limit':20}
    db.execute("UPDATE saas_crm_messages SET message_type='audio',body='SYNTHETIC_REFERENCE_NEVER_SEND',raw=%s::jsonb WHERE tenant_id=%s::uuid",(json.dumps({'mediaId':'synthetic-media','voice_transcription':{'status':'completed','text':'SYNTHETIC_REFERENCE_NEVER_SEND'}}),A))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata) VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)",(A,b'synthetic-cipher',json.dumps(metadata)));db.commit()
    monkeypatch.setattr(flow,'_company_key',Mock(return_value=(A,metadata,'synthetic-company-key')))
    download=Mock(return_value=(b'original-approved-audio','audio/ogg'))
    monkeypatch.setattr(flow,'_download',download)
    monkeypatch.setattr(flow,'_audio',lambda content,mime:(content,'ogg',mime,8.95))
    provider=Mock(side_effect=lambda key,model,format_,content,ext,mime:{'transcript':'ASR '+model,'usage':{'seconds':8.95},'diagnostics':{}})
    monkeypatch.setattr(flow,'_transcribe',provider)
    flow.schema(db)
    return db,source,provider,download


def command(source,version):
    return {**source,'version':version,'confirmed':True,'max_calls':2,'max_cost_microusd':60000}


def execute(ready):
    _,source,_,_=ready
    return flow.execute(PROFILE,command(source,flow.preview(PROFILE,source)['version']))


def test_two_atomic_calls_original_bytes_only_idempotent_no_production_overwrite(ready):
    db,source,provider,download=ready
    before=db.execute('SELECT id,body,raw FROM saas_crm_messages ORDER BY id')
    receipts=db.execute('SELECT * FROM saas_media_evidence_receipts')
    agents=db.execute('SELECT id,model,style FROM saas_ai_agents ORDER BY id');settings=db.execute('SELECT metadata,secrets FROM saas_tenant_integrations');db.commit()
    preview=flow.preview(PROFILE,source)
    assert preview['can_execute'] and sum(v['reserve_microusd'] for v in preview['variants'])==60000
    provider.assert_not_called();download.assert_not_called()
    result=execute(ready)
    assert provider.call_count==2 and download.call_count==1
    assert [call.args[1:3] for call in provider.call_args_list]==list(flow.VARIANTS)
    assert all(call.args[3]==b'original-approved-audio' for call in provider.call_args_list)
    assert result['byte_hash']==hashlib.sha256(b'original-approved-audio').hexdigest()
    assert result['upload_hash']==result['byte_hash'] and result['upload_mime']=='audio/ogg'
    assert all(v['parameters']['language']=='az' and v['parameters']['prompt_supplied'] is False for v in result['variants'])
    assert all(v['state']=='completed' and v['accounting_state']=='settled' for v in result['variants'])
    assert flow.execute(PROFILE,command(source,preview['version']))==result
    assert provider.call_count==2
    assert db.execute('SELECT id,body,raw FROM saas_crm_messages ORDER BY id')==before
    assert db.execute('SELECT id,model,style FROM saas_ai_agents ORDER BY id')==agents
    assert db.execute('SELECT metadata,secrets FROM saas_tenant_integrations')==settings
    assert db.execute('SELECT reserved_calls FROM saas_chat_ai_runs')[0]['reserved_calls']==2
    assert db.execute('SELECT * FROM saas_media_evidence_receipts')==receipts
    assert 'synthetic-company-key' not in json.dumps(result)
    assert 'SYNTHETIC_REFERENCE_NEVER_SEND' not in json.dumps(result)
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n']==len(before)


def test_unknown_hold_no_retry_after_new_source_config_version_or_new_id(ready):
    db,source,provider,_=ready
    provider.side_effect=[RuntimeError('SECRET_PROVIDER_ERROR'),{'transcript':'second','usage':{},'diagnostics':{}}]
    result=execute(ready)
    assert sorted(v['state'] for v in result['variants'])==['completed','unknown']
    rows=db.execute('SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations');db.commit()
    assert any(r['state']=='unknown' and r['reserved_microusd']==30000 and r['actual_microusd'] is None for r in rows)
    db.execute("UPDATE saas_crm_messages SET raw=raw||'{\"mediaId\":\"new-version\"}'::jsonb WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_ai_agents SET style='creative',version=version+1 WHERE tenant_id=%s::uuid",(A,));db.commit()
    new=flow.preview(PROFILE,source)
    assert new['admitted'] and not new['can_execute'] and new['historical'] and not new['context_current']
    flow.execute(PROFILE,command(source,'new-id-or-version-does-not-retry'))
    assert provider.call_count==2
    with pytest.raises(TenantLinearError):flow.execute(PROFILE,{**command(source,new['version']),'request_id':'new-uuid'})
    assert 'SECRET_PROVIDER_ERROR' not in json.dumps(result)


@pytest.mark.parametrize('change,code',[
    ('missing_quote','comparison_quote_unavailable'),
    ('over_quote','comparison_cost_cap_exceeded'),
])
def test_preview_fail_closed_without_config_mutation(ready,monkeypatch,change,code):
    db,source,provider,_=ready
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({} if change=='missing_quote' else {'gpt-4o-mini-transcribe':{'asr':31000}}))
    result=flow.preview(PROFILE,source)
    assert result['blocked'] and result['code']==code and not result['can_execute']
    with pytest.raises(TenantLinearError):flow.execute(PROFILE,command(source,result['version']))
    assert db.execute('SELECT count(*) AS n FROM saas_owner_asr_comparisons')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    provider.assert_not_called()


@pytest.mark.parametrize('cap',[1,59999])
def test_reserve_both_upfront_rolls_back_if_tenant_cannot_cover_second(ready,monkeypatch,cap):
    db,source,provider,_=ready
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:cap}))
    with pytest.raises(TenantLinearError):execute(ready)
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_owner_asr_comparisons')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==0
    provider.assert_not_called()


@pytest.mark.parametrize('field',['daily_limit','copilot_daily_limit'])
def test_existing_company_and_connect_daily_caps_count_both_calls(ready,field):
    db,_,provider,_=ready
    db.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,%s::text[],'1'::jsonb) WHERE tenant_id=%s::uuid",([field],A));db.commit()
    with pytest.raises(TenantLinearError):execute(ready)
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    provider.assert_not_called()


def test_owner_access_scope_assignment_and_version_fences(ready):
    db,source,provider,_=ready
    initial=flow.preview(PROFILE,source)
    for profile,data in [({'tenant_id':B,'telegram_id':22},source),(PROFILE,{**source,'channel_id':CB}),(PROFILE,{**source,'message_id':'other'})]:
        with pytest.raises(TenantLinearError):flow.preview(profile,data)
    db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):flow.preview(PROFILE,source)
    db.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_ai_agents SET model='different',version=version+1 WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):flow.execute(PROFILE,command(source,initial['version']))
    db.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,));db.commit()
    with pytest.raises(TenantLinearError):flow.preview(PROFILE,source)
    provider.assert_not_called()


def test_other_owner_cannot_read_or_execute_existing_owner_results(ready):
    db,source,provider,_=ready
    execute(ready)
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,99,'owner','Other owner')",(A,));db.commit()
    for fn,args in [(flow.preview,(source,)),(flow.execute,(command(source,'anything'),))]:
        with pytest.raises(TenantLinearError):fn({'tenant_id':A,'telegram_id':99},*args)
    assert provider.call_count==2


def test_process_crash_after_durable_dispatch_never_replays_or_releases_holds(ready):
    db,source,provider,_=ready
    provider.side_effect=SystemExit('synthetic process crash')
    with pytest.raises(SystemExit):execute(ready)
    provider.assert_called_once()
    existing=flow.preview(PROFILE,source)
    assert sorted(v['state'] for v in existing['variants'])==['reserved','unknown']
    flow.execute(PROFILE,command(source,'arbitrary-new-version'))
    provider.assert_called_once()
    assert all(row['state']=='reserved' and row['actual_microusd'] is None for row in db.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations'))


def test_concurrent_post_during_first_provider_call_returns_ledger_without_dispatch(ready):
    db,source,provider,_=ready
    entered=threading.Event();release=threading.Event();result=[];errors=[]
    def transcribe(*args):
        if provider.call_count==1:entered.set();assert release.wait(10)
        return {'transcript':'synthetic','usage':{},'diagnostics':{}}
    provider.side_effect=transcribe
    version=flow.preview(PROFILE,source)['version']
    def run():
        try:result.append(flow.execute(PROFILE,command(source,version)))
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=run);thread.start();assert entered.wait(10)
    try:
        concurrent=flow.execute(PROFILE,command(source,version))
        assert concurrent['admitted'] and not concurrent['can_execute'] and provider.call_count==1
    finally:release.set();thread.join(10)
    assert not errors and len(result)==1 and provider.call_count==2


@pytest.mark.parametrize('uncapped',[False,True])
def test_pre_dispatch_media_failure_is_retryable_without_consuming_paid_case(ready,monkeypatch,uncapped):
    db,source,provider,download=ready
    if uncapped:monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD','{}')
    download.side_effect=RuntimeError('SECRET download failure')
    with pytest.raises(TenantLinearError,match='comparison_pre_dispatch_failed'):execute(ready)
    assert db.execute('SELECT count(*) AS n FROM saas_owner_asr_comparisons')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    provider.assert_not_called()
    download.side_effect=None
    result=execute(ready)
    assert all(v['state']=='completed' for v in result['variants'])
    assert provider.call_count==2 and download.call_count==2
    execute(ready);assert provider.call_count==2 and download.call_count==2


@pytest.mark.parametrize('uncapped',[False,True])
def test_unused_admitted_claims_release_but_admission_cannot_replay(ready,monkeypatch,uncapped):
    db,source,provider,_=ready
    if uncapped:monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD','{}')
    monkeypatch.setattr(flow,'_mark',Mock(side_effect=TenantLinearError('synthetic pre-dispatch authority change',409)))
    result=execute(ready)
    assert all(v['state']=='skipped' and v['accounting_state']=='released' for v in result['variants'])
    assert db.execute('SELECT reserved_calls FROM saas_chat_ai_runs')[0]['reserved_calls']==0
    execute(ready);provider.assert_not_called()


def test_assignment_change_between_calls_prevents_second_provider(ready):
    db,source,provider,_=ready
    def changed(*args):
        db.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,));db.commit()
        return {'transcript':'first','usage':{},'diagnostics':{}}
    provider.side_effect=changed
    with pytest.raises(TenantLinearError):execute(ready)
    provider.assert_called_once()
    assert sorted(r['state'] for r in db.execute('SELECT state FROM saas_owner_asr_variants'))==['completed','skipped']


@pytest.mark.parametrize('model,format_',[('whisper-1','verbose_json'),('gpt-4o-mini-transcribe','json')])
def test_provider_payload_exactly_two_variants_language_no_reference_prompt(monkeypatch,model,format_):
    # The real adapter, independent of the SQL fixture's ASR mock.
    payload={'text':'Synthetic transcript','language':'az','duration':8.95,'segments':[{'start':0,'end':8.95,'avg_logprob':-0.1,'no_speech_prob':0.01}],
        'logprobs':[{'token':'Synthetic','logprob':-0.1}], 'usage':{'input_tokens':30,'output_tokens':4,'total_tokens':34}}
    response=Mock(status_code=200);response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    response.iter_content.return_value=[json.dumps(payload).encode()]
    post=Mock(return_value=response);monkeypatch.setattr(flow.requests,'post',post)
    result=flow._transcribe('synthetic-key',model,format_,b'ORIGINAL_AUDIO_ONLY','ogg','audio/ogg')
    data=post.call_args.kwargs['data']
    assert data=={'model':model,'language':'az','response_format':format_,**({'include[]':'logprobs'} if model.startswith('gpt-') else {})}
    assert set(post.call_args.kwargs['files'])=={'file'}
    assert post.call_args.kwargs['files']['file'][1]==b'ORIGINAL_AUDIO_ONLY'
    assert post.call_args.kwargs['allow_redirects'] is False
    assert result['transcript']=='Synthetic transcript'
    assert 'synthetic-key' not in json.dumps(result)


def test_real_audio_duration_validation_is_bounded_and_preserves_original_bytes():
    raw=io.BytesIO()
    with wave.open(raw,'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0\0'*143200)
    original=raw.getvalue();content,extension,mime,duration=flow._audio(original,'audio/wav')
    assert content==original and extension=='wav' and mime=='audio/wav' and 8.94<duration<8.96


def test_outcome_transitions_cannot_release_or_overwrite_dispatched_unknown(ready):
    db,source,provider,_=ready
    item,_=flow._admit(PROFILE,source,flow.preview(PROFILE,source)['version'])
    model=flow.VARIANTS[0][0]
    flow._outcome(item,model,'completed',{'transcript':'forbidden transition'})
    assert db.execute('SELECT state FROM saas_owner_asr_variants WHERE model=%s',(model,))[0]['state']=='reserved'
    flow._mark(PROFILE,item,model,'synthetic-byte-hash',8.95)
    flow._outcome(item,model,'skipped',code='must_not_release_sending')
    assert db.execute('SELECT state FROM saas_owner_asr_variants WHERE model=%s',(model,))[0]['state']=='sending'
    assert all(r['state']=='reserved' for r in db.execute('SELECT state FROM saas_ai_spend_reservations'))
    flow._outcome(item,model,'unknown',code='provider_outcome_unknown')
    flow._outcome(item,model,'skipped')
    flow._outcome(item,model,'completed',{'transcript':'must_not_overwrite_unknown'})
    row=db.execute('SELECT state,result FROM saas_owner_asr_variants WHERE model=%s',(model,))[0]
    assert row=={'state':'unknown','result':None}
    assert any(r['state']=='unknown' and r['actual_microusd'] is None for r in db.execute('SELECT state,actual_microusd FROM saas_ai_spend_reservations'))
    provider.assert_not_called()


def test_comparison_does_not_require_enabling_automatic_engine(ready,monkeypatch):
    db,source,provider,_=ready
    monkeypatch.setattr('tenant_ai_agent_runtime_flags.enabled',lambda _:False)
    db.execute('UPDATE saas_ai_agent_settings SET enabled=FALSE,auto_send_enabled=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    assert flow.preview(PROFILE,source)['can_execute']
    execute(ready);assert provider.call_count==2
    db.execute('UPDATE saas_ai_agent_settings SET emergency_stop=TRUE WHERE tenant_id=%s::uuid',(A,));db.commit()
    with pytest.raises(TenantLinearError):flow.preview(PROFILE,source)


@pytest.mark.parametrize('setting',[{}, {A:0}])
def test_uncapped_explicit_case_tracks_both_fixed_holds_without_global_config_change(ready,monkeypatch,setting):
    db,source,provider,_=ready
    encoded=json.dumps(setting);monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',encoded)
    with db.cursor() as cur:
        assert budget.reserve_media(cur,A,'ordinary-uncapped','ordinary','whisper-1','asr',1) is None
    db.commit()
    result=execute(ready)
    assert provider.call_count==2 and all(v['accounting_state']=='settled' for v in result['variants'])
    rows=db.execute('SELECT reserved_microusd FROM saas_ai_spend_reservations')
    assert len(rows)==2 and sum(r['reserved_microusd'] for r in rows)==60000
    import os
    assert os.environ['AI_TENANT_SPEND_LIMITS_MICROUSD']==encoded


def test_uncapped_unknown_hold_and_pre_dispatch_release_are_not_refunded(ready,monkeypatch):
    db,source,provider,_=ready
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD','{}')
    provider.side_effect=[RuntimeError('synthetic uncertain outcome'),{'transcript':'second','usage':{},'diagnostics':{}}]
    result=execute(ready)
    assert any(v['state']=='unknown' and v['accounting_state']=='unknown' for v in result['variants'])
    execute(ready);assert provider.call_count==2
    assert sum(r['reserved_microusd'] for r in db.execute('SELECT reserved_microusd FROM saas_ai_spend_reservations'))==60000


def test_orphan_unknown_common_reservation_never_creates_new_paid_run(ready):
    db,source,provider,_=ready
    import uuid
    key='owner-asr:'+flow.APPROVED_SCOPE+':whisper-1'
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,%s,'whisper-1','owner_asr_comparison',30000,'unknown')",(A,str(uuid.uuid4()),key));db.commit()
    with pytest.raises(TenantLinearError,match='existing_accounting_without_case'):execute(ready)
    assert db.execute('SELECT state FROM saas_ai_spend_reservations')==[{'state':'unknown'}]
    assert db.execute('SELECT count(*) AS n FROM saas_owner_asr_comparisons')[0]['n']==0
    provider.assert_not_called()


@pytest.mark.parametrize('registry',['broken','[]',json.dumps({A:'bad'}),json.dumps({A:-1})])
def test_invalid_explicit_cap_registry_is_blocked(ready,monkeypatch,registry):
    _,source,provider,_=ready
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',registry)
    assert flow.preview(PROFILE,source)['code']=='comparison_budget_configuration_invalid'
    with pytest.raises(TenantLinearError):execute(ready)
    provider.assert_not_called()


def test_source_change_during_download_cannot_admit_original_preview(ready):
    db,source,provider,download=ready
    def changed(*args):
        db.execute("UPDATE saas_crm_messages SET raw=raw||'{\"mediaId\":\"changed-during-download\"}'::jsonb WHERE tenant_id=%s::uuid",(A,));db.commit()
        return b'original-approved-audio','audio/ogg'
    download.side_effect=changed
    with pytest.raises(TenantLinearError):execute(ready)
    assert db.execute('SELECT count(*) AS n FROM saas_owner_asr_comparisons')[0]['n']==0
    provider.assert_not_called()
