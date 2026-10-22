"""One-use employee invitation and approval persistence."""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import uuid

from tenant_member_store import member
from tenant_platform_common import _invite_token, _permissions, _role
from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json
from tenant_policy import TenantPolicy

def _may_manage(current: dict | None) -> bool:
    return bool(current and TenantPolicy(current).allows_action('employees','manage'))

def create_invite(*, tenant_id: str, owner_id: int, display_name: str, role: str,
                  permissions: list[str], invited_email: str = '') -> dict:
    current = member(tenant_id, owner_id)
    if not _may_manage(current):
        raise TenantPlatformError("İcazə yoxdur.")
    name = str(display_name or "").strip()[:120]
    if not name:
        raise TenantPlatformError("Əməkdaşın adını yazın.")
    selected_role = _role(str(role or ""))
    email = str(invited_email or '').strip().casefold()
    if email and (len(email)>254 or '@' not in email or any(ch.isspace() for ch in email)):
        raise TenantPlatformError('Email ünvanını düzgün yazın.')
    selected_permissions = _permissions(permissions, selected_role)
    if current.get('role') != 'owner' and (selected_role in {'admin','owner'} or set(selected_permissions)-set(current.get('permissions') or [])):
        raise TenantPlatformError('Administrator yalnız öz hüquqları daxilində dəvət yarada bilər.')
    token = secrets.token_urlsafe(24)
    # Store only a one-way digest. The original token appears once in the
    # invite URL and cannot be recovered from the database.
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    invite_id = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            # Keep previously sent links valid until they are used or expire.
            # This matters when an administrator opens the settings page again
            # and creates a replacement link before the employee taps the first
            # one in Telegram.  Each token is still one-use and time-limited.
            cur.execute("""
                INSERT INTO saas_tenant_invites (id, tenant_id, token_hash, display_name, role, permissions, created_by, expires_at, invited_email)
                VALUES (%s, %s::uuid, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """, (invite_id, tenant_id, token_hash, name, selected_role, _json(selected_permissions), int(owner_id), expires, email))
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                           VALUES(%s,%s::uuid,%s,'employee.invite.created','invite',%s,%s::jsonb)''',
                        (uuid.uuid4(),tenant_id,int(owner_id),str(invite_id),_json({'role':selected_role,'email_bound':bool(email)})))
        conn.commit()
    return {"id": str(invite_id), "token": token, "role": selected_role, "invited_email": email,
            "expires_at": expires.isoformat()}


def accept_email_invite(*, token: str, actor_id: int, email: str, display_name: str = '') -> dict:
    """Accept an invitation using an already verified email account session."""
    token_hash = hashlib.sha256(_invite_token(token).encode('utf-8')).hexdigest()
    address = str(email or '').strip().casefold()
    if not address or int(actor_id) <= 0: raise TenantPlatformError('Email hesabı ilə giriş edin.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT i.*,t.name AS tenant_name FROM saas_tenant_invites i JOIN saas_tenants t ON t.id=i.tenant_id
                           WHERE i.token_hash=%s FOR UPDATE OF i''',(token_hash,))
            invite=cur.fetchone()
            if not invite or invite.get('accepted_at') or invite.get('expires_at')<=datetime.now(timezone.utc):
                raise TenantPlatformError('Dəvət linki etibarlı deyil və ya müddəti bitib.')
            allowed=str(invite.get('invited_email') or '').casefold()
            if allowed and allowed != address: raise TenantPlatformError('Bu dəvət başqa email ünvanı üçün yaradılıb.')
            if not allowed: cur.execute('UPDATE saas_tenant_invites SET invited_email=%s WHERE id=%s',(address,invite['id']))
            cur.execute('''SELECT id FROM saas_auth_users WHERE actor_id=%s AND email=%s AND verified_at IS NOT NULL
                           AND disabled_at IS NULL AND status='active' ''',(int(actor_id),address))
            if not cur.fetchone(): raise TenantPlatformError('Təsdiqlənmiş email hesabı tapılmadı.')
            cur.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions,active,invited_by)
                           VALUES(%s::uuid,%s,%s,%s,%s::jsonb,TRUE,%s) ON CONFLICT(tenant_id,telegram_id) DO NOTHING''',
                        (invite['tenant_id'],int(actor_id),str(display_name or invite['display_name'] or '').strip()[:120],
                         invite['role'],_json(invite['permissions']),int(invite['created_by'])))
            if cur.rowcount!=1: raise TenantPlatformError('Bu hesab artıq şirkətin üzvüdür.')
            cur.execute('UPDATE saas_tenant_invites SET accepted_by=%s,accepted_at=now() WHERE id=%s',(int(actor_id),invite['id']))
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                           VALUES(%s,%s::uuid,%s,'employee.invite.accepted','invite',%s,'{}'::jsonb)''',
                        (uuid.uuid4(),invite['tenant_id'],int(actor_id),str(invite['id'])))
        conn.commit()
    return {'tenant_id':str(invite['tenant_id']),'tenant_name':str(invite['tenant_name'])}


def request_invite_acceptance(*, token: str, telegram_id: int, display_name: str) -> dict:
    """Bind a Telegram identity to an invite, awaiting owner approval."""
    normalized_token = _invite_token(token)
    token_hash = hashlib.sha256(normalized_token.encode("utf-8")).hexdigest()
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM saas_tenant_invites WHERE token_hash = %s FOR UPDATE", (token_hash,))
            invite = cur.fetchone()
            if not invite:
                raise TenantPlatformError("Dəvət linki etibarlı deyil. Administratorla əlaqə saxlayın.")
            if invite.get("accepted_at"):
                raise TenantPlatformError("Bu dəvət linki artıq istifadə olunub.")
            if invite.get("expires_at") and invite["expires_at"] <= datetime.now(timezone.utc):
                raise TenantPlatformError("Dəvət linkinin müddəti bitib. Administratorla əlaqə saxlayın.")
            if str(invite.get('invited_email') or '').strip():
                raise TenantPlatformError('Bu dəvəti email ünvanı ilə qəbul edin.')
            cur.execute("SELECT * FROM saas_tenant_invite_requests WHERE invite_id = %s FOR UPDATE", (invite["id"],))
            existing = cur.fetchone()
            if existing:
                if int(existing["telegram_id"]) != int(telegram_id):
                    raise TenantPlatformError("Bu dəvət artıq başqa hesab tərəfindən istifadə olunub.")
                # A rejected request is not a permanent technical failure.
                # Let the same invited employee ask again after correcting a
                # mistaken decision, while keeping the audit timestamp.
                if existing["status"] == "rejected":
                    cur.execute("""
                        UPDATE saas_tenant_invite_requests
                        SET status = 'pending', decided_by = NULL, decided_at = NULL, display_name = %s
                        WHERE id = %s RETURNING *
                    """, (str(display_name or invite["display_name"] or "").strip()[:120], existing["id"]))
                    result = dict(cur.fetchone())
                else:
                    result = dict(existing)
            else:
                request_id = uuid.uuid4()
                cur.execute("""
                    INSERT INTO saas_tenant_invite_requests (id, invite_id, tenant_id, telegram_id, display_name)
                    VALUES (%s, %s, %s, %s, %s) RETURNING *
                """, (request_id, invite["id"], invite["tenant_id"], int(telegram_id), str(display_name or invite["display_name"] or "").strip()[:120]))
                result = dict(cur.fetchone())
        conn.commit()
    return {
        "id": str(result["id"]), "tenant_id": str(result["tenant_id"]), "status": result["status"],
        "owner_id": int(invite["created_by"]), "employee_name": str(result.get("display_name") or invite.get("display_name") or "Əməkdaş"),
        "card_name": str(invite.get("display_name") or "Əməkdaş"),
    }


def list_invite_requests(*, tenant_id: str, owner_id: int) -> list[dict]:
    current = member(tenant_id, owner_id)
    if not _may_manage(current):
        raise TenantPlatformError("İcazə yoxdur.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.id, r.telegram_id, r.display_name, r.status, r.created_at,
                       i.display_name AS card_name, i.role, i.permissions
                FROM saas_tenant_invite_requests r
                JOIN saas_tenant_invites i ON i.id = r.invite_id
                WHERE r.tenant_id = %s::uuid
                ORDER BY CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END, r.created_at DESC
            """, (tenant_id,))
            rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
        if not isinstance(item.get("permissions"), list):
            item["permissions"] = []
        result.append(item)
    return result


def decide_invite_request(*, tenant_id: str, owner_id: int, request_id: str, approve: bool) -> dict:
    current = member(tenant_id, owner_id)
    if not _may_manage(current):
        raise TenantPlatformError("İcazə yoxdur.")
    try:
        rid = uuid.UUID(str(request_id))
    except (ValueError, TypeError):
        raise TenantPlatformError("Sorğu tapılmadı.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.*, i.display_name AS card_name, i.role, i.permissions, i.created_by
                FROM saas_tenant_invite_requests r JOIN saas_tenant_invites i ON i.id = r.invite_id
                WHERE r.id = %s AND r.tenant_id = %s::uuid FOR UPDATE
            """, (rid, tenant_id))
            request = cur.fetchone()
            if not request or request["status"] != "pending":
                raise TenantPlatformError("Sorğu artıq işlənib və ya tapılmadı.")
            if current.get('role') != 'owner' and request.get('role') in {'owner','admin'}:
                raise TenantPlatformError('Administrator sahib və ya administrator rolunu təsdiqləyə bilməz.')
            if approve:
                cur.execute("""
                    INSERT INTO saas_tenant_members (tenant_id, telegram_id, display_name, role, permissions, active, invited_by)
                    VALUES (%s::uuid, %s, %s, %s, %s::jsonb, TRUE, %s)
                    ON CONFLICT (tenant_id, telegram_id) DO UPDATE SET active = TRUE, display_name = EXCLUDED.display_name,
                        role = EXCLUDED.role, permissions = EXCLUDED.permissions, updated_at = now()
                """, (tenant_id, int(request["telegram_id"]), str(request["display_name"] or request["card_name"] or "").strip()[:120], request["role"], _json(request["permissions"]), int(owner_id)))
                cur.execute("UPDATE saas_tenant_invites SET accepted_by = %s, accepted_at = now() WHERE id = %s", (int(request["telegram_id"]), request["invite_id"]))
            cur.execute("UPDATE saas_tenant_invite_requests SET status = %s, decided_by = %s, decided_at = now() WHERE id = %s", ("approved" if approve else "rejected", int(owner_id), rid))
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                           VALUES(%s,%s::uuid,%s,%s,'employee_invite_request',%s,%s::jsonb)''',
                        (uuid.uuid4(),tenant_id,int(owner_id),'employee.invite.approved' if approve else 'employee.invite.rejected',
                         str(rid),_json({'role':request['role']})))
        conn.commit()
    return {"telegram_id": int(request["telegram_id"]), "display_name": str(request["display_name"] or request["card_name"] or ""), "approved": bool(approve)}
