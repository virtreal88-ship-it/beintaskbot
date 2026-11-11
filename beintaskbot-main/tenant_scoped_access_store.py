"""Tenant-bound, transaction-friendly primitives for scoped API implementations.

These helpers never commit and never repair schema on a request. Callers retain
legacy provider restrictions when action_scope is None, and must use the same
transaction for the authorization check and the requested operation.
"""
import re

from tenant_policy import TenantPolicy
from tenant_scoped_access import ACTIVE_TENANT_STATUSES, canonical_module


def read_access_profile(cur, tenant_id, actor_id, lock=False):
    suffix = ' FOR SHARE' if lock else ''
    cur.execute('SELECT id,modules,status,owner_telegram_id,onboarding FROM saas_tenants WHERE id=%s::uuid' + suffix,
                (str(tenant_id),))
    company = cur.fetchone()
    if not company or company.get('status') not in ACTIVE_TENANT_STATUSES:
        return None
    if lock:
        cur.execute('''SELECT policy_key FROM saas_tenant_workflow_policies
            WHERE tenant_id=%s::uuid AND policy_key=ANY(%s::text[])
            ORDER BY policy_key FOR SHARE''', (str(tenant_id), ['members', 'modules']))
        cur.fetchall()
    cur.execute('''SELECT * FROM saas_tenant_members
        WHERE tenant_id=%s::uuid AND telegram_id=%s''' + suffix, (str(tenant_id), int(actor_id)))
    row = cur.fetchone()
    if not row or row.get('active') is not True:
        return None
    from tenant_workflow_store import _read_tenant_workflow
    return {**dict(row), 'modules': company.get('modules') or {},
            'tenant_status': company['status'], 'company_owner_id': company['owner_telegram_id'],
            'onboarding': company.get('onboarding') or {},
            'workflow': _read_tenant_workflow(cur, str(tenant_id))}


def _column(alias, column):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', alias) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', column):
        raise ValueError('invalid_scope_sql_identifier')
    return f'{alias}.{column}'


def resource_scope_sql(profile, module, action, alias='r', responsible_column='access_responsible_id'):
    """Return a parameterized predicate; always include the tenant boundary.

    For a missing scope, this tests the legacy CRUD grant but does not replace
    legacy pipeline, provider or assigned-task policies at the call site.
    """
    tenant = _column(alias, 'tenant_id')
    responsible = _column(alias, responsible_column)
    if not profile or profile.get('active') is False or profile.get('tenant_status', 'active') not in ACTIVE_TENANT_STATUSES:
        return 'FALSE', []
    if not profile.get('tenant_id') or not profile.get('telegram_id'):
        return 'FALSE', []
    module = canonical_module(module)
    policy = TenantPolicy(profile)
    scope = policy.action_scope(module, action)
    legacy_module = 'customers' if module == 'companies' else module
    if scope is None:
        if not policy.legacy_allows_action(legacy_module, action):
            return 'FALSE', []
        return f'{tenant}=%s::uuid', [str(profile['tenant_id'])]
    if scope == 'none':
        return 'FALSE', []
    params = [str(profile['tenant_id'])]
    if scope == 'all':
        return f'{tenant}=%s::uuid', params
    if scope == 'self':
        return f'({tenant}=%s::uuid AND {responsible}=%s)', params + [int(profile['telegram_id'])]
    if scope == 'department' and profile.get('department_id'):
        return (f'''({tenant}=%s::uuid AND EXISTS (
            SELECT 1 FROM saas_tenant_members scope_member
            WHERE scope_member.tenant_id={tenant}
              AND scope_member.telegram_id={responsible}
              AND scope_member.department_id=%s::uuid))''', params + [str(profile['department_id'])])
    return 'FALSE', []


def owned_resource_metadata(cur, tenant_id, row, *, lock=False, responsible_column='access_responsible_id'):
    """Resolve current responsible department in this tenant, never from payload."""
    resource = dict(row)
    resource['responsible_member_id'] = row.get(responsible_column)
    resource['responsible_department_id'] = None
    if str(row.get('tenant_id')) != str(tenant_id) or row.get(responsible_column) is None:
        return resource
    cur.execute('''SELECT department_id FROM saas_tenant_members
        WHERE tenant_id=%s::uuid AND telegram_id=%s''' + (' FOR SHARE' if lock else ''),
                (str(tenant_id), row[responsible_column]))
    member = cur.fetchone()
    if member:
        resource['responsible_department_id'] = member.get('department_id')
    return resource
