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


def _message(item, *, group=False, participant_names=None, contact_name=''):
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
    if group and isinstance(raw,dict) and raw.get('ai_authored') is True:
        author_type='ai'
    participant_jid, participant_alt, whatsapp_name = _participant_fields(raw)
    if group and direction=='outgoing' and author in {'Operator','WhatsApp operator'} and whatsapp_name:
        author=whatsapp_name
    if direction in {'incoming','outgoing'} and group and author_type!='ai':
        # Resolution is intentionally exact-JID scoped.  Do not conflate a
        # privacy LID with a phone JID, even if both are visible in one event.
        author = str((participant_names or {}).get((str(value.get('source_channel_id') or value.get('channel','').removeprefix('connect:')),participant_jid)) or (participant_names or {}).get(participant_jid) or author or whatsapp_name or '')
    if direction == 'incoming' and group and not author and participant_jid.endswith('@s.whatsapp.net'):
        candidate = participant_jid.split('@', 1)[0]
        if candidate.isdecimal():
            author = candidate
    # A direct conversation has one customer.  The route projection has
    # already resolved its tenant/channel-local name in the safe order
    # manual override -> saved provider name -> pushName -> phone.  Use that
    # display fact for its incoming message footer rather than the persisted
    # transport fallback (usually a phone/JID).  Group participants, AI and
    # operator authors intentionally remain on their independent branches.
    if not group and direction == 'incoming' and author_type == 'customer' and str(contact_name).strip():
        author = str(contact_name).strip()
    value['direction'] = direction
    value['author_type'] = author_type
    value['author_name'] = author
    value['participant_jid'] = participant_jid
    value['participant_alt'] = participant_alt
    value['source'] = 'connect' if str(value.get('channel') or '').startswith('connect:') else 'kommo'
    if value['source'] == 'connect':
        # No provider URL or raw webhook payload crosses the browser boundary.
        value['media_url'] = ''
        if value.get('message_type') in {'image','audio','voice','video','document'}:
            filename=_media_filename(raw)
            if filename:value['media_filename']=filename
        transcript = raw.get('voice_transcription', {}) if isinstance(raw, dict) else {}
        if (value.get('message_type') in {'audio','voice'} and isinstance(transcript, dict)
                and transcript.get('status') == 'completed' and value.get('body')):
            value['has_transcription'] = True
        if value.get('message_type') in {'audio','voice'}:
            duration = _audio_duration(raw)
            if duration is not None:
                value['duration_seconds'] = duration
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
        COALESCE(NULLIF(c.manual_display_name,''),NULLIF(CASE WHEN c.conversation_kind='group' THEN c.display_name ELSE c.contact_name END,''),NULLIF(c.display_name,''),NULLIF(c.phone,''),c.conversation_id) AS customer_name,c.manual_display_name,c.phone,c.conversation_kind,
        ch.name AS channel_name,ch.active AS channel_active,'connect:'||c.connect_channel_id::text AS history_channel,
        COALESCE(cs.agent_id,j.agent_id) AS agent_id,a.name AS agent_name,a.enabled AS agent_enabled,
        COALESCE(gc.state,cs.state,'active') AS state,COALESCE(gc.reason,cs.reason,'') AS reason,COALESCE(gc.updated_at,cs.updated_at) AS updated_at,
        cs.tenant_id IS NOT NULL AS tracked,s.enabled AS company_enabled,s.emergency_stop AS company_emergency_stop,
        t.status AS tenant_status,j.state AS latest_job_state,COALESCE(j.error_code,'') AS latest_job_error,
        COALESCE(lm.last_message_at,c.last_message_at,c.created_at) AS last_message_at,
        ticket.assigned_to,tm.display_name AS assignee_name,
        NOT ai_legacy_blocked(c.tenant_id,c.connect_channel_id::text,c.conversation_id) AS unambiguous,
        EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected') AS connected
      FROM saas_connect_conversations c
      JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      JOIN saas_tenants t ON t.id=c.tenant_id
      LEFT JOIN saas_ai_group_coordination gc ON gc.tenant_id=c.tenant_id AND gc.provider='connect' AND gc.group_jid=c.conversation_id
      LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=c.tenant_id AND cs.provider='connect' AND cs.conversation_id=c.conversation_id AND cs.channel_id=c.connect_channel_id::text
      LEFT JOIN LATERAL(SELECT j0.* FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=c.tenant_id AND j0.provider='connect'
        AND j0.channel_id=c.connect_channel_id::text AND j0.conversation_id=c.conversation_id ORDER BY j0.updated_at DESC LIMIT 1) j ON TRUE
      LEFT JOIN saas_ai_agents a ON a.tenant_id=c.tenant_id AND a.id=COALESCE(cs.agent_id,j.agent_id)
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
        COALESCE(NULLIF(d.contact_name,''),NULLIF(d.name,''),d.kommo_lead_id::text),''::text AS manual_display_name,COALESCE(d.phone,''),''::text AS conversation_kind,
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
    if item.get('provider') == 'connect' and item.get('unambiguous') is False:
        item.update(display_state='blocked', display_reason='legacy_channel_ambiguous', resume_allowed=False, resume_block_reason='legacy_channel_ambiguous')
    item['controls_allowed']=bool(item.get('channel_active') and item.get('unambiguous') and item.get('conversation_id'))
    item['control_block_reason']='channel_inactive' if not item.get('channel_active') else 'legacy_channel_ambiguous' if not item.get('unambiguous') else 'untracked_dialog' if not item.get('conversation_id') else ''
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
    item['available_actions'] = (['pause','handoff'] if item.get('state') == 'active' and item.get('ai_availability') == 'available' and item.get('controls_allowed')
                                 else ['resume'] if item.get('state') in {'paused','human'} and item.get('resume_allowed') and item.get('controls_allowed')
                                 else [])
    item['state_changed_at'] = item.get('updated_at')
    return public(item)


def apply_group_view(cur,tenant,dialog):
    from tenant_ai_group_coordination import view
    from tenant_ai_agent_access import tenant_status_allows_ai
    g=view(cur,tenant,dialog['conversation_id']);dialog['group_coordination']=g
    cur.execute('SELECT name FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,g['primary_agent_id']))
    name=(cur.fetchone() or {}).get('name');g['primary_agent_name']=name
    cur.execute('SELECT display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s',(tenant,g['assigned_to']))
    assignee=(cur.fetchone() or {}).get('display_name')
    usable=bool(g['primary_agent_id'] and dialog.get('controls_allowed') and dialog.get('company_enabled') and not dialog.get('company_emergency_stop') and dialog.get('connected') and runtime_flag('AI_AGENTS_ENABLED') and tenant_status_allows_ai(dialog.get('tenant_status'),'connect'))
    state=g['state'];display=state if state!='active' or usable else 'blocked'
    dialog.update(agent_id=g['primary_agent_id'],agent_name=name,assignee_name=assignee,
                  state=state,display_state=display,display_reason=g['reason'],
                  ai_availability='available' if display=='active' else 'stopped' if display in ('human','paused') else 'blocked',
                  resume_allowed=usable and state!='active',
                  available_actions=['pause','handoff'] if state=='active' and dialog.get('controls_allowed') else ['resume'] if usable else [])
    return dialog


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
            cur.execute(sql+"SELECT count(DISTINCT CASE WHEN provider='connect' AND conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$' THEN provider||':'||conversation_id ELSE provider||':'||channel_id||':'||lead_id END) FILTER(WHERE display_state IN ('human','paused','blocked','error')) AS attention_count FROM visible",params)
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
            cur.execute(sql+"SELECT * FROM (SELECT visible.*,row_number() OVER(PARTITION BY CASE WHEN provider='connect' AND conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$' THEN provider||':'||conversation_id ELSE provider||':'||channel_id||':'||lead_id END ORDER BY last_message_at DESC NULLS LAST,lead_id) AS logical_rank FROM visible"+(' WHERE '+' AND '.join(where) if where else '')+') folded WHERE logical_rank=1 ORDER BY last_message_at DESC NULLS LAST,provider,lead_id LIMIT 51 OFFSET %s',(*params,*args,offset))
            rows=cur.fetchall()
            from tenant_ai_group_coordination import group_jid,view as group_view
            rows=[decorate(r) for r in rows]
            for r in rows:
                if r['provider']=='connect' and group_jid(r['conversation_id']):
                    apply_group_view(cur,tenant,r)
    return {'dialogs':rows[:50],'channels':channels,'agents':agents,
            'attention_count':count,'next_offset':offset+50 if len(rows)>50 else None}


def history(profile,provider,lead_id,channel='',conversation='',before=None,anchor=None):
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
            from tenant_ai_group_coordination import group_jid, view as group_view
            if provider=='connect' and group_jid(conversation):
                apply_group_view(cur,tenant,dialog)
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
            if provider=='connect' and not group_jid(conversation) and not dialog.get('agent_id'):
                if dialog.get('unambiguous') and dialog.get('controls_allowed'):
                    from tenant_operator_assignment import options_in_transaction
                    dialog['agent_assignment']=options_in_transaction(cur,profile,{
                        'provider':'connect','lead_id':lead_id,'channel_id':channel,'conversation_id':conversation})
                else:
                    dialog['agent_assignment']={'allowed':False,'reason':dialog.get('control_block_reason'),'agents':[]}
            if provider=='kommo' and dialog['controls_allowed']:
                from tenant_ai_agent_test_mode import conversation_allowed
                if not conversation_allowed(cur,tenant,conversation):
                    dialog['controls_allowed']=False;dialog['control_block_reason']='test_binding_required'
            anchor_at=None;anchor_row={}
            if anchor and not before:
                # Resolve anchor only within the authorized route / exact tenant group.
                cur.execute('''SELECT m.id,m.external_id,COALESCE(m.happened_at,m.created_at) AS at FROM saas_crm_messages m
                  WHERE m.tenant_id=%s::uuid AND m.id=%s::uuid AND ((m.kommo_lead_id=%s AND m.channel=%s)
                    OR (%s AND EXISTS(SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=m.tenant_id
                      AND c.conversation_id=%s AND c.lead_id=m.kommo_lead_id AND m.channel='connect:'||c.connect_channel_id::text)))''',
                  (tenant,identifier(anchor),lead_id,row['history_channel'],bool(provider=='connect' and group_jid(conversation)),conversation))
                anchor_row=dict(cur.fetchone() or {});anchor_at=anchor_row.get('at')
            if provider=='connect' and group_jid(conversation):
                from tenant_connect_group_history import page as group_history
                messages=group_history(cur,tenant,conversation,before=identifier(before) if before else None,through=anchor_at)
            else:
                args=[tenant,lead_id,row['history_channel']];clause=''
                if anchor_at:
                    clause=' AND COALESCE(happened_at,created_at)<=%s'
                    args.append(anchor_at)
                if before:
                    clause=''' AND (COALESCE(happened_at,created_at),id)<(SELECT COALESCE(happened_at,created_at),id FROM saas_crm_messages
                      WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=%s::uuid)'''
                    args += [tenant,lead_id,row['history_channel'],identifier(before)]
                cur.execute('''SELECT id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,created_at,raw FROM saas_crm_messages
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s'''+clause+'''
                  ORDER BY COALESCE(happened_at,created_at) DESC,id DESC LIMIT 51''',tuple(args))
                messages=cur.fetchall()
            participant_names={}
            if provider == 'connect' and dialog.get('customer_kind') == 'group':
                # The override's primary key includes the tenant, exact Connect
                # channel and exact conversation/JID.  This deliberately does
                # not build a global LID/phone contact directory.
                participant_ids=[]
                for message in messages:
                    participant_jid, _, _ = _participant_fields(message.get('raw'))
                    if participant_jid and participant_jid not in participant_ids:
                        participant_ids.append(participant_jid)
                if participant_ids:
                    cur.execute('''SELECT connect_channel_id::text AS channel_id,participant_jid,display_name FROM saas_connect_participant_display_names
                      WHERE tenant_id=%s::uuid AND connect_channel_id::text=ANY(%s::text[]) AND conversation_id=%s
                        AND participant_jid=ANY(%s::text[])''',(tenant,list({m.get('source_channel_id',channel) for m in messages}),conversation,participant_ids))
                    participant_names={(str(item['channel_id']),str(item['participant_jid'])):str(item['display_name']) for item in cur.fetchall()
                                       if str(item.get('display_name') or '')}
    anchor_target=next((str(m['id']) for m in messages[:50] if anchor_row and (str(m['id'])==str(anchor_row['id']) or any(s.get('external_id')==anchor_row['external_id'] for s in m.get('sources',[])))),None)
    return {'dialog':dialog,'anchor_message_id':anchor_target,'messages':[_message(r, group=dialog.get('customer_kind') == 'group', participant_names=participant_names,
                                         contact_name=dialog.get('customer_name') if dialog.get('customer_kind') == 'contact' else '')
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
            tenant,actor,policy=access(cur,profile,'dialogs')
            selected=assert_route(cur,tenant,policy,'connect',conversation,channel,lead_id)
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
