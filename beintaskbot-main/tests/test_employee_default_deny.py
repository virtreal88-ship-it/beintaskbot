import copy
import json

import pytest

from tenant_policy import TenantPolicy
from tenant_member_access import page_capabilities, access_snapshot
from tenant_employee_defaults import published_section_allowed
from tenant_scoped_access import allows_assigned_task_exception
from tenant_shared_integration_access import allowed
from tenant_scoped_access_store import read_access_profile, resource_scope_sql
import tenant_employee_defaults_store as migration
from tenant_member_store import MemberCardError
from test_tenant_support_inbox import A, B, db, database


def profile(role='admin', enabled=True, scopes=None):
    settings = {'access_scopes': scopes or {}}
    return {'tenant_id': A, 'telegram_id': 33, 'active': True, 'role': role,
            'tenant_status': 'active', 'permissions': [], 'modules': {'tasks': True},
            'workflow': {'policies': {'employee_access_defaults': {'deny_unset': enabled},
                                      'members': {'33': settings}}}}


def test_unset_scopes_are_denied_server_side_and_owner_is_untouched(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    p = profile()
    caps = page_capabilities(p)
    assert {k for k, v in caps.items() if v} == {'tasks', 'profile'}
    policy = TenantPolicy(p)
    for section in ('dialogs', 'support', 'integrations', 'finance', 'employees', 'ai_agents', 'news', 'instructions'):
        assert policy.action_scope(section, 'view') == 'none'
        assert not policy.allows_action(section, 'view')
    for provider in ('connect', 'chat_ai', 'linear', 'kommo'):
        assert not allowed(p, {'tenant_id': A, 'responsible_member_id': 11}, provider, 'view')
    assert resource_scope_sql(p, 'customers', 'view') == ('FALSE', [])
    assert all(page_capabilities(profile(role='owner')).values())
    # Deliberate executor exception remains read/complete only, not CRUD.
    actor = {'tenant_id': A, 'member_id': 33, 'active': True}
    task = {'tenant_id': A, 'assignee_member_id': 33}
    assert allows_assigned_task_exception(actor, task, 'read')
    assert allows_assigned_task_exception(actor, task, 'complete')
    assert not allows_assigned_task_exception(actor, task, 'update')


def test_new_scopes_are_preserved_and_other_tenants_unchanged(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    p = profile(scopes={'dialogs': {'view': 'self', 'update': 'self'}, 'news': {'view': 'all'}})
    assert TenantPolicy(p).action_scope('dialogs', 'view') == 'self'
    assert TenantPolicy(p).action_scope('dialogs', 'delete') is None
    assert page_capabilities(p)['news']
    assert not page_capabilities(p)['instructions']
    assert published_section_allowed(TenantPolicy(profile(enabled=False)), 'instructions')
    assert not published_section_allowed(TenantPolicy(profile()), 'instructions')
    old = access_snapshot(profile(enabled=False))
    new = access_snapshot(profile())
    assert old['version'] != new['version']
    assert new['default_deny'] is True


@pytest.fixture
def setup(db, monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    monkeypatch.setattr(migration, '_connect', lambda: db)
    for tenant in (A, B):
        db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions) VALUES(%s::uuid,33,'admin','[\"dialogs\",\"linear\"]')", (tenant,))
    settings = {'33': {'ai_agents_manage': True, 'native_crm_actions': {'tasks': {'view': True}},
                       'access_scopes': {'dialogs': {'view': 'self', 'update': 'self'}},
                       'notifications': {'unchanged': True}}}
    db.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'members',%s::jsonb,11)", (A, json.dumps(settings)))
    db.execute("INSERT INTO saas_tenant_invites(id,tenant_id,token_hash,display_name,role,permissions,created_by,expires_at) VALUES(gen_random_uuid(),%s::uuid,'synthetic','Invite','manager','[\"linear\"]',11,now()+interval '1 day')", (A,))
    db.commit()
    return db


def test_transition_preview_digest_preserves_owner_and_explicit_scopes(setup):
    db = setup
    before = db.execute('SELECT * FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=11', (A,))[0]
    preview = migration.transition(tenant_id=A, owner_id=11)
    assert preview['employee_count'] == 1 and preview['pending_invite_count'] == 1
    assert preview['applied'] is False
    with pytest.raises(MemberCardError, match='conflict'):
        migration.transition(tenant_id=A, owner_id=11, expected_digest='wrong')
    assert migration.transition(tenant_id=A, owner_id=11, expected_digest=preview['digest'])['applied']
    with db.cursor() as cur:
        employee = read_access_profile(cur, A, 33, lock=True)
        other = read_access_profile(cur, B, 33, lock=True)
    assert employee['permissions'] == []
    settings = TenantPolicy(employee).member_settings
    assert settings['access_scopes'] == {'dialogs': {'view': 'self', 'update': 'self'}}
    assert settings['notifications'] == {'unchanged': True}
    assert 'native_crm_actions' not in settings and 'ai_agents_manage' not in settings
    assert other['permissions'] == ['dialogs', 'linear']
    assert db.execute('SELECT * FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=11', (A,))[0] == before
    assert db.execute('SELECT permissions FROM saas_tenant_invites WHERE tenant_id=%s::uuid', (A,))[0]['permissions'] == []
    assert len(db.execute("SELECT id FROM saas_tenant_audit_events WHERE action='employee.default_deny.enabled' AND tenant_id=%s::uuid", (A,))) == 1
    # Re-run does not erase owner grants added after the transition.
    db.execute("UPDATE saas_tenant_members SET permissions='[\"linear\"]' WHERE tenant_id=%s::uuid AND telegram_id=33", (A,)); db.commit()
    again = migration.transition(tenant_id=A, owner_id=11, expected_digest=preview['digest'])
    assert again['already_applied'] and not again['applied']
    assert db.execute('SELECT permissions FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=33', (A,))[0]['permissions'] == ['linear']


def test_transition_rejects_nonowner_and_stale_preview(setup):
    db = setup
    with pytest.raises(MemberCardError):
        migration.transition(tenant_id=A, owner_id=33)
    with pytest.raises(MemberCardError):
        migration.transition(tenant_id=B, owner_id=11)
    preview = migration.transition(tenant_id=A, owner_id=11)
    db.execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=33", (A,)); db.commit()
    with pytest.raises(MemberCardError, match='conflict'):
        migration.transition(tenant_id=A, owner_id=11, expected_digest=preview['digest'])


def test_runtime_refuses_legacy_fallback_after_activation(setup, monkeypatch):
    from tenant_employee_defaults import require_compatible_runtime
    preview = migration.transition(tenant_id=A, owner_id=11)
    migration.transition(tenant_id=A, owner_id=11, expected_digest=preview['digest'])
    require_compatible_runtime(setup)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    with pytest.raises(RuntimeError, match='requires_scoped_access_engine'):
        require_compatible_runtime(setup)


def test_runtime_allows_existing_tenants_without_activation(setup, monkeypatch):
    from tenant_employee_defaults import require_compatible_runtime
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    require_compatible_runtime(setup)


def test_transition_clears_expired_unaccepted_invite_grants(setup):
    setup.execute("UPDATE saas_tenant_invites SET expires_at=now()-interval '1 day' WHERE tenant_id=%s::uuid", (A,))
    setup.commit()
    preview = migration.transition(tenant_id=A, owner_id=11)
    assert preview['pending_invite_count'] == 1
    migration.transition(tenant_id=A, owner_id=11, expected_digest=preview['digest'])
    assert setup.execute('SELECT permissions FROM saas_tenant_invites WHERE tenant_id=%s::uuid', (A,))[0]['permissions'] == []
