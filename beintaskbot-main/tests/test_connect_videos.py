"""Synthetic-only incoming video regressions; no provider or production calls."""
import asyncio
import base64
import json
import shutil
import subprocess
from unittest.mock import AsyncMock, Mock, patch

import pytest

from test_connect_voice import JOB, MESSAGE, RESERVATION, row
import tenant_connect_videos as videos
from tenant_chat_ai_policy import history

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6cN8AAAAASUVORK5CYII=')


def _setup(monkeypatch, *, audio=False):
    reservation={**RESERVATION,'config_version':'version-1','video_model':'gpt-4.1',
        'video_api_key':'company-video-key','agent_language':'az',
        'config':{'video_enabled':True,'video_model':'gpt-4.1','audio_enabled':audio,
            'transcription_model':'whisper-1' if audio else ''}}
    video_row=row(kind='video');video_row['media_url']='https://media.example.invalid/video.mp4'
    item={'tenant_id':JOB['tenant_id'],'channel_id':JOB['channel_id'],'message_id':video_row['external_id']}
    monkeypatch.setattr(videos,'_active',lambda *_:True)
    monkeypatch.setattr(videos,'_resolve',lambda *_:item)
    monkeypatch.setattr(videos,'download_media',AsyncMock(return_value=(b'synthetic-video','video/mp4')))
    monkeypatch.setattr(videos,'sample_frames',lambda *_a,**_k:[(PNG,'image/jpeg',{'second':0.0}),(PNG,'image/jpeg',{'second':1.0})])
    return reservation,video_row,item


def test_live_video_uses_separate_video_model_and_timestamped_untrusted_observations(monkeypatch):
    reservation,row,item=_setup(monkeypatch)
    claims=iter([{'state':'running','token':'tok-0','call_key':'call-0'},
                 {'state':'running','token':'tok-1','call_key':'call-1'}])
    claim=Mock(side_effect=lambda *_a,**_k:next(claims))
    finish=Mock()
    monkeypatch.setattr(videos,'_claim',claim);monkeypatch.setattr(videos,'_finish',finish)
    generate=Mock(side_effect=[('Visible blank label.',{}),('Printer is feeding a label.',{})])
    monkeypatch.setattr('tenant_chat_ai_provider.generate',generate)
    observations,statuses,transcripts=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert [entry['second'] for entry in observations[row['external_id']]]==[0.0,1.0]
    assert statuses[row['external_id']]=={'visual':'sampled','audio':'not_enabled'}
    assert transcripts=={} and generate.call_count==2
    assert all(call.args[1]=='gpt-4.1' and call.args[0]=='company-video-key' for call in generate.call_args_list)
    assert all(call.kwargs['images'][0]['detail']=='low' for call in generate.call_args_list)
    lines,missing=history([row],{},video_observations=observations,video_statuses=statuses)
    assert missing==1 and '0s' in lines[0]['text'] and 'Untrusted sampled video observation' in lines[0]['text']
    assert 'audio was not analyzed because transcription is not enabled' in lines[0]['text']


def test_video_disabled_does_not_download_or_call_models(monkeypatch):
    reservation,row,_item=_setup(monkeypatch)
    reservation['config']['video_enabled']=False
    result=videos.prepare
    observations,statuses,transcripts=asyncio.run(result(JOB,reservation,[row],Mock()))
    assert observations==statuses==transcripts=={}
    videos.download_media.assert_not_called()


def test_video_model_403_stops_without_fallback_or_automatic_retry(monkeypatch):
    from tenant_chat_ai_provider import ChatAIProviderError
    reservation,row,_item=_setup(monkeypatch)
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'running','token':'tok','call_key':'key'}))
    finish=Mock();monkeypatch.setattr(videos,'_finish',finish)
    generate=Mock(side_effect=ChatAIProviderError(403,'model_access'))
    monkeypatch.setattr('tenant_chat_ai_provider.generate',generate)
    observations,statuses,transcripts=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert generate.call_count==1 and observations==transcripts=={}
    assert statuses[row['external_id']]['visual']=='unknown'
    assert finish.call_args.kwargs['unknown'] is True
    # A repeated webhook finds the unknown receipt and never retries the provider.
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'unknown','text':'',
        'failure_code':'provider_http_403_model_access','cached':True}))
    asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert generate.call_count==1


def test_budget_refusal_blocks_vision_before_provider(monkeypatch):
    reservation,row,_item=_setup(monkeypatch)
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'blocked','failure_code':'ai_spend_budget_exhausted'}))
    generate=Mock();monkeypatch.setattr('tenant_chat_ai_provider.generate',generate)
    observations,statuses,_=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert observations=={} and statuses[row['external_id']]['visual']=='blocked'
    generate.assert_not_called()


def test_replayed_completed_frame_cache_does_not_call_provider(monkeypatch):
    reservation,row,_item=_setup(monkeypatch)
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'completed','text':'Cached frame observation','cached':True}))
    generate=Mock();monkeypatch.setattr('tenant_chat_ai_provider.generate',generate)
    observations,statuses,_=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert len(observations[row['external_id']])==2 and statuses[row['external_id']]['visual']=='sampled'
    generate.assert_not_called()


def test_audio_can_succeed_when_visual_sampling_fails_and_transcript_is_unverified(monkeypatch):
    reservation,row,_item=_setup(monkeypatch,audio=True)
    monkeypatch.setattr(videos,'sample_frames',Mock(side_effect=ValueError('bad frame')))
    monkeypatch.setattr('tenant_video_frames.extract_audio',lambda *_:(b'RIFFsynthetic','wav','audio/wav'))
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'running','token':'asr-token','call_key':'asr-key'}))
    monkeypatch.setattr(videos,'_finish',Mock())
    safe_text='The printer is showing a blank label; email [EMAIL]; phone [NUMBER]; [SECRET]'
    monkeypatch.setattr('tenant_chat_ai_provider.transcribe_video',Mock(return_value=(
        'The printer is showing a blank label; email demo@example.test; phone +994 50 1234567; api_key=sk-12345678901234567890',
        {}, {'speech_state':'confident','language':'az'})))
    observations,statuses,transcripts=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert observations=={} and statuses[row['external_id']]['audio']=='confident'
    assert transcripts[row['external_id']]==safe_text
    assert videos._finish.call_args.kwargs['text']==safe_text
    assert videos._claim.call_args.args[-1]=='asr:az'
    lines,missing=history([row],{},video_statuses=statuses,video_transcripts=transcripts)
    assert missing==1 and 'Unverified video speech transcript' in lines[0]['text']
    assert 'was not analyzed' in lines[0]['text']


def test_suspect_video_asr_is_not_presented_as_speech(monkeypatch):
    reservation,row,_item=_setup(monkeypatch,audio=True)
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'running','token':'token','call_key':'call'}))
    finish=Mock();monkeypatch.setattr(videos,'_finish',finish)
    monkeypatch.setattr('tenant_video_frames.extract_audio',lambda *_:(b'RIFFsynthetic','wav','audio/wav'))
    monkeypatch.setattr('tenant_chat_ai_provider.transcribe_video',Mock(return_value=(
        '',{}, {'speech_state':'uncertain','language':'ja','reason':'language_mismatch'})))
    _,statuses,transcripts=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert statuses[row['external_id']]['audio']=='uncertain' and transcripts=={}
    assert finish.call_args.kwargs['speech_state']=='uncertain' and finish.call_args.kwargs['text']==''
    lines,_=history([row],{},video_statuses=statuses,video_transcripts=transcripts)
    assert 'could not be confidently transcribed' in lines[0]['text']
    assert 'microwave' not in lines[0]['text']


@pytest.mark.parametrize('payload,expected_language,expected_state,expected_text',[
    ({'text':'','language':'az','segments':[]},'az','no_speech_detected',''),
    ({'text':'printer sound','language':'az','segments':[{'text':'printer sound','no_speech_prob':0.9,'avg_logprob':-0.1}]},'az','no_speech_detected',''),
    ({'text':'maybe','language':'az','segments':[{'text':'maybe','no_speech_prob':0.45,'avg_logprob':-0.2}]},'az','uncertain',''),
    ({'text':'電子レンジ','language':'ja','segments':[{'text':'電子レンジ','no_speech_prob':0.01,'avg_logprob':-0.1}]},'az','uncertain',''),
    ({'text':'printer ready','language':'az','segments':[{'text':'printer ready','no_speech_prob':0.01,'avg_logprob':-0.1}]},'az','confident','printer ready'),
])
def test_video_whisper_confidence_and_language_mismatch(payload,expected_language,expected_state,expected_text):
    from tenant_chat_ai_provider import transcribe_video
    response=Mock(status_code=200);response.json.return_value=payload
    with patch('tenant_chat_ai_provider.requests.post',return_value=response) as post:
        text,_usage,diagnostics=transcribe_video('synthetic','whisper-1',b'audio','wav','audio/wav',
            expected_language=expected_language)
    assert text==expected_text and diagnostics['speech_state']==expected_state
    assert post.call_args.kwargs['data']['response_format']=='verbose_json'


def test_gpt4o_mini_video_asr_uses_audio_adapter_but_remains_unverified():
    from tenant_chat_ai_provider import transcribe_video
    response=Mock(status_code=200);response.json.return_value={'text':'printer is fixed','usage':{'input_tokens':40,'output_tokens':6}}
    with patch('tenant_chat_ai_provider.requests.post',return_value=response) as post:
        text,usage,diagnostics=transcribe_video('synthetic','gpt-4o-mini-transcribe',b'audio','wav','audio/wav')
    assert text=='printer is fixed' and usage=={'input_tokens':40,'output_tokens':6,'total_tokens':46}
    assert diagnostics=={'speech_state':'uncertain','reason':'speech_confidence_unavailable','language':''}
    assert post.call_args.kwargs['data']=={'model':'gpt-4o-mini-transcribe','response_format':'json'}


def test_video_history_fails_closed_without_observation_or_confident_speech():
    video=row(kind='video');video['media_url']='attachment'
    lines,missing=history([video],{},video_statuses={video['external_id']:{'visual':'unknown','audio':'uncertain'}})
    assert missing==1 and 'Video was not analyzed' in lines[0]['text']
    assert 'could not be confidently transcribed' in lines[0]['text']
    assert 'do not infer' in lines[0]['text']


def test_video_observation_redacts_personal_and_secret_text_before_cache_reuse():
    result=videos.redact_observation(
        'Blank label; customer name: Aysel Aliyeva; email: demo@example.test; '
        'phone: +994 50 1234567; api_key=sk-12345678901234567890')
    assert 'Blank label' in result
    for sensitive in ('Aysel Aliyeva','demo@example.test','1234567','sk-12345678901234567890'):
        assert sensitive not in result
    assert '[PERSONAL DATA]' in result and '[EMAIL]' in result and '[NUMBER]' in result and '[SECRET]' in result


def test_video_asr_cache_is_scoped_to_expected_language():
    from tenant_knowledge_media_cache import transcription_fingerprint
    media=(b'synthetic-audio','wav','audio/wav')
    az={'route':{'language':'az'}};ru={'route':{'language':'ru'}}
    assert transcription_fingerprint(az,'whisper-1',media,return_diagnostics=True) != \
        transcription_fingerprint(ru,'whisper-1',media,return_diagnostics=True)
    assert transcription_fingerprint(az,'whisper-1',media,return_diagnostics=False) == \
        transcription_fingerprint(ru,'whisper-1',media,return_diagnostics=False)


def test_response_policy_keeps_video_calls_within_four_call_reservation(monkeypatch):
    reservation,row,_item=_setup(monkeypatch,audio=True)
    reservation['response_policy']={'enabled':True}
    monkeypatch.setattr(videos,'_claim',Mock(return_value={'state':'running','token':'token','call_key':'key'}))
    monkeypatch.setattr(videos,'_finish',Mock())
    monkeypatch.setattr('tenant_video_frames.extract_audio',Mock(return_value=(b'RIFFsynthetic','wav','audio/wav')))
    generate=Mock(return_value=('A sampled frame.',{}));monkeypatch.setattr('tenant_chat_ai_provider.generate',generate)
    transcribe=Mock();monkeypatch.setattr('tenant_chat_ai_provider.transcribe_video',transcribe)
    asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    transcribe.assert_not_called();assert generate.call_count==2


def test_legacy_company_configuration_defaults_video_off_and_keeps_models_independent():
    from tenant_chat_ai_policy import configuration
    old={'enabled':True,'consent':True,'audio_enabled':True,'model':'gpt-4o-mini',
        'transcription_model':'whisper-1','daily_limit':50,'copilot_enabled':True,
        'copilot_model':'gpt-4o-mini','copilot_daily_limit':50,'media_hosts':[],
        'photo_model':'gpt-4.1'}
    result=configuration(old)
    assert result['video_enabled'] is False and result['video_model']==''
    assert result['photo_model']=='gpt-4.1' and result['model']=='gpt-4o-mini'


def test_video_setting_requires_model_when_enabled():
    from tenant_chat_ai_policy import configuration
    from tenant_linear_policy import TenantLinearError
    payload={'enabled':True,'consent':True,'audio_enabled':False,'model':'gpt-4o-mini',
        'transcription_model':'whisper-1','daily_limit':50,'video_enabled':True,
        'video_model':'','media_hosts':[]}
    with pytest.raises(TenantLinearError,match='Choose a video model'):
        configuration(payload)


def test_video_call_reservation_counts_policy_fallback_and_skips_asr():
    from tenant_ai_agent_run_store import video_reserved_calls
    assert video_reserved_calls(1,response_policy_enabled=True,audio_enabled=False,
        transcription_model='whisper-1',asr_tariff_available=True)==4
    assert video_reserved_calls(1,response_policy_enabled=True,audio_enabled=True,
        transcription_model='whisper-1',asr_tariff_available=True)==4
    assert video_reserved_calls(1,response_policy_enabled=False,audio_enabled=False,
        transcription_model='whisper-1',asr_tariff_available=True)==3
    assert video_reserved_calls(1,response_policy_enabled=False,audio_enabled=True,
        transcription_model='whisper-1',asr_tariff_available=True)==4


def test_video_reservation_is_bounded_and_requires_explicit_tariff_and_cap(monkeypatch):
    from tenant_ai_video_model import quote
    from tenant_linear_policy import TenantLinearError
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({JOB['tenant_id']:50000}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-4.1':{'vision_low':8896}}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'gpt-4.1':{'input':2000000,'output':8000000}}))
    monkeypatch.setenv('AI_AGENTS_VISION_MODELS','gpt-4.1')
    quote_result=quote('gpt-4.1',JOB['tenant_id'])
    assert quote_result['max_frames']==2 and quote_result['max_microusd']==17792
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    with pytest.raises(TenantLinearError,match='tariff'):
        quote('gpt-4.1',JOB['tenant_id'])


def test_video_resolution_is_tenant_channel_lead_and_message_scoped(monkeypatch):
    from unittest.mock import MagicMock
    conn=MagicMock();conn.__enter__.return_value=conn
    cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[{'ok':1}]
    route={'channel_id':JOB['channel_id'],'conversation_id':JOB['conversation_id'],'api_key':'synthetic'}
    monkeypatch.setattr(videos,'_active',lambda *_:True)
    monkeypatch.setattr(videos,'_connect',lambda:conn)
    monkeypatch.setattr(videos,'connect_route',lambda *_:route)
    item=videos._resolve(JOB,RESERVATION,MESSAGE)
    assert item['message_id']==MESSAGE and item['row']['message_type']=='video'
    sql,args=cur.execute.call_args.args
    assert JOB['tenant_id'] in args and JOB['lead_id'] in args
    assert 'connect:'+JOB['channel_id'] in args and 'connect:'+MESSAGE in args
    assert "direction='incoming'" in sql and "message_type='video'" in sql
    cur.fetchone.side_effect=[None]
    with pytest.raises(Exception,match='video_scope_mismatch'):
        videos._resolve(JOB,RESERVATION,MESSAGE)


def test_video_cache_identity_pins_tenant_source_bytes_model_component_and_timestamp():
    item={'tenant_id':JOB['tenant_id'],'channel_id':JOB['channel_id'],'message_id':MESSAGE}
    base=videos._call_key(item,'video-hash','frame-hash',1.0,'gpt-4.1',videos.VISION_COMPONENT)
    assert base==videos._call_key(item,'video-hash','frame-hash',1.0,'gpt-4.1',videos.VISION_COMPONENT)
    variants=[
        ({**item,'tenant_id':'00000000-0000-0000-0000-000000000099'},'video-hash','frame-hash',1.0,'gpt-4.1',videos.VISION_COMPONENT),
        (item,'video-hash-2','frame-hash',1.0,'gpt-4.1',videos.VISION_COMPONENT),
        (item,'video-hash','frame-hash-2',1.0,'gpt-4.1',videos.VISION_COMPONENT),
        (item,'video-hash','frame-hash',2.0,'gpt-4.1',videos.VISION_COMPONENT),
        (item,'video-hash','frame-hash',1.0,'gpt-4.1-mini',videos.VISION_COMPONENT),
        (item,'video-hash','frame-hash',1.0,'gpt-4.1','vision-v2'),
    ]
    assert all(videos._call_key(*args)!=base for args in variants)
    assert videos._call_key(item,'video-hash','audio-hash',-1.0,'whisper-1',videos._asr_component('az')) != \
        videos._call_key(item,'video-hash','audio-hash',-1.0,'whisper-1',videos._asr_component('ru'))


def test_synthetic_silent_printer_video_keeps_visual_fact_and_withholds_asr_guess(tmp_path,monkeypatch):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is not installed')
    from PIL import Image,ImageDraw
    frame=tmp_path/'printer.png';image=Image.new('RGB',(640,360),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((170,45,470,330),fill=(230,230,230),outline='black',width=4)
    draw.rectangle((215,160,425,245),fill='white',outline='black',width=3)
    draw.text((238,120),'LABEL PRINTER',fill='black');image.save(frame)
    clip=tmp_path/'silent-printer.mp4'
    subprocess.run([shutil.which('ffmpeg'),'-v','error','-loop','1','-i',str(frame),
        '-f','lavfi','-i','sine=frequency=440:duration=1','-t','1','-shortest','-pix_fmt','yuv420p',str(clip)],
        check=True,timeout=15)
    reservation,row,_item=_setup(monkeypatch,audio=True)
    monkeypatch.setattr(videos,'download_media',AsyncMock(return_value=(clip.read_bytes(),'video/mp4')))
    claims=iter([{'state':'running','token':f'tok-{i}','call_key':f'call-{i}'} for i in range(3)])
    monkeypatch.setattr(videos,'_claim',Mock(side_effect=lambda *_a,**_k:next(claims)))
    monkeypatch.setattr(videos,'_finish',Mock())
    monkeypatch.setattr('tenant_chat_ai_provider.generate',Mock(return_value=('Visible blank label in a label printer.',{})))
    whisper=Mock(status_code=200);whisper.json.return_value={'text':'電子レンジで電子レンジの電流を測定します。',
        'language':'ja','segments':[{'text':'電子レンジ','no_speech_prob':0.92,'avg_logprob':-0.1}]}
    with patch('tenant_chat_ai_provider.requests.post',return_value=whisper):
        observations,statuses,transcripts=asyncio.run(videos.prepare(JOB,reservation,[row],Mock()))
    assert len(observations[row['external_id']])==2
    assert statuses[row['external_id']]['audio']=='no_speech_detected'
    assert transcripts=={}
    lines,_=history([row],{},video_observations=observations,video_statuses=statuses,
                    video_transcripts=transcripts)
    assert 'Visible blank label' in lines[0]['text']
    assert '電子レンジ' not in lines[0]['text']
    assert 'does not prove the video is silent' in lines[0]['text']
