"""Single-dialog pilot scope, short-lived KWID attestation, and read-only preflight."""
import hashlib
import json
import os
import uuid
from datetime import timedelta

from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_linear_policy import TenantLinearError

_TRUE = {'1', 'true', 'yes', 'on'}
CONFIRMATION_MINUTES = 15


def _enabled(name):
    return str(os.environ.get(name) or '').strip().casefold() in _TRUE


def configured_scope():
    """Read the exact server-configured Connect scope, even while guard is off."""
    try:
        tenant = str(uuid.UUID(str(os.environ.get('AI_AGENTS_PILOT_TENANT_ID') or '').strip()))
        channel = str(uuid.UUID(str(os.environ.get('AI_AGENTS_PILOT_CHANNEL_ID') or '').strip()))
    except (ValueError, TypeError):
        return None
    conversation = str(os.environ.get('AI_AGENTS_PILOT_CONVERSATION_ID') or '').strip()
    if not conversation or len(conversation) > 200 or any(ord(c) < 32 for c in conversation):
        return None
    return {'tenant_id': tenant, 'channel_id': channel, 'conversation_id': conversation}


def _snapshot(cur, tenant, scope):
    cur.execute('''SELECT ch.active, ch.updated_at AS channel_version,
            i.status AS integration_status, i.updated_at AS integration_version,
            cv.conversation_id, cv.lead_id AS conversation_lead_id,
            cv.ai_enabled AS conversation_ai_enabled,
            cv.manual_hold AS conversation_manual_hold,
            cs.state AS conversation_state, cs.reason AS handoff_reason,
            s.enabled AS company_enabled, s.operating_mode, s.auto_send_enabled,
            s.emergency_stop, s.updated_at AS settings_version, t.status AS tenant_status,
            a.id::text AS agent_id, a.enabled AS agent_enabled, a.mode AS agent_mode,
            a.version AS agent_version, a.updated_at AS agent_settings_version, a.scopes
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
    if not row.get('conversation_id'):
        return row, ''
    scopes = row.get('scopes') if isinstance(row.get('scopes'), list) else []
    group = scope['conversation_id'].endswith('@g.us')
    row['exact_agent_scope'] = any(isinstance(item, dict) and item.get('provider') == 'connect'
        and str(item.get('channel_id') or '') == scope['channel_id']
        and (not group or item.get('allow_group_messages') is True) for item in scopes)
    version = [scope['tenant_id'], scope['channel_id'], scope['conversation_id'], row.get('active'),
        str(row.get('channel_version') or ''), row.get('integration_status'),
        str(row.get('integration_version') or ''), row.get('conversation_lead_id'), row.get('conversation_ai_enabled'),
        row.get('conversation_manual_hold'), row.get('conversation_state'), row.get('handoff_reason'), row.get('company_enabled'),
        row.get('operating_mode'), row.get('auto_send_enabled'), row.get('emergency_stop'),
        str(row.get('settings_version') or ''), row.get('tenant_status'), row.get('agent_id'),
        row.get('agent_enabled'), row.get('agent_mode'), row.get('agent_version'),
        str(row.get('agent_settings_version') or ''), row['exact_agent_scope'],
        _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED'), runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')]
    return row, hashlib.sha256(json.dumps(version, sort_keys=True, default=str).encode()).hexdigest()


def _confirmation(cur, tenant, scope, fingerprint):
    cur.execute('''SELECT kwid_confirmed_by,kwid_confirmed_at,kwid_expires_at,
        owner_approved_by,owner_approved_at,owner_expires_at
        FROM saas_ai_pilot_confirmations WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
          AND conversation_id=%s AND scope_fingerprint=%s''',
        (tenant, scope['channel_id'], scope['conversation_id'], fingerprint))
    row = cur.fetchone() or {}
    cur.execute('''SELECT (kwid_expires_at>clock_timestamp()) AS kwid_valid,
        (owner_expires_at>clock_timestamp()) AS owner_valid FROM saas_ai_pilot_confirmations
        WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
          AND scope_fingerprint=%s''', (tenant, scope['channel_id'], scope['conversation_id'], fingerprint))
    validity = cur.fetchone() or {}
    return row, validity


def _lock_scope(cur, tenant, scope):
    """Fence scope/settings edits until the provider accepts or rejects send."""
    cur.execute('''SELECT 1 FROM saas_connect_channels ch
        JOIN saas_tenant_integrations i ON i.tenant_id=ch.tenant_id AND i.provider='connect'
        JOIN saas_tenants t ON t.id=ch.tenant_id
        WHERE ch.tenant_id=%s::uuid AND ch.connect_channel_id=%s::uuid
        FOR SHARE OF ch,i,t''', (tenant, scope['channel_id']))
    if not cur.fetchone():
        return False
    cur.execute('''SELECT 1 FROM saas_connect_conversations
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s FOR SHARE''',
        (tenant, scope['channel_id'], scope['conversation_id']))
    if not cur.fetchone():
        return False
    cur.execute('SELECT 1 FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid FOR SHARE', (tenant,))
    if not cur.fetchone():
        return False
    cur.execute('''SELECT 1 FROM saas_ai_conversation_state cs
        JOIN saas_ai_agents a ON a.tenant_id=cs.tenant_id AND a.id=cs.agent_id
        WHERE cs.tenant_id=%s::uuid AND cs.provider='connect' AND cs.channel_id=%s
          AND cs.conversation_id=%s FOR SHARE OF cs,a''',
        (tenant, scope['channel_id'], scope['conversation_id']))
    return bool(cur.fetchone())


def automatic_send_allowed(job, connection=None):
    """Guard automatic sends to the configured pilot dialog at the send boundary.

    With no pilot configuration, existing behavior is unchanged. Once a scope
    is configured, that exact conversation is fail-closed even while the pilot
    switch is off; arming the pilot also blocks every other conversation.
    """
    scope = configured_scope()
    armed = _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED')
    if not scope:
        return not armed
    tenant = str(job.get('tenant_id') or '')
    conversation = str(job.get('conversation_id') or job.get('chat_id') or '')
    channel = str(job.get('channel_id') or '')
    if tenant != scope['tenant_id'] or channel != scope['channel_id'] or conversation != scope['conversation_id']:
        return not armed
    # A configured pilot scope must never be sent to before the owner arms it.
    if not armed:
        return False
    try:
        if connection is None:
            from tenant_platform import _connect
            with _connect() as conn:
                with conn.cursor() as cur:
                    if not _lock_scope(cur, tenant, scope):
                        return False
                    row, fingerprint = _snapshot(cur, tenant, scope)
                    if (not fingerprint or row.get('conversation_state') != 'active'
                            or row.get('conversation_ai_enabled') is not True
                            or row.get('conversation_manual_hold') is True
                            or row.get('active') is not True or row.get('integration_status') != 'connected'
                            or row.get('tenant_status') != 'active' or row.get('company_enabled') is not True
                            or row.get('operating_mode') != 'active' or row.get('auto_send_enabled') is not True
                            or row.get('emergency_stop') is True or row.get('agent_enabled') is not True
                            or row.get('agent_mode') != 'automatic' or not row.get('exact_agent_scope')
                            or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')):
                        return False
                    cur.execute('''SELECT (kwid_expires_at>clock_timestamp() AND owner_expires_at>clock_timestamp()) AS valid
                        FROM saas_ai_pilot_confirmations WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
                        AND conversation_id=%s AND scope_fingerprint=%s AND kwid_confirmed_by IS NOT NULL
                        AND owner_approved_by IS NOT NULL FOR SHARE''',
                        (tenant, scope['channel_id'], scope['conversation_id'], fingerprint))
                    return bool((cur.fetchone() or {}).get('valid'))
        with connection.cursor() as cur:
            if not _lock_scope(cur, tenant, scope):
                return False
            row, fingerprint = _snapshot(cur, tenant, scope)
            if (not fingerprint or row.get('conversation_state') != 'active'
                    or row.get('conversation_ai_enabled') is not True
                    or row.get('conversation_manual_hold') is True
                    or row.get('active') is not True or row.get('integration_status') != 'connected'
                    or row.get('tenant_status') != 'active' or row.get('company_enabled') is not True
                    or row.get('operating_mode') != 'active' or row.get('auto_send_enabled') is not True
                    or row.get('emergency_stop') is True or row.get('agent_enabled') is not True
                    or row.get('agent_mode') != 'automatic' or not row.get('exact_agent_scope')
                    or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')):
                return False
            cur.execute('''SELECT (kwid_expires_at>clock_timestamp() AND owner_expires_at>clock_timestamp()) AS valid
                FROM saas_ai_pilot_confirmations WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
                AND conversation_id=%s AND scope_fingerprint=%s AND kwid_confirmed_by IS NOT NULL
                AND owner_approved_by IS NOT NULL FOR SHARE''',
                (tenant, scope['channel_id'], scope['conversation_id'], fingerprint))
            return bool((cur.fetchone() or {}).get('valid'))
    except Exception:
        return False


def _authorized_scope(cur, profile):
    from tenant_ai_agents_store import _authorize
    tenant, actor = _authorize(cur, profile)
    scope = configured_scope()
    if not scope or scope['tenant_id'] != str(tenant):
        raise TenantLinearError('Exact pilot scope is unavailable for this company.', 409)
    row, fingerprint = _snapshot(cur, tenant, scope)
    if not fingerprint or not row.get('active') or row.get('integration_status') != 'connected':
        raise TenantLinearError('The exact Connect channel and conversation must be active before confirmation.', 409)
    return tenant, actor, scope, row, fingerprint


def confirm_kwid_disabled(profile, data):
    """Record an admin's explicit, short-lived confirmation; never contacts KWID."""
    if not isinstance(data, dict) or data.get('confirmed') is not True:
        raise TenantLinearError('Explicit KWID confirmation is required.', 400)
    from tenant_ai_agents_schema import ensure_schema
    from tenant_platform import _connect
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, scope, _row, fingerprint = _authorized_scope(cur, profile)
            cur.execute('''INSERT INTO saas_ai_pilot_confirmations
                (tenant_id,channel_id,conversation_id,scope_fingerprint,kwid_confirmed_by,kwid_confirmed_at,kwid_expires_at)
                VALUES(%s::uuid,%s::uuid,%s,%s,%s,now(),now()+(%s * interval '1 minute'))
                ON CONFLICT(tenant_id) DO UPDATE SET channel_id=EXCLUDED.channel_id,
                  conversation_id=EXCLUDED.conversation_id,scope_fingerprint=EXCLUDED.scope_fingerprint,
                  kwid_confirmed_by=EXCLUDED.kwid_confirmed_by,kwid_confirmed_at=now(),
                  kwid_expires_at=EXCLUDED.kwid_expires_at,owner_approved_by=NULL,
                  owner_approved_at=NULL,owner_expires_at=NULL,updated_at=now()''',
                (tenant, scope['channel_id'], scope['conversation_id'], fingerprint, actor, CONFIRMATION_MINUTES))
        conn.commit()
    return preflight(profile)


def approve_pilot(profile, data):
    """Record a separate owner decision; this does not arm the runtime guard."""
    if not isinstance(data, dict) or data.get('approved') is not True:
        raise TenantLinearError('Separate owner approval is required.', 400)
    from tenant_ai_agents_schema import ensure_schema
    from tenant_platform import _connect
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, scope, _row, fingerprint = _authorized_scope(cur, profile)
            cur.execute('SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE FOR SHARE', (tenant, actor))
            if (cur.fetchone() or {}).get('role') != 'owner':
                raise TenantLinearError('Only the company owner can separately approve the pilot.', 403)
            cur.execute('''UPDATE saas_ai_pilot_confirmations SET owner_approved_by=%s,
                owner_approved_at=now(),owner_expires_at=LEAST(kwid_expires_at,now()+(%s * interval '1 minute')),updated_at=now()
                WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
                  AND scope_fingerprint=%s AND kwid_confirmed_by IS NOT NULL AND kwid_expires_at>now()
                RETURNING tenant_id''', (actor, CONFIRMATION_MINUTES, tenant, scope['channel_id'], scope['conversation_id'], fingerprint))
            if not cur.fetchone():
                raise TenantLinearError('A current KWID-disabled confirmation is required first.', 409)
        conn.commit()
    return preflight(profile)


def preflight(profile):
    """Read tenant-owned pilot state; no writes, AI calls, or KWID API are used."""
    from tenant_platform import _connect
    from tenant_ai_agents_store import _authorize
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, _actor = _authorize(cur, profile)
            scope = configured_scope()
            result = {'ready': False, 'provider': 'connect', 'pilot_guard_enabled': _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED'),
                    'scope_configured': bool(scope), 'competing_handler': {'state':'unknown','source':'manual_confirmation_required'},
                    'separate_owner_approval': {'state':'missing'},
                    'auto_send': {'server': runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'), 'company': False},
                    'channel':'unknown','conversation':'unknown','agent':'unknown','handoff':'unknown',
                    'kwid_confirmation':{'state':'missing'},
                    'rollback':{'available':False,'action':'pause conversation; WhatsApp session remains connected'},'blockers':[]}
            if not scope:
                result['blockers'].append('exact_pilot_scope_missing')
                return result
            if scope['tenant_id'] != str(tenant):
                result['blockers'].append('pilot_scope_not_owned_by_current_tenant')
                return result
            result['scope'] = dict(scope)
            row, fingerprint = _snapshot(cur, tenant, scope)
            result['auto_send']['company'] = row.get('auto_send_enabled') is True
            result['channel'] = 'active' if row.get('active') is True and row.get('integration_status') == 'connected' else 'unavailable'
            result['conversation'] = 'verified' if row.get('conversation_id') else 'unavailable'
            result['agent'] = 'eligible' if (row.get('agent_enabled') is True and row.get('agent_mode') == 'automatic'
                and row.get('exact_agent_scope')) else 'unavailable'
            result['handoff'] = str(row.get('conversation_state') or 'uninitialized')
            result['rollback'] = {'available':bool(row.get('conversation_id')),'action':'pause this conversation; WhatsApp session remains connected'}
            confirmation, validity = _confirmation(cur, tenant, scope, fingerprint) if fingerprint else ({}, {})
            if confirmation.get('kwid_confirmed_by'):
                state = 'confirmed' if validity.get('kwid_valid') else 'expired'
                result['kwid_confirmation'] = {'state':state,'confirmed_by':confirmation.get('kwid_confirmed_by'),
                    'confirmed_at':confirmation.get('kwid_confirmed_at'),'expires_at':confirmation.get('kwid_expires_at')}
            if confirmation.get('owner_approved_by'):
                state = 'approved' if validity.get('owner_valid') else 'expired'
                result['separate_owner_approval'] = {'state':state,'approved_by':confirmation.get('owner_approved_by'),
                    'approved_at':confirmation.get('owner_approved_at'),'expires_at':confirmation.get('owner_expires_at')}
            if result['kwid_confirmation']['state'] == 'confirmed': result['competing_handler']={'state':'disabled','source':'admin_attestation'}
            if result['channel'] != 'active': result['blockers'].append('connect_channel_or_integration_unavailable')
            if result['conversation'] != 'verified': result['blockers'].append('conversation_not_found_on_selected_channel')
            if result['agent'] != 'eligible': result['blockers'].append('automatic_agent_not_active_for_conversation')
            if row.get('tenant_status') != 'active' or row.get('company_enabled') is not True or row.get('operating_mode') != 'active' or row.get('emergency_stop') is True:
                result['blockers'].append('company_ai_not_in_active_state')
            if row.get('auto_send_enabled') is not True or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'):
                result['blockers'].append('automatic_send_not_enabled')
            if row.get('conversation_state') != 'active': result['blockers'].append('conversation_not_active_or_handed_off')
            if not result['pilot_guard_enabled']: result['blockers'].append('pilot_guard_not_armed')
            if result['kwid_confirmation']['state'] != 'confirmed': result['blockers'].append('kwid_confirmation_missing_expired_or_scope_changed')
            if result['separate_owner_approval']['state'] != 'approved': result['blockers'].append('separate_owner_approval_missing_or_expired')
            result['ready'] = not result['blockers']
            return result
