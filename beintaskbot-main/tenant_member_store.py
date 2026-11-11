"""Tenant, membership and onboarding persistence."""
import hmac
import re
import uuid
from psycopg.errors import UniqueViolation

from tenant_platform_common import (
    _DEFAULT_MODULES, _DEFAULT_NOTIFICATIONS, _ROLE_PERMISSIONS,
    _permissions, _known_permissions, _role, _slug, _tenant_payload,
)
from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json
from tenant_workflow_store import _read_tenant_workflow
from tenant_policy import TenantPolicy
from tenant_employee_policy import employee_action_allowed, employee_target_allowed, creation_permission_allowed, delegated_permissions_allowed, member_permission_change_allowed

# The employee editor intentionally exposes only these stable product grants.
# Other stored grants (including known legacy modules) are retained verbatim
# when an employee card is updated; an older client must never erase them.
_EDITOR_PERMISSION_KEYS = {"tasks", "deals", "customers", "dialogs", "support"}
_RECOGNIZED_ROLES = {"owner", "admin", "manager", "operator", "worker", "master"}
_CARD_FIELDS = {'display_name', 'role', 'permissions', 'active', 'whatsapp_numbers', 'permission_changes', 'work_settings', 'access_settings', 'department_id'}
_GLOBAL_FIELDS = {'first_name', 'last_name', 'login', 'email', 'phone', 'avatar_url', 'account_status'}


class MemberCardError(TenantPlatformError):
    def __init__(self, code, status=400, *, fields=None, message=None):
        super().__init__(message or code)
        self.code, self.status, self.fields = code, status, fields or {}


def _require_member_schema(conn):
    """Startup installs these tables; employee requests never repair them."""
    with conn.cursor() as cur:
        cur.execute('''SELECT bool_and(to_regclass(name) IS NOT NULL) AS ready
          FROM unnest(%s::text[]) AS required(name)''', ([
            'saas_tenants', 'saas_tenant_members', 'saas_auth_users', 'saas_user_profiles',
            'saas_tenant_member_whatsapp_numbers', 'saas_tenant_audit_events',
            'saas_tenant_workflow_policies', 'saas_tenant_pipelines',
            'saas_tenant_pipeline_stages'],))
        if not (cur.fetchone() or {}).get('ready'):
            raise MemberCardError('member_schema_unavailable', 503)


def _version(row):
    value = row.get('updated_at')
    return value.isoformat() if hasattr(value, 'isoformat') else str(value or '')


def _employee_read_sql(current):
    scope = TenantPolicy(current).action_scope('employees', 'view')
    if not employee_action_allowed(current, 'view'):
        return 'FALSE', []
    if scope == 'self':
        return 'm.telegram_id=%s', [int(current['telegram_id'])]
    if scope == 'department':
        return ('m.department_id=%s::uuid', [str(current['department_id'])]) if current.get('department_id') else ('FALSE', [])
    return 'TRUE', []

def _card_access(current, target):
    reasons = {field: 'global_account_identity' for field in _GLOBAL_FIELDS}
    editable = set(_CARD_FIELDS)
    if current.get('role') != 'owner' and target.get('role') in {'owner', 'admin'}:
        reasons.update({field: 'protected_member' for field in editable})
        editable.clear()
    else:
        if target.get('role') == 'owner':
            for field in ('role', 'permissions', 'active'):
                editable.discard(field)
                reasons[field] = 'owner_protected'
        if int(current['telegram_id']) == int(target['telegram_id']):
            editable.discard('active')
            reasons['active'] = 'self_deactivation_forbidden'
            if current.get('role') != 'owner':
                for field in ('display_name', 'role', 'permissions', 'active'):
                    editable.discard(field)
                    reasons[field] = 'self_membership_protected'
        if target.get('active') is False:
            editable.discard('whatsapp_numbers')
            reasons['whatsapp_numbers'] = 'member_inactive'
        grants = set(target.get('permissions') or [])
        outside = grants - set(current.get('permissions') or [])
        if current.get('role') != 'owner' and outside:
            # Alias/role-only saves and reactivation retain the full grant
            # set. Visible grants can first be reduced by a permitted rights
            # save; preserved hidden grants cannot be removed by this editor.
            restricted = ['display_name', 'role']
            if outside - _EDITOR_PERMISSION_KEYS:
                restricted.append('permissions')
            for field in restricted:
                if field in editable:
                    editable.remove(field)
                    reasons[field] = 'permission_scope'
            if target.get('active') is False and 'active' in editable:
                editable.remove('active')
                reasons['active'] = 'permission_scope'
    if current.get('role') != 'owner':
        for field in ('permission_changes', 'work_settings', 'access_settings', 'department_id'):
            editable.discard(field)
            reasons[field] = 'owner_protected'
    elif 'permissions' not in editable:
        editable.discard('permission_changes')
        editable.discard('access_settings')
        reasons['permission_changes'] = 'owner_protected'
        reasons['access_settings'] = 'owner_protected'
    if not employee_target_allowed(current, target, 'update'):
        reasons.update({field: 'employee_update_scope' for field in editable})
        editable.clear()
    return sorted(editable), reasons


def _locked_members(cur, tenant_id, owner_id, target_id=None, *, operation=None):
    # Match native-task/workflow order: tenant, canonical policy keys, numeric
    # membership IDs. Include inactive targets so protected roles cannot hide.
    from tenant_scoped_access import scoped_access_enabled
    # Attenuation depends on company defaults, pipelines and member policies.
    # Their writers take tenant SHARE, so fence the complete snapshot before
    # policy/member locks; locking only existing policy rows misses inserts.
    fence = ' FOR UPDATE' if scoped_access_enabled() else ' FOR SHARE'
    cur.execute('SELECT id,modules,status,owner_telegram_id FROM saas_tenants WHERE id=%s::uuid' + fence, (tenant_id,))
    company = cur.fetchone()
    if not company:
        raise MemberCardError('member_access_revoked', 403)
    cur.execute('''SELECT policy_key FROM saas_tenant_workflow_policies
      WHERE tenant_id=%s::uuid AND policy_key=ANY(%s::text[])
      ORDER BY policy_key FOR SHARE''', (tenant_id, ['members', 'modules']))
    cur.fetchall()
    ids = sorted({int(owner_id)} | ({int(target_id)} if target_id is not None else set()))
    cur.execute('''SELECT * FROM saas_tenant_members WHERE tenant_id=%s::uuid
      AND telegram_id=ANY(%s::bigint[]) ORDER BY telegram_id FOR UPDATE''', (tenant_id, ids))
    workflow = None
    rows = {int(row['telegram_id']): dict(row) for row in cur.fetchall()}
    current = rows.get(int(owner_id))
    if current:
        workflow = _read_tenant_workflow(cur, tenant_id)
        current.update(modules=company.get('modules') or {}, workflow=workflow,
                       tenant_status=company.get('status', 'active'), company_owner_id=company.get('owner_telegram_id'))
    target = rows.get(int(target_id)) if target_id is not None else None
    allowed = employee_target_allowed(current, target, 'update') if target else (employee_action_allowed(current, 'create') if operation == 'create' else _can_manage_members(current))
    if not allowed:
        raise MemberCardError('member_access_revoked', 403)
    return current, target


def _creation_department(cur, tenant_id, current, department_id):
    from tenant_department_store import validate_department
    scope = TenantPolicy(current).action_scope('employees', 'create')
    if not employee_action_allowed(current, 'create'):
        raise MemberCardError('member_access_revoked', 403)
    if scope == 'department':
        own = current.get('department_id')
        if not own or str(department_id or '') != str(own):
            raise MemberCardError('member_field_read_only', 403, fields={'department_id': 'permission_scope'})
    return validate_department(cur, tenant_id, department_id)


def _creation_grants(current, permissions, *, role=None, recipient_id=0):
    # Existing legacy clients keep their established subset guard. New scoped
    # creation must not delegate a broad module grant masked by actor scopes.
    # Old policy records cannot silently revive authority for a new membership.
    from tenant_scoped_access import scoped_access_enabled
    from tenant_policy import policy_values
    inherited = (policy_values(current.get('workflow') or {}).get('members') or {}).get(str(recipient_id))
    if current.get('role') != 'owner' and scoped_access_enabled() and recipient_id and inherited:
        raise MemberCardError('member_field_read_only', 403, fields={'permissions': 'owner_review_required'})
    if not delegated_permissions_allowed(current, permissions, role=role, recipient_id=recipient_id):
        raise MemberCardError('member_field_read_only', 403, fields={'permissions': 'permission_scope'})


def employee_creation_options(*, tenant_id, owner_id):
    """Bounded department options, never employee identities, for C without R."""
    with _connect() as conn:
        _require_member_schema(conn)
        with conn.cursor() as cur:
            current, _ = _locked_members(cur, tenant_id, owner_id, operation='create')
            from tenant_department_store import department_catalog
            scope = TenantPolicy(current).action_scope('employees', 'create')
            departments = [row for row in department_catalog(cur, tenant_id) if row['active']
                and (scope != 'department' or row['id'] == str(current.get('department_id')))]
            if scope == 'department' and not departments:
                raise MemberCardError('member_access_revoked', 403)
            return {'scope': scope, 'departments': departments,
                    'department_required': scope == 'department',
                    'permission_keys': [grant for grant in sorted(_known_permissions())
                        if creation_permission_allowed(current, grant)]}


def _protected_target(current, target, *, require_active=False):
    if not target:
        raise MemberCardError('member_not_found', 404)
    if current.get('role') != 'owner' and target.get('role') in {'owner', 'admin'}:
        raise MemberCardError('member_field_read_only', 403, fields={'role': 'protected_member'})
    if require_active and target.get('active') is False:
        raise MemberCardError('member_inactive', 409, fields={'whatsapp_numbers': 'member_inactive'})


def _audit_member(cur, tenant_id, owner_id, target, action, payload):
    cur.execute('''INSERT INTO saas_tenant_audit_events
      (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
      VALUES(%s,%s::uuid,%s,%s,'employee',%s,%s::jsonb)''',
      (uuid.uuid4(), tenant_id, int(owner_id), action, str(target), _json(payload)))


def _bump_member(cur, tenant_id, target):
    cur.execute('''UPDATE saas_tenant_members
      SET updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
      WHERE tenant_id=%s::uuid AND telegram_id=%s RETURNING *''', (tenant_id, target))
    return cur.fetchone()

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
        _require_member_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT m.*, au.email, t.name AS tenant_name, t.slug AS tenant_slug, t.status AS tenant_status, t.owner_telegram_id AS company_owner_id,
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
    result['department_id'] = str(result['department_id']) if result.get('department_id') else None
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
    result['department_id'] = str(result['department_id']) if result.get('department_id') else None
    for key in ("created_at", "updated_at", "last_login_at"):
        if result.get(key):
            result[key] = result[key].isoformat()
    if not isinstance(result.get("permissions"), list):
        result["permissions"] = []
    # Stored grants are assignment data, not proof of effective access: module
    # toggles and role policy may still deny a grant.  Return both views so UI
    # cannot accidentally present an assigned right as usable access.
    result['explicit_permissions'] = list(result['permissions'])
    result['legacy_permissions'] = [item for item in result['permissions'] if item not in _known_permissions()]
    policy = TenantPolicy(result)
    effective = []
    for grant in result['permissions']:
        module, separator, action = str(grant).partition(':')
        allowed = policy.allows_action(module, action) if separator else policy.allows(module)
        if allowed:
            effective.append(grant)
    result['effective_permissions'] = effective
    result['card_version'] = _version(row)
    return result


def list_members(*, tenant_id: str, owner_id: int) -> list[dict]:
    """Return only cards in the current actor's independent read scope."""
    with _connect() as conn:
        _require_member_schema(conn)
        with conn.cursor() as cur:
            from tenant_scoped_access_store import read_access_profile
            current = read_access_profile(cur, tenant_id, owner_id, lock=True)
            if not employee_action_allowed(current, 'view'):
                raise MemberCardError('member_access_revoked', 403)
            predicate, scope_params = _employee_read_sql(current)
            cur.execute(f"""
                SELECT m.tenant_id, m.telegram_id, m.display_name, m.role, m.permissions, m.active, m.department_id, t.modules,
                       m.created_at, m.updated_at, m.last_login_at, au.email, COALESCE(NULLIF(au.phone,''),up.phone,'') AS phone,
                       COALESCE(NULLIF(au.first_name,''),up.first_name,'') AS first_name,
                       COALESCE(NULLIF(au.last_name,''),up.last_name,'') AS last_name,
                       COALESCE(NULLIF(au.avatar_url,''),up.avatar_url,'') AS avatar_url,
                       COALESCE(NULLIF(au.login,''),up.login,'') AS login, au.status AS account_status,
                       COALESCE(m.active=TRUE AND au.tenant_id=m.tenant_id
                         AND au.verified_at IS NOT NULL AND au.disabled_at IS NULL AND au.status='active'
                         AND m.role<>'owner'
                         AND EXISTS(SELECT 1 FROM saas_tenant_audit_events a
                           WHERE a.tenant_id=m.tenant_id AND a.action='employee.provisioned'
                             AND a.entity_type='employee' AND a.entity_id=m.telegram_id::text)
                         AND NOT EXISTS(SELECT 1 FROM saas_tenant_members other
                           WHERE other.telegram_id=m.telegram_id AND other.tenant_id<>m.tenant_id)
                         AND NOT EXISTS(SELECT 1 FROM saas_tenants owned WHERE owned.owner_telegram_id=m.telegram_id),FALSE)
                           AS _password_assign_eligible,
                       COALESCE(m.telegram_id=%s AND au.tenant_id=m.tenant_id
                         AND au.disabled_at IS NULL AND au.status='active',FALSE) AS password_change_available
                FROM saas_tenant_members m
                JOIN saas_tenants t ON t.id=m.tenant_id
                LEFT JOIN saas_auth_users au ON au.actor_id = m.telegram_id
                LEFT JOIN saas_user_profiles up ON up.actor_id = m.telegram_id
                WHERE m.tenant_id = %s::uuid AND ({predicate})
                ORDER BY CASE WHEN m.role = 'owner' THEN 0 ELSE 1 END, m.display_name, m.telegram_id
            """, [int(owner_id), tenant_id, *scope_params])
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
            # TenantPolicy also uses per-member workflow settings (for
            # example admin AI-management); include its authoritative source.
            workflow = _read_tenant_workflow(cur, tenant_id)
            from tenant_department_store import department_catalog
            departments = department_catalog(cur, tenant_id)
    result=[]
    for row in rows:
        row['workflow'] = workflow
        value=_member_payload(row)
        # Workflow contains other employees' policy records, never card data.
        value.pop('workflow', None)
        value['whatsapp_numbers']=numbers.get(int(row['telegram_id']),[])
        value['editable_fields'], value['read_only_reasons'] = _card_access(current, row)
        value['password_assign_available'] = bool(value.pop('_password_assign_eligible', False)
            and current.get('role') == 'owner' and current.get('active') is True
            and current.get('company_owner_id') == int(owner_id)
            and int(row['telegram_id']) != int(owner_id))
        value['email_assign_available'] = value['password_assign_available']
        value['profile_assign_available'] = value['password_assign_available']
        if current.get('role') == 'owner':
            from tenant_member_settings import settings_snapshot, PERMISSION_MODULES
            value['work_settings'] = settings_snapshot(workflow, row)
            value['permission_modules'] = sorted(PERMISSION_MODULES)
            from tenant_member_access import access_snapshot
            value['access_settings'] = access_snapshot(row)
            value['department_catalog'] = departments
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


def _normalize_numbers(numbers):
    if not isinstance(numbers, list) or len(numbers) > 10:
        raise MemberCardError('member_card_invalid', fields={'whatsapp_numbers': 'invalid'})
    selected = {}
    for item in numbers:
        if not isinstance(item, dict) or set(item) - {'phone', 'recognition_enabled'}:
            raise MemberCardError('member_card_invalid', fields={'whatsapp_numbers': 'invalid'})
        digits = _whatsapp_digits(item.get('phone'))
        enabled = item.get('recognition_enabled', True)
        if not digits or type(enabled) is not bool:
            raise MemberCardError('member_card_invalid', fields={'whatsapp_numbers': 'invalid'})
        # Preserve the original first-occurrence semantics for duplicate input.
        selected.setdefault(digits, enabled)
    return sorted(selected.items())


def _replace_numbers(cur, tenant_id, owner_id, target, selected):
    # Foreign ownership is checked without taking another member's number
    # locks. The existing tenant/phone unique key closes absent-row races.
    cur.execute('''SELECT phone_digits,telegram_id FROM saas_tenant_member_whatsapp_numbers
      WHERE tenant_id=%s::uuid AND phone_digits=ANY(%s::text[])''',
      (tenant_id, [digits for digits, _ in selected]))
    if any(int(row['telegram_id']) != target for row in cur.fetchall()):
        raise MemberCardError('member_duplicate', 409, fields={'whatsapp_numbers': 'duplicate'})
    cur.execute('DELETE FROM saas_tenant_member_whatsapp_numbers WHERE tenant_id=%s::uuid AND telegram_id=%s',
                (tenant_id, target))
    for digits, enabled in selected:
        cur.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
          (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
          VALUES(%s::uuid,%s,%s,%s,%s)''', (tenant_id, target, digits, enabled, int(owner_id)))


def _unique_error(exc):
    constraint = getattr(getattr(exc, 'diag', None), 'constraint_name', '') or ''
    field = {'saas_auth_users_login_idx': 'login', 'saas_user_profiles_login_idx': 'login',
             'saas_auth_users_email_key': 'email',
             'saas_tenant_member_whatsapp_numbers_pkey': 'whatsapp_numbers'}.get(constraint)
    if not field:
        raise exc
    # Called outside the connection context, after the whole write rolled back.
    raise MemberCardError('member_duplicate', 409, fields={field: 'duplicate'}) from None


def _selected_membership(current, existing, requested_role, permissions, *, target, strict=False):
    if strict:
        selected_role = str(requested_role)
        if (selected_role == 'owner' and existing.get('role') != 'owner'
                or selected_role not in _RECOGNIZED_ROLES and selected_role != str(existing.get('role'))):
            raise MemberCardError('member_card_invalid', fields={'role': 'invalid'})
    else:
        selected_role = _role(str(requested_role or ''), owner=target == int(current['telegram_id']))
    selected_permissions = _permissions(permissions, selected_role)
    if existing and selected_role != 'owner':
        preserved = [item for item in (existing.get('permissions') or []) if item not in _EDITOR_PERMISSION_KEYS]
        selected_permissions = list(dict.fromkeys(selected_permissions + preserved))
        existing_role = str(existing.get('role') or '')
        if existing_role not in _RECOGNIZED_ROLES and requested_role == existing_role:
            selected_role = existing_role
    if current.get('role') != 'owner':
        if selected_role in {'owner', 'admin'} or set(selected_permissions) - set(current.get('permissions') or []):
            raise MemberCardError('member_field_read_only', 403, fields={'permissions': 'permission_scope'})
        if existing and existing.get('role') in {'owner', 'admin'}:
            raise MemberCardError('member_field_read_only', 403, fields={'role': 'protected_member'})
        if existing and not member_permission_change_allowed(current, existing, selected_role, selected_permissions):
            raise MemberCardError('member_field_read_only', 403, fields={'permissions': 'permission_scope'})
    return selected_role, selected_permissions


def _strict_card_update(*, tenant_id, owner_id, telegram_id, expected_version, changes):
    if (not isinstance(expected_version, str) or not expected_version or len(expected_version) > 100
            or not isinstance(changes, dict) or not changes or set(changes) - _CARD_FIELDS):
        raise MemberCardError('member_card_invalid', fields={'changes': 'invalid'})
    if 'display_name' in changes and (not isinstance(changes['display_name'], str)
            or not changes['display_name'].strip() or len(changes['display_name'].strip()) > 120):
        raise MemberCardError('member_card_invalid', fields={'display_name': 'invalid'})
    if 'role' in changes and not isinstance(changes['role'], str):
        raise MemberCardError('member_card_invalid', fields={'role': 'invalid'})
    if 'active' in changes and type(changes['active']) is not bool:
        raise MemberCardError('member_card_invalid', fields={'active': 'invalid'})
    if 'permissions' in changes and (not isinstance(changes['permissions'], list)
            or any(not isinstance(item, str) for item in changes['permissions'])):
        raise MemberCardError('member_card_invalid', fields={'permissions': 'invalid'})
    if 'permission_changes' in changes:
        from tenant_member_settings import PERMISSION_MODULES
        patch = changes['permission_changes']
        if (not isinstance(patch, dict) or not patch or set(patch) - PERMISSION_MODULES
                or any(type(value) is not bool for value in patch.values()) or 'permissions' in changes):
            raise MemberCardError('member_card_invalid', fields={'permissions': 'invalid'})
    if 'access_settings' in changes:
        patch = changes['access_settings']
        if (not isinstance(patch, dict) or set(patch) != {'expected_version', 'changes'}
                or not isinstance(patch['expected_version'], str)
                or not isinstance(patch['changes'], dict) or not patch['changes']
                or 'permissions' in changes):
            raise MemberCardError('member_card_invalid', fields={'access_settings': 'invalid'})
        action_modules = {**patch['changes'].get('actions', {}), **patch['changes'].get('scopes', {})} if all(isinstance(patch['changes'].get(key, {}), dict) for key in ('actions', 'scopes')) else None
        if isinstance(action_modules, dict) and set(action_modules) & set(changes.get('permission_changes', {})):
            raise MemberCardError('member_card_invalid', fields={'access_settings': 'overlapping_permissions'})
    selected = _normalize_numbers(changes['whatsapp_numbers']) if 'whatsapp_numbers' in changes else None
    try:
        with _connect() as conn:
            _require_member_schema(conn)
            with conn.cursor() as cur:
                from tenant_scoped_access import scoped_access_enabled
                if scoped_access_enabled() or set(changes) & {'department_id', 'access_settings'}:
                    # Fence all effective grant inputs before any policy locks.
                    # Department scope and first scoped-policy activation
                    # depend on more than an existing policy row. Serialize
                    # membership moves with all scoped reads and writes before
                    # taking workflow/member locks, not after acquiring them.
                    cur.execute('SELECT id FROM saas_tenants WHERE id=%s::uuid FOR UPDATE', (tenant_id,))
                if set(changes) & {'work_settings', 'access_settings'}:
                    cur.execute('SELECT id FROM saas_tenants WHERE id=%s::uuid FOR SHARE', (tenant_id,))
                    cur.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'members','{}'::jsonb,%s) ON CONFLICT DO NOTHING", (tenant_id, int(owner_id)))
                    cur.execute("SELECT policy_key FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='members' FOR UPDATE", (tenant_id,))
                current, existing = _locked_members(cur, tenant_id, owner_id, telegram_id)
                _protected_target(current, existing)
                if _version(existing) != expected_version:
                    raise MemberCardError('member_card_conflict', 409, fields={'expected_version': 'version_conflict'})
                editable, reasons = _card_access(current, existing)
                forbidden = set(changes) - set(editable)
                if forbidden:
                    raise MemberCardError('member_field_read_only', 403,
                                          fields={field: reasons.get(field, 'read_only') for field in forbidden})
                role = existing['role']; permissions = list(existing.get('permissions') or [])
                if 'permissions' in changes:
                    hidden = set(changes['permissions']) - _EDITOR_PERMISSION_KEYS
                    if hidden - set(existing.get('permissions') or []):
                        raise MemberCardError('member_card_invalid', fields={'permissions': 'read_only'})
                if set(changes) & {'display_name', 'role', 'permissions'} or changes.get('active') is True:
                    role, permissions = _selected_membership(current, existing,
                        changes.get('role', role), changes.get('permissions', permissions),
                        target=telegram_id, strict=True)
                    if 'permissions' not in changes:
                        permissions = list(existing.get('permissions') or [])
                if set(changes) & {'permission_changes', 'work_settings', 'access_settings', 'department_id'}:
                    cur.execute('SELECT owner_telegram_id,status FROM saas_tenants WHERE id=%s::uuid', (tenant_id,))
                    company = cur.fetchone() or {}
                    if company.get('owner_telegram_id') != int(owner_id) or company.get('status') != 'active':
                        raise MemberCardError('member_access_revoked', 403)
                department_id = existing.get('department_id')
                if 'department_id' in changes:
                    from tenant_department_store import validate_department
                    department_id = validate_department(cur, tenant_id, changes['department_id'])
                if 'permission_changes' in changes:
                    for module, enabled in changes['permission_changes'].items():
                        permissions = [grant for grant in permissions if grant != module and not grant.startswith(module + ':')]
                        if enabled:
                            permissions.append(module)
                if 'access_settings' in changes:
                    from tenant_member_access import access_snapshot, apply_access_patch
                    access_profile = {**existing, 'modules': current.get('modules') or {},
                                      'workflow': current.get('workflow') or {}}
                    snapshot = access_snapshot(access_profile)
                    patch = changes['access_settings']
                    if patch['expected_version'] != snapshot['version']:
                        raise MemberCardError('member_settings_conflict', 409,
                                              fields={'access_settings': 'version_conflict'})
                    try:
                        applied = apply_access_patch({**access_profile, 'permissions': permissions}, patch['changes'])
                    except (TypeError, ValueError):
                        raise MemberCardError('member_card_invalid',
                                              fields={'access_settings': 'invalid'}) from None
                    dialog_scopes = patch['changes'].get('scopes', {}).get('dialogs', {})
                    if any(scope in {'self', 'department'} for scope in dialog_scopes.values()):
                        from tenant_dialog_access import activation_preflight
                        preflight = activation_preflight(cur, tenant_id)
                        if not preflight['ready']:
                            raise MemberCardError('dialog_source_mapping_required', 409,
                                fields={'access_settings': 'source_mapping_required'},
                                message='Сначала распределите источники диалогов между сотрудниками.')
                    permissions = applied['permissions']
                    # Update this setting only. The same transaction may also
                    # change work settings; neither patch replaces the other.
                    settings_patch = {key: applied[key] for key in ('native_crm_actions', 'access_scopes')
                                      if key in applied}
                    cur.execute('''UPDATE saas_tenant_workflow_policies
                      SET value=jsonb_set(value,ARRAY[%s::text],
                        COALESCE(value->%s::text,'{}'::jsonb) || %s::jsonb,TRUE),
                        updated_by=%s,updated_at=now()
                      WHERE tenant_id=%s::uuid AND policy_key='members' ''',
                      (str(telegram_id),str(telegram_id),_json(settings_patch),int(owner_id),tenant_id))
                if 'work_settings' in changes:
                    from tenant_member_settings import apply_settings_patch
                    apply_settings_patch(cur, tenant_id=tenant_id, owner_id=owner_id, target=existing,
                        workflow=_read_tenant_workflow(cur, tenant_id), patch=changes['work_settings'])
                if selected is not None:
                    _replace_numbers(cur, tenant_id, owner_id, telegram_id, selected)
                cur.execute('''UPDATE saas_tenant_members SET display_name=%s,role=%s,permissions=%s::jsonb,
                  active=%s,department_id=%s::uuid,updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                  WHERE tenant_id=%s::uuid AND telegram_id=%s RETURNING *''',
                  (changes.get('display_name', existing['display_name']).strip(), role, _json(permissions),
                   changes.get('active', existing['active']), department_id, tenant_id, telegram_id))
                row = cur.fetchone()
                _audit_member(cur, tenant_id, owner_id, telegram_id, 'employee.card.updated',
                              {'fields': sorted(changes), 'role': role})
                receipt = {'telegram_id': telegram_id, 'card_version': _version(row)}
            conn.commit()
    except UniqueViolation as exc:
        _unique_error(exc)
    return receipt


def set_member_whatsapp_numbers(*, tenant_id: str, owner_id: int, telegram_id: int,
                                numbers: list[dict]) -> list[dict]:
    """Compatible replacement; every successful write invalidates card versions."""
    try: target = int(telegram_id)
    except (TypeError, ValueError): target = 0
    if target <= 0:
        raise MemberCardError('member_card_invalid', fields={'telegram_id': 'invalid'})
    selected = _normalize_numbers(numbers)
    try:
        with _connect() as conn:
            _require_member_schema(conn)
            with conn.cursor() as cur:
                current, existing = _locked_members(cur, tenant_id, owner_id, target)
                _protected_target(current, existing, require_active=True)
                _replace_numbers(cur, tenant_id, owner_id, target, selected)
                _bump_member(cur, tenant_id, target)
                _audit_member(cur, tenant_id, owner_id, target, 'employee.whatsapp_numbers.updated',
                              {'number_count': len(selected)})
            conn.commit()
    except UniqueViolation as exc:
        _unique_error(exc)
    return [{'phone': '+' + digits, 'recognition_enabled': enabled} for digits, enabled in selected]


def add_member_whatsapp_number(*, tenant_id: str, owner_id: int, telegram_id: int, phone: str) -> dict:
    """Add an explicitly attested number; keep participant-recognition idempotency."""
    try: target = int(telegram_id)
    except (TypeError, ValueError): target = 0
    digits = _whatsapp_digits(phone)
    if target <= 0 or not digits:
        raise MemberCardError('member_card_invalid', fields={'whatsapp_numbers': 'invalid'})
    try:
        with _connect() as conn:
            _require_member_schema(conn)
            with conn.cursor() as cur:
                current, existing = _locked_members(cur, tenant_id, owner_id, target)
                _protected_target(current, existing, require_active=True)
                cur.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
                  (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
                  VALUES(%s::uuid,%s,%s,TRUE,%s)
                  ON CONFLICT(tenant_id,phone_digits) DO UPDATE SET recognition_enabled=TRUE,updated_at=now()
                  WHERE saas_tenant_member_whatsapp_numbers.telegram_id=EXCLUDED.telegram_id RETURNING telegram_id''',
                  (tenant_id, target, digits, int(owner_id)))
                if not cur.fetchone():
                    raise MemberCardError('member_duplicate', 409, fields={'whatsapp_numbers': 'duplicate'})
                _bump_member(cur, tenant_id, target)
                _audit_member(cur, tenant_id, owner_id, target, 'employee.whatsapp_number.attested',
                              {'recognition_enabled': True})
            conn.commit()
    except UniqueViolation as exc:
        _unique_error(exc)
    return {'phone': '+' + digits, 'recognition_enabled': True}


def upsert_member(*, tenant_id: str, owner_id: int, telegram_id: int, display_name: str = '',
                  role: str = 'worker', permissions: list[str] | None = None,
                  expected_version=None, changes=None, department_id=None) -> dict:
    """Keep legacy upsert compatible; the new card explicitly selects strict CAS."""
    try: target = int(telegram_id)
    except (TypeError, ValueError): target = 0
    if target <= 0:
        raise MemberCardError('member_card_invalid', fields={'telegram_id': 'invalid'})
    if changes is not None or expected_version is not None:
        return _strict_card_update(tenant_id=tenant_id, owner_id=owner_id, telegram_id=target,
                                   expected_version=expected_version, changes=changes)
    name = str(display_name or '').strip()[:120]
    if not name:
        raise MemberCardError('member_card_invalid', fields={'display_name': 'invalid'})
    with _connect() as conn:
        _require_member_schema(conn)
        with conn.cursor() as cur:
            current, existing = _locked_members(cur, tenant_id, owner_id, target, operation='create')
            if existing:
                _protected_target(current, existing)
                if department_id is not None:
                    raise MemberCardError('member_card_invalid', fields={'department_id': 'use_versioned_card'})
            else:
                department_id = _creation_department(cur, tenant_id, current, department_id)
            selected_role, selected_permissions = _selected_membership(current, existing, str(role or ''),
                                                                       permissions, target=target)
            if not existing:
                _creation_grants(current, selected_permissions, role=selected_role, recipient_id=target)
            if existing:
                cur.execute('''UPDATE saas_tenant_members SET display_name=%s,
                  role=CASE WHEN role='owner' THEN 'owner' ELSE %s END,
                  permissions=CASE WHEN role='owner' THEN permissions ELSE %s::jsonb END,
                  active=TRUE,updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                  WHERE tenant_id=%s::uuid AND telegram_id=%s RETURNING *''',
                  (name, selected_role, _json(selected_permissions), tenant_id, target))
            else:
                # A concurrently created protected membership must not be
                # overwritten through a previously absent-target snapshot.
                cur.execute('''INSERT INTO saas_tenant_members
                  (tenant_id,telegram_id,display_name,role,permissions,active,invited_by,department_id)
                  VALUES(%s::uuid,%s,%s,%s,%s::jsonb,TRUE,%s,%s::uuid)
                  ON CONFLICT(tenant_id,telegram_id) DO NOTHING RETURNING *''',
                  (tenant_id, target, name, selected_role, _json(selected_permissions), int(owner_id), department_id))
            row = cur.fetchone()
            if not row:
                raise MemberCardError('member_card_conflict', 409)
            _audit_member(cur, tenant_id, owner_id, target, 'employee.updated' if existing else 'employee.created',
                          {'role': selected_role, 'permission_count': len(selected_permissions)})
        conn.commit()
    return _member_payload(row)


def provision_employee_account(*, tenant_id: str, owner_id: int, first_name: str, last_name: str,
                               login: str, password: str, role: str, permissions: list[str],
                               password_confirm: str, email: str = '', phone: str = '', department_id=None) -> dict:
    """Create a new global account and tenant membership, never merge an account."""
    from tenant_email_auth import _hash_password, _password, _email
    username = re.sub(r'[^a-zA-Z0-9_.-]', '', str(login or '').strip()).casefold()
    if not re.fullmatch(r'[a-z0-9_.-]{3,64}', username):
        raise MemberCardError('member_card_invalid', fields={'login': 'invalid'})
    try: plain = _password(password)
    except TenantPlatformError:
        raise MemberCardError('member_card_invalid', fields={'password': 'invalid'}) from None
    if not hmac.compare_digest(plain, str(password_confirm or '')):
        raise MemberCardError('member_card_invalid', fields={'confirm_password': 'invalid'})
    first = ' '.join(str(first_name or '').split())[:120]
    last = ' '.join(str(last_name or '').split())[:120]
    fields = {key: 'invalid' for key, value in [('first_name', first), ('last_name', last)] if len(value) < 2}
    if fields:
        raise MemberCardError('member_card_invalid', fields=fields)
    address = str(email or '').strip().casefold() or None
    if address:
        try: address = _email(address)
        except TenantPlatformError:
            raise MemberCardError('member_card_invalid', fields={'email': 'invalid'}) from None
    selected_role = _role(str(role or 'worker'))
    selected_permissions = _permissions(permissions, selected_role)
    hashed = _hash_password(plain)
    try:
        with _connect() as conn:
            _require_member_schema(conn)
            with conn.cursor() as cur:
                current, _ = _locked_members(cur, tenant_id, owner_id, operation='create')
                department_id = _creation_department(cur, tenant_id, current, department_id)
                _creation_grants(current, selected_permissions, role=selected_role)
                if current.get('role') != 'owner' and (selected_role in {'owner', 'admin'}
                        or set(selected_permissions) - set(current.get('permissions') or [])):
                    raise MemberCardError('member_field_read_only', 403, fields={'permissions': 'permission_scope'})
                cur.execute('''SELECT EXISTS(SELECT 1 FROM saas_auth_users WHERE lower(login)=lower(%s))
                  OR EXISTS(SELECT 1 FROM saas_user_profiles WHERE lower(login)=lower(%s)) AS login_taken,
                  EXISTS(SELECT 1 FROM saas_auth_users WHERE %s::text IS NOT NULL AND lower(email)=lower(%s)) AS email_taken''',
                  (username, username, address, address))
                duplicate = cur.fetchone()
                fields = {key: 'duplicate' for key in ('login', 'email') if duplicate.get(key + '_taken')}
                if fields:
                    raise MemberCardError('member_duplicate', 409, fields=fields)
                cur.execute("SELECT nextval('saas_auth_actor_seq') AS actor_id")
                actor = int(cur.fetchone()['actor_id']); uid = uuid.uuid4(); display = (first + ' ' + last).strip()
                _creation_grants(current, selected_permissions, role=selected_role, recipient_id=actor)
                cur.execute('''INSERT INTO saas_auth_users
                  (id,actor_id,email,phone,display_name,first_name,last_name,login,password_hash,tenant_id,verified_at,force_password_change)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid,now(),TRUE)''',
                  (uid, actor, address, str(phone or '')[:40], display, first, last, username, hashed, tenant_id))
                cur.execute('''INSERT INTO saas_user_profiles(actor_id,first_name,last_name,phone,login)
                  VALUES(%s,%s,%s,%s,%s)''', (actor, first, last, str(phone or '')[:40], username))
                cur.execute('''INSERT INTO saas_tenant_members
                  (tenant_id,telegram_id,display_name,role,permissions,active,invited_by,department_id)
                  VALUES(%s::uuid,%s,%s,%s,%s::jsonb,TRUE,%s,%s::uuid) RETURNING updated_at''',
                  (tenant_id, actor, display, selected_role, _json(selected_permissions), owner_id, department_id))
                version = _version(cur.fetchone())
                _audit_member(cur, tenant_id, owner_id, actor, 'employee.provisioned',
                              {'role': selected_role, 'login': username, 'has_email': bool(address)})
            conn.commit()
    except UniqueViolation as exc:
        _unique_error(exc)
    return {'actor_id': actor, 'display_name': display, 'login': username, 'role': selected_role,
            'permissions': selected_permissions, 'force_password_change': True, 'card_version': version}


def deactivate_member(*, tenant_id: str, owner_id: int, telegram_id: int) -> dict:
    """Compatible explicit deactivation, with fresh locked actor/target guards."""
    with _connect() as conn:
        _require_member_schema(conn)
        with conn.cursor() as cur:
            target = int(telegram_id)
            current, existing = _locked_members(cur, tenant_id, owner_id, target)
            _protected_target(current, existing)
            if target == int(owner_id):
                raise MemberCardError('member_field_read_only', 403, fields={'active': 'self_deactivation_forbidden'})
            if existing.get('role') == 'owner':
                raise MemberCardError('member_field_read_only', 403, fields={'active': 'owner_protected'})
            cur.execute('''UPDATE saas_tenant_members SET active=FALSE,
              updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
              WHERE tenant_id=%s::uuid AND telegram_id=%s RETURNING *''', (tenant_id, target))
            row = cur.fetchone()
            _audit_member(cur, tenant_id, owner_id, target, 'employee.deactivated', {})
        conn.commit()
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
