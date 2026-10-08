"""Tenant Linear connection/config storage. Legacy integration stays independent."""
import json
from tenant_platform import _connect, _ensure_schema, _fernet
from tenant_linear_policy import owner, settings, identifier, TenantLinearError
from tenant_linear_provider import verify, catalog, team_catalog


def authorize(cur, tenant, user):
    cur.execute('SELECT role,active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE', (tenant, user))
    row = cur.fetchone()
    if not row or row['role'] != 'owner' or not row['active']:
        raise TenantLinearError('İcazə yoxdur.', 403)


def public(row):
    row = row or {}
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    return {'status': row.get('status', 'not_connected'), 'settings': metadata.get('settings', {}),
            'updated_at': str(row.get('updated_at') or ''), 'runtime_enabled': row.get('status') == 'connected' and bool(metadata.get('settings', {}).get('workflow', {}).get('enabled'))}


def read(profile):
    tenant, user = owner(profile)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            authorize(cur, tenant, user)
            cur.execute("SELECT status,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='linear'", (tenant,))
            result = public(cur.fetchone())
            cur.execute('SELECT telegram_id,display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid ORDER BY display_name,telegram_id LIMIT 501', (tenant,))
            result['members'] = cur.fetchall()
            if len(result['members']) > 500:
                raise TenantLinearError('500-dən çox əməkdaş üçün səhifələmə tələb olunur.', 409)
    return result


def credential(profile):
    tenant, user = owner(profile)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            authorize(cur, tenant, user)
            cur.execute("SELECT status,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='linear'", (tenant,))
            row = cur.fetchone()
    if not row or row['status'] != 'connected' or not row.get('secrets'):
        raise TenantLinearError('Linear hələ qoşulmayıb.', 409)
    try:
        payload = json.loads(_fernet().decrypt(bytes(row['secrets'])))
        if payload['tenant_id'] != tenant or payload['provider'] != 'linear':
            raise ValueError('Wrong credential owner')
        return payload['api_key']
    except Exception:
        raise TenantLinearError('Linear təhlükəsizlik ayarlarını yoxlayın.', 503) from None


def choices(profile, team_id='', after=None):
    key = credential(profile)
    if team_id:
        result = team_catalog(key, identifier(team_id))
        if not result:
            raise TenantLinearError('Komanda tapılmadı.', 404)
        from tenant_linear_tasks_provider import team_members
        result['members'] = team_members(key, identifier(team_id))
        return {'team': result}
    if after is not None and (not isinstance(after, str) or len(after) > 512):
        raise TenantLinearError('Səhifə kursoru düzgün deyil.')
    return {'teams': catalog(key, after)}


def command(profile, data):
    tenant, user = owner(profile)
    action = data.get('action')
    encrypted = None
    config = None
    if action == 'connect':
        cipher = _fernet()  # Missing key fails before contacting provider or storing anything.
        key = data.get('api_key')
        verify(key)
        encrypted = cipher.encrypt(json.dumps({'tenant_id': tenant, 'provider': 'linear', 'api_key': key.strip()}).encode())
    elif action == 'settings':
        config = settings(data.get('settings'))
        if config['team_id']:
            team = choices(profile, config['team_id'])['team']
            states = {s['id'] for s in team['states']['nodes'] if s['type'] == 'completed'}
            projects = {p['id'] for p in team['projects']['nodes']}
            if not set(config['done_state_ids']) <= states or not set(config['news']['projects']) <= projects:
                raise TenantLinearError('Seçilmiş status/layihə bu komandaya aid deyil.')
            state_ids = {s['id'] for s in team['states']['nodes']}
            required = {config['workflow']['creation_state_id']} - {''}
            for button in config['workflow']['buttons']:
                required.update(button['from_state_ids']); required.add(button['to_state_id'])
            if not required <= state_ids:
                raise TenantLinearError('İş qaydasının statusları bu komandaya aid deyil.')
            if any(m['assignee_id'] for m in config['members'].values()):
                from tenant_linear_tasks_provider import team_members
                people = team.get('members') or team_members(credential(profile), config['team_id'])
                if people['pageInfo']['hasNextPage']:
                    raise TenantLinearError('İcraçı kataloqu tam deyil. Səhifələmə tələb olunur.')
                if not {m['assignee_id'] for m in config['members'].values() if m['assignee_id']} <= {m['id'] for m in people['nodes']}:
                    raise TenantLinearError('İcraçı bu komandaya aid deyil.')
        elif config['done_state_ids'] or config['news']['projects']:
            raise TenantLinearError('Əvvəlcə komandanı seçin.')
        if config['workflow']['enabled'] and not config['team_id']:
            raise TenantLinearError('İş qaydaları üçün komandanı seçin.')
    elif action != 'disconnect':
        raise TenantLinearError('Əməliyyat düzgün deyil.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            authorize(cur, tenant, user)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-linear:' + tenant,))
            cur.execute("SELECT updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='linear' FOR UPDATE", (tenant,))
            previous = cur.fetchone()
            if config is not None and not previous:
                raise TenantLinearError('Əvvəlcə Linear bağlantısını yaradın.', 409)
            if str(data.get('expected_updated_at') or '') != str((previous or {}).get('updated_at') or ''):
                raise TenantLinearError('Ayarlar dəyişib. Yeniləyin.', 409)
            if config is not None:
                cur.execute('SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s::uuid', (tenant,))
                members = {str(m['telegram_id']) for m in cur.fetchall()}
                if not set(config['members']) <= members:
                    raise TenantLinearError('Əməkdaş bu şirkətə aid deyil.')
                cur.execute("UPDATE saas_tenant_integrations SET metadata=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND provider='linear' RETURNING status,metadata,updated_at", (json.dumps({'settings': config}), tenant))
            elif action == 'connect':
                cur.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,connected_at)
                  VALUES(%s::uuid,'linear','connected',%s,now()) ON CONFLICT(tenant_id,provider) DO UPDATE
                  SET status='connected',secrets=EXCLUDED.secrets,metadata='{}'::jsonb,connected_at=now(),updated_at=now()
                  RETURNING status,metadata,updated_at""", (tenant, encrypted))
            else:
                cur.execute("UPDATE saas_tenant_integrations SET status='not_connected',secrets=NULL,connected_at=NULL,updated_at=now() WHERE tenant_id=%s::uuid AND provider='linear' RETURNING status,metadata,updated_at", (tenant,))
            result = public(cur.fetchone())
        conn.commit()
    return result
