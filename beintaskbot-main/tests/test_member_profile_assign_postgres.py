"""Owner profile edits against disposable synthetic PostgreSQL only."""
import pytest

import tenant_member_profile_assign as profile
from tenant_member_store import MemberCardError
from test_member_email_assign_postgres import fixture, query, command, snapshot, A, B


@pytest.fixture
def data(fixture, monkeypatch):
    monkeypatch.setattr(profile, '_connect', fixture)
    return fixture


def edit(f, changes):
    args = command(f)
    del args['email']
    args['changes'] = changes
    return args


def test_managed_profile_updates_both_stores_only_target(data):
    f = data
    foreign = query(f, 'SELECT * FROM saas_auth_users WHERE actor_id=42')
    before = query(f, 'SELECT * FROM saas_auth_users WHERE actor_id=41')[0]
    args = edit(f, {'first_name': '  New   First ', 'last_name': 'Surname',
        'phone': '+994 (50) 123-45-67', 'avatar_url': 'https://example.test/avatar.png'})
    result = profile.assign_member_profile(**args)
    updated = query(f, 'SELECT * FROM saas_auth_users WHERE actor_id=41')[0]
    view = query(f, 'SELECT * FROM saas_user_profiles WHERE actor_id=41')[0]
    for field, value in {'first_name': 'New First', 'last_name': 'Surname',
                        'phone': '+994501234567', 'avatar_url': 'https://example.test/avatar.png'}.items():
        assert updated[field] == view[field] == value
    assert updated['display_name'] == 'New First Surname'
    assert updated['password_hash'] == before['password_hash']
    assert updated['email'] == before['email']
    assert updated['login'] == before['login']
    assert not updated['legacy_sessions_revoked']
    assert all(not row['revoked_at'] for row in query(f, 'SELECT revoked_at FROM saas_tenant_browser_sessions'))
    assert query(f, 'SELECT * FROM saas_auth_users WHERE actor_id=42') == foreign
    assert result['card_version'] != args['expected_version']
    assert result['profile_changed'] is True
    audit = query(f, "SELECT payload FROM saas_tenant_audit_events WHERE action='employee.profile.assigned'")
    assert audit == [{'payload': {'fields': ['avatar_url', 'first_name', 'last_name', 'phone']}}]


def test_login_change_revokes_target_only_and_stale_command_is_blocked(data):
    args = edit(data, {'login': 'NEW.LOGIN'})
    result = profile.assign_member_profile(**args)
    assert result['profile_changed'] is True
    assert query(data, 'SELECT login FROM saas_auth_users WHERE actor_id=41')[0]['login'] == 'new.login'
    assert all(bool(row['revoked_at']) == (row['actor_id'] == 41)
        for row in query(data, 'SELECT actor_id,revoked_at FROM saas_tenant_browser_sessions'))
    assert all(row['used_at'] for row in query(data, 'SELECT used_at FROM saas_auth_tokens'))
    before = snapshot(data)
    with pytest.raises(MemberCardError) as error:
        profile.assign_member_profile(**args)
    assert error.value.code == 'member_card_conflict'
    assert snapshot(data) == before


@pytest.mark.parametrize('changes', [None, {}, {'email': 'other@example.test'},
    {'first_name': ''}, {'first_name': 'x'*121}, {'phone': 'abc12345678'},
    {'phone': '++994501234567'}, {'login': 'a b'}, {'login': 'xx'},
    {'avatar_url': 'javascript:alert(1)'}, {'avatar_url': 'https://user:password@example.test/a'},
    {'last_name': None}])
def test_invalid_changes_are_rejected_without_writes(data, changes):
    before = snapshot(data)
    with pytest.raises(MemberCardError):
        profile.assign_member_profile(**edit(data, changes))
    assert snapshot(data) == before


@pytest.mark.parametrize('mutation', [
    "DELETE FROM saas_tenant_audit_events WHERE entity_id='41'",
    "UPDATE saas_tenant_members SET active=FALSE WHERE telegram_id=41",
    "UPDATE saas_auth_users SET tenant_id='" + B + "' WHERE actor_id=41",
    "INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,active) VALUES('" + B + "',41,'worker',FALSE)",
    "UPDATE saas_tenant_members SET role='admin' WHERE telegram_id=11",
])
def test_wrong_ownership_cannot_edit_profile(data, mutation):
    query(data, mutation)
    before = snapshot(data)
    with pytest.raises(MemberCardError):
        profile.assign_member_profile(**edit(data, {'first_name': 'No write'}))
    assert snapshot(data) == before


def test_duplicate_login_preserves_entire_profile(data):
    before = snapshot(data)
    with pytest.raises(MemberCardError) as error:
        profile.assign_member_profile(**edit(data, {'first_name': 'No write', 'login': 'EMPLOYEE42'}))
    assert error.value.fields == {'login': 'duplicate'}
    assert snapshot(data) == before


def test_sparse_edit_keeps_unedited_fields_and_company_alias(data):
    query(data, "UPDATE saas_auth_users SET first_name='First',last_name='Last',phone='+994501234567' WHERE actor_id=41")
    alias = query(data, 'SELECT display_name FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=41', (A,))
    profile.assign_member_profile(**edit(data, {'last_name': ''}))
    assert query(data, 'SELECT first_name,last_name,phone,display_name FROM saas_auth_users WHERE actor_id=41') == [
        {'first_name': 'First', 'last_name': '', 'phone': '+994501234567', 'display_name': 'First'}]
    assert query(data, 'SELECT display_name FROM saas_tenant_members WHERE tenant_id=%s AND telegram_id=41', (A,)) == alias
