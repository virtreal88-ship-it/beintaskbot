"""Tenant-scoped Connect support queue. Viewing never changes AI conversation state."""
import uuid
import hashlib
import json
from tenant_ai_dialog_scope import job_scope, lock_dialog
from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agents_store import _audit, _decorate_conversation_state
from tenant_operator_access import authorize as _authorize
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect

STATUSES = {'new', 'waiting', 'in_progress', 'resolved'}


def public(row):
    row=dict(row)
    if 'ai_state' in row:
        effective=_decorate_conversation_state({**row,'provider':'connect','state':row.get('ai_state') or 'active'})
        row['ai_state']=effective['display_state'] if row.get('agent_id') or row.get('ai_state') in {'human','paused'} else 'unmanaged'
        if row.get('unambiguous') is False:
            row['ai_state']='blocked'
            row['resume_allowed']=False
            row['resume_block_reason']='legacy_channel_ambiguous'
    return {key: value.isoformat() if hasattr(value, 'isoformat') else str(value) if isinstance(value, uuid.UUID) else value
            for key, value in dict(row).items()}


def route(cur, tenant, channel, conversation, lead_id=None):
    if lead_id is not None:
        try:
            lead_id = int(lead_id)
        except (TypeError, ValueError):
            raise TenantLinearError('Выберите диалог.') from None
        cur.execute('''SELECT c.*,ch.name AS channel_name FROM saas_connect_conversations c
          JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
          WHERE c.tenant_id=%s::uuid AND c.lead_id=%s AND ch.active=TRUE FOR SHARE OF c,ch''', (tenant, lead_id))
    else:
        channel = identifier(channel)
        conversation = str(conversation or '').strip()
        if not conversation or len(conversation) > 160:
            raise TenantLinearError('Выберите диалог.')
        cur.execute('''SELECT c.*,ch.name AS channel_name FROM saas_connect_conversations c
          JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
          WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s
            AND ch.active=TRUE FOR SHARE OF c,ch''', (tenant, channel, conversation))
    value = cur.fetchone()
    if not value or (channel and str(value['connect_channel_id']) != str(channel)) or (conversation and str(value['conversation_id']) != str(conversation)):
        raise TenantLinearError('Диалог или канал недоступен в этой компании.', 404)
    return dict(value)


def create_in_transaction(cur, tenant, channel, conversation, reason, event_key, *, actor=None, description='', source='ai_handoff', lead_id=None, message_ids=None, bounded_episode=False):
    """Caller initializes schema first; ticket and handoff commit together. No provider calls."""
    tenant = identifier(tenant)
    cur.execute('SELECT status FROM saas_tenants WHERE id=%s::uuid FOR SHARE', (tenant,))
    company = cur.fetchone() or {}
    from tenant_ai_agent_access import tenant_status_allows_management
    if not tenant_status_allows_management(company.get('status')):
        raise TenantLinearError('Компания архивирована или приостановлена.', 403)
    selected = route(cur, tenant, channel, conversation, lead_id)
    channel, conversation = str(selected['connect_channel_id']), selected['conversation_id']
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('support:' + tenant + ':' + channel + ':' + conversation,))
    event_key = str(event_key)
    if not event_key or len(event_key) > 500:
        raise TenantLinearError('Некорректный код события.')
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('support-event:'+tenant+':'+event_key,))
    cur.execute('SELECT e.ticket_id,t.channel_id,t.conversation_id FROM saas_support_events e JOIN saas_support_tickets t ON t.tenant_id=e.tenant_id AND t.id=e.ticket_id WHERE e.tenant_id=%s::uuid AND e.event_key=%s', (tenant, event_key))
    previous = cur.fetchone()
    if previous:
        if str(previous['channel_id']) != channel or previous['conversation_id'] != conversation:
            raise TenantLinearError('Код события уже использован для другого диалога.',409)
        return {'id': str(previous['ticket_id']), 'created': False}
    if not description:
        cur.execute('''SELECT body FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
          AND channel=%s AND direction='incoming' AND body<>''
          ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 1''',
          (tenant,selected['lead_id'],'connect:'+channel))
        latest = cur.fetchone() or {}
        description = str(latest.get('body') or '')[:240]
    ticket_id = str(uuid.uuid4())
    scope_kind = 'selected' if bounded_episode or message_ids is not None else 'snapshot'
    cur.execute('''INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,reason,description,status,created_by,history_scope)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(tenant_id,channel_id,conversation_id)
      WHERE status<>'resolved' AND completion_outcome<>'test_stopped' DO UPDATE
        SET source=CASE WHEN EXCLUDED.source='ai_handoff' THEN 'ai_handoff' ELSE saas_support_tickets.source END,
        reason=CASE WHEN EXCLUDED.source='ai_handoff' THEN EXCLUDED.reason ELSE saas_support_tickets.reason END,
        status=CASE WHEN EXCLUDED.source='ai_handoff' AND saas_support_tickets.status='new' THEN 'waiting' ELSE saas_support_tickets.status END
      RETURNING id''', (tenant,ticket_id,channel,conversation,source,str(reason or 'Требуется помощь сотрудника')[:1000],
        str(description or reason or 'Требуется помощь сотрудника')[:1000], 'waiting' if source=='ai_handoff' else 'new',actor,scope_kind))
    result_id = str(cur.fetchone()['id'])
    if result_id == ticket_id and not bounded_episode:
        from tenant_support_case_context import SNAPSHOT_LIMIT, _selection
        if message_ids is None:
            # Pin a bounded local snapshot once. Later attachment/share actions
            # cannot narrow it, and future unrelated incidents do not leak in.
            cur.execute('''SELECT id FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
                AND direction IN ('incoming','outgoing')
              ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT %s''',
              (tenant, selected['lead_id'], 'connect:' + channel, SNAPSHOT_LIMIT))
            snapshot_ids = [str(row['id']) for row in cur.fetchall()]
        else:
            selection_scope = {'tenant_id': tenant, 'lead_id': int(selected['lead_id']),
                               'channel_id': channel, 'kind': 'conversation'}
            snapshot_ids = [row['id'] for row in _selection(cur, selection_scope, message_ids, 80)]
        if snapshot_ids:
            cur.execute('''INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key)
              SELECT %s::uuid,%s::uuid,m.id,%s FROM saas_crm_messages m
              WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
                AND m.id=ANY(%s::uuid[]) ON CONFLICT DO NOTHING''',
              (tenant, ticket_id, 'case-snapshot:' + event_key, tenant, selected['lead_id'],
               'connect:' + channel, snapshot_ids))
    cur.execute('''INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid)
      ON CONFLICT(tenant_id,event_key) DO NOTHING''', (tenant,event_key,result_id))
    return {'id': result_id, 'created': result_id == ticket_id}


def record_case_message_membership(cur, tenant, ticket_id, channel_id, conversation_id,
                                  external_message_ids, event_key):
    """Attach an explicitly bounded set of inbound and agent-reply messages to one ticket."""
    if not isinstance(external_message_ids,(list,tuple)) or not external_message_ids:
        return 0
    values=[]
    for value in external_message_ids[:30]:
        text=str(value or '').strip()
        if text.startswith('connect:'):text=text[len('connect:'):]
        if text and len(text)<=200 and text not in values:values.append(text)
    if not values:return 0
    cur.execute('''SELECT c.lead_id FROM saas_support_tickets t JOIN saas_connect_conversations c
      ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
      WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid AND t.channel_id=%s::uuid AND t.conversation_id=%s''',
      (tenant,ticket_id,channel_id,conversation_id))
    route=cur.fetchone()
    if not route:return 0
    cur.execute('''INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key)
      SELECT %s::uuid,%s::uuid,m.id,%s FROM saas_crm_messages m
      WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
        AND m.direction IN ('incoming','outgoing') AND m.message_type IN ('text','image','video','audio','voice','document')
        AND m.external_id=ANY(%s::text[])
      ON CONFLICT(tenant_id,ticket_id,message_id) DO NOTHING RETURNING message_id''',
      (tenant,ticket_id,str(event_key or '')[:500],tenant,int(route['lead_id']),
       'connect:'+str(channel_id),list(dict.fromkeys([item for value in values for item in ('connect:'+value,value)]))))
    return len(cur.fetchall())


def record_case_selected_message_ids(cur,tenant,ticket_id,message_ids,event_key):
    """Record the owner's explicit, bounded transcript selection for this CASE."""
    if not isinstance(message_ids,(list,tuple)) or not 1<=len(message_ids)<=30:return 0
    keys=[]
    for value in message_ids:
        try:key=str(uuid.UUID(str(value)))
        except (ValueError,TypeError,AttributeError):return 0
        if key in keys:return 0
        keys.append(key)
    cur.execute('''SELECT t.channel_id::text AS channel_id,t.conversation_id,c.lead_id
      FROM saas_support_tickets t JOIN saas_connect_conversations c
        ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
      WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid''',(tenant,ticket_id))
    route=cur.fetchone()
    if not route:return 0
    cur.execute('''INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key)
      SELECT %s::uuid,%s::uuid,m.id,%s FROM saas_crm_messages m
      WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s AND m.id=ANY(%s::uuid[])
        AND m.direction IN ('incoming','outgoing') AND m.message_type IN ('text','image','video','audio','voice','document')
      ON CONFLICT(tenant_id,ticket_id,message_id) DO NOTHING''',
      (tenant,ticket_id,str(event_key or '')[:500],tenant,int(route['lead_id']),'connect:'+route['channel_id'],keys))
    # Return the number of selected messages verified in this exact
    # conversation, including IDs already linked to the CASE. Callers use
    # this as validation; INSERT's RETURNING count would incorrectly reject an
    # idempotent selection after the ticket renderer had already linked it.
    cur.execute('''SELECT count(*) AS n FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=ANY(%s::uuid[])
        AND direction IN ('incoming','outgoing')
        AND message_type IN ('text','image','video','audio','voice','document')''',
      (tenant,int(route['lead_id']),'connect:'+route['channel_id'],keys))
    return int((cur.fetchone() or {}).get('n') or 0)


def handoff_ticket(cur, job, reason):
    if job.get('provider') != 'connect':
        return None
    description=''
    source_id=str(job.get('source_message_id') or '')
    if source_id:
        cur.execute('''SELECT external_id,body,raw,message_type,channel FROM saas_crm_messages
          WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND external_id=%s
            AND direction='incoming'
          ''',
          (job['tenant_id'],job['lead_id'],'connect:'+str(job['channel_id']),
           source_id if source_id.startswith('connect:') else 'connect:'+source_id))
        source=cur.fetchone()
        if source:
            from tenant_media_evidence import annotate_rows
            annotate_rows(cur,str(job['tenant_id']),[source])
            if ((source.get('media_evidence') or {}).get('has_audio_result')
                and (source.get('media_evidence') or {}).get('status') in {'processed','partial'}):
                description=str(source.get('media_evidence_context') or '')[:1000]
    ids=job.get('case_message_ids') if isinstance(job.get('case_message_ids'),(list,tuple)) else []
    source_id=str(job.get('source_message_id') or '')
    if source_id and source_id not in ids:ids=[*ids,source_id]
    receipt=create_in_transaction(cur, str(job['tenant_id']), job['channel_id'],
        job.get('conversation_id') or job.get('chat_id'), reason,
        'handoff:' + str(job['event_fingerprint']),description=description,bounded_episode=bool(ids))
    if receipt.get('id') and ids:
        record_case_message_membership(cur,str(job['tenant_id']),receipt['id'],job['channel_id'],
          job.get('conversation_id') or job.get('chat_id'),ids,'handoff:'+str(job['event_fingerprint']))
    return receipt


def summarize_created(tenant, receipt):
    if receipt and receipt.get('created'):
        from tenant_support_inbox_summary import summarize
        try:
            summarize(str(tenant), receipt['id'])
        except Exception:
            # Ticket has already committed. An interrupted summary is never replayed automatically.
            import logging
            logging.getLogger(__name__).warning('Support summary unavailable; ticket preserved')


_SELECT = '''SELECT t.*,c.contact_name,c.phone,c.lead_id,COALESCE(lm.last_message_at,c.last_message_at) AS last_message_at,ch.name AS channel_name,m.display_name AS assignee_name,
 cs.state AS ai_state,cs.reason AS ai_reason,a.name AS agent_name,cs.agent_id AS agent_id,
 a.enabled AS agent_enabled,s.enabled AS company_enabled,s.emergency_stop AS company_emergency_stop,
 company.status AS tenant_status,j.state AS latest_job_state,j.error_code AS latest_job_error,
 ch.active AS channel_active,cs.updated_at AS ai_updated_at,
 NOT ai_legacy_blocked(t.tenant_id,t.channel_id::text,t.conversation_id) AS unambiguous
 FROM saas_support_tickets t
 JOIN saas_connect_conversations c ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
 JOIN saas_connect_channels ch ON ch.tenant_id=t.tenant_id AND ch.connect_channel_id=t.channel_id
 JOIN saas_tenants company ON company.id=t.tenant_id
 LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=t.tenant_id
 LEFT JOIN LATERAL (SELECT agent_id,state,error_code FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=t.tenant_id
   AND j0.provider='connect' AND j0.channel_id=t.channel_id::text AND j0.conversation_id=t.conversation_id
   ORDER BY j0.updated_at DESC LIMIT 1) j ON TRUE
 LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=t.tenant_id AND cs.provider='connect' AND cs.conversation_id=t.conversation_id AND cs.channel_id=t.channel_id::text
 LEFT JOIN saas_ai_agents a ON a.tenant_id=t.tenant_id AND a.id=cs.agent_id
 LEFT JOIN saas_tenant_members m ON m.tenant_id=t.tenant_id AND m.telegram_id=t.assigned_to
 LEFT JOIN LATERAL (SELECT COALESCE(happened_at,created_at) AS last_message_at FROM saas_crm_messages
   WHERE tenant_id=t.tenant_id AND kommo_lead_id=c.lead_id AND channel='connect:'||t.channel_id::text
   ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 1) lm ON TRUE'''


def eligible_members(cur, tenant):
    cur.execute('''SELECT telegram_id,display_name,role FROM saas_tenant_members m WHERE tenant_id=%s::uuid AND active=TRUE
      AND (role='owner' OR (role<>'master' AND permissions @> '["support"]'::jsonb) OR (role='admin' AND EXISTS (SELECT 1 FROM saas_tenant_workflow_policies p
        WHERE p.tenant_id=m.tenant_id AND p.policy_key='members'
          AND p.value->(m.telegram_id::text)->>'ai_agents_manage'='true'))) ORDER BY display_name,telegram_id''', (tenant,))
    return [dict(row) for row in cur.fetchall()]


def listing(profile, status='unresolved', assignee='', offset=0, search='', channel_id=''):
    if status not in STATUSES | {'unresolved','all'}:
        raise TenantLinearError('Некорректный статус.')
    offset = max(0, min(int(offset), 1000000))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            where, args = ['t.tenant_id=%s::uuid'], [tenant]
            if status == 'unresolved':
                where.append("t.status<>'resolved'")
            elif status != 'all':
                where.append('t.status=%s'); args.append(status)
            if assignee == 'unassigned':
                where.append('t.assigned_to IS NULL')
            elif assignee:
                where.append('t.assigned_to=%s'); args.append(actor if assignee=='me' else int(assignee))
            if search:
                where.append("concat_ws(' ',c.contact_name,c.phone,t.description,t.id::text) ILIKE %s");args.append('%'+str(search)[:200]+'%')
            if channel_id:
                where.append('t.channel_id=%s::uuid');args.append(identifier(channel_id))
            cur.execute(_SELECT + ' WHERE ' + ' AND '.join(where) + ''' ORDER BY
              (t.status<>'resolved') DESC,(t.source='ai_handoff') DESC,(t.status='waiting') DESC,t.created_at,t.id LIMIT 51 OFFSET %s''', (*args,offset))
            rows = cur.fetchall()
            members = eligible_members(cur, tenant)
            cur.execute("SELECT count(*) AS total FROM saas_support_tickets WHERE tenant_id=%s::uuid AND status<>'resolved'",(tenant,))
            unresolved_count=int(cur.fetchone()['total'])
            cur.execute('SELECT connect_channel_id::text AS id,name FROM saas_connect_channels WHERE tenant_id=%s::uuid ORDER BY name',(tenant,))
            channels=[dict(row) for row in cur.fetchall()]
    return {'tickets': [public(row) for row in rows[:50]], 'members': members, 'channels':channels, 'unresolved_count':unresolved_count,
            'next_offset': offset+50 if len(rows)>50 else None}


def detail(profile, ticket_id, before=None, message_id=None):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute(_SELECT + ' WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid', (tenant,identifier(ticket_id)))
            ticket = cur.fetchone()
            if not ticket:
                raise TenantLinearError('Обращение не найдено.', 404)
            from tenant_support_case_context import load_scope, messages, metadata
            material_scope = load_scope(cur, tenant, ticket_id)
            scope_metadata = metadata(cur, material_scope)
            rows = list(reversed(messages(cur, material_scope, limit=51, before=before)))
            # Keep internal raw/URLs out of the case browser projection.
            fields = ('id','external_id','direction','author_name','body','message_type','happened_at','created_at')
            rows = [{key: row.get(key) for key in fields} for row in rows]
            try:
                from tenant_dialog_ai_context import resolve, binding
                from tenant_support_case_context import fingerprint
                current = resolve(cur, tenant, {'channel_id': str(ticket['channel_id']), 'conversation_id': ticket['conversation_id']})
                ticket['agent_id'] = str(current['agent_id'])
                cur.execute('SELECT name,enabled FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid', (tenant, current['agent_id']))
                agent = cur.fetchone() or {}
                ticket['agent_name'] = agent.get('name') or ''
                ticket['agent_enabled'] = bool(agent.get('enabled'))
                ticket['summary_available'] = bool(agent.get('enabled') and ticket.get('company_enabled')
                                                   and not ticket.get('company_emergency_stop'))
                ticket['summary_current'] = bool(ticket.get('summary_agent_binding')) and (
                    ticket.get('summary_agent_binding') == binding(current)
                    and ticket.get('summary_scope_hash') == fingerprint(cur, material_scope))
            except TenantLinearError:
                ticket['agent_id'] = None
                ticket['agent_name'] = ''
                ticket['summary_available'] = False
                ticket['summary_current'] = False
            cur.execute('''SELECT m.id,m.external_id,m.direction,m.message_type,m.happened_at,m.created_at
              FROM saas_support_ticket_messages cm JOIN saas_crm_messages m
                ON m.tenant_id=cm.tenant_id AND m.id=cm.message_id
              WHERE cm.tenant_id=%s::uuid AND cm.ticket_id=%s::uuid
                AND m.kommo_lead_id=%s AND m.channel=%s
                AND m.message_type IN ('image','audio','voice','video','document')
              ORDER BY cm.created_at DESC,m.id DESC LIMIT 30''',
              (tenant,ticket_id,ticket['lead_id'],'connect:'+str(ticket['channel_id'])))
            attached_media=[public(row) for row in cur.fetchall()]
            focus_message = None
            if message_id:
                # Attachment-derived observations require the same current
                # dialog permission as opening the protected attachment bytes.
                _authorize(cur,profile,'dialogs')
                cur.execute('''SELECT id,external_id,message_type,channel,body,raw FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=%s::uuid
                    AND message_type IN ('image','audio','voice','video','document')''',
                  (tenant,ticket['lead_id'],'connect:'+str(ticket['channel_id']),identifier(message_id)))
                focus_message=cur.fetchone()
                if focus_message and material_scope['kind'] != 'conversation':
                    cur.execute('SELECT 1 FROM saas_support_ticket_messages WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid AND message_id=%s::uuid',(tenant,ticket_id,identifier(message_id)))
                    if not cur.fetchone():focus_message=None
                if not focus_message:raise TenantLinearError('Вложение недоступно в этом обращении.',404)
                focus_message=dict(focus_message)
                # Reuse only the existing exact-source receipt lookup. This is
                # read-only; it neither downloads media nor invokes a model.
                from tenant_media_evidence import annotate_rows
                annotate_rows(cur,tenant,[focus_message])
                focus_message.pop('raw',None);focus_message.pop('body',None);focus_message.pop('channel',None)
            cur.execute('''SELECT e.id,e.status,e.result,e.error_code,e.created_at,a.name AS expert_name,r.model
              FROM saas_ai_expert_tasks e JOIN saas_ai_agents a ON a.tenant_id=e.tenant_id AND a.id=e.expert_agent_id
              JOIN saas_ai_agent_runs r ON r.tenant_id=e.tenant_id AND r.id=e.id
              WHERE e.tenant_id=%s::uuid AND e.provider='connect' AND e.channel_id=%s AND e.conversation_id=%s
              ORDER BY e.created_at DESC LIMIT 10''',(tenant,str(ticket['channel_id']),ticket['conversation_id']))
            expert_results=[public(row) for row in cur.fetchall()]
    return {'expert_results':expert_results,'ticket':public(ticket), 'messages':[public(row) for row in reversed(rows[:50])],
            'attached_media':attached_media,'scope':scope_metadata,
            'focus_message':public(focus_message) if focus_message else None,
            'next_before':str(rows[49]['id']) if len(rows)>50 else None}


def available_case_media(profile,ticket_id,before=None):
    """List media metadata from exactly one ticket source route, newest first."""
    from tenant_operator_access import access,assert_route
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=access(cur,profile,'support')
            if policy.profile.get('role')!='owner' or not policy.allows('dialogs'):
                raise TenantLinearError('Только владелец с доступом к диалогам может просматривать медиа.',403)
            ticket_id=identifier(ticket_id)
            cur.execute('''SELECT t.id,t.channel_id::text AS channel_id,t.conversation_id,c.lead_id
              FROM saas_support_tickets t JOIN saas_connect_conversations c
                ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
              WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid''',(tenant,ticket_id))
            ticket=cur.fetchone()
            if not ticket:raise TenantLinearError('Обращение не найдено.',404)
            route=assert_route(cur,tenant,policy,'connect',ticket['conversation_id'],ticket['channel_id'],ticket['lead_id'])
            args=[tenant,int(route['lead_id']),'connect:'+str(route['connect_channel_id'])]
            cursor_clause=''
            if before:
                cursor_clause=''' AND (COALESCE(m.happened_at,m.created_at),m.id)<(
                  SELECT COALESCE(happened_at,created_at),id FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=%s::uuid)'''
                args.extend([tenant,int(route['lead_id']),'connect:'+str(route['connect_channel_id']),identifier(before)])
            cur.execute('''SELECT m.id,m.external_id,m.direction,m.message_type,m.happened_at,m.created_at
              FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
                AND m.direction IN ('incoming','outgoing') AND m.message_type IN ('image','audio','voice','video','document')'''+cursor_clause+'''
              ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 51''',tuple(args))
            rows=cur.fetchall()
    return {'messages':[public(row) for row in rows[:50]],'next_before':str(rows[49]['id']) if len(rows)>50 else None}


def attach_case_media(profile,ticket_id,message_ids):
    """Attach explicitly selected media from the ticket's exact Connect route; no delivery or analysis."""
    if not isinstance(message_ids,list) or not 1<=len(message_ids)<=10:
        raise TenantLinearError('Выберите от 1 до 10 вложений.')
    try:keys=[str(uuid.UUID(str(value))) for value in message_ids]
    except (ValueError,TypeError,AttributeError):raise TenantLinearError('Некорректный идентификатор вложения.') from None
    if len(set(keys))!=len(keys):raise TenantLinearError('Повторяющиеся вложения.')
    from tenant_operator_access import access,assert_route
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=access(cur,profile,'support')
            if policy.profile.get('role')!='owner' or not policy.allows('dialogs'):
                raise TenantLinearError('Только владелец с доступом к диалогам может привязать вложение.',403)
            ticket_id=identifier(ticket_id)
            cur.execute('''SELECT t.id,t.status,t.channel_id::text AS channel_id,t.conversation_id,c.lead_id
              FROM saas_support_tickets t JOIN saas_connect_conversations c
                ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
              WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid FOR SHARE OF t,c''',(tenant,ticket_id))
            ticket=cur.fetchone()
            if not ticket:raise TenantLinearError('Обращение не найдено.',404)
            if ticket['status']=='resolved':raise TenantLinearError('Обращение уже закрыто.',409)
            route=assert_route(cur,tenant,policy,'connect',ticket['conversation_id'],ticket['channel_id'],ticket['lead_id'])
            cur.execute('''SELECT m.id FROM saas_crm_messages m
              WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s AND m.id=ANY(%s::uuid[])
                AND m.direction IN ('incoming','outgoing')
                AND m.message_type IN ('image','audio','voice','video','document')
                AND EXISTS (SELECT 1 FROM saas_connect_conversations c
                  WHERE c.tenant_id=m.tenant_id AND c.connect_channel_id=%s::uuid
                    AND c.conversation_id=%s AND c.lead_id=m.kommo_lead_id)''',
              (tenant,int(route['lead_id']),'connect:'+str(route['connect_channel_id']),keys,
               str(route['connect_channel_id']),route['conversation_id']))
            eligible={str(row['id']) for row in cur.fetchall()}
            if eligible!=set(keys):raise TenantLinearError('Вложение недоступно в исходном диалоге этого обращения.',404)
            event_key='support-media-picker:'+str(uuid.uuid4())
            if record_case_selected_message_ids(cur,tenant,ticket_id,keys,event_key)!=len(keys):
                raise TenantLinearError('Не удалось привязать выбранные вложения.',409)
            _audit(cur,tenant,actor,'support_ticket_media_attached','support_ticket',ticket_id,
                   {'message_ids':keys,'source':'scoped_dialog_picker'})
        conn.commit()
    return {'ticket_id':ticket_id,'attached_message_ids':keys,'attached_count':len(keys)}


def create(profile, data):
    if not isinstance(data, dict) or set(data) - {'request_id','lead_id','channel_id','conversation_id','reason','description','message_ids'}:
        raise TenantLinearError('Некорректные поля обращения.')
    if 'message_ids' in data and (not isinstance(data['message_ids'], list) or not 1 <= len(data['message_ids']) <= 80):
        raise TenantLinearError('Выберите от 1 до 80 сообщений обращения.')
    request_id = identifier(data.get('request_id'))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            receipt = create_in_transaction(cur,tenant,data.get('channel_id'),data.get('conversation_id'),
                data.get('reason') or 'Создано сотрудником','manual:'+request_id,actor=actor,
                description=data.get('description',''),source='manual',lead_id=data.get('lead_id'),message_ids=data.get('message_ids'))
            _audit(cur,tenant,actor,'support_create','support_ticket',receipt['id'])
        conn.commit()
    summarize_created(tenant,receipt)
    return detail(profile,receipt['id'])


def update(profile, ticket_id, data):
    if set(data)-{'status','assigned_to','description','next_steps','suggestions'}:
        raise TenantLinearError('Некорректные поля обращения.')
    if 'status' in data and data['status'] not in STATUSES:
        raise TenantLinearError('Некорректный статус.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            ticket_id = identifier(ticket_id)
            if 'assigned_to' in data and data['assigned_to'] is not None:
                assigned = int(data['assigned_to'])
                if assigned not in {int(m['telegram_id']) for m in eligible_members(cur,tenant)}:
                    raise TenantLinearError('Сотрудник не имеет доступа к очереди этой компании.',403)
                # Keep membership stable until the update commits.
                _authorize(cur,{'tenant_id':tenant,'telegram_id':assigned})
            fields, args = [], []
            for key, value in data.items():
                fields.append(key+'=%s')
                if key=='assigned_to': value = int(value) if value is not None else None
                elif key!='status':
                    if not isinstance(value,str) or len(value)>4000: raise TenantLinearError('Слишком длинный текст.')
                args.append(value)
            if not fields: raise TenantLinearError('Нет изменений.')
            if 'status' in data:
                fields.append("resolved_at=CASE WHEN %s='resolved' THEN now() ELSE NULL END");args.append(data['status'])
            try:
                cur.execute('UPDATE saas_support_tickets SET '+','.join(fields)+',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING id', (*args,tenant,ticket_id))
            except Exception as error:
                if getattr(error,'sqlstate',None) != '23505':
                    raise
                raise TenantLinearError('В этом диалоге уже есть открытое обращение.',409) from None
            if not cur.fetchone(): raise TenantLinearError('Обращение не найдено.',404)
            _audit(cur,tenant,actor,'support_update','support_ticket',ticket_id,data)
        conn.commit()
    return detail(profile,ticket_id)


def complete(profile, data):
    """Close one Connect support request with an explicit staff outcome."""
    allowed={'request_id','lead_id','channel_id','conversation_id','outcome','note','propose_knowledge'}
    if not isinstance(data,dict) or set(data)-allowed:
        raise TenantLinearError('Некорректные параметры завершения обращения.')
    outcome=data.get('outcome')
    if outcome not in {'resolved_confirmed','recommendations_unknown','unresolved','informational'}:
        raise TenantLinearError('Выберите результат обращения.')
    note=data.get('note','')
    if not isinstance(note,str) or len(note)>1200: raise TenantLinearError('Комментарий слишком длинный.')
    propose=data.get('propose_knowledge',False)
    if type(propose) is not bool: raise TenantLinearError('Некорректный выбор базы знаний.')
    request_id=identifier(data.get('request_id'))
    channel=identifier(data.get('channel_id'))
    conversation=str(data.get('conversation_id') or '').strip()
    try: lead_id=int(data.get('lead_id'))
    except (TypeError,ValueError): raise TenantLinearError('Выберите диалог.') from None
    if not conversation or len(conversation)>160: raise TenantLinearError('Выберите диалог.')
    shape={'channel_id':channel,'conversation_id':conversation,'lead_id':lead_id,'outcome':outcome,'note':note,'propose_knowledge':propose}
    digest=hashlib.sha256(json.dumps(shape,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            selected=route(cur,tenant,channel,conversation)
            if int(selected.get('lead_id') or 0)!=lead_id:
                raise TenantLinearError('Диалог изменился. Обновите его.',409)
            channel=str(selected['connect_channel_id']);conversation=str(selected['conversation_id'])
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('support-complete:'+tenant+':'+channel+':'+conversation,))
            cur.execute('SELECT actor_id,input_hash,ticket_id,channel_id,conversation_id FROM saas_support_completions WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE',(tenant,request_id))
            previous=cur.fetchone()
            if previous:
                if int(previous['actor_id'])!=actor or previous['input_hash']!=digest or str(previous['channel_id'])!=channel or previous['conversation_id']!=conversation:
                    raise TenantLinearError('Код завершения уже использован для другого обращения.',409)
                ticket_id=str(previous['ticket_id'])
            else:
                cur.execute('''SELECT id FROM saas_support_tickets WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
                  AND status<>'resolved' ORDER BY created_at DESC,id DESC LIMIT 1 FOR UPDATE''',(tenant,channel,conversation))
                ticket=cur.fetchone()
                if ticket:
                    ticket_id=str(ticket['id'])
                else:
                    receipt=create_in_transaction(cur,tenant,channel,conversation,'Завершено сотрудником','completion:'+request_id,
                        actor=actor,description=note[:1000],source='manual',lead_id=lead_id)
                    ticket_id=str(receipt['id'])
                cur.execute('''UPDATE saas_support_tickets SET status='resolved',resolved_at=COALESCE(resolved_at,now()),
                  completion_outcome=%s,completion_note=%s,knowledge_candidate_requested=%s,updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid''',(outcome,note,propose,tenant,ticket_id))
                cur.execute('''INSERT INTO saas_support_completions(tenant_id,request_id,actor_id,channel_id,conversation_id,input_hash,ticket_id)
                  VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s::uuid)''',(tenant,request_id,actor,channel,conversation,digest,ticket_id))
                _audit(cur,tenant,actor,'support_complete','support_ticket',ticket_id,{'outcome':outcome,'knowledge_candidate_requested':propose})
        conn.commit()
    return detail(profile,ticket_id)


def handoff_recorded(cur, job):
    if job.get('provider') != 'connect':
        return False
    # Serialize replay checks with Resume and competing handoff transactions.
    lock_dialog(cur, job['tenant_id'], 'connect', job['channel_id'], job.get('conversation_id') or job.get('chat_id'))
    cur.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s FOR UPDATE",
                (job['tenant_id'],job.get('conversation_id') or job.get('chat_id'),job_scope(job)))
    cur.fetchone()
    cur.execute("SELECT 1 FROM saas_support_events WHERE tenant_id=%s::uuid AND event_key=%s",
                (job['tenant_id'], 'handoff:' + str(job['event_fingerprint'])))
    return bool(cur.fetchone())
