"""Owner-owned encrypted OpenAI credential and explicit news processing consent."""
import json
import re
from tenant_platform import _connect, _ensure_schema, _fernet
from tenant_linear_policy import identifier, TenantLinearError


def authorize(cur, session):
    if not session:
        raise TenantLinearError('İcazə yoxdur.', 403)
    tenant, user = identifier(session['tenant_id']), int(session['telegram_id'])
    cur.execute('''SELECT m.role,m.active,t.status FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, user))
    person = cur.fetchone()
    if not person or person['role'] != 'owner' or person['active'] is not True or person['status'] != 'active':
        raise TenantLinearError('Yalnız şirkət sahibi üçün.', 403)
    return tenant, user


def public(row):
    row = row or {}; config = row.get('metadata') or {}
    return {'configured': bool(row.get('secrets')), 'enabled': config.get('enabled') is True,
        'model': config.get('model', ''), 'daily_limit': config.get('daily_limit', 10), 'updated_at': str(row.get('updated_at') or '')}


def read(session):
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = authorize(cur, session)
            cur.execute("SELECT secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='news_ai'", (tenant,))
            return public(cur.fetchone())


def save(session, data):
    enabled, consent = data.get('enabled'), data.get('consent')
    model, limit, key = data.get('model', ''), data.get('daily_limit'), data.get('api_key', '')
    if not isinstance(enabled, bool) or type(limit) is not int or not 1 <= limit <= 100:
        raise TenantLinearError('AI ayarları düzgün deyil.')
    if enabled and consent is not True:
        raise TenantLinearError('Mətnlərin OpenAI-yə göndərilməsinə icazə verin.')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', model):
        raise TenantLinearError('Modelin adını yazın.')
    if not isinstance(key, str) or (key and (not 20 <= len(key) <= 500 or any(c.isspace() for c in key))):
        raise TenantLinearError('API açarı düzgün deyil.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = authorize(cur, session)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-news-ai:' + tenant,))
            cur.execute("SELECT secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='news_ai' FOR UPDATE", (tenant,))
            previous = cur.fetchone() or {}
            if str(previous.get('updated_at') or '') != str(data.get('expected_updated_at') or ''):
                raise TenantLinearError('AI ayarları dəyişib. Yeniləyin.', 409)
            encrypted = previous.get('secrets')
            if data.get('remove_key') is True:
                encrypted = None
            elif key:
                encrypted = _fernet().encrypt(json.dumps({'tenant_id': tenant, 'provider': 'news_ai', 'api_key': key}).encode())
            if enabled and not encrypted:
                raise TenantLinearError('Əvvəlcə şirkətinizin OpenAI API açarını daxil edin.')
            metadata = {'enabled': enabled, 'consent': consent is True, 'model': model, 'daily_limit': limit}
            cur.execute('''INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata)
              VALUES(%s::uuid,'news_ai','configured',%s,%s::jsonb) ON CONFLICT(tenant_id,provider) DO UPDATE
              SET status='configured',secrets=EXCLUDED.secrets,metadata=EXCLUDED.metadata,updated_at=now()
              RETURNING secrets,metadata,updated_at''', (tenant, encrypted, json.dumps(metadata)))
            result = public(cur.fetchone())
        conn.commit()
    return result
