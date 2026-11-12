"""Explicit tenant default-deny policy, never inferred from role or page hiding."""
POLICY_KEY = 'employee_access_defaults'


def default_deny(policy):
    value = policy.policies.get(POLICY_KEY)
    return isinstance(value, dict) and value.get('deny_unset') is True


def published_section_allowed(policy, module):
    """Published catalog read only; public sharing and management stay separate."""
    if module not in {'news', 'instructions'}:
        return False
    if policy.profile.get('active') is False or policy.profile.get('tenant_status', 'active') != 'active':
        return False
    if policy.profile.get('role') == 'owner':
        return True
    scope = policy.action_scope(module, 'view')
    return scope == 'all' if scope is not None else not default_deny(policy)


def require_compatible_runtime(conn):
    """A rollout switch is not permission to reopen an activated workspace.

    Called before returning any platform connection when the engine is off.
    Refuse this misconfigured process rather than executing legacy-only routes.
    No policy is cached: another process may activate the mode concurrently.
    """
    from tenant_scoped_access import scoped_access_enabled
    if scoped_access_enabled():
        return
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('saas_tenant_workflow_policies') AS present")
        if not cur.fetchone()['present']:
            return
        cur.execute("SELECT 1 FROM saas_tenant_workflow_policies WHERE policy_key=%s AND value->'deny_unset'='true'::jsonb LIMIT 1", (POLICY_KEY,))
        if cur.fetchone():
            raise RuntimeError('employee_default_deny_requires_scoped_access_engine')
