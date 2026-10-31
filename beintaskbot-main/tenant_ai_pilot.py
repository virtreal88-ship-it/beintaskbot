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
SCOPED_LIVE_TENANT = 'e09a5c24-093f-4141-992d-532052e00c90'
SCOPED_LIVE_CHANNEL = '96863d75-d272-4c93-a25e-ffb2b16ecae3'
SCOPED_LIVE_CUSTOMER = '994502072240@s.whatsapp.net'
SCOPED_LIVE_GROUP = '120363431915456655@g.us'
SCOPED_LIVE_AGENT = '67da1058-2ffb-48fa-bfd9-d3d01c1990d1'


def schema(cur):
    """Idempotent additive migration for exact-dialog attestations."""
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_ai_pilot_confirmations(
      tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
      channel_id UUID NOT NULL,
      conversation_id TEXT NOT NULL CHECK(length(conversation_id) BETWEEN 1 AND 200),
      scope_fingerprint TEXT NOT NULL CHECK(length(scope_fingerprint)=64),
      kwid_confirmed_by BIGINT NULL,
      kwid_confirmed_at TIMESTAMPTZ NULL,
      kwid_expires_at TIMESTAMPTZ NULL,
      owner_approved_by BIGINT NULL,
      owner_approved_at TIMESTAMPTZ NULL,
      owner_expires_at TIMESTAMPTZ NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CHECK ((kwid_confirmed_by IS NULL AND kwid_confirmed_at IS NULL AND kwid_expires_at IS NULL)
          OR (kwid_confirmed_by IS NOT NULL AND kwid_confirmed_at IS NOT NULL AND kwid_expires_at>kwid_confirmed_at)),
      CHECK ((owner_approved_by IS NULL AND owner_approved_at IS NULL AND owner_expires_at IS NULL)
          OR (owner_approved_by IS NOT NULL AND owner_approved_at IS NOT NULL
              AND kwid_confirmed_by IS NOT NULL AND owner_expires_at>owner_approved_at
                  AND owner_expires_at<=kwid_expires_at)))""")


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


def configured_group_scope():
    """Return the separately configured developer group for the two-target pilot."""
    scope = configured_scope()
    if not scope:
        return None
    group = str(os.environ.get('AI_AGENTS_PILOT_GROUP_CONVERSATION_ID') or '').strip()
    # Do not accept arbitrary groups from a request or a database listing.
    from tenant_ai_group_coordination import group_jid
    customer=scope['conversation_id']
    local=customer[:-len('@s.whatsapp.net')] if customer.endswith('@s.whatsapp.net') else ''
    if (scope['tenant_id'] != SCOPED_LIVE_TENANT or scope['channel_id'] != SCOPED_LIVE_CHANNEL
            or not local.isdigit() or not 8 <= len(local) <= 15 or group != SCOPED_LIVE_GROUP
            or not group_jid(group)):
        return None
    return {**scope, 'conversation_id': group}


def scoped_live_enabled():
    """Dedicated opt-in for the scoped pilot; independent of global auto-send."""
    return _enabled('AI_AGENTS_SCOPED_LIVE_ENABLED') and configured_group_scope() is not None


def is_pilot_group_target(tenant_id, channel_id, conversation_id):
    scope = configured_group_scope()
    return bool(scoped_live_enabled() and scope
        and str(tenant_id) == scope['tenant_id']
        and str(channel_id) == scope['channel_id']
        and str(conversation_id) == scope['conversation_id'])


def is_pilot_customer_target(tenant_id, channel_id, conversation_id):
    scope = configured_scope()
    return bool(scoped_live_enabled() and scope
        and str(tenant_id) == SCOPED_LIVE_TENANT
        and str(channel_id) == SCOPED_LIVE_CHANNEL
        and str(conversation_id) == scope['conversation_id']
        and scope['tenant_id'] == SCOPED_LIVE_TENANT
        and scope['channel_id'] == SCOPED_LIVE_CHANNEL)


def manual_live_job(job):
    return (str(job.get('provider') or '') == 'connect'
        and is_pilot_customer_target(job.get('tenant_id'),job.get('channel_id'),job.get('conversation_id')))


def test_client_override_allowed(cur, tenant_id, channel_id, conversation_id, occurred_at):
    """Authorize an employee-identified inbound as the owner's pilot test client.

    This never changes provider direction or employee identity. It only permits
    the exact configured direct peer to enter the already-armed pilot pipeline.
    """
    scope = configured_scope()
    if (not scope or not scoped_live_enabled()
            or _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED')
            or not runtime_flag('AI_AGENTS_ENABLED')
            or str(tenant_id) != scope['tenant_id']
            or str(channel_id) != scope['channel_id']
            or str(conversation_id) != scope['conversation_id']):
        return False
    try:
        from datetime import datetime
        # Provider timestamps are required so an old employee message cannot
        # be revived by later approvals.
        event_time = datetime.fromisoformat(str(occurred_at).replace('Z', '+00:00'))
        if event_time.tzinfo is None:
            return False
        if not _lock_scope(cur, scope['tenant_id'], scope):
            return False
        row, fingerprint = _snapshot(cur, scope['tenant_id'], scope)
        if (not fingerprint or row.get('conversation_state') != 'active'
                or row.get('conversation_ai_enabled') is not True
                or row.get('conversation_manual_hold') is True
                or row.get('active') is not True or row.get('integration_status') != 'connected'
                or row.get('tenant_status') != 'active' or row.get('company_enabled') is not True
                or row.get('emergency_stop') is True or row.get('agent_enabled') is not True
                or str(row.get('agent_id') or '') != SCOPED_LIVE_AGENT
                or row.get('agent_mode') != 'manual' or not row.get('exact_agent_scope')):
            return False
        cur.execute('''SELECT (kwid_expires_at>clock_timestamp()
              AND owner_expires_at>clock_timestamp()
              AND kwid_confirmed_by IS NOT NULL AND owner_approved_by IS NOT NULL
              AND kwid_confirmed_at<=%s::timestamptz
              AND owner_approved_at<=%s::timestamptz) AS valid
          FROM saas_ai_pilot_confirmations
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
            AND scope_fingerprint=%s''',
            (event_time.isoformat(), event_time.isoformat(), scope['tenant_id'],
             scope['channel_id'], scope['conversation_id'], fingerprint))
        return bool((cur.fetchone() or {}).get('valid'))
    except Exception:
        return False


def assert_pilot_group_target(tenant_id, channel_id, conversation_id):
    """Fail closed when the scoped live pilot attempts a non-approved group."""
    if scoped_live_enabled() and not is_pilot_group_target(tenant_id, channel_id, conversation_id):
        raise TenantLinearError('Destination is outside the configured two-address pilot scope.', 409)


def _snapshot(cur, tenant, scope):
    cur.execute('''SELECT ch.active, ch.updated_at AS channel_version,
            i.status AS integration_status, i.updated_at AS integration_version,
            cv.conversation_id, cv.lead_id AS conversation_lead_id,
            cv.ai_enabled AS conversation_ai_enabled,
            cv.manual_hold AS conversation_manual_hold,
            cs.state AS conversation_state, cs.reason AS handoff_reason,
            s.enabled AS company_enabled, s.auto_send_enabled,
            s.emergency_stop, s.updated_at AS settings_version, t.status AS tenant_status,
            a.id::text AS agent_id, a.enabled AS agent_enabled, a.mode AS agent_mode,
            a.version AS agent_version, a.updated_at AS agent_settings_version, a.scopes,
            a.response_policy
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
        # Conversation state/reason change during this pilot's expected
        # human->active workflow. Runtime gates check those independently;
        # pause/revoke explicitly clears the owner approval below.
        row.get('conversation_manual_hold'), row.get('company_enabled'),
        row.get('auto_send_enabled'), row.get('emergency_stop'),
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


def processing_allowed(job, connection=None):
    """Fence all AI/CRM stages to one explicitly armed and confirmed dialog."""
    scope = configured_scope()
    manual_live = manual_live_job(job)
    armed = _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED') or manual_live
    exact = bool(scope and str(job.get('provider') or '') == 'connect'
        and str(job.get('tenant_id') or '') == scope['tenant_id']
        and str(job.get('channel_id') or '') == scope['channel_id']
        and str(job.get('conversation_id') or '') == scope['conversation_id'])
    # A claimed pilot run stays bound to its original exact scope through send.
    if job.get('_pilot_scope_fenced') is True:
        if not exact or not armed:
            return False
        return automatic_send_allowed(job, connection)
    if not scope:
        return not armed
    if not exact:
        return not armed
    if not armed:
        return False
    allowed = automatic_send_allowed(job, connection)
    if allowed:
        job['_pilot_scope_fenced'] = True
    return allowed


def automatic_send_allowed(job, connection=None):
    """Guard automatic sends to the configured pilot dialog at the send boundary.

    With no pilot configuration, existing behavior is unchanged. Once a scope
    is configured, that exact conversation is fail-closed even while the pilot
    switch is off; arming the pilot also blocks every other conversation.
    """
    scope = configured_scope()
    manual_live = manual_live_job(job)
    armed = _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED') or manual_live
    if job.get('_pilot_scope_fenced') is True and (not scope or not armed):
        return False
    if not scope:
        return not armed
    tenant = str(job.get('tenant_id') or '')
    conversation = str(job.get('conversation_id') or job.get('chat_id') or '')
    channel = str(job.get('channel_id') or '')
    if str(job.get('provider') or '') != 'connect' or tenant != scope['tenant_id'] or channel != scope['channel_id'] or conversation != scope['conversation_id']:
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
                            or (not manual_live and row.get('auto_send_enabled') is not True)
                            or row.get('emergency_stop') is True or row.get('agent_enabled') is not True
                            or (not manual_live and row.get('agent_mode') != 'automatic') or not row.get('exact_agent_scope')
                            or (job.get('agent_id') and str(row.get('agent_id') or '') != str(job.get('agent_id')))
                            or (job.get('agent_id') and str(row.get('agent_id') or '') != str(job.get('agent_id')))
                            or (not manual_live and not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'))
                            or (manual_live and not runtime_flag('AI_AGENTS_ENABLED'))):
                        return False
                    cur.execute('''SELECT (c.kwid_expires_at>clock_timestamp() AND c.owner_expires_at>clock_timestamp()
                      AND e.received_at>=GREATEST(c.kwid_confirmed_at,c.owner_approved_at)) AS valid
                    FROM saas_ai_pilot_confirmations c JOIN saas_tenant_webhook_events e
                      ON e.tenant_id=c.tenant_id AND e.fingerprint=%s
                    WHERE c.tenant_id=%s::uuid AND c.channel_id=%s::uuid
                      AND c.conversation_id=%s AND c.scope_fingerprint=%s
                      AND c.kwid_confirmed_by IS NOT NULL AND c.owner_approved_by IS NOT NULL FOR SHARE OF c,e''',
                    (str(job.get('event_fingerprint') or ''), tenant, scope['channel_id'], scope['conversation_id'], fingerprint))
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
                    or (not manual_live and row.get('auto_send_enabled') is not True)
                    or row.get('emergency_stop') is True or row.get('agent_enabled') is not True
                    or (not manual_live and row.get('agent_mode') != 'automatic') or not row.get('exact_agent_scope')
                    or (job.get('agent_id') and str(row.get('agent_id') or '') != str(job.get('agent_id')))
                    or (not manual_live and not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'))
                    or (manual_live and not runtime_flag('AI_AGENTS_ENABLED'))):
                return False
            cur.execute('''SELECT (c.kwid_expires_at>clock_timestamp() AND c.owner_expires_at>clock_timestamp()
                      AND e.received_at>=GREATEST(c.kwid_confirmed_at,c.owner_approved_at)) AS valid
                    FROM saas_ai_pilot_confirmations c JOIN saas_tenant_webhook_events e
                      ON e.tenant_id=c.tenant_id AND e.fingerprint=%s
                    WHERE c.tenant_id=%s::uuid AND c.channel_id=%s::uuid
                      AND c.conversation_id=%s AND c.scope_fingerprint=%s
                      AND c.kwid_confirmed_by IS NOT NULL AND c.owner_approved_by IS NOT NULL FOR SHARE OF c,e''',
                    (str(job.get('event_fingerprint') or ''), tenant, scope['channel_id'], scope['conversation_id'], fingerprint))
            return bool((cur.fetchone() or {}).get('valid'))
    except Exception:
        return False


def pilot_group_handoff_allowed(job, connection=None):
    """Validate the exact customer-side handoff immediately before group send."""
    if not scoped_live_enabled() or not manual_live_job(job):
        return False
    scope = configured_scope()
    tenant = str(job.get('tenant_id') or '')
    if not scope or tenant != scope['tenant_id'] or not _enabled('AI_AGENTS_SCOPED_LIVE_ENABLED'):
        return False
    try:
        if connection is None:
            from tenant_platform import _connect
            with _connect() as conn:
                return pilot_group_handoff_allowed(job, conn)
        with connection.cursor() as cur:
            if not _lock_scope(cur, tenant, scope):
                return False
            row, fingerprint = _snapshot(cur, tenant, scope)
            handoff_reason=str(row.get('handoff_reason') or '')
            group_turn_active=(row.get('conversation_state')=='active'
                and handoff_reason=='pilot_developer_followup'
                and isinstance(job.get('payload'),dict)
                and (job['payload'].get('pilot_developer_followup') is True
                     or job['payload'].get('pilot_customer_case_reply') is True))
            initial_handoff=(row.get('conversation_state')=='human'
                and handoff_reason.startswith('ai_developer_consultation:'))
            if (not fingerprint or not (initial_handoff or group_turn_active)
                    or row.get('conversation_ai_enabled') is not True
                    or row.get('conversation_manual_hold') is True
                    or row.get('active') is not True or row.get('integration_status') != 'connected'
                    or row.get('tenant_status') != 'active' or row.get('company_enabled') is not True
                    or row.get('emergency_stop') is True or row.get('agent_enabled') is not True
                    or row.get('agent_mode') != 'manual' or not row.get('exact_agent_scope')
                    or (job.get('agent_id') and str(row.get('agent_id') or '') != str(job.get('agent_id')))
                    or not runtime_flag('AI_AGENTS_ENABLED')):
                return False
            cur.execute('''SELECT (c.kwid_expires_at>clock_timestamp()
                    AND c.owner_expires_at>clock_timestamp()
                    AND e.received_at>=GREATEST(c.kwid_confirmed_at,c.owner_approved_at)) AS valid
              FROM saas_ai_pilot_confirmations c
              JOIN saas_tenant_webhook_events e ON e.tenant_id=c.tenant_id AND e.fingerprint=%s
              WHERE c.tenant_id=%s::uuid AND c.channel_id=%s::uuid
                AND c.conversation_id=%s AND c.scope_fingerprint=%s
                AND c.kwid_confirmed_by IS NOT NULL AND c.owner_approved_by IS NOT NULL
              FOR SHARE OF c,e''', (str(job.get('event_fingerprint') or ''),tenant,
                scope['channel_id'],scope['conversation_id'],fingerprint))
            return bool((cur.fetchone() or {}).get('valid'))
    except Exception:
        return False


def _valid_consultation_continuation(cur, tenant, scope):
    """Require one live CASE consultation with a delivered developer-group receipt."""
    group_scope = configured_group_scope()
    if not group_scope or group_scope['tenant_id'] != str(tenant):
        return False
    cur.execute('''SELECT count(DISTINCT c.id)=1 AS valid
      FROM saas_internal_consultations c
      JOIN saas_support_tickets t ON t.tenant_id=c.tenant_id AND t.id=c.case_id
      JOIN saas_consultation_group_deliveries d ON d.tenant_id=c.tenant_id
        AND d.consultation_id=c.id AND d.case_id=c.case_id
        AND d.channel_id=%s::uuid AND d.conversation_id=%s
        AND d.state='sent' AND d.provider_message_id IS NOT NULL
      WHERE c.tenant_id=%s::uuid AND c.channel_id=%s::uuid AND c.conversation_id=%s
        AND c.status<>'closed' AND t.status NOT IN ('resolved','closed')''',
      (group_scope['channel_id'], group_scope['conversation_id'], tenant,
       scope['channel_id'], scope['conversation_id']))
    return bool((cur.fetchone() or {}).get('valid'))


def _authorized_scope(cur, profile):
    from tenant_ai_agents_store import _authorize
    tenant, actor = _authorize(cur, profile)
    scope = configured_scope()
    if not scope or scope['tenant_id'] != str(tenant):
        raise TenantLinearError('Exact pilot scope is unavailable for this company.', 409)
    row, fingerprint = _snapshot(cur, tenant, scope)
    if not fingerprint or not row.get('active') or row.get('integration_status') != 'connected':
        raise TenantLinearError('The exact Connect channel and conversation must be active before confirmation.', 409)
    common_ok = (row.get('conversation_manual_hold') is not True
            and row.get('tenant_status') == 'active' and row.get('company_enabled') is True
            and row.get('emergency_stop') is not True)
    if not common_ok:
        raise TenantLinearError('The selected customer dialogue is paused or the company is unavailable.', 409)
    state = row.get('conversation_state')
    if state == 'active':
        return tenant, actor, scope, row, fingerprint
    reason = str(row.get('handoff_reason') or '')
    if (state != 'human' or not reason.startswith('ai_developer_consultation:')
            or not _valid_consultation_continuation(cur, tenant, scope)):
        raise TenantLinearError('Only the same open developer consultation can renew pilot attestations.', 409)
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


def resume_pilot_dialog(profile, data):
    """Owner-only, exact-dialog preparation resume while both pilot guards are off.

    This changes only the configured customer conversation state. The existing
    Connect resume path still enforces unique route resolution and never queues
    old messages. It cannot be used to arm either runtime guard.
    """
    if not isinstance(data, dict) or data != {'confirmed': True}:
        raise TenantLinearError('Explicit confirmation is required to resume the configured test dialogue.', 400)
    if (_enabled('AI_AGENTS_SCOPED_LIVE_ENABLED')
            or _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED')):
        raise TenantLinearError('Preparation resume is available only while both pilot guards are disabled.', 409)
    scope = configured_scope()
    group_scope = configured_group_scope()
    if (not scope or not group_scope or scope['tenant_id'] != SCOPED_LIVE_TENANT
            or scope['channel_id'] != SCOPED_LIVE_CHANNEL
            or group_scope['conversation_id'] != SCOPED_LIVE_GROUP):
        raise TenantLinearError('The exact two-target pilot scope is not configured.', 409)

    from tenant_platform import _connect
    from tenant_ai_agents_store import _authorize, set_conversation_state
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            if str(tenant) != SCOPED_LIVE_TENANT:
                raise TenantLinearError('The configured pilot belongs to another company.', 403)
            cur.execute('''SELECT role FROM saas_tenant_members
              WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE FOR SHARE''', (tenant, actor))
            if (cur.fetchone() or {}).get('role') != 'owner':
                raise TenantLinearError('Only the company owner can resume the configured pilot dialogue.', 403)
            row, _fingerprint = _snapshot(cur, tenant, scope)
            if (row.get('conversation_id') != scope['conversation_id']
                    or row.get('active') is not True or row.get('integration_status') != 'connected'
                    or row.get('tenant_status') != 'active' or row.get('company_enabled') is not True
                    or row.get('emergency_stop') is True
                    or row.get('conversation_ai_enabled') is not True
                    or row.get('conversation_manual_hold') is True
                    or row.get('conversation_state') not in {'human', 'paused'}
                    or str(row.get('agent_id') or '') != SCOPED_LIVE_AGENT
                    or row.get('agent_enabled') is not True or row.get('agent_mode') != 'manual'
                    or not row.get('exact_agent_scope')):
                raise TenantLinearError('The exact test dialogue is not eligible for preparation resume.', 409)
        conn.commit()

    # This established path rechecks the exact provider route under its dialog
    # lock. In particular, it keeps the existing legacy ambiguity 409 intact.
    set_conversation_state(profile, scope['conversation_id'], 'active',
        'pilot_preparation_resume', 'connect', scope['channel_id'])
    return preflight(profile)


def preflight(profile):
    """Read tenant-owned pilot state; no writes, AI calls, or KWID API are used."""
    from tenant_platform import _connect
    from tenant_ai_agents_store import _authorize
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, _actor = _authorize(cur, profile)
            scope = configured_scope()
            group_scope=configured_group_scope()
            scoped_live=scoped_live_enabled()
            result = {'ready': False, 'provider': 'connect', 'pilot_guard_enabled': _enabled('AI_AGENTS_PILOT_SCOPE_ENABLED') or scoped_live,
                    'scoped_live':{'enabled':scoped_live,'customer_target':scope['conversation_id'] if scope else '',
                        'developer_group_target':SCOPED_LIVE_GROUP if group_scope else '',
                        'mode_override':'exact pilot only' if scoped_live else 'disabled'},
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
            group_row={}
            if group_scope:
                cur.execute('''SELECT c.lead_id,g.state,
                    NOT EXISTS(SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=c.tenant_id
                      AND j.provider='connect' AND j.channel_id=c.connect_channel_id::text
                      AND j.conversation_id=c.conversation_id AND j.state IN ('queued','processing')) AS no_pending_jobs
                  FROM saas_connect_conversations c JOIN saas_connect_channels ch
                    ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
                  JOIN saas_tenant_integrations i ON i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected'
                  LEFT JOIN saas_ai_group_coordination g ON g.tenant_id=c.tenant_id
                    AND g.provider='connect' AND g.group_jid=c.conversation_id
                  WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s''',
                  (tenant,group_scope['channel_id'],group_scope['conversation_id']))
                group_row=cur.fetchone() or {}
            result['scoped_live']['developer_group_route']='verified' if group_row else 'unavailable'
            result['scoped_live']['developer_group_state']=group_row.get('state') or 'uninitialized'
            # In the scoped pilot the owner-attested WhatsApp group is the
            # consultation destination. A tenant employee ID is not required.
            group_ready=bool(group_scope and group_row.get('lead_id'))
            result['scoped_live']['consultation_destination']='pilot_group' if group_ready else 'unavailable'
            result['auto_send']['company'] = row.get('auto_send_enabled') is True
            result['channel'] = 'active' if row.get('active') is True and row.get('integration_status') == 'connected' else 'unavailable'
            result['conversation'] = 'verified' if row.get('conversation_id') else 'unavailable'
            result['agent'] = 'eligible' if (row.get('agent_enabled') is True and (row.get('agent_mode') == 'automatic'
                or (scoped_live and row.get('agent_mode')=='manual'))
                and row.get('exact_agent_scope')) else 'unavailable'
            result['handoff'] = str(row.get('conversation_state') or 'uninitialized')
            result['continuation_renewal'] = bool(row.get('conversation_state') == 'human'
                and str(row.get('handoff_reason') or '').startswith('ai_developer_consultation:')
                and _valid_consultation_continuation(cur, tenant, scope))
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
            if row.get('tenant_status') != 'active' or row.get('company_enabled') is not True or row.get('emergency_stop') is True:
                result['blockers'].append('company_ai_not_in_active_state')
            if (not scoped_live and (row.get('auto_send_enabled') is not True or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'))):
                result['blockers'].append('automatic_send_not_enabled')
            # An uninitialized coordination row means the group is not assigned
            # to automation. It is safe to initialize it paused on first send.
            if scoped_live and (not group_row or group_row.get('state') not in {'human','paused',None}
                    or group_row.get('no_pending_jobs') is not True):
                result['blockers'].append('developer_group_route_not_stopped_and_verified')
            if scoped_live and not group_ready:
                result['blockers'].append('developer_group_route_unconfigured_or_unavailable')
            if row.get('conversation_state') != 'active': result['blockers'].append('conversation_not_active_or_handed_off')
            if not result['pilot_guard_enabled']: result['blockers'].append('pilot_guard_not_armed')
            if result['kwid_confirmation']['state'] != 'confirmed': result['blockers'].append('kwid_confirmation_missing_expired_or_scope_changed')
            if result['separate_owner_approval']['state'] != 'approved': result['blockers'].append('separate_owner_approval_missing_or_expired')
            result['ready'] = not result['blockers']
            return result
