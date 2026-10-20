"""Fail-closed, single-dialog automatic-send guard and read-only pilot preflight."""
import os
import uuid

from tenant_ai_agent_runtime_flags import enabled as runtime_flag

_TRUE = {'1', 'true', 'yes', 'on'}

def _enabled(name):
    return str(os.environ.get(name) or '').strip().casefold() in _TRUE

def configured_scope():
    """Return an exact Connect dialog scope; incomplete config is never a scope."""
    if not _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED'):
        return None
    try:
        tenant = str(uuid.UUID(str(os.environ.get('AI_AGENTS_PILOT_TENANT_ID') or '').strip()))
        channel = str(uuid.UUID(str(os.environ.get('AI_AGENTS_PILOT_CHANNEL_ID') or '').strip()))
    except (ValueError, TypeError):
        return None
    conversation = str(os.environ.get('AI_AGENTS_PILOT_CONVERSATION_ID') or '').strip()
    if not conversation or len(conversation) > 200 or any(ord(c) < 32 for c in conversation):
        return None
    return {'tenant_id': tenant, 'channel_id': channel, 'conversation_id': conversation}

def competing_handler_state():
    """Only an explicit owner-supplied server attestation counts as disabled."""
    state = str(os.environ.get('AI_AGENTS_PILOT_COMPETING_HANDLER_STATE') or '').strip().casefold()
    return state if state in {'disabled', 'enabled'} else 'unknown'

def automatic_send_allowed(job):
    """When pilot guard is armed, allow only the exact, externally cleared dialog."""
    if not _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED'):
        return True  # Existing deployments retain their existing policy.
    scope = configured_scope()
    if not scope or competing_handler_state() != 'disabled':
        return False
    tenant = str(job.get('tenant_id') or '')
    conversation = str(job.get('conversation_id') or job.get('chat_id') or '')
    channel = str(job.get('channel_id') or (job.get('route') or {}).get('origin') or '')
    return (tenant == scope['tenant_id'] and channel == scope['channel_id']
            and conversation == scope['conversation_id'])

def preflight(profile):
    """Read tenant-owned route/settings; never edits state or invokes AI/provider."""
    from tenant_platform import _connect
    from tenant_ai_agents_store import _authorize
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, _actor = _authorize(cur, profile)
            scope = configured_scope()
            result = {'ready': False, 'provider': 'connect', 'pilot_guard_enabled': _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED'),
                      'scope_configured': bool(scope), 'competing_handler': competing_handler_state(),
                      'competing_handler_source': 'server_environment_attestation' if competing_handler_state() != 'unknown' else 'unavailable',
                      'auto_send': {'server': runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'), 'company': False},
                      'channel': 'unknown', 'conversation': 'unknown', 'agent': 'unknown',
                      'handoff': 'unknown', 'rollback': {'available': False, 'action': 'pause conversation'}, 'blockers': []}
            if not scope:
                result['blockers'].append('exact_pilot_scope_missing_or_disabled')
                return result
            if scope['tenant_id'] != str(tenant):
                result['blockers'].append('pilot_scope_not_owned_by_current_tenant')
                return result
            result['scope'] = dict(scope)
            cur.execute('''SELECT ch.active, i.status AS integration_status, cv.conversation_id,
                    cs.state AS conversation_state, cs.reason AS handoff_reason,
                    s.enabled AS company_enabled, s.operating_mode, s.auto_send_enabled,
                    s.emergency_stop, t.status AS tenant_status,
                    a.id::text AS agent_id, a.enabled AS agent_enabled, a.mode AS agent_mode,
                    a.scopes
                FROM saas_connect_channels ch
                JOIN saas_tenant_integrations i ON i.tenant_id=ch.tenant_id AND i.provider='connect'
                JOIN saas_tenants t ON t.id=ch.tenant_id
                LEFT JOIN saas_connect_conversations cv ON cv.tenant_id=ch.tenant_id
                    AND cv.connect_channel_id=ch.connect_channel_id AND cv.conversation_id=%s
                LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=cv.tenant_id AND cs.provider='connect'
                    AND cs.channel_id=ch.connect_channel_id::text AND cs.conversation_id=cv.conversation_id
                LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=ch.tenant_id
                LEFT JOIN saas_ai_agents a ON a.tenant_id=cs.tenant_id AND a.id=cs.agent_id
                WHERE ch.tenant_id=%s::uuid AND ch.connect_channel_id=%s::uuid''',
                (scope['conversation_id'], tenant, scope['channel_id']))
            row = cur.fetchone() or {}
    result['auto_send']['company'] = row.get('auto_send_enabled') is True
    result['channel'] = 'active' if row.get('active') is True and row.get('integration_status') == 'connected' else 'unavailable'
    result['conversation'] = 'verified' if row.get('conversation_id') else 'unavailable'
    scopes = row.get('scopes') if isinstance(row.get('scopes'), list) else []
    group = str(scope['conversation_id']).endswith('@g.us')
    exact_agent_scope = any(isinstance(item, dict) and item.get('provider') == 'connect'
        and str(item.get('channel_id') or '') == scope['channel_id']
        and (not group or item.get('allow_group_messages') is True) for item in scopes)
    result['agent'] = 'eligible' if (row.get('agent_enabled') is True and row.get('agent_mode') == 'automatic'
        and exact_agent_scope) else 'unavailable'
    result['handoff'] = str(row.get('conversation_state') or 'uninitialized')
    result['rollback'] = {'available': bool(row.get('conversation_id')), 'action': 'pause this conversation; WhatsApp session remains connected'}
    if result['channel'] != 'active': result['blockers'].append('connect_channel_or_integration_unavailable')
    if result['conversation'] != 'verified': result['blockers'].append('conversation_not_found_on_selected_channel')
    if result['agent'] != 'eligible': result['blockers'].append('automatic_agent_not_active_for_conversation')
    if row.get('tenant_status') != 'active' or row.get('company_enabled') is not True or row.get('operating_mode') != 'active' or row.get('emergency_stop') is True:
        result['blockers'].append('company_ai_not_in_active_state')
    if row.get('auto_send_enabled') is not True or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'):
        result['blockers'].append('automatic_send_not_enabled')
    if row.get('conversation_state') != 'active': result['blockers'].append('conversation_not_active_or_handed_off')
    if not result['pilot_guard_enabled']: result['blockers'].append('pilot_guard_not_armed')
    if result['competing_handler'] != 'disabled': result['blockers'].append('competing_handler_not_confirmed_disabled')
    result['ready'] = not result['blockers']
    return result
