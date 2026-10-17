"""Unified, redacted AI state over the existing diagnostics and reliability readers.

This is a read projection, never an admission, retry, send or Resume authority.
Operators can read a summary; technical details require current AI management
permission on the server. No settings, payloads or credentials are exported.
"""
import hashlib
import os
import uuid
from datetime import datetime, timezone

from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agents_store import _authorize, read_connect_diagnostics
from tenant_ai_channel_catalog import read_catalog, channel_key
from tenant_ai_reliability import read_snapshot, reason
from tenant_ai_agent_schedule import is_open, next_open
from tenant_ai_agent_key_policy import platform_key_ready, platform_model, platform_model_allowed, platform_service_ready
from tenant_ai_agent_access import tenant_status_allows_ai
from tenant_ai_agent_runtime_flags import enabled
from tenant_operator_access import access
from tenant_platform import _connect


def _uuid(value):
    try: return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError): return None


def _safe_schedule(value):
    if not isinstance(value, dict): return {}
    return {key: value[key] for key in ('timezone', 'days', 'start', 'end') if key in value}


def _agent_state(agent, channels, system, chat, worker_alive, tenant, now, bindings):
    """Eligibility is conditional on a new, fully scoped inbound message."""
    schedule = _safe_schedule(agent.get('schedule'))
    try:
        scheduled = is_open(schedule, now=now)
        next_start = next_open(schedule, now=now)
    except (ValueError, TypeError, KeyError):
        scheduled, next_start = False, None
    common = []
    if not agent.get('enabled'): common.append('agent_disabled')
    if not enabled('AI_AGENTS_ENABLED'): common.append('ai_agents_server_disabled')
    if not system.get('company_ai_enabled'): common.append('company_ai_disabled')
    if system.get('emergency_stop'): common.append('emergency_stop')
    if not worker_alive: common.append('worker_unavailable')
    if not scheduled: common.append('outside_schedule')
    selected = []
    scopes = agent.get('scopes') if isinstance(agent.get('scopes'), list) else []
    for scope in scopes:
        if not isinstance(scope, dict): continue
        provider = scope.get('provider', 'kommo')
        cid = str(scope.get('channel_id') or '')
        key = channel_key(provider, cid)
        channel = channels.get(key)
        codes = list(common)
        if not tenant_status_allows_ai(system.get('tenant_status'), provider): codes.append('tenant_inactive')
        if provider == 'kommo' and not cid:
            # Existing wildcard Kommo scopes remain supported. Do not invent
            # channel records or silently rewrite them to a selected channel.
            if not system.get('kommo_connected'): codes.append('integration_disconnected')
        elif not channel:
            codes.append('channel_not_in_catalog')
        else:
            codes.extend(channel['ai']['reason_codes'])
        if provider == 'kommo' and (not scope.get('pipeline_id') or not scope.get('stage_id')):
            codes.append('kommo_scope_incomplete')
        model = agent.get('model') or (platform_model() if platform_service_ready() else '') or chat.get('model') or ''
        use_platform = platform_key_ready(tenant, agent['id'], provider) and (not chat.get('has_key') or platform_model_allowed(model))
        if use_platform:
            if not model or not platform_model_allowed(model): codes.append('model_unavailable')
        elif not (chat.get('has_key') is True and chat.get('enabled') is True and chat.get('consent') is True):
            codes.append('ai_configuration_not_ready')
        elif not model or model not in (chat.get('models') or []) + [chat.get('model')]:
            codes.append('model_unavailable')
        if system.get('operating_mode') != 'active':
            codes.append('test_mode_draft_only')
            binding = bindings.get(provider) or {}
            if not (binding.get('enabled') is True and binding.get('verified') is True
                    and binding.get('emergency_stop') is not True
                    and (not binding.get('agent_id') or str(binding['agent_id']) == str(agent['id']))
                    and (not cid or str(binding.get('channel_id')) == cid)):
                codes.append('test_binding_mismatch')
        elif agent.get('mode') != 'automatic': codes.append('manual_mode')
        elif not (system.get('auto_send_enabled') and enabled('AI_AGENTS_AUTO_SEND_ENABLED')):
            codes.append('auto_send_disabled')
        hard = [code for code in codes if code not in {'manual_mode', 'test_mode_draft_only', 'auto_send_disabled'}]
        state = ('disabled' if 'agent_disabled' in codes else 'outside_schedule' if hard == ['outside_schedule']
                 else 'blocked' if hard else 'test' if 'test_mode_draft_only' in codes else
                 'manual' if 'manual_mode' in codes or 'auto_send_disabled' in codes else 'ready')
        selected.append({'id': key, 'provider': provider, 'channel_id': cid,
                         'name': channel['name'] if channel else 'Каналы CRM' if provider == 'kommo' and not cid else 'Недоступный канал',
                         'type': channel['type'] if channel else 'unknown', 'state': state,
                         'reason_codes': list(dict.fromkeys(codes)),
                         'group_messages_allowed': provider == 'connect' and scope.get('allow_group_messages') is True,
                         'scope': {'provider': provider, **{k: str(scope.get(k) or '') for k in ('channel_id', 'pipeline_id', 'stage_id')}}})
    states = {s['state'] for s in selected}
    status = ('disabled' if not agent.get('enabled') else 'blocked' if not selected else
              'ready' if 'ready' in states else 'manual' if 'manual' in states else
              'test' if 'test' in states else 'outside_schedule' if states == {'outside_schedule'} else 'blocked')
    return {'id': str(agent['id']), 'name': str(agent.get('name') or 'AI Agent'),
            'enabled': bool(agent.get('enabled')), 'mode': 'automatic' if agent.get('mode') == 'automatic' else 'manual',
            'state': status, 'schedule': schedule, 'schedule_open': scheduled,
            'next_shift_at': next_start.isoformat() if next_start else None,
            'channels': selected, 'recent_events': []}


def _event_routes(cur, tenant, messages, policy):
    """Batch exact route checks. Never create a link from a contact/JID alone."""
    if not messages: return {}
    cur.execute('''SELECT e.external_id,e.provider,
      COALESCE(e.payload->>'channel_id',e.payload->>'channel','') AS channel_id,
      e.tenant_id,c.lead_id AS connect_lead_id,d.kommo_lead_id,d.pipeline_id,d.status_id
      FROM saas_tenant_webhook_events e
      LEFT JOIN saas_connect_conversations c ON e.provider='connect' AND c.tenant_id=e.tenant_id
        AND c.connect_channel_id::text=e.payload->>'channel_id' AND c.conversation_id=e.payload->>'conversation_id'
      LEFT JOIN saas_crm_deals d ON e.provider='kommo' AND d.tenant_id=e.tenant_id AND d.deleted_at IS NULL
        AND d.kommo_lead_id::text=e.payload->>'lead_id'
      WHERE e.tenant_id=%s::uuid AND e.external_id=ANY(%s::text[])''',
                (tenant, [m['message_id'] for m in messages]))
    result = {}
    for row in cur.fetchall():
        lead = row.get('connect_lead_id') if row['provider'] == 'connect' else row.get('kommo_lead_id')
        if not lead: continue
        if row['provider'] == 'kommo' and policy.profile.get('role') != 'owner' and not policy.can_access_deal(row): continue
        result[(row['provider'], row['channel_id'], row['external_id'])] = {
            'provider': row['provider'], 'channel_id': row['channel_id'], 'lead_id': int(lead)}
    return result


def _event(message, agents, channels, route):
    code, _ = reason(message.get('reason_code'))
    category = 'error' if message['status'] == 'error' else 'human' if code in {
        'manager_replied', 'human_handoff', 'conversation_human', 'conversation_human_or_paused', 'conversation_paused_or_human',
        'connect_human_takeover', 'customer_requested_manager', 'trigger_reply_sent_then_handoff'} else 'policy' if code in {
        'legacy_channel_ambiguous', 'connect_policy_blocked', 'connect_admission_blocked'} else 'info'
    key = channel_key(message['provider'], message['channel_id'])
    agent = agents.get(message['agent_id'])
    channel = channels.get(key)
    # External message IDs and JIDs are never exposed as event names or report data.
    event_id = hashlib.sha256(('\0'.join([message['provider'], message['channel_id'], message['message_id'], message['agent_id']])).encode()).hexdigest()[:24]
    return {'id': event_id, 'agent_id': message['agent_id'] if agent else None,
            'agent_name': agent['name'] if agent else None, 'channel': {'id': key,
            'name': channel['name'] if channel else 'Канал CRM' if message['provider'] == 'kommo' else 'WhatsApp',
            'provider': message['provider']}, 'category': category, 'status': message['status'],
            'reason_code': code, 'occurred_at': message['received_at'], 'dialog': route,
            'action': 'open_dialog' if route else 'contact_administrator',
            'checkpoints': message['checkpoints']}


def snapshot(profile, limit=30, *, technical=False, now=None):
    now = now or datetime.now(timezone.utc)
    limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _, policy = access(cur, profile, 'dialogs')
            if technical:
                # Deliberately check the DB role/manage grant, never profile.role.
                _authorize(cur, profile)
            cur.execute("SET LOCAL statement_timeout='5000ms'")
            catalog = read_catalog(cur, tenant)
            diagnostics = read_connect_diagnostics(cur, tenant, limit)
            diagnostics['system']['kommo_connected'] = catalog['integrations']['kommo']
            reliability = read_snapshot(cur, tenant, limit)
            cur.execute('''SELECT id,name,enabled,mode,scopes,schedule,model FROM saas_ai_agents
              WHERE tenant_id=%s::uuid ORDER BY created_at,id''', (tenant,))
            agent_rows = cur.fetchall()
            cur.execute('''SELECT secrets IS NOT NULL AS has_key,metadata->>'model' AS model,
              metadata->'models' AS models,metadata->'enabled' AS enabled,metadata->'consent' AS consent
              FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' ''', (tenant,))
            chat = cur.fetchone() or {}
            if not isinstance(chat.get('models'), list): chat['models'] = []
            cur.execute('''SELECT 'connect' AS provider,agent_id,channel_id,enabled,verified,emergency_stop
              FROM saas_ai_agent_connect_test_mode WHERE tenant_id=%s::uuid
              UNION ALL SELECT 'kommo',agent_id,channel_id,enabled,verified,emergency_stop
              FROM saas_ai_agent_test_mode WHERE tenant_id=%s::uuid''', (tenant, tenant))
            bindings = {b['provider']: b for b in cur.fetchall()}
            channels = {item['id']: item for item in catalog['channels']}
            agents = {str(a['id']): _agent_state(a, channels, diagnostics['system'], chat,
                      reliability['health']['worker_alive'], tenant, now, bindings) for a in agent_rows}
            messages = reliability['messages']
            routes = _event_routes(cur, tenant, messages, policy)
            events = []
            for message in messages:
                route = routes.get((message['provider'], message['channel_id'], message['message_id']))
                # Kommo events inherit deal visibility even when no link exists.
                if message['provider'] == 'kommo' and not route and policy.profile.get('role') != 'owner': continue
                event = _event(message, agents, channels, route)
                events.append(event)
                if event['agent_id']: agents[event['agent_id']]['recent_events'].append(event)
            cur.execute("SELECT count(*) AS n FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=''", (tenant,))
            quarantined = int(cur.fetchone()['n'])
            cur.execute('''SELECT provider,channel_id,agent_id,state,count(*) AS n
              FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id<>''
              GROUP BY provider,channel_id,agent_id,state''', (tenant,))
            for state in cur.fetchall():
                agent = agents.get(str(state.get('agent_id')))
                if agent:
                    for channel in agent['channels']:
                        if channel['provider'] == state['provider'] and channel['channel_id'] == state['channel_id']:
                            channel.setdefault('dialogue_states', {})[state['state']] = int(state['n'])
    metrics = reliability['metrics']
    errors = int(metrics.get('ai_errors_24h') or 0)
    sends = int(metrics.get('unsuccessful_sends_24h') or 0)
    attention = bool(errors or sends or metrics.get('overdue') or metrics.get('expired_leases'))
    ready_count = sum(a['state'] in {'ready', 'manual', 'test'} for a in agents.values())
    result = {'schema_version': 1, 'generated_at': now.isoformat(),
              'summary': {'service': 'available' if reliability['health']['worker_alive'] else 'unavailable',
                          'state': 'attention' if attention else 'conditional' if ready_count else 'not_ready',
                          'enabled_agents': sum(a['enabled'] for a in agents.values()),
                          'eligible_agents': ready_count,
                          'connected_channels': sum(c['connection']['state'] == 'connected' for c in channels.values()),
                          'ai_errors_24h': errors, 'unconfirmed_sends_24h': sends,
                          'quarantined_legacy_dialogues': quarantined},
              'agents': list(agents.values()), 'events': events,
              'can_view_technical': policy.allows('ai_agents'),
              'limitations': ['readiness_is_conditional', 'cached_connection_status', 'quotas_checked_at_runtime',
                             'events_are_bounded', 'errors_cover_recorded_events_only', 'no_provider_health_probe'],
              'events_limit': limit, 'window_hours': 24}
    if technical:
        result['technical'] = {'tenant_id': tenant, 'deployment_id': _uuid(os.environ.get('RAILWAY_DEPLOYMENT_ID')),
                               'worker_last_seen_at': reliability['health']['worker_last_seen_at'],
                               'server_flags': {key: bool(value) for key, value in diagnostics['server_flags'].items()},
                               'queue': {key: int(value or 0) for key, value in metrics.items()},
                               'reason_codes': sorted({e['reason_code'] for e in events if e['reason_code']})}
    return result
