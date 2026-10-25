"""Verified Telegram-chat bindings for browser login accounts.

An employee actor id is deliberately not a Telegram chat id.  Binding is
completed only after the signed-in employee opens a one-time /start link in
the Beintaskbot bot; delivery code reads this table, never the member key.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema


def _schema(cur) -> None:
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_tenant_telegram_bindings(
      tenant_id UUID NOT NULL,actor_id BIGINT NOT NULL,chat_id BIGINT NOT NULL,
      verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),revoked_at TIMESTAMPTZ NULL,
      PRIMARY KEY(tenant_id,actor_id),UNIQUE(tenant_id,chat_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_tenant_telegram_binding_tokens(
      id UUID PRIMARY KEY,tenant_id UUID NOT NULL,actor_id BIGINT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE,expires_at TIMESTAMPTZ NOT NULL,
      used_at TIMESTAMPTZ NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')


def request_binding(*, tenant_id: str, actor_id: int, bot_username: str) -> dict:
    raw = secrets.token_urlsafe(24)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _schema(cur)
            cur.execute('''SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE FOR SHARE''',
                        (tenant_id, int(actor_id)))
            if not cur.fetchone():
                raise TenantPlatformError('Şirkət üzvlüyü aktiv deyil.')
            cur.execute('''UPDATE saas_tenant_telegram_binding_tokens SET used_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND used_at IS NULL''', (tenant_id, int(actor_id)))
            cur.execute('''INSERT INTO saas_tenant_telegram_binding_tokens(id,tenant_id,actor_id,token_hash,expires_at)
              VALUES(%s,%s::uuid,%s,%s,now()+interval '15 minutes')''',
                        (uuid.uuid4(), tenant_id, int(actor_id), hashlib.sha256(raw.encode()).hexdigest()))
        conn.commit()
    username = str(bot_username or '').strip().lstrip('@')
    return {'bound': False, 'expires_in_seconds': 900,
            'deep_link': f'https://t.me/{username}?start=bind_{raw}' if username else '',
            'start_payload': 'bind_' + raw}


def complete_binding(*, chat_id: int, user_id: int, token: str) -> dict:
    """Consume a binding token only from the owner of a private Telegram chat."""
    try:
        chat_id, user_id = int(chat_id), int(user_id)
    except (TypeError, ValueError):
        raise TenantPlatformError('Telegram şəxsi söhbəti təsdiqlənmədi.') from None
    if chat_id <= 0 or user_id <= 0 or chat_id != user_id:
        raise TenantPlatformError('Telegram şəxsi söhbəti təsdiqlənmədi.')
    digest = hashlib.sha256(str(token or '').encode()).hexdigest()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _schema(cur)
            cur.execute('''SELECT * FROM saas_tenant_telegram_binding_tokens WHERE token_hash=%s
              AND used_at IS NULL AND expires_at>now() FOR UPDATE''', (digest,))
            row = cur.fetchone()
            if not row:
                raise TenantPlatformError('Telegram bağlantı linki etibarsızdır və ya müddəti bitib.')
            cur.execute('''SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=%s AND active=TRUE FOR SHARE''',
                        (row['tenant_id'], row['actor_id']))
            if not cur.fetchone():
                raise TenantPlatformError('Şirkət üzvlüyü aktiv deyil.')
            cur.execute('''INSERT INTO saas_tenant_telegram_bindings(tenant_id,actor_id,chat_id,verified_at,revoked_at)
              VALUES(%s,%s,%s,now(),NULL) ON CONFLICT(tenant_id,actor_id) DO UPDATE SET chat_id=EXCLUDED.chat_id,
              verified_at=now(),revoked_at=NULL''', (row['tenant_id'], row['actor_id'], chat_id))
            cur.execute('UPDATE saas_tenant_telegram_binding_tokens SET used_at=now() WHERE id=%s', (row['id'],))
        conn.commit()
    return {'bound': True, 'tenant_id': str(row['tenant_id']), 'actor_id': int(row['actor_id'])}


def binding_status(*, tenant_id: str, actor_id: int) -> dict:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _schema(cur)
            cur.execute('''SELECT verified_at FROM saas_tenant_telegram_bindings WHERE tenant_id=%s::uuid AND actor_id=%s AND revoked_at IS NULL''',
                        (tenant_id, int(actor_id)))
            row = cur.fetchone()
    return {'bound': bool(row), 'verified_at': row['verified_at'].isoformat() if row else None}


def bound_chat_id(*, tenant_id: str, actor_id: int) -> int | None:
    snapshot = binding_snapshot(tenant_id=tenant_id, actor_id=actor_id)
    return snapshot['chat_id'] if snapshot else None


def binding_snapshot(*, tenant_id: str, actor_id: int) -> dict | None:
    """Current verified recipient identity, never an employee actor id."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            _schema(cur)
            cur.execute('''SELECT chat_id,verified_at FROM saas_tenant_telegram_bindings
              WHERE tenant_id=%s::uuid AND actor_id=%s AND revoked_at IS NULL''', (tenant_id, int(actor_id)))
            row = cur.fetchone()
    if not row:
        return None
    return {'chat_id': int(row['chat_id']), 'verified_at': row['verified_at'].isoformat()}
