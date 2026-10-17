"""Tenant-scoped Connect support queue. Viewing never changes AI conversation state."""
import uuid
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


def create_in_transaction(cur, tenant, channel, conversation, reason, event_key, *, actor=None, description='', source='ai_handoff', lead_id=None):
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
    cur.execute('''INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,reason,description,status,created_by)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s)
      ON CONFLICT(tenant_id,channel_id,conversation_id) WHERE status<>'resolved' DO UPDATE
        SET source=CASE WHEN EXCLUDED.source='ai_handoff' THEN 'ai_handoff' ELSE saas_support_tickets.source END,
        reason=CASE WHEN EXCLUDED.source='ai_handoff' THEN EXCLUDED.reason ELSE saas_support_tickets.reason END,
        status=CASE WHEN EXCLUDED.source='ai_handoff' AND saas_support_tickets.status='new' THEN 'waiting' ELSE saas_support_tickets.status END
      RETURNING id''', (tenant,ticket_id,channel,conversation,source,str(reason or 'Требуется помощь сотрудника')[:1000],
        str(description or reason or 'Требуется помощь сотрудника')[:1000], 'waiting' if source=='ai_handoff' else 'new',actor))
    result_id = str(cur.fetchone()['id'])
    cur.execute('''INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid)
      ON CONFLICT(tenant_id,event_key) DO NOTHING''', (tenant,event_key,result_id))
    return {'id': result_id, 'created': result_id == ticket_id}


def handoff_ticket(cur, job, reason):
    if job.get('provider') != 'connect':
        return None
    return create_in_transaction(cur, str(job['tenant_id']), job['channel_id'],
        job.get('conversation_id') or job.get('chat_id'), reason,
        'handoff:' + str(job['event_fingerprint']))


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
 cs.state AS ai_state,cs.reason AS ai_reason,a.name AS agent_name,COALESCE(cs.agent_id,j.agent_id) AS agent_id,
 a.enabled AS agent_enabled,s.enabled AS company_enabled,s.emergency_stop AS company_emergency_stop,
 company.status AS tenant_status,j.state AS latest_job_state,j.error_code AS latest_job_error,
 ch.active AS channel_active,cs.updated_at AS ai_updated_at,
 NOT EXISTS (SELECT 1 FROM saas_ai_conversation_state legacy WHERE legacy.tenant_id=t.tenant_id AND legacy.provider='connect' AND legacy.channel_id='' AND legacy.conversation_id=t.conversation_id) AS unambiguous
 FROM saas_support_tickets t
 JOIN saas_connect_conversations c ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
 JOIN saas_connect_channels ch ON ch.tenant_id=t.tenant_id AND ch.connect_channel_id=t.channel_id
 JOIN saas_tenants company ON company.id=t.tenant_id
 LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=t.tenant_id
 LEFT JOIN LATERAL (SELECT agent_id,state,error_code FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=t.tenant_id
   AND j0.provider='connect' AND j0.channel_id=t.channel_id::text AND j0.conversation_id=t.conversation_id
   ORDER BY j0.updated_at DESC LIMIT 1) j ON TRUE
 LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=t.tenant_id AND cs.provider='connect' AND cs.conversation_id=t.conversation_id AND cs.channel_id=t.channel_id::text
 LEFT JOIN saas_ai_agents a ON a.tenant_id=t.tenant_id AND a.id=COALESCE(cs.agent_id,j.agent_id)
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


def detail(profile, ticket_id, before=None):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute(_SELECT + ' WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid', (tenant,identifier(ticket_id)))
            ticket = cur.fetchone()
            if not ticket:
                raise TenantLinearError('Обращение не найдено.', 404)
            # Use the exact tenant/channel/conversation binding; no remote CRM history.
            args = [tenant,ticket['lead_id'],'connect:'+str(ticket['channel_id'])]
            clause = ''
            if before:
                before_id = identifier(before)
                clause = ''' AND (COALESCE(happened_at,created_at),id)<(SELECT COALESCE(happened_at,created_at),id
                  FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=%s::uuid)'''
                args += [tenant,ticket['lead_id'],'connect:'+str(ticket['channel_id']),before_id]
            cur.execute('''SELECT id,external_id,direction,body,message_type,happened_at,created_at FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s''' + clause + '''
              ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 51''', tuple(args))
            rows = cur.fetchall()
            cur.execute('''SELECT e.id,e.status,e.result,e.error_code,e.created_at,a.name AS expert_name,r.model
              FROM saas_ai_expert_tasks e JOIN saas_ai_agents a ON a.tenant_id=e.tenant_id AND a.id=e.expert_agent_id
              JOIN saas_ai_agent_runs r ON r.tenant_id=e.tenant_id AND r.id=e.id
              WHERE e.tenant_id=%s::uuid AND e.provider='connect' AND e.channel_id=%s AND e.conversation_id=%s
              ORDER BY e.created_at DESC LIMIT 10''',(tenant,str(ticket['channel_id']),ticket['conversation_id']))
            expert_results=[public(row) for row in cur.fetchall()]
    return {'expert_results':expert_results,'ticket':public(ticket), 'messages':[public(row) for row in reversed(rows[:50])],
            'next_before':str(rows[49]['id']) if len(rows)>50 else None}


def create(profile, data):
    request_id = identifier(data.get('request_id'))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            receipt = create_in_transaction(cur,tenant,data.get('channel_id'),data.get('conversation_id'),
                data.get('reason') or 'Создано сотрудником','manual:'+request_id,actor=actor,
                description=data.get('description',''),source='manual',lead_id=data.get('lead_id'))
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
