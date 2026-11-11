"""Tenant-local department catalog. Never cascade membership or permission edits."""
import uuid

from tenant_member_store import MemberCardError, _require_member_schema, _version
from tenant_platform_core import _connect, _json


def department_payload(row):
    return {'id': str(row['id']), 'name': row['name'], 'active': row['active'],
            'version': _version(row)}


def department_catalog(cur, tenant_id):
    cur.execute('''SELECT id,name,active,updated_at FROM saas_tenant_departments
      WHERE tenant_id=%s::uuid ORDER BY lower(name),id''', (tenant_id,))
    return [department_payload(row) for row in cur.fetchall()]


def validate_department(cur, tenant_id, department_id):
    """Lock the chosen department, denying foreign or archived IDs uniformly."""
    if department_id is None:
        return None
    try:
        if not isinstance(department_id, str):
            raise ValueError()
        identifier = str(uuid.UUID(department_id))
    except (ValueError, TypeError, AttributeError):
        raise MemberCardError('member_card_invalid', fields={'department_id': 'invalid'}) from None
    cur.execute('''SELECT id FROM saas_tenant_departments
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND active=TRUE FOR SHARE''', (tenant_id, identifier))
    if not cur.fetchone():
        raise MemberCardError('member_card_invalid', fields={'department_id': 'unavailable'})
    return identifier


def upsert_department(*, tenant_id, owner_id, name=None, department_id=None,
                      expected_version=None, active=None):
    # Lock company first: serializes catalog changes with each other and with
    # employee cards (FOR SHARE), avoiding archive/assignment races.
    if (active is not None and type(active) is not bool) or (department_id is None and active is False):
        raise MemberCardError('department_invalid', fields={'active': 'invalid'})
    if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 120):
        raise MemberCardError('department_invalid', fields={'name': 'invalid'})
    if department_id is None and name is None:
        raise MemberCardError('department_invalid', fields={'name': 'required'})
    if department_id is not None:
        try:
            if not isinstance(department_id, str):
                raise ValueError()
            department_id = str(uuid.UUID(department_id))
        except (ValueError, AttributeError):
            raise MemberCardError('department_invalid', fields={'department_id': 'invalid'}) from None
        if not isinstance(expected_version, str) or not 1 <= len(expected_version) <= 100:
            raise MemberCardError('department_invalid', fields={'expected_version': 'required'})
    with _connect() as conn:
        _require_member_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT owner_telegram_id,status FROM saas_tenants WHERE id=%s::uuid FOR UPDATE', (tenant_id,))
            company = cur.fetchone() or {}
            cur.execute('''SELECT role,active FROM saas_tenant_members
              WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE''', (tenant_id, int(owner_id)))
            owner = cur.fetchone() or {}
            if (company.get('status') != 'active' or company.get('owner_telegram_id') != int(owner_id)
                    or owner.get('role') != 'owner' or owner.get('active') is not True):
                raise MemberCardError('member_access_revoked', 403)
            existing = None
            if department_id:
                cur.execute('''SELECT * FROM saas_tenant_departments
                  WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE''', (tenant_id, department_id))
                existing = cur.fetchone()
                if not existing:
                    raise MemberCardError('department_not_found', 404)
                if _version(existing) != expected_version:
                    raise MemberCardError('department_conflict', 409, fields={'expected_version': 'version_conflict'})
            active = active if active is not None else existing['active'] if existing else True
            selected_name = name.strip() if name is not None else existing['name']
            cur.execute('''SELECT id FROM saas_tenant_departments WHERE tenant_id=%s::uuid
              AND lower(name)=lower(%s) AND (%s::uuid IS NULL OR id<>%s::uuid)''',
                        (tenant_id, selected_name, department_id, department_id))
            if cur.fetchone():
                raise MemberCardError('department_duplicate', 409, fields={'name': 'duplicate'})
            if not active:
                # Keep both active and inactive memberships intact. A later
                # reactivation must not unexpectedly change their department.
                cur.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND department_id=%s::uuid LIMIT 1',
                            (tenant_id, department_id))
                if cur.fetchone():
                    raise MemberCardError('department_in_use', 409, fields={'department_id': 'in_use'})
            if existing:
                cur.execute('''UPDATE saas_tenant_departments SET name=%s,active=%s,
                  updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
                  WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *''',
                            (selected_name, active, tenant_id, department_id))
            else:
                department_id = str(uuid.uuid4())
                cur.execute('''INSERT INTO saas_tenant_departments(tenant_id,id,name,active)
                  VALUES(%s::uuid,%s::uuid,%s,TRUE) RETURNING *''', (tenant_id, department_id, selected_name))
            result = department_payload(cur.fetchone())
            cur.execute('''INSERT INTO saas_tenant_audit_events
              (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s,%s::uuid,%s,%s,'department',%s,%s::jsonb)''',
                        (uuid.uuid4(), tenant_id, int(owner_id), 'department.updated' if existing else 'department.created',
                         department_id, _json({'active': active, 'name': selected_name})))
        conn.commit()
    return result
