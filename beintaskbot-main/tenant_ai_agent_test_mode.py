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
                ok = exact_binding(binding, tenant_id=job.get('tenant_id'), account_domain=integration.get('account_domain'), **event)
            if ok and job.get('agent_id'):
                cur.execute('''SELECT 1 FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id
                  JOIN saas_tenants t ON t.id=a.tenant_id WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid
                  AND a.enabled=TRUE AND s.enabled=TRUE AND t.status='active' ''',(str(job['tenant_id']),str(job['agent_id'])))
                ok=bool(cur.fetchone())
        conn.commit()
    return ok


def assert_job_allowed(job):
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


async def assert_expected_route(tenant_id, lead_id, expected_route, request):
    """Final server-side guard for a reviewed AI draft send."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            binding=_db_binding(cur,tenant_id)
            integration=binding.get('connected_account_domain') if binding else ''
            cur.execute('SELECT operating_mode,enabled,emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid',(str(tenant_id),))
            settings=cur.fetchone() or {}
            active_mode=settings.get('operating_mode') == 'active'
            if active_mode:
                if settings.get('enabled') is not True or settings.get('emergency_stop') is True:
                    raise TenantLinearError('AI Agents are stopped for this company.',409)
            elif not binding or not exact_binding(binding,tenant_id=tenant_id,account_domain=integration,
                lead_id=lead_id,contact_id=binding.get('contact_id'),talk_id=expected_route.get('talk_id'),
                chat_id=expected_route.get('chat_id'),channel_id=expected_route.get('channel_id')):
                raise TenantLinearError('Reviewed AI draft is outside the active Test Mode binding.',409)
        conn.commit()
    tenant=str(tenant_id)
    talks=await read_talks(tenant,int(lead_id),request)
    talk=next((row for row in talks if str(row.get('talk_id') or row.get('id') or '')==str(expected_route.get('talk_id'))
        and str(row.get('chat_id') or '')==str(expected_route.get('chat_id'))),None)
    lead=await request(tenant,'GET',f'leads/{int(lead_id)}',params={'with':'contacts'})
    if not talk or int(lead.get('id') or 0)!=int(lead_id):
        raise TenantLinearError('Live Kommo route changed; AI draft send blocked.',409)
    job={'tenant_id':tenant,'agent_id':expected_route.get('agent_id'),'lead_id':int(lead_id),'talk_id':expected_route.get('talk_id'),
      'chat_id':expected_route.get('chat_id'),'channel_id':expected_route.get('channel_id'),
      'payload':{'contact_id':binding['contact_id'] if binding else ''}}
    await assert_live_job_allowed(job,talk,lead,request)


def read_test_mode(profile):
    from tenant_ai_agents_store import _authorize
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute('''SELECT b.*,i.account_domain AS connected_account_domain,i.status AS integration_status
              FROM saas_ai_agent_test_mode b LEFT JOIN saas_tenant_integrations i
                ON i.tenant_id=b.tenant_id AND i.provider='kommo'
              WHERE b.tenant_id=%s::uuid''', (tenant,))
            row = cur.fetchone()
            cur.execute('''SELECT enabled,emergency_stop,channel_id,conversation_id,verified,verified_at,updated_at
              FROM saas_ai_agent_connect_test_mode WHERE tenant_id=%s::uuid''', (tenant,))
            connect_row = cur.fetchone()
        conn.commit()
    connect = dict(connect_row) if connect_row else {'enabled': False, 'emergency_stop': False,
        'channel_id': '', 'conversation_id': '', 'verified': False, 'verified_at': None}
    for key in ('verified_at', 'updated_at'):
        if connect.get(key):
            connect[key] = connect[key].isoformat()
    if not row:
        return {'enabled': False, 'emergency_stop': False, 'verified': False,
            'protection_state': 'blocked_unbound', 'account_domain': '', 'connected_account_domain': '',
            'company_phone': COMPANY_PHONE, 'client_phone': CLIENT_PHONE, 'channel_id': '',
            'lead_id': None, 'contact_id': None, 'talk_id': '', 'chat_id': '', 'verified_at': None,
            'connect': connect}
    result = dict(row)
    for key in ('tenant_id', 'updated_by'):
        result.pop(key, None)
    for key in ('lead_id', 'contact_id'):
        if result.get(key) is not None:
            result[key] = int(result[key])
    for key in ('verified_at', 'updated_at'):
        if result.get(key):
            result[key] = result[key].isoformat()
    result['protection_state'] = ('emergency_stopped' if result.get('emergency_stop') else
        'active_exact_binding' if result.get('enabled') and result.get('verified') else
        'blocked_unverified' if any(result.get(k) for k in ('lead_id','talk_id','chat_id')) else 'blocked_unbound')
    result['connect'] = connect
    return result


def save_connect_test_mode(profile, data):
    """Bind draft-only Test Mode to an already received Connect conversation."""
    from tenant_ai_agents_store import _authorize, _audit
    if not isinstance(data, dict) or set(data) - {'provider','channel_id','conversation_id','enabled','emergency_stop'}:
        raise TenantLinearError('Connect Test Mode settings are invalid.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            channel, conversation = str(data.get('channel_id') or '').strip(), str(data.get('conversation_id') or '').strip()
            cur.execute('''SELECT 1 FROM saas_connect_conversations c JOIN saas_connect_channels ch
              ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
              WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s''',
                        (tenant, channel, conversation))
            verified = bool(cur.fetchone())
            enabled, emergency = bool(data.get('enabled')), bool(data.get('emergency_stop'))
            if enabled and not verified:
                raise TenantLinearError('Əvvəlcə bu Connect dialoqundan imzalanmış giriş mesajı alın.', 409)
            cur.execute('''INSERT INTO saas_ai_agent_connect_test_mode
              (tenant_id,enabled,emergency_stop,channel_id,conversation_id,verified,verified_at,updated_by)
              VALUES(%s::uuid,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() ELSE NULL END,%s)
              ON CONFLICT(tenant_id) DO UPDATE SET enabled=EXCLUDED.enabled,emergency_stop=EXCLUDED.emergency_stop,
                channel_id=EXCLUDED.channel_id,conversation_id=EXCLUDED.conversation_id,verified=EXCLUDED.verified,
                verified_at=EXCLUDED.verified_at,updated_by=EXCLUDED.updated_by,updated_at=now()''',
                        (tenant, enabled, emergency, channel, conversation, verified, verified, actor))
            _audit(cur, tenant, actor, 'ai_connect_test_mode_updated', 'ai_connect_test_binding', tenant,
                   {'enabled':enabled,'emergency_stop':emergency,'verified':verified})
        conn.commit()
    return {'provider':'connect','enabled':enabled,'emergency_stop':emergency,'channel_id':channel,
            'conversation_id':conversation,'verified':verified,'draft_only':True}


def save_test_mode(profile, data):
    if isinstance(data, dict) and data.get('provider') == 'connect':
        return save_connect_test_mode(profile, data)
    from tenant_ai_agents_store import _authorize, _audit
    if not isinstance(data, dict) or set(data) - {'company_phone','company_phone_attested','client_phone','channel_id','lead_id','contact_id','talk_id','chat_id','enabled','emergency_stop'}:
        raise TenantLinearError('Test Mode settings are invalid.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            cur.execute("SELECT account_domain,status FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' FOR SHARE", (tenant,))
            integration = cur.fetchone()
            if not integration or integration.get('status') != 'connected' or not integration.get('account_domain'):
                raise TenantLinearError('A connected Kommo account is required before binding Test Mode.', 409)
            cur.execute('SELECT * FROM saas_ai_agent_test_mode WHERE tenant_id=%s::uuid FOR UPDATE', (tenant,))
            previous = cur.fetchone() or {}
            value = {key: previous.get(key) for key in ('company_phone','client_phone','channel_id','lead_id','contact_id','talk_id','chat_id')}
            value.update({key:data[key] for key in value if key in data})
            value['company_phone'] = normalize_phone(value.get('company_phone'))
            value['client_phone'] = normalize_phone(value.get('client_phone'))
            company_phone_attested=data.get('company_phone_attested',previous.get('company_phone_attested') is True)
            if type(company_phone_attested) is not bool:
                raise TenantLinearError('Company WhatsApp channel confirmation is invalid.')
            if value['company_phone'] != COMPANY_PHONE or value['client_phone'] != CLIENT_PHONE:
                raise TenantLinearError('Test Mode must use the two configured test numbers exactly.', 409)
            if 'enabled' in data and type(data['enabled']) is not bool:
                raise TenantLinearError('Test Mode state is invalid.')
            if 'emergency_stop' in data and type(data['emergency_stop']) is not bool:
                raise TenantLinearError('Emergency stop state is invalid.')
            for key in ('channel_id','talk_id','chat_id'):
                value[key] = str(value.get(key) or '').strip()[:160]
            for key in ('lead_id','contact_id'):
                try: value[key] = int(value[key]) if value.get(key) not in (None, '') else None
                except (ValueError,TypeError): raise TenantLinearError(f'{key} must be confirmed in Kommo.') from None
                if value[key] is not None and value[key] <= 0: raise TenantLinearError(f'{key} must be confirmed in Kommo.')
            identifiers_complete = all(value.get(key) not in (None, '') for key in ('channel_id','lead_id','contact_id','talk_id','chat_id'))
            changed = any(value.get(key) != previous.get(key) for key in value)
            verified = bool(previous.get('verified')) and not changed and str(previous.get('account_domain') or '') == str(integration['account_domain'])
            enabled = data.get('enabled', previous.get('enabled') is True)
            if enabled and (not identifiers_complete or not verified or not company_phone_attested):
                raise TenantLinearError('Verify the exact Kommo conversation before enabling Test Mode.', 409)
            emergency = data.get('emergency_stop', previous.get('emergency_stop') is True)
            cur.execute('''INSERT INTO saas_ai_agent_test_mode
              (tenant_id,enabled,emergency_stop,account_domain,company_phone,company_phone_attested,client_phone,channel_id,lead_id,contact_id,talk_id,chat_id,verified,verified_at,updated_by,updated_at)
              VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN COALESCE(%s,now()) ELSE NULL END,%s,now())
              ON CONFLICT(tenant_id) DO UPDATE SET enabled=EXCLUDED.enabled,emergency_stop=EXCLUDED.emergency_stop,
                account_domain=EXCLUDED.account_domain,company_phone=EXCLUDED.company_phone,company_phone_attested=EXCLUDED.company_phone_attested,client_phone=EXCLUDED.client_phone,
                channel_id=EXCLUDED.channel_id,lead_id=EXCLUDED.lead_id,contact_id=EXCLUDED.contact_id,talk_id=EXCLUDED.talk_id,
                chat_id=EXCLUDED.chat_id,verified=EXCLUDED.verified,verified_at=EXCLUDED.verified_at,updated_by=EXCLUDED.updated_by,updated_at=now()''',
              (tenant,enabled,emergency,integration['account_domain'],value['company_phone'],company_phone_attested,value['client_phone'],value['channel_id'],
               value['lead_id'],value['contact_id'],value['talk_id'],value['chat_id'],verified,verified,previous.get('verified_at'),actor))
            if emergency or not enabled:
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='ai_emergency_stop' WHERE tenant_id=%s::uuid AND state IN ('queued','processing') AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND state IN ('queued','processing'))", (tenant,tenant))
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='ai_emergency_stop',updated_at=now() WHERE tenant_id=%s::uuid AND state IN ('queued','processing')", (tenant,))
            _audit(cur,tenant,actor,'ai_test_mode_updated','ai_test_binding',tenant,{'enabled':enabled,'emergency_stop':emergency,'verified':verified})
        conn.commit()
    return read_test_mode(profile)


async def verify_test_mode(profile, request):
    if not CLIENT_PHONE or not COMPANY_PHONE:
        raise TenantLinearError('Test Mode phone allowlist is not configured on this server.',409)
    from tenant_ai_agents_store import _authorize
    from tenant_chat_reader import read_talks
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            cur.execute('''SELECT b.*,i.account_domain,i.status AS integration_status
              FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations i
                ON i.tenant_id=b.tenant_id AND i.provider='kommo'
              WHERE b.tenant_id=%s::uuid FOR UPDATE OF b,i''',(tenant,))
            binding=cur.fetchone()
        if not binding or binding['integration_status']!='connected':
            raise TenantLinearError('Kommo connection is not available.',409)
        binding=dict(binding)
        conn.commit()
    fields=('lead_id','contact_id','talk_id','chat_id','channel_id')
    if any(binding.get(key) in (None,'') for key in fields):
        raise TenantLinearError('Enter all Kommo IDs before verification.',409)
    tenant=str(binding['tenant_id'])
    lead=await request(tenant,'GET',f"leads/{int(binding['lead_id'])}",params={'with':'contacts'})
    if int(lead.get('id') or 0)!=int(binding['lead_id']):
        raise TenantLinearError('Kommo deal ID did not match.',409)
    embedded=lead.get('_embedded') if isinstance(lead.get('_embedded'),dict) else {}
    contacts=embedded.get('contacts') if isinstance(embedded.get('contacts'),list) else []
    contact_ids=[str(row.get('id')) for row in contacts if isinstance(row,dict) and row.get('id')]
    if contact_ids.count(str(binding['contact_id']))!=1:
        raise TenantLinearError('Test contact is not uniquely linked to the selected Kommo deal.',409)
    contact=await request(tenant,'GET',f"contacts/{int(binding['contact_id'])}")
    if int(contact.get('id') or 0)!=int(binding['contact_id']):
        raise TenantLinearError('Kommo contact ID did not match.',409)
    cfv=contact.get('custom_fields_values') or (contact.get('_embedded') or {}).get('custom_fields_values') or []
    phones=[normalize_phone(v.get('value')) for f in cfv if isinstance(f,dict) and str(f.get('field_code') or '').upper()=='PHONE'
        for v in (f.get('values') or []) if isinstance(v,dict)]
    if phones.count(CLIENT_PHONE)!=1:
        raise TenantLinearError('Kommo contact phone does not match the configured test client exactly.',409)
    talks=await read_talks(tenant,int(binding['lead_id']),request)
    matches=[row for row in talks if str(row.get('talk_id') or row.get('id') or '')==str(binding['talk_id'])
        and str(row.get('chat_id') or '')==str(binding['chat_id'])]
    if len(matches)!=1 or str(matches[0].get('origin') or '').casefold()!=str(binding['channel_id']).casefold():
        raise TenantLinearError('The selected Talk, chat and channel must match one live Kommo Talk exactly.',409)
    if str(binding.get('company_phone') or '')!=COMPANY_PHONE:
        raise TenantLinearError('Connected business WhatsApp number does not match the configured test number.',409)
    if binding.get('company_phone_attested') is not True:
        raise TenantLinearError('Confirm that this exact Kommo channel is connected to the configured business WhatsApp number.',409)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant2,actor2=_authorize(cur,profile)
            cur.execute("SELECT account_domain,status FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' FOR SHARE",(tenant2,))
            integration=cur.fetchone() or {}
            if str(integration.get('account_domain') or '').casefold()!=str(binding['account_domain']).casefold() or integration.get('status')!='connected':
                raise TenantLinearError('Kommo account binding changed during verification.',409)
            cur.execute('''UPDATE saas_ai_agent_test_mode SET verified=TRUE,verified_at=now(),updated_by=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND account_domain=%s AND company_phone=%s AND company_phone_attested=TRUE AND client_phone=%s
                AND lead_id=%s AND contact_id=%s AND talk_id=%s AND chat_id=%s AND channel_id=%s''',
              (actor2,tenant2,binding['account_domain'],COMPANY_PHONE,CLIENT_PHONE,binding['lead_id'],binding['contact_id'],binding['talk_id'],binding['chat_id'],binding['channel_id']))
            if cur.rowcount!=1: raise TenantLinearError('Binding changed during verification.',409)
        conn.commit()
    return read_test_mode(profile)


async def discover_test_talks(profile, lead_id, contact_id, request):
    if not CLIENT_PHONE or not COMPANY_PHONE:
        raise TenantLinearError('Test Mode phone allowlist is not configured on this server.',409)
    """List Talk identities only after Kommo confirms the bound test contact."""
    from tenant_ai_agents_store import _authorize
    try:
        lead_id=int(lead_id); contact_id=int(contact_id)
    except (TypeError,ValueError):
        raise TenantLinearError('Enter Kommo deal and contact IDs first.',400) from None
    if lead_id<=0 or contact_id<=0: raise TenantLinearError('Enter Kommo deal and contact IDs first.',400)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            cur.execute("SELECT account_domain,status FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo'",(tenant,))
            integration=cur.fetchone() or {}
        conn.commit()
    if integration.get('status')!='connected' or not integration.get('account_domain'):
        raise TenantLinearError('A connected Kommo account is required.',409)
    tenant=str(tenant)
    lead=await request(tenant,'GET',f'leads/{lead_id}',params={'with':'contacts'})
    if int(lead.get('id') or 0)!=lead_id:
        raise TenantLinearError('Deal ID did not match Kommo.',409)
    embedded=lead.get('_embedded') if isinstance(lead.get('_embedded'),dict) else {}
    contacts=embedded.get('contacts') if isinstance(embedded.get('contacts'),list) else []
    if sum(1 for row in contacts if isinstance(row,dict) and str(row.get('id') or '')==str(contact_id))!=1:
        raise TenantLinearError('Contact is not uniquely linked to this deal.',409)
    contact=await request(tenant,'GET',f'contacts/{contact_id}')
    if int(contact.get('id') or 0)!=contact_id:
        raise TenantLinearError('Contact ID did not match Kommo.',409)
    cfv=contact.get('custom_fields_values') or (contact.get('_embedded') or {}).get('custom_fields_values') or []
    phones=[normalize_phone(v.get('value')) for f in cfv if isinstance(f,dict) and str(f.get('field_code') or '').upper()=='PHONE'
        for v in (f.get('values') or []) if isinstance(v,dict)]
    if phones.count(CLIENT_PHONE)!=1:
        raise TenantLinearError('Selected contact is not the configured test client.',409)
    talks=await read_talks(tenant,lead_id,request)
    return {'account_domain':str(integration['account_domain']),'lead_id':lead_id,'contact_id':contact_id,
      'talks':[{'talk_id':str(row.get('talk_id') or row.get('id')),'chat_id':str(row.get('chat_id') or ''),
        'channel_id':str(row.get('origin') or ''),'is_in_work':row.get('is_in_work') is True,
        'is_closed':row.get('is_closed') is True} for row in talks]}
