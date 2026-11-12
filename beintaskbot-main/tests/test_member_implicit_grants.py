"""Account/invitation normalization must not turn a role into a permission."""
import ast
from pathlib import Path

import pytest

from tenant_platform_common import _permissions
from tenant_policy import ROLE_PERMISSIONS, TenantPolicy


@pytest.mark.parametrize('role', ['admin', 'manager', 'operator', 'worker', 'master', 'unknown'])
@pytest.mark.parametrize('value', [None, {}, '', True])
def test_missing_or_malformed_grants_do_not_grant_role_defaults(role, value):
    assert _permissions(value, role, deny_unset=True) == []


def test_explicit_grants_and_owner_authority_are_preserved():
    assert _permissions(['tasks:view', 'dialogs', 'tasks:view'], 'manager') == ['tasks:view', 'dialogs']
    assert _permissions([], 'operator') == []
    assert _permissions(None, 'owner') == ROLE_PERMISSIONS['owner']
    assert _permissions([], 'owner') == ROLE_PERMISSIONS['owner']


def test_existing_member_omission_preserves_recorded_grants():
    # Isolate the pure selection function from database/bootstrap imports.
    source = Path(__file__).parents[1] / 'tenant_member_store.py'
    tree = ast.parse(source.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_selected_membership')
    env = {'TenantPolicy': TenantPolicy, '_permissions': _permissions, '_role': lambda value, owner=False: 'owner' if owner else value,
           '_EDITOR_PERMISSION_KEYS': {'tasks', 'dialogs'}, '_RECOGNIZED_ROLES': {'manager'}}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), env)
    current = {'role': 'owner', 'telegram_id': 1, 'workflow': {'policies': {'employee_access_defaults': {'deny_unset': True}}}}
    existing = {'role': 'manager', 'permissions': ['dialogs', 'tasks', 'custom:retained']}
    choose = env['_selected_membership']
    assert choose(current, existing, 'manager', None, target=2)[1] == existing['permissions']
    assert choose(current, existing, 'manager', [], target=2)[1] == ['custom:retained']
    assert choose(current, None, 'manager', None, target=2)[1] == []


def test_nonactivated_tenants_retain_creation_defaults():
    for role, grants in ROLE_PERMISSIONS.items():
        assert _permissions(None, role) == grants
