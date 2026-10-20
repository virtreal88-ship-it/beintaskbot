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
from tenant_ai_agent_connect import scope_matches, test_allowed
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
    assert_resolved(cur, tenant, channel, conversation)
    cur.execute('''SELECT state,reason,revision,agent_id,agent_assignment_id
      FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect'
        AND channel_id=%s AND conversation_id=%s''' + (' FOR UPDATE' if writing else ''),
        (tenant, channel, conversation))
    state = dict(cur.fetchone() or {})
    cur.execute('''SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid
      AND provider='connect' AND channel_id=%s AND conversation_id=%s LIMIT 1''',
        (tenant, channel, conversation))
    has_job = bool(cur.fetchone())
    cur.execute('''SELECT t.status AS tenant_status,s.enabled AS company_enabled,
      s.emergency_stop AS company_emergency_stop,s.operating_mode,ch.active AS channel_active,
      EXISTS(SELECT 1 FROM saas_tenant_integrations i WHERE i.tenant_id=t.id
        AND i.provider='connect' AND i.status='connected') AS connected
      FROM saas_tenants t JOIN saas_connect_channels ch ON ch.tenant_id=t.id
        AND ch.connect_channel_id=%s::uuid
      LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=t.id WHERE t.id=%s::uuid
      FOR SHARE OF t,ch''', (channel, tenant))
    config = dict(cur.fetchone() or {})
    return tenant, actor, channel, conversation, route, state, has_job, config


def candidates(cur, tenant, channel, conversation, route, state, has_job, config):
    if state.get('agent_id') or has_job:
        return [], 'agent_already_assigned'
    if not config.get('channel_active'):
        return [], 'channel_inactive'
    if not config.get('connected'):
        return [], 'integration_disconnected'
    reason = _resume_block_reason({**config, 'agent_id': 'candidate', 'agent_enabled': True}, 'connect')
    if reason:
        return [], reason
    cur.execute('''SELECT id,name,mode,scopes FROM saas_ai_agents
      WHERE tenant_id=%s::uuid AND enabled=TRUE ORDER BY name,id FOR SHARE''', (tenant,))
    is_group = route.get('conversation_kind') == 'group' or conversation.endswith('@g.us')
    agents = [dict(a) for a in cur.fetchall() if scope_matches(a.get('scopes'), channel, is_group=is_group)]
    # Candidate discovery is not runtime admission: an out-of-shift agent can
    # be explicitly assigned, but the worker still enforces its schedule before
    # processing. Discovery never resumes AI or authorizes automatic sending.
    if config.get('operating_mode') != 'active':
        agents = [a for a in agents if test_allowed(cur, tenant, channel, conversation, agent_id=a['id'])]
    return [{'id': str(a['id']), 'name': a['name'], 'mode': a['mode']} for a in agents], ('' if agents else 'no_eligible_agent')


def options_in_transaction(cur, profile, data):
    values = context(cur, profile, data)
    tenant, _, channel, conversation, route, state, has_job, config = values
    agents, reason = candidates(cur, tenant, channel, conversation, route, state, has_job, config)
    return {'allowed': bool(agents), 'reason': reason, 'agents': agents,
            'revision': int(state.get('revision', 0)), 'state': state.get('state', 'unmanaged')}


def options(profile, data):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            return options_in_transaction(cur, profile, data)


def assign(profile, data):
    agent = identifier(data.get('agent_id'))
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
            agents, reason = candidates(cur, tenant, channel, conversation, route, state, has_job, config)
            if not any(a['id'] == agent for a in agents):
                error = TenantLinearError('Агент недоступен для назначения в этот диалог.', 409)
                error.reason_code = reason or 'agent_scope_mismatch'
                raise error
            # An untracked/active but unassigned dialogue is stopped. Existing
            # Human/Pause reasons and the inbound/history fields are preserved.
            stopped = state.get('state') if state.get('state') in {'human', 'paused'} else 'human'
            stop_reason = state.get('reason', '') if state.get('state') in {'human', 'paused'} else 'operator_agent_assigned'
            cur.execute('''INSERT INTO saas_ai_conversation_state
              (tenant_id,provider,channel_id,conversation_id,agent_id,agent_assignment_id,state,reason)
              VALUES(%s::uuid,'connect',%s,%s,%s::uuid,%s::uuid,%s,%s)
              ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE SET
                agent_id=EXCLUDED.agent_id,agent_assignment_id=EXCLUDED.agent_assignment_id,
                state=EXCLUDED.state,reason=EXCLUDED.reason,
                revision=saas_ai_conversation_state.revision+1,updated_at=now()
              RETURNING revision,state''', (tenant, channel, conversation, agent, request_id, stopped, stop_reason))
            result = dict(cur.fetchone())
            _audit(cur, tenant, actor, 'ai_conversation_agent_assigned', 'conversation', conversation,
                   {'provider': 'connect', 'channel_id': channel, 'agent_id': agent,
                    'request_id': request_id, 'previous_state': state.get('state', 'unmanaged'), 'state': stopped})
        conn.commit()
    return {**result, 'agent_id': agent, 'replayed': False}


def _require_owner(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            _tenant, _actor, policy = access(cur, profile, 'dialogs')
            if policy.profile.get('role') != 'owner':
                raise TenantLinearError('Массовое назначение доступно только владельцу компании.', 403)


def bulk_preview(profile):
    """Read-only inventory and exact-one-candidate preview for owner review."""
    _require_owner(profile)
    from tenant_operator_dialogs import listing
    rows=[]; offset=0
    while True:
        page=listing(profile,offset=offset)
        rows.extend(page['dialogs'])
        if page.get('next_offset') is None: break
        offset=page['next_offset']
        if offset>=100_000:
            raise TenantLinearError('Предпросмотр слишком велик. Уточните список диалогов.',413)
    summary={'total':len(rows),'assigned':0,'one_candidate':0,'multiple_candidates':0,'no_candidate':0,
             'handoff':0,'excluded_from_automatic':0,'ambiguous_state':0,'group':0}
    eligible=[]
    for row in rows:
        if row.get('agent_id'): summary['assigned']+=1
        if row.get('handoff_active'): summary['handoff']+=1
        if row.get('display_state')!='active' or row.get('state') in {'human','paused'}:
            summary['excluded_from_automatic']+=1
        reason=row.get('display_reason') or row.get('reason')
        if row.get('state') in {'human','paused','unmanaged'} or reason in {'legacy_channel_ambiguous','untracked_dialog','ambiguous_channel','group_assignment_required_or_ambiguous','group_authorship_unresolved'}:
            summary['ambiguous_state']+=1
        if row.get('customer_kind')=='group' or str(row.get('conversation_id') or '').endswith('@g.us'):
            summary['group']+=1
            continue
        if row.get('provider')!='connect' or row.get('agent_id'):
            continue
        if not row.get('agent_assignment'):
            summary['no_candidate']+=1
            continue
        choices=row['agent_assignment'].get('agents') or []
        if len(choices)==1 and row['agent_assignment'].get('allowed'):
            summary['one_candidate']+=1
            eligible.append({'provider':'connect','lead_id':int(row['lead_id']),'channel_id':row['channel_id'],
                'conversation_id':row['conversation_id'],'agent_id':str(choices[0]['id']),
                'agent_name':str(choices[0]['name']),'customer_name':str(row.get('customer_name') or ''),
                'revision':int(row['agent_assignment'].get('revision') or 0)})
        elif len(choices)>1:
            summary['multiple_candidates']+=1
        else:
            summary['no_candidate']+=1
    canonical=json.dumps(eligible,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    token=hashlib.sha256(canonical.encode()).hexdigest()
    return {'summary':summary,'eligible':eligible,'preview_token':token,'requires_owner_confirmation':True}


def bulk_apply(profile,data):
    if not isinstance(data,dict) or data.get('confirmed') is not True:
        raise TenantLinearError('Сначала просмотрите кандидатов и подтвердите массовое назначение.',400)
    token=str(data.get('preview_token') or '')
    batch_id=identifier(data.get('batch_id'))
    if not re.fullmatch(r'[0-9a-f]{64}',token):
        raise TenantLinearError('Предпросмотр устарел. Обновите его перед подтверждением.',409)
    preview=bulk_preview(profile)
    if token!=preview['preview_token']:
        raise TenantLinearError('Список диалогов или кандидаты изменились. Проверьте новый предпросмотр.',409)
    outcomes=[];namespace=uuid.UUID(batch_id)
    for item in preview['eligible']:
        request_id=str(uuid.uuid5(namespace,f"{item['channel_id']}:{item['conversation_id']}"))
        try:
            result=assign(profile,{**item,'expected_revision':item['revision'],'request_id':request_id})
            outcomes.append({'channel_id':item['channel_id'],'conversation_id':item['conversation_id'],
                'agent_id':item['agent_id'],'status':'assigned','replayed':bool(result.get('replayed'))})
        except TenantLinearError as error:
            outcomes.append({'channel_id':item['channel_id'],'conversation_id':item['conversation_id'],
                'agent_id':item['agent_id'],'status':'skipped','reason':str(error)})
    return {'total':len(outcomes),'assigned':sum(o['status']=='assigned' for o in outcomes),
            'skipped':sum(o['status']=='skipped' for o in outcomes),'outcomes':outcomes}
