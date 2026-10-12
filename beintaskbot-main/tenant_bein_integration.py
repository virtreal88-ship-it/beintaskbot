"""Encrypted tenant configuration for the BeinSystems ERP CRM provider."""
from __future__ import annotations

import json
import uuid
from cryptography.fernet import InvalidToken

from tenant_bein_crm import BeinCrm
from tenant_crm_provider import CrmProviderError
from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _fernet, _json

PROVIDER = "beinsystems"
CRM_PROVIDERS = ("kommo", PROVIDER)


def _owner(profile: dict | None) -> tuple[str, int]:
    if not profile or profile.get("role") != "owner" or not profile.get("active", True):
        raise TenantPlatformError("İcazə yoxdur.")
    return str(profile.get("tenant_id") or ""), int(profile.get("telegram_id") or 0)


def _public(row: dict | None) -> dict:
    row = dict(row or {})
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "provider": PROVIDER,
        "status": str(row.get("status") or "not_connected"),
        "base_url": str(row.get("account_domain") or ""),
        "branch_id": str(metadata.get("branch_id") or ""),
        "primary": bool(metadata.get("primary")),
        "capabilities": {
            "deals": True, "tasks": True, "customers": True,
            "pipelines": True, "stages": True,
            "notes": False, "chats": False, "webhooks": False,
        },
        "connected_at": row.get("connected_at").isoformat() if row.get("connected_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }


def status(profile: dict | None) -> dict:
    tenant, _ = _owner(profile)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT status,account_domain,metadata,connected_at,updated_at
                FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider=%s""",
                (tenant, PROVIDER))
            row = cur.fetchone()
    return _public(row)


def credential(tenant_id: str) -> BeinCrm:
    tenant = str(tenant_id or "")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT status,account_domain,metadata,secrets
                FROM saas_tenant_integrations
                WHERE tenant_id=%s::uuid AND provider=%s FOR SHARE""", (tenant, PROVIDER))
            row = cur.fetchone()
    if not row or row.get("status") != "connected" or not row.get("secrets"):
        raise TenantPlatformError("BeinSystems ERP hələ qoşulmayıb.")
    try:
        secret = json.loads(_fernet().decrypt(bytes(row["secrets"])).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TenantPlatformError("BeinSystems ERP bağlantısı oxunmadı. Yenidən qoşun.") from exc
    if secret.get("tenant_id") != tenant or secret.get("provider") != PROVIDER:
        raise TenantPlatformError("BeinSystems ERP bağlantısı bu şirkətə aid deyil.")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    try:
        return BeinCrm(str(row.get("account_domain") or ""), str(secret.get("token") or ""),
                       str(metadata.get("branch_id") or ""))
    except CrmProviderError as exc:
        raise TenantPlatformError(str(exc)) from exc


def save(profile: dict | None, *, base_url: str, token: str, branch_id: str,
         primary: bool = False) -> dict:
    tenant, actor = _owner(profile)
    # Validate before touching the currently working connection. Encryption is
    # also performed before the transaction so a missing key cannot corrupt it.
    client = BeinCrm(base_url, token, branch_id)
    encrypted = _fernet().encrypt(_json({
        "tenant_id": tenant, "provider": PROVIDER, "token": client.integration_token,
    }).encode("utf-8"))
    metadata = {"branch_id": client.branch_id, "primary": bool(primary)}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT role,active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE",
                        (tenant, actor))
            current = cur.fetchone()
            if not current or current.get("role") != "owner" or not current.get("active"):
                raise TenantPlatformError("İcazə yoxdur.")
            cur.execute("""INSERT INTO saas_tenant_integrations
                (tenant_id,provider,status,account_domain,metadata,secrets,connected_at,updated_at)
                VALUES (%s::uuid,%s,'connected',%s,%s::jsonb,%s,now(),now())
                ON CONFLICT (tenant_id,provider) DO UPDATE SET
                  status='connected',account_domain=EXCLUDED.account_domain,
                  metadata=EXCLUDED.metadata,secrets=EXCLUDED.secrets,
                  connected_at=COALESCE(saas_tenant_integrations.connected_at,now()),updated_at=now()
                RETURNING status,account_domain,metadata,connected_at,updated_at""",
                (tenant, PROVIDER, client.base_url, _json(metadata), encrypted))
            row = cur.fetchone()
            if primary:
                cur.execute("""UPDATE saas_tenant_integrations SET
                    metadata=jsonb_set(COALESCE(metadata,'{}'::jsonb),'{primary}',
                      to_jsonb((provider=%s)::boolean),true),updated_at=now()
                    WHERE tenant_id=%s::uuid AND provider=ANY(%s)""", (PROVIDER, tenant, list(CRM_PROVIDERS)))
            cur.execute("""INSERT INTO saas_tenant_audit_events
                (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                VALUES (%s,%s::uuid,%s,'integration_connected','integration',%s,%s::jsonb)""",
                (uuid.uuid4(), tenant, actor, PROVIDER,
                 _json({"base_url": client.base_url, "branch_id": client.branch_id, "primary": bool(primary)})))
        conn.commit()
    return _public(row)


def set_primary(profile: dict | None, provider: str) -> dict:
    tenant, actor = _owner(profile)
    selected = str(provider or "").casefold()
    if selected not in CRM_PROVIDERS:
        raise TenantPlatformError("CRM provayderi düzgün deyil.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT role,active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE",
                        (tenant, actor))
            current = cur.fetchone()
            if not current or current.get("role") != "owner" or not current.get("active"):
                raise TenantPlatformError("İcazə yoxdur.")
            cur.execute("SELECT status FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider=%s",
                        (tenant, selected))
            integration = cur.fetchone()
            if not integration or integration.get("status") != "connected":
                raise TenantPlatformError("Əsas CRM seçilməzdən əvvəl qoşulmalıdır.")
            cur.execute("""UPDATE saas_tenant_integrations SET
                metadata=jsonb_set(COALESCE(metadata,'{}'::jsonb),'{primary}',
                  to_jsonb((provider=%s)::boolean),true),updated_at=now()
                WHERE tenant_id=%s::uuid AND provider=ANY(%s)""", (selected, tenant, list(CRM_PROVIDERS)))
            cur.execute("""INSERT INTO saas_tenant_audit_events
                (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                VALUES (%s,%s::uuid,%s,'primary_crm_changed','integration',%s,'{}'::jsonb)""",
                (uuid.uuid4(), tenant, actor, selected))
        conn.commit()
    return {"provider": selected, "primary": True}


def primary_provider(tenant_id: str) -> str:
    """Compatibility default stays Kommo until an owner explicitly switches."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT provider FROM saas_tenant_integrations
                WHERE tenant_id=%s::uuid AND provider=ANY(%s) AND status='connected'
                  AND COALESCE((metadata->>'primary')::boolean,false)
                ORDER BY updated_at DESC LIMIT 1""", (tenant_id, list(CRM_PROVIDERS)))
            row = cur.fetchone()
    return str((row or {}).get("provider") or "kommo")
