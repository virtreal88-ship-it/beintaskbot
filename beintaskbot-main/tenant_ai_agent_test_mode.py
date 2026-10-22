"""Fail-closed, tenant-scoped binding for isolated AI Agents testing."""
import os
import re
from tenant_ai_agents_schema import ensure_schema
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect
from tenant_chat_reader import read_talks


def normalize_phone(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return '+' + digits if digits else ''

CLIENT_PHONE = normalize_phone(os.environ.get('AI_AGENTS_TEST_CLIENT_PHONE', ''))
COMPANY_PHONE = normalize_phone(os.environ.get('AI_AGENTS_TEST_COMPANY_PHONE', ''))


def exact_binding(binding, *, tenant_id, account_domain, lead_id, contact_id, talk_id, chat_id, channel_id):
    """Return true only for a complete exact match; missing fields never match."""
    if (not CLIENT_PHONE or not COMPANY_PHONE or not isinstance(binding, dict) or binding.get('enabled') is not True or binding.get('emergency_stop') is True
            or binding.get('verified') is not True or binding.get('company_phone_attested') is not True
            or normalize_phone(binding.get('company_phone'))!=COMPANY_PHONE
            or normalize_phone(binding.get('client_phone'))!=CLIENT_PHONE):
        return False
    required = ('account_domain', 'channel_id', 'lead_id', 'contact_id', 'talk_id', 'chat_id')
    if any(binding.get(key) in (None, '') for key in required):
        return False
    return (str(binding.get('tenant_id') or '') == str(tenant_id or '')
        and str(binding['account_domain']).casefold() == str(account_domain or '').casefold()
        and str(binding['channel_id']).casefold() == str(channel_id or '').casefold()
        and str(binding['lead_id']) == str(lead_id or '')
        and str(binding['contact_id']) == str(contact_id or '')
        and str(binding['talk_id']) == str(talk_id or '')
        and str(binding['chat_id']) == str(chat_id or ''))


def _db_binding(cur, tenant_id, *, lock=False):
    cur.execute('''SELECT b.*,i.account_domain AS connected_account_domain
      FROM saas_ai_agent_test_mode b
      JOIN saas_tenant_integrations i ON i.tenant_id=b.tenant_id AND i.provider='kommo' AND i.status='connected'
      WHERE b.tenant_id=%s::uuid''' + (' FOR SHARE OF b,i' if lock else ''), (str(tenant_id),))
    row = cur.fetchone()
    if not row:
        return None
    value = dict(row)
    value['tenant_id'] = str(value.get('tenant_id') or tenant_id)
    return value


def allowed_event(cur, tenant_id, account_domain, event):
    """Admission policy for both modes.

    Test mode retains the exact immutable binding.  Active mode accepts only
    the tenant's connected Kommo account; the worker later verifies the live
    Talk, lead and configured agent scope before any model or CRM action.
    """
    cur.execute('''SELECT s.operating_mode,s.emergency_stop,s.enabled,t.status
      FROM saas_ai_agent_settings s JOIN saas_tenants t ON t.id=s.tenant_id
      WHERE s.tenant_id=%s::uuid FOR SHARE OF s,t''', (str(tenant_id),))
    settings = cur.fetchone() or {}
    if not isinstance(settings, dict):
        settings = {}
    # Empty settings is retained for legacy unit-level callers; real schema
    # always creates the row before webhook admission.
    if settings and (settings.get('enabled') is not True or settings.get('emergency_stop') is True or settings.get('status') != 'active'):
        return False
    if settings.get('operating_mode') == 'active':
        cur.execute('''SELECT 1 FROM saas_tenant_integrations WHERE tenant_id=%s::uuid
          AND provider='kommo' AND status='connected' AND lower(account_domain)=lower(%s)''',
          (str(tenant_id),str(account_domain or '')))
        return bool(cur.fetchone())
    binding = _db_binding(cur, tenant_id)
    if not binding or str(binding.get('connected_account_domain') or '').casefold() != str(account_domain or '').casefold():
        return False
    return exact_binding(binding, tenant_id=tenant_id, account_domain=account_domain,
        lead_id=event.get('lead_id'), contact_id=event.get('contact_id'),
        talk_id=event.get('talk_id'), chat_id=event.get('chat_id'), channel_id=event.get('channel'))


async def enrich_webhook_rows(data, rows, request, *, fallback_domain=''):
    """Resolve incomplete webhook rows without weakening the selected mode.

    Test Mode keeps its immutable two-number binding.  Active Mode uses the
    connected tenant only; each row is still re-read and checked against the
    live Talk before the worker can process it.
    """
    from tenant_ai_agent_queue import _account_domain
    domain=_account_domain(data if isinstance(data,dict) else {},fallback_domain)
    if not domain or not isinstance(rows,list): return []
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT s.operating_mode,s.emergency_stop,s.enabled,b.*,i.account_domain AS connected_account_domain
              FROM saas_tenant_integrations i JOIN saas_tenants t ON t.id=i.tenant_id AND t.status='active'
              JOIN saas_ai_agent_settings s ON s.tenant_id=i.tenant_id AND s.enabled=TRUE AND s.emergency_stop=FALSE
              LEFT JOIN saas_ai_agent_test_mode b ON b.tenant_id=i.tenant_id
              WHERE i.provider='kommo' AND i.status='connected' AND i.account_domain=%s AND (
                s.operating_mode='active' OR (b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.company_phone_attested=TRUE AND b.company_phone=%s AND b.client_phone=%s))''',(domain,COMPANY_PHONE,CLIENT_PHONE))
            bindings=[dict(row) for row in cur.fetchall()]
        conn.commit()
    if len(bindings)!=1: return []
    binding=bindings[0]; tenant=str(binding['tenant_id']); active_mode=binding.get('operating_mode') == 'active'; result=[]
    for raw in rows:
        if not isinstance(raw,dict): continue
        try:
            lead_id=int(raw.get('entity_id') or raw.get('element_id') or 0)
            talk_id=str(int(raw.get('talk_id') or 0)); chat_id=str(raw.get('chat_id') or '').strip()
        except (TypeError,ValueError): continue
        # IDs present in Test Mode must match exactly.  Active Mode resolves
        # the current live route below instead of inheriting a hidden allowlist.
        if not active_mode and (lead_id!=int(binding['lead_id']) or talk_id!=str(binding['talk_id'])
                or chat_id!=str(binding['chat_id'])): continue
        try:
            lead=await request(tenant,'GET',f'leads/{lead_id}',params={'with':'contacts'})
            if int(lead.get('id') or 0)!=lead_id: continue
            embedded=lead.get('_embedded') if isinstance(lead.get('_embedded'),dict) else {}
            contacts=embedded.get('contacts') if isinstance(embedded.get('contacts'),list) else []
            if not active_mode:
                if sum(1 for c in contacts if isinstance(c,dict) and str(c.get('id') or '')==str(binding['contact_id']))!=1: continue
                contact=await request(tenant,'GET',f"contacts/{int(binding['contact_id'])}")
                if int(contact.get('id') or 0)!=int(binding['contact_id']): continue
                cfv=contact.get('custom_fields_values') or (contact.get('_embedded') or {}).get('custom_fields_values') or []
                phones=[normalize_phone(v.get('value')) for f in cfv if isinstance(f,dict) and str(f.get('field_code') or '').upper()=='PHONE'
                    for v in (f.get('values') or []) if isinstance(v,dict)]
                if phones.count(CLIENT_PHONE)!=1: continue
            talks=await read_talks(tenant,lead_id,request)
            matches=[talk for talk in talks if str(talk.get('talk_id') or talk.get('id') or '')==talk_id
                and str(talk.get('chat_id') or '')==chat_id]
            if len(matches)!=1: continue
            if not active_mode and str(matches[0].get('origin') or '').casefold()!=str(binding['channel_id']).casefold(): continue
            if not active_mode and raw.get('contact_id') and str(raw.get('contact_id'))!=str(binding['contact_id']): continue
            if not active_mode and raw.get('origin') and str(raw.get('origin')).casefold()!=str(binding['channel_id']).casefold(): continue
            result.append({**raw,'contact_id': int(raw.get('contact_id') or binding.get('contact_id') or 0),
                'origin':str(matches[0].get('origin') or raw.get('origin') or '')})
        except Exception:
            # A failed Kommo read never degrades into an ID-only allow.
            continue
    return result


def conversation_allowed(cur, tenant_id, chat_id):
    cur.execute('''SELECT operating_mode,enabled,emergency_stop FROM saas_ai_agent_settings
      WHERE tenant_id=%s::uuid''', (str(tenant_id),))
    settings=cur.fetchone() or {}
    if settings.get('operating_mode') == 'active':
        if settings.get('enabled') is not True or settings.get('emergency_stop') is True:
            return False
        cur.execute('''SELECT 1 FROM saas_ai_agent_jobs j JOIN saas_ai_agents a
          ON a.tenant_id=j.tenant_id AND a.id=j.agent_id WHERE j.tenant_id=%s::uuid
          AND j.provider='kommo' AND j.conversation_id=%s AND a.enabled=TRUE LIMIT 1''',
          (str(tenant_id),str(chat_id)))
        return bool(cur.fetchone())
    binding=_db_binding(cur,tenant_id)
    return bool(binding and binding.get('connected_account_domain') and exact_binding(binding,
        tenant_id=tenant_id,account_domain=binding.get('connected_account_domain'),lead_id=binding.get('lead_id'),
        contact_id=binding.get('contact_id'),talk_id=binding.get('talk_id'),chat_id=chat_id,channel_id=binding.get('channel_id')))


def allowed_job(job):
    # Connect carries a signed, tenant-owned channel identity and has its own
    # test binding.  It must never be forced through a Kommo account lookup.
    if str(job.get('provider') or '') == 'connect':
        from tenant_ai_agent_connect import allowed as connect_allowed
        return connect_allowed(job)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            binding = _db_binding(cur, job.get('tenant_id'), lock=True)
            cur.execute('''SELECT account_domain FROM saas_tenant_integrations
              WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected' FOR SHARE''', (str(job.get('tenant_id')),))
            integration = cur.fetchone() or {}
            cur.execute('''SELECT operating_mode,enabled,emergency_stop FROM saas_ai_agent_settings
              WHERE tenant_id=%s::uuid FOR SHARE''', (str(job.get('tenant_id')),))
            settings=cur.fetchone() or {}
            if not isinstance(settings,dict):
                settings={}
            active_mode=settings.get('operating_mode') == 'active'
            if settings and (settings.get('enabled') is not True or settings.get('emergency_stop') is True):
                return False
            payload = job.get('payload') or {}
            event = {'lead_id': job.get('lead_id'), 'contact_id': payload.get('contact_id'),
                'talk_id': job.get('talk_id'), 'chat_id': job.get('chat_id'),
                'channel_id': job.get('channel_id') or payload.get('channel')}
            ok = active_mode and bool(integration and integration.get('account_domain'))
            if not active_mode:
                ok = bool((not binding or not binding.get('agent_id') or str(binding['agent_id'])==str(job.get('agent_id'))) and exact_binding(binding, tenant_id=job.get('tenant_id'), account_domain=integration.get('account_domain'), **event))
            if ok and job.get('agent_id'):
                cur.execute('''SELECT 1 FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
                  JOIN saas_tenants t ON t.id=a.tenant_id WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid
                  AND a.enabled=TRUE AND s.enabled=TRUE AND t.status='active' ''',(str(job['tenant_id']),str(job['agent_id'])))
                ok=bool(cur.fetchone())
        conn.commit()
    return ok


def assert_job_allowed(job):
    from tenant_ai_pilot import processing_allowed
    if not processing_allowed(job):
        raise TenantLinearError('AI pilot scope is unavailable, unconfirmed, or does not match this conversation.', 409)
    if not allowed_job(job):
        raise TenantLinearError('AI Agents Test Mode is disabled or the exact test conversation binding changed.', 409)


async def assert_live_job_allowed(job, talk, lead, request):
    """Recheck live Kommo identities and the bound customer's phone in a worker."""
    assert_job_allowed(job)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            binding = _db_binding(cur, job.get('tenant_id'))
        conn.commit()
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT operating_mode FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid',(str(job['tenant_id']),))
            active_mode=(cur.fetchone() or {}).get('operating_mode') == 'active'
        conn.commit()
    if active_mode:
        if (str(talk.get('talk_id') or talk.get('id') or '') != str(job['talk_id'])
                or str(talk.get('chat_id') or '') != str(job['chat_id'])
                or str(talk.get('origin') or '').casefold() != str(job.get('channel_id') or '').casefold()
                or int(lead.get('id') or 0) != int(job['lead_id'])):
            raise TenantLinearError('Live Kommo chat, channel or deal differs from the queued event.',409)
        return
    if not binding:
        raise TenantLinearError('Test Mode binding is unavailable.',409)
    tenant=str(job['tenant_id'])
    if (str(talk.get('talk_id') or talk.get('id') or '')!=str(binding['talk_id'])
            or str(talk.get('chat_id') or '')!=str(binding['chat_id'])
            or str(talk.get('origin') or '').casefold()!=str(binding['channel_id']).casefold()
            or int(lead.get('id') or 0)!=int(binding['lead_id'])):
        raise TenantLinearError('Live Kommo chat, channel or deal differs from the Test Mode binding.',409)
    embedded=lead.get('_embedded') if isinstance(lead.get('_embedded'),dict) else {}
    contacts=embedded.get('contacts') if isinstance(embedded.get('contacts'),list) else []
    if sum(1 for row in contacts if isinstance(row,dict) and str(row.get('id') or '')==str(binding['contact_id']))!=1:
        raise TenantLinearError('Bound test contact is no longer uniquely linked to the deal.',409)
    contact=await request(tenant,'GET',f"contacts/{int(binding['contact_id'])}")
    if int(contact.get('id') or 0)!=int(binding['contact_id']):
        raise TenantLinearError('Live Kommo contact identity changed.',409)
    cfv=contact.get('custom_fields_values') or (contact.get('_embedded') or {}).get('custom_fields_values') or []
    phones=[normalize_phone(v.get('value')) for f in cfv if isinstance(f,dict) and str(f.get('field_code') or '').upper()=='PHONE'
        for v in (f.get('values') or []) if isinstance(v,dict)]
    if phones.count(CLIENT_PHONE)!=1:
        raise TenantLinearError('Bound Kommo contact no longer matches the test client number.',409)
    assert_job_allowed(job)
