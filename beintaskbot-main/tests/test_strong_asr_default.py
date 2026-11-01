"""Internal ASR migration, synthetic inputs and offline provider boundary only."""
import io
import json
import subprocess
import wave
from unittest.mock import Mock
import pytest
import tenant_ai_asr_model as asr
import tenant_chat_ai_provider as provider
import tenant_connect_voice as voice
from tenant_linear_policy import TenantLinearError

@pytest.mark.parametrize('configured,platform,expected',[
    ('whisper-1',False,'gpt-transcribe'),('gpt-4o-mini-transcribe',False,'gpt-4o-mini-transcribe'),
    ('gpt-4o-mini-transcribe',True,'gpt-transcribe'),('whisper-1',True,'gpt-transcribe'),
    ('gpt-transcribe',False,'gpt-transcribe')])
def test_resolver(configured,platform,expected):
    assert asr.effective_model(configured,platform=platform)==expected

@pytest.mark.parametrize('configured',['','custom-asr','gpt-4o-transcribe'])
def test_unknown_or_empty_closed(configured):
    with pytest.raises(TenantLinearError):asr.effective_model(configured)

@pytest.mark.parametrize('value',[0,None,'disabled',True,22500.5,22499,30001])
def test_explicit_invalid_or_expanded_tariff_closed(monkeypatch,value):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-transcribe':{'asr':value}}))
    with pytest.raises(TenantLinearError):asr.quote('gpt-transcribe')

@pytest.mark.parametrize('raw',['not-json','[]','{"gpt-transcribe":null}'])
def test_malformed_tariff_closed(monkeypatch,raw):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',raw)
    with pytest.raises(TenantLinearError):asr.quote('gpt-transcribe')

@pytest.mark.parametrize('price',[None,22500,30000])
def test_fixed_default_and_duration_bound(monkeypatch,price):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({} if price is None else {'gpt-transcribe':{'asr':price}}))
    result=asr.quote('gpt-transcribe')
    assert result['minimum_microusd']==22500 and result['max_duration_seconds']==300
    assert result['reserve_microusd']==(30000 if price is None else price)

def wav(seconds=0.1):
    output=io.BytesIO()
    with wave.open(output,'wb') as handle:
        handle.setnchannels(1);handle.setsampwidth(2);handle.setframerate(16000)
        handle.writeframes(b'\0\0'*int(seconds*16000))
    return output.getvalue()

def test_new_provider_form_auto_policy_same_bytes_no_prompt_or_confidence(monkeypatch):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    response=Mock(status_code=200);response.json.return_value={'text':'Synthetic speech','languages':[{'code':'ru'},{'code':'az'}],'usage':{'seconds':0.1}}
    post=Mock(return_value=response);monkeypatch.setattr(provider.requests,'post',post)
    audio=wav()
    text,usage=provider.transcribe('synthetic-key','gpt-transcribe',audio,'wav','audio/wav',with_usage=True)
    assert text=='Synthetic speech' and usage['detected_languages']==['ru','az'] and usage['seconds']==0.1
    assert post.call_args.args[0]=='https://api.openai.com/v1/audio/transcriptions'
    assert post.call_args.kwargs['data']=={'model':'gpt-transcribe','response_format':'json'}
    assert post.call_args.kwargs['files']['file'][1]==audio
    assert not post.call_args.kwargs['allow_redirects']
    provider.transcribe('synthetic-key','gpt-transcribe',audio,'wav','audio/wav',speech_languages=['az','ru'])
    assert post.call_args.kwargs['data']['languages[]']==['az','ru']

@pytest.mark.parametrize('streams,duration',[
    ([{'codec_type':'audio'},{'codec_type':'audio'}],'1'),
    ([{'codec_type':'audio'},{'codec_type':'video'}],'1'),
    ([{'codec_type':'audio'}],'N/A'),([{'codec_type':'audio'}],'nan'),
    ([{'codec_type':'audio'}],'301')])
def test_ambiguous_or_overlong_upload_never_dispatched(monkeypatch,streams,duration):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    monkeypatch.setattr(voice.subprocess,'run',Mock(return_value=Mock(returncode=0,stdout=json.dumps({'streams':streams,'format':{'duration':duration}}).encode())))
    post=Mock();monkeypatch.setattr(provider.requests,'post',post)
    with pytest.raises(voice.VoiceError):provider.transcribe('synthetic','gpt-transcribe',b'x','ogg','audio/ogg')
    post.assert_not_called()

def test_existing_caller_shorter_duration_and_size_caps():
    with pytest.raises(voice.VoiceError,match='duration'):voice.validate_audio(wav(0.2),'wav','audio/wav',max_seconds=0.1,prove_upload_duration=True)
    with pytest.raises(voice.VoiceError,match='size'):voice.validate_audio(b'x'*(voice.MAX_BYTES+1),'wav','audio/wav',prove_upload_duration=True)

def test_effective_readonly_settings_preserve_saved_values(monkeypatch):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    from tenant_chat_ai_settings import public
    row={'metadata':{'transcription_model':'whisper-1','model':'gpt-4.1'}}
    result=public(row)
    assert result['transcription_model']=='whisper-1' and result['effective_transcription_model']=='gpt-transcribe'
    assert row=={'metadata':{'transcription_model':'whisper-1','model':'gpt-4.1'}}

def test_ordinary_auto_resolves_legacy_default_after_assignment_guard(monkeypatch):
    import asyncio
    import tenant_ai_agent_worker as worker
    monkeypatch.setattr(worker,'download',Mock(return_value=(b'synthetic','ogg','audio/ogg')))
    gate=Mock();monkeypatch.setattr(worker,'assert_job_allowed',gate)
    current=Mock();monkeypatch.setattr(worker,'assert_reservation_current',current)
    transcribe=Mock(return_value='Synthetic speech');monkeypatch.setattr(worker,'transcribe',transcribe)
    config={'audio_enabled':True,'transcription_model':'whisper-1'}
    row={'direction':'incoming','external_id':'synthetic','media_url':'https://synthetic.invalid/a','message_type':'audio'}
    result=asyncio.run(worker._prepare_agent_media({},[row],config,'synthetic',Mock(),reservation={'credential_source':'company'}))
    assert result[0]=={'synthetic':'Synthetic speech'}
    assert transcribe.call_args.args[1]=='gpt-transcribe'
    current.side_effect=TenantLinearError('assignment removed',409)
    transcribe.reset_mock()
    asyncio.run(worker._prepare_agent_media({},[row],config,'synthetic',Mock(),reservation={'credential_source':'company'}))
    transcribe.assert_not_called()


def test_video_new_adapter_does_not_force_response_language_or_claim_confidence(monkeypatch):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    response=Mock(status_code=200);response.json.return_value={'text':'Synthetic speech','languages':[{'code':'ru'}]}
    post=Mock(return_value=response);monkeypatch.setattr(provider.requests,'post',post)
    text,usage,diagnostics=provider.transcribe_video('synthetic','gpt-transcribe',wav(),'wav','audio/wav',expected_language='az')
    assert text=='Synthetic speech' and diagnostics['speech_state']=='uncertain'
    assert diagnostics['detected_languages']==['ru']
    assert post.call_args.args[0]=='https://api.openai.com/v1/audio/transcriptions'
    assert post.call_args.kwargs['data']=={'model':'gpt-transcribe','response_format':'json'}


def test_real_multistream_container_rejected_only_for_duration_billed_adapter(tmp_path):
    path=tmp_path/'two.m4a'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=400:duration=0.1',
        '-map','0:a','-map','0:a','-c:a','aac',str(path)],check=True,capture_output=True)
    content=path.read_bytes()
    assert voice.validate_audio(content,'m4a','audio/mp4')==(content,'m4a','audio/mp4')
    with pytest.raises(voice.VoiceError,match='duration_unproven'):
        voice.validate_audio(content,'m4a','audio/mp4',prove_upload_duration=True)


def test_connect_video_legacy_default_skips_unusable_paid_speech_preserves_frames(monkeypatch):
    import asyncio
    from test_connect_videos import _setup
    from test_connect_voice import JOB
    import tenant_connect_videos as videos
    reservation,row,_=_setup(monkeypatch,audio=True)
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'completed','text':'Synthetic visible device','second':0}))
    monkeypatch.setattr('tenant_video_frames.extract_audio',Mock(return_value=(b'synthetic','wav','audio/wav')))
    asr_call=Mock();monkeypatch.setattr(provider,'transcribe_video',asr_call)
    observations,statuses,transcripts=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert observations and statuses[row['external_id']]['audio']=='uncertain' and not transcripts
    asr_call.assert_not_called()
    assert all(call.args[4]!='whisper-1' for call in videos._claim.call_args_list)

@pytest.mark.parametrize('state',['completed','running','unknown'])
def test_connect_video_old_exact_receipt_reused_without_claim_or_paid_asr(monkeypatch,state):
    import asyncio
    from test_connect_videos import _setup
    from test_connect_voice import JOB
    import tenant_connect_videos as videos
    reservation,row,_=_setup(monkeypatch,audio=True)
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'completed','text':'Synthetic visible device'}))
    monkeypatch.setattr('tenant_video_frames.extract_audio',Mock(return_value=(b'synthetic','wav','audio/wav')))
    monkeypatch.setattr(videos,'_retained_asr',Mock(return_value={'state':state,'text':'Old retained speech','speech_state':'confident'}))
    asr_call=Mock();monkeypatch.setattr(provider,'transcribe_video',asr_call)
    _,statuses,transcripts=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert statuses[row['external_id']]['audio']==('confident' if state=='completed' else state)
    assert transcripts==({row['external_id']:'Old retained speech'} if state=='completed' else {})
    assert videos._claim.call_count==2  # Only the two visual frame receipts.
    asr_call.assert_not_called()
