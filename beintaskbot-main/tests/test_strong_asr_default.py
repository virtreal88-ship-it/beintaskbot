"""One-company shared standalone ASR rollout; synthetic scope/offline only."""
import hashlib
import io
import json
import subprocess
import wave
from unittest.mock import Mock
import pytest
import tenant_ai_asr_model as asr
import tenant_chat_ai_provider as provider
import tenant_connect_voice as voice
from tenant_episode_media import ManualExecution,AutoExecution
from tenant_linear_policy import TenantLinearError

A='00000000-0000-0000-0000-000000000001'
B='00000000-0000-0000-0000-000000000002'

@pytest.fixture(autouse=True)
def scoped(monkeypatch):
    monkeypatch.setattr(asr,'SHARED_ASR_TENANT_DIGEST',hashlib.sha256(('shared-dialog-asr-rollout:v1:'+A).encode()).hexdigest())
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')


def authority(kind='manual',tenant=A,source='company',raw='whisper-1',payload=None):
    config={'audio_enabled':True,'consent':True,'transcription_model':raw}
    if kind=='manual':
        result=ManualExecution(tenant,11,{'channel_id':B,'conversation_id':'synthetic','lead_id':41,'_agent_id':B},config,'v','synthetic','gpt-4.1','auto',[])
    else:
        result=AutoExecution({'tenant_id':tenant,'channel_id':B,'conversation_id':'synthetic','lead_id':41,'agent_id':B,'provider':'connect','payload':payload or {}},
            {'config':config,'credential_source':source,'model':'gpt-4.1','api_key':'synthetic'})
    result.credential_source=source
    return result

@pytest.mark.parametrize('kind',['manual','auto'])
def test_only_shared_target_company_audio_maps_transiently(kind):
    execution=authority(kind)
    item=execution.item({'media_kind':'audio'},{})
    assert item['config']['transcription_model']=='gpt-transcribe' and item['_shared_dialog_asr']
    assert execution.config['transcription_model']=='whisper-1'
    assert execution.model=='gpt-4.1' and execution.agent_id==B
    assert voice.transcription_model(item)=='gpt-transcribe'

@pytest.mark.parametrize('change', ['other_tenant','platform','mini','custom','empty','video','pilot_case','pilot_followup'])
def test_other_paths_do_not_opt_in(change):
    execution=authority('auto',tenant=B if change=='other_tenant' else A,
        source='platform' if change=='platform' else 'company',
        raw={'mini':'gpt-4o-mini-transcribe','custom':'custom-asr','empty':''}.get(change,'whisper-1'),
        payload={'pilot_customer_case_reply':True} if change=='pilot_case' else {'pilot_developer_followup':True} if change=='pilot_followup' else {})
    item=execution.item({'media_kind':'video' if change=='video' else 'voice'},{})
    assert not item.get('_shared_dialog_asr', False)
    assert item['config']['transcription_model']==execution.config['transcription_model']


def test_legacy_resolver_and_public_registry_exactly_unchanged(monkeypatch):
    from tenant_ai_spend_budget import media_price
    assert asr.MODELS==('gpt-4o-mini-transcribe','whisper-1')
    assert media_price('gpt-transcribe','asr') is None
    with pytest.raises(TenantLinearError):asr.quote('gpt-transcribe')
    monkeypatch.delenv('AI_AGENTS_TRANSCRIPTION_MODEL',raising=False)
    assert voice.transcription_model({'credential_source':'platform'})=='gpt-4o-mini-transcribe'
    for raw in ['whisper-1','gpt-4o-mini-transcribe','custom-asr','gpt-transcribe','']:
        assert voice.transcription_model({'config':{'audio_enabled':True,'transcription_model':raw}})==raw
    assert asr.scoped_strong_quote(A)=={'model':'gpt-transcribe','operation':'asr','reserve_microusd':30000,'minimum_microusd':22500,'max_duration_seconds':300}
    with pytest.raises(TenantLinearError):asr.scoped_strong_quote(B)

@pytest.mark.parametrize('prices',[
    {'whisper-1':{'asr':0}}, {'whisper-1':{'asr':12000}}, {'whisper-1':{'asr':40000}},
    {'gpt-transcribe':{'asr':0}}, {'gpt-transcribe':{'asr':22500}}, {'gpt-transcribe':{'asr':30001}},
    {'gpt-transcribe':None},{'gpt-transcribe':{'asr':True}}, {'gpt-transcribe':{'asr':30000.5}},
])
def test_changed_disabled_or_underbound_tariff_retains_original_path(monkeypatch,prices):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps(prices))
    item=authority().item({'media_kind':'audio'},{})
    assert item['config']['transcription_model']=='whisper-1' and not item.get('_shared_dialog_asr',False)
    with pytest.raises(TenantLinearError):asr.scoped_strong_quote(A)

@pytest.mark.parametrize('raw',['not-json','[]'])
def test_malformed_financial_registry_cannot_enable_rollout(monkeypatch,raw):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',raw)
    assert not authority().item({'media_kind':'audio'},{}).get('_shared_dialog_asr', False)


def wav(seconds=0.1):
    output=io.BytesIO()
    with wave.open(output,'wb') as h:
        h.setnchannels(1);h.setsampwidth(2);h.setframerate(16000);h.writeframes(b'\0\0'*int(seconds*16000))
    return output.getvalue()


def test_opted_provider_preserves_original_language_contract_and_bytes(monkeypatch):
    response=Mock(status_code=200);response.json.return_value={'text':'Synthetic speech','languages':[{'code':'ru'},{'code':'az'}],'usage':{'seconds':0.1}}
    post=Mock(return_value=response);monkeypatch.setattr(provider.requests,'post',post)
    audio=wav()
    text,usage=provider.transcribe('synthetic','gpt-transcribe',audio,'wav','audio/wav',with_usage=True,bounded_asr=True,before_dispatch=lambda:None,asr_tenant=A)
    assert text=='Synthetic speech' and usage['detected_languages']==['ru','az']
    assert post.call_args.args[0]=='https://api.openai.com/v1/audio/transcriptions'
    assert post.call_args.kwargs['data']=={'model':'gpt-transcribe','response_format':'json'}
    assert post.call_args.kwargs['files']['file'][1]==audio
    post.reset_mock()
    with pytest.raises(TenantLinearError):provider.transcribe('synthetic','gpt-transcribe',audio,'wav','audio/wav',bounded_asr=True,before_dispatch=lambda:None,asr_tenant=B)
    post.assert_not_called()


def test_preexisting_custom_gpt_provider_behavior_is_unchanged_without_optin(monkeypatch):
    post=Mock(return_value=Mock(status_code=200,json=lambda:{'text':'Synthetic speech'}))
    monkeypatch.setattr(provider.requests,'post',post)
    # Outside existing adapters keep their old behavior, including no new decoder/tariff gate.
    assert provider.transcribe('synthetic','gpt-transcribe',b'old mock bytes','ogg','audio/ogg')=='Synthetic speech'
    post.assert_called_once()

@pytest.mark.parametrize('streams,duration',[
    ([{'codec_type':'audio'},{'codec_type':'audio'}],'1'),([{'codec_type':'audio'},{'codec_type':'video'}],'1'),
    ([{'codec_type':'audio'}],'N/A'),([{'codec_type':'audio'}],'nan'),([{'codec_type':'audio'}],'301'),
    (None,'1'),([None],'1')])
def test_opted_ambiguous_or_overlong_upload_never_dispatched(monkeypatch,streams,duration):
    monkeypatch.setattr(voice.subprocess,'run',Mock(return_value=Mock(returncode=0,stdout=json.dumps({'streams':streams,'format':{'duration':duration}}).encode())))
    post=Mock();monkeypatch.setattr(provider.requests,'post',post)
    with pytest.raises(voice.VoiceError):provider.transcribe('synthetic','gpt-transcribe',b'x','ogg','audio/ogg',bounded_asr=True,before_dispatch=lambda:None,asr_tenant=A)
    post.assert_not_called()


def test_real_duration_size_and_multistream_limits(tmp_path):
    with pytest.raises(voice.VoiceError,match='duration'):voice.validate_audio(wav(.2),'wav','audio/wav',max_seconds=.1,prove_upload_duration=True)
    with pytest.raises(voice.VoiceError,match='size'):voice.validate_audio(b'x'*(voice.MAX_BYTES+1),'wav','audio/wav',prove_upload_duration=True)
    path=tmp_path/'two.m4a'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=400:duration=0.1','-map','0:a','-map','0:a','-c:a','aac',str(path)],check=True,capture_output=True)
    data=path.read_bytes()
    assert voice.validate_audio(data,'m4a','audio/mp4')==(data,'m4a','audio/mp4')
    with pytest.raises(voice.VoiceError):voice.validate_audio(data,'m4a','audio/mp4',prove_upload_duration=True)


def test_readonly_diagnostic_is_precisely_scoped():
    from tenant_chat_ai_settings import public
    row={'metadata':{'audio_enabled':True,'transcription_model':'whisper-1','model':'gpt-4.1'}}
    result=public(row,A)
    assert result['transcription_model']=='whisper-1' and result['effective_shared_dialog_audio_model']=='gpt-transcribe'
    assert 'effective_shared_dialog_audio_model' not in public(row,B)
    assert row['metadata']['model']=='gpt-4.1' and row['metadata']['transcription_model']=='whisper-1'

@pytest.mark.parametrize('trusted',[True,False])
def test_collect_requires_real_shared_execution_not_a_forged_item_flag(monkeypatch,trusted):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import tenant_knowledge_media as media
    import tenant_media_evidence as evidence
    ref={'id':'synthetic','channel_id':B,'lead_id':41,'external_id':'connect:synthetic','media_kind':'audio'}
    current={'provider':'connect','route':{'channel_id':B,'conversation_id':'synthetic'},'row':{'message_type':'audio'}}
    executor=authority()
    executor.check=Mock();executor.load=Mock(return_value=current)
    executor.dispatch=Mock(side_effect=lambda model,op,invoke:invoke())
    item=executor.item(ref,{'text':'','ref':'synthetic'})
    if not trusted:
        item['config']={**item['config'],'transcription_model':'whisper-1'}
        def once(_profile,item,model,audio,invoke,**kw):
            text,usage=invoke(item['key'],model,*audio,with_usage=True)
            return text,usage,True
        executor=SimpleNamespace(tenant=A,scoped_dialog_asr=True,shared_asr_context='manual_helper',
            load=Mock(return_value=current),check=Mock(),transcribe_once=once)
    monkeypatch.setattr(media,'download_media',AsyncMock(return_value=(wav(),'audio/wav')))
    monkeypatch.setattr(evidence,'reusable',lambda *_:None)
    call=Mock(return_value=('Synthetic speech',{'total_tokens':0}))
    monkeypatch.setattr(provider,'transcribe',call)
    _,results=media.collect({},item,execution=executor)
    assert results[0]['status']=='processed'
    assert call.call_args.args[1]==('gpt-transcribe' if trusted else 'whisper-1')
    assert call.call_args.kwargs.get('bounded_asr',False) is trusted
    assert callable(call.call_args.kwargs.get('before_dispatch')) is trusted
    assert ('asr_request_policy' in results[0]) is trusted


def test_bounded_adapter_requires_a_final_authority_callback(monkeypatch):
    post=Mock();monkeypatch.setattr(provider.requests,'post',post)
    with pytest.raises(ValueError):
        provider.transcribe('synthetic','gpt-transcribe',wav(),'wav','audio/wav',bounded_asr=True,asr_tenant=A)
    post.assert_not_called()

@pytest.mark.parametrize('change',['assignment_removed_or_reassigned','source_version_changed'])
def test_real_collect_late_decode_change_is_fenced_before_http(monkeypatch,change):
    from unittest.mock import AsyncMock
    import tenant_knowledge_media as media
    import tenant_media_evidence as evidence
    ref={'id':'synthetic','channel_id':B,'lead_id':41,'external_id':'connect:synthetic','media_kind':'audio'}
    current={'provider':'connect','route':{'channel_id':B,'conversation_id':'synthetic'},'row':{'message_type':'audio'}}
    executor=authority();executor.check=Mock();executor.load=Mock(return_value=current)
    state={'current':True,'decodes':0}
    def validate(exact_ref):
        assert exact_ref is ref
        if not state['current']:raise TenantLinearError(change,409)
    executor.validate=Mock(side_effect=validate)
    def dispatch(model,operation,invoke):
        executor.validate(ref)  # The existing pre-provider Execution guard.
        return invoke()
    executor.dispatch=Mock(side_effect=dispatch)
    item=executor.item(ref,{'text':'','ref':'synthetic'})
    audio=wav()
    def decode(*args,**kwargs):
        state['decodes']+=1
        if state['decodes']==2:state['current']=False  # Provider's independent proof finishes after the change.
        return audio,'wav','audio/wav'
    monkeypatch.setattr(voice,'validate_audio',decode)
    monkeypatch.setattr(media,'download_media',AsyncMock(return_value=(audio,'audio/wav')))
    monkeypatch.setattr(evidence,'reusable',lambda *_:None)
    post=Mock();monkeypatch.setattr(provider.requests,'post',post)
    _,results=media.collect({},item,execution=executor)
    assert state['decodes']==2 and executor.validate.call_count==2
    assert results[0]['status']=='error'
    post.assert_not_called()
