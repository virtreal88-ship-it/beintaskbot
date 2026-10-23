"""Explicit, channel-local binding of unassigned Connect dialogues.

Assignment never queues work or sends a message. Resume remains a separate
operation. The existing dialogue lock serializes this with incoming webhooks,
operator takeover and Resume; the assignment token makes retries harmless.
"""
import hashlib
import json
import re
import uuid
from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agents_store import _audit, _resume_block_reason
from tenant_ai_agent_connect import scope_matches
from tenant_ai_dialog_scope import assert_resolved, channel_scope, lock_dialog
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access, assert_route
from tenant_platform import _connect


def identity(data):
    if not isinstance(data, dict) or data.get('provider') != 'connect':
        raise TenantLinearError('Назначение доступно только для WhatsApp-диалога.', 400)
    channel = channel_scope('connect', data.get('channel_id'))
    conversation = str(data.get('conversation_id') or '').strip()
    if not conversation or len(conversation) > 160:
        raise TenantLinearError('Укажите конкретный диалог.', 400)
    try:
        lead = int(data['lead_id'])
    except (KeyError, TypeError, ValueError):
        raise TenantLinearError('Укажите конкретный диалог.', 400) from None
    return channel, conversation, lead


def context(cur, profile, data, *, writing=False):
    channel, conversation, lead = identity(data)
    from tenant_ai_group_coordination import group_jid
    if group_jid(conversation):
        raise TenantLinearError('Для группы используйте назначение основного агента и канала.', 409)
    tenant, actor, policy = access(cur, profile, 'dialogs')
    if writing:
        lock_dialog(cur, tenant, 'connect', channel, conversation)
    route = assert_route(cur, tenant, policy, 'connect', conversation, channel, lead)
    from tenant_connect_identity import conflicted
    identity_conflict=conflicted(cur,tenant,channel,conversation)
    if writing and identity_conflict:
        raise TenantLinearError('conversation_identity_conflict',409)
    assert_resolved(cur, tenant, channel, conversation)
    cur.execute('''SELECT state,reason,revision,agent_id,agent_assignment_id
      FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect'
        AND channel_id=%s AND conversation_id=%s''' + (' FOR UPDATE' if writing else ''),
        (tenant, channel, conversation))
    state = dict(cur.fetchone() or {})
    cur.execute('''SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid
      AND provider='connect' AND channel_id=%s AND conversation_id=%s
      AND state IN ('queued','processing','unknown') LIMIT 1''',
        (tenant, channel, conversation))
    has_job = bool(cur.fetchone())
    cur.execute('''SELECT t.status AS tenant_status,s.enabled AS company_enabled,
      s.emergency_stop AS company_emergency_stop,ch.active AS channel_active,
      EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=t.id
        AND i.provider='connect' AND i.status='connected') AS connected
      FROM saas_tenants t JOIN saas_connect_channels ch ON ch.tenant_id=t.id
        AND ch.connect_channel_id=%s::uuid
      LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=t.id WHERE t.id=%s::uuid
      FOR SHARE OF t,ch''', (channel, tenant))
    config = dict(cur.fetchone() or {})
    config['identity_conflict']=identity_conflict
    return tenant, actor, channel, conversation, route, state, has_job, config


def candidates(cur, tenant, channel, conversation, route, state, has_job, config, *, allow_current=False):
    if config.get('identity_conflict'):
        return [], 'conversation_identity_conflict'
    if has_job:
        return [], 'agent_already_assigned'
    if state.get('agent_id') and not allow_current:
        return [], 'agent_already_assigned'
    if not config.get('channel_active'):
        return [], 'channel_inactive'
    if not config.get('connected'):
        return [], 'integration_disconnected'
    reason = _resume_block_reason({**config, 'agent_id': 'candidate', 'agent_enabled': True}, 'connect')
    if reason:
        return [], reason
    cur.execute('''SELECT id,name,mode,scopes,enabled FROM saas_ai_agents
      WHERE tenant_id=%s::uuid AND role='primary' ORDER BY name,id FOR SHARE''', (tenant,))
    is_group = route.get('conversation_kind') == 'group' or conversation.endswith('@g.us')
    scoped = [dict(a) for a in cur.fetchall() if scope_matches(a.get('scopes'), channel)]
    if not scoped:
        return [], 'no_connect_scope'
    agents = [a for a in scoped if a.get('enabled') is True]
    if not agents:
        return [], 'agent_disabled'
    if is_group:
        agents = [a for a in agents if scope_matches(a.get('scopes'), channel, is_group=True)]
        if not agents:
            return [], 'group_messages_not_allowed'
    # Assignment is independent of the agent's Manual/Automatic mode and never
    # queues work. The worker separately enforces all runtime send checks.
    return [{'id': str(a['id']), 'name': a['name'], 'mode': a['mode']} for a in agents], ('' if agents else 'no_eligible_agent')


def options_in_transaction(cur, profile, data):
    values = context(cur, profile, data)
    tenant, _, channel, conversation, route, state, has_job, config = values
    manage = data.get('manage') in (True,'true','1',1)
    agents, reason = candidates(cur, tenant, channel, conversation, route, state, has_job, config, allow_current=manage)
    return {'allowed': bool(agents), 'reason': reason, 'agents': agents,
            'current_agent_id': str(state['agent_id']) if state.get('agent_id') else None,
            'can_manage': not has_job,
            'revision': int(state.get('revision', 0)), 'state': state.get('state', 'unmanaged')}


def options(profile, data):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            return options_in_transaction(cur, profile, data)


def group_options_in_transaction(cur, tenant, channel, conversation):
    """Read-only, channel-bound group candidates for preview only."""
    from tenant_ai_group_coordination import group_jid, read as read_group
    if not group_jid(conversation):
        return {'allowed':False,'reason':'invalid_group','agents':[],'revision':0}
    state=read_group(cur,tenant,conversation)
    revision=int(state.get('revision') or 0)
    if state.get('assignments'):
        return {'allowed':False,'reason':'group_already_assigned','agents':[],'revision':revision}
    if state.get('state') in {'human','active'}:
        return {'allowed':False,'reason':'group_stopped_or_active','agents':[],'revision':revision}
    cur.execute('''SELECT t.status,s.enabled,s.emergency_stop,ch.active,
        EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=t.id
          AND i.provider='connect' AND i.status='connected') AS connected,
        NOT ai_legacy_blocked(t.id,ch.connect_channel_id::text,%s) AS unambiguous
      FROM saas_tenants t JOIN saas_connect_channels ch ON ch.tenant_id=t.id
        AND ch.connect_channel_id=%s::uuid LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=t.id
      WHERE t.id=%s::uuid''',(conversation,channel,tenant))
    gate=cur.fetchone() or {}
    from tenant_ai_agent_access import tenant_status_allows_ai
    from tenant_ai_agent_runtime_flags import enabled as runtime_flag
    if (not runtime_flag('AI_AGENTS_ENABLED') or not gate.get('active') or not gate.get('connected') or not gate.get('unambiguous')
            or gate.get('enabled') is not True or gate.get('emergency_stop') is True
            or not tenant_status_allows_ai(gate.get('status'),'connect')):
        return {'allowed':False,'reason':'connect_admission_blocked','agents':[],'revision':revision}
    cur.execute("SELECT id,name,enabled,mode,scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND role='primary' ORDER BY name,id",(tenant,))
    rows=[dict(row) for row in cur.fetchall()]
    from tenant_ai_agent_connect import scope_matches
    scoped=[row for row in rows if scope_matches(row.get('scopes'),channel,is_group=True)]
    candidates=[row for row in scoped if row.get('enabled') is True]
    reason='' if candidates else 'group_messages_not_allowed' if scoped else 'no_connect_scope' if rows else 'agent_not_found'
    return {'allowed':bool(candidates),'reason':reason,'agents':[{'id':str(row['id']),'name':row['name'],'mode':row['mode']} for row in candidates],'revision':revision}


def assign_group(profile,data):
    """Persist an owner-confirmed unique group binding without resuming it."""
    from tenant_ai_group_coordination import group_jid,lock_group,read as read_group
    from tenant_platform import _json
    channel,conversation,lead=identity(data)
    if not group_jid(conversation):
        raise TenantLinearError('Укажите групповую переписку.',400)
    agent_id=identifier(data.get('agent_id'))
    revision=data.get('expected_revision')
    if type(revision) is not int or revision<0:
        raise TenantLinearError('Обновите предпросмотр назначения группы.',409)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=access(cur,profile,'dialogs')
            if policy.profile.get('role')!='owner':
                raise TenantLinearError('Массовое назначение доступно только владельцу компании.',403)
            route=assert_route(cur,tenant,policy,'connect',conversation,channel,lead)
            assert_resolved(cur,tenant,channel,conversation)
            lock_group(cur,tenant,conversation)
            state=read_group(cur,tenant,conversation)
            if int(state.get('revision') or 0)!=revision:
                raise TenantLinearError('Состояние группы изменилось. Обновите предпросмотр.',409)
            candidates=group_options_in_transaction(cur,tenant,channel,conversation)
            if not candidates.get('allowed') or len(candidates['agents'])!=1 or candidates['agents'][0]['id']!=agent_id:
                raise TenantLinearError('Единственный подходящий агент изменился. Обновите предпросмотр.',409)
            cur.execute('''INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,revision) VALUES(%s::uuid,%s,0) ON CONFLICT DO NOTHING''',(tenant,conversation))
            assignments=[{'agent_id':agent_id,'channel_id':channel,'priority':0}]
            cur.execute('''UPDATE saas_ai_group_coordination SET assignments=%s::jsonb,reason='operator_agent_assigned',
              revision=revision+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s
                AND revision=%s AND state='paused' AND assignments='[]'::jsonb RETURNING revision''',
              (_json(assignments),tenant,conversation,revision))
            if not cur.fetchone():
                raise TenantLinearError('Состояние группы изменилось. Обновите предпросмотр.',409)
            _audit(cur,tenant,actor,'ai_group_agent_assigned','conversation',conversation,
              {'channel_id':channel,'agent_id':agent_id,'source':'confirmed_bulk_preview'})
        conn.commit()
    return {'agent_id':agent_id,'state':state.get('state','paused'),'automatic_replies_enabled':False}


def assign(profile, data):
    agent = identifier(data.get('agent_id')) if data.get('agent_id') else None
    request_id = identifier(data.get('request_id'))
    revision = data.get('expected_revision')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise TenantLinearError('Обновите диалог перед назначением.', 409)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, channel, conversation, route, state, has_job, config = context(cur, profile, data, writing=True)
            if str(state.get('agent_assignment_id') or '') == request_id:
                if str(state.get('agent_id') or '') != agent:
                    raise TenantLinearError('Этот запрос уже использован для другого назначения.', 409)
                return {'agent_id': agent, 'state': state['state'], 'revision': state['revision'], 'replayed': True}
            if int(state.get('revision', 0)) != revision:
                raise TenantLinearError('Диалог изменился. Обновите его перед назначением.', 409)
            if state.get('agent_id') is not None and str(state.get('agent_id')) == str(agent):
                return {'agent_id': agent, 'state': state.get('state','active'),
                        'revision': int(state.get('revision',0)), 'replayed': False,
                        'automatic_replies_enabled': False, 'unchanged': True}
            agents, reason = candidates(cur, tenant, channel, conversation, route, state, has_job, config, allow_current=True)
            if agent and not any(a['id'] == agent for a in agents):
                error = TenantLinearError('Агент недоступен для назначения в этот диалог.', 409)
                error.reason_code = reason or 'agent_scope_mismatch'
                raise error
            # Reconfiguration never resumes AI. Preserve human handoff and pause;
            # changing an active or untracked binding leaves it paused.
            stopped = 'human' if state.get('state') == 'human' else 'paused'
            stop_reason = state.get('reason') if state.get('state') in {'human','paused'} else ('operator_agent_assigned' if agent else 'operator_agent_unassigned')
            cur.execute('''INSERT INTO saas_ai_conversation_state
              (tenant_id,provider,channel_id,conversation_id,agent_id,agent_assignment_id,state,reason,assigned_to)
              VALUES(%s::uuid,'connect',%s,%s,%s::uuid,%s::uuid,%s,%s,%s)
              ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE SET
                agent_id=EXCLUDED.agent_id,agent_assignment_id=EXCLUDED.agent_assignment_id,
                state=EXCLUDED.state,reason=EXCLUDED.reason,
                assigned_to=CASE WHEN EXCLUDED.state='human' THEN saas_ai_conversation_state.assigned_to ELSE NULL END,
                revision=saas_ai_conversation_state.revision+1,updated_at=now()
              RETURNING revision,state''', (tenant, channel, conversation, agent, request_id, stopped, stop_reason,
                state.get('assigned_to') if stopped=='human' else None))
            result = dict(cur.fetchone())
            _audit(cur, tenant, actor, 'ai_conversation_agent_configured', 'conversation', conversation,
                   {'provider': 'connect', 'channel_id': channel, 'agent_id': agent,
                    'request_id': request_id, 'previous_state': state.get('state', 'unmanaged'), 'state': stopped})
        conn.commit()
    return {**result, 'agent_id': agent, 'replayed': False, 'automatic_replies_enabled': False}


def _require_owner(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _actor, policy = access(cur, profile, 'dialogs')
            if policy.profile.get('role') != 'owner':
                raise TenantLinearError('Массовое назначение доступно только владельцу компании.', 403)
            return tenant


def bulk_preview(profile, channel_id, agent_id):
    """Read-only, channel and agent scoped exact-candidate preview for owner review."""
    tenant=_require_owner(profile)
    try:
        channel_id = str(uuid.UUID(str(channel_id)))
    except (TypeError, ValueError, AttributeError):
        raise TenantLinearError('Выберите сохранённый WhatsApp-канал.', 400) from None
    agent_id = identifier(agent_id)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,agent_id))
            if not cur.fetchone():
                raise TenantLinearError('Агент не найден в текущей компании.',404)
    from tenant_operator_dialogs import listing
    rows=[]; offset=0
    while True:
        page=listing(profile,channel='connect:'+channel_id,offset=offset)
        rows.extend(page['dialogs'])
        if page.get('next_offset') is None: break
        offset=page['next_offset']
        if offset>=100_000:
            raise TenantLinearError('Предпросмотр слишком велик. Уточните список диалогов.',413)
    summary={'total':len(rows),'assigned':0,'agent_assigned':0,'one_candidate':0,'single_candidate_unassigned':0,
             'multiple_candidates':0,'no_candidate':0,'handoff':0,'handoff_and_exceptions':0,
             'excluded_from_automatic':0,'ambiguous_state':0,'legacy_ambiguity':0,
             'group':0,'group_without_primary_agent':0,'group_eligible':0}
    eligible=[]; skipped_reasons={}
    for row in rows:
        group_row=row.get('customer_kind')=='group' or str(row.get('conversation_id') or '').endswith('@g.us')
        group_state=row.get('group_coordination') or {}
        has_group_agent=group_row and any(str(a.get('channel_id'))==str(row.get('channel_id')) for a in group_state.get('assignments') or [])
        if row.get('agent_id') or has_group_agent:
            summary['assigned']+=1;summary['agent_assigned']+=1
        if row.get('handoff_active'): summary['handoff']+=1
        if row.get('handoff_active') or row.get('state') in {'human','paused'}:
            summary['handoff_and_exceptions']+=1
        if row.get('display_state')!='active' or row.get('state') in {'human','paused'}:
            summary['excluded_from_automatic']+=1
        reason=row.get('display_reason') or row.get('reason')
        if row.get('state') in {'human','paused','unmanaged'} or reason in {'legacy_channel_ambiguous','untracked_dialog','ambiguous_channel','group_assignment_required_or_ambiguous','group_authorship_unresolved'}:
            summary['ambiguous_state']+=1
        if row.get('unambiguous') is False or row.get('display_reason')=='legacy_channel_ambiguous':
            summary['legacy_ambiguity']+=1
        if group_row:
            summary['group']+=1
            if not row.get('agent_id') and not has_group_agent:
                summary['group_without_primary_agent']+=1
            if row.get('provider')!='connect':
                continue
            if row.get('agent_id') or any(str(a.get('channel_id'))==str(row.get('channel_id')) for a in group_state.get('assignments') or []):
                skipped_reasons['already_assigned']=skipped_reasons.get('already_assigned',0)+1
                continue
            if row.get('handoff_active') or group_state.get('state')=='human':
                skipped_reasons['handoff']=skipped_reasons.get('handoff',0)+1
                continue
            if group_state.get('state')=='active':
                skipped_reasons['group_active_state_preserved']=skipped_reasons.get('group_active_state_preserved',0)+1
                continue
            if row.get('unambiguous') is False:
                skipped_reasons['legacy_channel_ambiguous']=skipped_reasons.get('legacy_channel_ambiguous',0)+1
                continue
            assignment=row.get('group_assignment_preview') or {}
            choices=assignment.get('agents') or []
            matches=[choice for choice in choices if str(choice.get('id'))==agent_id]
            if len(choices)==1:
                summary['one_candidate']+=1;summary['single_candidate_unassigned']+=1
            elif len(choices)>1:
                summary['multiple_candidates']+=1
            else:
                summary['no_candidate']+=1
            if assignment.get('allowed') and len(choices)==1 and len(matches)==1:
                summary['group_eligible']+=1
                eligible.append({'provider':'connect','kind':'group','lead_id':int(row['lead_id']),
                  'channel_id':row['channel_id'],'conversation_id':row['conversation_id'],'agent_id':agent_id,
                  'agent_name':str(matches[0]['name']),'customer_name':str(row.get('customer_name') or ''),
                  'revision':int(assignment.get('revision') or 0)})
            elif len(choices)>1:
                skipped_reasons['multiple_candidates']=skipped_reasons.get('multiple_candidates',0)+1
            else:
                reason=assignment.get('reason') or ('selected_agent_not_unique_candidate' if matches else 'selected_agent_not_candidate')
                skipped_reasons[str(reason)]=skipped_reasons.get(str(reason),0)+1
            continue
        if row.get('provider')!='connect':
            continue
        if row.get('agent_id'):
            skipped_reasons['already_assigned']=skipped_reasons.get('already_assigned',0)+1
            continue
        if row.get('handoff_active'):
            skipped_reasons['handoff']=skipped_reasons.get('handoff',0)+1
            continue
        if row.get('unambiguous') is False or row.get('display_reason')=='legacy_channel_ambiguous':
            skipped_reasons['legacy_channel_ambiguous']=skipped_reasons.get('legacy_channel_ambiguous',0)+1
            continue
        if not row.get('agent_assignment'):
            summary['no_candidate']+=1
            skipped_reasons['no_candidate']=skipped_reasons.get('no_candidate',0)+1
            continue
        choices=row['agent_assignment'].get('agents') or []
        matches=[choice for choice in choices if str(choice.get('id'))==agent_id]
        if len(choices)==1:
            summary['one_candidate']+=1;summary['single_candidate_unassigned']+=1
        elif len(choices)>1:
            summary['multiple_candidates']+=1
        else:
            summary['no_candidate']+=1
        if len(choices)==1 and len(matches)==1 and row['agent_assignment'].get('allowed'):
            eligible.append({'provider':'connect','lead_id':int(row['lead_id']),'channel_id':row['channel_id'],
                'conversation_id':row['conversation_id'],'agent_id':str(choices[0]['id']),
                'agent_name':str(choices[0]['name']),'customer_name':str(row.get('customer_name') or ''),
                'revision':int(row['agent_assignment'].get('revision') or 0)})
        elif len(choices)>1:
            skipped_reasons['multiple_candidates']=skipped_reasons.get('multiple_candidates',0)+1
        else:
            reason=row['agent_assignment'].get('reason') or ('selected_agent_not_unique_candidate' if matches else 'selected_agent_not_candidate')
            skipped_reasons[str(reason)]=skipped_reasons.get(str(reason),0)+1
    canonical=json.dumps({'channel_id':channel_id,'agent_id':agent_id,'eligible':eligible},sort_keys=True,ensure_ascii=False,separators=(',',':'))
    token=hashlib.sha256(canonical.encode()).hexdigest()
    return {'channel_id':channel_id,'agent_id':agent_id,'summary':summary,'skipped_reasons':skipped_reasons,
            'eligible':eligible,'preview_token':token,'requires_owner_confirmation':True,
            'automatic_replies_enabled':False}


def bulk_apply(profile,data):
    if not isinstance(data,dict) or data.get('confirmed') is not True:
        raise TenantLinearError('Сначала просмотрите кандидатов и подтвердите массовое назначение.',400)
    token=str(data.get('preview_token') or '')
    batch_id=identifier(data.get('batch_id'))
    if not re.fullmatch(r'[0-9a-f]{64}',token):
        raise TenantLinearError('Предпросмотр устарел. Обновите его перед подтверждением.',409)
    preview=bulk_preview(profile,data.get('channel_id'),data.get('agent_id'))
    if token!=preview['preview_token']:
        raise TenantLinearError('Список диалогов или кандидаты изменились. Проверьте новый предпросмотр.',409)
    outcomes=[];namespace=uuid.UUID(batch_id)
    for item in preview['eligible']:
        request_id=str(uuid.uuid5(namespace,f"{item['channel_id']}:{item['conversation_id']}"))
        try:
            result=(assign_group(profile,{**item,'expected_revision':item['revision']}) if item.get('kind')=='group'
                    else assign(profile,{**item,'expected_revision':item['revision'],'request_id':request_id}))
            outcomes.append({'channel_id':item['channel_id'],'conversation_id':item['conversation_id'],
                'agent_id':item['agent_id'],'status':'assigned','replayed':bool(result.get('replayed'))})
        except TenantLinearError as error:
            outcomes.append({'channel_id':item['channel_id'],'conversation_id':item['conversation_id'],
                'agent_id':item['agent_id'],'status':'skipped','reason':str(error)})
    return {'total':len(outcomes),'assigned':sum(o['status']=='assigned' for o in outcomes),
            'skipped':sum(o['status']=='skipped' for o in outcomes),'outcomes':outcomes}
