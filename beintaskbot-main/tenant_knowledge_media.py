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
from tenant_video_frames import (MAX_VIDEO_BYTES, MAX_VIDEO_SECONDS, MAX_VIDEO_AUDIO_BYTES,
    extract_audio as video_audio, sample_frames as video_frames, validate_video_duration)

MAX_FILES=4
MAX_VISUALS=2
MAX_BYTES=20*1024*1024
MAX_AUDIO_BYTES=10*1024*1024


def media_byte_limit(kind):
    """Analysis limits are deliberately independent from attachment preview."""
    if kind == 'video': return MAX_VIDEO_BYTES
    if kind in {'audio','voice'}: return MAX_AUDIO_BYTES
    return MAX_BYTES


def validate_media_size(kind, size):
    if not isinstance(size, int) or size < 0 or size > media_byte_limit(kind):
        raise ValueError('video_size_limit' if kind == 'video' else 'media_size_limit')


def pdf_pages(data):
    """Scanned PDF fallback is bounded local rendering, never a claim of OCR success."""
    if not shutil.which('pdftoppm'): raise LookupError('pdf_renderer_unavailable')
    with tempfile.TemporaryDirectory(prefix='knowledge-pages-') as directory:
        source=Path(directory)/'scan.pdf';source.write_bytes(data)
        result=subprocess.run(['pdftoppm','-f','1','-l','2','-scale-to','1280','-png',str(source),str(Path(directory)/'page')],capture_output=True,timeout=15)
        pages=sorted(Path(directory).glob('page-*.png'))
        if result.returncode or not pages: raise ValueError('pdf_render_failed')
        return [(p.read_bytes(),'image/png',{'page':i+1}) for i,p in enumerate(pages) if p.stat().st_size<=2*1024*1024]


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
        retry_unknown=(item.get('route') or {}).get('owner_retry_unknown') is True
        # The old receipt index is based on webhook metadata, which is not a
        # byte identity even when it contains a provider media ID. For any
        # explicitly acknowledged retry, download through the protected loader
        # first; observe_once then reuses only a cache keyed by SHA-256 of the
        # bytes actually fetched.
        prior=None if retry_unknown else reusable(profile,ref)
        reusable_failure=prior and prior.get('status') in {'unavailable','error'}
        if prior and not (retry_unknown and reusable_failure):
            reused=apply_reusable(message,prior)
            receipt={k:ref[k] for k in ('id','channel_id','lead_id','role','happened_at') if k in ref}
            receipt.update(kind=kind,**reused)
            results.append(receipt)
            continue
        receipt={k:ref[k] for k in ('id','channel_id','lead_id','role','happened_at') if k in ref}
        receipt.update(kind=kind,status='pending_processing',code='processing_limit')
        results.append(receipt)
        if len(results)>MAX_FILES:
            receipt.update(status='partial',code='file_count_limit');continue
        if time.monotonic()>deadline:
            receipt.update(status='partial',code='time_budget');continue
        if kind not in {'image','audio','voice','video','document'}:
            receipt.update(status='unsupported',code='unsupported_format');continue
        stage='source_validation'
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
                stage='media_download'
                data,mime=asyncio.run(asyncio.wait_for(download_media(current,max_bytes=min(MAX_AUDIO_BYTES,MAX_BYTES-size)),timeout=10))
                size+=len(data)
                validate_media_size(kind,len(data))
                if size>MAX_BYTES: receipt.update(status='unavailable',code='byte_budget');continue
                stage='media_validation'
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
            stage='media_download'
            data,mime=asyncio.run(asyncio.wait_for(download_media(current,max_bytes=cap),timeout=min(10,max(.1,deadline-time.monotonic()))))
            if load(profile,ref['lead_id'],ref['external_id'])!=current: raise TenantLinearError('source_changed',409)
            stage='media_validation'
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
                suffix={'application/pdf':'pdf','text/plain':'txt',
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'docx',
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'xlsx'}.get(mime)
                if not suffix: receipt.update(status='unsupported',code='unsupported_format');continue
                try:
                    _,_,text=extract('attachment.'+suffix,data)
                    safe_text=redact(text,names)
                    truncated=len(safe_text)>4000
                    safe_text=safe_text[:4000]
                    message['text']+='\n[Extracted document text] '+safe_text
                    receipt.update(status='partial' if truncated else 'processed',
                                   code='document_text_truncated' if truncated else 'document_text',
                                   observation=safe_text)
                    if truncated:receipt['truncated']=True
                    continue
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
                from tenant_ai_video_model import require_video_tariff
                video_input_model=item['route'].get('video_model') or item['model']
                # Reject before any optional ASR dispatch; the input ceiling
                # is a prerequisite for the whole video operation.
                require_video_tariff(video_input_model)
                stage='video_decode'
                visuals=video_frames(data,mime)
                receipt['audio_status']='not_requested'
                if item['route'].get('allow_paid_asr') and not asr_used:
                    try:
                        from tenant_connect_voice import transcription_model
                        from tenant_chat_ai_provider import transcribe_video
                        from tenant_knowledge_candidates_service import check_active
                        stage='audio_transcription'
                        track=video_audio(data,mime);model=transcription_model(item)
                        if not model: raise LookupError('asr_model_unavailable')
                        if load(profile,ref['lead_id'],ref['external_id'])!=current: raise TenantLinearError('source_changed',409)
                        check_active(profile,item)
                        if check_before_ai: check_before_ai()
                        asr_used=True
                        from tenant_knowledge_media_cache import transcribe_once
                        invoke=lambda key,selected,*args,**kwargs: transcribe_video(
                            key,selected,*args,expected_language=item['route'].get('language'),**kwargs)
                        text,usage,asr_used,diagnostics=transcribe_once(
                            profile,item,model,track,invoke,check_before_ai=check_before_ai,return_diagnostics=True)
                        item['asr_usage']=usage
                        speech_state=diagnostics.get('speech_state') or 'uncertain'
                        receipt.update(audio_status=('processed' if speech_state=='confident' else speech_state),model=model,
                            audio_language=diagnostics.get('speech_language') or '',
                            audio_reason=diagnostics.get('speech_reason') or '')
                        if speech_state=='confident' and text:
                            receipt['transcript']=text
                            message['text']+='\n[Unverified video speech transcript; confirm before treating as fact] '+text
                    except Exception as error: receipt['audio_status']='unknown' if getattr(error,'media_outcome_unknown',False) else 'unavailable_or_error'
            else:
                if not signature(data,mime): raise ValueError('invalid_image')
                visuals=[(data,mime,{})]
            if not consent: receipt.update(status='unavailable',code='external_media_permission_required');continue
            if item['route'].get('allow_vision') is False: receipt.update(status='unavailable',code='vision_not_approved');continue
            observation_model=((item['route'].get('video_model') if kind=='video' else
                item['route'].get('campaign_photo_model') if kind=='image' else item['model']) or item['model'])
            if not supports_images(observation_model): receipt.update(status='unsupported',code='model_has_no_vision');continue
            if visual_count+len(visuals)>MAX_VISUALS: receipt.update(status='partial',code='visual_budget');continue
            if not visuals: raise ValueError('empty_visuals')
            visual_count+=len(visuals)
            if item['route'].get('allow_vision') is True:
                stage='visual_analysis'
                from tenant_knowledge_media_cache import observe_once
                from tenant_chat_ai_provider import generate
                observation_route={**item['route']}
                if kind=='video':
                    observation_route['_video_input_model']=observation_model
                observation_item={**item,'model':observation_model,'route':observation_route}
                if kind=='image' and item['route'].get('campaign_photo_model'):
                    observation_item={**observation_item,'key':item.get('photo_api_key') or item['key'],
                      'route':{**item['route'],'photo_model':observation_model}}
                for content,content_type,origin in visuals:
                    if check_before_ai: check_before_ai()
                    try:
                        text,_,_=observe_once(profile,observation_item,ref,content,content_type,origin,generate,check_before_ai=check_before_ai)
                    finally:
                        # observe_once increments attempts on its per-model
                        # item copy. Propagate that boundary to the request
                        # object used by finish() for unknown-status and spend
                        # settlement, including provider exceptions. The usage
                        # list is created lazily on that copy, so propagate it
                        # as well before accounting.
                        item['vision_attempts']=int(observation_item.get('vision_attempts') or 0)
                        item['vision_usage']=list(observation_item.get('vision_usage') or [])
                    message['text']+='\n[Untrusted visual observation; not a confirmed solution] '+text
                    receipt.setdefault('observations',[]).append({'origin':origin,'text':text})
                receipt.update(status='partial' if kind=='video' else 'processed',code='vision_observation',visuals=[origin for _,_,origin in visuals])
                continue
            stage='visual_input_prepare'
            for content,content_type,origin in visuals:
                images.append({'content':content,'content_type':content_type,'detail':'low','source_ref':message['ref']})
            receipt.update(status='pending_processing',code='awaiting_model',visuals=[origin for _,_,origin in visuals])
            message['text']+='\n[Visual inputs supplied: '+message['ref']+']'
        except LiveExtractionDisabled: raise
        except LookupError as error:
            _failure_detail(receipt,error)
            code=str(error) if str(error) in {'video_decoder_unavailable','pdf_renderer_unavailable','video_audio_unavailable','asr_model_unavailable'} else 'media_processing_failed'
            receipt.update(status='unknown' if getattr(error,'media_outcome_unknown',False) else 'unsupported',code='media_result_unknown' if getattr(error,'media_outcome_unknown',False) else code,failure_stage=stage)
        except TenantLinearError as error:
            _failure_detail(receipt,error)
            if getattr(error,'media_outcome_unknown',False):
                receipt.update(status='unknown',code='media_result_unknown',failure_stage=stage);continue
            code=('unsupported_document_feature' if kind=='document' and error.status==415 else
                'video_size_limit' if kind=='video' and error.status==413 else
                'media_size_limit' if error.status==413 else 'media_unavailable')
            receipt.update(status='unavailable' if error.status in {403,404,410,413} else 'unsupported' if error.status==415 else 'error',code=code,failure_stage=stage)
        except ValueError as error:
            _failure_detail(receipt,error)
            if getattr(error,'media_outcome_unknown',False):
                receipt.update(status='unknown',code='media_result_unknown',failure_stage=stage);continue
            code=str(error)
            receipt.update(status='unsupported' if code in {'video_size_limit','video_duration_limit'} else 'error',code=code if code in {'video_size_limit','video_duration_limit'} else 'media_processing_failed',failure_stage=stage)
        except Exception as error:
            _failure_detail(receipt,error)
            receipt.update(status='unknown' if getattr(error,'media_outcome_unknown',False) else 'error',code='media_result_unknown' if getattr(error,'media_outcome_unknown',False) else 'media_processing_failed',failure_stage=stage)
    for receipt in results:
        message=next(m for r,m in zip(item['refs'],item['messages']) if r['id']==receipt['id'])
        message['text']+='\n[Attachment status: '+receipt['status']+'; '+receipt['code']+'. Never infer unread contents.]'
    return images,results


def _failure_detail(receipt,error):
    from tenant_chat_ai_provider import is_safe_failure_code
    code=getattr(error,'media_failure_code',None)
    receipt['failure_code']=code if is_safe_failure_code(code) else 'media_processing_failed'
