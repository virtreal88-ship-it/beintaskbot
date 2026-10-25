"""Synthetic historical attachments; providers are mocked, never paid."""
import asyncio
import copy
import io
import json
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
import tenant_knowledge_media as media
from tenant_linear_policy import TenantLinearError
from test_connect_images import PNG

@pytest.fixture
def context(monkeypatch):
    import tenant_knowledge_media_cache as cache
    def unit_asr(profile,item,model,media,invoke,check_before_ai=None):
        item['asr_attempted']=True
        if check_before_ai: check_before_ai()
        text,usage=invoke(item['key'],model,*media,with_usage=True)
        return text,usage,True
    monkeypatch.setattr(cache,'transcribe_once',unit_asr)
    item={'tenant':'tenant','model':'gpt-4.1-2025-04-14','route':{'conversation_id':'demo@g.us'},'refs':[], 'messages':[]}
    def add(kind='image',channel='one'):
        index=len(item['refs'])+1
        item['refs'].append({'id':str(index),'channel_id':channel,'lead_id':index,'external_id':'connect:'+str(index),'media_kind':kind,'role':'customer','happened_at':'2026-10-18T00:00:00Z'})
        item['messages'].append({'ref':'m'+str(index),'role':'customer','text':'Synthetic caption'})
    monkeypatch.setattr(media,'load',lambda p,l,m:{'provider':'connect','route':{'channel_id':item['refs'][l-1]['channel_id'],'conversation_id':'demo@g.us'},'row':{'message_type':item['refs'][l-1]['media_kind']}})
    download=AsyncMock(return_value=(PNG,'image/png'));monkeypatch.setattr(media,'download_media',download)
    monkeypatch.setattr(media,'cached_transcript',lambda *a:'')
    return item,add,download

def test_photo_permission_exact_model_and_origin(context):
    item,add,download=context;add()
    images,results=media.collect({},item)
    assert not images and results[0]['status']=='unavailable'
    assert results[0]['code']=='external_media_permission_required'
    item['route']['allow_external_media']=True
    images,results=media.collect({},item)
    assert images[0]['source_ref']=='m1' and results[0]['channel_id']=='one'
    assert results[0]['role']=='customer' and results[0]['happened_at']
    assert results[0]['code']=='awaiting_model'
    item['model']='gpt-4.1-unknown'
    assert media.collect({},item)[1][0]['status']=='unsupported'

def test_unified_two_channel_identical_photo_is_one_input(context):
    item,add,download=context;add(channel='one');add(channel='two');item['route']['allow_external_media']=True
    images,results=media.collect({},item)
    assert len(images)==1 and results[1]['duplicate_of']=='1'
    assert [r['channel_id'] for r in results]==['one','two']

@pytest.mark.parametrize('status,expected',[(404,'unavailable'),(410,'unavailable'),(403,'unavailable'),(502,'error'),(415,'unsupported')])
def test_evolution_failure_is_never_success(context,status,expected):
    item,add,download=context;add();download.side_effect=TenantLinearError('synthetic',status)
    images,results=media.collect({},item)
    assert images==[] and results[0]['status']==expected

def test_channel_or_conversation_mismatch_never_downloads(context,monkeypatch):
    item,add,download=context;add()
    monkeypatch.setattr(media,'load',lambda *a:{'provider':'connect','route':{'channel_id':'foreign','conversation_id':'demo@g.us'}})
    assert media.collect({},item)[1][0]['status']=='unavailable'
    download.assert_not_called()

def test_count_limit_unsupported_and_pending(context):
    item,add,download=context
    for _ in range(5):add('sticker')
    _,results=media.collect({},item)
    assert [r['status'] for r in results]==['unsupported']*4+['pending_processing']
    download.assert_not_called()

def test_plain_document_extracts_redacts_and_preserves_caption(context):
    item,add,download=context;add('document')
    download.return_value=(b'Restart demo. email: demo@example.com\npassword: fake-secret','text/plain')
    images,results=media.collect({},item)
    assert not images and results[0]['status']=='processed'
    assert 'Synthetic caption' in item['messages'][0]['text']
    assert 'demo@example.com' not in item['messages'][0]['text'] and 'fake-secret' not in item['messages'][0]['text']

@pytest.mark.parametrize('kind',['audio','voice'])
@pytest.mark.parametrize('language,text',[('az','Miqdarı yoxlayın'),('ru','Проверьте количество'),('en','Check quantity')])
def test_cached_asr_reused_without_download(context,monkeypatch,kind,language,text):
    item,add,download=context;add(kind);monkeypatch.setattr(media,'cached_transcript',lambda *a:text)
    assert media.collect({},item)[1][0]['code']=='existing_transcript'
    assert text in item['messages'][0]['text'];download.assert_not_called()

def test_asr_requires_both_explicit_permissions(context):
    item,add,download=context;add('audio')
    assert media.collect({},item)[1][0]['code']=='asr_authorization_required'
    download.assert_not_called()

@pytest.mark.parametrize('text',['Məhsulları yoxlayın','Проверьте товары','Check the products'])
def test_missing_asr_explicit_paid_call_uses_existing_provider(context,monkeypatch,text):
    import tenant_connect_voice as voice
    import tenant_chat_ai_provider as provider
    item,add,download=context;add('audio');add('audio')
    item.update(key='synthetic',credential_source='platform')
    item['route'].update(allow_external_media=True,allow_paid_asr=True)
    download.return_value=(b'OggSsynthetic','audio/ogg')
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    monkeypatch.setattr(voice,'validate_audio',lambda *a,**kw:(b'synthetic','ogg','audio/ogg'))
    calls=[]
    monkeypatch.setattr(provider,'transcribe',lambda *a,**kw:(calls.append(a) or (text,{'total_tokens':20})))
    images,results=media.collect({},item)
    assert len(calls)==1 and calls[0][1]=='gpt-4o-mini-transcribe'
    assert results[0]['transcript']==text and results[0]['status']=='processed'
    assert results[1]['status']=='unavailable' and item['asr_usage']['total_tokens']==20

def test_docx_extractor_uses_no_external_relationships(context):
    import zipfile
    item,add,download=context;add('document')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:t>Synthetic solution</w:t></w:p></w:document>')
    download.return_value=(out.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    assert media.collect({},item)[1][0]['code']=='document_text'
    assert 'Synthetic solution' in item['messages'][0]['text']

def test_scanned_pdf_is_not_claimed_as_read_without_consent(context):
    from pypdf import PdfWriter
    item,add,download=context;add('document')
    out=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.write(out)
    download.return_value=(out.getvalue(),'application/pdf')
    assert media.collect({},item)[1][0]['code']=='external_media_permission_required'

def test_scanned_pdf_bounded_pages(context,monkeypatch):
    from pypdf import PdfWriter
    item,add,download=context;add('document');item['route']['allow_external_media']=True
    out=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.write(out)
    download.return_value=(out.getvalue(),'application/pdf')
    monkeypatch.setattr(media,'pdf_pages',lambda b:[(PNG,'image/png',{'page':1})])
    images,results=media.collect({},item)
    assert len(images)==1 and results[0]['visuals']==[{'page':1}]

@pytest.mark.skipif(not shutil.which('pdftoppm'),reason='local PDF renderer unavailable')
def test_real_synthetic_pdf_page_rendering():
    from pypdf import PdfWriter
    out=io.BytesIO();writer=PdfWriter()
    for _ in range(3): writer.add_blank_page(width=200,height=200)
    writer.write(out)
    pages=media.pdf_pages(out.getvalue())
    assert len(pages)==2 and [origin['page'] for _,_,origin in pages]==[1,2]
    assert all(media.signature(data,mime) for data,mime,_ in pages)

def test_video_frames_are_bounded(context,monkeypatch):
    item,add,download=context;add('video');item['route']['allow_external_media']=True
    download.return_value=(b'synthetic video','video/mp4')
    monkeypatch.setattr(media,'video_frames',lambda *a:[(PNG,'image/png',{'second':0}),(PNG,'image/png',{'second':2})])
    assert len(media.collect({},item)[0])==2


def test_video_analysis_limit_is_100_mib_inclusive_and_separate_from_photo_audio():
    assert media.media_byte_limit('video')==100*1024*1024
    media.validate_media_size('video',100*1024*1024)
    with pytest.raises(ValueError,match='video_size_limit'):
        media.validate_media_size('video',100*1024*1024+1)
    assert media.media_byte_limit('image')==20*1024*1024
    assert media.media_byte_limit('audio')==10*1024*1024


def test_video_analysis_duration_accepts_120_and_rejects_longer_or_missing():
    media.validate_video_duration(120)
    with pytest.raises(ValueError,match='video_duration_limit'):
        media.validate_video_duration(120.001)
    with pytest.raises(ValueError,match='video_duration_limit'):
        media.validate_video_duration(None)


def test_terminal_receipt_reuses_exact_scoped_evidence_without_download(context,monkeypatch):
    item,add,download=context;add('video')
    item['tenant_id']='tenant'
    saved={'status':'partial','code':'vision_observation','coverage':{'complete':False,'sampled_frames':[{'second':0}]},
           'result':{'transcript':'Synthetic saved narration','observation':'Synthetic sampled frame'}}
    import tenant_media_evidence as evidence
    monkeypatch.setattr(evidence,'reusable',lambda profile,ref:saved)
    images,results=media.collect(item,item)
    assert images==[] and results[0]['status']=='partial' and results[0]['reused'] is True
    assert 'Synthetic saved narration' in item['messages'][0]['text']
    assert 'partial sampled video observation' in item['messages'][0]['text']
    download.assert_not_called()

def test_video_audio_uses_single_authorized_asr_and_preserves_frames(context,monkeypatch):
    import tenant_connect_voice as voice
    import tenant_chat_ai_provider as provider
    item,add,download=context;add('video');item.update(key='synthetic',credential_source='platform')
    item['route'].update(allow_external_media=True,allow_paid_asr=True)
    download.return_value=(b'synthetic video','video/mp4')
    monkeypatch.setattr(media,'video_frames',lambda *a:[(PNG,'image/png',{'second':0})])
    monkeypatch.setattr(media,'video_audio',lambda *a:(b'RIFFsynthetic','wav','audio/wav'))
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    monkeypatch.setattr(provider,'transcribe',lambda *a,**kw:('Synthetic explanation',{'total_tokens':25}))
    images,results=media.collect({},item)
    assert len(images)==1 and results[0]['audio_status']=='processed'
    assert 'Synthetic explanation' in item['messages'][0]['text']
    assert item['asr_attempted'] and item['asr_usage']['total_tokens']==25

def test_decoder_unavailable_is_explicit(context,monkeypatch):
    item,add,download=context;add('video');item['route']['allow_external_media']=True
    monkeypatch.setattr(media.shutil,'which',lambda *a:None)
    download.return_value=(b'synthetic','video/mp4')
    assert media.collect({},item)[1][0]['status']=='unsupported'

def test_real_synthetic_video_decoder(tmp_path,monkeypatch):
    imageio_ffmpeg=pytest.importorskip('imageio_ffmpeg')
    executable=imageio_ffmpeg.get_ffmpeg_exe()
    target=tmp_path/('ffmpeg.exe' if os.name=='nt' else 'ffmpeg');shutil.copy(executable,target)
    monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    path=tmp_path/'demo.mp4'
    subprocess.run([str(target),'-v','error','-f','lavfi','-i','color=c=blue:s=160x120:r=10','-f','lavfi','-i','sine=frequency=440','-t','1','-pix_fmt','yuv420p',str(path)],check=True,timeout=10)
    frames=media.video_frames(path.read_bytes(),'video/mp4')
    assert len(frames)==2 and all(media.signature(b,mime) for b,mime,_ in frames)
    track,extension,mime=media.video_audio(path.read_bytes(),'video/mp4')
    assert extension=='wav' and mime=='audio/wav' and track.startswith(b'RIFF')

from test_knowledge_candidates import ready, db, database, A, CA, PROFILE
import tenant_knowledge_candidates_service as service

def test_media_only_source_is_retained_status_persisted_and_replay_idempotent(ready,monkeypatch):
    import uuid
    database,generation,data=ready
    source=str(uuid.uuid4());external='connect:'+str(uuid.uuid4())
    database.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','','image',now()+interval '4 seconds')",(A,source,external,'connect:'+CA));database.commit()
    # Unsupported source remains visible and must never be invented as analyzed.
    monkeypatch.setattr(media,'load',lambda *a:(_ for _ in ()).throw(TenantLinearError('expired',410)))
    response=service.analyze(PROFILE,data)
    assert response['request']['message_count']==4
    assert response['request']['media_results'][0]['status']=='unavailable'
    assert response['request']['media_results'][0]['id']==source
    assert any('Attachment status: unavailable' in m['text'] for m in generation.call_args.args[3])
    assert service.analyze(PROFILE,data)['request']['id']==response['request']['id']
    generation.assert_called_once()


def test_finish_revalidation_rejects_replaced_media_identity(ready):
    """The source fence covers the durable media identity, not only text."""
    import uuid
    database,_,data=ready
    source=str(uuid.uuid4()); external='connect:'+str(uuid.uuid4())
    database.execute("""INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','same caption','image',%s::jsonb,now()+interval '4 seconds')""",
      (A,source,external,'connect:'+CA,json.dumps({'mediaId':'before','fileLength':10})))
    database.commit()
    item=service.prepare(PROFILE,data)
    assert any(ref.get('id')==source and ref.get('media_version') for ref in item['refs'])
    database.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
      (json.dumps({'mediaId':'after','fileLength':10}),A,source))
    database.commit()
    import tenant_knowledge_candidates_store as candidate_store
    with database.cursor() as cur:
        with pytest.raises(TenantLinearError,match='Вложение исходного сообщения изменилось'):
            candidate_store.evidence(cur,PROFILE,{**item,'source_refs':item['refs']})

def test_media_consent_changes_fingerprint_without_replaying_original(ready,monkeypatch):
    import uuid
    database,generation,data=ready
    original=service.analyze(PROFILE,data)
    changed=service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4()),'allow_external_media':True})
    assert changed['request']['id']!=original['request']['id']

def test_asr_permission_reserves_extra_call_and_tokens(ready):
    database,generation,data=ready
    item=service.prepare(PROFILE,{**data,'allow_external_media':True,'allow_paid_asr':True})
    rows=database.execute('SELECT reserved_calls FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,item['id']))
    assert rows[0]['reserved_calls']==2
    assert database.execute('SELECT reserved_tokens FROM saas_ai_agent_token_usage WHERE tenant_id=%s::uuid AND run_id=%s::uuid',(A,item['id']))[0]['reserved_tokens']==service.MAX_TOKENS+8000


def test_inline_visual_without_explicit_vision_fails_closed_under_money_cap(ready,monkeypatch):
    """Inline image inputs are not an unpriced escape hatch when capped."""
    import tenant_ai_spend_budget as budget
    import uuid
    database,generation,data=ready
    source=str(uuid.uuid4())
    database.execute("""INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','photo attached','image',now()+interval '4 seconds')""",
      (A,source,'media-inline','connect:'+CA))
    database.commit()
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1_000_000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'fixture':{'input':1,'output':1}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'fixture':{'vision_low':100}}))
    monkeypatch.setattr(budget,'_connect',lambda:database)
    monkeypatch.setattr(service,'platform_model',lambda:'fixture')
    # No campaign grants Vision for this source: the extraction must stop
    # before any provider call instead of accepting an unpriced inline image.
    with pytest.raises(TenantLinearError,match='Vision'):
        service.prepare(PROFILE,{**data,'allow_external_media':True})
    generation.assert_not_called()


def test_finish_separates_text_media_unknown_and_media_only_cancel(ready,monkeypatch):
    """Text and fixed media settlements have independent lifecycle receipts."""
    import tenant_ai_spend_budget as budget
    import uuid
    database,_,data=ready
    settled=[];media=[];released=[]
    monkeypatch.setattr(budget,'settle',lambda *args,**kwargs:settled.append((args,kwargs)))
    monkeypatch.setattr(budget,'settle_media',lambda *args,**kwargs:media.append((args,kwargs)))
    monkeypatch.setattr(budget,'release',lambda *args,**kwargs:released.append((args,kwargs)))

    item=service.prepare(PROFILE,data)
    item.update(generation_attempted=True,asr_attempted=True,vision_attempts=1,vision_usage=[],
      media_spend={
        'asr':{'key':'asr-key','operation':'asr','units':1},
        'vision':{'key':'vision-key','operation':'vision_low','units':2},
      })
    service.finish(PROFILE,item,[],{'input_tokens':7,'output_tokens':3,'total_tokens':10},'',True)
    assert settled and settled[0][0][2]=={'input_tokens':7,'output_tokens':3,'total_tokens':10}
    assert released==[]
    assert [(call[0][1],call[1]['unknown']) for call in media]==[('asr-key',True),('vision-key',True)]

    # If only a cached/cancelled media branch ran, no text generation occurred:
    # release its reservation instead of settling it as a zero-token model call.
    item=service.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'allow_external_media':True})
    item.update(generation_attempted=False,asr_attempted=False,vision_attempts=0,
                media_spend={'asr':{'key':'cached-asr','operation':'asr','units':1}})
    service.finish(PROFILE,item,None,None,'',False)
    assert released and released[-1][0][1]=='extraction:'+item['id']
    assert media[-1][0][1]=='cached-asr' and media[-1][1]['attempted_units']==0


