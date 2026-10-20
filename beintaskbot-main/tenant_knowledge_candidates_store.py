"""Tenant/route-authorized candidates; publication uses existing Knowledge Sources."""
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
        cur.execute('SELECT id,status FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s',(tenant,ticket_id,channel,conversation))
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
    if start: binding.update(period_start=start,period_end=end)
    return tenant,actor,policy,chosen,binding,ticket


def author_role(row):
    raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
    if raw.get('ai_authored') is True or raw.get('run_id') or row.get('author_name')=='AI Agent' or any(v.get('author_name')=='AI Agent' for v in row.get('sources',[])):
        return 'ai'
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
        if kind!='text': ref.update(media_kind=kind,external_id=row.get('external_id'),happened_at=str(row.get('happened_at') or row.get('created_at') or ''))
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
    return {'tenant':tenant,'actor':actor,'route':binding,'refs':refs,'messages':messages,'source_digest':digest(messages),'next_before':next_before,'ticket':{'status':ticket['status']} if ticket else None}


def evidence(cur,profile,extraction,names_out=None):
    tenant,_,policy,_,_,_=route(cur,profile,extraction['route'])
    items=[];names=[]
    for ref in extraction['source_refs']:
        if 'approved_channel_ids' in extraction['route'] and ref['channel_id'] not in extraction['route']['approved_channel_ids']:
            raise TenantLinearError('Источник не утверждён.',403)
        source=assert_route(cur,tenant,policy,'connect',extraction['route']['conversation_id'],ref['channel_id'])
        cur.execute('SELECT id,body,direction,author_name,raw,message_type,happened_at FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid AND kommo_lead_id=%s AND channel=%s',
            (tenant,ref['id'],source['lead_id'],'connect:'+ref['channel_id']))
        row=cur.fetchone()
        if not row or digest(str(row.get('body') or ''))!=ref['body_hash']: raise TenantLinearError('Исходное сообщение изменилось или больше недоступно.',409)
        names.extend([source.get('contact_name'),source.get('phone'),row.get('author_name')])
        raw=row.get('raw') or {};names.extend(raw.get(k) for k in ('participantName','pushName','senderName','contactName'))
        items.append((ref,row,source))
    if names_out is not None: names_out.extend(names)
    transcripts={r['id']:r.get('transcript','') for r in extraction.get('media_results',[]) if (r.get('status')=='processed' and r.get('code')=='transcribed') or r.get('audio_status')=='processed'}
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
    return result


def extraction_row(cur,tenant,request_id):
    cur.execute('SELECT *,deadline<now() AS expired FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,identifier(request_id)))
    value=cur.fetchone()
    if not value: raise TenantLinearError('Анализ не найден.',404)
    return value


def public_request(row):
    expired=row['status']=='running' and row.get('expired')
    return {'id':str(row['id']),'status':'unknown' if expired else row['status'],
      'error_code':'provider_timeout' if expired else row['error_code'],'route':row['route'],
      'source_message_ids':[ref['id'] for ref in row['source_refs']],'message_count':len(row['source_refs']),
      'media_results':row.get('media_results') or [],'next_before':row['route'].get('next_before'),'limits':{'max_messages':MAX_MESSAGES,'max_context_chars':MAX_CONTEXT,'max_cards':5}}


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
    elif group:
        ids=[str(value) for value in card.get('source_message_ids',[]) if value]
        if ids:
            cur.execute('''SELECT raw,author_name FROM saas_crm_messages WHERE tenant_id=%s::uuid
              AND id=ANY(%s::uuid[]) AND direction='incoming' AND channel LIKE 'connect:%%' ''',(tenant,ids))
            from tenant_operator_dialogs import _participant_fields
            senders={}
            for message in cur.fetchall():
                raw=message.get('raw') if isinstance(message.get('raw'),dict) else {}
                jid,_,display=_participant_fields(raw)
                if jid.endswith(('@s.whatsapp.net','@c.us')):
                    number=jid.split('@',1)[0]
                    if number.isdecimal(): senders[number]=_safe_client_name(display or message.get('author_name'))
            numbers=list(senders)
            if numbers:
                linked=[(match,number) for number in numbers if (match:=_crm_contact_by_wa_identity(cur,tenant,number))]
                linked_contacts={str(match['id']):match for match,_ in linked}
                if len(linked_contacts)==1:
                    contact=next(iter(linked_contacts.values()))
                    linked_numbers={number for _,number in linked}
                    if len(linked_numbers)==1: fallback_phone=_safe_client_phone(next(iter(linked_numbers)))
                elif not linked and len(numbers)==1:
                    fallback_phone=_safe_client_phone(numbers[0]);fallback_name=senders[numbers[0]]
    name=_safe_client_name(contact.get('display_name')) if contact else fallback_name
    phone=_safe_client_phone(contact.get('phone')) if contact else ''
    phone=phone or fallback_phone
    return {'customer_name':name or phone,'customer_phone':phone,
            'company_name':str(contact.get('company_name') or '') if contact else ''}


def public_card(cur,row,content_language='az',source_route=None,tenant=None,dialog=None):
    value={**_row(row),**row['content']};value.pop('content',None);value.pop('content_hash',None)
    if value.get('reviewed_at'): value['reviewed_at']=value['reviewed_at'].isoformat()
    value['extraction_id']=str(row['extraction_id'])
    cur.execute('SELECT agent_id,source_id FROM saas_knowledge_candidate_sources WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid ORDER BY agent_id',(row['tenant_id'],row['id']))
    value['publications']=[{k:str(v) for k,v in item.items()} for item in cur.fetchall()]
    value.setdefault('resolution_method','unknown');value.setdefault('operator_actions',[]);value.setdefault('operator_action_evidence',[]);value.setdefault('cause_evidence',[]);value.setdefault('case_kind','technical')
    value.setdefault('clarification_note','');value.setdefault('work_status','unknown')
    sources=value.get('source_message_ids') or []
    value.setdefault('start_message_id',sources[0] if sources else '')
    value.setdefault('end_message_id',sources[-1] if sources else '')
    value.setdefault('case_relation','unknown')
    route=source_route or {}
    value['source_route']={key:route[key] for key in ('provider','channel_id','conversation_id','phone') if route.get(key)}
    if tenant:
        value.update(_case_customer(cur,tenant,route,value,dialog))
    # Existing records predate the explicit content-language field. Their extraction
    # route records the language requested when the card was generated.
    value.setdefault('content_language',content_language if content_language in {'ru','az','en'} else 'az')
    reason=approval_reason(value)
    value['result_confirmed']=value['outcome']['status']=='confirmed'
    value['clarification_status']='required' if reason else 'not_required'
    value['can_approve']=value['status'] in {'pending_review','needs_clarification'} and not reason
    value['approval_block_reason']=None if value['can_approve'] else (reason if value['status'] in {'pending_review','needs_clarification'} else 'already_reviewed')
    return value


def request_detail(profile,request_id):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,_,_=authorize(cur,profile);row=extraction_row(cur,tenant,request_id);evidence(cur,profile,row)
            cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND extraction_id=%s::uuid ORDER BY created_at,id',(tenant,row['id']))
            cards=[public_card(cur,r,row.get('route',{}).get('language') or 'az') for r in cur.fetchall()]
    return {'request':public_request(row),'candidates':cards}


def detail(profile,candidate_id):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,_,_=authorize(cur,profile)
            cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,identifier(candidate_id)))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Карточка не найдена.',404)
            run=extraction_row(cur,tenant,row['extraction_id']);items=evidence(cur,profile,run)
            chosen=route(cur,profile,run['route'])[3]
            value=public_card(cur,row,run.get('route',{}).get('language') or 'az',run['route'],tenant,chosen)
            value['boundary_summary']=boundary_summary(cur,tenant,run['route'],chosen,value)
    return {'candidate':value,'evidence':items,'route':run['route']}


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
                if status not in {'pending_review','needs_clarification','approved','rejected','all'}: raise TenantLinearError('Некорректный статус.')
                if status!='all': clauses.append('c.status=%s');args.append(status)
            else:
                clauses.append("c.status<>'rejected'")
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
                cards.append(public_card(cur,row,source_route.get('language') or 'az',source_route,tenant,runs[extraction_id]['_chosen']))
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


def publication_text(value):
    return '\n'.join([value['title'],'Тип кейса: '+value.get('case_kind','technical'),'Симптомы: '+ '; '.join(value['symptoms']),
      'Причина ('+value['cause']['status']+'): '+value['cause']['text'],
      'Решение:\n'+'\n'.join(str(i+1)+'. '+s for i,s in enumerate(value['resolution_steps'])),
      'Состояние работы: '+value.get('work_status','unknown'),
      'Результат подтверждён в исходной переписке: '+value['outcome']['text'],
      'Способ решения: '+value.get('resolution_method','unknown'),
       'Действия сотрудника: '+'; '.join(value.get('operator_actions',[])),
       'Продукт и версия из карточки (не являются отдельным доказательством): '+value['product']+'; '+value['product_version']])


def mutate(profile,candidate_id,data,action='edit'):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=authorize(cur,profile)
            cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,identifier(candidate_id)))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Карточка не найдена.',404)
            run=extraction_row(cur,tenant,row['extraction_id']);items=evidence(cur,profile,run)
            if action=='approve':
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
            elif action=='approve':
                if set(data)-{'expected_version','agent_ids'}: raise TenantLinearError('Неизвестное поле.')
                value=enforce_confirmation(card(row['content'],[i['id'] for i in items],allow_boundary_outside_sources=True),items)
                reason=approval_reason(value)
                if reason: raise TenantLinearError(reason,409)
                for agent_id in agents:
                    cur.execute('SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,agent_id))
                    if not cur.fetchone(): raise TenantLinearError('Агент недоступен в этой компании.',404)
                content=publication_text(value)
                for agent_id in agents:
                    source=save_knowledge(profile,{'title':value['title'],'kind':'text','content':content,'active':True,'agent_id':agent_id,'setup_key':'candidate_'+str(row['id']).replace('-','')},connection=conn)
                    cur.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
                        (_json({'candidate_id':str(row['id']),'extraction_id':str(run['id']),'route':run['route'],'source_message_ids':value['source_message_ids'],'approved_by':actor}),tenant,source['id']))
                    cur.execute('INSERT INTO saas_knowledge_candidate_sources(tenant_id,candidate_id,agent_id,source_id) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid)',(tenant,row['id'],agent_id,source['id']))
                cur.execute("UPDATE saas_knowledge_candidates SET status='approved',version=version+1,reviewed_by=%s,reviewed_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(actor,tenant,row['id']))
            else: raise TenantLinearError('Некорректное действие.')
            _audit(cur,tenant,actor,'knowledge_candidate_'+action,'knowledge_candidate',row['id'],{'message_count':message_count} if action=='boundaries' else {})
        conn.commit()
    return detail(profile,candidate_id)
