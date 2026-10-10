"""Opt-in, expiring login for an isolated marketplace review company.

Disabled unless all deployment settings are present. Never authenticates a
real Telegram identity or accepts a tenant/company supplied by the browser.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DemoConfig:
    tenant_id: str
    username: str
    password_hash: str
    expires_at: int

    @property
    def user_id(self) -> int:
        # Reserved synthetic ID, outside the positive Telegram user namespace.
        return -(1_000_000_000 + int(uuid.UUID(self.tenant_id)) % 8_000_000_000)


def config() -> DemoConfig | None:
    try:
        if os.environ.get("MODERATION_DEMO_ENABLED") != "true":
            return None
        tenant_id = str(uuid.UUID(os.environ["MODERATION_DEMO_TENANT_ID"]))
        username = os.environ["MODERATION_DEMO_USERNAME"].strip()
        password_hash = os.environ["MODERATION_DEMO_PASSWORD_HASH"]
        expires = datetime.fromisoformat(os.environ["MODERATION_DEMO_EXPIRES_AT"].replace("Z", "+00:00"))
        if expires.tzinfo is None or not username or not password_hash:
            return None
        expires_at = int(expires.timestamp())
        if expires_at <= int(time.time()):
            return None
        return DemoConfig(tenant_id, username, password_hash, expires_at)
    except (KeyError, TypeError, ValueError):
        return None


def password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        iterations = int(rounds)
        if algorithm != "pbkdf2_sha256" or not 200_000 <= iterations <= 1_000_000:
            return False
        salt_bytes, expected_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
        if len(salt_bytes) < 16 or len(expected_bytes) != 32 or not 12 <= len(password) <= 256:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_bytes, iterations)
        return hmac.compare_digest(actual, expected_bytes)
    except (TypeError, ValueError):
        return False


def identity_allowed(tenant_id: str, user_id: int) -> bool:
    """Revocation and expiry also invalidate previously issued demo sessions."""
    if user_id > 0:
        return True
    current = config()
    return bool(current and current.tenant_id == tenant_id and current.user_id == user_id)


def provision(current: DemoConfig) -> None:
    """Create only the configured NEW company; refuse existing non-demo data."""
    from tenant_platform import _connect, _ensure_schema, _ROLE_PERMISSIONS

    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("moderation-demo:" + current.tenant_id,))
            cur.execute("SELECT owner_telegram_id, onboarding FROM saas_tenants WHERE id=%s::uuid FOR UPDATE", (current.tenant_id,))
            existing = cur.fetchone()
            if existing:
                marker = existing.get("onboarding") or {}
                if existing["owner_telegram_id"] != current.user_id or marker.get("moderation_demo") is not True:
                    raise ValueError("Configured company is not an isolated review company")
                cur.execute("SELECT role, active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s", (current.tenant_id, current.user_id))
                membership = cur.fetchone()
                if not membership or membership["role"] != "owner" or not membership["active"]:
                    raise ValueError("Review access was revoked")
                return
            cur.execute("""INSERT INTO saas_tenants
                (id,slug,name,industry,owner_telegram_id,modules,notification_rules,onboarding)
                VALUES (%s::uuid,%s,'Marketplace review — demo','Demonstration',%s,%s::jsonb,%s::jsonb,%s::jsonb)""",
                (current.tenant_id, "moderation-" + current.tenant_id, current.user_id,
                 json.dumps({"deals": True, "tasks": True, "customers": True, "hot_orders": False, "finance": False}),
                 json.dumps({"new_lead": False, "incoming_message": False, "task_assigned": False, "task_overdue": False}),
                 json.dumps({"moderation_demo": True, "company_name": "Marketplace review — demo", "completed_steps": ["company", "owner"]})))
            cur.execute("""INSERT INTO saas_tenant_members (tenant_id,telegram_id,display_name,role,permissions)
                VALUES (%s::uuid,%s,'Marketplace reviewer','owner',%s::jsonb)""",
                (current.tenant_id, current.user_id, json.dumps(_ROLE_PERMISSIONS["owner"])))
        conn.commit()
