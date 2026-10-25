"""Tenant, membership and onboarding persistence."""
import hmac
import re
import uuid

from tenant_platform_common import (
    _DEFAULT_MODULES, _DEFAULT_NOTIFICATIONS, _ROLE_PERMISSIONS,
    _permissions, _known_permissions, _role, _slug, _tenant_payload,
)
from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json
from tenant_workflow_store import _read_tenant_workflow
from tenant_policy import TenantPolicy

def _can_manage_members(current: dict | None) -> bool:
    return bool(current and current.get('active') is not False
                and TenantPolicy(current).allows_action('employees', 'manage'))

def create_tenant(*, name: str, industry: str, owner_telegram_id: int, owner_name: str) -> dict:
    company = str(name or "").strip()[:120]
    if len(company) < 2:
        raise TenantPlatformError("Şirkətin adını yazın.")
    tenant_id = uuid.uuid4()
    onboarding = {"company_name": company, "industry": str(industry or "").strip()[:120], "completed_steps": ["company", "owner"]}
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO saas_tenants (id, slug, name, industry, owner_telegram_id, modules, notification_rules, onboarding)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                RETURNING *
            """, (tenant_id, _slug(company), company, onboarding["industry"], int(owner_telegram_id), _json(_DEFAULT_MODULES), _json(_DEFAULT_NOTIFICATIONS), _json(onboarding)))
            tenant = cur.fetchone()
            cur.execute("""
                INSERT INTO saas_tenant_members (tenant_id, telegram_id, display_name, role, permissions)
                VALUES (%s, %s, %s, 'owner', %s::jsonb)
            """, (tenant_id, int(owner_telegram_id), str(owner_name or "").strip()[:120], _json(_ROLE_PERMISSIONS["owner"])))
        conn.commit()
    return _tenant_payload(tenant)


def member(tenant_id: str, telegram_id: int) -> dict | None:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT m.*, au.email, t.name AS tenant_name, t.slug AS tenant_slug, t.status AS tenant_status,
                       t.modules, t.notification_rules, t.onboarding, t.industry,
                       COALESCE(NULLIF(au.first_name,''),up.first_name,'') AS first_name,
                       COALESCE(NULLIF(au.last_name,''),up.last_name,'') AS last_name,
                       COALESCE(NULLIF(au.avatar_url,''),up.avatar_url,'') AS avatar_url,
                       COALESCE(NULLIF(au.login,''),up.login,'') AS login,
                       COALESCE(NULLIF(au.phone,''),up.phone,'') AS account_phone, au.status AS account_status,
                       COALESCE(au.last_login_at,m.last_login_at) AS account_last_login_at
                FROM saas_tenant_members m
                JOIN saas_tenants t ON t.id = m.tenant_id
                LEFT JOIN saas_auth_users au ON au.actor_id = m.telegram_id
                LEFT JOIN saas_user_profiles up ON up.actor_id = m.telegram_id
                WHERE m.tenant_id = %s::uuid AND m.telegram_id = %s AND m.active = TRUE
            """, (tenant_id, int(telegram_id)))
            row = cur.fetchone()
            workflow = _read_tenant_workflow(cur, tenant_id) if row else {}
    if not row:
        return None
    result = dict(row)
    result['workflow'] = workflow
    result["tenant_id"] = str(result["tenant_id"])
    # ``member`` is returned directly by JSON API handlers. PostgreSQL gives
    # timestamps as datetime instances, which must be made JSON-safe here.
    for key in ("created_at", "updated_at", "last_login_at", "account_last_login_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    for key in ("permissions", "modules", "notification_rules", "onboarding"):
        if not isinstance(result.get(key), (dict, list)):
            result[key] = {} if key != "permissions" else []
    return result


def tenants_for_user(telegram_id: int) -> list[dict]:
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.*, m.role, m.permissions, m.display_name
                FROM saas_tenants t JOIN saas_tenant_members m ON m.tenant_id = t.id
                WHERE m.telegram_id = %s AND m.active = TRUE ORDER BY t.created_at DESC
            """, (int(telegram_id),))
            rows = cur.fetchall()
    return [_tenant_payload(row) for row in rows]


def _member_payload(row: dict) -> dict:
    """Make a member row safe to return from the platform API."""
    result = dict(row)
    result["tenant_id"] = str(result["tenant_id"])
    for key in ("created_at", "updated_at", "last_login_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    if not isinstance(result.get("permissions"), list):
        result["permissions"] = []
    # Stored grants are the only source of non-owner access. Keep unknown
    # historical grants visible but never treat them as currently effective.
    result['explicit_permissions'] = list(result['permissions'])
    result['legacy_permissions'] = [item for item in result['permissions'] if item not in _known_permissions()]
    result['effective_permissions'] = (list(_ROLE_PERMISSIONS['owner']) if result.get('role') == 'owner'
                                       else [item for item in result['permissions'] if item in _known_permissions()])
    return result


def list_members(*, tenant_id: str, owner_id: int) -> list[dict]:
    """Return the tenant's employee cards to its owner only."""
    current = member(tenant_id, owner_id)
    if not _can_manage_members(current):
        raise TenantPlatformError("İcazə yoxdur.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT m.tenant_id, m.telegram_id, m.display_name, m.role, m.permissions, m.active,
                       m.created_at, m.updated_at, m.last_login_at, au.email, COALESCE(NULLIF(au.phone,''),up.phone,'') AS phone,
                       COALESCE(NULLIF(au.first_name,''),up.first_name,'') AS first_name,
                       COALESCE(NULLIF(au.last_name,''),up.last_name,'') AS last_name,
                       COALESCE(NULLIF(au.avatar_url,''),up.avatar_url,'') AS avatar_url,
                       COALESCE(NULLIF(au.login,''),up.login,'') AS login, au.status AS account_status
                FROM saas_tenant_members m
                LEFT JOIN saas_auth_users au ON au.actor_id = m.telegram_id
                LEFT JOIN saas_user_profiles up ON up.actor_id = m.telegram_id
                WHERE m.tenant_id = %s::uuid
                ORDER BY CASE WHEN m.role = 'owner' THEN 0 ELSE 1 END, m.display_name, m.telegram_id
            """, (tenant_id,))
            rows = cur.fetchall()
            ids=[int(row['telegram_id']) for row in rows]
            numbers={member_id:[] for member_id in ids}
            if ids:
                cur.execute('''SELECT telegram_id,phone_digits,recognition_enabled
                  FROM saas_tenant_member_whatsapp_numbers
                  WHERE tenant_id=%s::uuid AND telegram_id=ANY(%s::bigint[])
                  ORDER BY telegram_id,phone_digits''',(tenant_id,ids))
                for item in cur.fetchall():
                    numbers[int(item['telegram_id'])].append({
                        'phone':'+'+str(item['phone_digits']),
                        'recognition_enabled':bool(item['recognition_enabled'])})
    result=[]
    for row in rows:
        value=_member_payload(row)
        value['whatsapp_numbers']=numbers.get(int(row['telegram_id']),[])
        result.append(value)
    return result


def _whatsapp_digits(value):
    raw=str(value or '').strip()
    if not raw or any(char not in '+0123456789 ()-.' for char in raw):
        return ''
    if '+' in raw and (not raw.startswith('+') or raw.count('+')!=1):
        return ''
    digits=re.sub(r'\D','',raw)
    if raw.startswith('00'):
        digits=digits[2:]
    elif raw.startswith('+'):
        pass
    elif digits.startswith('0') and len(digits)==10:
        # Azerbaijan national notation: 0XX XXX XX XX.
        digits='994'+digits[1:]
    elif not digits.startswith('994'):
        # A country code must be explicit outside the supported AZ local form.
        return ''
    return digits if re.fullmatch(r'[1-9][0-9]{7,14}',digits) else ''


def set_member_whatsapp_numbers(*, tenant_id: str, owner_id: int, telegram_id: int,
                                numbers: list[dict]) -> list[dict]:
    """Replace one member's explicit WhatsApp recognition numbers, tenant-locally."""
    current=member(tenant_id,owner_id)
    if not _can_manage_members(current):
        raise TenantPlatformError('İcazə yoxdur.')
    try:target=int(telegram_id)
    except (TypeError,ValueError):target=0
    if target<=0 or not isinstance(numbers,list) or len(numbers)>10:
        raise TenantPlatformError('WhatsApp nömrələri düzgün deyil.')
    selected=[];seen=set()
    for item in numbers:
        if not isinstance(item,dict) or set(item)-{'phone','recognition_enabled'}:
            raise TenantPlatformError('WhatsApp nömrələri düzgün deyil.')
        digits=_whatsapp_digits(item.get('phone'))
        if not digits:
            raise TenantPlatformError('Nömrəni beynəlxalq formatda yazın; Azərbaycan üçün +994, 994 və ya 0 ilə başlayan lokal format uyğundur.')
        if digits in seen:continue
        seen.add(digits)
        enabled=item.get('recognition_enabled',True)
        if type(enabled) is not bool:raise TenantPlatformError('Nömrənin tanınma statusu düzgün deyil.')
        selected.append((digits,enabled))
    target_member=member(tenant_id,target)
    if not target_member:
        raise TenantPlatformError('Əməkdaş bu şirkətdə aktiv deyil.')
    if current.get('role')!='owner' and target_member.get('role') in {'owner','admin'}:
        raise TenantPlatformError('Administrator sahibin və ya başqa administratorun nömrələrini dəyişə bilməz.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT phone_digits,telegram_id FROM saas_tenant_member_whatsapp_numbers
              WHERE tenant_id=%s::uuid AND phone_digits=ANY(%s::text[]) FOR UPDATE''',
              (tenant_id,[item[0] for item in selected]))
            for row in cur.fetchall():
                if int(row['telegram_id'])!=target:
                    raise TenantPlatformError('Bu WhatsApp nömrəsi artıq başqa əməkdaşa məxsusdur.')
            cur.execute('DELETE FROM saas_tenant_member_whatsapp_numbers WHERE tenant_id=%s::uuid AND telegram_id=%s',
                        (tenant_id,target))
            for digits,enabled in selected:
                cur.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
                  (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
                  VALUES(%s::uuid,%s,%s,%s,%s)''',(tenant_id,target,digits,enabled,int(owner_id)))
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'employee.whatsapp_numbers.updated','employee',%s,%s::jsonb)''',
              (uuid.uuid4(),tenant_id,int(owner_id),str(target),_json({'number_count':len(selected)})))
        conn.commit()
    return [{'phone':'+'+digits,'recognition_enabled':enabled} for digits,enabled in selected]


def add_member_whatsapp_number(*, tenant_id: str, owner_id: int, telegram_id: int, phone: str) -> dict:
    """Add one explicitly attested employee number without replacing other numbers."""
    current=member(tenant_id,owner_id)
    if not _can_manage_members(current):
        raise TenantPlatformError('İcazə yoxdur.')
    try:target=int(telegram_id)
    except (TypeError,ValueError):target=0
    digits=_whatsapp_digits(phone)
    if target<=0 or not digits:
        raise TenantPlatformError('WhatsApp nömrələri düzgün deyil.')
    target_member=member(tenant_id,target)
    if not target_member:
        raise TenantPlatformError('Əməkdaş bu şirkətdə aktiv deyil.')
    if current.get('role')!='owner' and target_member.get('role') in {'owner','admin'}:
        raise TenantPlatformError('Administrator sahibin və ya başqa administratorun nömrələrini dəyişə bilməz.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
              (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
              VALUES(%s::uuid,%s,%s,TRUE,%s)
              ON CONFLICT(tenant_id,phone_digits) DO UPDATE
                SET recognition_enabled=TRUE,updated_at=now()
                WHERE saas_tenant_member_whatsapp_numbers.telegram_id=EXCLUDED.telegram_id
              RETURNING telegram_id''',(tenant_id,target,digits,int(owner_id)))
            if not cur.fetchone():
                raise TenantPlatformError('Bu WhatsApp nömrəsi artıq başqa əməkdaşa məxsusdur.')
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'employee.whatsapp_number.attested','employee',%s,%s::jsonb)''',
              (uuid.uuid4(),tenant_id,int(owner_id),str(target),_json({'recognition_enabled':True})))
        conn.commit()
    return {'phone':'+'+digits,'recognition_enabled':True}


def upsert_member(*, tenant_id: str, owner_id: int, telegram_id: int, display_name: str,
                  role: str, permissions: list[str] | None = None) -> dict:
    """Create or update an employee card keyed by their Telegram account ID.

    This intentionally needs no invitation link: a person is admitted only
    after Telegram proves that they own this exact numeric account ID.
    """
    current = member(tenant_id, owner_id)
    if not _can_manage_members(current):
        raise TenantPlatformError("İcazə yoxdur.")
    try:
        target_id = int(telegram_id)
    except (TypeError, ValueError):
        target_id = 0
    if target_id <= 0:
        raise TenantPlatformError("Telegram ID düzgün deyil.")
    name = str(display_name or "").strip()[:120]
    if not name:
        raise TenantPlatformError("Əməkdaşın adını yazın.")
    is_owner = target_id == int(current["telegram_id"])
    selected_role = _role(str(role or ""), owner=is_owner)
    selected_permissions = _permissions(permissions, selected_role)
    existing_member = member(tenant_id, target_id)
    # A newer UI may not know a historical, now-retired grant.  Keep it
    # visible/read-only and preserve it during an otherwise unrelated edit;
    # authorization still fails closed because TenantPolicy does not infer it.
    if existing_member and selected_role != 'owner':
        legacy=[item for item in (existing_member.get('permissions') or []) if item not in _known_permissions()]
        selected_permissions=list(dict.fromkeys(selected_permissions+legacy))
    existed = existing_member is not None
    if current.get('role') != 'owner':
        if selected_role in {'owner', 'admin'} or set(selected_permissions) - set(current.get('permissions') or []):
            raise TenantPlatformError('Administrator yalnız öz hüquqları daxilində əməkdaş idarə edə bilər.')
        if existing_member and existing_member.get('role') in {'owner', 'admin'}:
            raise TenantPlatformError('Administrator sahibin və ya başqa administratorun rolunu dəyişə bilməz.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO saas_tenant_members (tenant_id, telegram_id, display_name, role, permissions, active, invited_by)
                VALUES (%s::uuid, %s, %s, %s, %s::jsonb, TRUE, %s)
                ON CONFLICT (tenant_id, telegram_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    role = CASE WHEN saas_tenant_members.role = 'owner' THEN 'owner' ELSE EXCLUDED.role END,
                    permissions = CASE WHEN saas_tenant_members.role = 'owner' THEN saas_tenant_members.permissions ELSE EXCLUDED.permissions END,
                    active = TRUE,
                    updated_at = now()
                RETURNING tenant_id, telegram_id, display_name, role, permissions, active, created_at, updated_at
            """, (tenant_id, target_id, name, selected_role, _json(selected_permissions), int(owner_id)))
            row = cur.fetchone()
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                           VALUES(%s,%s::uuid,%s,%s,'employee',%s,%s::jsonb)''',
                        (uuid.uuid4(),tenant_id,int(owner_id),'employee.updated' if existed else 'employee.created',
                         str(target_id),_json({'role':selected_role,'permission_count':len(selected_permissions)})))
        conn.commit()
    return _member_payload(row)


def provision_employee_account(*, tenant_id: str, owner_id: int, first_name: str, last_name: str,
                               login: str, password: str, role: str, permissions: list[str],
                               password_confirm: str, email: str = '', phone: str = '') -> dict:
    """Owner-created login account and membership, atomically and without invites."""
    current=member(tenant_id,owner_id)
    if not _can_manage_members(current): raise TenantPlatformError('İcazə yoxdur.')
    from tenant_email_auth import _hash_password, _password
    username=re.sub(r'[^a-zA-Z0-9_.-]','',str(login or '').strip()).casefold()
    if not re.fullmatch(r'[a-z0-9_.-]{3,64}',username): raise TenantPlatformError('Login 3–64 hərfdən ibarət olmalıdır.')
    plain=_password(password)
    if not hmac.compare_digest(plain, str(password_confirm or '')):
        raise TenantPlatformError('Parollar eyni deyil.')
    first=' '.join(str(first_name or '').split())[:120];last=' '.join(str(last_name or '').split())[:120]
    if len(first)<2 or len(last)<2: raise TenantPlatformError('Ad və soyad yazın.')
    selected_role=_role(str(role or 'worker'));selected_permissions=_permissions(permissions,selected_role)
    if current.get('role')!='owner' and (selected_role in {'owner','admin'} or set(selected_permissions)-set(current.get('permissions') or [])):
        raise TenantPlatformError('Administrator yalnız öz hüquqları daxilində əməkdaş yarada bilər.')
    address=str(email or '').strip().casefold() or None
    if address:
        from tenant_email_auth import _email
        address=_email(address)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            # Keep these checks lockable. PostgreSQL rejects FOR UPDATE on a
            # UNION, and the unique indexes remain the final race protection.
            cur.execute('''SELECT 1 FROM saas_auth_users WHERE lower(login)=lower(%s)
              OR (%s::text IS NOT NULL AND lower(email)=lower(%s)) FOR UPDATE''',(username,address,address))
            if cur.fetchone(): raise TenantPlatformError('Login və ya email artıq istifadə olunur.')
            cur.execute('SELECT 1 FROM saas_user_profiles WHERE lower(login)=lower(%s) FOR UPDATE',(username,))
            if cur.fetchone(): raise TenantPlatformError('Login və ya email artıq istifadə olunur.')
            cur.execute("SELECT nextval('saas_auth_actor_seq') AS actor_id");actor=int(cur.fetchone()['actor_id']);uid=uuid.uuid4();display=(first+' '+last).strip()
            cur.execute('''INSERT INTO saas_auth_users(id,actor_id,email,phone,display_name,first_name,last_name,login,password_hash,tenant_id,verified_at,force_password_change)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid,now(),TRUE)''',(uid,actor,address,str(phone or '')[:40],display,first,last,username,_hash_password(plain),tenant_id))
            cur.execute('''INSERT INTO saas_user_profiles(actor_id,first_name,last_name,phone,login) VALUES(%s,%s,%s,%s,%s)''',(actor,first,last,str(phone or '')[:40],username))
            cur.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions,active,invited_by)
              VALUES(%s::uuid,%s,%s,%s,%s::jsonb,TRUE,%s)''',(tenant_id,actor,display,selected_role,_json(selected_permissions),owner_id))
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,'employee.provisioned','employee',%s,%s::jsonb)''',(uuid.uuid4(),tenant_id,owner_id,str(actor),_json({'role':selected_role,'login':username,'has_email':bool(address)})))
        conn.commit()
    return {'actor_id':actor,'display_name':display,'login':username,'role':selected_role,'permissions':selected_permissions,'force_password_change':True}


def deactivate_member(*, tenant_id: str, owner_id: int, telegram_id: int) -> dict:
    """Disable a former employee without deleting their audit record."""
    current = member(tenant_id, owner_id)
    if not _can_manage_members(current):
        raise TenantPlatformError("İcazə yoxdur.")
    if int(telegram_id) == int(current["telegram_id"]):
        raise TenantPlatformError("Öz girişinizi bağlaya bilməzsiniz.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE saas_tenant_members SET active = FALSE, updated_at = now()
                WHERE tenant_id = %s::uuid AND telegram_id = %s AND role <> 'owner'
                  AND (%s='owner' OR role NOT IN ('admin'))
                RETURNING tenant_id, telegram_id, display_name, role, permissions, active, created_at, updated_at
            """, (tenant_id, int(telegram_id), current.get('role')))
            row = cur.fetchone()
            if row:
                cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                               VALUES(%s,%s::uuid,%s,'employee.deactivated','employee',%s,'{}'::jsonb)''',
                            (uuid.uuid4(),tenant_id,int(owner_id),str(telegram_id)))
        conn.commit()
    if not row:
        raise TenantPlatformError("Əməkdaş tapılmadı.")
    return _member_payload(row)


def update_onboarding(*, tenant_id: str, owner_id: int, patch: dict) -> dict:
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    safe_patch = {key: value for key, value in patch.items() if key in {"company_name", "industry", "team", "sources", "pipeline", "modules", "notifications", "notes", "completed_steps"}}
    onboarding = dict(current.get("onboarding") or {})
    onboarding.update(safe_patch)
    modules = dict(current.get("modules") or _DEFAULT_MODULES)
    if isinstance(safe_patch.get("modules"), dict):
        modules.update({key: bool(value) for key, value in safe_patch["modules"].items() if key in _DEFAULT_MODULES})
    notifications = dict(current.get("notification_rules") or _DEFAULT_NOTIFICATIONS)
    if isinstance(safe_patch.get("notifications"), dict):
        notifications.update({key: bool(value) for key, value in safe_patch["notifications"].items() if key in _DEFAULT_NOTIFICATIONS})
    company_name = str(safe_patch.get("company_name") or "").strip()[:120]
    if len(company_name) < 2:
        company_name = str(current.get("tenant_name") or "Yeni şirkət")[:120]
    industry = str(safe_patch.get("industry") or current.get("industry") or "").strip()[:120]
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE saas_tenants SET name = %s, industry = %s, onboarding = %s::jsonb, modules = %s::jsonb,
                    notification_rules = %s::jsonb, updated_at = now()
                WHERE id = %s::uuid RETURNING *
            """, (company_name, industry, _json(onboarding), _json(modules), _json(notifications), tenant_id))
            row = cur.fetchone()
        conn.commit()
    return _tenant_payload(row)


def finalize_onboarding(*, tenant_id: str, owner_id: int) -> dict:
    current = member(tenant_id, owner_id)
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("İcazə yoxdur.")
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE saas_tenants t SET status = CASE
                    WHEN t.status IN ('onboarding', 'ready_for_integration') THEN 'active'
                    ELSE t.status END, updated_at = now()
                WHERE t.id = %s::uuid RETURNING t.*
            """, (tenant_id,))
            row = cur.fetchone()
        conn.commit()
    return _tenant_payload(row)
