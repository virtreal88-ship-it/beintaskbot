"""Separate company chat key; never read the global or news AI credential."""
import json
from tenant_platform import _connect, _fernet
from tenant_news_ai_settings import authorize
from tenant_chat_ai_schema import ensure_schema
from tenant_chat_ai_policy import configuration
from tenant_linear_policy import TenantLinearError


def public(row):
    row = row or {}
    return {'configured': bool(row.get('secrets')), 'updated_at': str(row.get('updated_at') or ''),
            **{k: (row.get('metadata') or {}).get(k, v) for k, v in {
                'enabled': False, 'consent': False, 'audio_enabled': False, 'model': '',
                'transcription_model': '', 'daily_limit': 50, 'media_hosts': []}.items()}}


def read(session):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = authorize(cur, session)
            cur.execute("SELECT secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'", (tenant,))
            return public(cur.fetchone())


def save(session, data):
    config = configuration(data)
    key = data.get('api_key', '')
    if not isinstance(key, str) or (key and (not 20 <= len(key) <= 500 or any(c.isspace() for c in key))):
        raise TenantLinearError('API açarı düzgün deyil.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = authorize(cur, session)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-chat-ai:' + tenant,))
            cur.execute("SELECT secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' FOR UPDATE", (tenant,))
            previous = cur.fetchone() or {}
            if str(previous.get('updated_at') or '') != str(data.get('expected_updated_at') or ''):
                raise TenantLinearError('AI ayarları dəyişib. Yeniləyin.', 409)
            encrypted = previous.get('secrets')
            if data.get('remove_key') is True:
                encrypted = None
            elif key:
                encrypted = _fernet().encrypt(json.dumps({'tenant_id': tenant, 'provider': 'chat_ai', 'api_key': key}).encode())
            if config['enabled'] and not encrypted:
                raise TenantLinearError('Şirkətin API açarını daxil edin.')
            cur.execute('''INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata)
              VALUES(%s::uuid,'chat_ai','configured',%s,%s::jsonb) ON CONFLICT(tenant_id,provider) DO UPDATE
              SET secrets=EXCLUDED.secrets,metadata=EXCLUDED.metadata,status='configured',updated_at=now()
              RETURNING secrets,metadata,updated_at''', (tenant, encrypted, json.dumps(config)))
            result = public(cur.fetchone())
        conn.commit()
    return result
