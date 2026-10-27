"""Tenant/route-authorized candidates; publication uses existing Knowledge Sources."""
from contextlib import nullcontext
import hashlib
import json
import re
import uuid
from tenant_platform import _connect, _json
from tenant_ai_agents_schema import ensure_schema
from tenant_chat_ai_schema import ensure_schema as chat_schema
from tenant_ai_agents_store import _authorize, _audit, _row, save_knowledge
from tenant_operator_access import access, assert_route
from tenant_linear_policy import TenantLinearError, identifier
from tenant_knowledge_candidates_policy import MAX_MESSAGES, MAX_CONTEXT, card, redact, positive_confirmation, period, moment, approval_reason, language
from tenant_ai_group_coordination import group_jid
from tenant_connect_group_history import page as group_page


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def schema(conn):
    ensure_schema(conn)
    chat_schema(conn)


def authorize(cur,profile):
    tenant,actor=_authorize(cur,profile)
    checked,_,policy=access(cur,profile,'dialogs')
    if checked!=tenant: raise TenantLinearError('Нет доступа.',403)
    return tenant,actor,policy


def route(cur,profile,data):
    tenant,actor,policy=authorize(cur,profile)
    if data.get('provider','connect')!='connect': raise TenantLinearError('Поддерживается только WhatsApp.')
    channel=identifier(data.get('channel_id'));conversation=data.get('conversation_id')
    if not isinstance(conversation,str) or not conversation or len(conversation)>180: raise TenantLinearError('Выберите диалог.')
    chosen=assert_route(cur,tenant,policy,'connect',conversation,channel)
    mode=data.get('history_mode','channel')
    if mode not in ('channel','unified_group') or (mode=='unified_group' and not group_jid(conversation)): raise TenantLinearError('Некорректный режим истории.')
    for source in data.get('source_channels') or [channel]:
        assert_route(cur,tenant,policy,'connect',conversation,identifier(source))
    ticket_id=identifier(data['ticket_id']) if data.get('ticket_id') else None
    ticket=None
    if ticket_id:
        cur.execute('SELECT id,status,completion_outcome,completion_note FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s',(tenant,ticket_id,channel,conversation))
        ticket=cur.fetchone()
        if not ticket: raise TenantLinearError('Обращение недоступно в этом диалоге.',404)
    start,end=period(data)
    binding={'language':language(data.get('language'),profile),'provider':'connect','channel_id':channel,'conversation_id':conversation,'history_mode':mode,'ticket_id':ticket_id}
    if 'approved_channel_ids' in data:
        selected=data['approved_channel_ids']
        if not isinstance(selected,list) or not selected or len(selected)>100:
            raise TenantLinearError('Выберите источники.')
        selected=sorted(set(identifier(c) for c in selected))
        if channel not in selected: raise TenantLinearError('Источник не утверждён.',403)
        binding['approved_channel_ids']=selected
    for flag in ('allow_external_media','allow_paid_asr','allow_vision'):
        if flag in data: binding[flag]=data[flag]
    if data.get('retry_of'):binding['retry_of']=identifier(data['retry_of'])
    if data.get('photo_model'):binding['photo_model']=data['photo_model']
    # These fields are set only by an approved fixed-snapshot media campaign;
    # service-level fragment validation binds them to the stored campaign row.
    if data.get('video_model'):binding['video_model']=str(data['video_model'])[:100]
    if data.get('media_grant_id'):binding['media_grant_id']=identifier(data['media_grant_id'])
    if start: binding.update(period_start=start,period_end=end)
    return tenant,actor,policy,chosen,binding,ticket


def author_role(row):
    raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
    if raw.get('ai_authored') is True or raw.get('run_id') or row.get('author_name')=='AI Agent' or any(v.get('author_name')=='AI Agent' for v in row.get('sources',[])):
        return 'ai'
    if str(row.get('channel') or '').startswith('connect:') and raw.get('verified_employee_id') is not None:
        return 'operator'
    return {'incoming':'customer','outgoing':'operator'}.get(row.get('direction'),'system')


def snapshot(cur,profile,data):
    tenant,actor,policy,chosen,binding,ticket=route(cur,profile,data)
    before=identifier(data['before']) if data.get('before') else None
    start,end=period(binding)
    if binding['history_mode']=='unified_group':
        from datetime import timedelta
        through=(moment(end)-timedelta(microseconds=1)).isoformat() if end else None
        rows=group_page(cur,tenant,binding['conversation_id'],before=before,limit=81,through=through,
                        channel_ids=binding.get('approved_channel_ids'))
        if start: rows=[r for r in rows if moment(r.get('happened_at') or r['created_at'])>=moment(start)]
    else:
        cur.execute('''SELECT m.*,m.kommo_lead_id AS source_lead_id,%s AS source_channel_id
          FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
          AND (%s::timestamptz IS NULL OR COALESCE(m.happened_at,m.created_at)>=%s::timestamptz)
          AND (%s::timestamptz IS NULL OR COALESCE(m.happened_at,m.created_at)<%s::timestamptz)
          AND (%s::uuid IS NULL OR (COALESCE(m.happened_at,m.created_at),m.id)<(
            SELECT COALESCE(x.happened_at,x.created_at),x.id FROM saas_crm_messages x
            WHERE x.tenant_id=%s::uuid AND x.kommo_lead_id=%s AND x.channel=%s AND x.id=%s::uuid))
          ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 81''',
          (binding['channel_id'],tenant,chosen['lead_id'],'connect:'+binding['channel_id'],start,start,end,end,before,tenant,chosen['lead_id'],'connect:'+binding['channel_id'],before))
        rows=cur.fetchall()
    next_before=str(rows[79]['id']) if len(rows)>80 else None
    rows=rows[:MAX_MESSAGES]
    selected=data.get('message_ids')
    if selected is not None:
        if not isinstance(selected,list) or not 1<=len(selected)<=MAX_MESSAGES: raise TenantLinearError('Выберите от 1 до 80 сообщений.')
        ids={identifier(v) for v in selected}
        if ids-{str(r['id']) for r in rows}: raise TenantLinearError('Выбор вне разрешённого окна истории; используйте before.',404)
        rows=[r for r in rows if str(r['id']) in ids]
    names=[chosen.get('contact_name'),chosen.get('phone')]
    channels=set();route_cache={binding['channel_id']:chosen}
    for row in rows:
        raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
        names.extend([row.get('author_name')]+[raw.get(k) for k in ('participantName','pushName','senderName','contactName')])
        for channel in [str(row.get('source_channel_id') or binding['channel_id'])]+[str(s['channel_id']) for s in row.get('sources',[])]:
            if channel not in route_cache: route_cache[channel]=assert_route(cur,tenant,policy,'connect',binding['conversation_id'],channel)
            source=route_cache[channel]
            names.extend([source.get('contact_name'),source.get('phone')]);channels.add(channel)
    refs=[];messages=[];remaining=MAX_CONTEXT
    for row in rows:
        body=redact(row.get('body'),names)[:min(1500,remaining)]
        kind=row.get('message_type') or 'text'
        if not body.strip() and kind=='text': continue
        if kind!='text': body=(body+'\n[Attachment pending_processing: contents not read]').strip()
        raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
        role=author_role(row)
        ref={'id':str(row['id']),'channel_id':str(row.get('source_channel_id') or binding['channel_id']),'lead_id':int(row.get('source_lead_id') or row['kommo_lead_id']),'role':role,'body_hash':digest(str(row.get('body') or '')),'text_limit':len(body)}
        if kind!='text':
            # Provider media can be replaced while retaining a message ID.  A
            # hash of safe identity/size/version facts fences receipt reuse
            # without storing a URL or raw provider payload.
            media_version=digest({key:raw.get(key) for key in ('mediaId','media_id','file_uuid','fileUuid','fileLength','file_size','size_bytes','mediaKeyTimestamp','timestamp')})
            ref.update(media_kind=kind,external_id=row.get('external_id'),media_version=media_version,happened_at=str(row.get('happened_at') or row.get('created_at') or ''))
        refs.append(ref);messages.append({'ref':'m'+str(len(refs)),'role':role,'text':body});remaining-=len(body)
        if remaining<=0: break
    if not refs:
        error=TenantLinearError('В выбранном фрагменте нет доступного текста.',409)
        error.next_before=next_before
        raise error
    refs.reverse();messages.reverse()
    for index,message in enumerate(messages): message['ref']='m'+str(index+1)
    if remaining<=0 and len(refs)<len(rows): next_before=refs[0]['id']
    binding['source_channels']=sorted(channels)
    return {'tenant':tenant,'actor':actor,'route':binding,'refs':refs,'messages':messages,'source_digest':digest(messages),'next_before':next_before,'ticket':{'status':ticket['status'],'completion_outcome':ticket.get('completion_outcome') or '','completion_note':ticket.get('completion_note') or ''} if ticket else None}


_MEDIA_IDENTITY_FIELDS=('mediaId','media_id','file_uuid','fileUuid','fileLength','file_size','size_bytes','mediaKeyTimestamp','timestamp')


def evidence(cur,profile,extraction,names_out=None,*,owner_diagnostics=False):
    tenant,_,policy,_,_,_=route(cur,profile,extraction['route'])
    reviewed_refs={};review_rows={}
    if extraction.get('_candidate_id'):
        cur.execute('''SELECT DISTINCT ON (message_id) revision,reviewed_ref,source_hash,receipt_source_hash
          FROM saas_knowledge_case_source_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid
          ORDER BY message_id,revision DESC''',(tenant,extraction['_candidate_id']))
        for review in cur.fetchall():
            reviewed_refs[str(review['reviewed_ref']['id'])]=review['reviewed_ref'];review_rows[str(review['reviewed_ref']['id'])]=review
    items=[];names=[];stale_media=[]
    source_refs=extraction['source_refs']
    candidate_source_ids=extraction.get('_candidate_source_ids')
    if candidate_source_ids is not None:
        source_refs=[ref for ref in source_refs if str(ref.get('id')) in candidate_source_ids]
    for ref in source_refs:
        if 'approved_channel_ids' in extraction['route'] and ref['channel_id'] not in extraction['route']['approved_channel_ids']:
            raise TenantLinearError('Источник не утверждён.',403)
        source=assert_route(cur,tenant,policy,'connect',extraction['route']['conversation_id'],ref['channel_id'])
        cur.execute('SELECT id,body,direction,author_name,raw,message_type,happened_at,external_id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s AND channel=%s FOR SHARE',
            (tenant,ref['id'],source['lead_id'],'connect:'+ref['channel_id']))
        row=cur.fetchone()
        if not row or digest(str(row.get('body') or ''))!=ref['body_hash']:
            raise TenantLinearError('Исходное сообщение изменилось или больше недоступно.',409)
        used_review=reviewed_refs.get(str(ref['id']))
        effective_ref=ref
        if ref.get('media_kind'):
            raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
            current_facts={key:raw.get(key) for key in _MEDIA_IDENTITY_FIELDS}
            version=digest(current_facts)
            current_ref={**ref,'external_id':row.get('external_id'),'media_kind':str(row.get('message_type') or ''),'media_version':version}
            changed_original=row.get('message_type')!=ref.get('media_kind') or version!=ref.get('media_version')
            if used_review:
                from tenant_media_evidence import source_hash
                current_hash=source_hash(current_ref)
                if (current_ref['media_kind']==used_review['media_kind'] and version==used_review['media_version']
                    and current_hash==review_rows[str(ref['id'])]['source_hash']):
                    effective_ref=current_ref
                else:
                    if not owner_diagnostics: raise TenantLinearError('Reviewed attachment source changed; review it again.',409)
                    stale_media.append({'id':str(ref['id']),'channel_id':str(ref['channel_id']),'saved_kind':used_review['media_kind'],
                      'current_kind':current_ref['media_kind'],'saved_version':used_review['media_version'],'current_version':version,'review_expired':True})
            elif changed_original:
                if not owner_diagnostics: raise TenantLinearError('Вложение исходного сообщения изменилось или больше недоступно.',409)
                stale_media.append({'id':str(ref['id']),'channel_id':str(ref['channel_id']),
                  'saved_kind':str(ref.get('media_kind') or ''),'current_kind':str(row.get('message_type') or ''),
                  'saved_version':str(ref.get('media_version') or ''),'current_version':version,
                  'identity_fields':{key:{'original_value_retained':False,'current_present':current_facts.get(key) is not None}
                    for key in _MEDIA_IDENTITY_FIELDS}})
        names.extend([source.get('contact_name'),source.get('phone'),row.get('author_name')])
        raw=row.get('raw') or {};names.extend(raw.get(k) for k in ('participantName','pushName','senderName','contactName'))
        items.append((effective_ref,row,source))
    if names_out is not None: names_out.extend(names)
    stale_ids={item['id'] for item in stale_media}
    reviewed_ids=set(reviewed_refs)
    transcripts={r['id']:r.get('transcript','') for r in extraction.get('media_results',[]) if r['id'] not in stale_ids|reviewed_ids and ((r.get('status')=='processed' and r.get('code')=='transcribed') or r.get('audio_status')=='processed')}
    conversation=extraction['route']['conversation_id'];is_group=bool(group_jid(conversation))
    from tenant_operator_dialogs import _message,_participant_fields
    participant_ids=[]
    if is_group:
        for _,row,_ in items:
            jid,_,_=_participant_fields(row.get('raw'))
            if jid and jid not in participant_ids: participant_ids.append(jid)
    participant_names={};verified_staff={}
    if is_group and participant_ids:
        channel_ids=list({str(ref['channel_id']) for ref,_,_ in items})
        cur.execute("SELECT to_regclass('public.saas_connect_participant_display_names') AS relation")
        if cur.fetchone().get('relation'):
            cur.execute('''SELECT connect_channel_id::text AS channel_id,participant_jid,display_name
              FROM saas_connect_participant_display_names WHERE tenant_id=%s::uuid AND conversation_id=%s
                AND connect_channel_id::text=ANY(%s::text[]) AND participant_jid=ANY(%s::text[])''',
              (tenant,conversation,channel_ids,participant_ids))
            participant_names={(str(value['channel_id']),str(value['participant_jid'])):str(value['display_name'])
              for value in cur.fetchall() if str(value.get('display_name') or '')}
        cur.execute("SELECT to_regclass('public.saas_ai_group_staff') AS relation")
        if cur.fetchone().get('relation'):
            cur.execute('''SELECT s.channel_id::text AS channel_id,s.jid,m.display_name FROM saas_ai_group_staff s
              JOIN saas_tenant_members m ON m.tenant_id=s.tenant_id AND m.telegram_id=s.member_id
              WHERE s.tenant_id=%s::uuid AND s.channel_id::text=ANY(%s::text[]) AND s.jid=ANY(%s::text[]) AND m.active=TRUE''',
              (tenant,channel_ids,participant_ids))
            verified_staff={(str(value['channel_id']),str(value['jid'])):str(value['display_name'])
              for value in cur.fetchall() if str(value.get('display_name') or '')}
    def display_name(value,source,row,role):
        value=str(value or '').strip()[:180]
        if role=='ai': return 'AI Agent'
        if not value or value in {'Operator','WhatsApp operator'} or re.fullmatch(r'[+\d\s().-]+',value) or '@' in value:
            return ''
        for technical in (source.get('phone'),row.get('external_id')):
            if technical and value==str(technical): return ''
        return value
    result=[]
    for ref,row,source in items:
        role='ai' if ref.get('role')=='ai' else author_role(row)
        raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
        participant_jid,_,_=_participant_fields(raw)
        staff_name=verified_staff.get((str(ref['channel_id']),participant_jid)) if is_group else None
        if role=='customer' and staff_name: role='operator'
        author_input={**row,'channel':'connect:'+str(ref['channel_id']),'source_channel_id':str(ref['channel_id'])}
        contact_name=(source.get('manual_display_name') or source.get('contact_name') or source.get('display_name')) if not is_group else ''
        author=staff_name or _message(author_input,group=is_group,participant_names=participant_names,contact_name=contact_name).get('author_name')
        result.append({'id':ref['id'],'channel_id':ref['channel_id'],'lead_id':ref['lead_id'],'provider':'connect','conversation_id':conversation,
          'text':redact(row['body'],names)[:ref['text_limit']]+('\n[Transcript] '+redact(transcripts[ref['id']],names)[:4000] if ref['id'] in transcripts else ''),
          'role':role,'sender_name':display_name(author,source,row,role),
          'happened_at':row['happened_at'].isoformat() if row.get('happened_at') else None})
        if ref['id'] in review_rows and ref['id'] not in stale_ids:
            review=review_rows[ref['id']]
            result[-1]['attachment_evidence_status']='owner_reviewed_new_source'
            result[-1]['source_review_revision']=review['revision']
            if review.get('receipt_source_hash'):
                cur.execute('''SELECT result FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
                  AND external_id=%s AND source_hash=%s FOR SHARE''',(tenant,ref['channel_id'],str(row.get('external_id') or ''),review['receipt_source_hash']))
                saved=cur.fetchone();media_result=(saved or {}).get('result') or {}
                current_observation=media_result.get('observation') or media_result.get('transcript')
                if isinstance(current_observation,str) and current_observation.strip():
                    result[-1]['text']+='\n[Saved observation for owner-reviewed current attachment; untrusted] '+redact(current_observation,names)[:4000]
        if ref['id'] in stale_ids:
            result[-1]['attachment_evidence_status']='stale_unverified'
            result[-1]['text']+='\n[Saved attachment analysis is not attributed to the current attachment.]'
    if owner_diagnostics:
        extraction['_owner_media_diagnostics']=stale_media
    return result


def extraction_row(cur,tenant,request_id):
    cur.execute('SELECT *,deadline<now() AS expired FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,identifier(request_id)))
    value=cur.fetchone()
    if not value: raise TenantLinearError('Анализ не найден.',404)
    return value


def public_request(row):
    expired=row['status']=='running' and row.get('expired')
    from tenant_knowledge_media_coverage import project
    coverage=project(row.get('source_refs'),row.get('media_results'), 'unknown' if expired else row['status'])
    coverage['media_coverage']['scope']='extraction_fragment'
    return {'id':str(row['id']),**coverage,
      'error_code':'provider_timeout' if expired else row['error_code'],'route':row['route'],
      'source_message_ids':[ref['id'] for ref in row['source_refs']],'message_count':len(row['source_refs']),
      'next_before':row['route'].get('next_before'),'limits':{'max_messages':MAX_MESSAGES,'max_context_chars':MAX_CONTEXT,'max_cards':5}}


def _safe_client_name(value):
    value=str(value or '').strip()[:180]
    if not value or '@' in value or re.fullmatch(r'[+\d\s().-]+',value): return ''
    return value


def _safe_client_phone(value):
    value=str(value or '').strip()
    if '@' in value or not re.fullmatch(r'\+?[\d\s().-]{6,}',value): return ''
    digits=re.sub(r'\D','',value)
    return '+'+digits if 6<=len(digits)<=18 else ''


def _crm_contact_by_wa_identity(cur,tenant,number):
    digits=re.sub(r'\D','',str(number or ''))
    if not digits: return None
    cur.execute('''SELECT c.id,c.display_name,c.phone,c.company_id,co.name AS company_name
      FROM saas_crm_native_contact_identities i
      JOIN saas_crm_native_contacts c ON c.tenant_id=i.tenant_id AND c.id=i.contact_id AND c.archived_at IS NULL
      LEFT JOIN saas_crm_native_companies co ON co.tenant_id=c.tenant_id AND co.id=c.company_id AND co.archived_at IS NULL
      WHERE i.tenant_id=%s::uuid AND i.identity_type='whatsapp_phone' AND i.identity_value=%s''',(tenant,digits))
    rows=cur.fetchall()
    return rows[0] if len(rows)==1 else None


def _case_customer(cur,tenant,route_value,card,dialog=None):
    """Resolve only tenant-scoped CRM links and provider-verified WhatsApp identities."""
    channel=str(route_value.get('channel_id') or '')
    conversation=str(route_value.get('conversation_id') or '')
    group=group_jid(conversation)
    contact=None;fallback_name='';fallback_phone=''
    group_name='';participants={};participant_count=0
    if group:
        from tenant_operator_dialogs import _safe_group_name, _participant_fields
        group_name=_safe_group_name({**(dialog or {}),'conversation_id':conversation})
        ids=[str(value) for value in card.get('source_message_ids',[]) if value]
        if ids:
            cur.execute('''SELECT m.raw,m.author_name,m.channel FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid
              AND m.id=ANY(%s::uuid[]) AND m.direction='incoming' AND m.channel LIKE 'connect:%%'
              AND EXISTS(SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=m.tenant_id
                AND c.connect_channel_id::text=substring(m.channel from 9) AND c.conversation_id=%s AND c.lead_id=m.kommo_lead_id)''',(tenant,ids,conversation))
            sender_jids=[]; sender_names={}
            for message in cur.fetchall():
                raw=message.get('raw') if isinstance(message.get('raw'),dict) else {}
                jid,_,display=_participant_fields(raw)
                if jid and jid not in sender_jids: sender_jids.append(jid)
                safe_name=_safe_client_name(display or message.get('author_name'))
                if jid and safe_name: sender_names[(str(message.get('channel') or '').removeprefix('connect:'),jid)]=safe_name
            participant_count=len(sender_jids)
            sender_channels=sorted({item[0] for item in sender_names if item[0]})
            cur.execute("SELECT to_regclass('public.saas_connect_participant_display_names') AS relation")
            participant_names_available=bool((cur.fetchone() or {}).get('relation'))
            if participant_names_available and sender_jids and sender_channels:
                cur.execute('''SELECT participant_jid,display_name FROM saas_connect_participant_display_names
                  WHERE tenant_id=%s::uuid AND connect_channel_id::text=ANY(%s::text[]) AND conversation_id=%s AND participant_jid=ANY(%s::text[])''',
                  (tenant,sender_channels,conversation,sender_jids))
                for row in cur.fetchall():
                    safe_name=_safe_client_name(row.get('display_name'))
                    if safe_name:
                        for source_channel in sender_channels:
                            sender_names[(source_channel,str(row['participant_jid']))]=safe_name
            participants={name for name in sender_names.values()}
    if not group and channel and conversation:
        cur.execute('''SELECT c.id,c.display_name,c.phone,c.company_id,co.name AS company_name
          FROM saas_crm_native_contact_conversations x
          JOIN saas_crm_native_contacts c ON c.tenant_id=x.tenant_id AND c.id=x.contact_id AND c.archived_at IS NULL
          LEFT JOIN saas_crm_native_companies co ON co.tenant_id=c.tenant_id AND co.id=c.company_id AND co.archived_at IS NULL
          WHERE x.tenant_id=%s::uuid AND x.provider='connect' AND x.channel_id=%s::uuid AND x.conversation_id=%s''',
          (tenant,channel,conversation))
        contact=cur.fetchone()
        if dialog:
            fallback_name=_safe_client_name(dialog.get('manual_display_name') or dialog.get('contact_name') or dialog.get('display_name'))
            candidate=conversation.split('@',1)[0] if conversation.endswith(('@s.whatsapp.net','@c.us')) else ''
            if candidate.isdecimal(): fallback_phone=_safe_client_phone(candidate)
            fallback_phone=fallback_phone or _safe_client_phone(dialog.get('phone'))
            if not contact:
                contact=_crm_contact_by_wa_identity(cur,tenant,fallback_phone)
    if group:
        return {'customer_name':'','customer_phone':'','company_name':'','conversation_kind':'group',
                'conversation_name':group_name,'participant_count':participant_count,'participants':sorted(participants)}
    name=_safe_client_name(contact.get('display_name')) if contact else fallback_name
    phone=_safe_client_phone(contact.get('phone')) if contact else ''
    phone=phone or fallback_phone
    return {'customer_name':name or phone,'customer_phone':phone,
            'company_name':str(contact.get('company_name') or '') if contact else '',
            'conversation_kind':'direct','conversation_name':'','participant_count':0,'participants':[]}


def _public_media_processing(media_results,source_ids,extraction_status,source_refs=None):
    from tenant_knowledge_media_coverage import project
    source_ids={str(value) for value in source_ids or []}
    if source_refs is None:
        # Preserve the legacy status-only projection for callers without an
        # inventory. It cannot establish aggregate completeness.
        return [{'id':str(item.get('id') or ''),'status':str(item.get('status') or ''),
          'code':str(item.get('code') or ''),'audio_status':str(item.get('audio_status') or ''),
          'extraction_status':str(extraction_status or '')}
          for item in media_results or [] if str(item.get('id') or '') in source_ids]
    refs=source_refs
    return project([r for r in refs if str(r.get('id')) in source_ids],media_results,extraction_status)['media_results']


def public_card(cur,row,content_language='az',source_route=None,tenant=None,dialog=None,media_results=None,extraction_status=None,source_refs=None,include_media_content=False):
    value={**_row(row),**row['content']};value.pop('content',None);value.pop('content_hash',None)
    if value.get('reviewed_at'): value['reviewed_at']=value['reviewed_at'].isoformat()
    value['extraction_id']=str(row['extraction_id'])
    cur.execute('SELECT agent_id,source_id FROM saas_knowledge_candidate_sources WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid ORDER BY agent_id',(row['tenant_id'],row['id']))
    value['publications']=[{k:str(v) for k,v in item.items()} for item in cur.fetchall()]
    cur.execute('''SELECT r.source_id,r.source_title,r.source_hash,s.title AS current_title,s.content_hash AS current_hash,s.active,r.reviewed_by,r.created_at
      FROM saas_knowledge_candidate_knowledge_reviews r
      JOIN saas_ai_knowledge_sources s ON s.tenant_id=r.tenant_id AND s.id=r.source_id
      WHERE r.tenant_id=%s::uuid AND r.candidate_id=%s::uuid''',(row['tenant_id'],row['id']))
    knowledge_review=cur.fetchone()
    value['knowledge_publication']={
      'source_id':str(knowledge_review['source_id']),'title':knowledge_review['source_title'],
      'source_hash':knowledge_review['source_hash'],
      'active':bool(knowledge_review['active']),'reviewed_by':knowledge_review['reviewed_by'],
      'current':knowledge_review['source_hash']==knowledge_review['current_hash'] and knowledge_review['source_title']==knowledge_review['current_title'],
      'created_at':knowledge_review['created_at'].isoformat() if knowledge_review.get('created_at') else None
    } if knowledge_review else None
    value.setdefault('resolution_method','unknown');value.setdefault('operator_actions',[]);value.setdefault('operator_action_evidence',[]);value.setdefault('cause_evidence',[]);value.setdefault('case_kind','technical')
    value.setdefault('clarification_note','');value.setdefault('work_status','unknown')
    sources=value.get('source_message_ids') or []
    # A forwarded/internal operator thread is useful evidence for review, but
    # it is not proof of an original customer support request.  Do not let it
    # become reusable support knowledge until a customer-origin message is
    # actually present in the case evidence.
    cur.execute('''SELECT count(*) FILTER(WHERE direction='incoming' AND NOT (raw ? 'verified_employee_id')) AS customer_messages
      FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])''',(row['tenant_id'],sources))
    value['source_customer_proven']=int((cur.fetchone() or {}).get('customer_messages') or 0)>0
    value['source_provenance']='customer_message_present' if value['source_customer_proven'] else 'original_customer_unproven'
    value.setdefault('start_message_id',sources[0] if sources else '')
    value.setdefault('end_message_id',sources[-1] if sources else '')
    value.setdefault('case_relation','unknown')
    route=source_route or {}
    value['source_route']={key:route[key] for key in ('provider','channel_id','conversation_id','phone') if route.get(key)}
    if media_results is not None:
        value['media_processing']=_public_media_processing(media_results,sources,extraction_status,source_refs)
        if tenant and dialog and source_route is not None:
            coverage=case_media_coverage(cur,tenant,{'route':source_route,'source_refs':source_refs,
              'media_results':media_results,'status':extraction_status},dialog,row['content'],include_content=include_media_content)
            value['media_processing']=coverage['media_results']
            value['media_coverage']=coverage['media_coverage']
            value['media_analysis_status']=coverage['status']

    if tenant:
        value.update(_case_customer(cur,tenant,route,value,dialog))
    # Existing records predate the explicit content-language field. Their extraction
    # route records the language requested when the card was generated.
    value.setdefault('content_language',content_language if content_language in {'ru','az','en'} else 'az')
    reason=approval_reason(value)
    value['result_confirmed']=value['outcome']['status']=='confirmed'
    value['clarification_status']='required' if reason else 'not_required'
    value['can_approve']=value['status'] in {'pending_review','needs_clarification'} and not reason and value['source_customer_proven']
    value['approval_block_reason']=None if value['can_approve'] else ('original_customer_unproven' if not value['source_customer_proven'] and value['status'] in {'pending_review','needs_clarification'} else (reason if value['status'] in {'pending_review','needs_clarification'} else 'already_reviewed'))
    return value


def request_detail(profile,request_id):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,_,_=authorize(cur,profile);row=extraction_row(cur,tenant,request_id);evidence(cur,profile,row)
            chosen=route(cur,profile,row['route'])[3]
            cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND extraction_id=%s::uuid ORDER BY created_at,id',(tenant,row['id']))
            cards=[public_card(cur,r,row.get('route',{}).get('language') or 'az',source_route=row['route'],tenant=tenant,dialog=chosen,media_results=row.get('media_results') or [],extraction_status=row.get('status'),source_refs=row.get('source_refs'),include_media_content=True) for r in cur.fetchall()]
    return {'request':public_request(row),'candidates':cards}


def detail(profile,candidate_id):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=authorize(cur,profile)
            cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,identifier(candidate_id)))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Карточка не найдена.',404)
            run=extraction_row(cur,tenant,row['extraction_id']);run['_candidate_id']=str(row['id'])
            owner_diagnostics=policy.profile.get('role')=='owner'
            items=evidence(cur,profile,run,owner_diagnostics=owner_diagnostics)
            chosen=route(cur,profile,run['route'])[3]
            value=public_card(cur,row,run.get('route',{}).get('language') or 'az',run['route'],tenant,chosen,run.get('media_results') or [],run.get('status'),run.get('source_refs'),include_media_content=True)
            if owner_diagnostics:
                claim_source_ids={str(value) for value in value.get('source_message_ids',[])}
                diagnostics=[item for item in run.pop('_owner_media_diagnostics',[])
                             if str(item.get('id')) in claim_source_ids]
                if diagnostics:
                    value['media_source_diagnostics']=diagnostics
                    value['source_claims_unverified']=True
                    value['can_approve']=False
                    value['approval_block_reason']='source_attachment_changed_review_required'
            cur.execute('''SELECT revision,message_id,reviewed_ref,source_hash,receipt_source_hash,reviewed_by,review_note,created_at
              FROM saas_knowledge_case_source_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid
              ORDER BY revision DESC LIMIT 20''',(tenant,row['id']))
            reviews=cur.fetchall()
            if reviews:
                newest=reviews[0]
                value['media_source_review']={'revision':newest['revision'],'message_id':str(newest['message_id']),
                  'current_kind':newest['reviewed_ref']['media_kind'],'current_version':newest['reviewed_ref']['media_version'],
                  'receipt_reused':bool(newest.get('receipt_source_hash')),'reviewed_at':newest['created_at'].isoformat() if newest.get('created_at') else None,
                  'review_note':newest['review_note']}
                value['source_claims_unverified']=bool(approval_reason(value))
            value['boundary_summary']=boundary_summary(cur,tenant,run['route'],chosen,value)
    return {'candidate':value,'evidence':items,'route':run['route']}


def _source_review_membership(content,source_refs,message_id):
    """One canonical membership predicate shared by source-review reads and writes."""
    case_ids=set()
    for source_id in (content or {}).get('source_message_ids',[]):
        try: case_ids.add(identifier(source_id))
        except TenantLinearError: continue
    extraction_refs=[]
    for ref in source_refs or []:
        try: ref_id=identifier(ref.get('id'))
        except (TenantLinearError,AttributeError): continue
        if ref_id==message_id and ref.get('media_kind'):
            extraction_refs.append(ref)
    return message_id in case_ids, extraction_refs


def source_review_preflight(profile,candidate_id,message_id,expected_version):
    """Owner-only, read-only diagnostic immediately before source-review POST."""
    if type(expected_version) is not int or expected_version<1:
        raise TenantLinearError('Некорректная версия карточки.')
    normalized_message_id=identifier(message_id)
    with _connect() as conn:
        # _connect returns a fresh connection in production.  Roll back any
        # wrapper/test transaction before this function's SELECT-only reads.
        conn.rollback()
        with conn.cursor() as cur:
            tenant,_,policy=authorize(cur,profile)
            if policy.profile.get('role')!='owner':
                raise TenantLinearError('Только владелец может проверять новую provenance вложения.',403)
            key=identifier(candidate_id)
            cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,key))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Карточка не найдена.',404)
            run=extraction_row(cur,tenant,row['extraction_id'])
            in_case,matching_refs=_source_review_membership(row.get('content'),run.get('source_refs'),normalized_message_id)
            in_extraction=bool(matching_refs)
            version_matches=expected_version==row['version']
            reviewable=row['status'] in {'pending_review','needs_clarification'}
            reason=('stale_version' if not version_matches else
                    'not_case_evidence' if not in_case else
                    'not_extraction_media' if not in_extraction else
                    'case_not_reviewable' if not reviewable else 'membership_and_version_match')
            return {'build_marker':'source-review-preflight-v1','candidate_id':str(row['id']),
              'extraction_id':str(row['extraction_id']),
              'candidate_version':row['version'],'expected_version':expected_version,
              'message_id':normalized_message_id,'in_case_evidence':in_case,
              'in_extraction_media':in_extraction,'version_matches':version_matches,
              'case_reviewable':reviewable,'status':'eligible' if reason=='membership_and_version_match' else 'blocked','reason':reason}


def conversation_link(profile,candidate_id,message_id=None,review_run_id=None):
    """Resolve one case evidence message to an authorized dialog route.

    The browser never gets to turn a case ID into an arbitrary conversation.
    Both the case and the exact message are tenant-bound and the saved Connect
    route is checked against the caller's dialog permissions before it is
    returned.
    """
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=authorize(cur,profile)
            key=identifier(candidate_id)
            cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,key))
            row=cur.fetchone()
            if not row:
                raise TenantLinearError('Case is unavailable for this company.',404)
            if row['status']=='archived':
                raise TenantLinearError('Case is archived; its original conversation is unavailable from this link.',410)
            content=row.get('content') or {}
            source_order=[str(value) for value in content.get('source_message_ids',[]) if value]
            source_ids=set(source_order)
            # Old case links contained only ``case_id``.  Resolve their anchor
            # server-side from evidence already bound to this case; never let a
            # missing client parameter become an arbitrary dialog lookup.
            requested=str(message_id or '').strip()
            if requested:
                anchor=identifier(requested)
            else:
                preferred=str(content.get('start_message_id') or '')
                anchor=preferred if preferred in source_ids else (source_order[0] if source_order else None)
                if not anchor:
                    raise TenantLinearError('Case has no available evidence message.',404)
                anchor=identifier(anchor)
            if anchor not in source_ids:
                raise TenantLinearError('The requested message is not evidence for this case.',403)
            run=extraction_row(cur,tenant,row['extraction_id']); saved=run.get('route') or {}
            channel=identifier(saved.get('channel_id')); conversation=str(saved.get('conversation_id') or '')
            if not conversation:
                raise TenantLinearError('Case conversation route is unavailable.',404)
            chosen=assert_route(cur,tenant,policy,'connect',conversation,channel)
            cur.execute('''SELECT id::text AS id FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s
                AND channel='connect:'||%s::text FOR SHARE''',(tenant,anchor,chosen['lead_id'],channel))
            if not cur.fetchone():
                raise TenantLinearError('The requested message is unavailable, archived, or outside the permitted conversation.',404)
            review=None
            if review_run_id not in (None,''):
                review=identifier(review_run_id)
                cur.execute('''SELECT 1 FROM saas_ai_simulation_runs
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND source_message_id=%s::uuid
                    AND channel_id=%s::uuid AND conversation_id=%s FOR SHARE''',(tenant,review,anchor,channel,conversation))
                if not cur.fetchone():
                    raise TenantLinearError('The requested AI Learning review is unavailable for this message.',404)
            return {'case_id':key,'message_id':anchor,'review_run_id':review,
                    'route':{'provider':'connect','lead_id':chosen['lead_id'],'channel_id':str(channel),
                             'conversation_id':conversation}}


def _boundary_context(cur,tenant,run,chosen,start_id,end_id,source_ids):
    channel=str(run.get('channel_id') or '')
    lead_id=int(chosen['lead_id'])
    ids=list(dict.fromkeys([start_id,end_id,*source_ids]))
    cur.execute('''SELECT id::text AS id,COALESCE(happened_at,created_at) AS at FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=ANY(%s::uuid[]) FOR SHARE''',
      (tenant,lead_id,'connect:'+channel,ids))
    rows={str(row['id']):row for row in cur.fetchall()}
    if start_id not in rows or end_id not in rows:
        raise TenantLinearError('Выбранное сообщение недоступно в этом канале или диалоге.',409)
    start=(rows[start_id]['at'],start_id);end=(rows[end_id]['at'],end_id)
    if start>end: raise TenantLinearError('Начальное сообщение должно предшествовать конечному.',409)
    cur.execute('''SELECT count(*) AS message_count FROM saas_crm_messages WHERE tenant_id=%s::uuid
      AND kommo_lead_id=%s AND channel=%s
      AND (COALESCE(happened_at,created_at),id)>=(%s::timestamptz,%s::uuid)
      AND (COALESCE(happened_at,created_at),id)<=(%s::timestamptz,%s::uuid)''',
      (tenant,lead_id,'connect:'+channel,start[0],start_id,end[0],end_id))
    count=int((cur.fetchone() or {}).get('message_count') or 0)
    inside=[message_id for message_id in source_ids if message_id in rows and start<=(rows[message_id]['at'],message_id)<=end]
    return rows,count,inside


def boundary_summary(cur,tenant,run,chosen,value):
    start_id=str(value.get('start_message_id') or '')
    end_id=str(value.get('end_message_id') or '')
    if not start_id or not end_id:
        return {'available':False,'start':None,'end':None,'message_count':None}
    try: _,count,_=_boundary_context(cur,tenant,run,chosen,start_id,end_id,[])
    except TenantLinearError: return {'available':False,'start':None,'end':None,'message_count':None}
    cur.execute('''SELECT id::text AS id,body,direction,author_name,raw,message_type,happened_at,created_at
      FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=ANY(%s::uuid[])''',
      (tenant,int(chosen['lead_id']),'connect:'+str(run.get('channel_id') or ''),[start_id,end_id]))
    messages={str(row['id']):row for row in cur.fetchall()}
    if start_id not in messages or end_id not in messages:
        return {'available':False,'start':None,'end':None,'message_count':None}
    from tenant_operator_dialogs import _participant_fields
    def preview(message):
        raw=message.get('raw') if isinstance(message.get('raw'),dict) else {}
        _,_,participant_name=_participant_fields(raw)
        author=str(participant_name or message.get('author_name') or '').strip()
        if '@' in author or re.fullmatch(r'[+\d\s().-]+',author): author=''
        return {'author':author,'role':'customer' if message.get('direction')=='incoming' else 'operator',
          'at':(message.get('happened_at') or message.get('created_at')).isoformat(),
          'text':str(message.get('body') or '')[:180],
          'message_type':str(message.get('message_type') or 'text')}
    return {'available':True,'start':preview(messages[start_id]),'end':preview(messages[end_id]),'message_count':count}


def listing(profile,data):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,_,_=authorize(cur,profile)
            try: limit=int(data.get('limit',25))
            except (ValueError,TypeError): raise TenantLinearError('Некорректный limit.') from None
            if not 1<=limit<=50: raise TenantLinearError('limit: 1–50.')
            clauses=['c.tenant_id=%s::uuid'];args=[tenant]
            if data.get('channel_id'):
                channel=identifier(data['channel_id'])
                cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(tenant,channel))
                if not cur.fetchone(): raise TenantLinearError('Канал недоступен.',404)
                clauses.append("e.route->>'channel_id'=%s");args.append(channel)
            if data.get('conversation_id'):
                if not data.get('channel_id'): raise TenantLinearError('Для диалога выберите канал.')
                route(cur,profile,data)
                clauses.append("e.route->>'conversation_id'=%s");args.append(data['conversation_id'])
            status=data.get('status')
            if status:
                if status not in {'pending_review','needs_clarification','approved','knowledge_published','rejected','archived','all'}: raise TenantLinearError('Некорректный статус.')
                if status!='all': clauses.append('c.status=%s');args.append(status)
            else:
                clauses.append("c.status NOT IN ('rejected','archived','knowledge_published')")
            if data.get('batch_id'):
                clauses.append('EXISTS(SELECT 1 FROM saas_knowledge_batch_items bi WHERE bi.tenant_id=c.tenant_id AND bi.extraction_id=c.extraction_id AND bi.batch_id=%s::uuid)')
                args.append(identifier(data['batch_id']))
            if data.get('before'):
                clauses.append('(c.created_at,c.id)<(SELECT created_at,id FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid)')
                args.extend([tenant,identifier(data['before'])])
            cur.execute("SELECT c.* FROM saas_knowledge_candidates c JOIN saas_knowledge_extractions e ON e.tenant_id=c.tenant_id AND e.id=c.extraction_id WHERE "+' AND '.join(clauses)+' ORDER BY c.created_at DESC,c.id DESC LIMIT %s',tuple(args+[limit+1]))
            values=cur.fetchall();cards=[];runs={}
            for row in values[:limit]:
                extraction_id=str(row['extraction_id'])
                if extraction_id not in runs:
                    runs[extraction_id]=extraction_row(cur,tenant,extraction_id)
                    runs[extraction_id]['_chosen']=route(cur,profile,runs[extraction_id]['route'])[3]
                source_route=runs[extraction_id].get('route',{})
                cards.append(public_card(cur,row,source_route.get('language') or 'az',source_route,tenant,runs[extraction_id]['_chosen'],runs[extraction_id].get('media_results') or [],runs[extraction_id].get('status'),runs[extraction_id].get('source_refs')))
    return {'candidates':cards,'limit':limit,'next_before':str(values[limit-1]['id']) if len(values)>limit else None}


def enforce_confirmation(value,items):
    by_id={item['id']:item for item in items}
    for item in value.get('operator_action_evidence',[]):
        source=by_id.get(item.get('message_id'))
        if (not source or item.get('message_id') not in value['source_message_ids']
                or type(item.get('action_index')) is not int
                or not 0<=item['action_index']<len(value.get('operator_actions',[]))
                or not item.get('quote') or item['quote'] not in source.get('text','')
                or source.get('role') not in {'customer','operator'}):
            raise TenantLinearError('Подтверждение действия не совпадает с исходным сообщением.',409)
    source_text='\n'.join(by_id[i]['text'] for i in value['source_message_ids'])
    import re
    if value.get('resolution_method','unknown')=='unknown':
        if re.search(r'any\s*desk|удал[её]н|remote desktop',source_text,re.I): value['resolution_method']='remote'
        elif re.search(r'телефон|звон|phone|zəng',source_text,re.I): value['resolution_method']='phone'
    # Missing remote/phone diagnostics must never become a reusable invented instruction.
    if value.get('resolution_method') in {'remote','phone'} and not value.get('operator_actions'):
        value['resolution_steps']=[]
    confirmed=any(by_id[i]['role'] in {'customer','operator'} and positive_confirmation(by_id[i]['text'],value.get('case_kind')=='informational') for i in value['outcome']['message_ids'])
    if value['outcome']['status']=='confirmed' and not confirmed: value['outcome']['status']='unconfirmed'
    cause_evidence=value.get('cause_evidence',[])
    for item in cause_evidence:
        source=by_id.get(item.get('message_id'))
        if (not source or item.get('message_id') not in value['source_message_ids']
                or not item.get('quote') or item['quote'] not in source.get('text','')
                or source.get('role') not in {'customer','operator'}):
            raise TenantLinearError('Подтверждение причины не совпадает с исходным сообщением.',409)
    # A status is never upgraded from the mere presence of a human message.  A
    # reviewer must explicitly select and quote the source that establishes it.
    if value['cause']['status']=='confirmed' and not cause_evidence:
        value['cause']['status']='suspected'
    return value


def publication_text(value,media_warning=False):
    parts=['РУКОВОДСТВО ВЛАДЕЛЬЦА — не подтверждение исхода клиента',value['title'],'Тип кейса: '+value.get('case_kind','technical'),'Симптомы: '+ '; '.join(value['symptoms']),
      'Причина ('+value['cause']['status']+'): '+value['cause']['text'],
      'Решение:\n'+'\n'.join(str(i+1)+'. '+s for i,s in enumerate(value['resolution_steps'])),
      'Состояние работы: '+value.get('work_status','unknown'),
      'Статус результата в исходной переписке: '+value['outcome']['status']+'. '+value['outcome']['text'],
      'Способ решения: '+value.get('resolution_method','unknown'),
       'Действия сотрудника: '+'; '.join(value.get('operator_actions',[])),
       'Продукт и версия из карточки (не являются отдельным доказательством): '+value['product']+'; '+value['product_version']]
    if media_warning:
        parts.insert(1,'Вложения не проверены для этой публикации; сохранённые наблюдения вложений не подтверждают приведённые инструкции.')
    if value.get('owner_additions'):
        parts.append('Дополнение владельца (знание владельца; не подтверждает исход клиента): '+value['owner_additions'])
    return '\n'.join(parts)


def mutate(profile,candidate_id,data,action='edit',*,connection=None):
    with (nullcontext(connection) if connection is not None else _connect()) as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=authorize(cur,profile)
            cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,identifier(candidate_id)))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Карточка не найдена.',404)
            if action in {'archive','restore'}:
                if policy.profile.get('role') not in {'owner','admin'}:
                    raise TenantLinearError('Архивировать и восстанавливать кейсы может только владелец или администратор.',403)
                if type(data.get('expected_version')) is not int or data['expected_version']!=row['version']:
                    raise TenantLinearError('Карточка изменилась. Обновите страницу.',409)
                if action=='archive':
                    if row['status']=='archived': return {'candidate':public_card(cur,row)} if connection is not None else detail(profile,candidate_id)
                    cur.execute("UPDATE saas_ai_knowledge_sources SET active=FALSE WHERE tenant_id=%s::uuid AND provenance->>'candidate_id'=%s",(tenant,str(row['id'])))
                    cur.execute("""UPDATE saas_knowledge_candidates SET status='archived',archived_at=now(),archived_by=%s,
                      archived_from_status=status,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid""",(actor,tenant,row['id']))
                else:
                    if row['status']!='archived': raise TenantLinearError('Восстановить можно только архивный кейс.',409)
                    restored=row.get('archived_from_status') if row.get('archived_from_status') in {'pending_review','needs_clarification','approved','knowledge_published','rejected'} else 'pending_review'
                    cur.execute("""UPDATE saas_knowledge_candidates SET status=%s,archived_at=NULL,archived_by=NULL,archived_from_status=NULL,
                      version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid""",(restored,tenant,row['id']))
                    if restored=='approved': cur.execute("UPDATE saas_ai_knowledge_sources SET active=TRUE WHERE tenant_id=%s::uuid AND provenance->>'candidate_id'=%s",(tenant,str(row['id'])))
                _audit(cur,tenant,actor,'knowledge_candidate_'+action,'knowledge_candidate',row['id'])
                if connection is None: conn.commit()
                else:
                    cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,candidate_id))
                    return {'candidate':public_card(cur,cur.fetchone())}
                return detail(profile,candidate_id)
            if action=='delete':
                if policy.profile.get('role')!='owner':
                    raise TenantLinearError('Только владелец может безвозвратно удалить кейс.',403)
                if set(data)!={'expected_version','confirm_candidate_id','linked_knowledge_action'}:
                    raise TenantLinearError('Некорректное подтверждение удаления.')
                if str(identifier(data.get('confirm_candidate_id')))!=str(row['id']):
                    raise TenantLinearError('Подтвердите удаление именно этой карточки.',409)
                if type(data.get('expected_version')) is not int or data['expected_version']!=row['version']:
                    raise TenantLinearError('Карточка изменилась. Обновите страницу.',409)
                cur.execute('''SELECT source_id::text AS source_id FROM saas_knowledge_candidate_sources
                  WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid
                  UNION SELECT source_id::text AS source_id FROM saas_knowledge_candidate_knowledge_reviews
                  WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid
                  UNION SELECT id::text AS source_id FROM saas_ai_knowledge_sources
                  WHERE tenant_id=%s::uuid AND provenance->>'candidate_id'=%s ORDER BY source_id''',
                  (tenant,row['id'],tenant,row['id'],tenant,str(row['id'])))
                linked_ids=[str(value['source_id']) for value in cur.fetchall()]
                linked_action=data.get('linked_knowledge_action')
                if linked_ids and linked_action not in {'retain','deactivate'}:
                    raise TenantLinearError('Выберите, сохранить ли связанную базу знаний активной или отключить её.',409)
                if not linked_ids and linked_action!='none':
                    raise TenantLinearError('У карточки нет связанной публикации знаний.',409)
                for source_id in linked_ids:
                    cur.execute('SELECT provenance FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,source_id))
                    source=cur.fetchone()
                    if not source:raise TenantLinearError('Связанная база знаний изменилась. Обновите карточку.',409)
                    cur.execute('''SELECT 1 FROM saas_knowledge_candidate_sources WHERE tenant_id=%s::uuid AND source_id=%s::uuid AND candidate_id<>%s::uuid
                      UNION SELECT 1 FROM saas_knowledge_candidate_knowledge_reviews WHERE tenant_id=%s::uuid AND source_id=%s::uuid AND candidate_id<>%s::uuid LIMIT 1''',
                      (tenant,source_id,row['id'],tenant,source_id,row['id']))
                    shared_elsewhere=bool(cur.fetchone())
                    if shared_elsewhere and linked_action=='deactivate':
                        raise TenantLinearError('Связанное знание используется другим кейсом; отключите его отдельно в центре знаний.',409)
                    provenance=source.get('provenance') if isinstance(source.get('provenance'),dict) else {}
                    # An explicit keep/deactivate choice detaches the source
                    # from the soon-to-be-deleted case and removes case claims.
                    provenance={key:value for key,value in provenance.items()
                                if key not in {'candidate_id','extraction_id','source_message_ids','media_source_review','request_hash'}}
                    provenance['retained_after_case_deletion']=True
                    provenance['review_status']='owner_retained_independent'
                    cur.execute('UPDATE saas_ai_knowledge_sources SET active=CASE WHEN %s=%s THEN FALSE ELSE active END,provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
                      (linked_action,'deactivate',_json(provenance),tenant,source_id))
                extraction_id=str(row['extraction_id'])
                cur.execute('SELECT content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND extraction_id=%s::uuid AND id<>%s::uuid FOR UPDATE',(tenant,extraction_id,row['id']))
                siblings=cur.fetchall()
                cur.execute('DELETE FROM saas_knowledge_candidate_knowledge_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid',(tenant,row['id']))
                cur.execute('DELETE FROM saas_knowledge_case_source_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid',(tenant,row['id']))
                cur.execute('DELETE FROM saas_knowledge_candidate_sources WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid',(tenant,row['id']))
                cur.execute('DELETE FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid AND version=%s RETURNING id',(tenant,row['id'],row['version']))
                if not cur.fetchone():raise TenantLinearError('Карточка изменилась. Обновите страницу.',409)
                keep_ids=set()
                for sibling in siblings:
                    content=sibling.get('content') if isinstance(sibling.get('content'),dict) else {}
                    keep_ids.update(str(value) for value in content.get('source_message_ids',[]) if value)
                    keep_ids.update(str(content.get(key)) for key in ('start_message_id','end_message_id') if content.get(key))
                    keep_ids.update(str(value.get('message_id')) for field in ('outcome','cause')
                      for value in (content.get(field,{}).get('message_ids',[]) if field=='outcome' else content.get('cause_evidence',[]))
                      if isinstance(value,dict) and value.get('message_id'))
                cur.execute('SELECT source_refs,route FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,extraction_id))
                extraction=cur.fetchone()
                if extraction:
                    refs=extraction.get('source_refs') if isinstance(extraction.get('source_refs'),list) else []
                    refs=[ref for ref in refs if str(ref.get('id') or '') in keep_ids]
                    cur.execute('''UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,
                      media_results=COALESCE((SELECT jsonb_agg(item) FROM jsonb_array_elements(media_results) item
                        WHERE item->>'id'=ANY(%s::text[])),'[]'::jsonb),source_digest=%s,
                      route=CASE WHEN %s THEN '{}'::jsonb ELSE route END WHERE tenant_id=%s::uuid AND id=%s::uuid''',
                      (_json(refs),list(keep_ids),digest(refs),not siblings,tenant,extraction_id))
                # Raw model proposal may include the deleted candidate. Current
                # candidate rows are the durable review records; usage/spend
                # ledgers and the AI run itself remain untouched.
                cur.execute('UPDATE saas_ai_agent_runs SET result=NULL WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,extraction_id))
                _audit(cur,tenant,actor,'knowledge_candidate_deleted','knowledge_candidate',row['id'],
                  {'linked_knowledge_action':linked_action,'linked_source_ids':linked_ids,'sibling_cases_retained':len(siblings)})
                if connection is None:conn.commit()
                return {'deleted':True,'candidate_id':str(row['id']),'linked_knowledge_action':linked_action}
            if action=='source_review':
                if policy.profile.get('role')!='owner': raise TenantLinearError('Только владелец может проверить новую provenance вложения.',403)
                if set(data)!={'expected_version','message_id','review_note','acknowledge_new_source'}:
                    raise TenantLinearError('Некорректный запрос проверки источника.')
                if type(data.get('expected_version')) is not int or data['expected_version']!=row['version']:
                    raise TenantLinearError('Карточка изменилась. Обновите страницу.',409)
                if data.get('acknowledge_new_source') is not True:
                    raise TenantLinearError('Подтвердите, что текущая версия рассматривается как новый источник без доказанной эквивалентности.',409)
                note=data.get('review_note')
                if not isinstance(note,str) or not 20<=len(note.strip())<=1000:
                    raise TenantLinearError('Опишите проверку нового источника (20–1000 символов).')
                safe_note=redact(note.strip())[:1000]
                if len(safe_note)<20:
                    raise TenantLinearError('Добавьте достаточно подробностей без контактных данных или секретов.')
                if row['status'] not in {'pending_review','needs_clarification'}:
                    raise TenantLinearError('Рассматривать источник можно только до публикации.',409)
                run=extraction_row(cur,tenant,row['extraction_id']);binding=run['route']
                media_id=identifier(data['message_id'])
                in_case,matching_refs=_source_review_membership(row.get('content'),run.get('source_refs'),media_id)
                if not in_case:
                    raise TenantLinearError('Вложение не входит в доказательства этого кейса.',403)
                prior=matching_refs[0] if matching_refs else None
                if not prior: raise TenantLinearError('Вложение не входит в исходную extraction этого кейса.',404)
                if 'approved_channel_ids' in binding and prior['channel_id'] not in binding['approved_channel_ids']:
                    raise TenantLinearError('Источник не утверждён.',403)
                chosen=assert_route(cur,tenant,policy,'connect',binding['conversation_id'],prior['channel_id'])
                cur.execute('''SELECT id,body,direction,author_name,external_id,raw,message_type FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s AND channel=%s FOR SHARE''',
                  (tenant,media_id,chosen['lead_id'],'connect:'+str(prior['channel_id'])))
                source=cur.fetchone()
                if not source or digest(str(source.get('body') or ''))!=prior.get('body_hash'):
                    raise TenantLinearError('Текст исходного сообщения изменился или больше недоступен.',409)
                if author_role(source)!=prior.get('role'):
                    raise TenantLinearError('Автор исходного сообщения изменился или больше не подтверждён.',409)
                kind=str(source.get('message_type') or '')
                from tenant_media_evidence import media_version,source_hash
                raw=source.get('raw') if isinstance(source.get('raw'),dict) else {}
                if not any(raw.get(key) is not None for key in _MEDIA_IDENTITY_FIELDS):
                    raise TenantLinearError('Текущая версия вложения не имеет проверяемых метаданных.',409)
                current_ref={**prior,'id':media_id,'channel_id':str(prior['channel_id']),
                  'external_id':source.get('external_id'),'body_hash':digest(str(source.get('body') or '')),
                  'media_kind':kind,'media_version':media_version(source.get('raw'))}
                if kind not in {'image','video','audio','voice','document'}:
                    raise TenantLinearError('Текущий тип вложения не поддерживается для новой provenance.',409)
                current_hash=source_hash(current_ref)
                if kind==prior.get('media_kind') and current_ref['media_version']==prior.get('media_version'):
                    raise TenantLinearError('Источник не отличается от сохранённой версии.',409)
                cur.execute('SELECT revision,source_hash FROM saas_knowledge_case_source_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid ORDER BY revision DESC LIMIT 1',(tenant,row['id']))
                latest_review=cur.fetchone() or {}
                if latest_review.get('source_hash')==current_hash:
                    raise TenantLinearError('Эта версия вложения уже проверена владельцем.',409)
                revision=int(latest_review.get('revision') or 0)+1
                receipt_hash=None
                cur.execute("SELECT to_regclass('public.saas_media_evidence_receipts') AS relation")
                if cur.fetchone().get('relation'):
                    cur.execute('''SELECT status,result FROM saas_media_evidence_receipts
                      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s AND media_kind=%s FOR SHARE''',
                      (tenant,current_ref['channel_id'],str(current_ref['external_id'] or ''),current_hash,kind))
                    saved=cur.fetchone();saved_result=(saved or {}).get('result') or {}
                    if saved and saved.get('status') in {'processed','partial'} and any(isinstance(saved_result.get(k),str) and saved_result[k].strip() for k in ('observation','transcript')):
                        receipt_hash=current_hash
                snapshot=dict(row['content']);revised=dict(snapshot)
                revised['title']='Review the current support request'
                revised['symptoms']=['Review the current customer request before saving']
                revised['product']='';revised['product_version']='';revised['case_relation']='unknown'
                revised['resolution_method']='unknown'
                revised['cause']={'status':'unknown','text':''};revised['cause_evidence']=[]
                revised['outcome']={'status':'unconfirmed','text':'','message_ids':[]}
                revised['operator_actions']=[];revised['operator_action_evidence']=[];revised['resolution_steps']=[]
                revised['work_status']='unknown'
                revised['clarification_note']='A new attachment source version was reviewed. Reconfirm the cause and outcome from current evidence.'
                cur.execute('''INSERT INTO saas_knowledge_case_source_reviews
                  (tenant_id,candidate_id,revision,extraction_id,message_id,prior_ref,reviewed_ref,source_hash,receipt_source_hash,claims_snapshot,review_note,reviewed_by)
                  VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s,%s)''',
                  (tenant,row['id'],revision,row['extraction_id'],media_id,_json(prior),_json(current_ref),current_hash,receipt_hash,_json(snapshot),safe_note,actor))
                cur.execute('''UPDATE saas_knowledge_candidates SET content=%s::jsonb,status='needs_clarification',version=version+1,updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND version=%s''',(_json(revised),tenant,row['id'],row['version']))
                _audit(cur,tenant,actor,'knowledge_candidate_source_reviewed','knowledge_candidate',row['id'],
                  {'revision':revision,'message_id':media_id,'source_hash':current_hash,'receipt_reused':bool(receipt_hash)})
                if connection is None:
                    conn.commit()
                    return detail(profile,candidate_id)
                cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,row['id']))
                result={'candidate':public_card(cur,cur.fetchone())}
                result['source_review_revision']=revision
                return result
            if action=='mark_knowledge_published':
                if policy.profile.get('role')!='owner':
                    raise TenantLinearError('Отметить публикацию знания может только владелец компании.',403)
                if set(data)!={'expected_version','source_id'}:
                    raise TenantLinearError('Некорректные параметры публикации знания.')
                if type(data.get('expected_version')) is not int or data['expected_version']!=row['version']:
                    raise TenantLinearError('Карточка изменилась. Обновите страницу.',409)
                if row['status'] not in {'pending_review','needs_clarification'}:
                    raise TenantLinearError('Этот статус доступен только для кейса, который ещё не опубликован как подтверждённое решение.',409)
                source_id=identifier(data['source_id'])
                cur.execute('''SELECT id,title,content_hash FROM saas_ai_knowledge_sources
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND active=TRUE
                    AND NOT (COALESCE(provenance,'{}'::jsonb) ? 'candidate_id')
                    AND NOT (COALESCE(provenance,'{}'::jsonb) ? 'instruction_id') FOR SHARE''',(tenant,source_id))
                published_source=cur.fetchone()
                if not published_source:
                    raise TenantLinearError('Активный опубликованный источник знания не найден в этой компании.',409)
                cur.execute('''INSERT INTO saas_knowledge_candidate_knowledge_reviews
                  (tenant_id,candidate_id,source_id,source_title,source_hash,reviewed_by)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s)
                  ON CONFLICT(tenant_id,candidate_id) DO UPDATE SET source_id=EXCLUDED.source_id,
                    source_title=EXCLUDED.source_title,source_hash=EXCLUDED.source_hash,
                    reviewed_by=EXCLUDED.reviewed_by,created_at=now()''',
                  (tenant,row['id'],source_id,published_source['title'],published_source['content_hash'],actor))
                cur.execute('''UPDATE saas_knowledge_candidates SET status='knowledge_published',version=version+1,
                  reviewed_by=%s,reviewed_at=now(),updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND version=%s''',(actor,tenant,row['id'],row['version']))
                _audit(cur,tenant,actor,'knowledge_candidate_knowledge_published','knowledge_candidate',row['id'],
                  {'source_id':str(published_source['id']),'source_title':published_source['title'],'source_hash':published_source['content_hash'],'historical_outcome':(row.get('content') or {}).get('outcome',{}).get('status','unknown')})
                cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,row['id']))
                result={'candidate':public_card(cur,cur.fetchone())}
                if connection is None: conn.commit()
                return result
            if action=='publish_guidance':
                if policy.profile.get('role')!='owner':
                    raise TenantLinearError('Публиковать знание по кейсу может только владелец компании.',403)
                from tenant_knowledge_candidates_policy import FIELDS
                required={'expected_version','agent_ids'}
                if not required<=set(data) or set(data)-required-FIELDS:
                    raise TenantLinearError('Некорректные поля публикации знания.')
                agents=data.get('agent_ids')
                if not isinstance(agents,list) or not 1<=len(agents)<=20:
                    raise TenantLinearError('Выберите от 1 до 20 агентов.')
                normalized_agents=[identifier(v) for v in agents]
                agents=sorted(set(normalized_agents))
                if not agents or len(agents)!=len(normalized_agents):
                    raise TenantLinearError('Выберите агентов без повторов.',409)
                request_hash=digest({k:v for k,v in data.items() if k!='expected_version'})
                if row['status']=='knowledge_published':
                    current=public_card(cur,row)
                    source_ref=(current.get('knowledge_publication') or {}).get('source_id')
                    provenance=None
                    if source_ref:
                        cur.execute("SELECT provenance->>'review_status' AS state,provenance->>'request_hash' AS request_hash FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,source_ref))
                        provenance=cur.fetchone()
                    if (type(data.get('expected_version')) is int and data['expected_version']==row['version']-1
                        and provenance and provenance.get('state')=='owner_reviewed_guidance'
                        and provenance.get('request_hash')==request_hash
                        and agents==sorted(v['agent_id'] for v in current['publications'])):
                        return {'candidate':current}
                    raise TenantLinearError('Кейс уже опубликован; повторный набор адресатов запрещён.',409)
                if type(data.get('expected_version')) is not int or data['expected_version']!=row['version']:
                    raise TenantLinearError('Карточка изменилась. Обновите страницу.',409)
                if row['status'] not in {'pending_review','needs_clarification'}:
                    raise TenantLinearError('Кейс больше не ожидает решения владельца.',409)
                run=extraction_row(cur,tenant,row['extraction_id']);run['_candidate_id']=str(row['id'])
                run['_candidate_source_ids']={identifier(v) for v in row['content'].get('source_message_ids',[])}
                items=evidence(cur,profile,run,owner_diagnostics=True)
                if not any(item.get('role')=='customer' and item.get('id') in run['_candidate_source_ids'] for item in items):
                    raise TenantLinearError('original_customer_unproven',409)
                value=card({**row['content'],**{k:v for k,v in data.items() if k in FIELDS}},
                  [item['id'] for item in items],allow_boundary_outside_sources=True)
                value=enforce_confirmation(value,items)
                if not any(value.get(key) for key in ('symptoms','resolution_steps','operator_actions','owner_additions')):
                    raise TenantLinearError('Добавьте хотя бы одно полезное описание или инструкцию перед публикацией.',409)
                for agent_id in agents:
                    cur.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,agent_id))
                    if not cur.fetchone():raise TenantLinearError('Агент недоступен в этой компании.',404)
                media_warning=bool(run.get('_owner_media_diagnostics'))
                content=publication_text(value,media_warning=media_warning)
                for agent_id in agents:
                    source=save_knowledge(profile,{'title':value['title'],'kind':'text','content':content,
                      'active':True,'agent_id':agent_id,'setup_key':'candidate_'+str(row['id']).replace('-','')},connection=conn)
                    provenance={'candidate_id':str(row['id']),'extraction_id':str(run['id']),
                      'route':run['route'],'source_message_ids':value['source_message_ids'],
                      'approved_by':str(actor),'review_status':'owner_reviewed_guidance',
                      'customer_outcome_status':value['outcome']['status'],
                      'cause_status':value['cause']['status'],
                      'request_hash':request_hash,
                      'media_review_warning':media_warning}
                    cur.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
                      (_json(provenance),tenant,source['id']))
                    cur.execute('''INSERT INTO saas_knowledge_candidate_sources(tenant_id,candidate_id,agent_id,source_id)
                      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid) ON CONFLICT DO NOTHING''',(tenant,row['id'],agent_id,source['id']))
                    cur.execute('''INSERT INTO saas_knowledge_candidate_knowledge_reviews
                      (tenant_id,candidate_id,source_id,source_title,source_hash,reviewed_by)
                      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s)
                      ON CONFLICT(tenant_id,candidate_id) DO UPDATE SET source_id=EXCLUDED.source_id,
                       source_title=EXCLUDED.source_title,source_hash=EXCLUDED.source_hash,
                       reviewed_by=EXCLUDED.reviewed_by,created_at=now()''',
                      (tenant,row['id'],source['id'],value['title'],source['content_hash'],actor))
                cur.execute('''UPDATE saas_knowledge_candidates SET content=%s::jsonb,status='knowledge_published',
                  version=version+1,reviewed_by=%s,reviewed_at=now(),updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND version=%s''',
                  (_json(value),actor,tenant,row['id'],row['version']))
                _audit(cur,tenant,actor,'knowledge_candidate_guidance_published','knowledge_candidate',row['id'],
                  {'agent_ids':agents,'outcome_status':value['outcome']['status'],'cause_status':value['cause']['status'],
                   'media_review_warning':media_warning})
                if connection is not None:
                    cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,row['id']))
                    return {'candidate':public_card(cur,cur.fetchone())}
                conn.commit()
                return detail(profile,candidate_id)
            run=extraction_row(cur,tenant,row['extraction_id']);run['_candidate_id']=str(row['id'])
            # An extraction can contain media for several candidate cards.
            # Validate only the messages this candidate currently claims (or
            # the proposed source set for an edit); stale, unclaimed media in
            # a sibling fragment must not make this case unsaveable. Any stale
            # media that remains in this candidate's evidence still fails in
            # evidence(), including during publication.
            claimed_source_ids=(row.get('content') or {}).get('source_message_ids',[])
            if action=='edit' and isinstance(data.get('source_message_ids'),list):
                claimed_source_ids=data['source_message_ids']
            run['_candidate_source_ids']={identifier(value) for value in claimed_source_ids}
            items=evidence(cur,profile,run)
            if action in {'approve','delegated_approve'}:
                ids=data.get('agent_ids')
                if not isinstance(ids,list) or not 1<=len(ids)<=20: raise TenantLinearError('Выберите от 1 до 20 агентов.')
                agents=sorted(set(identifier(v) for v in ids))
                if row['status']=='approved':
                    existing=public_card(cur,row)
                    if agents==sorted(v['agent_id'] for v in existing['publications']): return {'candidate':existing}
                    raise TenantLinearError('Карточка уже опубликована для другого набора агентов.',409)
            if type(data.get('expected_version')) is not int or data['expected_version']!=row['version']: raise TenantLinearError('Карточка изменилась. Обновите страницу.',409)
            if action=='boundaries' and row['status']=='approved':
                raise TenantLinearError('Границы опубликованного кейса нельзя изменить. Опубликованное знание останется без изменений.',409)
            if action=='boundaries' and policy.profile.get('role') not in {'owner','admin'}:
                raise TenantLinearError('Изменять границы обращения могут только владелец или администратор.',403)
            if row['status'] not in {'pending_review','needs_clarification'}: raise TenantLinearError('Карточка уже рассмотрена.',409)
            if action=='boundaries':
                if set(data)!={'expected_version','start_message_id','end_message_id'}:
                    raise TenantLinearError('Некорректный запрос изменения границ.')
                start_id=identifier(data['start_message_id']);end_id=identifier(data['end_message_id'])
                chosen=assert_route(cur,tenant,policy,'connect',run['route']['conversation_id'],run['route']['channel_id'])
                content=dict(row['content']);source_ids=[identifier(value) for value in content.get('source_message_ids',[])]
                _,message_count,inside=_boundary_context(cur,tenant,run['route'],chosen,start_id,end_id,source_ids)
                if not inside:
                    raise TenantLinearError('Диапазон не включает исходные сообщения этого кейса. Выберите более широкий диапазон.',409)
                inside_set=set(inside);removed=set(source_ids)-inside_set
                invalid=[]
                if any(item.get('message_id') in removed for item in content.get('operator_action_evidence',[])): invalid.append('действий сотрудника')
                if any(item.get('message_id') in removed for item in content.get('cause_evidence',[])) or (content.get('cause',{}).get('status')=='confirmed' and not content.get('cause_evidence')): invalid.append('причины')
                if any(value in removed for value in content.get('outcome',{}).get('message_ids',[])): invalid.append('результата')
                if invalid:
                    raise TenantLinearError('Границы не сохранены: диапазон исключает подтверждение '+', '.join(invalid)+'. Выберите диапазон с нужными сообщениями или отправьте кейс на уточнение.',409)
                updated={**content,'source_message_ids':[message_id for message_id in source_ids if message_id in inside_set],
                    'start_message_id':start_id,'end_message_id':end_id}
                updated=card(updated,[item['id'] for item in items],allow_boundary_outside_sources=True)
                checked=enforce_confirmation(updated,items)
                if checked['cause']['status']!=updated['cause']['status'] or checked['outcome']['status']!=updated['outcome']['status']:
                    raise TenantLinearError('Границы не сохранены: оставшиеся сообщения не подтверждают указанную причину или результат.',409)
                cur.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(updated),tenant,row['id']))
            elif action=='edit':
                patch={k:v for k,v in data.items() if k!='expected_version'}
                from tenant_knowledge_candidates_policy import FIELDS
                if set(patch)-FIELDS: raise TenantLinearError('Неизвестное поле карточки.')
                if 'owner_additions' in patch and policy.profile.get('role')!='owner':
                    raise TenantLinearError('Дополнение владельца может изменить только владелец компании.',403)
                for key in ('start_message_id','end_message_id'):
                    if key in patch and str(patch[key])!=str(row['content'].get(key) or ''):
                        raise TenantLinearError('Границы меняются только в настройках кейса.',403)
                value=card({**row['content'],**patch},[i['id'] for i in items],allow_boundary_outside_sources=True)
                added=set(value.get('source_message_ids',[]))-set(row['content'].get('source_message_ids',[]))
                if added:
                    chosen=assert_route(cur,tenant,policy,'connect',run['route']['conversation_id'],run['route']['channel_id'])
                    rows,_,inside=_boundary_context(cur,tenant,run['route'],chosen,value['start_message_id'],value['end_message_id'],list(added))
                    if set(inside)!=added or set(rows)!=set(added)|{value['start_message_id'],value['end_message_id']}:
                        raise TenantLinearError('Нельзя добавить подтверждение вне выбранных границ обращения.',409)
                value=enforce_confirmation(value,items)
                cur.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(value),tenant,row['id']))
            elif action=='clarify':
                if set(data)-{'expected_version','clarification_note'}: raise TenantLinearError('Неизвестное поле.')
                if not isinstance(data.get('clarification_note'),str) or not data['clarification_note'].strip():
                    raise TenantLinearError('Dəqiqləşdirmə üçün çatışmayan məlumatı qeyd edin.')
                patch=card({**row['content'],'clarification_note':data['clarification_note']},[i['id'] for i in items],allow_boundary_outside_sources=True)
                cur.execute("UPDATE saas_knowledge_candidates SET content=%s::jsonb,status='needs_clarification',version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json(patch),tenant,row['id']))
            elif action=='reject':
                if set(data)-{'expected_version'}: raise TenantLinearError('Неизвестное поле.')
                cur.execute("UPDATE saas_knowledge_candidates SET status='rejected',version=version+1,reviewed_by=%s,reviewed_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(actor,tenant,row['id']))
            elif action in {'approve','delegated_approve'}:
                allowed={'expected_version','agent_ids'}|({'delegation_policy_id','channel_id'} if action=='delegated_approve' else set())
                if set(data)-allowed: raise TenantLinearError('Неизвестное поле.')
                delegation=None
                if action=='delegated_approve':
                    policy_id=identifier(data.get('delegation_policy_id'));channel_id=identifier(data.get('channel_id'))
                    cur.execute('SELECT * FROM saas_ai_training_policies WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,policy_id));delegation=cur.fetchone()
                    if (not delegation or delegation['owner_id']!=actor or delegation.get('scope_kind')!='company'
                        or delegation.get('revoked_at') is not None or delegation.get('expires_at') is not None
                        or 'support_case' not in delegation['kinds'] or 'publish' not in delegation['rights']):
                        raise TenantLinearError('Delegated case publication is unavailable.',403)
                    if str(run['route'].get('channel_id'))!=channel_id: raise TenantLinearError('Case route changed.',403)
                if not any(item.get('role')=='customer' for item in items if item.get('id') in row['content'].get('source_message_ids',[])):
                    raise TenantLinearError('original_customer_unproven',409)
                value=enforce_confirmation(card(row['content'],[i['id'] for i in items],allow_boundary_outside_sources=True),items)
                reason=approval_reason(value)
                if reason: raise TenantLinearError(reason,409)
                for agent_id in agents:
                    cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,agent_id));target=cur.fetchone()
                    if not target: raise TenantLinearError('Агент недоступен в этой компании.',404)
                    if delegation is not None:
                        from tenant_ai_administrator_approvals import _publication_agent_scope
                        if not _publication_agent_scope(target['scopes'],channel_id):raise TenantLinearError('Agent scope changed.',403)
                content=publication_text(value)
                cur.execute('''SELECT revision,source_hash FROM saas_knowledge_case_source_reviews
                  WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid ORDER BY revision DESC LIMIT 1''',(tenant,row['id']))
                reviewed_source=cur.fetchone()
                for agent_id in agents:
                    source=save_knowledge(profile,{'title':value['title'],'kind':'text','content':content,'active':True,'agent_id':agent_id,'setup_key':'candidate_'+str(row['id']).replace('-','')},connection=conn)
                    provenance={'candidate_id':str(row['id']),'extraction_id':str(run['id']),'route':run['route'],'source_message_ids':value['source_message_ids']}
                    if reviewed_source: provenance['media_source_review']={'revision':reviewed_source['revision'],'source_hash':reviewed_source['source_hash']}
                    if delegation is None: provenance['approved_by']=actor
                    else: provenance.update({'authorization_mode':'owner_delegated_policy','delegation_policy_id':str(delegation['id']),'review_status':'policy_delegated_case'})
                    cur.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(provenance),tenant,source['id']))
                    cur.execute('INSERT INTO saas_knowledge_candidate_sources(tenant_id,candidate_id,agent_id,source_id) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid)',(tenant,row['id'],agent_id,source['id']))
                if delegation is None:
                    cur.execute("UPDATE saas_knowledge_candidates SET status='approved',version=version+1,reviewed_by=%s,reviewed_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(actor,tenant,row['id']))
                else:
                    cur.execute("UPDATE saas_knowledge_candidates SET status='approved',version=version+1,reviewed_by=NULL,reviewed_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,row['id']))
            else: raise TenantLinearError('Некорректное действие.')
            audit_action='knowledge_candidate_delegated_publish' if action=='delegated_approve' else 'knowledge_candidate_'+action
            audit_data={'delegation_policy_id':str(delegation['id']),'agent_ids':agents,'channel_id':channel_id} if action=='delegated_approve' else {'message_count':message_count} if action=='boundaries' else {}
            _audit(cur,tenant,actor,audit_action,'knowledge_candidate',row['id'],audit_data)
            if connection is not None:
                cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,candidate_id))
                return {'candidate':public_card(cur,cur.fetchone())}
        conn.commit()
    return detail(profile,candidate_id)


def case_media_coverage(cur,tenant,run,chosen,content,*,include_content=False):
    """Reuse verified case endpoints; never substitute an extraction or history.

    The normal caller already holds route authority. Reads only metadata/text
    for the exact bounded interval; no media bytes, remote downloads or writes.
    """
    from tenant_knowledge_media_coverage import project
    from tenant_media_evidence import annotate_rows,media_version,source_hash,has_visual_result
    execution=run.get('status')
    def uncertain(reason):
        result=project(None,[],execution)
        result['media_coverage'].update(scope='case_interval',boundary_status='uncertain',reason=reason)
        return result
    start=content.get('start_message_id');end=content.get('end_message_id')
    if not start or not end:return uncertain('case_boundaries_missing')
    try:
        anchors,count,_=_boundary_context(cur,tenant,run['route'],chosen,start,end,[])
    except TenantLinearError:return uncertain('case_boundaries_unavailable')
    if count>MAX_MESSAGES:return uncertain('case_boundary_context_limit')
    channel=str(run['route']['channel_id'])
    cur.execute("""SELECT id::text AS id,external_id,body,raw,message_type,channel,
        COALESCE(happened_at,created_at) AS at
      FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
        AND (COALESCE(happened_at,created_at),id)>=(%s::timestamptz,%s::uuid)
        AND (COALESCE(happened_at,created_at),id)<=(%s::timestamptz,%s::uuid)
      ORDER BY COALESCE(happened_at,created_at),id LIMIT %s""",
      (tenant,int(chosen['lead_id']),'connect:'+channel,anchors[start]['at'],start,anchors[end]['at'],end,MAX_MESSAGES))
    messages=cur.fetchall()
    refs=[];receipts=[]
    original={str(r.get('id')):r for r in run.get('source_refs') or []}
    saved={str(r.get('id')):r for r in run.get('media_results') or []}
    messages=[r for r in messages if r['message_type']!='text' or original.get(str(r['id']),{}).get('media_kind')]
    annotate_rows(cur,tenant,messages)
    for message in messages:
        key=str(message['id'])
        ref={'id':key,'channel_id':channel,'external_id':message['external_id'],
          'body_hash':digest(str(message.get('body') or '')),'media_kind':original.get(key,{}).get('media_kind') if message['message_type']=='text' else message['message_type'],
          'media_version':media_version(message.get('raw'))}
        refs.append(ref)
        previous=original.get(key)
        if message['message_type']=='text':
            receipts.append({'id':key,'status':'unknown','code':'source_version_unverified'});continue
        receipt=dict(saved[key]) if previous and key in saved and source_hash(previous)==source_hash(ref) else None
        if receipt and receipt.get('reused') is True:
            # Both legacy stubs and copied reuse payloads are historical
            # snapshots. Neither can authorize evidence after withdrawal.
            receipt=None
        # Legacy apply_reusable stripped result text from extraction stubs.
        # annotate_rows has independently matched the current tenant/channel/
        # external ID/source hash. Enrich from that exact saved receipt only.
        if message.get('media_evidence') and (receipt is None or receipt.get('reused') is True or not has_visual_result(receipt) and not receipt.get('transcript')):
            evidence=message['media_evidence']
            receipt={**evidence,'id':key,'channel_id':channel,'reused':True}
            if include_content and message.get('media_evidence_context'):receipt['observation']=message['media_evidence_context']
        if receipt is not None:receipts.append(receipt)
        elif key in saved:
            code='shared_media_result_unavailable' if saved[key].get('reused') is True and previous and source_hash(previous)==source_hash(ref) else 'source_version_unverified'
            receipts.append({'id':key,'status':'unknown','code':code})
    result=project(refs,receipts,execution,include_content=include_content)
    if include_content:
        names=[chosen.get('contact_name'),chosen.get('phone')]
        for message in messages:
            raw=message.get('raw') if isinstance(message.get('raw'),dict) else {}
            names.extend(raw.get(k) for k in ('participantName','pushName','senderName','contactName'))
        for receipt in result['media_results']:
            payload=receipt.get('result') or {}
            for key in ('observation','transcript'):
                if key in payload:payload[key]=redact(payload[key],names)
            for observation in payload.get('observations',[]):observation['text']=redact(observation['text'],names)
    result['media_coverage'].update(scope='case_interval',boundary_status='verified')
    return result
