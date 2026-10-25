"""Bounded historical evidence. No URLs from clients, sends or automatic retries."""
import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
from tenant_knowledge_candidates_service import LiveExtractionDisabled
from tenant_chat_attachment_store import load
from tenant_connect_media import download_media
from tenant_connect_images import signature, supports_images
from tenant_ai_knowledge_files import extract
from tenant_knowledge_candidates_policy import redact

MAX_FILES=4
MAX_VISUALS=2
MAX_BYTES=20*1024*1024
MAX_AUDIO_BYTES=10*1024*1024
MAX_VIDEO_BYTES=100*1024*1024
MAX_VIDEO_SECONDS=120
MAX_VIDEO_AUDIO_BYTES=4*1024*1024


def media_byte_limit(kind):
    """Analysis limits are deliberately independent from attachment preview."""
    if kind == 'video': return MAX_VIDEO_BYTES
    if kind in {'audio','voice'}: return MAX_AUDIO_BYTES
    return MAX_BYTES


def validate_media_size(kind, size):
    if not isinstance(size, int) or size < 0 or size > media_byte_limit(kind):
        raise ValueError('video_size_limit' if kind == 'video' else 'media_size_limit')


def validate_video_duration(seconds):
    if not isinstance(seconds, (int, float)) or not 0 < seconds <= MAX_VIDEO_SECONDS:
        raise ValueError('video_duration_limit')


def video_frames(data,mime):
    """Sample start/middle only; never execute secondary URLs or demux playlists."""
    if not shutil.which('ffmpeg'): raise LookupError('video_decoder_unavailable')
    with tempfile.TemporaryDirectory(prefix='knowledge-video-') as directory:
        source=Path(directory)/('clip.mp4' if mime=='video/mp4' else 'clip.webm');source.write_bytes(data)
        common=['-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm']
        probe=subprocess.run(['ffmpeg','-nostdin','-v','error',*common,'-i',str(source),'-map','0:v:0','-an','-sn','-dn','-t',str(MAX_VIDEO_SECONDS+1),'-threads','1','-progress','pipe:1','-f','null','-'],capture_output=True,timeout=12)
        ticks=[int(line.split('=')[1]) for line in probe.stdout.decode().splitlines() if line.startswith('out_time_us=') and line.split('=')[1].lstrip('-').isdigit()]
        duration=max(ticks,default=0)/1_000_000
        if probe.returncode: raise ValueError('video_duration_limit')
        validate_video_duration(duration)
        frames=[]
        for index,second in enumerate((0,duration/2)):
            output=Path(directory)/f'frame{index}.jpg'
            result=subprocess.run(['ffmpeg','-nostdin','-v','error',*common,'-ss',str(second),'-i',str(source),'-map','0:v:0','-an','-sn','-dn','-frames:v','1','-vf','scale=1280:720:force_original_aspect_ratio=decrease','-threads','1',str(output)],capture_output=True,timeout=8)
            if result.returncode or not output.exists() or output.stat().st_size>1024*1024: raise ValueError('video_frame_failed')
            frames.append((output.read_bytes(),'image/jpeg',{'second':round(second,3)}))
        return frames


def pdf_pages(data):
    """Scanned PDF fallback is bounded local rendering, never a claim of OCR success."""
    if not shutil.which('pdftoppm'): raise LookupError('pdf_renderer_unavailable')
    with tempfile.TemporaryDirectory(prefix='knowledge-pages-') as directory:
        source=Path(directory)/'scan.pdf';source.write_bytes(data)
        result=subprocess.run(['pdftoppm','-f','1','-l','2','-scale-to','1280','-png',str(source),str(Path(directory)/'page')],capture_output=True,timeout=15)
        pages=sorted(Path(directory).glob('page-*.png'))
        if result.returncode or not pages: raise ValueError('pdf_render_failed')
        return [(p.read_bytes(),'image/png',{'page':i+1}) for i,p in enumerate(pages) if p.stat().st_size<=2*1024*1024]


def video_audio(data,mime):
    if not shutil.which('ffmpeg'): raise LookupError('video_decoder_unavailable')
    with tempfile.TemporaryDirectory(prefix='knowledge-track-') as directory:
        source=Path(directory)/('clip.mp4' if mime=='video/mp4' else 'clip.webm');source.write_bytes(data)
        output=Path(directory)/'track.wav'
        result=subprocess.run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm','-i',str(source),'-map','0:a:0','-vn','-sn','-dn','-t',str(MAX_VIDEO_SECONDS+1),'-ac','1','-ar','16000','-threads','1',str(output)],capture_output=True,timeout=12)
        if result.returncode or not output.exists() or not 0<output.stat().st_size<MAX_VIDEO_AUDIO_BYTES: raise LookupError('video_audio_unavailable')
        from tenant_connect_voice import validate_audio
        return validate_audio(output.read_bytes(),'wav','audio/wav',max_seconds=MAX_VIDEO_SECONDS)


def cached_transcript(item,ref):
    # Existing durable receipt, exact company/channel/opaque Connect message ID.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT m.body AS text FROM saas_connect_voice_receipts v JOIN saas_crm_messages m ON m.tenant_id=v.tenant_id AND m.external_id=v.external_id AND m.channel='connect:'||v.channel_id::text WHERE v.tenant_id=%s::uuid AND v.channel_id=%s::uuid AND v.external_id=%s AND v.state='completed' AND m.kommo_lead_id=%s",(item['tenant'],ref['channel_id'],ref['external_id'],ref['lead_id']))
            row=cur.fetchone()
    return row['text'] if row else ''


def collect(profile,item,check_before_ai=None):
    images=[];results=[];seen={};size=0;deadline=time.monotonic()+65
    consent=item['route'].get('allow_external_media') is True
    names=item.get('redaction_names',[])
    asr_used=False;visual_count=0;video_used=False
    for ref,message in zip(item['refs'],item['messages']):
        kind=ref.get('media_kind')
        if not kind: continue
        message['text']=message['text'].replace('[Attachment pending_processing: contents not read]','')
        # A receipt is exact-message/version/channel scoped.  Reuse terminal
        # evidence instead of downloading or paying to analyse the attachment
        # again; partial video remains explicitly partial.
        from tenant_media_evidence import reusable, apply_reusable
        prior=reusable(profile,ref)
        if prior:
            reused=apply_reusable(message,prior)
            receipt={k:ref[k] for k in ('id','channel_id','lead_id','role','happened_at') if k in ref}
            receipt.update(kind=kind,**reused)
            results.append(receipt)
            continue
        receipt={k:ref[k] for k in ('id','channel_id','lead_id','role','happened_at') if k in ref}
        receipt.update(kind=kind,status='pending_processing',code='processing_limit')
        results.append(receipt)
        if len(results)>MAX_FILES or time.monotonic()>deadline: continue
        if kind not in {'image','audio','voice','video','document'}:
            receipt.update(status='unsupported',code='unsupported_format');continue
        try:
            # Re-check employee access to the exact historical source, never current channel substitution.
            current=load(profile,ref['lead_id'],ref['external_id'])
            if current.get('provider')!='connect' or current['route']['channel_id']!=ref['channel_id'] or current['route']['conversation_id']!=item['route']['conversation_id']: raise TenantLinearError('scope',403)
            if kind in {'audio','voice'}:
                text=cached_transcript(item,ref)
                if text:
                    message['text']+='\n[Existing transcript] '+redact(text,names)[:4000]
                    receipt.update(status='processed',code='existing_transcript');continue
                if not consent or not item['route'].get('allow_paid_asr') or asr_used:
                    receipt.update(status='unavailable',code='asr_authorization_required');continue
                from tenant_connect_voice import validate_audio,transcription_model,FORMATS
                from tenant_chat_ai_provider import transcribe
                model=transcription_model(item)
                if not model: raise LookupError('asr_model_unavailable')
                data,mime=asyncio.run(asyncio.wait_for(download_media(current,max_bytes=min(MAX_AUDIO_BYTES,MAX_BYTES-size)),timeout=10))
                size+=len(data)
                validate_media_size(kind,len(data))
                if size>MAX_BYTES: receipt.update(status='unavailable',code='byte_budget');continue
                media=validate_audio(data,FORMATS[mime],mime,max_seconds=60)
                # Extraction is already durably reserved and is never replayed automatically.
                if load(profile,ref['lead_id'],ref['external_id'])!=current: raise TenantLinearError('source_changed',409)
                from tenant_knowledge_candidates_service import check_active
                check_active(profile,item)
                if check_before_ai: check_before_ai()
                asr_used=True
                from tenant_knowledge_media_cache import transcribe_once
                text,usage,asr_used=transcribe_once(profile,item,model,media,transcribe,check_before_ai=check_before_ai)
                item['asr_usage']=usage
                text=redact(text,names)[:4000]
                receipt.update(status='processed',code='transcribed',transcript=text,model=model)
                message['text']+='\n[Transcript] '+text
                continue
            if kind=='video' and video_used:
                receipt.update(status='partial',code='video_count_limit');continue
            cap=media_byte_limit(kind) if kind=='video' else MAX_BYTES-size
            if cap<=0:
                receipt.update(status='unavailable',code='byte_budget');continue
            data,mime=asyncio.run(asyncio.wait_for(download_media(current,max_bytes=cap),timeout=min(10,max(.1,deadline-time.monotonic()))))
            if load(profile,ref['lead_id'],ref['external_id'])!=current: raise TenantLinearError('source_changed',409)
            validate_media_size(kind,len(data))
            if kind=='video': video_used=True
            else:
                size+=len(data)
                if size>MAX_BYTES: receipt.update(status='unavailable',code='byte_budget');continue
            digest=hashlib.sha256(data).hexdigest();receipt['sha256']=digest
            if digest in seen:
                prior=seen[digest];receipt.update(status=prior['status'],code='duplicate',duplicate_of=prior['id']);continue
            seen[digest]=receipt
            visuals=[]
            if kind=='document':
                suffix={'application/pdf':'pdf','text/plain':'txt','application/vnd.openxmlformats-officedocument.wordprocessingml.document':'docx'}.get(mime)
                if not suffix: receipt.update(status='unsupported',code='unsupported_format');continue
                try:
                    _,_,text=extract('attachment.'+suffix,data)
                    message['text']+='\n[Extracted document text] '+redact(text,names)[:4000]
                    receipt.update(status='processed',code='document_text',observation=redact(text,names)[:4000]);continue
                except TenantLinearError:
                    if suffix!='pdf': raise
                    # Only textless, valid, unencrypted bounded PDFs may enter page rendering.
                    from pypdf import PdfReader
                    from io import BytesIO
                    reader=PdfReader(BytesIO(data),strict=True)
                    if reader.is_encrypted or len(reader.pages)>100 or any((p.extract_text() or '').strip() for p in reader.pages): raise ValueError('invalid_pdf')
                    if not consent: receipt.update(status='unavailable',code='external_media_permission_required');continue
                    visuals=pdf_pages(data)
            elif kind=='video':
                if not consent: receipt.update(status='unavailable',code='external_media_permission_required');continue
                visuals=video_frames(data,mime)
                receipt['audio_status']='not_requested'
                if item['route'].get('allow_paid_asr') and not asr_used:
                    try:
                        from tenant_connect_voice import transcription_model
                        from tenant_chat_ai_provider import transcribe
                        from tenant_knowledge_candidates_service import check_active
                        track=video_audio(data,mime);model=transcription_model(item)
                        if not model: raise LookupError('asr_model_unavailable')
                        if load(profile,ref['lead_id'],ref['external_id'])!=current: raise TenantLinearError('source_changed',409)
                        check_active(profile,item)
                        if check_before_ai: check_before_ai()
                        asr_used=True
                        from tenant_knowledge_media_cache import transcribe_once
                        text,usage,asr_used=transcribe_once(profile,item,model,track,transcribe,check_before_ai=check_before_ai)
                        item['asr_usage']=usage;text=redact(text,names)[:4000]
                        receipt.update(audio_status='processed',transcript=text,model=model)
                        message['text']+='\n[Video audio transcript] '+text
                    except Exception: receipt['audio_status']='unavailable_or_error'
            else:
                if not signature(data,mime): raise ValueError('invalid_image')
                visuals=[(data,mime,{})]
            if not consent: receipt.update(status='unavailable',code='external_media_permission_required');continue
            if item['route'].get('allow_vision') is False: receipt.update(status='unavailable',code='vision_not_approved');continue
            if not supports_images(item['model']): receipt.update(status='unsupported',code='model_has_no_vision');continue
            if visual_count+len(visuals)>MAX_VISUALS: receipt.update(status='partial',code='visual_budget');continue
            if not visuals: raise ValueError('empty_visuals')
            visual_count+=len(visuals)
            if item['route'].get('allow_vision') is True:
                from tenant_knowledge_media_cache import observe_once
                from tenant_chat_ai_provider import generate
                for content,content_type,origin in visuals:
                    if check_before_ai: check_before_ai()
                    text,_,_=observe_once(profile,item,ref,content,content_type,origin,generate,check_before_ai=check_before_ai)
                    message['text']+='\n[Untrusted visual observation; not a confirmed solution] '+text
                    receipt.setdefault('observations',[]).append({'origin':origin,'text':text})
                receipt.update(status='partial' if kind=='video' else 'processed',code='vision_observation',visuals=[origin for _,_,origin in visuals])
                continue
            for content,content_type,origin in visuals:
                images.append({'content':content,'content_type':content_type,'detail':'low','source_ref':message['ref']})
            receipt.update(status='pending_processing',code='awaiting_model',visuals=[origin for _,_,origin in visuals])
            message['text']+='\n[Visual inputs supplied: '+message['ref']+']'
        except LiveExtractionDisabled: raise
        except LookupError as error: receipt.update(status='unsupported',code=str(error))
        except TenantLinearError as error:
            code='video_size_limit' if kind=='video' and error.status==413 else 'media_size_limit' if error.status==413 else 'media_unavailable'
            receipt.update(status='unavailable' if error.status in {403,404,410,413} else 'unsupported' if error.status==415 else 'error',code=code)
        except ValueError as error:
            code=str(error)
            receipt.update(status='unsupported' if code in {'video_size_limit','video_duration_limit'} else 'error',code=code if code in {'video_size_limit','video_duration_limit'} else 'media_processing_failed')
        except Exception: receipt.update(status='error',code='media_processing_failed')
    for receipt in results:
        message=next(m for r,m in zip(item['refs'],item['messages']) if r['id']==receipt['id'])
        message['text']+='\n[Attachment status: '+receipt['status']+'; '+receipt['code']+'. Never infer unread contents.]'
    return images,results
