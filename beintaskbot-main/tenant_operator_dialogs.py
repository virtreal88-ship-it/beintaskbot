"""Read projection over existing conversations, AI state, tickets and cached messages. No new inbox tables."""
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
    name = str(item.get('customer_name') or '').strip()
    conversation = str(item.get('conversation_id') or '').strip()
    phone = str(item.get('phone') or '').strip()
    technical = {conversation, conversation.split('@', 1)[0], phone}
    return '' if name in technical else name


def _message(item, *, group=False):
    value = dict(item)
    raw = value.pop('raw', None) or {}
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
    participant_jid = ''
    if isinstance(raw, dict):
        participant_jid = str(raw.get('participantJid') or raw.get('senderJid') or '')[:180]
    if direction == 'incoming' and group and not author and participant_jid.endswith('@s.whatsapp.net'):
        candidate = participant_jid.split('@', 1)[0]
        if candidate.isdecimal():
            author = candidate
    value['direction'] = direction
    value['author_type'] = author_type
    value['author_name'] = author
    value['participant_jid'] = participant_jid
    value['source'] = 'connect' if str(value.get('channel') or '').startswith('connect:') else 'kommo'
    return public(value)


def projection(policy):
    scope,params=[],[]
    if policy.profile.get('role')!='owner':
        for item in (policy.pipeline_scope() if policy.allows('deals') or policy.allows('customers') else []):
            part='d.pipeline_id=%s';params.append(item['pipeline_id'])
            if item['status_ids']:
                part+=' AND d.status_id=ANY(%s::bigint[])';params.append(item['status_ids'])
            scope.append('('+part+')')
    kommo_scope='TRUE' if policy.profile.get('role')=='owner' else '('+' OR '.join(scope)+')' if scope else 'FALSE'
    sql='''WITH routes AS (
      SELECT 'connect'::text AS provider,c.connect_channel_id::text AS channel_id,c.conversation_id,c.lead_id,
        COALESCE(NULLIF(c.display_name,''),NULLIF(c.contact_name,''),NULLIF(c.phone,''),c.conversation_id) AS customer_name,c.phone,c.conversation_kind,
        ch.name AS channel_name,ch.active AS channel_active,'connect:'||c.connect_channel_id::text AS history_channel,
        COALESCE(j.agent_id,cs.agent_id) AS agent_id,a.name AS agent_name,a.enabled AS agent_enabled,
        COALESCE(cs.state,'active') AS state,COALESCE(cs.reason,'') AS reason,cs.updated_at,
        cs.tenant_id IS NOT NULL AS tracked,s.enabled AS company_enabled,s.emergency_stop AS company_emergency_stop,
        t.status AS tenant_status,j.state AS latest_job_state,COALESCE(j.error_code,'') AS latest_job_error,
        COALESCE(lm.last_message_at,c.last_message_at,c.created_at) AS last_message_at,
        ticket.assigned_to,tm.display_name AS assignee_name,
        (SELECT count(*) FROM saas_connect_conversations other WHERE other.tenant_id=c.tenant_id
          AND other.conversation_id=c.conversation_id)=1 AS unambiguous,
        EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected') AS connected
      FROM saas_connect_conversations c
      JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      JOIN saas_tenants t ON t.id=c.tenant_id
      LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=c.tenant_id AND cs.provider='connect' AND cs.conversation_id=c.conversation_id
      LEFT JOIN LATERAL(SELECT j0.* FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=c.tenant_id AND j0.provider='connect'
        AND j0.channel_id=c.connect_channel_id::text AND j0.conversation_id=c.conversation_id ORDER BY j0.updated_at DESC LIMIT 1) j ON TRUE
      LEFT JOIN saas_ai_agents a ON a.tenant_id=c.tenant_id AND a.id=COALESCE(j.agent_id,cs.agent_id)
      LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id
      LEFT JOIN LATERAL(SELECT COALESCE(m.happened_at,m.created_at) AS last_message_at FROM saas_crm_messages m
        WHERE m.tenant_id=c.tenant_id AND m.kommo_lead_id=c.lead_id AND m.channel='connect:'||c.connect_channel_id::text
        ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 1) lm ON TRUE
      LEFT JOIN saas_support_tickets ticket ON ticket.tenant_id=c.tenant_id AND ticket.channel_id=c.connect_channel_id
        AND ticket.conversation_id=c.conversation_id AND ticket.status<>'resolved'
      LEFT JOIN saas_tenant_members tm ON tm.tenant_id=c.tenant_id AND tm.telegram_id=ticket.assigned_to
      WHERE c.tenant_id=%s::uuid
      UNION ALL
      SELECT 'kommo',COALESCE(j.channel_id,lm.channel,''),COALESCE(j.conversation_id,''),d.kommo_lead_id,
        COALESCE(NULLIF(d.contact_name,''),NULLIF(d.name,''),d.kommo_lead_id::text),COALESCE(d.phone,''),''::text AS conversation_kind,
        COALESCE(NULLIF(lm.channel,''),'Kommo'),TRUE,COALESCE(lm.channel,''),j.agent_id,a.name,a.enabled,
        COALESCE(cs.state,'active'),COALESCE(cs.reason,''),cs.updated_at,cs.tenant_id IS NOT NULL,
        s.enabled,s.emergency_stop,t.status,j.state,COALESCE(j.error_code,''),lm.last_message_at,
        cs.assigned_to,tm.display_name,TRUE,
        EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=d.tenant_id AND i.provider='kommo' AND i.status='connected')
      FROM saas_crm_deals d JOIN saas_tenants t ON t.id=d.tenant_id
      JOIN LATERAL(SELECT m.channel,COALESCE(m.happened_at,m.created_at) AS last_message_at FROM saas_crm_messages m
        WHERE m.tenant_id=d.tenant_id AND m.kommo_lead_id=d.kommo_lead_id AND m.channel NOT LIKE 'connect:%%'
        ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 1) lm ON TRUE
      LEFT JOIN LATERAL(SELECT j0.* FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=d.tenant_id AND j0.provider='kommo'
        AND j0.lead_id=d.kommo_lead_id ORDER BY j0.updated_at DESC LIMIT 1) j ON TRUE
      LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=d.tenant_id AND cs.provider='kommo' AND cs.conversation_id=j.conversation_id
      LEFT JOIN saas_ai_agents a ON a.tenant_id=d.tenant_id AND a.id=j.agent_id
      LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=d.tenant_id
      LEFT JOIN saas_tenant_members tm ON tm.tenant_id=d.tenant_id AND tm.telegram_id=cs.assigned_to
      WHERE d.tenant_id=%s::uuid AND d.deleted_at IS NULL AND '''+kommo_scope+'''
        AND NOT EXISTS(SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=d.tenant_id AND c.lead_id=d.kommo_lead_id)
    ), visible AS (SELECT routes.*,CASE WHEN state IN ('human','paused') THEN state
      WHEN agent_id IS NULL THEN 'unmanaged'
      WHEN latest_job_state IN ('failed','unknown') AND latest_job_error<>'' THEN 'error'
      WHEN NOT COALESCE(company_enabled,FALSE) OR COALESCE(company_emergency_stop,FALSE) OR NOT COALESCE(agent_enabled,FALSE)
        OR NOT %s OR (latest_job_state='skipped' AND latest_job_error<>'') THEN 'blocked'
      ELSE 'active' END AS display_state FROM routes) '''
    return sql,params


def decorate(row):
    item=dict(row)
    unmanaged=item['display_state']=='unmanaged'
    execution_error=(item.get('state') == 'active' and item.get('latest_job_state') in {'failed','unknown'}
                     and bool(item.get('latest_job_error')))
    item=_decorate_conversation_state(item)
    if unmanaged and item['state']=='active':
        item['display_state']='unmanaged';item['display_reason']='agent_not_found'
    elif execution_error:
        item['display_state']='error';item['display_reason']=str(item.get('latest_job_error') or '')
    if item.get('provider') == 'connect' and item.get('conversation_kind') == 'group':
        item['customer_name'] = _safe_group_name(item)
        item['phone'] = ''
    item['customer_kind'] = ('group' if item.get('conversation_kind') == 'group' else
                             'contact' if item.get('provider') == 'connect' else 'contact')
    item['controls_allowed']=bool(item.get('channel_active') and item.get('unambiguous') and item.get('conversation_id'))
    item['control_block_reason']='channel_inactive' if not item.get('channel_active') else 'ambiguous_channel' if not item.get('unambiguous') else 'untracked_dialog' if not item.get('conversation_id') else ''
    item['can_send']=bool(item.get('channel_active') and item.get('connected') and item.get('tenant_status')=='active')
    item['send_block_reason']='integration_disconnected' if not item.get('connected') else 'channel_inactive' if not item.get('channel_active') else 'tenant_not_active' if item.get('tenant_status')!='active' else ''
    if item.get('display_state') == 'active':
        item['ai_availability'] = 'available'
    elif item.get('display_state') == 'error':
        item['ai_availability'] = 'error'
    elif item.get('display_state') == 'unmanaged':
        item['ai_availability'] = 'unmanaged'
    elif item.get('state') in {'paused','human'}:
        item['ai_availability'] = 'stopped'
    else:
        item['ai_availability'] = 'blocked'
    item['available_actions'] = (['pause','handoff'] if item.get('state') == 'active' and item.get('ai_availability') == 'available'
                                 else ['resume'] if item.get('state') in {'paused','human'} and item.get('resume_allowed') and item.get('controls_allowed')
                                 else [])
    item['state_changed_at'] = item.get('updated_at')
    return public(item)


def listing(profile,search='',channel='',agent='',state='',offset=0):
    if state not in {'','active','paused','human','blocked','error','unmanaged','attention'}: raise TenantLinearError('Некорректный статус.')
    offset=max(0,min(int(offset),1000000))
    with _connect() as conn:
        ensure_schema(conn)
        from tenant_connect_transport import _schema as ensure_connect_schema
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=access(cur,profile,'dialogs')
            sql,scopes=projection(policy);params=(tenant,tenant,*scopes,runtime_flag('AI_AGENTS_ENABLED'))
            cur.execute(sql+"SELECT count(*) FILTER(WHERE display_state IN ('human','paused','blocked','error')) AS attention_count FROM visible",params)
            count=int(cur.fetchone()['attention_count'])
            cur.execute(sql+"SELECT DISTINCT provider||':'||channel_id AS id,channel_name AS name FROM visible ORDER BY name",params)
            channels=[dict(r) for r in cur.fetchall()]
            cur.execute(sql+'SELECT DISTINCT agent_id::text AS id,agent_name AS name FROM visible WHERE agent_id IS NOT NULL ORDER BY name',params)
            agents=[dict(r) for r in cur.fetchall()]
            where,args=[],[]
            if search:where.append("concat_ws(' ',customer_name,phone,conversation_id) ILIKE %s");args.append('%'+str(search)[:200]+'%')
            if channel:where.append("provider||':'||channel_id=%s");args.append(str(channel)[:250])
            if agent=='unassigned':where.append('agent_id IS NULL')
            elif agent:where.append('agent_id=%s::uuid');args.append(identifier(agent))
            if state=='attention':where.append("display_state IN ('human','paused','blocked','error')")
            elif state:where.append('display_state=%s');args.append(state)
            cur.execute(sql+'SELECT * FROM visible'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY last_message_at DESC NULLS LAST,provider,lead_id LIMIT 51 OFFSET %s',(*params,*args,offset))
            rows=cur.fetchall()
    return {'dialogs':[decorate(r) for r in rows[:50]],'channels':channels,'agents':agents,
            'attention_count':count,'next_offset':offset+50 if len(rows)>50 else None}


def history(profile,provider,lead_id,channel='',conversation='',before=None):
    lead_id=int(lead_id)
    with _connect() as conn:
        ensure_schema(conn)
        from tenant_connect_transport import _schema as ensure_connect_schema
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=access(cur,profile,'dialogs')
            selected=assert_route(cur,tenant,policy,provider,conversation,str(channel),lead_id)
            if provider=='connect':
                channel=str(selected['connect_channel_id']);conversation=selected['conversation_id']
            sql,scopes=projection(policy)
            cur.execute(sql+'SELECT * FROM visible WHERE provider=%s AND lead_id=%s AND channel_id=%s AND conversation_id=%s',
                (tenant,tenant,*scopes,runtime_flag('AI_AGENTS_ENABLED'),provider,lead_id,str(channel),str(conversation)))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Диалог не найден.',404)
            dialog=decorate(row)
            if provider=='kommo' and dialog['controls_allowed']:
                from tenant_ai_agent_test_mode import conversation_allowed
                if not conversation_allowed(cur,tenant,conversation):
                    dialog['controls_allowed']=False;dialog['control_block_reason']='test_binding_required'
            args=[tenant,lead_id,row['history_channel']];clause=''
            if before:
                clause=''' AND (COALESCE(happened_at,created_at),id)<(SELECT COALESCE(happened_at,created_at),id FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=%s::uuid)'''
                args += [tenant,lead_id,row['history_channel'],identifier(before)]
            cur.execute('''SELECT id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,created_at,raw FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s'''+clause+'''
              ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 51''',tuple(args))
            messages=cur.fetchall()
    return {'dialog':dialog,'messages':[_message(r, group=dialog.get('customer_kind') == 'group') for r in reversed(messages[:50])],
            'next_before':str(messages[49]['id']) if len(messages)>50 else None}
