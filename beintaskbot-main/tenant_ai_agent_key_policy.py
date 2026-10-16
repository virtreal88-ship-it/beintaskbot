"""Explicit, tenant-and-agent scoped platform credential policy."""
import os


def platform_agent_pairs():
    """Return only exact tenant/agent allowlist pairs from Railway env."""
    result = set()
    for item in str(os.environ.get('AI_AGENTS_PLATFORM_KEY_AGENTS') or '').split(','):
        tenant, separator, agent = item.strip().partition(':')
        if separator and tenant.strip() and agent.strip():
            result.add((tenant.strip().lower(), agent.strip().lower()))
    return result


def platform_model():
    return str(os.environ.get('OPENAI_HISTORY_REPLY_MODEL') or '').strip()


def platform_agent_enabled(tenant_id, agent_id, provider='connect'):
    if str(provider or '').lower() != 'connect':
        return False
    pair = (str(tenant_id or '').strip().lower(), str(agent_id or '').strip().lower())
    return bool(pair[0] and pair[1] and pair in platform_agent_pairs())


def platform_key_ready(tenant_id, agent_id, provider='connect'):
    return (platform_agent_enabled(tenant_id, agent_id, provider)
            and bool(os.environ.get('OPENAI_API_KEY'))
            and bool(platform_model())
            and bool(str(os.environ.get('AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT') or '').strip()))


def skip_pre_activation_jobs(cur):
    """Terminally skip only allowlisted Connect jobs received before cutover."""
    pairs = [tenant + ':' + agent for tenant, agent in platform_agent_pairs()]
    activated_at = str(os.environ.get('AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT') or '').strip()
    if not pairs or not activated_at:
        return False
    cur.execute('''UPDATE saas_tenant_webhook_events e SET state='skipped',completed_at=now(),
        error_code='platform_key_activation_cutoff'
      WHERE e.provider='connect' AND e.state IN ('queued','processing')
        AND e.received_at < %s::timestamptz AND EXISTS (
          SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=e.tenant_id
            AND j.event_fingerprint=e.fingerprint AND j.provider='connect'
            AND (j.tenant_id::text || ':' || j.agent_id::text)=ANY(%s)
            AND j.state IN ('queued','processing'))''', (activated_at, pairs))
    cur.execute('''UPDATE saas_ai_agent_jobs j SET state='skipped',revision=revision+1,
        lease_token=NULL,lease_until=NULL,error_code='platform_key_activation_cutoff',updated_at=now()
      WHERE j.provider='connect' AND j.state IN ('queued','processing')
        AND (j.tenant_id::text || ':' || j.agent_id::text)=ANY(%s)
        AND EXISTS (SELECT 1 FROM saas_tenant_webhook_events e
          WHERE e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
            AND e.received_at < %s::timestamptz)''', (pairs, activated_at))
    return True
