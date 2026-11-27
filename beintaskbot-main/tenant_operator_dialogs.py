"""Read projection over existing conversations, AI state, tickets and cached messages. No new inbox tables."""
import json
import re
import uuid
from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agents_store import _decorate_conversation_state
from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access, assert_route
from tenant_platform import _connect


def public(row):
    return {k:v.isoformat() if hasattr(v,'isoformat') else str(v) if isinstance(v,uuid.UUID) else v
            for k,v in dict(row).items()}


def _safe_group_name(item):
    """A group title may be absent, but a JID is never a display title."""
    # Route rows from Connect and the support-case store carry provider
    # metadata as display_name; customer_name is only a UI projection and is
    # not present on every path. Prefer the explicit local override.
    conversation = str(item.get('conversation_id') or '').strip()
    phone = str(item.get('phone') or '').strip()
    technical = {conversation, conversation.split('@', 1)[0], phone}
    for candidate in (item.get('manual_display_name'),item.get('customer_name'),item.get('display_name')):
        name=str(candidate or '').strip()
        if name and name not in technical and '@' not in name:
            return name
    return ''


def _manual_label(value):
    if not isinstance(value, str):
        raise TenantLinearError('Отображаемое имя должно быть текстом.')
    value = value.strip()
    if len(value) > 180 or any(ord(char) < 32 for char in value):
        raise TenantLinearError('Отображаемое имя содержит недопустимые символы или слишком длинное.')
    return value


def _participant_fields(raw):
    """Return provider facts without resolving or joining LID identities."""
    if not isinstance(raw, dict):
        return '', '', ''
    jid = str(raw.get('participantJid') or raw.get('participantAlt') or raw.get('senderJid') or '')[:180]
    alt = str(raw.get('participantAlt') or '')[:180]
    whatsapp_name = str(raw.get('participantName') or raw.get('senderName') or raw.get('pushName') or '')[:180]
    return jid, alt, whatsapp_name


def _audio_duration(raw):
    """Expose a bounded provider duration only when the incoming event has one."""
    if not isinstance(raw, dict):
        return None
    for key in ('durationSeconds', 'duration_seconds', 'seconds', 'duration'):
        value = raw.get(key)
        try:
            seconds = int(float(value))
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 <= seconds <= 24 * 60 * 60:
            return seconds
    return None


def _media_size(raw):
    """Return a positive bounded size already recorded in the provider event."""
    if not isinstance(raw, dict):
        return None
    for key in ('size_bytes', 'file_size', 'fileLength'):
        value = raw.get(key)
        if type(value) is int:
            size = value
        elif isinstance(value, str) and value.isdecimal() and len(value) <= 19:
            size = int(value)
        else:
            continue
        if 0 < size <= (1 << 63) - 1:
            return size
    return None


def _media_filename(raw):
    """Expose a short safe basename from provider metadata, never a URL/path."""
    if not isinstance(raw, dict):
        return ''
    stack=[raw];seen=0
    while stack and seen<120:
        item=stack.pop();seen+=1
        if isinstance(item,dict):
            for key in ('fileName','filename','file_name','documentName','originalName'):
                value=item.get(key)
                if isinstance(value,str):
                    value=value.strip()
                    if '://' in value or value.startswith('//') or any(ord(char)<32 for char in value):
                        continue
                    name=value.replace('\\','/').rsplit('/',1)[-1]
                    if name:return name[:160]
            stack.extend(value for value in item.values() if isinstance(value,(dict,list)))
        elif isinstance(item,list):stack.extend(value for value in item if isinstance(value,(dict,list)))
    return ''


def _media_thumbnail(raw):
    """Only a small real image thumbnail; never a URL, quote or guessed image."""
    if not isinstance(raw, dict):
        return ''
    candidates = [raw]
    if isinstance(raw.get('imageMessage'), dict):
        candidates.append(raw['imageMessage'])
    message = raw.get('message')
    if isinstance(message, dict) and isinstance(message.get('imageMessage'), dict):
        candidates.append(message['imageMessage'])
    import base64
    import io
    import warnings
    for item in candidates:
        value = item.get('jpegThumbnail') or item.get('thumbnail_base64') or item.get('thumbnailBase64')
        if not isinstance(value, str) or not 1 <= len(value) <= 44000:
            continue
        try:
            if value.startswith('data:image/'):
                prefix, value = value.split(',', 1)
                if prefix not in {'data:image/jpeg;base64','data:image/png;base64','data:image/webp;base64'}:
                    continue
            data = base64.b64decode(value, validate=True)
            if not 0 < len(data) <= 32768:
                continue
            from PIL import Image
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in {'JPEG','PNG','WEBP'} or min(image.size) < 1 or max(image.size) > 1024:
                        continue
                    image.load()
                    image.thumbnail((192, 192))
                    clean = io.BytesIO()
                    image.convert('RGB').save(clean, format='JPEG', quality=65)
            encoded = base64.b64encode(clean.getvalue()).decode('ascii')
            if len(encoded) <= 24000:
                return 'data:image/jpeg;base64,' + encoded
        except Exception:
            continue
    return ''


def _audio_diagnostic(status, code, result, at):
    """Product diagnostics: fixed enums only, never raw errors, text or URLs."""
    if status not in {'processed','partial','unavailable','unsupported','error','unknown'}:return None
    from tenant_media_admission import REASONS
    codes=set(REASONS) | {'transcribed','transcript_truncated','existing_transcript','media_unavailable',
        'source_fetch_unavailable','source_check_failed','source_rate_limited','source_access_denied',
        'source_changed','media_processing_failed','media_result_unknown','asr_authorization_required',
        'legacy_voice_version_unverified','pre_dispatch_retryable','media_size_limit',
        'asr_model_unavailable','audio_evidence_incomplete','legacy_exact_transcript'}
    result=result if isinstance(result,dict) else {}
    stage=result.get('failure_stage')
    if stage not in {'source_validation','media_download','media_validation','audio_transcription'}:stage='unknown'
    return {'status':status,'code':code if code in codes else 'audio_evidence_incomplete',
            'stage':stage,'at':str(at or '')}


def _attach_saved_audio_transcripts(cur, tenant, rows, *, conversation_id=None,view_channel=None):
    """Read only exact-version ASR evidence for already-authorized message rows."""
    from tenant_media_evidence import media_version, source_hash, _body_hash, retryable_audio_download_failure
    audio=[row for row in rows if str(row.get('message_type') or '').lower() in {'audio','voice','voice_message','ptt'}
           and str(row.get('channel') or '').startswith('connect:') and row.get('external_id')]
    if not audio:return
    for row in audio:
        for field in ('_saved_audio_transcript','_saved_audio_state','_saved_audio_diagnostic','_saved_audio_receipt_diagnostic','_explicit_audio_transcript'):
            row.pop(field,None)
    from tenant_dialog_audio_store import selected_successes
    explicit_rows=selected_successes(cur,tenant,audio,view_channel=view_channel,view_conversation=conversation_id)
    for row in audio:
        explicit=explicit_rows.get(str(row['id']))
        if explicit:row['_explicit_audio_transcript']=explicit['transcript']
    cur.execute("SELECT to_regclass('public.saas_media_evidence_receipts') AS relation")
    if not (cur.fetchone() or {}).get('relation'):return
    keys=[]
    for row in audio:
        channel=str(row['channel']).removeprefix('connect:');kind=str(row['message_type']).lower()
        ref={'external_id':row['external_id'],'body_hash':_body_hash(row.get('body')),
             'media_kind':kind,'media_version':media_version(row.get('raw'))}
        keys.append((channel,str(row['external_id']),source_hash(ref),kind))
    placeholders=','.join(['(%s::uuid,%s,%s,%s)']*len(keys))
    cur.execute("""SELECT channel_id::text AS channel_id,external_id,source_hash,media_kind,status,code,result,provenance,updated_at
      FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid
        AND (channel_id,external_id,source_hash,media_kind) IN ("""+placeholders+")",
      (tenant,*[value for key in keys for value in key]))
    receipts={(str(r['channel_id']),str(r['external_id']),str(r['source_hash']),str(r['media_kind'])):r for r in cur.fetchall()}
    for row,key in zip(audio,keys):
        receipt=receipts.get(key) or {};result=receipt.get('result') if isinstance(receipt.get('result'),dict) else {}
        text=result.get('transcript')
        diagnostic=_audio_diagnostic(receipt.get('status'),receipt.get('code'),result,receipt.get('updated_at'))
        if diagnostic:
            row['_saved_audio_diagnostic']=diagnostic
            ref={'external_id':row['external_id'],'body_hash':_body_hash(row.get('body')),
                 'media_kind':str(row['message_type']).lower(),'media_version':media_version(row.get('raw'))}
            row['_saved_audio_receipt_diagnostic']={**diagnostic,
                'pre_dispatch_proven':retryable_audio_download_failure(receipt,ref)}
        if receipt.get('status')=='error':row['_saved_audio_state']='failed'
        if receipt.get('status')=='processed' and isinstance(text,str) and text.strip():
            row['_saved_audio_transcript']=text
    # Existing authorized history, bounded metadata-only projection. No model,
    # download, log access or private copilot text is read or dispatched here.
    if conversation_id:
        cur.execute("SELECT to_regclass('public.saas_chat_ai_runs') AS relation")
        if (cur.fetchone() or {}).get('relation'):
            channels=sorted({key[0] for key in keys})
            cur.execute('''SELECT channel_id,result->'media_failure' AS failure,
              result->'source_versions' AS refs,result->'context_usage'->'evidence' AS evidence,updated_at
              FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND provider='connect'
                AND channel_id=ANY(%s::text[]) AND conversation_id=%s AND result IS NOT NULL
              ORDER BY updated_at DESC LIMIT 20''',(tenant,channels,conversation_id))
            found={}
            for attempt in cur.fetchall():
                failure=attempt.get('failure')
                entries=[]
                if isinstance(failure,dict):entries.append((failure.get('source_id'),failure.get('source_hash'),failure.get('kind'),failure))
                refs=attempt.get('refs') if isinstance(attempt.get('refs'),list) else []
                evidence=attempt.get('evidence') if isinstance(attempt.get('evidence'),list) else []
                for item in evidence:
                    if not isinstance(item,dict):continue
                    ref=next((r for r in refs if isinstance(r,dict) and r.get('external_id')==item.get('source_id')
                              and str(r.get('channel_id'))==str(attempt['channel_id'])),None)
                    if ref:entries.append((item.get('source_id'),source_hash(ref),item.get('kind'),item))
                for external,version,kind,item in entries:
                    key=(str(attempt['channel_id']),str(external),str(version),str(kind))
                    diagnostic=_audio_diagnostic(item.get('status'),item.get('code'),item,attempt.get('updated_at'))
                    if key in keys and key not in found and diagnostic:found[key]=diagnostic
            for row,key in zip(audio,keys):
                if key in found and not row.get('_saved_audio_transcript'):
                    prior=row.get('_saved_audio_diagnostic') or {}
                    if not prior or found[key]['at']>=prior.get('at',''):row['_saved_audio_diagnostic']=found[key]


def _attach_saved_visual_status(cur, tenant, rows):
    """Two bounded reads, no schema writes or OCR content in the UI projection."""
    from tenant_media_evidence import source_hash, media_version, _body_hash, has_visual_result
    selected=[r for r in rows if r.get('message_type') in {'image','video','document'}
              and str(r.get('channel') or '').startswith('connect:') and r.get('external_id')]
    if not selected:return
    for row in selected:row.pop('media_evidence',None)
    cur.execute("SELECT to_regclass('public.saas_media_evidence_receipts') AS relation")
    if not (cur.fetchone() or {}).get('relation'):return
    keys=[(str(row['channel']).removeprefix('connect:'),str(row['external_id']),
           source_hash({'external_id':row['external_id'],'body_hash':_body_hash(row.get('body')),
                        'media_kind':row['message_type'],'media_version':media_version(row.get('raw'))}),row['message_type']) for row in selected]
    placeholders=','.join(['(%s::uuid,%s,%s,%s)']*len(keys))
    cur.execute('''SELECT channel_id::text AS channel_id,external_id,source_hash,media_kind,status,coverage,result
      FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid
      AND (channel_id,external_id,source_hash,media_kind) IN ('''+placeholders+')',
      (tenant,*[value for key in keys for value in key]))
    receipts={(str(r['channel_id']),str(r['external_id']),str(r['source_hash']),r['media_kind']):r for r in cur.fetchall()}
    for row,key in zip(selected,keys):
        receipt=receipts.get(key) or {}
        row['media_evidence']={'status':receipt.get('status'),
            'coverage':receipt.get('coverage') or {},'has_visual_result':has_visual_result(receipt.get('result'))}


# Apply before LIMIT/cursor pagination. Keep the original Connect event in storage:
# only a persisted recording in this exact physical route can replace its neutral
# observation. Missed/rejected calls, missing recordings and ambiguous peers remain.
_RECORDED_CALL_OBSERVATION_FILTER = ''' AND NOT EXISTS (
  SELECT 1 FROM saas_browser_calls c
  JOIN saas_test_call_recordings r ON r.id=c.recording_id AND r.browser_call_id=c.id
    AND r.tenant_id=c.tenant_id AND r.channel_id=c.channel_id
    AND r.conversation_id=c.conversation_id AND r.lead_id=c.lead_id
  JOIN saas_crm_messages recorded ON recorded.tenant_id=r.tenant_id
    AND recorded.kommo_lead_id=r.lead_id AND recorded.channel='connect:'||r.channel_id::text
    AND recorded.external_id='internal:call-recording:'||r.id::text
    AND recorded.message_type='call_recording' AND recorded.direction='system'
    AND recorded.raw->>'recording_id'=r.id::text
    AND recorded.raw->>'conversationId'=r.conversation_id
  WHERE m.message_type='call_observed' AND m.direction='system'
    AND m.external_id LIKE 'connect:call:%%' AND m.raw->'isVideo'='false'::jsonb
    AND c.tenant_id=m.tenant_id AND m.channel='connect:'||c.channel_id::text
    AND c.lead_id=m.kommo_lead_id AND c.conversation_id=m.raw->>'conversationId'
    AND c.provider_id=m.raw->>'externalCallId')'''


def _attach_call_recording_directions(cur, tenant, rows):
    """Enrich only authorized history rows from their exact stored call binding."""
    candidates=[]
    for row in rows:
        row.pop('_call_recording_direction',None)
        raw=row.get('raw')
        if row.get('message_type')!='call_recording' or row.get('direction')!='system' or not isinstance(raw,dict):
            continue
        try:record_id=str(uuid.UUID(str(raw['recording_id'])))
        except (KeyError,ValueError,TypeError):continue
        if row.get('external_id')=='internal:call-recording:'+record_id:
            candidates.append((row,record_id))
    if not candidates:return
    cur.execute('''SELECT r.id,r.channel_id,r.conversation_id,r.lead_id,c.direction
      FROM saas_test_call_recordings r JOIN saas_browser_calls c
        ON c.id=r.browser_call_id AND c.recording_id=r.id AND c.tenant_id=r.tenant_id
        AND c.channel_id=r.channel_id AND c.conversation_id=r.conversation_id AND c.lead_id=r.lead_id
      WHERE r.tenant_id=%s::uuid AND r.id=ANY(%s::uuid[])''',
      (tenant,list({record_id for _,record_id in candidates})))
    bindings={str(row['id']):row for row in cur.fetchall()}
    for row,record_id in candidates:
        binding=bindings.get(record_id)
        if (binding and row.get('channel')=='connect:'+str(binding['channel_id'])
                and str(row.get('source_lead_id'))==str(binding['lead_id'])
                and row.get('source_conversation_id')==binding['conversation_id']
                and binding['direction'] in {'inbound','outbound'}):
            row['_call_recording_direction']=binding['direction']


def _message(item, *, group=False, participant_names=None, employee_participants=None, contact_name='', contact_route=''):
    value = dict(item)
    call_direction=value.pop('_call_recording_direction',None)
    media_evidence=value.pop('media_evidence',{}) or {}
    value.pop('media_evidence_context',None)
    saved_audio_transcript=value.pop('_saved_audio_transcript',None)
    explicit_audio_transcript=value.pop('_explicit_audio_transcript',None)
    saved_audio_state=value.pop('_saved_audio_state',None)
    audio_diagnostic=value.pop('_saved_audio_diagnostic',None)
    receipt_diagnostic=value.pop('_saved_audio_receipt_diagnostic',None)
    raw = value.pop('raw', None) or {}
    if isinstance(raw, dict) and raw.get('_crm_text_edit'):
        value['wa_edited'] = True
    if isinstance(raw, dict) and raw.get('message_deleted') is True:
        value['message_deleted'] = True
        value['message_delete_source'] = raw.get('message_delete_source') or 'whatsapp'
        value['message_deleted_at'] = raw.get('message_deleted_at')
        value['body'] = ''
        value['media_url'] = ''
    direction = str(value.get('direction') or '')
    if direction not in {'incoming', 'outgoing'}:
        direction = 'system'
    author = str(value.get('author_name') or '')
    if direction == 'outgoing':
        author_type = 'ai' if author == 'AI Agent' or (isinstance(raw, dict) and raw.get('run_id')) else 'operator'
    elif direction == 'incoming':
        author_type = 'participant' if group else 'customer'
    else:
        author_type = 'system'
    if direction == 'incoming' and (value.get('sending_number') or isinstance(raw,dict) and raw.get('verified_employee_id') is not None):
        author_type='employee'
    if group and isinstance(raw,dict) and raw.get('ai_authored') is True:
        author_type='ai'
    participant_jid, participant_alt, whatsapp_name = _participant_fields(raw)
    employee_key=(str(value.get('source_channel_id') or value.get('channel','').removeprefix('connect:')),participant_jid)
    if direction == 'incoming' and group and employee_key in (employee_participants or set()):
        author_type='employee'
    if group and direction=='outgoing' and author in {'Operator','WhatsApp operator'} and whatsapp_name:
        author=whatsapp_name
    if direction in {'incoming','outgoing'} and group and author_type!='ai' and not value.get('sending_number'):
        # Resolution is intentionally exact-JID scoped.  Do not conflate a
        # privacy LID with a phone JID, even if both are visible in one event.
        author = str((participant_names or {}).get((str(value.get('source_channel_id') or value.get('channel','').removeprefix('connect:')),participant_jid)) or (participant_names or {}).get(participant_jid) or author or whatsapp_name or '')
    if direction == 'incoming' and group and not author and participant_jid.endswith('@s.whatsapp.net'):
        candidate = participant_jid.split('@', 1)[0]
        if candidate.isdecimal():
            author = candidate
    # Direct Connect chats can retain a technical route fallback in
    # customer_name for routing/search, but that fallback is not an author.
    # Preserve an independently named genuine author when no route name is
    # available. Groups, employees, system and legacy messages are unchanged.
    if not group and direction == 'incoming' and author_type == 'customer':
        if contact_route and str(value.get('channel') or '').startswith('connect:'):
            routes = (str(contact_route or ''), str(value.get('source_conversation_id') or ''))
            identifiers = {part for route in routes for part in (route, route.split('@',1)[0])}
            def meaningful_name(label):
                label = str(label or '').strip()
                if (label in identifiers or re.search(r'@(?:s\.whatsapp\.net|g\.us|lid)$',label,re.I)
                        or re.fullmatch(r'[+0-9().\s-]+',label)):
                    return ''
                return label
            author = meaningful_name(contact_name) or meaningful_name(author)
        elif str(contact_name).strip():
            author = str(contact_name).strip()
    value['direction'] = direction
    value['author_type'] = author_type
    value['author_name'] = author
    value['participant_jid'] = participant_jid
    value['participant_alt'] = participant_alt
    value['source'] = 'connect' if str(value.get('channel') or '').startswith('connect:') else 'kommo'
    if value.get('message_type')=='call_recording' and direction=='system' and isinstance(raw,dict):
        try:
            value['call_recording']={'id':str(uuid.UUID(str(raw['recording_id']))),
                'direction':call_direction if call_direction in {'inbound','outbound'} else 'unknown',
                'duration_ms':max(0,min(900000 if raw.get('test_recording') is False else 120000,int(raw.get('duration_ms',0)))),
                'complete':raw.get('complete') is True,'expires_at':str(raw.get('expires_at','')),
                'test_recording':raw.get('test_recording',True) is not False,
                'account_number':str(raw.get('call_account_number','')) if re.fullmatch(r'[0-9]{5,22}',str(raw.get('call_account_number',''))) else '',
                'archived_owner_binding':raw.get('archived_owner_binding') is True}
        except (KeyError,ValueError,TypeError):
            pass
    value.pop('delivery_state', None)
    if value.get('message_type') in {'call_missed', 'call_rejected', 'call_invalidated', 'call_observed'}:
        value['call_is_video'] = isinstance(raw, dict) and raw.get('isVideo') is True
    if value['source'] == 'connect' and not value.get('message_deleted'):
        if value.get('message_type') in {'contact', 'location'} and isinstance(raw, dict):
            from tenant_whatsapp_shared_content import facts as shared_facts
            value.update(shared_facts(value['message_type'], raw))
        # Display only a persisted provider/receipt status; viewing a dialog or
        # an outgoing row alone never proves delivery or customer readership.
        if direction == 'outgoing':
            status = raw.get('status') if isinstance(raw, dict) else None
            if not isinstance(status, str):
                status = None
            if status in {'pending', 'retrying', 'unknown', 'failed', 'sent', 'delivered', 'read'}:
                value['delivery_state'] = status
            elif status in {'accepted', 'queued', 'sending', 'reserved'}:
                value['delivery_state'] = 'pending'
            if str(value.get('message_type') or '').lower() == 'audio' and isinstance(raw,dict):
                try:
                    uuid.UUID(str(raw['voice_receipt_request_id']))
                    int(raw['verified_employee_id'])
                    value['customer_voice_status'] = True
                except (KeyError, ValueError, TypeError):
                    pass
        # Only validated provider identifiers leave the server; raw webhook data stays private.
        for key,target in (('externalMessageId','provider_message_id'),('quotedExternalMessageId','quoted_provider_message_id')):
            identifier_value=raw.get(key) if isinstance(raw,dict) else None
            if isinstance(identifier_value,str) and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',identifier_value):
                value[target]=identifier_value
        # No provider URL or raw webhook payload crosses the browser boundary.
        value['media_url'] = ''
        if value.get('message_type') in {'image','audio','voice','voice_message','ptt','video','document'}:
            from tenant_media_evidence import media_version
            value['media_version']=media_version(raw)
            filename=_media_filename(raw)
            if filename:value['media_filename']=filename
            if value['message_type']=='document' and isinstance(raw,dict):
                # Publish only the recognized presentation type, not raw metadata.
                mime=raw.get('mimeType') or raw.get('mimetype') or raw.get('mime_type')
                if isinstance(mime,str) and mime.split(';',1)[0].strip().lower()=='application/pdf':
                    value['media_mime_type']='application/pdf'
            size=_media_size(raw)
            if size is not None:value['media_size_bytes']=size
            if value.get('message_type') == 'image':
                thumbnail = _media_thumbnail(raw)
                if thumbnail:value['media_thumbnail']=thumbnail
        transcript = raw.get('voice_transcription', {}) if isinstance(raw, dict) else {}
        if value.get('message_type') in {'image','audio','voice','voice_message','ptt','video','document'}:
            state=transcript.get('status') if isinstance(transcript,dict) else None
            value['analysis_state']=state if state in {'completed','pending','processing','failed','disabled','not_required'} else 'not_requested'
            if value['analysis_state']=='not_requested' and saved_audio_state:value['analysis_state']=saved_audio_state
            code=transcript.get('error_code') if isinstance(transcript,dict) else None
            if state=='failed':
                if code in {'voice_media_unavailable','voice_download_timeout'}:
                    value['analysis_state']='file_unavailable'
                elif code in {'voice_unsupported','voice_invalid_audio'}:
                    value['analysis_state']='unsupported'
                elif code in {'voice_transcription_failed','voice_model_unavailable'}:
                    value['analysis_state']='transcription_unavailable'
        if value.get('message_type') in {'image','video','document'}:
            # Receipt metadata is exact tenant/channel/source-version scoped.
            # Do not expose private OCR or infer vision from legacy ASR status.
            value['analysis_state']='not_requested'
            if media_evidence.get('status') in {'processed','partial'} and media_evidence.get('has_visual_result'):
                complete=(media_evidence.get('coverage') or {}).get('complete') is True
                value['analysis_state']='visual_completed' if complete and value['message_type']!='video' else 'visual_partial'
            elif media_evidence.get('status') in {'error','unsupported','unavailable'}:
                value['analysis_state']='failed'
        if value.get('message_type') in {'audio','voice','voice_message','ptt'}:
            text=explicit_audio_transcript or (str(value.get('body') or '') if isinstance(transcript,dict) and transcript.get('status')=='completed' else '') or saved_audio_transcript
            if isinstance(text,str) and text.strip():
                value['audio_transcript']=text
                value['has_transcription']=True
        if value.get('message_type') in {'audio','voice','voice_message','ptt'}:
            if receipt_diagnostic:value['analysis_receipt_diagnostic']=receipt_diagnostic
            if audio_diagnostic:
                value['analysis_diagnostic']=audio_diagnostic
                if audio_diagnostic['status']!='processed' and not value.get('has_transcription'):
                    value['analysis_state']='unsupported' if audio_diagnostic['status']=='unsupported' else 'failed'
            duration = _audio_duration(raw)
            if duration is not None:
                value['duration_seconds'] = duration
    return public(value)


def projection(cur, policy, *, include_read_cursors=False, include_contact_names=True, allow_closed_archive=False, owner_overview=False, search_overview=False, duplicate_metadata_only=False, hide_removed=False):
    from tenant_operator_dialog_removal import CONNECT_VISIBLE_SQL
    removal_filter = ' AND ' + CONNECT_VISIBLE_SQL if hide_removed else ''
    scope,params=[],[]
    if policy.profile.get('role')!='owner':
        for item in (policy.pipeline_scope() if policy.can_read_legacy_crm() else []):
            part='d.pipeline_id=%s';params.append(item['pipeline_id'])
            if item['status_ids']:
                part+=' AND d.status_id=ANY(%s::bigint[])';params.append(item['status_ids'])
            scope.append('('+part+')')
    kommo_scope='TRUE' if policy.profile.get('role')=='owner' else '('+' OR '.join(scope)+')' if scope else 'FALSE'
    if policy.action_scope('dialogs','view') is not None:
        kommo_scope='TRUE';params=[]
    from tenant_dialog_access import sql_filter
    from tenant_dialog_lifecycle import visibility_sql
    dialog_filter='('+sql_filter(policy)+') AND '+visibility_sql(cur, policy,'routes.provider','routes.channel_id','routes.conversation_id',
        allow_closed_archive=allow_closed_archive,owner_overview=owner_overview,search_overview=search_overview)
    from tenant_support_access import sql_filter as support_filter
    ticket_filter=support_filter(policy,'st')
    handoff_filter=support_filter(policy,'ht')
    # A visible conversation may use its explicitly linked contact's name
    # without granting access to the CRM directory. Match the complete
    # contact route; never infer a CRM identity from a phone or JID alone.
    # Optional CRM may not be installed in an older deployment. Check once
    # for this request and avoid referencing missing relations/columns at all.
    # Do not run DDL or retain a positive result across requests/databases.
    from tenant_schema_preflight import ready as schema_ready
    # The same display-name filter applies before choosing either a route
    # label or the first usable label across retained direct-route aliases.
    usable_label = """clean.label<>'' AND clean.label NOT IN (
          c.conversation_id,split_part(c.conversation_id,'@',1){other_ids})
          AND clean.label !~* '@(s[.]whatsapp[.]net|g[.]us|lid)$'
          AND clean.label !~ '^[+0-9().[:space:]-]+$'"""
    # Historical rows predate the kind column and keep its empty default.
    # Such a row may contribute only on an exact direct route, never on a
    # provider group address, and both ends of an alias must pass this check.
    direct_route = "({route}.conversation_kind IN ('contact','') AND {route}.conversation_id !~* '@g[.]us$')"
    canonical_direct = direct_route.format(route='c')
    contact_join,contact_name='', 'NULL::text'
    if include_contact_names and schema_ready(cur, {
        'saas_crm_native_contact_conversations': ('tenant_id','provider','channel_id','conversation_id','contact_id'),
        'saas_crm_native_contacts': ('tenant_id','id','display_name','archived_at'),
    }, []):
        contact_name='linked_contact.dialog_name'
        contact_join='''      LEFT JOIN LATERAL(SELECT contact.display_name AS dialog_name
        FROM saas_crm_native_contact_conversations link
        JOIN saas_crm_native_contacts contact ON contact.tenant_id=link.tenant_id AND contact.id=link.contact_id
        WHERE '''+canonical_direct+''' AND link.tenant_id=c.tenant_id AND link.provider='connect'
          AND link.channel_id=c.connect_channel_id AND link.conversation_id=c.conversation_id
          AND contact.archived_at IS NULL AND position('@' in contact.display_name)=0
      ) linked_contact ON TRUE
'''
    manual_phone,phone_join="''::text",''
    if include_contact_names and policy.profile.get('role')=='owner' and schema_ready(cur, {
        'saas_connect_dialog_display_phones': ('tenant_id','channel_id','conversation_id','lead_id','phone'),
    }, []):
        manual_phone="COALESCE(CASE WHEN "+canonical_direct+" THEN mdp.phone END,'')"
        phone_join='''LEFT JOIN saas_connect_dialog_display_phones mdp ON mdp.tenant_id=c.tenant_id
          AND mdp.channel_id=c.connect_channel_id AND mdp.conversation_id=c.conversation_id AND mdp.lead_id=c.lead_id'''
    # Discovery needs stored metadata; no latest-message or cursor query.
    if duplicate_metadata_only:
        if include_read_cursors:
            raise ValueError('metadata-only cannot project read cursors')
        null_preview = ("NULL::text AS last_message_id,NULL::text AS last_message_direction,"
                        "NULL::text AS last_message_type,NULL::text AS last_message_text")
        connect_latest = "LEFT JOIN LATERAL(SELECT c.last_message_at,"+null_preview+") lm ON TRUE\n"
        kommo_latest = "JOIN LATERAL(SELECT ''::text AS channel,NULL::timestamptz AS last_message_at,"+null_preview+") lm ON FALSE\n"
    else:
        connect_latest = '''      LEFT JOIN LATERAL(SELECT COALESCE(m.happened_at,m.created_at) AS last_message_at,m.id::text AS last_message_id,
          m.direction AS last_message_direction,m.message_type AS last_message_type,left(COALESCE(m.body,''),240) AS last_message_text
        FROM saas_crm_messages m
        WHERE m.tenant_id=c.tenant_id AND m.kommo_lead_id=ANY(ARRAY(SELECT c.lead_id UNION ALL
          SELECT dl.source_lead FROM saas_connect_dialog_links dl WHERE dl.tenant_id=c.tenant_id
            AND dl.channel_id=c.connect_channel_id AND dl.canonical_id=c.conversation_id AND dl.active)) AND m.channel='connect:'||c.connect_channel_id::text
        ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 1) lm ON TRUE
'''
        kommo_latest = '''      JOIN LATERAL(SELECT m.channel,COALESCE(m.happened_at,m.created_at) AS last_message_at,m.id::text AS last_message_id,
          m.direction AS last_message_direction,m.message_type AS last_message_type,left(COALESCE(m.body,''),240) AS last_message_text
        FROM saas_crm_messages m
        WHERE m.tenant_id=d.tenant_id AND m.kommo_lead_id=d.kommo_lead_id AND m.channel NOT LIKE 'connect:%%'
        ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 1) lm ON TRUE
'''
    sql='''WITH routes AS (
      SELECT 'connect'::text AS provider,c.connect_channel_id::text AS channel_id,c.conversation_id,c.lead_id,
        COALESCE(meaningful_name.dialog_name,NULLIF(c.manual_display_name,''),NULLIF(CASE WHEN c.conversation_kind='group' THEN c.display_name ELSE c.contact_name END,''),NULLIF(c.display_name,''),NULLIF(names.customer_name,''),NULLIF(c.phone,''),c.conversation_id) AS customer_name,c.manual_display_name,c.phone,c.conversation_kind,
        '''+manual_phone+''' AS manual_display_phone,
        CASE WHEN (c.avatar_checked_at IS NULL AND c.avatar_status IN ('unavailable','loading'))
          OR c.avatar_checked_at<now()-interval '7 days'
          OR (c.avatar_status='loading' AND c.avatar_checked_at<now()-interval '3 minutes') THEN '' ELSE c.avatar_status END AS avatar_status,
        ch.name AS channel_name,ch.active AS channel_active,'connect:'||c.connect_channel_id::text AS history_channel,
        cs.agent_id AS agent_id,a.name AS agent_name,a.enabled AS agent_enabled,a.mode AS agent_mode,a.scopes AS agent_scopes,a.schedule AS agent_schedule,
        COALESCE(gc.state,cs.state,'active') AS state,COALESCE(gc.reason,cs.reason,'') AS reason,COALESCE(gc.updated_at,cs.updated_at) AS updated_at,
        cs.tenant_id IS NOT NULL AS tracked,s.enabled AS company_enabled,s.emergency_stop AS company_emergency_stop,s.auto_send_enabled AS company_auto_send_enabled,
        t.status AS tenant_status,j.state AS latest_job_state,COALESCE(j.error_code,'') AS latest_job_error,
        COALESCE(lm.last_message_at,c.last_message_at,c.created_at) AS last_message_at,
        lm.last_message_id,lm.last_message_direction,lm.last_message_type,lm.last_message_text,
        ticket.assigned_to,tm.display_name AS assignee_name,
        NOT ai_legacy_blocked(c.tenant_id,c.connect_channel_id::text,c.conversation_id) AS unambiguous,
        EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected') AS connected,
        h.source AS handoff_source,h.reason AS handoff_reason,h.created_at AS handoff_at,h.event_key AS handoff_event_id,h.assigned_name AS handoff_assignee_name,
        NULL::text AS pipeline_id,NULL::text AS stage_id
      FROM saas_connect_conversations c
      '''+phone_join+'''
      JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      JOIN saas_tenants t ON t.id=c.tenant_id
      LEFT JOIN saas_ai_group_coordination gc ON gc.tenant_id=c.tenant_id AND gc.provider='connect' AND gc.group_jid=c.conversation_id
      LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=c.tenant_id AND cs.provider='connect' AND cs.conversation_id=c.conversation_id AND cs.channel_id=c.connect_channel_id::text
      LEFT JOIN LATERAL(SELECT j0.* FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=c.tenant_id AND j0.provider='connect'
        AND j0.channel_id=c.connect_channel_id::text AND j0.conversation_id=c.conversation_id ORDER BY j0.updated_at DESC LIMIT 1) j ON TRUE
      LEFT JOIN saas_ai_agents a ON a.tenant_id=c.tenant_id AND a.id=cs.agent_id
      LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id
'''+connect_latest+'''      LEFT JOIN LATERAL(SELECT st.* FROM saas_support_tickets st WHERE ('''+ticket_filter+''') AND st.tenant_id=c.tenant_id
        AND st.channel_id=c.connect_channel_id AND st.status<>'resolved'
        AND (st.conversation_id=c.conversation_id OR EXISTS(SELECT 1 FROM saas_connect_dialog_links dl
          WHERE dl.tenant_id=c.tenant_id AND dl.channel_id=c.connect_channel_id
            AND dl.canonical_id=c.conversation_id AND dl.source_id=st.conversation_id AND dl.active))
        ORDER BY st.created_at DESC,st.id LIMIT 1) ticket ON TRUE
      LEFT JOIN saas_tenant_members tm ON tm.tenant_id=c.tenant_id AND tm.telegram_id=ticket.assigned_to
      LEFT JOIN LATERAL(SELECT CASE WHEN he.event_key LIKE 'manual-handoff:%%' OR he.event_key LIKE 'group-handoff:%%' THEN 'manual' ELSE 'ai_handoff' END AS source,ht.reason,ht.created_at,he.event_key,hm.display_name AS assigned_name
        FROM saas_support_tickets ht JOIN saas_support_events he ON he.tenant_id=ht.tenant_id AND he.ticket_id=ht.id
        LEFT JOIN saas_tenant_members hm ON hm.tenant_id=ht.tenant_id AND hm.telegram_id=ht.assigned_to
        WHERE ('''+handoff_filter+''') AND ht.tenant_id=c.tenant_id AND ht.channel_id=c.connect_channel_id AND ht.conversation_id=c.conversation_id
          AND (he.event_key LIKE 'handoff:%%' OR he.event_key LIKE 'manual-handoff:%%' OR he.event_key LIKE 'group-handoff:%%')
        ORDER BY ht.created_at DESC,he.event_key DESC LIMIT 1) h ON TRUE
      LEFT JOIN LATERAL(SELECT clean.label AS customer_name
        FROM saas_connect_dialog_links dl JOIN saas_connect_conversations sc
          ON sc.tenant_id=dl.tenant_id AND sc.connect_channel_id=dl.channel_id AND sc.conversation_id=dl.source_id
          AND sc.lead_id=dl.source_lead
        CROSS JOIN LATERAL unnest(ARRAY[sc.manual_display_name,sc.contact_name,sc.display_name])
          WITH ORDINALITY AS candidate(label,priority)
        CROSS JOIN LATERAL(SELECT regexp_replace(candidate.label,'^[[:space:]]+|[[:space:]]+$','','g') AS label) clean
        WHERE dl.tenant_id=c.tenant_id AND dl.channel_id=c.connect_channel_id AND dl.canonical_id=c.conversation_id
          AND dl.canonical_lead=c.lead_id AND dl.active AND '''+canonical_direct+''' AND '''+direct_route.format(route='sc')+'''
          AND '''+usable_label.format(other_ids=",sc.conversation_id,split_part(sc.conversation_id,'@',1)")+'''
        ORDER BY sc.created_at,sc.lead_id,candidate.priority LIMIT 1) names ON TRUE
'''+contact_join+'''      LEFT JOIN LATERAL(SELECT clean.label AS dialog_name
        FROM unnest(ARRAY[c.manual_display_name,c.contact_name,c.display_name,'''+contact_name+''',names.customer_name])
          WITH ORDINALITY AS candidate(label,priority)
        CROSS JOIN LATERAL(SELECT regexp_replace(candidate.label,'^[[:space:]]+|[[:space:]]+$','','g') AS label) clean
        WHERE '''+canonical_direct+''' AND '''+usable_label.format(other_ids='')+'''
        ORDER BY candidate.priority LIMIT 1
      ) meaningful_name ON TRUE
      WHERE c.tenant_id=%s::uuid AND NOT EXISTS(SELECT 1 FROM saas_connect_dialog_links dl
        WHERE dl.tenant_id=c.tenant_id AND dl.channel_id=c.connect_channel_id AND dl.source_id=c.conversation_id AND dl.active)
      '''+removal_filter+'''
      UNION ALL
      SELECT 'kommo',COALESCE(j.channel_id,lm.channel,''),COALESCE(j.conversation_id,''),d.kommo_lead_id,
        COALESCE(NULLIF(d.contact_name,''),NULLIF(d.name,''),d.kommo_lead_id::text),''::text AS manual_display_name,COALESCE(d.phone,''),''::text AS conversation_kind,''::text AS manual_display_phone,''::text AS avatar_status,
        COALESCE(NULLIF(lm.channel,''),'Kommo'),TRUE,COALESCE(lm.channel,''),j.agent_id,a.name,a.enabled,a.mode,a.scopes,a.schedule,
        COALESCE(cs.state,'active'),COALESCE(cs.reason,''),cs.updated_at,cs.tenant_id IS NOT NULL,
        s.enabled,s.emergency_stop,s.auto_send_enabled,t.status,j.state,COALESCE(j.error_code,''),lm.last_message_at,
        lm.last_message_id,lm.last_message_direction,lm.last_message_type,lm.last_message_text,
        cs.assigned_to,tm.display_name,TRUE,
        EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=d.tenant_id AND i.provider='kommo' AND i.status='connected'),
        NULL::text,NULL::text,NULL::timestamptz,NULL::text,NULL::text,d.pipeline_id::text,d.status_id::text
      FROM saas_crm_deals d JOIN saas_tenants t ON t.id=d.tenant_id
'''+kommo_latest+'''      LEFT JOIN LATERAL(SELECT j0.* FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=d.tenant_id AND j0.provider='kommo'
        AND j0.lead_id=d.kommo_lead_id ORDER BY j0.updated_at DESC LIMIT 1) j ON TRUE
      LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=d.tenant_id AND cs.provider='kommo' AND cs.conversation_id=j.conversation_id AND cs.channel_id=j.scope_channel_id
      LEFT JOIN saas_ai_agents a ON a.tenant_id=d.tenant_id AND a.id=j.agent_id
      LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=d.tenant_id
      LEFT JOIN saas_tenant_members tm ON tm.tenant_id=d.tenant_id AND tm.telegram_id=cs.assigned_to
      WHERE d.tenant_id=%s::uuid AND d.deleted_at IS NULL AND '''+kommo_scope+'''
        AND NOT EXISTS(SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=d.tenant_id AND c.lead_id=d.kommo_lead_id)
    ), visible AS (SELECT routes.*,CASE WHEN state IN ('human','paused') THEN state
      WHEN agent_id IS NULL THEN 'unmanaged'
      WHEN latest_job_state IN ('failed','unknown') AND latest_job_error<>'' THEN 'error'
      WHEN NOT COALESCE(company_enabled,FALSE) OR COALESCE(company_emergency_stop,FALSE) OR NOT COALESCE(agent_enabled,FALSE)
        OR (agent_mode='automatic' AND NOT COALESCE(company_auto_send_enabled,FALSE))
        OR NOT %s OR COALESCE(tenant_status,'')<>'active' OR NOT COALESCE(channel_active,FALSE)
        OR NOT COALESCE(connected,FALSE) THEN 'blocked'
      ELSE 'active' END AS display_state FROM routes WHERE '''+dialog_filter+''') '''
    # A native source does not need a reserve Connect credential to enable the
    # composer. This is a display observation only; each send still resolves
    # and fences the actual source through connect_route/bind.
    from tenant_whatsapp_native_rollout import modes as native_modes
    native_sources=[str(uuid.UUID(source)) for source,row in native_modes(cur,str(policy.profile['tenant_id'])).items() if row['enabled']]
    if native_sources:
        legacy="EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected')"
        sql=sql.replace(legacy,"("+legacy+" OR c.connect_channel_id=ANY(ARRAY["+','.join("'"+source+"'" for source in native_sources)+"]::uuid[]))")
    if include_read_cursors:
        sql=sql.replace('lm.last_message_id,lm.last_message_direction,lm.last_message_type,lm.last_message_text,',
                        'lm.last_message_id,lm.last_message_direction,lm.last_message_type,lm.last_message_text,lrc.read_through_message_id,')
        sql=sql.replace(') lm ON TRUE\n      LEFT JOIN LATERAL(SELECT st.*',
          ") lm ON TRUE\n      LEFT JOIN LATERAL(SELECT m.id::text AS read_through_message_id FROM saas_crm_messages m\n"
          "        WHERE m.tenant_id=c.tenant_id AND m.kommo_lead_id=ANY(ARRAY(SELECT c.lead_id UNION ALL\n"
          "          SELECT dl.source_lead FROM saas_connect_dialog_links dl WHERE dl.tenant_id=c.tenant_id\n"
          "            AND dl.channel_id=c.connect_channel_id AND dl.canonical_id=c.conversation_id AND dl.active)) AND m.channel='connect:'||c.connect_channel_id::text\n"
          "          AND (m.direction IN ('incoming','outgoing') OR (m.direction='system' AND m.message_type='call_missed'))\n"
          "        ORDER BY m.created_at DESC,m.id DESC LIMIT 1) lrc ON TRUE\n      LEFT JOIN LATERAL(SELECT st.*")
        sql=sql.replace(') lm ON TRUE\n      LEFT JOIN LATERAL(SELECT j0.*',
          ") lm ON TRUE\n      LEFT JOIN LATERAL(SELECT m.id::text AS read_through_message_id FROM saas_crm_messages m\n"
          "        WHERE m.tenant_id=d.tenant_id AND m.kommo_lead_id=d.kommo_lead_id AND m.channel NOT LIKE 'connect:%%'\n"
          "          AND m.direction IN ('incoming','outgoing')\n"
          "        ORDER BY m.created_at DESC,m.id DESC LIMIT 1) lrc ON TRUE\n      LEFT JOIN LATERAL(SELECT j0.*")
    return sql,params


def search_overview_matches(cur,tenant,policy,channel,conversation,lead_id,search):
    """Prove a cross-owner read request against an exact route and user search."""
    query=str(search or '').strip()[:200].casefold()
    if not query:return False
    sql,scopes=projection(cur,policy,allow_closed_archive=True,search_overview=True)
    cur.execute(sql+'''SELECT customer_name,phone,conversation_id FROM visible
      WHERE provider='connect' AND channel_id=%s AND conversation_id=%s AND lead_id=%s''',
      (tenant,tenant,*scopes,runtime_flag('AI_AGENTS_ENABLED'),str(channel),str(conversation),int(lead_id)))
    row=cur.fetchone()
    return bool(row and any(query in str(row.get(key) or '').casefold()
                             for key in ('customer_name','phone','conversation_id')))


def _agent_scope_available(item):
    if not item.get('agent_id') or item.get('agent_enabled') is not True:
        return False
    scopes=item.get('agent_scopes') or []
    if item.get('provider')=='connect':
        from tenant_ai_agent_connect import scope_matches
        return scope_matches(scopes,str(item.get('channel_id') or ''),is_group=item.get('conversation_kind')=='group')
    from tenant_ai_agent_queue import _scope_matches
    return _scope_matches(scopes,item.get('pipeline_id'),item.get('stage_id'),item.get('channel_id'))


_HANDOFF_REASONS={'human_handoff','operator_handoff','trigger_reply_sent_then_handoff','customer_requested_manager','connect_human_takeover','verified_employee_reply'}


def _attach_verified_phones(cur,tenant,dialogs,*,check_conflicts=False):
    """Batch only authorized, exact direct routes; LID digits are not phones."""
    from tenant_connect_direct_identity import direct_identity, phone_jid
    from tenant_whatsapp_contact_refresh import attach_display_phones
    pending={(str(d['channel_id']),d['conversation_id']):d for d in dialogs
             if d.get('provider','connect')=='connect'
             and d.get('customer_kind')!='group' and d.get('conversation_kind')!='group'
             and direct_identity(d.get('conversation_id'))}
    display_candidates=list(pending.values())
    if check_conflicts:
        lids=[{'channel':ch,'conversation':conv} for ch,conv in pending if conv.endswith('@lid')]
        if lids:
            cur.execute("SELECT to_regclass('public.saas_connect_identity_conflicts') AS relation")
            if (cur.fetchone() or {}).get('relation'):
                cur.execute('''SELECT r.channel::text AS channel,r.conversation
                  FROM jsonb_to_recordset(%s::jsonb) AS r(channel uuid,conversation text)
                  JOIN saas_connect_identity_conflicts i ON i.tenant_id=%s::uuid
                   AND i.connect_channel_id=r.channel AND i.conversation_id=r.conversation''',
                  (json.dumps(lids),tenant))
                for row in cur.fetchall():
                    dialog=pending.pop((row['channel'],row['conversation']),None)
                    if dialog is not None:
                        dialog.update(phone='',contact_identity_conflict=True)
    pending={key:d for key,d in pending.items() if not d.get('phone')}
    if not pending:
        attach_display_phones(cur, tenant, display_candidates)
        return
    phones={key:{phone_jid(key[1])}-{''} for key in pending}
    cur.execute("SELECT to_regclass('public.saas_connect_direct_identities') AS relation")
    if (cur.fetchone() or {}).get('relation'):
        cur.execute('''SELECT i.connect_channel_id::text AS channel,i.canonical_conversation_id AS conversation,i.identity_key
          FROM jsonb_to_recordset(%s::jsonb) AS r(channel uuid,conversation text)
          JOIN saas_connect_direct_identities i ON i.tenant_id=%s::uuid
           AND i.connect_channel_id=r.channel AND i.canonical_conversation_id=r.conversation''',
          (json.dumps([{'channel':ch,'conversation':conv} for ch,conv in pending]),tenant))
        for row in cur.fetchall():
            phone=phone_jid(row['identity_key'])
            if phone:phones[(row['channel'],row['conversation'])].add(phone)
    for key,candidates in phones.items():
        if len(candidates)==1:
            match=re.fullmatch(r'([1-9][0-9]{6,14})@s\.whatsapp\.net',next(iter(candidates)))
            if match:pending[key]['phone']='+'+match[1]
        elif check_conflicts and len(candidates)>1 and key[1].endswith('@lid'):
            pending[key]['contact_identity_conflict']=True
    attach_display_phones(cur, tenant, display_candidates)


def _attach_verified_phone(cur,tenant,dialog):
    from tenant_whatsapp_contact_refresh import enabled_for
    _attach_verified_phones(cur, tenant, [dialog], check_conflicts=enabled_for(tenant, dialog.get('channel_id')))


def decorate(row):
    item=dict(row)
    unmanaged=item['display_state']=='unmanaged'
    execution_error=(item.get('state') == 'active' and item.get('latest_job_state') in {'failed','unknown'}
                     and bool(item.get('latest_job_error')))
    item=_decorate_conversation_state(item)
    item['agent_available']=_agent_scope_available(item)
    item['handoff_active']=item.get('state')=='human' and (bool(item.get('handoff_event_id')) or str(item.get('reason') or '') in _HANDOFF_REASONS)
    if item['handoff_active']:
        item['handoff_source']=item.get('handoff_source') or ('manual' if str(item.get('handoff_event_id') or '').startswith(('manual-handoff:','group-handoff:')) else 'ai_handoff')
        item['handoff_at']=item.get('handoff_at') or item.get('updated_at')
        item['handoff_event_id']=item.get('handoff_event_id') or ''
    if unmanaged and item['state']=='active':
        item['display_state']='unmanaged';item['display_reason']='agent_not_found'
    elif execution_error:
        item['display_state']='error';item['display_reason']=str(item.get('latest_job_error') or '')
    if item.get('provider') == 'connect' and item.get('conversation_kind') == 'group':
        item['customer_name'] = _safe_group_name(item)
        item['phone'] = ''
    item['customer_kind'] = ('group' if item.get('conversation_kind') == 'group' else
                             'contact' if item.get('provider') == 'connect' else 'contact')
    if item.get('provider') == 'connect' and item.get('unambiguous') is False:
        item.update(display_state='blocked', display_reason='legacy_channel_ambiguous', resume_allowed=False, resume_block_reason='legacy_channel_ambiguous')
    schedule_open=True
    if item.get('agent_mode')=='automatic':
        try:
            from tenant_ai_agent_schedule import is_open
            schedule_open=is_open(item.get('agent_schedule') or {})
        except (ValueError,TypeError,KeyError):
            schedule_open=False
    operational=bool(item.get('agent_available') and item.get('channel_active') and item.get('unambiguous') and item.get('conversation_id') and item.get('connected')
        and item.get('company_enabled') and not item.get('company_emergency_stop')
        and item.get('tenant_status')=='active' and runtime_flag('AI_AGENTS_ENABLED')
        and (item.get('agent_mode')!='automatic' or (item.get('company_auto_send_enabled') is True and runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'))))
    if item.get('display_state')=='error':
        item['agent_status']='error'
    # Manual is an assigned operating mode, not an error state. Keep its blue
    # identity even when a stop rule, handoff, schedule, or admission gate
    # currently prevents automatic processing; those details stay in reason.
    elif item.get('agent_id') and item.get('agent_enabled') is True and item.get('agent_mode')=='manual':
        item['agent_status']='manual'
    elif item.get('agent_id') and item.get('agent_mode')=='automatic' and operational and schedule_open and item.get('state')=='active' and item.get('display_state')=='active':
        item['agent_status']='automatic'
    elif item.get('agent_id'):
        item['agent_status']='attention'
    elif item.get('display_reason')=='legacy_channel_ambiguous':
        item['agent_status']='attention'
    else:
        item['agent_status']='no_agent'
    item['agent_status_reason']=''
    if item['agent_status']=='attention':
        if not item.get('channel_active'): item['agent_status_reason']='channel_inactive'
        elif not item.get('connected'): item['agent_status_reason']='integration_disconnected'
        elif item.get('tenant_status')!='active': item['agent_status_reason']='tenant_not_active'
        elif item.get('company_emergency_stop'): item['agent_status_reason']='emergency_stop'
        elif not item.get('company_enabled'): item['agent_status_reason']='company_ai_disabled'
        elif item.get('agent_mode')=='automatic' and (item.get('company_auto_send_enabled') is not True or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')): item['agent_status_reason']='auto_send_disabled'
        elif item.get('agent_enabled') is not True: item['agent_status_reason']='agent_disabled'
        elif item.get('state') in {'human','paused'}: item['agent_status_reason']=item.get('reason') or ('human_handoff' if item['handoff_active'] else 'manager_pause')
        elif item.get('agent_mode')=='automatic' and not schedule_open: item['agent_status_reason']='outside_schedule'
        elif not item.get('controls_allowed'): item['agent_status_reason']=item.get('control_block_reason') or 'connect_admission_blocked'
        else: item['agent_status_reason']=item.get('display_reason') or 'connect_admission_blocked'
    item['controls_allowed']=bool(item.get('channel_active') and item.get('unambiguous') and item.get('conversation_id'))
    item['control_block_reason']='channel_inactive' if not item.get('channel_active') else 'legacy_channel_ambiguous' if not item.get('unambiguous') else 'untracked_dialog' if not item.get('conversation_id') else ''
    item['can_send']=bool(item.get('channel_active') and item.get('connected') and item.get('tenant_status')=='active')
    item['send_block_reason']='integration_disconnected' if not item.get('connected') else 'channel_inactive' if not item.get('channel_active') else 'tenant_not_active' if item.get('tenant_status')!='active' else ''
    if item.get('agent_available') and item.get('display_state') == 'active':
        item['ai_availability'] = 'available'
    elif item.get('display_state') == 'error':
        item['ai_availability'] = 'error'
    elif item.get('display_state') == 'unmanaged':
        item['ai_availability'] = 'unmanaged'
    elif item.get('state') in {'paused','human'}:
        item['ai_availability'] = 'stopped'
    else:
        item['ai_availability'] = 'blocked'
    item['available_actions'] = (['pause','handoff'] if item.get('agent_available') and item.get('agent_mode')=='automatic' and item.get('state')=='active' and item.get('ai_availability')=='available' and item.get('controls_allowed')
                                 else ['resume'] if item.get('agent_available') and item.get('state') in {'paused','human'} and item.get('resume_allowed') and item.get('controls_allowed')
                                 else ['handoff'] if item.get('agent_available') and item.get('ai_availability') in {'available','stopped'} and not item.get('handoff_active') and item.get('controls_allowed')
                                 else [])
    item['state_changed_at'] = item.get('updated_at')
    item.pop('agent_scopes',None)
    item.pop('agent_schedule',None)
    return public(item)


def apply_group_view(cur,tenant,dialog,*,snapshot=None):
    from tenant_ai_group_coordination import view
    from tenant_ai_agent_access import tenant_status_allows_ai
    g=snapshot.group_view(dialog['conversation_id']) if snapshot is not None else view(cur,tenant,dialog['conversation_id']);dialog['group_coordination']=g
    if snapshot is not None:
        primary_agent=snapshot.agents.get(str(g['primary_agent_id']),{})
    else:
        cur.execute('SELECT name,mode,enabled,scopes,schedule FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,g['primary_agent_id']))
        primary_agent=dict(cur.fetchone() or {})
    name=primary_agent.get('name');g['primary_agent_name']=name
    dialog.update(agent_mode=primary_agent.get('mode'),agent_enabled=primary_agent.get('enabled'),agent_scopes=primary_agent.get('scopes'))
    dialog['agent_available']=_agent_scope_available(dialog)
    dialog['handoff_active']=g['state']=='human' and (bool(g.get('group_event_key')) or str(g.get('reason') or '') in _HANDOFF_REASONS)
    if dialog['handoff_active']:
        dialog['handoff_source']='manual' if str(g.get('group_event_key') or '').startswith(('manual-handoff:','group-handoff:')) else 'ai_handoff'
        dialog['handoff_at']=g.get('updated_at')
        dialog['handoff_event_id']=g.get('group_event_key') or ''
    dialog.pop('agent_scopes',None)
    if snapshot is not None:
        assignee=snapshot.groups.get(dialog['conversation_id'],{}).get('assignee_name')
    else:
        cur.execute('SELECT display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s',(tenant,g['assigned_to']))
        assignee=(cur.fetchone() or {}).get('display_name')
    usable=bool(g['primary_agent_id'] and dialog.get('controls_allowed') and dialog.get('company_enabled') and not dialog.get('company_emergency_stop') and dialog.get('connected') and runtime_flag('AI_AGENTS_ENABLED') and tenant_status_allows_ai(dialog.get('tenant_status'),'connect') and (primary_agent.get('mode')!='automatic' or (dialog.get('company_auto_send_enabled') is True and runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'))))
    state=g['state'];display=state if state!='active' or usable else 'blocked'
    dialog.update(agent_id=g['primary_agent_id'],agent_name=name,assignee_name=assignee,
                  state=state,display_state=display,display_reason=g['reason'],
                  ai_availability='available' if display=='active' else 'stopped' if display in ('human','paused') else 'blocked',
                  resume_allowed=usable and state!='active',
                  available_actions=['pause','handoff'] if state=='active' and dialog.get('agent_mode')=='automatic' and usable and dialog.get('controls_allowed') else ['resume'] if state in ('human','paused') and usable and dialog.get('controls_allowed') else ['handoff'] if usable and dialog.get('controls_allowed') and state!='human' else [])
    if not g.get('primary_agent_id') and g.get('assignments'):
        dialog['agent_status']='attention'
        dialog['agent_status_reason']='agent_selection_required'
    elif not g.get('primary_agent_id'):
        dialog['agent_status']='needs_assignment'
        dialog['agent_status_reason']='assignment_required'
    else:
        schedule_open=True
        if primary_agent.get('mode')=='automatic':
            try:
                from tenant_ai_agent_schedule import is_open
                schedule_open=is_open(primary_agent.get('schedule') or {})
            except (ValueError,TypeError,KeyError):schedule_open=False
        dialog['agent_status']='manual' if primary_agent.get('mode')=='manual' and primary_agent.get('enabled') else 'automatic' if primary_agent.get('mode')=='automatic' and usable and schedule_open and state=='active' else 'attention'
        dialog['agent_status_reason']='' if dialog['agent_status'] in {'manual','automatic'} else (g.get('reason') or 'connect_admission_blocked')
        if dialog['agent_status']=='attention' and primary_agent.get('mode')=='automatic' and not schedule_open:
            dialog['agent_status_reason']='outside_schedule'
        elif dialog['agent_status']=='attention' and primary_agent.get('mode')=='automatic' and (dialog.get('company_auto_send_enabled') is not True or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')):
            dialog['agent_status_reason']='auto_send_disabled'
    return dialog


def _unread_counts(cur,tenant,telegram_id,rows):
    """Initialize at the latest ordinary message, then count inbound messages and Connect missed calls."""
    routes=[]
    for row in rows:
        try:
            route=(str(row.get('provider') or ''),str(row.get('channel_id') or ''),
                   str(row.get('conversation_id') or ''),int(row.get('lead_id')))
            if route[0] not in {'connect','kommo'}:continue
            message_id=row.get('last_message_id')
            if message_id:
                try:message_id=str(uuid.UUID(str(message_id)))
                except (ValueError,TypeError,AttributeError):message_id=None
            routes.append(route)
        except (TypeError,ValueError):
            continue
    if not routes:return 0
    routes=list({tuple(route[:4]):route for route in routes}.values())
    equivalences={}
    cur.execute('''SELECT channel_id::text,canonical_id,canonical_lead,source_id,source_lead
      FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid AND active
        AND channel_id::text=ANY(%s::text[])''',(tenant,list({r[1] for r in routes if r[0]=='connect'})))
    original=set(routes)
    for link in cur.fetchall():
        key=('connect',link['channel_id'],link['canonical_id'],int(link['canonical_lead']))
        if key not in original: continue
        source=('connect',link['channel_id'],link['source_id'],int(link['source_lead']))
        equivalences.setdefault(key,[key]).append(source)
        if source not in routes: routes.append(source)
    cur.execute('''WITH routes(provider,channel_id,conversation_id,lead_id) AS (
        SELECT * FROM unnest(%s::text[],%s::text[],%s::text[],%s::bigint[]))
      INSERT INTO saas_operator_dialog_reads(tenant_id,telegram_id,provider,channel_id,conversation_id,lead_id,last_read_at,last_read_message_id)
      SELECT %s::uuid,%s,r.provider,r.channel_id,r.conversation_id,r.lead_id,
          COALESCE(read_base.created_at,'-infinity'::timestamptz),read_base.id FROM routes r
      LEFT JOIN LATERAL(SELECT m.created_at,m.id FROM saas_crm_messages m
        WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=r.lead_id
          AND m.channel=CASE WHEN r.provider='connect' THEN 'connect:'||r.channel_id ELSE r.channel_id END
          AND m.direction IN ('incoming','outgoing')
        ORDER BY m.created_at DESC,m.id DESC LIMIT 1) read_base ON TRUE
      ON CONFLICT(tenant_id,telegram_id,provider,channel_id,conversation_id,lead_id) DO NOTHING''',
      ([r[0] for r in routes],[r[1] for r in routes],[r[2] for r in routes],[r[3] for r in routes],tenant,telegram_id,tenant))
    cur.execute('''WITH routes(provider,channel_id,conversation_id,lead_id) AS (
        SELECT * FROM unnest(%s::text[],%s::text[],%s::text[],%s::bigint[])), counts AS (
        SELECT r.provider,r.channel_id,r.conversation_id,r.lead_id,count(m.id)::int AS unread_count
        FROM routes r JOIN saas_operator_dialog_reads rc ON rc.tenant_id=%s::uuid AND rc.telegram_id=%s
          AND rc.provider=r.provider AND rc.channel_id=r.channel_id AND rc.conversation_id=r.conversation_id AND rc.lead_id=r.lead_id
        LEFT JOIN saas_crm_messages m ON m.tenant_id=%s::uuid AND m.kommo_lead_id=r.lead_id
          AND m.channel=CASE WHEN r.provider='connect' THEN 'connect:'||r.channel_id ELSE r.channel_id END
          AND (m.direction='incoming' OR (r.provider='connect' AND m.direction='system' AND m.message_type='call_missed'))
          AND (m.created_at,m.id)>(rc.last_read_at,COALESCE(rc.last_read_message_id,'00000000-0000-0000-0000-000000000000'::uuid))
        GROUP BY r.provider,r.channel_id,r.conversation_id,r.lead_id)
      SELECT provider,channel_id,conversation_id,lead_id,unread_count FROM counts''',
      ([r[0] for r in routes],[r[1] for r in routes],[r[2] for r in routes],[r[3] for r in routes],tenant,telegram_id,tenant))
    counts={}
    for item in cur.fetchall():
        key=(str(item['provider']),str(item['channel_id']),str(item['conversation_id']),int(item['lead_id']))
        counts[key]=int(item['unread_count'] or 0)
    for row in rows:
        try:key=(str(row.get('provider') or ''),str(row.get('channel_id') or ''),str(row.get('conversation_id') or ''),int(row.get('lead_id')))
        except (TypeError,ValueError):continue
        row['unread_count']=sum(counts.get(k,0) for k in equivalences.get(key,[key]))
    return sum(counts.values())



def _scoped_ai_documents(policy):
    return policy.profile.get('role') != 'owner' and policy.has_scoped_access('ai_agents')


def _assignment_display(cur, tenant, policy, dialogs):
    """Read-only name/fact, not an AI document or an operation grant.

    The caller already selected visible routes. Recheck source membership and
    tenant before exposing the minimal display value. Group primary metadata
    belongs to its sending source, not every source present in that group.
    """
    # Display-only authorization follows the explicit dialog AI flag, not the
    # legacy CRM gate used by AI operations. Never use this as an action grant.
    if (str(policy.profile.get('tenant_id')) != str(tenant)
            or policy.profile.get('active') is not True
            or (policy.profile.get('role') != 'owner'
                and policy.member_settings.get('ai_enabled') is not True)):
        return
    from tenant_dialog_access import allowed_channels
    from tenant_ai_group_coordination import group_jid
    allowed = allowed_channels(cur, tenant, policy)
    permitted = None if allowed is None else set(allowed)
    visible = [d for d in dialogs if d.get('provider') == 'connect'
               and (permitted is None or str(d.get('channel_id')) in permitted)]
    groups = [d['conversation_id'] for d in visible if group_jid(d['conversation_id'])]
    group_names = {}
    if groups:
        cur.execute("""SELECT g.group_jid,a.name,a.enabled,a.role,a.scopes,
                    g.assignments->0->>'channel_id' AS sending_channel_id
          FROM saas_ai_group_coordination g
          JOIN saas_ai_agents a ON a.tenant_id=g.tenant_id
            AND a.id::text=g.assignments->0->>'agent_id'
          JOIN saas_connect_conversations c ON c.tenant_id=g.tenant_id
            AND c.conversation_id=g.group_jid
            AND c.connect_channel_id::text=g.assignments->0->>'channel_id'
          JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id
            AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
          WHERE g.tenant_id=%s::uuid AND g.provider='connect' AND g.group_jid=ANY(%s::text[])
            AND CASE WHEN jsonb_typeof(g.assignments)='array'
                     THEN jsonb_array_length(g.assignments) ELSE 0 END=1
            AND (%s OR c.connect_channel_id::text=ANY(%s::text[]))
            AND NOT ai_legacy_blocked(g.tenant_id,c.connect_channel_id::text,g.group_jid,NULL)
            AND NOT EXISTS(SELECT 1 FROM saas_connect_dialog_links l
              WHERE l.tenant_id=g.tenant_id AND l.channel_id=c.connect_channel_id
                AND l.source_id=g.group_jid AND l.active)""",
                    (tenant, groups, permitted is None, sorted(permitted or [])))
        from tenant_ai_agent_connect import scope_matches
        group_names = {r['group_jid']: r.get('name') for r in cur.fetchall()
                       if r.get('enabled') and r.get('role') == 'primary'
                       and scope_matches(r.get('scopes'), r['sending_channel_id'], is_group=True)}
    for dialog in visible:
        if group_jid(dialog['conversation_id']):
            # Absence may mean an inaccessible sending source. Do not label it
            # unassigned or disclose the hidden source's existence/name.
            if dialog['conversation_id'] not in group_names:
                continue
            name = group_names[dialog['conversation_id']]
        else:
            name = dialog.get('agent_name')
        dialog['assignment_display'] = {'assigned': bool(name), 'name': name or ''}


def _hide_shared_ai(dialog):
    for key in list(dialog):
        # Status refresh uses the authorized group-work revision.
        if key == 'group_work_revision':
            continue
        if key.startswith(('agent_', 'company_', 'latest_job_', 'group_')) or key in {
                'handoff_event_id', 'reason', 'display_reason', 'resume_block_reason'}:
            dialog.pop(key, None)
    dialog.update(controls_allowed=False, control_block_reason='ai_management_restricted',
                  available_actions=[], resume_allowed=False, ai_availability='restricted',
                  agent_status='restricted', agent_status_reason='ai_management_restricted')
    return dialog

def _fold_group_routes(rows, *, prefer_actionable=False, group_unread=None):
    """Fold authorized exact group routes after filtering, before pagination.

    Each selectable source keeps its own lifecycle and sending route. Never
    use a title, phone alias or message similarity as a group identity.
    """
    from tenant_ai_group_coordination import group_jid
    result, groups = [], {}
    for row in rows:
        conversation = row.get('conversation_id', '')
        if row.get('provider') != 'connect' or not group_jid(conversation):
            result.append(row)
            continue
        key = conversation  # rows already belong to one authorized tenant
        if key not in groups:
            representative = dict(row)
            representative['group_sources'] = [dict(row)]
            groups[key] = representative
            result.append(representative)
        else:
            representative = groups[key]
            representative['group_sources'].append(dict(row))
            work = row.get('lifecycle',{}).get('group_work') or {}
            selected_work = representative.get('lifecycle',{}).get('group_work') or {}
            prefer_work = not selected_work.get('role') and (work.get('role') or work.get('left') and not selected_work.get('left'))
            if prefer_work or (prefer_actionable and representative.get('archive_read_only') and not row.get('archive_read_only')):
                sources=representative['group_sources']
                representative.clear()
                representative.update(row,group_sources=sources)
            representative['unread_count'] = max(
                int(representative.get('unread_count') or 0), int(row.get('unread_count') or 0))
    for row in groups.values():
        row['group_sources'].sort(key=lambda source: (str(source.get('channel_name') or ''), str(source['channel_id']), source['lead_id']))
        if group_unread is not None and row['conversation_id'] in group_unread:
            row['unread_count']=group_unread[row['conversation_id']]
    return result


def _attach_missed_call_attention(cur, tenant, cards):
    """Keep the latest Connect missed-call preview distinct from read cursors."""
    routes = {}
    for card in cards:
        for row in (card, *card.get('group_sources', ())):
            if (row.get('provider') != 'connect' or not row.get('lifecycle_enabled')
                    or row.get('archive_read_only') or row.get('search_overview_read_only')
                    or row.get('last_message_type') != 'call_missed'
                    or row.get('last_message_direction') != 'system'):
                continue
            row['missed_call_pending'] = row['lifecycle']['state'] != 'accepted'
            if row['missed_call_pending'] and row.get('last_message_at'):
                key = (row['channel_id'], row['conversation_id'], row['lead_id'])
                routes.setdefault(key, []).append(row)
    if not routes:
        return
    # Only for displayed, authorized exact routes. During a live accepted
    # episode the dialogue is already in work; a completed episode covers
    # calls at or before its close, even if the route has gone back to waiting.
    cur.execute("""SELECT r.channel::text AS channel_id,r.conversation,r.lead
      FROM jsonb_to_recordset(%s::jsonb) AS r(channel uuid,conversation text,lead bigint,call_at timestamptz)
      JOIN LATERAL (
        SELECT e.closed_at FROM saas_dialog_episodes e
        WHERE e.tenant_id=%s::uuid AND e.channel_id=r.channel
          AND e.conversation_id=r.conversation AND e.lead_id=r.lead
          AND e.closed_at IS NOT NULL
        ORDER BY e.accepted_at DESC LIMIT 1
      ) latest ON latest.closed_at >= r.call_at""",
      (json.dumps([dict(channel=channel, conversation=conversation, lead=lead,
                       call_at=rows[0]['last_message_at'])
                   for (channel, conversation, lead), rows in routes.items()]), tenant))
    for row in cur.fetchall():
        for target in routes[(row['channel_id'], row['conversation'], row['lead'])]:
            target['missed_call_pending'] = False


def _completion_inbound_snapshot(cur, tenant, rows, *, presence_only=False):
    """Read-only optimistic tokens for already-authorized physical Connect routes.

    One SQL round trip. Tokens match lifecycle insertion ordering; ordinary
    lists only test presence and stop at the first row without sorting history.
    No message content, read markers, episode creation or action is performed.
    """
    routes=sorted({(str(r['channel_id']),str(r['conversation_id']),int(r['lead_id']))
                   for r in rows if r.get('provider')=='connect'})
    if not routes:return {}
    ordering='' if presence_only else ' ORDER BY m.created_at DESC,m.id DESC'
    # Confirmed missed calls enter the work queue like a new inbound message,
    # but do not become text tokens for rhetorical/bulk completion actions.
    inbound="(m.direction='incoming' OR (m.direction='system' AND m.message_type='call_missed'))" if presence_only else "m.direction='incoming'"
    cur.execute("""WITH routes(channel_id,conversation_id,lead_id) AS (
      SELECT * FROM unnest(%s::text[],%s::text[],%s::bigint[]))
      SELECT r.channel_id,r.conversation_id,r.lead_id,m.id::text AS message_id
      FROM routes r LEFT JOIN LATERAL (
        SELECT id FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid
          AND m.channel='connect:'||r.channel_id AND m.kommo_lead_id=r.lead_id
          AND """+inbound+ordering+""" LIMIT 1
      ) m ON TRUE""",([r[0] for r in routes],[r[1] for r in routes],[r[2] for r in routes],tenant))
    return {(r['channel_id'],r['conversation_id'],int(r['lead_id'])):r['message_id'] for r in cur.fetchall()}


def colleague_options(profile):
    from tenant_operator_access import authorize_observer
    from tenant_beinsystems_operator_access import is_operator_workspace
    from tenant_dialog_lifecycle import enabled
    from tenant_dialog_access import allowed_channels
    with _connect() as conn, conn.cursor() as cur:
        tenant,actor,policy=access(cur,profile,'dialogs')
        if policy.profile.get('role')!='operator' or not is_operator_workspace(tenant) or not enabled(cur,tenant):
            return {'enabled':False,'operators':[]}
        own=allowed_channels(cur,tenant,policy)
        cur.execute("""SELECT telegram_id, display_name FROM saas_tenant_members
          WHERE tenant_id=%s::uuid AND active AND role='operator' AND telegram_id<>%s
          ORDER BY telegram_id""",(tenant,actor))
        people=cur.fetchall()
        result=[]
        for person in people:
            try:
                other=authorize_observer(cur,tenant,policy,int(person['telegram_id']))
            except TenantLinearError as error:
                if error.status!=404:
                    raise
                continue
            channels=allowed_channels(cur,tenant,other)
            if own==[] or channels==[] or (own is not None and channels is not None and not set(own).intersection(channels)):
                continue
            if person.get('display_name'):
                result.append({'id':str(person['telegram_id']),'name':person['display_name']})
        return {'enabled':True,'operators':sorted(result,key=lambda item:item['name'].casefold())}


def _colleague_listing(cur,tenant,policy,target,search,channel,lifecycle,offset):
    from tenant_operator_access import authorize_observer
    from tenant_dialog_access import sql_filter
    from tenant_dialog_lifecycle import enabled
    target_policy=authorize_observer(cur,tenant,policy,target)
    if not enabled(cur,tenant):
        raise TenantLinearError('Диалог недоступен.',404)
    sql,params=projection(cur,policy,allow_closed_archive=True,search_overview=True,hide_removed=True)
    target_scope=sql_filter(target_policy,"'connect'","v.channel_id")
    states={'all':['accepted','closed','dismissed'],'active':['accepted'],'completed':['closed','dismissed']}[lifecycle]
    query=str(search or '')[:200].strip().casefold()
    cur.execute(sql+"""
      , selected AS MATERIALIZED (
        SELECT v.provider,v.lead_id,v.channel_id,v.channel_name,v.conversation_id,
               v.customer_name,CASE WHEN v.conversation_kind='group' THEN 'group' ELSE 'contact' END AS customer_kind,CASE WHEN v.conversation_kind='group' THEN '' ELSE v.phone END AS phone,v.last_message_at,w.state
        FROM visible v JOIN saas_dialog_workflows w ON w.tenant_id=%s::uuid
             AND w.channel_id::text=v.channel_id AND w.conversation_id=v.conversation_id
        WHERE v.provider='connect' AND w.operator_id=%s AND w.state=ANY(%s::text[])
          AND """+target_scope+"""
          AND (%s='' OR STRPOS(LOWER(COALESCE(v.customer_name,'')||' '||COALESCE(v.phone,'')||' '||v.conversation_id),%s)>0)
      )
      SELECT (SELECT count(*) FROM selected WHERE %s='' OR 'connect:'||channel_id=%s) AS total,
             (SELECT COALESCE(jsonb_agg(x ORDER BY name),'[]'::jsonb)
                FROM (SELECT DISTINCT 'connect:'||channel_id AS id, channel_name AS name FROM selected) x) AS channels,
             (SELECT COALESCE(jsonb_agg(y ORDER BY y.last_message_at DESC NULLS LAST,y.lead_id),'[]'::jsonb)
                FROM (SELECT * FROM selected WHERE %s='' OR 'connect:'||channel_id=%s
                      ORDER BY last_message_at DESC NULLS LAST,lead_id LIMIT 51 OFFSET %s) y) AS rows
      """,(tenant,tenant,*params,runtime_flag('AI_AGENTS_ENABLED'),tenant,target,states,query,query,
           channel,channel,channel,channel,offset))
    snapshot=cur.fetchone()
    rows=snapshot['rows']
    cards=[{**r,'customer_name':_safe_group_name(r) if r['customer_kind']=='group' else r['customer_name'],
            'colleague_read_only':True,'archive_read_only':True,'unread_count':0,
            'can_send':False,'controls_allowed':False,'lifecycle_enabled':True,
            'lifecycle':{'state':r['state'],'operator_id':str(target)}} for r in rows[:50]]
    return {'dialogs':cards,'channels':snapshot['channels'],'agents':[],'unread_count':None,
            'attention_count':None,'ai_management_restricted':True,'total_count':int(snapshot['total']),
            'next_offset':offset+50 if len(rows)>50 else None}


def listing(profile,search='',channel='',agent='',state='',offset=0,*,all_rows=False,completion_preview=False,lifecycle="active",include_read_cursors=False,combine_history=False,
            owner_overview=False,search_overview=False,allow_mixed_history_card=False,observed_operator_id=None):
    if lifecycle not in {'active','all','completed'}: raise TenantLinearError('Некорректный режим диалогов.',400)
    if state not in {'','automatic','manual','needs_assignment','attention','no_agent'}: raise TenantLinearError('Некорректный статус.')
    offset=max(0,min(int(offset),1000000))
    with _connect() as conn:
        from tenant_platform import _ensure_schema
        _ensure_schema(conn)
        ensure_schema(conn)
        from tenant_connect_transport import _schema as ensure_connect_schema
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=access(cur,profile,'dialogs')
            if observed_operator_id is not None:
                if (all_rows or completion_preview or include_read_cursors or combine_history
                        or owner_overview or search_overview or agent or state):
                    raise TenantLinearError('Некорректный режим просмотра.',400)
                return _colleague_listing(cur,tenant,policy,observed_operator_id,search,str(channel or '')[:250],lifecycle,offset)
            restricted_ai = _scoped_ai_documents(policy)
            if restricted_ai and (agent or state):
                raise TenantLinearError('ai_shared_filter_requires_unscoped_management',403)
            interactive=not (all_rows or completion_preview or include_read_cursors)
            search_overview=bool(search_overview and str(search or '').strip() and interactive)
            owner_search_overview=bool(str(search or '').strip() and interactive and policy.profile.get('role')=='owner')
            owner_overview=bool(((owner_overview and lifecycle=='all') or owner_search_overview)
                                and interactive and policy.profile.get('role')=='owner')
            # Employee All includes the same source-scoped archive as Completed.
            employee_all=lifecycle=='all' and policy.profile.get('role')!='owner'
            allow_closed_archive=interactive and (lifecycle=='completed' or employee_all or owner_overview or search_overview)
            sql,scopes=projection(cur, policy,include_read_cursors=include_read_cursors,
                allow_closed_archive=allow_closed_archive,owner_overview=owner_overview,
                search_overview=search_overview,hide_removed=True);params=(tenant,tenant,*scopes,runtime_flag('AI_AGENTS_ENABLED'))
            # All metadata and logical routes share one materialized, authorized projection.
            sql=sql.replace('visible AS (','visible AS MATERIALIZED (')
            from tenant_dialog_rollout import guard as rollout_guard
            rollout=rollout_guard(cur,tenant)
            from tenant_whatsapp_history_pairs import available_pairs, fold_cards
            history_pairs=available_pairs(cur,tenant) if combine_history and rollout.enforced and not (completion_preview or all_rows) else []
            group_fold= "provider='connect' AND conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$'"
            if lifecycle != 'all' and not history_pairs:
                sql=sql.replace('visible AS MATERIALIZED (','eligible AS MATERIALIZED (')
                completed="EXISTS(SELECT 1 FROM saas_dialog_workflows dw WHERE dw.tenant_id='"+str(uuid.UUID(str(tenant)))+"'::uuid AND dw.channel_id::text=eligible.channel_id AND dw.conversation_id=eligible.conversation_id AND dw.state IN ('closed','dismissed'))"
                condition=("provider='connect' AND "+completed if lifecycle=='completed' else "provider<>'connect' OR NOT "+completed) if rollout.enforced else ('FALSE' if lifecycle=='completed' else 'TRUE')
                sql+=" , visible AS (SELECT eligible.* FROM eligible WHERE "+condition+") "
            cur.execute(sql+"""
              SELECT (SELECT count(DISTINCT CASE
                WHEN """+group_fold+"""
                THEN provider||':'||conversation_id ELSE provider||':'||channel_id||':'||lead_id END)
                FILTER(WHERE display_state IN ('human','paused','blocked','error')) FROM visible) AS attention_count,
              (SELECT COALESCE(jsonb_agg(x ORDER BY name),'[]'::jsonb) FROM
                (SELECT DISTINCT provider||':'||channel_id AS id,channel_name AS name FROM visible) x) AS channels,
              (SELECT COALESCE(jsonb_agg(x ORDER BY name),'[]'::jsonb) FROM
                (SELECT DISTINCT agent_id::text AS id,agent_name AS name FROM visible WHERE agent_id IS NOT NULL) x) AS agents,
              (SELECT COALESCE(jsonb_agg(visible ORDER BY last_message_at DESC NULLS LAST,provider,lead_id),'[]'::jsonb)
                FROM visible) AS dialogs""",params)
            snapshot=cur.fetchone()
            count=int(snapshot['attention_count']);channels=snapshot['channels'];agents=snapshot['agents'];rows=snapshot['dialogs']
            from tenant_operator_list_snapshot import ListSnapshot
            enrichment=None if restricted_ai else ListSnapshot(cur,tenant,rows)
            from tenant_ai_group_coordination import group_jid
            rows=[decorate(r) for r in rows]
            for r in rows:
                if restricted_ai:
                    continue
                if r['provider']=='connect' and group_jid(r['conversation_id']):
                    apply_group_view(cur,tenant,r,snapshot=enrichment)
                    r['group_assignment_preview']=enrichment.group_options(r)
                    if not r.get('agent_id') and not r.get('group_coordination',{}).get('assignments'):
                        candidates=r['group_assignment_preview'].get('agents') or []
                        r['agent_status']='needs_assignment' if candidates else 'no_agent'
                        r['agent_status_reason']='assignment_required' if candidates else r['group_assignment_preview'].get('reason') or ''
                elif r['provider']=='connect' and not r.get('agent_id') and r.get('unambiguous') and r.get('controls_allowed'):
                    # Surface the same read-only candidate choices used by the
                    # card for every page of existing dialogues. Never bind or
                    # resume an agent while listing conversations.
                    r['agent_assignment']=enrichment.direct_options(r)
                    r['agent_status']='needs_assignment' if r['agent_assignment'].get('agents') else 'no_agent'
                    r['agent_status_reason']=('assignment_required' if len(r['agent_assignment'].get('agents') or [])==1 else
                      'agent_selection_required' if len(r['agent_assignment'].get('agents') or [])>1 else r['agent_assignment'].get('reason') or '')
            from tenant_dialog_access import apply_capabilities
            from tenant_dialog_lifecycle import decorate as lifecycle_decorate, is_owner_observer
            from tenant_operator_list_access import ListAccess
            list_access=ListAccess(cur,tenant,policy,rows,rollout) if rollout.enforced or policy.has_scoped_access('dialogs') else None
            inbound=_completion_inbound_snapshot(cur,tenant,rows,presence_only=not completion_preview) if rollout.enforced else {}
            workflows={}
            marked_inbound={}
            company_prelinked_for_bulk=None
            presence_online=False
            presence_sources=frozenset()
            group_work_snapshots={}
            group_modes={}
            if rollout.enforced:
                # One tenant-scoped snapshot, not one lookup per visible row.
                # Only entries matching already-authorized rows are projected.
                cur.execute('SELECT * FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(tenant,))
                workflows={(str(w['channel_id']),w['conversation_id']):w for w in cur.fetchall()}
                from tenant_dialog_lifecycle import optional_no_reply_marks
                visible_routes={(str(r['channel_id']),r['conversation_id']) for r in rows if r['provider']=='connect'}
                marked=optional_no_reply_marks(cur,tenant,[(channel,conversation,workflow.get('last_inbound_id'))
                    for (channel,conversation),workflow in workflows.items()
                    if workflow['state'] in {'waiting','accepted'} and (channel,conversation) in visible_routes])
                if marked:
                    exact_routes={(channel,conversation) for channel,conversation,_ in marked}
                    latest=_completion_inbound_snapshot(cur,tenant,[r for r in rows
                        if (str(r['channel_id']),r['conversation_id']) in exact_routes])
                    marked_inbound={key:message for key,message in latest.items() if (*key[:2],message) in marked}
                from tenant_dialog_presence import is_online, work_status_routing_sources
                presence_online=is_online(cur,tenant,int(profile['telegram_id']))
                presence_sources=work_status_routing_sources(tenant)
                import tenant_dialog_group_work as group_work
                group_conversations=[r['conversation_id'] for r in rows
                                     if r['provider']=='connect' and group_jid(r['conversation_id'])]
                if group_conversations:
                    from tenant_whatsapp_native_rollout import modes as native_modes
                    group_modes=native_modes(cur,tenant)
                    group_work_snapshots=group_work.snapshots(cur,tenant,group_conversations,
                        **group_work.source_selection(group_modes))
                if completion_preview:
                    import tenant_dialog_company_completion as completion
                    if completion.enabled(tenant):
                        company_prelinked_for_bulk=completion.bulk_prelinked_routes(cur,tenant,int(profile['telegram_id']))
            for r in rows:
                action_capabilities={}
                # The legacy, unscoped capability path is a no-op; avoid its
                # per-group mode recheck under our already-retained barrier.
                if rollout.enforced or policy.has_scoped_access('dialogs'):
                    apply_capabilities(cur,tenant,policy,r,_list_access=list_access,_action_capabilities=action_capabilities)
                if rollout.enforced:
                    workflow=workflows.get((str(r['channel_id']),r['conversation_id']))
                    online=policy.profile.get('role')=='owner' or presence_online or bool(presence_sources and str(r['channel_id']) not in presence_sources)
                    observer=owner_overview and r['provider']=='connect' and is_owner_observer(cur,tenant,actor,r['channel_id'],workflow,online=online)
                    group=group_work_snapshots.get(r['conversation_id']) if r['provider']=='connect' else None
                    group_source=group_modes.get(r['channel_id'],{})
                    search_observer=(search_overview and not observer and r['provider']=='connect' and workflow
                        and workflow.get('state')=='accepted' and int(workflow.get('operator_id') or 0)!=int(actor)
                        and not (group and group_work.accepted_route(group,workflow)
                                 and group_work.source_available(group_source)))
                    if group and (group['configured'] or group_work.active(group)) and not group['legacy_active'] and group_work.source_available(group_source):
                        r['can_send']=action_capabilities.get('update',False) and r.get('tenant_status')=='active'
                    lifecycle_decorate(cur,tenant,actor,r,details=False,allow_closed_archive=allow_closed_archive,owner_observer=observer,
                        search_observer=search_observer,
                        _rollout=rollout,_policy=policy,_can_update=action_capabilities.get('update',False),_workflow=workflow,
                        _online=online,
                        _no_reply_marked=(str(r['channel_id']),r['conversation_id'],int(r['lead_id'])) in marked_inbound,
                        _has_inbound=bool(inbound.get((str(r['channel_id']),r['conversation_id'],int(r['lead_id'])))),
                        _last_inbound=inbound.get((str(r['channel_id']),r['conversation_id'],int(r['lead_id']))) if completion_preview else None)
                    if group and not search_observer:
                        group_work.decorate(r,actor,group,workflow,source=group_source,admission=rollout.admission_enabled,
                            can_update=action_capabilities.get('update',False),online=online)
                    if completion_preview and r.get('lifecycle_enabled'):
                        row_lifecycle=r['lifecycle']
                        if row_lifecycle.get('group_work'):
                            # Shared closure requires a decision in the open chat.
                            row_lifecycle.update(can_close=False,can_dismiss=False,bulk_waiting=False)
                            continue
                        row_lifecycle['bulk_waiting']=bool(row_lifecycle['state']=='waiting' and row_lifecycle['has_inbound']
                            and (row_lifecycle['no_reply_needed'] or (rollout.admission_enabled
                                 and action_capabilities.get('update',False) is True)))
                    if company_prelinked_for_bulk is not None and r.get('lifecycle_enabled'):
                        row_lifecycle=r['lifecycle']
                        bulk_gate='not_applicable'
                        if row_lifecycle['bulk_waiting']:
                            bulk_gate='no_reply_separate'
                        elif row_lifecycle['state']=='accepted' and row_lifecycle['can_close']:
                            bulk_gate=('prelinked_unverified' if (str(r['channel_id']),r['conversation_id'])
                                       in company_prelinked_for_bulk else 'choose')
                        row_lifecycle['bulk_company_gate']=bulk_gate
            # Mode is a read-only view over already-authorized physical routes.
            # Recheck lifecycle ownership from the later snapshot as well.
            rows=[r for r in rows if r.get('archive_read_only') or r.get('search_overview_read_only')
                  or ((r.get('lifecycle') or {}).get('group_work') or {}).get('can_join')
                  or not (r.get('lifecycle') or {}).get('operator_id')
                  or int(r['lifecycle']['operator_id'])==int(profile['telegram_id'])]
            if allow_closed_archive:
                # Only exclude options introduced exclusively by the new
                # archive rows, not existing options from another UI filter.
                prior_agents={str(r['agent_id']) for r in rows if r.get('agent_id')}
                agents=[a for a in agents if str(a.get('id')) in prior_agents]
                count=len({('group',r['conversation_id']) if r['provider']=='connect' and group_jid(r['conversation_id'])
                           else (r['provider'],r['channel_id'],r['lead_id']) for r in rows
                           if not r.get('archive_read_only') and r['display_state'] in {'human','paused','blocked','error'}})
            history_companions={}
            if history_pairs:
                if allow_closed_archive:
                    exact_shared={r['lead_id'] for r in rows if r.get('archive_read_only')}
                    history_pairs=[p for p in history_pairs
                                   if p['primary_lead'] not in exact_shared and p['alternate_lead'] not in exact_shared]
                rows,history_companions=fold_cards(rows,history_pairs)
            if lifecycle != 'all':
                if lifecycle == 'active' and group_work_snapshots:
                    left_groups={r['conversation_id'] for r in rows if r['provider']=='connect'
                                 and (r['lifecycle'].get('group_work') or {}).get('left')}
                    joined_groups={r['conversation_id'] for r in rows if r['provider']=='connect'
                                   and (r['lifecycle'].get('group_work') or {}).get('role')}
                    personally_closed=left_groups-joined_groups
                    if personally_closed:
                        rows=[r for r in rows if r['provider']!='connect' or r['conversation_id'] not in personally_closed]
                rows=[r for r in rows if ((r.get('lifecycle') or {}).get('state') in {'closed','dismissed'}) == (lifecycle=='completed')]
            if history_pairs:
                count=len({('group',r['conversation_id']) if r['provider']=='connect' and group_jid(r['conversation_id'])
                           else (r['provider'],r['channel_id'],r['lead_id']) for r in rows
                           if not r.get('archive_read_only') and r['display_state'] in {'human','paused','blocked','error'}})
            if lifecycle == 'active' and rollout.enforced and not completion_preview:
                # A source with only outgoing history has no active request.
                # Keep it in All and in completion previews (for skip auditing),
                # but filter physical routes before group folding and pagination.
                rows=[r for r in rows if not (r.get('provider') == 'connect'
                      and (r.get('lifecycle') or {}).get('state') == 'waiting'
                      and (r.get('lifecycle') or {}).get('has_inbound') is False
                      and not (r['lifecycle'].get('group_work') or {}).get('can_join'))]
            if restricted_ai:
                _assignment_display(cur,tenant,policy,[r for r in rows if not r.get('archive_read_only')])
                rows=[_hide_shared_ai(r) for r in rows]
                agents=[]
                count=None
            # Preparation must not initialize read cursors or mark anything read.
            unread_total=None if completion_preview else _unread_counts(cur,tenant,int(profile['telegram_id']),
                [r for r in rows if not r.get('archive_read_only')])
            def group_readable(routes):
                return [r for r in routes if r['provider']=='connect' and r['conversation_id'] in group_work_snapshots
                        and not r.get('archive_read_only')]
            group_unread={}
            counted=group_readable(rows)
            if counted and unread_total is not None:
                from tenant_connect_group_history import unread_counts as count_group_events
                group_unread={r['conversation_id']:0 for r in counted}
                group_unread.update(count_group_events(cur,tenant,actor,counted))
                unread_total += sum(group_unread.values()) - sum(r['unread_count'] for r in counted)
            from tenant_beinsystems_operator_access import dialogue_chooser_enabled
            # A company label is already visible in the source-scoped archive
            # with the independent Operator read grant. Keep the same rule
            # when searching; do not expose labels for cross-owner live rows.
            show_archive_labels = (interactive and lifecycle in {'all','completed'}
                and dialogue_chooser_enabled(tenant) and policy.allows_action('operator_data','view'))
            widget_rows, widget_channel = [], None
            if interactive or (all_rows and include_read_cursors and not completion_preview):
                from tenant_operator_widget_queue import cards as widget_cards
                widget_rows, widget_channel = widget_cards(cur,policy,lifecycle,rollout)
                if widget_channel:
                    channels.append(widget_channel)
                    if unread_total is not None:
                        unread_total += sum(w['unread_count'] for w in widget_rows if not w['archive_read_only'])
                    rows.extend(widget_rows)
            if search:
                query_text=str(search)[:200].casefold()
                def matches(row):
                    fields=('customer_name','phone','conversation_id')
                    if row['provider']=='operator_widget':
                        fields+=('operator_account','dialog_company_name')
                    return any(query_text in str(row.get(key) or '').casefold() for key in fields)

                # A card may keep the workflow under a private WhatsApp ID.
                # Search its phone route only if both routes passed visibility.
                def matches_text(r):
                    if matches(r):
                        return True
                    companion=history_companions.get((r['channel_id'],r['conversation_id'],int(r['lead_id'])))
                    return bool(r['provider']=='connect' and companion and matches(companion))

                # Match the company on an authorized physical route before
                # folding groups and paginating. Never borrow a different
                # source's company name from a paired history or a group.
                from tenant_dialog_company_binding import list_route_key, matching_list_company_routes
                search_rows=[(r,matches_text(r)) for r in rows]
                company_matches=matching_list_company_routes(cur,tenant,policy,
                    [r for r,match in search_rows if not match],query_text,
                    show_archive_labels=show_archive_labels) if interactive else set()
                rows=[r for r,match in search_rows if match or (company_matches and list_route_key(r) in company_matches)]
            if channel:rows=[r for r in rows if str(r.get('provider') or '')+':'+str(r.get('channel_id') or '')==str(channel)[:250]]
            if agent or state:
                # Agent metadata is intentionally redacted on passive rows.
                # Do not misclassify those dialogs as unassigned or infer
                # their private AI state from a filter match.
                rows=[r for r in rows if not r.get('archive_read_only')]
            for r in rows:
                if not r.get('archive_read_only') and r.get('agent_id') and r.get('agent_name') and not any(str(a.get('id'))==str(r['agent_id']) for a in agents):
                    agents.append({'id':str(r['agent_id']),'name':r['agent_name']})
            if agent=='unassigned':rows=[r for r in rows if not r.get('agent_id')]
            elif agent:
                selected_agent=identifier(agent)
                rows=[r for r in rows if str(r.get('agent_id') or '')==selected_agent]
            rows=[r for r in rows if not state or r.get('agent_status')==state]
            agents.sort(key=lambda item:str(item.get('name') or '').casefold())
            if interactive and combine_history and not completion_preview and not all_rows and not search_overview:
                # Fold only two original visible physical routes, after filtering and
                # before pagination. Keep counters and controls independent.
                from tenant_dialog_history_merge_cards import fold_completed
                merge_channel=str(channel).removeprefix('connect:') if str(channel).startswith('connect:') else None
                rows=fold_completed(cur,tenant,policy,rows,history_companions,channel=merge_channel,
                    allow_mixed=bool(allow_mixed_history_card and lifecycle=='all' and rollout.enforced))
            # Adding a read-only sibling must not replace an actionable route.
            if group_unread:
                displayed=group_readable(rows)
                if {(r['channel_id'],r['conversation_id']) for r in displayed} != {(r['channel_id'],r['conversation_id']) for r in counted}:
                    group_unread={r['conversation_id']:0 for r in displayed}
                    group_unread.update(count_group_events(cur,tenant,actor,displayed))
            rows=_fold_group_routes(rows,prefer_actionable=owner_overview or (interactive and employee_all),
                                    group_unread=group_unread)
            if widget_rows:
                from tenant_operator_widget_queue import merge_order
                rows = merge_order(rows)
            total_count=len(rows)
            if not all_rows:rows=rows[offset:offset+51]
            if interactive and rollout.enforced:
                _attach_missed_call_attention(cur,tenant,rows[:50])
            if not all_rows and not completion_preview:
                from tenant_dialog_company_binding import attach_list_company_names
                cards = rows[:50]
                owner = policy.profile.get('role') == 'owner'
                contact_cards = cards if owner else [r for r in cards if not r.get('archive_read_only')]
                _attach_verified_phones(cur,tenant,contact_cards,check_conflicts=True)
                from tenant_contact_phone import attach_list_source_revisions
                attach_list_source_revisions(cur,tenant,contact_cards)
                company_cards = cards if owner else [r for r in cards
                    if not r.get('search_overview_read_only')
                    and (not r.get('archive_read_only') or show_archive_labels)]
                attach_list_company_names(cur,tenant,company_cards,policy=policy)
    return {**({'completion_preview':True} if completion_preview else {}),
            'dialogs':rows if all_rows else rows[:50],'channels':channels,'agents':agents,
            'ai_management_restricted':restricted_ai,
            'attention_count':count,'unread_count':unread_total,'total_count':total_count,
            'next_offset':None if all_rows else offset+50 if len(rows)>50 else None}


def mark_read(profile,payload):
    """Advance each retained source cursor only through messages on that source."""
    if not isinstance(payload,dict):raise TenantLinearError('Некорректный запрос.')
    provider=str(payload.get('provider') or '')
    if provider=='operator_widget':
        from tenant_operator_widget_queue import read
        return read(profile,payload)
    if provider not in {'connect','kommo'}:raise TenantLinearError('Некорректный канал.')
    message_id=identifier(payload.get('message_id'));lead_id=int(payload.get('lead_id'))
    channel=str(payload.get('channel_id') or '')[:250]
    conversation=str(payload.get('conversation_id') or '')[:250]
    with _connect() as conn:
        ensure_schema(conn)
        from tenant_connect_transport import _schema
        _schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=access(cur,profile,'dialogs')
            search_overview=payload.get('search_overview') is True
            if search_overview and (provider!='connect' or not search_overview_matches(cur,tenant,policy,channel,conversation,lead_id,payload.get('search'))):
                raise TenantLinearError('Диалог недоступен.',404)
            route=assert_route(cur,tenant,policy,provider,conversation,channel,lead_id,allow_merged=True,
                search_overview=search_overview)
            members={conversation:lead_id}
            if provider=='connect':
                channel=str(route['connect_channel_id']);conversation=route['conversation_id']
                from tenant_connect_dialog_equivalence import members as read_members
                members=read_members(cur,tenant,channel,conversation)
            history_channel='connect:'+channel if provider=='connect' else str(route.get('history_channel') or channel)
            readable_direction="direction IN ('incoming','outgoing')"
            if provider=='connect':
                readable_direction+=" OR (direction='system' AND message_type='call_missed')"
            cur.execute(f"""SELECT id,created_at AS at FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s AND id=%s::uuid
                AND ({readable_direction})""",(tenant,list(members.values()),history_channel,message_id))
            message=cur.fetchone()
            if not message:raise TenantLinearError('Сообщение не найдено в этом диалоге.',404)
            for identity,lead in members.items():
                cur.execute(f"""SELECT id,created_at AS at FROM saas_crm_messages WHERE tenant_id=%s::uuid
                  AND kommo_lead_id=%s AND channel=%s AND (created_at,id)<=(%s,%s::uuid)
                  AND ({readable_direction}) ORDER BY created_at DESC,id DESC LIMIT 1""",
                  (tenant,lead,history_channel,message['at'],str(message['id'])))
                last=cur.fetchone()
                if not last: continue
                cur.execute("""INSERT INTO saas_operator_dialog_reads(tenant_id,telegram_id,provider,channel_id,conversation_id,lead_id,last_read_at,last_read_message_id,updated_at)
                  VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s::uuid,clock_timestamp())
                  ON CONFLICT(tenant_id,telegram_id,provider,channel_id,conversation_id,lead_id) DO UPDATE SET
                    last_read_at=EXCLUDED.last_read_at,last_read_message_id=EXCLUDED.last_read_message_id,updated_at=clock_timestamp()
                  WHERE (EXCLUDED.last_read_at,COALESCE(EXCLUDED.last_read_message_id,'00000000-0000-0000-0000-000000000000'::uuid))>
                        (saas_operator_dialog_reads.last_read_at,COALESCE(saas_operator_dialog_reads.last_read_message_id,'00000000-0000-0000-0000-000000000000'::uuid))""",
                  (tenant,int(profile['telegram_id']),provider,channel,identity,lead,last['at'],str(last['id'])))
    return {'read_through_message_id':str(message_id),'updated':True}


def mark_all_read(profile, payload=None):
    """Mark each currently accessible unread dialog through its captured latest message."""
    # The shared operator mutation handler always passes a payload after
    # stripping the expected tenant/user scope fields. This action intentionally
    # accepts no operation-specific fields or client-selected dialog IDs.
    if payload not in (None, {}):
        raise TenantLinearError('Некорректный запрос.')
    # The operation ignores the current UI filters, including the active vs.
    # completed queue mode. Keep the existing route ACL projection, but capture
    # both open and closed accessible conversations.
    snapshot=listing(profile,all_rows=True,lifecycle='all',include_read_cursors=True)
    targets=[row for card in snapshot.get('dialogs',[]) for row in card.get('group_sources',[card])
             if int(row.get('unread_count') or 0)>0 and row.get('read_through_message_id')]
    updated=0
    for row in targets:
        mark_read(profile,{'provider':row.get('provider'),'lead_id':row.get('lead_id'),
                           'channel_id':row.get('channel_id'),'conversation_id':row.get('conversation_id'),
                           'message_id':row.get('read_through_message_id')})
        updated+=1
    return {'updated_dialogs':updated,'captured_dialogs':len(snapshot.get('dialogs',[])),
            'unread_before':int(snapshot.get('unread_count') or 0)}


def _linked_support_case(cur, tenant, policy, channel, conversation_ids):
    """Minimal support projection for an already-authorized exact dialogue."""
    from tenant_support_access import predicate
    scope, params = predicate(policy)
    if scope == 'FALSE' or not conversation_ids:
        return None
    # The support inbox predicate authorizes both active and resolved cases.
    # Never match a chat assignee, another source, or an unverified identity.
    cur.execute("""SELECT t.status, m.display_name AS responsible_name
      FROM saas_support_tickets t
      LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.assigned_to
      WHERE t.tenant_id=%s::uuid AND t.channel_id=%s::uuid AND t.conversation_id=ANY(%s::text[])
        AND t.completion_outcome<>'test_stopped' AND """+scope+"""
      ORDER BY (t.status<>'resolved') DESC,
        CASE WHEN t.status='resolved' THEN COALESCE(t.resolved_at,t.updated_at,t.created_at)
             ELSE t.created_at END DESC,t.id DESC LIMIT 1""",
      (tenant,channel,list(conversation_ids),*params))
    row=cur.fetchone()
    return dict(row) if row else None


def history(profile,provider,lead_id,channel='',conversation='',before=None,anchor=None,
            *,support_case_only=False,include_operator_events=False,operator_events_revision=None,
            include_shared_reminder_events=False,include_internal_comments=False,combine_history=False,redirect_paired_history=False,
            owner_overview=False,search_overview=False,search='',observed_operator_id=None):
    if provider == 'operator_widget':
        if observed_operator_id is not None or support_case_only or anchor:
            raise TenantLinearError('Диалог недоступен в этом режиме.',403)
        from tenant_operator_widget_queue import history as widget_history
        return widget_history(profile,dict(provider=provider,lead_id=lead_id,channel_id=channel,conversation_id=conversation),before)
    colleague=observed_operator_id is not None
    if colleague and (support_case_only or owner_overview or search_overview or combine_history
                      or redirect_paired_history or include_operator_events or include_shared_reminder_events
                      or include_internal_comments or anchor):
        raise TenantLinearError('Некорректный режим просмотра.',400)
    if support_case_only and provider=='connect':
        # Unlike ordinary history shortcuts, case display always names the
        # selected source explicitly, including when a group spans numbers.
        if (str(channel)!=identifier(channel) or not isinstance(conversation,str)
                or not conversation or len(conversation)>160 or conversation.strip()!=conversation):
            raise TenantLinearError('Диалог не найден.',404)
    lead_id=int(lead_id)
    requested_route={'provider':provider,'lead_id':lead_id,'channel_id':str(channel),'conversation_id':str(conversation)}
    operator_timeline={}
    shared_reminder_timeline={}
    internal_comment_timeline={}
    group_read_targets=[]
    with _connect() as conn:
        ensure_schema(conn)
        from tenant_connect_transport import _schema as ensure_connect_schema
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=access(cur,profile,'dialogs')
            owner_overview=bool(owner_overview and not support_case_only and policy.profile.get('role')=='owner')
            search_overview=bool(search_overview and not support_case_only and provider=='connect')
            if search_overview and not search_overview_matches(cur,tenant,policy,channel,conversation,lead_id,search):
                raise TenantLinearError('Диалог недоступен.',404)
            transfer_targets=[]
            from tenant_dialog_lifecycle import enabled as lifecycle_enabled, acceptance_enabled, _targets, _row, is_shared_archive, is_owner_observer
            if not (support_case_only or colleague) and provider=='connect' and lifecycle_enabled(cur,tenant):
                from tenant_operator_access import _resolve_connect_route
                candidate=_resolve_connect_route(cur,tenant,conversation,str(channel),lead_id,allow_merged=True)
                transfer_targets=_targets(cur,tenant,policy,str(candidate['connect_channel_id']),int(profile['telegram_id']))
            selected=assert_route(cur,tenant,policy,provider,conversation,str(channel),lead_id,allow_merged=not colleague,
                allow_closed_archive=not (support_case_only or colleague),owner_overview=owner_overview,
                search_overview=search_overview,observed_operator_id=observed_operator_id)
            if provider=='connect':
                channel=str(selected['connect_channel_id']);conversation=selected['conversation_id'];lead_id=int(selected['lead_id'])
            workflow=_row(cur,tenant,channel,conversation) if provider=='connect' and lifecycle_enabled(cur,tenant) else None
            owner_observer=(owner_overview and provider=='connect' and lifecycle_enabled(cur,tenant)
                            and is_owner_observer(cur,tenant,actor,channel,workflow))
            search_observer=(search_overview and not owner_observer and provider=='connect' and workflow
                and workflow.get('state')=='accepted' and int(workflow.get('operator_id') or 0)!=int(actor))
            group_dialog=None
            group_mode={}
            group_modes={}
            if provider=='connect' and not support_case_only:
                import tenant_dialog_group_work as group_work
                from tenant_whatsapp_native_rollout import modes as native_modes
                group_modes=native_modes(cur,tenant)
                group_mode=group_modes.get(channel,{})
                if not (owner_observer or colleague) and lifecycle_enabled(cur,tenant):
                    group_dialog=group_work.snapshot(cur,tenant,conversation,**group_work.source_selection(group_modes))
                    if (search_observer and group_work.accepted_route(group_dialog,workflow)
                            and group_work.source_available(group_mode)):
                        search_observer=False
            closed_archive=is_shared_archive(workflow,actor)
            shared_archive=closed_archive or owner_observer or search_observer or colleague
            if shared_archive and requested_route != {'provider':provider,'channel_id':channel,'conversation_id':conversation,'lead_id':lead_id}:
                raise TenantLinearError('Диалог недоступен.',404)
            history_members={conversation:lead_id}
            if provider=='connect' and not shared_archive:
                from tenant_connect_dialog_equivalence import members
                history_members=members(cur,tenant,channel,conversation)
            history_leads=list(history_members.values())
            paired_history=None
            if combine_history and not support_case_only and not shared_archive and provider=='connect' and lifecycle_enabled(cur,tenant) and len(history_members)==1:
                from tenant_whatsapp_history_pairs import history_pair
                paired_history=history_pair(cur,tenant,policy,channel,conversation,lead_id,
                    include_alternate=redirect_paired_history and not before and not anchor)
                if paired_history and conversation==paired_history['alternate_id']:
                    # An old card can lead to the combined history only after
                    # both exact routes have independently passed view checks.
                    return {'combined_dialog':{'provider':'connect','channel_id':channel,
                        'conversation_id':paired_history['primary_id'],'lead_id':paired_history['primary_lead']}}
            history_merge=None
            if (combine_history and not (support_case_only or search_overview or anchor)
                    and provider=='connect' and not paired_history and len(history_members)==1
                    and requested_route=={'provider':provider,'channel_id':channel,
                                          'conversation_id':conversation,'lead_id':lead_id}):
                from tenant_dialog_history_merge_projection import history_pair as completed_merge, lifecycle_enabled as merged_lifecycle
                if not shared_archive or merged_lifecycle():
                    history_merge=completed_merge(cur,tenant,policy,channel,conversation,lead_id,owner_overview=owner_overview)
            message_leads=history_leads.copy()
            if paired_history:
                message_leads.append(paired_history['alternate_lead'])
            sql,scopes=projection(cur, policy,include_contact_names=not support_case_only,
                allow_closed_archive=closed_archive,owner_overview=owner_observer,
                search_overview=search_observer or colleague)
            columns='provider,lead_id,channel_id,conversation_id' if support_case_only else '*'
            cur.execute(sql+'SELECT '+columns+' FROM visible WHERE provider=%s AND lead_id=%s AND channel_id=%s AND conversation_id=%s',
                (tenant,tenant,*scopes,runtime_flag('AI_AGENTS_ENABLED'),provider,lead_id,str(channel),str(conversation)))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Диалог не найден.',404)
            if support_case_only:
                support=_linked_support_case(cur,tenant,policy,channel,history_members) if provider=='connect' else None
                return {'dialog':{**public(row),'support_case':support}}
            dialog=decorate(row)
            if provider=='connect':
                dialog['wecall']=bool(group_mode.get('enabled'))
                if search_observer:
                    group_dialog=None
                if (not shared_archive and group_dialog and (group_dialog['configured'] or group_work.active(group_dialog))
                        and not group_dialog['legacy_active'] and group_work.source_available(group_mode)):
                    dialog['can_send']=dialog.get('tenant_status')=='active'
            if paired_history:
                dialog['history_pair_id']=paired_history['id']
            if history_merge:
                dialog['history_merge_id']=history_merge.get('projection_revision',history_merge['id'])
                for field in ('name','phone'):
                    value=history_merge.get('display_'+field)
                    if value:
                        dialog['history_merge_display_'+field]=value
                if history_merge.get('action_routes'):
                    dialog['history_merge_action_routes']=history_merge['action_routes']
                if history_merge.get('read_routes'):
                    dialog['history_merge_certificate']=history_merge['id']
                    dialog['history_merge_read_routes']=history_merge['read_routes']
            restricted_ai = _scoped_ai_documents(policy)
            # This server-produced binding is emitted only after the requested
            # route passes assert_route and the canonical route is visible.
            # Historical cases keep their original route and material scope.
            history_scope={'tenant_id':str(tenant),'requested_route':requested_route,
                           'canonical_route':{key:dialog[key] for key in requested_route}}
            if provider=='connect':_attach_verified_phone(cur,tenant,dialog)
            from tenant_ai_group_coordination import group_jid, view as group_view
            if not shared_archive and not restricted_ai and provider=='connect' and group_jid(conversation):
                apply_group_view(cur,tenant,dialog)
                from tenant_operator_assignment import group_options_in_transaction
                dialog['group_assignment_preview']=group_options_in_transaction(cur,tenant,channel,conversation)
                if not dialog.get('agent_id') and not dialog.get('group_coordination',{}).get('assignments'):
                    candidates=dialog['group_assignment_preview'].get('agents') or []
                    dialog['agent_status']='needs_assignment' if candidates else 'no_agent'
                    dialog['agent_status_reason']='assignment_required' if candidates else dialog['group_assignment_preview'].get('reason') or ''
                try:
                    from tenant_ai_agents_store import _authorize
                    _authorize(cur,profile)
                    dialog['group_coordination']['can_configure']=True
                    cur.execute('SELECT id,name,role,scopes,schedule,expert_policy FROM saas_ai_agents WHERE tenant_id=%s::uuid AND enabled=TRUE',(tenant,))
                    team={str(r['id']):dict(r) for r in cur.fetchall()}
                    from tenant_ai_expert_policy import helper_ids
                    primary=[]
                    for agent in team.values():
                        if agent['role']!='primary':continue
                        helpers=[]
                        for helper_id in helper_ids(agent.get('expert_policy') or {}):
                            helper=team.get(str(helper_id))
                            if not helper or helper['role']!='expert':continue
                            helper_policy=helper.get('expert_policy') or {}
                            if helper_policy and (not helper_policy.get('internal') or str(agent['id']) not in helper_policy.get('allowed_primary_ids',[])):continue
                            helpers.append(helper['name'])
                        primary.append({**public({k:agent[k] for k in ('id','name','scopes','schedule')}),'role':'primary','helpers':helpers})
                    dialog['group_coordination']['agents']=primary
                except TenantLinearError:
                    dialog['group_coordination']['can_configure']=False
            if not shared_archive and not restricted_ai and provider=='connect' and not group_jid(conversation) and not dialog.get('agent_id'):
                if dialog.get('unambiguous') and dialog.get('controls_allowed'):
                    from tenant_operator_assignment import options_in_transaction
                    dialog['agent_assignment']=options_in_transaction(cur,profile,{
                        'provider':'connect','lead_id':lead_id,'channel_id':channel,'conversation_id':conversation})
                else:
                    dialog['agent_assignment']={'allowed':False,'reason':dialog.get('control_block_reason'),'agents':[]}
                dialog['agent_status']='needs_assignment' if dialog['agent_assignment'].get('agents') else 'no_agent'
                dialog['agent_status_reason']=('assignment_required' if len(dialog['agent_assignment'].get('agents') or [])==1 else
                  'agent_selection_required' if len(dialog['agent_assignment'].get('agents') or [])>1 else dialog['agent_assignment'].get('reason') or '')
            if provider=='kommo' and dialog['controls_allowed']:
                from tenant_ai_agent_admission import conversation_allowed
                if not conversation_allowed(cur,tenant,conversation):
                    dialog['controls_allowed']=False;dialog['control_block_reason']='untracked_dialog'
            anchor_at=None;anchor_row={}
            if anchor and not before:
                from tenant_dialog_access import sql_filter
                # Resolve anchor only within the authorized route / exact tenant group.
                cur.execute('''SELECT m.id,m.external_id,m.kommo_lead_id,COALESCE(m.happened_at,m.created_at) AS at FROM saas_crm_messages m
                  WHERE m.tenant_id=%s::uuid AND m.id=%s::uuid AND (NOT %s OR m.direction IN ('incoming','outgoing'))
                    AND ((m.kommo_lead_id=ANY(%s::bigint[]) AND m.channel=%s)
                    OR (%s AND EXISTS(SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=m.tenant_id
                      AND c.conversation_id=%s AND c.lead_id=m.kommo_lead_id AND m.channel='connect:'||c.connect_channel_id::text))) AND '''+sql_filter(policy,"CASE WHEN m.channel LIKE 'connect:%%' THEN 'connect' ELSE 'kommo' END","substring(m.channel from 9)"),
                  (tenant,identifier(anchor),shared_archive,message_leads,row['history_channel'],bool(not shared_archive and provider=='connect' and group_jid(conversation)),conversation))
                anchor_row=dict(cur.fetchone() or {});anchor_at=anchor_row.get('at')
                if not anchor_row:
                    raise TenantLinearError('The requested message is unavailable or outside the permitted conversation.',404)
                from tenant_dialog_access import require_lead
                if not shared_archive:
                    require_lead(cur,tenant,policy,int(anchor_row['kommo_lead_id']))
            group_channels=None
            merge_timeline_boundary={}
            if provider=='connect' and group_jid(conversation):
                from tenant_connect_group_history import page as group_history
                from tenant_dialog_access import allowed_channels
                group_channels=[channel] if shared_archive else allowed_channels(cur,tenant,policy,conversation=conversation)
                messages=group_history(cur,tenant,conversation,before=identifier(before) if before else None,through=anchor_at,
                  channel_ids=group_channels,transcript_only=shared_archive)
                for message in messages:
                    message['source_conversation_id']=conversation
                if group_dialog:
                    group_work.attach_senders(cur,tenant,actor,conversation,messages[:50])
                    if not before and not anchor:
                        from tenant_connect_group_history import read_targets
                        group_read_targets=read_targets(cur,tenant,conversation,messages[:50])
            elif history_merge:
                from tenant_dialog_history_merge_projection import page as merged_page
                messages=merged_page(cur,tenant,channel,history_merge,before=identifier(before) if before else None,
                                     call_filter=_RECORDED_CALL_OBSERVATION_FILTER,
                                     timeline_boundary=merge_timeline_boundary)
            else:
                args=[tenant,message_leads,row['history_channel']];clause=''
                if anchor_at:
                    clause=' AND (COALESCE(happened_at,created_at),id)<=(%s,%s::uuid)'
                    args.extend((anchor_at,str(anchor_row['id'])))
                if before:
                    clause=''' AND (COALESCE(happened_at,created_at),id)<(SELECT COALESCE(happened_at,created_at),id FROM saas_crm_messages
                      WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s AND id=%s::uuid
                        AND (NOT %s OR direction IN ('incoming','outgoing')))'''
                    args += [tenant,message_leads,row['history_channel'],identifier(before),shared_archive]
                if shared_archive:
                    clause+=" AND m.direction IN ('incoming','outgoing')"
                if paired_history:
                    clause+=" AND (m.kommo_lead_id<>%s OR m.direction='outgoing')"
                    args.append(paired_history['alternate_lead'])
                call_filter=_RECORDED_CALL_OBSERVATION_FILTER if provider=='connect' else ''
                cur.execute('''SELECT id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,created_at,raw,kommo_lead_id AS source_lead_id FROM saas_crm_messages m
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s'''+clause+call_filter+'''
                  ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 51''',tuple(args))
                messages=cur.fetchall()
                if provider=='connect':
                    by_lead={v:k for k,v in history_members.items()}
                    if paired_history:
                        by_lead[paired_history['alternate_lead']]=paired_history['alternate_id']
                    for message in messages:
                        message['source_conversation_id']=by_lead[int(message['source_lead_id'])]
                        message['source_channel_id']=channel
            if provider=='connect' and not colleague:
                _attach_saved_audio_transcripts(cur,tenant,messages[:50],conversation_id=conversation,view_channel=channel)
                _attach_saved_visual_status(cur,tenant,messages[:50])
                _attach_call_recording_directions(cur,tenant,messages[:50])
                if not history_merge:
                    from tenant_whatsapp_native_reactions import attach as attach_reactions
                    attach_reactions(cur,tenant,messages[:50])
            captured_group=[]
            if provider=='connect' and group_jid(conversation):
                # A displayed group event can be represented by another channel's
                # copy. Resolve only captured copies belonging to this route.
                external_ids={str(src.get('external_id')) for m in messages[:50] for src in m.get('sources',[])
                    if str(src.get('channel_id') or '')==str(channel)
                    and int(src.get('lead_id') or 0) in history_leads and src.get('external_id')}
                if external_ids:
                    cur.execute("""SELECT id,created_at,direction FROM saas_crm_messages
                      WHERE tenant_id=%s::uuid AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s
                        AND external_id=ANY(%s::text[]) AND direction IN ('incoming','outgoing')""",
                      (tenant,history_leads,row['history_channel'],list(external_ids)))
                    captured_group=cur.fetchall()
            participant_names={};employee_participants=set()
            if provider == 'connect' and dialog.get('customer_kind') == 'group':
                # The override's primary key includes the tenant, exact Connect
                # channel and exact conversation/JID.  This deliberately does
                # not build a global LID/phone contact directory.
                participant_ids=[];employee_participants=set()
                for message in messages:
                    participant_jid, _, _ = _participant_fields(message.get('raw'))
                    if participant_jid and participant_jid not in participant_ids:
                        participant_ids.append(participant_jid)
                if participant_ids:
                    from tenant_ai_group_coordination import verified_employee
                    for message in messages:
                        participant_jid, _, _ = _participant_fields(message.get('raw'))
                        source_channel=str(message.get('source_channel_id') or message.get('channel','').removeprefix('connect:'))
                        if participant_jid and verified_employee(cur,tenant,{'participantJid':participant_jid,'channelId':source_channel}) is not None:
                            employee_participants.add((source_channel,participant_jid))
                if participant_ids:
                    cur.execute('''SELECT connect_channel_id::text AS channel_id,participant_jid,display_name FROM saas_connect_participant_display_names
                      WHERE tenant_id=%s::uuid AND connect_channel_id::text=ANY(%s::text[]) AND conversation_id=%s
                        AND participant_jid=ANY(%s::text[])''',(tenant,list({m.get('source_channel_id',channel) for m in messages}),conversation,participant_ids))
                    participant_names={(str(item['channel_id']),str(item['participant_jid'])):str(item['display_name']) for item in cur.fetchall()
                                       if str(item.get('display_name') or '')}
            if provider == 'connect':
                from tenant_connect_identity import conflicted
                dialog['identity_conflict'] = conflicted(cur, tenant, channel, conversation)
            from tenant_dialog_access import apply_capabilities
            action_capabilities={}
            apply_capabilities(cur,tenant,policy,dialog,_action_capabilities=action_capabilities)
            from tenant_dialog_lifecycle import decorate as lifecycle_decorate, _targets
            lifecycle_decorate(cur,tenant,actor,dialog,_workflow=workflow,allow_closed_archive=closed_archive,owner_observer=owner_observer,
                search_observer=search_observer or colleague,
                _can_update=not colleague and action_capabilities.get('update',False),_policy=policy)
            if owner_observer:
                # The owner dashboard deliberately retains archive isolation;
                # consult group state only to suppress legacy takeover, never
                # hydrate a colleague's sender routes or grant action rights.
                import tenant_dialog_group_work as group_work
                protected_group=group_work.snapshot(cur,tenant,conversation,**group_work.source_selection(group_modes))
                if protected_group:
                    group_work.decorate(dialog,actor,protected_group,workflow,source=group_mode,
                        admission=False,can_update=False,online=False)
            if group_dialog:
                from tenant_dialog_presence import is_online
                group_admission=acceptance_enabled(cur,tenant)
                group_work.decorate(dialog,actor,group_dialog,workflow,source=group_mode,
                    admission=group_admission,can_update=action_capabilities.get('update',False),
                    online=policy.profile.get('role')=='owner' or is_online(cur,tenant,actor,source_channel=channel),
                    allow_archive_join=closed_archive)
                group_state=dialog['lifecycle'].get('group_work')
                if group_state and not group_state['role'] and not before and not anchor and group_admission:
                    group_state['sources']=group_work.join_options(cur,tenant,actor,policy,conversation,
                        group_dialog,group_modes)
            if provider=='connect' and not shared_archive and not colleague and dialog.get('wecall'):
                from tenant_whatsapp_message_edit import attach_capabilities as attach_message_actions
                attach_message_actions(messages[:50],dialog,actor)
            if not (support_case_only or colleague):
                from tenant_operator_dialog_removal import can_remove
                dialog['can_remove_dialog'] = can_remove(policy, dialog)
            if colleague:
                dialog={key:dialog[key] for key in (
                    'provider','lead_id','channel_id','channel_name','conversation_id','customer_kind',
                    'customer_name','phone','lifecycle_enabled','lifecycle') if key in dialog}
                dialog['colleague_read_only']=True
                dialog['archive_read_only']=True
                dialog.update(can_send=False,can_use_copilot=False,can_edit_display_name=False,
                              controls_allowed=False,available_actions=[],resume_allowed=False,handoff_active=False,
                              unread_count=0)
                for key in list(dialog.get('lifecycle',{})):
                    if key.startswith('can_'):
                        dialog['lifecycle'][key]=False
            if history_merge and not shared_archive and dialog.get('can_send') is True:
                # A companion is a read projection only, even when the actor
                # can act on it by explicitly opening its own separate route.
                exact_messages=[m for m in messages[:50] if (
                    m.get('source_channel_id'), m.get('source_conversation_id'), m.get('source_lead_id')
                ) == (channel, conversation, lead_id)]
                if exact_messages:
                    from tenant_whatsapp_native_reactions import attach as attach_reactions
                    attach_reactions(cur,tenant,exact_messages)
            if (dialog.get('lifecycle') or {}).get('can_transfer'):
                dialog['lifecycle']['transfer_targets']=transfer_targets
            if not (before or anchor or shared_archive or group_dialog) and provider == 'connect':
                from tenant_deal_dialog_links import enabled as deal_links_enabled, proposal
                if deal_links_enabled(tenant) and requested_route == history_scope['canonical_route']:
                    deal_proposal = proposal(cur, tenant, actor, policy, dialog, workflow)
                    if deal_proposal:
                        dialog['deal_proposal'] = deal_proposal
            if before is None and not shared_archive:
                from tenant_dialog_company_binding import read_dialogue_binding
                dialog['company_binding'] = read_dialogue_binding(cur,tenant,actor,policy,dialog)
            elif before is None and closed_archive and not (owner_observer or search_observer or colleague):
                # The shared archive grants history, not access to the company editor.
                # Reuse the contextual list permission on this one exact, authorized route.
                from tenant_beinsystems_operator_access import dialogue_chooser_enabled
                if dialogue_chooser_enabled(tenant) and policy.allows_action('operator_data','view'):
                    from tenant_dialog_company_binding import attach_list_company_names
                    label_card = {**requested_route}
                    attach_list_company_names(cur,tenant,[label_card],policy=policy)
                    if 'dialog_company_name' in label_card:
                        dialog['dialog_company_name'] = label_card['dialog_company_name']
            if before is None and not colleague:
                from tenant_shared_dialog_reminders import dialogue_marker
                reminder=dialogue_marker(cur,tenant,actor,policy,dialog,workflow=workflow,
                    source_active=row.get('channel_active') is True,
                    exact_history=requested_route==history_scope['canonical_route'],
                    view='owner' if owner_observer else 'search' if search_observer else '',
                    include_delivery=not shared_archive)
                if reminder is not None:
                    dialog['shared_reminder']=reminder
            if restricted_ai:
                if not shared_archive:
                    _assignment_display(cur,tenant,policy,[dialog])
                _hide_shared_ai(dialog)
            lifecycle=dialog.get('lifecycle')
            work=(lifecycle or {}).get('group_work') or {}
            group_acceptance=work.get('id') if work.get('can_join') else None
            if (include_operator_events and not shared_archive and provider=='connect' and lifecycle and not anchor
                    and requested_route==history_scope['canonical_route']
                    and (before or group_acceptance or str(lifecycle['revision'])!=str(operator_events_revision))):
                from tenant_dialog_operator_timeline import page as operator_event_page
                operator_timeline=operator_event_page(conn,cur,tenant,channel,conversation,messages=messages,
                    before=identifier(before) if before else None,history_leads=history_leads,
                    history_channel=row['history_channel'],group_channels=group_channels,
                    authorized_before_stamp=merge_timeline_boundary.get('before_stamp'),
                    group_actor=actor if group_dialog else None,group_acceptance=group_acceptance)
                if 'operator_events' in operator_timeline:
                    operator_timeline['operator_events_revision']=lifecycle['revision']
            if (include_shared_reminder_events and not shared_archive and provider=='connect' and not anchor
                    and requested_route==history_scope['canonical_route']):
                from tenant_shared_dialog_reminders import timeline_page
                shared_reminder_timeline=timeline_page(conn,cur,tenant,channel,conversation,lead_id,
                    messages=messages,before=identifier(before) if before else None,history_leads=history_leads,
                    history_channel=row['history_channel'],group_channels=group_channels)
            if include_internal_comments and not anchor and requested_route==history_scope['canonical_route']:
                from tenant_dialog_internal_comments import page as comments_page
                internal_comment_timeline=comments_page(conn,cur,tenant,provider,channel,conversation,lead_id,
                    messages=messages,before=identifier(before) if before else None,history_leads=history_leads,
                    history_channel=row['history_channel'],group_channels=group_channels,
                    authorized_before_stamp=merge_timeline_boundary.get('before_stamp'))
                dialog['can_add_internal_comment']=bool('internal_comments' in internal_comment_timeline
                    and action_capabilities.get('update') and not shared_archive
                    and (not dialog.get('lifecycle_enabled') or workflow and workflow['state']=='accepted'
                         and (workflow['operator_id']==actor
                              or work.get('role') in {'leader','participant'})))
    # Unread cursors use insertion order, while the UI orders history by
    # provider time. Capture only records in this response and on this route;
    # a later arrival must never be consumed by this snapshot.
    eligible=[m for m in messages[:50] if (m.get('direction') in ('incoming','outgoing')
              or (provider=='connect' and m.get('direction')=='system' and m.get('message_type')=='call_missed'))
              and m.get('created_at') and str(m.get('channel') or '')==str(row['history_channel'])
              and int(m.get('source_lead_id') or 0) in history_leads]
    eligible.extend(captured_group)
    read_through=max(eligible,key=lambda m:(m['created_at'],m['id'])) if eligible and not (closed_archive or owner_observer or colleague) else None
    anchor_target=next((str(m['id']) for m in messages[:50] if anchor_row and (str(m['id'])==str(anchor_row['id']) or any(s.get('external_id')==anchor_row['external_id'] for s in m.get('sources',[])))),None)
    from tenant_call_recordings import offer_import
    dialog['test_recording_import_allowed']=not shared_archive and offer_import({**profile,'role':policy.profile.get('role')},dialog)
    from tenant_browser_calls import offer as offer_browser_call
    dialog['browser_call_available']=not shared_archive and offer_browser_call(profile,dialog)
    return {**operator_timeline,**shared_reminder_timeline,**internal_comment_timeline,
            **({'group_read_targets':group_read_targets} if group_read_targets else {}),
            'dialog':dialog,'history_scope':history_scope,'anchor_message_id':anchor_target,'read_through_message_id':str(read_through['id']) if read_through else None,'messages':[_message(r, group=dialog.get('customer_kind') == 'group', participant_names=participant_names,employee_participants=employee_participants,
                                         contact_name=dialog.get('customer_name') if dialog.get('customer_kind') == 'contact' else '',
                                         contact_route=str(dialog.get('conversation_id') or ''))
                                        for r in reversed(messages[:50])],
            'next_before':str(messages[49]['id']) if len(messages)>50 else None}


def save_display_name(profile, payload):
    """Save a tenant/channel-local display override for one existing route."""
    if not isinstance(payload, dict):
        raise TenantLinearError('Некорректный запрос.')
    if str(payload.get('provider') or '') != 'connect':
        raise TenantLinearError('Имя можно изменить только для WhatsApp-диалога.')
    kind=str(payload.get('kind') or '')
    if kind not in {'conversation','participant'}:
        raise TenantLinearError('Некорректный тип отображаемого имени.')
    try:
        lead_id=int(payload.get('lead_id'))
    except (TypeError,ValueError):
        raise TenantLinearError('Диалог не найден.',404) from None
    channel=str(payload.get('channel_id') or '').strip()
    conversation=str(payload.get('conversation_id') or '').strip()
    label=_manual_label(payload.get('display_name'))
    with _connect() as conn:
        ensure_schema(conn)
        from tenant_connect_transport import _schema as ensure_connect_schema
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=access(cur,profile,'dialogs',action='update')
            selected=assert_route(cur,tenant,policy,'connect',conversation,channel,lead_id,action='update')
            if kind == 'conversation':
                cur.execute('''UPDATE saas_connect_conversations SET manual_display_name=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s
                  RETURNING manual_display_name''',(label,tenant,selected['connect_channel_id'],selected['conversation_id']))
                saved=cur.fetchone()
                if not saved: raise TenantLinearError('Диалог не найден.',404)
                result={'kind':'conversation','display_name':str(saved['manual_display_name'] or '')}
            else:
                participant_jid=str(payload.get('participant_jid') or '').strip()
                if not participant_jid or len(participant_jid)>180 or any(ord(char)<32 for char in participant_jid):
                    raise TenantLinearError('Укажите участника группы.')
                if str(selected.get('conversation_kind') or '') != 'group':
                    raise TenantLinearError('Участника можно изменить только в WhatsApp-группе.')
                if label:
                    cur.execute('''INSERT INTO saas_connect_participant_display_names
                      (tenant_id,connect_channel_id,conversation_id,participant_jid,display_name,updated_by)
                      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s)
                      ON CONFLICT(tenant_id,connect_channel_id,conversation_id,participant_jid) DO UPDATE
                      SET display_name=EXCLUDED.display_name,updated_by=EXCLUDED.updated_by,updated_at=now()''',
                      (tenant,selected['connect_channel_id'],selected['conversation_id'],participant_jid,label,actor))
                else:
                    cur.execute('''DELETE FROM saas_connect_participant_display_names WHERE tenant_id=%s::uuid
                      AND connect_channel_id=%s::uuid AND conversation_id=%s AND participant_jid=%s''',
                      (tenant,selected['connect_channel_id'],selected['conversation_id'],participant_jid))
                result={'kind':'participant','participant_jid':participant_jid,'display_name':label}
            conn.commit()
    return result
