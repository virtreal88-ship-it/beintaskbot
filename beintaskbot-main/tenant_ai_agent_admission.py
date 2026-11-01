"""Shared server-side admission checks for real AI Agent jobs.

The old company Test Mode/binding tables are intentionally not consulted here.
The virtual test chat has an isolated storage and execution path.
"""
from tenant_ai_agents_schema import ensure_schema
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect


def allowed_event(cur, tenant_id, account_domain, event=None):
    """Allow only an active tenant's connected Kommo account."""
    cur.execute('''SELECT s.enabled,s.emergency_stop,t.status
      FROM saas_ai_agent_settings s JOIN saas_tenants t ON t.id=s.tenant_id
      WHERE s.tenant_id=%s::uuid FOR SHARE OF s,t''', (str(tenant_id),))
    settings = cur.fetchone() or {}
    if settings.get('enabled') is not True or settings.get('emergency_stop') is True or settings.get('status') != 'active':
        return False
    cur.execute('''SELECT 1 FROM saas_tenant_integrations WHERE tenant_id=%s::uuid
      AND provider='kommo' AND status='connected' AND lower(account_domain)=lower(%s)''',
      (str(tenant_id), str(account_domain or '')))
    return bool(cur.fetchone())


async def enrich_webhook_rows(data, rows, request, *, fallback_domain=''):
    """Resolve incomplete Kommo callbacks using the live tenant CRM source."""
    from tenant_ai_agent_queue import _account_domain
    from tenant_chat_reader import read_talks
    domain = _account_domain(data if isinstance(data, dict) else {}, fallback_domain)
    if not domain or not isinstance(rows, list):
        return []
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT i.tenant_id FROM saas_tenant_integrations i
              JOIN saas_tenants t ON t.id=i.tenant_id AND t.status='active'
              JOIN saas_ai_agent_settings s ON s.tenant_id=i.tenant_id AND s.enabled=TRUE AND s.emergency_stop=FALSE
              WHERE i.provider='kommo' AND i.status='connected' AND lower(i.account_domain)=lower(%s)''', (domain,))
            tenants = cur.fetchall()
        conn.commit()
    if len(tenants) != 1:
        return []
    tenant = str(tenants[0]['tenant_id'])
    result = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            lead_id = int(raw.get('entity_id') or raw.get('element_id') or 0)
            talk_id = str(int(raw.get('talk_id') or 0))
            chat_id = str(raw.get('chat_id') or '').strip()
        except (TypeError, ValueError):
            continue
        if lead_id <= 0 or not talk_id or not chat_id:
            continue
        try:
            lead = await request(tenant, 'GET', f'leads/{lead_id}', params={'with': 'contacts'})
            if int(lead.get('id') or 0) != lead_id:
                continue
            talks = await read_talks(tenant, lead_id, request)
            matches = [talk for talk in talks if str(talk.get('talk_id') or talk.get('id') or '') == talk_id
                       and str(talk.get('chat_id') or '') == chat_id]
            if len(matches) != 1:
                continue
            contact_id = raw.get('contact_id') or raw.get('contactId') or 0
            result.append({**raw, 'contact_id': contact_id,
                           'origin': str(matches[0].get('origin') or raw.get('origin') or '')})
        except Exception:
            # Failure to verify a live CRM route never degrades into ID-only admission.
            continue
    return result


def conversation_allowed(cur, tenant_id, chat_id):
    """Check that a Kommo conversation is tenant-owned and still connected."""
    cur.execute('''SELECT 1 FROM saas_tenant_integrations i
      JOIN saas_ai_agent_settings s ON s.tenant_id=i.tenant_id AND s.enabled=TRUE
      JOIN saas_tenants t ON t.id=i.tenant_id AND t.status='active'
      WHERE i.tenant_id=%s::uuid AND i.provider='kommo' AND i.status='connected'
        AND EXISTS (SELECT 1 FROM saas_ai_conversation_state cs
          WHERE cs.tenant_id=i.tenant_id AND cs.provider='kommo' AND cs.conversation_id=%s)
      LIMIT 1''', (str(tenant_id), str(chat_id)))
    return bool(cur.fetchone())


def allowed_job(job):
    """Recheck company, provider route and agent authorization before work."""
    provider = str(job.get('provider') or '')
    from tenant_ai_agent_runtime_flags import enabled as runtime_flag
    from tenant_ai_pilot import manual_live_job
    pilot_live=manual_live_job(job)
    if not runtime_flag('AI_AGENTS_ENABLED') or (not pilot_live and not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')):
        return False
    if provider == 'connect':
        from tenant_ai_agent_connect import allowed as connect_allowed
        return connect_allowed(job)
    if provider != 'kommo':
        return False
    from tenant_ai_dialog_scope import job_scope
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT 1 FROM saas_ai_agent_settings s
              JOIN saas_tenants t ON t.id=s.tenant_id
              JOIN saas_tenant_integrations i ON i.tenant_id=s.tenant_id
                AND i.provider='kommo' AND i.status='connected'
              JOIN saas_ai_agents a ON a.tenant_id=s.tenant_id AND a.id=%s::uuid
                AND a.enabled=TRUE AND a.role='primary'
              WHERE s.tenant_id=%s::uuid AND s.enabled=TRUE AND s.emergency_stop=FALSE
                AND t.status='active' AND i.account_domain<>''
                AND EXISTS(SELECT 1 FROM saas_ai_conversation_state cs
                  WHERE cs.tenant_id=s.tenant_id AND cs.provider='kommo'
                    AND cs.conversation_id=%s AND cs.channel_id=%s
                    AND cs.agent_id=a.id) ''',
              (str(job.get('agent_id') or ''), str(job.get('tenant_id') or ''),
               str(job.get('conversation_id') or ''),job_scope(job)))
            ok = bool(cur.fetchone())
        conn.commit()
    return ok


def assert_job_allowed(job):
    from tenant_ai_pilot import processing_allowed
    if not processing_allowed(job):
        raise TenantLinearError('AI pilot scope is unavailable, unconfirmed, or does not match this conversation.', 409)
    if not allowed_job(job):
        raise TenantLinearError('AI Agent, company, or connected conversation is no longer authorized.', 409)


async def assert_live_job_allowed(job, talk, lead, request=None):
    """Bind a queued Kommo job to its current live Talk/deal/channel."""
    assert_job_allowed(job)
    if (str(talk.get('talk_id') or talk.get('id') or '') != str(job.get('talk_id') or '')
            or str(talk.get('chat_id') or '') != str(job.get('chat_id') or job.get('conversation_id') or '')
            or str(talk.get('origin') or '').casefold() != str(job.get('channel_id') or '').casefold()
            or int(lead.get('id') or 0) != int(job.get('lead_id') or 0)):
        raise TenantLinearError('Live Kommo chat, channel or deal differs from the queued event.', 409)
