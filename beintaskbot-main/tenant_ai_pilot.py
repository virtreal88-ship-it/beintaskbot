"""Single-dialog pilot scope, short-lived KWID attestation, and read-only preflight."""
import hashlib
import json
import os
import uuid

from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_linear_policy import TenantLinearError

_TRUE = {'1', 'true', 'yes', 'on'}
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
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_pilot_episode_cutoffs(
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,
      reset_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),reset_by BIGINT NOT NULL,
      PRIMARY KEY(tenant_id,channel_id,conversation_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_pilot_episode_resets(
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      request_id UUID NOT NULL,channel_id UUID NOT NULL,
      conversation_id TEXT NOT NULL,reset_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      reset_by BIGINT NOT NULL,PRIMARY KEY(tenant_id,request_id))''')


def episode_cutoff(job):
    """Return the reset boundary for the exact configured customer pilot."""
    scope=configured_scope()
    if not scope or not manual_live_job(job) or any(str(job.get(k) or '')!=scope[k]
            for k in ('tenant_id','channel_id','conversation_id')):
        return None
    from tenant_platform import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT reset_at FROM saas_ai_pilot_episode_cutoffs
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s''',
              (scope['tenant_id'],scope['channel_id'],scope['conversation_id']))
            row=cur.fetchone() or {}
        conn.commit()
    return row.get('reset_at')


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


def _manual_case_followup(cur,tenant,job,row):
    """A saved developer turn never overrides the current Manual assignment.

    Older linked jobs may predate the enqueue guard, so use their persisted
    event payload as well as the worker's payload at the final admission fence.
    No receipt, assignment, setting or customer message is changed here.
    """
    if row.get('agent_mode')!='manual':return False
    payload=job.get('payload') if isinstance(job.get('payload'),dict) else {}
    flags=('pilot_developer_followup','pilot_customer_case_reply')
    if any(payload.get(key) is True for key in flags):return True
    if not job.get('event_fingerprint'):return False
    cur.execute('SELECT payload FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND fingerprint=%s',
      (tenant,str(job['event_fingerprint'])))
    stored=(cur.fetchone() or {}).get('payload') or {}
    return isinstance(stored,dict) and any(stored.get(key) is True for key in flags)


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
                    if _manual_case_followup(cur,tenant,job,row):return False
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
            if _manual_case_followup(cur,tenant,job,row):return False
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
