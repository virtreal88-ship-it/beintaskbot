"""Current-DB CRUD scopes for company-wide integrations.

The responsible member is the unique active company owner, never the caller.
Provider-specific guards remain inside the operation. Credentials are never
returned here. Callers must wrap the complete operation, including provider I/O.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect

from tenant_linear_policy import TenantLinearError, identifier
from tenant_policy import TenantPolicy
from tenant_scoped_access import allows_owned_resource, profile_actor, scoped_access_enabled
from tenant_scoped_access_store import read_access_profile

_ADMISSION = ContextVar('shared_integration_admission', default=None)
PROVIDERS = frozenset({'kommo', 'kommo_manual', 'beinsystems', 'beinsystems_operator', 'linear', 'connect', 'chat_ai'})
LEGACY_ROLES = {'connect': {'owner', 'admin'}, 'chat_ai': {'owner', 'admin'}}


def principal(profile):
    if not isinstance(profile, dict):
        raise TenantLinearError('integration_access_denied', 403)
    tenant = identifier(profile.get('tenant_id'))
    try:
        if isinstance(profile.get('telegram_id'), bool): raise ValueError()
        actor = int(profile.get('telegram_id'))
    except (ValueError, TypeError):
        raise TenantLinearError('integration_access_denied', 403) from None
    if actor <= 0:
        raise TenantLinearError('integration_access_denied', 403)
    return tenant, actor


def admitted(profile, provider):
    value = _ADMISSION.get()
    return bool(value and isinstance(profile, dict) and value[:2] == principal(profile) and value[2] == provider)


def current_role(profile, provider):
    value = _ADMISSION.get()
    return value[4] if admitted(profile, provider) else None


def owner_resource(cur, current):
    """Ambiguous owner records do not become self/department grants."""
    tenant = str(current['tenant_id'])
    cur.execute("SELECT telegram_id,department_id,active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND role='owner' AND active=TRUE ORDER BY telegram_id FOR SHARE", (tenant,))
    rows = cur.fetchall()
    owner = rows[0] if len(rows) == 1 else None
    valid = bool(owner and owner.get('active') is True
                 and str(owner['telegram_id']) == str(current.get('company_owner_id')))
    return {'tenant_id': tenant,
            'responsible_member_id': owner['telegram_id'] if valid else None,
            'responsible_department_id': owner.get('department_id') if valid else None}


def allowed(current, resource, provider, action):
    if action not in {'view', 'create', 'update', 'delete'} or provider not in PROVIDERS:
        return False
    if provider == 'beinsystems_operator':
        from tenant_beinsystems_operator_access import is_operator_workspace
        if not is_operator_workspace(current.get('tenant_id')):
            return False
        if action != 'view' and current.get('role') != 'owner':
            return False
    if current.get('active') is not True or current.get('tenant_status') != 'active':
        return False
    if current.get('role') == 'owner':
        return True
    scope = TenantPolicy(current).action_scope('integrations', action)
    if scope is None:
        return current.get('role') in LEGACY_ROLES.get(provider, {'owner'})
    return allows_owned_resource(profile_actor(current), resource, scope)


def capabilities(cur, current, provider):
    resource = owner_resource(cur, current)
    return {action: allowed(current, resource, provider, action)
            for action in ('view', 'create', 'update', 'delete')}



def _action(cur, tenant, provider, action):
    if action != 'connect': return action
    cur.execute("SELECT 1 FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider=ANY(%s::text[]) AND (secrets IS NOT NULL OR status IN ('connected','configured','awaiting_oauth'))", (tenant, ['kommo','kommo_manual'] if provider in {'kommo','kommo_manual'} else [provider]))
    if cur.fetchone(): return 'update'
    if provider == 'connect':
        cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid LIMIT 1', (tenant,))
        if cur.fetchone(): return 'update'
    return 'create'


def _context(tenant, actor, action, connecting, data):
    if action == 'view': return
    if not isinstance(data, dict) or str(data.get('expected_tenant_id')) != tenant or str(data.get('expected_user_id')) != str(actor):
        raise TenantLinearError('integration_context_changed', 409)
    impact = action == 'delete' or (action == 'update' and (connecting or any(data.get(k) for k in ('api_key','access_token','token','remove_key'))))
    if impact and data.get('confirm_company_impact') is not True:
        raise TenantLinearError('integration_company_impact_confirmation_required',409)


def mutation_receipt(result, provider):
    """No previous settings/channel/employee projection through a write grant."""
    if isinstance(result, tuple) and len(result) == 2:
        return ({}, mutation_receipt(result[1], provider))  # manual Kommo account/connection
    result = result if isinstance(result, dict) else {}
    return {'provider': provider, **{key: result[key] for key in
        ('status','updated_at','connected','configured') if key in result}}

@contextmanager
def guard(factory, profile, provider, action, *, data=None, required_actions=(), legacy_member_read=False):
    """Hold current member/department/policy fences across a scoped operation.

    No override means the exact old provider guard remains authoritative. The
    context marker is internal and bound to actor, tenant and provider; a caller
    cannot manufacture it using role fields or JSON.
    """
    if not scoped_access_enabled():
        yield
        return
    existing = _ADMISSION.get()
    if existing:
        if not admitted(profile, provider):
            raise TenantLinearError('integration_nested_operation_denied',403)
        nested_action = _action(existing[7], existing[0], provider, action)
        if nested_action != existing[3] or not all(allowed(existing[5],existing[6],provider,item) for item in required_actions):
            raise TenantLinearError('integration_nested_operation_denied',403)
        _context(existing[0],existing[1],nested_action,action == 'connect',data)
        yield
        return
    tenant, actor = principal(profile)
    with factory() as conn:
        with conn.cursor() as cur:
            current = read_access_profile(cur, tenant, actor, lock=True)
            if not current:
                raise TenantLinearError('integration_access_denied', 403)
            policy = TenantPolicy(current)
            explicit = policy.has_scoped_access('integrations')
            if not explicit and current.get('tenant_status') != 'active':
                # Release these new locks before old onboarding operations,
                # whose own provider-specific transaction model is unchanged.
                conn.commit()
                yield
                return
            if provider not in PROVIDERS:
                if explicit: raise TenantLinearError('integration_operation_not_scoped',403)
                conn.commit()
                yield
                return
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('shared-integration:'+tenant+':'+('kommo' if provider=='kommo_manual' else provider),))
            connecting = action == 'connect'
            action = _action(cur, tenant, provider, action)
            resource = owner_resource(cur, current)
            legacy_status = (legacy_member_read and action == 'view'
                             and policy.action_scope('integrations', 'view') is None)
            if not ((legacy_status or allowed(current, resource, provider, action))
                    and all(allowed(current, resource, provider, item) for item in required_actions)):
                raise TenantLinearError('integration_action_denied', 403)
            _context(tenant,actor,action,connecting,data)
            token = _ADMISSION.set((tenant, actor, provider, action, current['role'], current, resource, cur))
            try:
                yield
            finally:
                _ADMISSION.reset(token)


def operation(provider, action, *, data_arg=None, required_actions=(), legacy_member_read=False):
    """Decorate an existing synchronous provider service, never a background job."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def run(*args, **kwargs):
            if not scoped_access_enabled():
                return function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            profile = values.get('profile', values.get('session'))
            if profile is None:
                profile = {'tenant_id': values['tenant_id'], 'telegram_id': values['owner_id']}
            actual_provider = provider(values) if callable(provider) else provider
            actual_action = action(values) if callable(action) else action
            data = values.get(data_arg) if data_arg else None
            additional = required_actions(values) if callable(required_actions) else required_actions
            # Initialize schema before holding member/company row fences. Legacy
            # service initializers can commit or acquire DDL locks.
            if scoped_access_enabled() and _ADMISSION.get() is None:
                initializer = next((function.__globals__.get(name) for name in
                    ('_schema', 'ensure_schema', '_ensure_schema') if callable(function.__globals__.get(name))), None)
                if initializer:
                    with function.__globals__['_connect']() as conn:
                        initializer(conn)
                        conn.commit()
            with guard(function.__globals__['_connect'], profile, actual_provider, actual_action, data=data, required_actions=additional, legacy_member_read=legacy_member_read):
                result = function(*args, **kwargs)
                admission = _ADMISSION.get()
                if admission and admission[3] != 'view' and not allowed(admission[5],admission[6],actual_provider,'view'):
                    return mutation_receipt(result, actual_provider)
                return result
        return run
    return decorate


def safe_status(row):
    """Public connection state, never an arbitrary metadata/settings projection."""
    row = row or {}
    return {'provider': str(row.get('provider') or ''),
            'status': str(row.get('status') or 'not_connected'),
            'connected_at': str(row.get('connected_at') or '') or None,
            'updated_at': str(row.get('updated_at') or '') or None}


def project_status(cur, current, rows):
    """Scope-aware bootstrap projection; non-managers see only connection state."""
    resource = owner_resource(cur, current)
    by_provider = {row['provider']: row for row in rows}
    for provider in sorted(PROVIDERS - {'kommo_manual'}):
        by_provider.setdefault(provider, {'provider': provider, 'status': 'not_connected'})
    result = []
    for provider, row in by_provider.items():
        if provider == 'beinsystems_operator':
            from tenant_beinsystems_operator_access import is_operator_workspace
            if not is_operator_workspace(current.get('tenant_id')):
                continue
        actions = {action: allowed(current, resource, provider, action)
                   for action in ('view', 'create', 'update', 'delete')}
        # Even managers never receive arbitrary metadata from bootstrap. Each
        # provider's guarded settings endpoint owns its explicit safe projection.
        result.append({**safe_status(row), 'management_actions': actions,
                       'status_only': not actions['view']})
    return sorted(result, key=lambda row: row['provider'])
