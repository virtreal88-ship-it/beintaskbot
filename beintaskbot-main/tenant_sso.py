"""Minimal OAuth 2.1 authorization-code service for BeinSystems products.

It deliberately reuses the existing authenticated tenant session.  Passwords,
email confirmation and session signing remain exclusively in tenant_email_auth
and TenantBrowserSession.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json

CONNECT_CLIENT_ID = "beintaskbot-connect"
CONNECT_PRODUCT = "connect"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _client_secret() -> str:
    value = str(os.environ.get("CONNECT_SSO_CLIENT_SECRET") or "")
    if len(value) < 32:
        raise TenantPlatformError("Connect SSO hələ konfiqurasiya edilməyib.")
    return value


def _redirect_uri() -> str:
    """Return the one callback registered for the Connect client.

    A prefix check here would let a hostile site receive a valid authorization
    code.  Connect is a first-party client, so it has exactly one callback.
    """
    value = str(os.environ.get("CONNECT_SSO_REDIRECT_URI") or "").rstrip("/")
    if not value.startswith("https://"):
        raise TenantPlatformError("Connect SSO callback konfiqurasiya edilməyib.")
    return value


def ensure_schema(cur) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_product_entitlements (
        tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
        product TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'inactive'
          CHECK (status IN ('inactive','trial','active','suspended','admin')),
        trial_ends_at TIMESTAMPTZ NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, product))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_product_members (
        tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
        actor_id BIGINT NOT NULL, product TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('owner','admin','viewer')),
        active BOOLEAN NOT NULL DEFAULT TRUE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, actor_id, product))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_oauth_codes (
        id UUID PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, client_id TEXT NOT NULL,
        redirect_uri TEXT NOT NULL, pkce_challenge TEXT NOT NULL, tenant_id UUID NOT NULL,
        actor_id BIGINT NOT NULL, role TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
        used_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_oauth_codes_expiry_idx ON saas_oauth_codes(expires_at) WHERE used_at IS NULL")


def _access(cur, tenant_id: str, actor_id: int) -> str | None:
    cur.execute("""SELECT m.role FROM saas_product_members m
      JOIN saas_product_entitlements e ON e.tenant_id=m.tenant_id AND e.product=m.product
      JOIN saas_tenant_members tm ON tm.tenant_id=m.tenant_id AND tm.telegram_id=m.actor_id AND tm.active=TRUE
      WHERE m.tenant_id=%s::uuid AND m.actor_id=%s AND m.product=%s AND m.active=TRUE
        AND (e.status IN ('trial','active','admin') OR (e.status='suspended' AND FALSE))""",
      (tenant_id, actor_id, CONNECT_PRODUCT))
    row = cur.fetchone()
    return str(row['role']) if row else None


def _bootstrap_owner_access(cur, tenant_id: str, actor_id: int) -> str | None:
    """Enable the product for the first owner in an MVP installation.

    This creates an explicit administrative entitlement, not a paid
    subscription.  It never grants access to a non-owner and never changes an
    existing entitlement, so billing can later replace this state safely.
    """
    cur.execute("""SELECT role FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE FOR UPDATE""",
      (tenant_id, actor_id))
    member = cur.fetchone()
    if not member or str(member['role']) != 'owner':
        return None
    cur.execute("""SELECT 1 FROM saas_product_entitlements
      WHERE tenant_id=%s::uuid AND product=%s""", (tenant_id, CONNECT_PRODUCT))
    if cur.fetchone():
        return None
    cur.execute("""INSERT INTO saas_product_entitlements(tenant_id,product,status)
      VALUES(%s::uuid,%s,'admin')""", (tenant_id, CONNECT_PRODUCT))
    cur.execute("""INSERT INTO saas_product_members(tenant_id,actor_id,product,role)
      VALUES(%s::uuid,%s,%s,'owner')""", (tenant_id, actor_id, CONNECT_PRODUCT))
    return 'owner'


def issue_code(*, tenant_id: str, actor_id: int, client_id: str, redirect_uri: str, code_challenge: str) -> str:
    if client_id != CONNECT_CLIENT_ID or redirect_uri.rstrip("/") != _redirect_uri() or len(code_challenge) < 43:
        raise TenantPlatformError("SSO sorğusu etibarlı deyil.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            ensure_schema(cur)
            role = _access(cur, tenant_id, actor_id)
            if not role:
                role = _bootstrap_owner_access(cur, tenant_id, actor_id)
            if not role:
                raise TenantPlatformError("Bu şirkət üçün Connect girişi aktiv deyil.")
            code = secrets.token_urlsafe(32)
            cur.execute("""INSERT INTO saas_oauth_codes(id,code_hash,client_id,redirect_uri,pkce_challenge,tenant_id,actor_id,role,expires_at)
              VALUES(%s,%s,%s,%s,%s,%s::uuid,%s,%s,now()+interval '90 seconds')""",
              (uuid.uuid4(), _hash(code), client_id, redirect_uri, code_challenge, tenant_id, actor_id, role))
        conn.commit()
    return code


def exchange_code(*, code: str, verifier: str, client_id: str, redirect_uri: str, client_secret: str) -> dict:
    if client_id != CONNECT_CLIENT_ID or not hmac.compare_digest(client_secret, _client_secret()):
        raise TenantPlatformError("SSO client təsdiqlənmədi.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            ensure_schema(cur)
            cur.execute("""SELECT * FROM saas_oauth_codes WHERE code_hash=%s AND client_id=%s
              AND redirect_uri=%s AND used_at IS NULL AND expires_at>now() FOR UPDATE""", (_hash(code), client_id, redirect_uri))
            row = cur.fetchone()
            if not row or not hmac.compare_digest(str(row['pkce_challenge']), _pkce(verifier)):
                raise TenantPlatformError("SSO kodu etibarsızdır və ya vaxtı bitib.")
            if not _access(cur, str(row['tenant_id']), int(row['actor_id'])):
                raise TenantPlatformError("Connect girişi artıq aktiv deyil.")
            cur.execute('''SELECT email FROM saas_auth_users
              WHERE actor_id=%s AND verified_at IS NOT NULL AND disabled_at IS NULL
              ORDER BY created_at LIMIT 1''', (int(row['actor_id']),))
            auth_user = cur.fetchone()
            cur.execute("UPDATE saas_oauth_codes SET used_at=now() WHERE id=%s", (row['id'],))
        conn.commit()
    return {'actor_id': int(row['actor_id']), 'tenant_id': str(row['tenant_id']), 'role': str(row['role']),
            'email': str(auth_user['email']).strip().lower() if auth_user and auth_user.get('email') else None,
            'expires_in': 300, 'product': CONNECT_PRODUCT}
