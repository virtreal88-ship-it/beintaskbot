"""EMP01: actual cookie/API/PostgreSQL writes, conflicts and authority fences."""
import asyncio
from collections import Counter
import json
import os
import socket
import subprocess
import threading
import time
import uuid

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import psycopg
import pytest

from test_company_employees_navigation_postgres import (
    A, B, COOKIE_NAME, ORIGIN, ROOT, LOGGER, database, db, staff, _apis, _snapshot,
)
import tenant_member_store as members
from tenant_email_auth import _hash_password, _password_matches


INITIAL_PASSWORD = 'Synthetic-initial-password!42'
LOCAL_FIELDS = {'display_name', 'role', 'permissions', 'active', 'whatsapp_numbers', 'permission_changes', 'work_settings', 'access_settings', 'department_id'}
GLOBAL_FIELDS = {'first_name', 'last_name', 'login', 'email', 'phone', 'avatar_url', 'account_status'}
ALLOWED_ACTIONS = {'update_member_card', 'provision_member_account', 'upsert', 'deactivate',
                   'whatsapp_numbers', 'add_whatsapp_number', 'provision_account',
                   'assign_member_email', 'assign_member_password', 'assign_member_profile'}


@pytest.fixture
def cards(staff):
    f = staff
    with f['factory']() as conn:
        for tenant, actor, role, active, permissions, name in (
            (A, 61, 'worker', True, ['tasks', 'support', 'finance', 'legacy:retired'], 'A card alias'),
            (B, 61, 'worker', True, [], 'B card alias'),
            (A, 62, 'worker', True, [], 'A simple worker'),
            (B, 62, 'worker', True, [], 'B simple worker'),
            (A, 63, 'admin', False, ['employees:manage'], 'A inactive administrator'),
            (A, 64, 'owner', False, ['employees'], 'A inactive owner'),
            (A, 65, 'worker', True, [], 'A second simple worker'),
            (A, 66, 'worker', False, [], 'A inactive worker'),
        ):
            conn.execute('''INSERT INTO saas_tenant_members
              (tenant_id,telegram_id,role,active,permissions,display_name)
              VALUES(%s::uuid,%s,%s,%s,%s::jsonb,%s)''',
                         (tenant, actor, role, active, json.dumps(permissions), name))
        conn.execute('''INSERT INTO saas_auth_users
          (id,actor_id,tenant_id,email,first_name,last_name,login,phone,password_hash,verified_at,force_password_change)
          VALUES(%s,61,%s::uuid,'shared61@isolated.example','Global','Shared','global.shared61',
            '+994990000061',%s,now(),TRUE)
          ON CONFLICT(actor_id) DO UPDATE SET tenant_id=EXCLUDED.tenant_id,
            email=EXCLUDED.email,first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,
            login=EXCLUDED.login,phone=EXCLUDED.phone,password_hash=EXCLUDED.password_hash,
            status='active',disabled_at=NULL,verified_at=EXCLUDED.verified_at,
            force_password_change=EXCLUDED.force_password_change''',
                     (uuid.uuid4(), A, _hash_password(INITIAL_PASSWORD)))
        conn.execute('''INSERT INTO saas_user_profiles(actor_id,first_name,last_name,login,phone)
          VALUES(61,'Global','Shared','global.shared61','+994990000061')
          ON CONFLICT(actor_id) DO UPDATE SET first_name=EXCLUDED.first_name,
            last_name=EXCLUDED.last_name,login=EXCLUDED.login,phone=EXCLUDED.phone''')
        for tenant, actor, phone in ((A, 61, '994501230061'), (B, 61, '994501230061'),
                                    (A, 62, '994501230062'), (A, 65, '994501230065')):
            conn.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
              (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
              VALUES(%s::uuid,%s,%s,TRUE,%s)''', (tenant, actor, phone, 11 if tenant == A else 22))
    f['posts'] = []
    yield f
    assert f['blocked'] == []


def _app(f, origin=ORIGIN):
    @web.middleware
    async def isolate_mutations(request, handler):
        if request.method not in {'GET', 'HEAD'}:
            data = await request.json()
            if (request.method != 'POST' or request.path != '/api/platform/members'
                    or not isinstance(data, dict) or data.get('action') not in ALLOWED_ACTIONS):
                f['blocked'].append((request.method, request.path))
                return web.json_response({'success': False, 'error': 'Unexpected mutation'}, status=405)
            # Never keep a plaintext credential in the test request journal.
            f['posts'].append({key: value for key, value in data.items()
                               if key not in {'password', 'confirm_password'}})
        return await handler(request)
    app = web.Application(middlewares=[isolate_mutations])
    auth, employee = _apis(f, origin)
    async def employee_route(request):
        if f.get('before_member_http'):
            await f['before_member_http'](request)
        response = await employee['members'](request)
        if f.get('after_member_http'):
            await f['after_member_http'](request, response)
        return response
    app.router.add_get('/api/platform/me', auth['me'])
    app.router.add_get('/api/platform/members', employee_route)
    app.router.add_post('/api/platform/members', employee_route)
    return app


async def _list(http, cookie):
    response = await http.get('/api/platform/members', headers={'Cookie': COOKIE_NAME + '=' + cookie})
    data = await response.json()
    assert response.status == 200, data
    return data['members']


async def _card(http, cookie, actor):
    rows = await _list(http, cookie)
    return next(row for row in rows if int(row['telegram_id']) == actor)


async def _post(http, cookie, data, *, origin=ORIGIN):
    headers = {'Cookie': COOKIE_NAME + '=' + cookie}
    if origin is not None:
        headers['Origin'] = origin
    response = await http.post('/api/platform/members', headers=headers, json=data)
    try:
        payload = await response.json()
    except Exception:
        payload = {'unexpected_body': await response.text()}
    return response.status, payload


def _update(row, changes, *, actor=11, tenant=A):
    return {'action': 'update_member_card', 'telegram_id': row['telegram_id'],
            'expected_tenant_id': tenant, 'expected_user_id': actor,
            'expected_version': row['card_version'], 'changes': changes}


def _create(*, actor=11, tenant=A, login=None, email=None):
    username = login or 'emp01.' + uuid.uuid4().hex[:12]
    return {'action': 'provision_member_account', 'expected_tenant_id': tenant,
            'expected_user_id': actor, 'first_name': 'Synthetic', 'last_name': 'Employee',
            'login': username, 'email': email or username + '@isolated.example',
            'phone': '+994500001234', 'password': INITIAL_PASSWORD,
            'confirm_password': INITIAL_PASSWORD, 'role': 'worker', 'permissions': []}


def _globals(f):
    snapshot = _snapshot(f)
    return {key: snapshot[key] for key in ('saas_auth_users', 'saas_user_profiles')}


def _expected_member_change(snapshot, tenant, actor, **changes):
    result = {key: list(value) for key, value in snapshot.items()}
    rows = [json.loads(value) for value in result['saas_tenant_members']]
    selected = next(row for row in rows if row['tenant_id'] == tenant and int(row['telegram_id']) == actor)
    selected.update(changes)
    result['saas_tenant_members'] = sorted(json.dumps(row, sort_keys=True) for row in rows)
    return result


def _wait_blocked(f, blocker_pid, count=1):
    deadline = time.monotonic() + 8
    found = []
    while time.monotonic() < deadline:
        with f['factory']() as conn:
            found = conn.execute('''SELECT pid FROM pg_stat_activity
              WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))''', (blocker_pid,)).fetchall()
        if len(found) >= count:
            return
        time.sleep(.02)
    raise AssertionError('Actual PostgreSQL writers did not wait on the fixture lock: ' + repr(found))


def test_strict_creation_returns_applied_receipt_and_reopens_every_field_without_returning_password(cards):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            command = _create()
            before = _snapshot(f)
            status, result = await _post(http, cookie, command)
            assert status == 200 and result['success'] is True, result
            assert 'members' not in result and result['applied']['created'] is True
            actor = int(result['applied']['telegram_id'])
            row = await _card(http, cookie, actor)
            assert row['card_version'] == result['applied']['card_version'] == row['updated_at']
            for key in ('first_name', 'last_name', 'login', 'email', 'phone'):
                assert row[key] == command[key]
            assert row['display_name'] == 'Synthetic Employee' and row['role'] == 'worker'
            assert row['active'] is True and row['permissions'] == [] and row['whatsapp_numbers'] == []
            assert set(row['editable_fields']) <= LOCAL_FIELDS
            assert GLOBAL_FIELDS <= set(row['read_only_reasons'])
            serialized = json.dumps(result) + json.dumps(row)
            assert INITIAL_PASSWORD not in serialized and 'password_hash' not in serialized
            with f['factory']() as conn:
                account = conn.execute('SELECT password_hash,force_password_change FROM saas_auth_users WHERE actor_id=%s', (actor,)).fetchone()
                assert account['force_password_change'] is True
                assert account['password_hash'] != INITIAL_PASSWORD
                assert _password_matches(INITIAL_PASSWORD, account['password_hash'])
            after = _snapshot(f)
            for table in ('saas_auth_users', 'saas_user_profiles', 'saas_tenant_members', 'saas_tenant_audit_events'):
                assert len(after[table]) == len(before[table]) + 1
            for table in set(before) - {'saas_auth_users', 'saas_user_profiles', 'saas_tenant_members', 'saas_tenant_audit_events'}:
                assert after[table] == before[table], table
            assert INITIAL_PASSWORD not in ''.join(after['saas_tenant_audit_events'])
    asyncio.run(scenario())


def test_one_atomic_card_patch_preserves_global_identity_and_other_company_membership(cards):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, cookie, 61)
            before_globals = _globals(f)
            with f['factory']() as conn:
                foreign = conn.execute('SELECT to_jsonb(m) AS value FROM saas_tenant_members m WHERE tenant_id=%s::uuid AND telegram_id=61', (B,)).fetchone()['value']
            status, result = await _post(http, cookie, _update(row, {'display_name': 'A local edited alias',
                'whatsapp_numbers': [{'phone': '+994501230099', 'recognition_enabled': False}]}))
            assert status == 200 and 'members' not in result, result
            current = await _card(http, cookie, 61)
            assert current['card_version'] == result['applied']['card_version'] != row['card_version']
            assert current['display_name'] == 'A local edited alias'
            assert current['whatsapp_numbers'] == [{'phone': '+994501230099', 'recognition_enabled': False}]
            for field in ('role', 'permissions', 'active', *GLOBAL_FIELDS):
                assert current[field] == row[field], field
            with f['factory']() as conn:
                assert conn.execute('SELECT to_jsonb(m) AS value FROM saas_tenant_members m WHERE tenant_id=%s::uuid AND telegram_id=61', (B,)).fetchone()['value'] == foreign
                assert conn.execute('SELECT phone_digits FROM saas_tenant_member_whatsapp_numbers WHERE tenant_id=%s::uuid AND telegram_id=61', (B,)).fetchone()['phone_digits'] == '994501230061'
            assert _globals(f) == before_globals
    asyncio.run(scenario())


@pytest.mark.parametrize('actor,expected', [(11, 200), (31, 200), (33, 200), (34, 200), (32, 403), (35, 403), (36, 403)])
def test_card_mutation_uses_existing_current_manage_grant_without_settings_or_role_expansion(cards, actor, expected):
    f = cards
    owner_cookie = f['session'].issue(A, 11)
    cookie = f['session'].issue(A, actor)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, owner_cookie, 62)
            before = _snapshot(f)
            status, result = await _post(http, cookie, _update(row, {'display_name': 'Authorized local alias'}, actor=actor))
            assert status == expected, result
            if expected == 403:
                assert result['success'] is False and result['code']
                assert _snapshot(f) == before
            else:
                assert (await _card(http, owner_cookie, 62))['display_name'] == 'Authorized local alias'
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', ['missing_version', 'empty_version', 'empty_changes', 'unknown_change',
    'global_change', 'unknown_top_level', 'wrong_boolean', 'wrong_whatsapp_shape'])
def test_strict_card_protocol_rejects_missing_or_unrepresentable_data_without_side_effects(cards, invalid):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            command = _update(await _card(http, cookie, 62), {'display_name': 'Should not apply'})
            if invalid == 'missing_version': del command['expected_version']
            elif invalid == 'empty_version': command['expected_version'] = ''
            elif invalid == 'empty_changes': command['changes'] = {}
            elif invalid == 'unknown_change': command['changes']['unrelated'] = True
            elif invalid == 'global_change': command['changes']['email'] = 'forged@isolated.example'
            elif invalid == 'unknown_top_level': command['role'] = 'owner'
            elif invalid == 'wrong_boolean': command['changes'] = {'active': 'false'}
            else: command['changes'] = {'whatsapp_numbers': [{'phone': '+994501230099', 'recognition_enabled': 'true'}]}
            before = _snapshot(f)
            status, result = await _post(http, cookie, command)
            assert status == 400 and result['success'] is False and result['code'], result
            assert result['fields']
            assert _snapshot(f) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('mismatch', ['tenant', 'actor', 'changed_cookie', 'missing_identity', 'bad_origin'])
def test_current_company_identity_and_origin_cannot_be_bypassed_by_a_stale_card(cards, mismatch):
    f = cards
    cookie_a = f['session'].issue(A, 37)
    cookie_b = f['session'].issue(B, 37)
    owner_cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            command = _update(await _card(http, owner_cookie, 62), {'display_name': 'Stale A alias'}, actor=37)
            cookie, origin, expected = cookie_a, ORIGIN, 409
            if mismatch == 'tenant': command['expected_tenant_id'] = B
            elif mismatch == 'actor': command['expected_user_id'] = 11
            elif mismatch == 'changed_cookie': cookie = cookie_b
            elif mismatch == 'missing_identity': del command['expected_user_id']; expected = 400
            else: origin = 'https://foreign.example'; expected = 403
            before = _snapshot(f)
            status, result = await _post(http, cookie, command, origin=origin)
            assert status == expected and result['success'] is False, result
            assert _snapshot(f) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('target', [11, 38, 63, 64])
def test_nonowner_cannot_edit_protected_active_or_inactive_admin_owner_rows(cards, target):
    f = cards
    cookie = f['session'].issue(A, 31)
    owner_cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, owner_cookie, target)
            before = _snapshot(f)
            status, result = await _post(http, cookie, _update(row,
                {'display_name': 'Protected rewrite', 'role': 'worker', 'permissions': [], 'active': True}, actor=31))
            assert status == 403 and result['success'] is False, result
            assert _snapshot(f) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('actor,change', [(11, {'active': False}), (11, {'role': 'worker'}),
                                      (31, {'display_name': 'Self alias'}), (31, {'permissions': []})])
def test_self_membership_protections_remain_server_enforced(cards, actor, change):
    f = cards
    cookie = f['session'].issue(A, actor)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, cookie, actor)
            before = _snapshot(f)
            status, result = await _post(http, cookie, _update(row, change, actor=actor))
            assert status == 403, result
            assert _snapshot(f) == before
    asyncio.run(scenario())


def test_inactive_worker_is_an_existing_card_and_alias_edit_does_not_reactivate_it(cards):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, cookie, 66)
            status, result = await _post(http, cookie, _update(row, {'display_name': 'Inactive local alias'}))
            assert status == 200, result
            current = await _card(http, cookie, 66)
            assert current['active'] is False and current['display_name'] == 'Inactive local alias'
    asyncio.run(scenario())


def test_foreign_only_target_is_not_created_by_a_strict_edit(cards):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            command = _update(await _card(http, cookie, 62), {'display_name': 'Wrong target'})
            command['telegram_id'] = 42
            before = _snapshot(f)
            status, result = await _post(http, cookie, command)
            assert status == 404, result
            assert _snapshot(f) == before
    asyncio.run(scenario())


def test_two_actual_editors_with_one_card_version_cannot_overwrite_each_other(cards):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, cookie, 62)
            before_globals = _globals(f)
            before_audit = len(_snapshot(f)['saas_tenant_audit_events'])
            results = await asyncio.gather(*(_post(http, cookie, _update(row, {'display_name': value}))
                for value in ('First editor', 'Second editor')))
            assert sorted(status for status, _ in results) == [200, 409], results
            current = await _card(http, cookie, 62)
            winner = next(index for index, (status, _) in enumerate(results) if status == 200)
            assert current['display_name'] == ('First editor', 'Second editor')[winner]
            assert current['card_version'] != row['card_version']
            assert len(_snapshot(f)['saas_tenant_audit_events']) == before_audit + 1
            assert _globals(f) == before_globals
    asyncio.run(scenario())


@pytest.mark.parametrize('legacy', ['upsert', 'whatsapp_numbers', 'add_whatsapp_number', 'deactivate'])
def test_legacy_local_writers_invalidate_the_new_card_version(cards, legacy):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            original = await _card(http, cookie, 62)
            command = {'action': legacy, 'telegram_id': 62, 'expected_tenant_id': A, 'expected_user_id': 11}
            if legacy == 'upsert': command.update(display_name='Legacy edit', role='worker', permissions=[])
            elif legacy == 'whatsapp_numbers': command['numbers'] = [{'phone': '+994501230078', 'recognition_enabled': True}]
            elif legacy == 'add_whatsapp_number': command['phone'] = '+994501230079'
            status, result = await _post(http, cookie, command)
            assert status == 200, result
            assert (await _card(http, cookie, 62))['card_version'] != original['card_version']
            before = _snapshot(f)
            status, result = await _post(http, cookie, _update(original, {'display_name': 'Old form overwrite'}))
            assert status == 409, result
            assert _snapshot(f) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('lock_kind', ['actor', 'target', 'tenant'])
def test_actual_post_waits_then_rechecks_current_actor_and_raw_protected_target(cards, lock_kind):
    f = cards
    cookie = f['session'].issue(A, 31)
    owner_cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, owner_cookie, 62)
            before = _snapshot(f)
            with f['factory']() as holder:
                if lock_kind == 'tenant':
                    holder.execute('SELECT id FROM saas_tenants WHERE id=%s::uuid FOR UPDATE', (A,)).fetchone()
                else:
                    holder.execute('SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR UPDATE',
                                   (A, 31 if lock_kind == 'actor' else 62)).fetchone()
                pid = holder.info.backend_pid
                pending = asyncio.create_task(_post(http, cookie, _update(row, {'display_name': 'Blocked old authority'}, actor=31)))
                try:
                    await asyncio.to_thread(_wait_blocked, f, pid)
                    if lock_kind == 'target':
                        holder.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=62", (A,))
                        expected = _expected_member_change(before, A, 62, role='admin')
                    else:
                        holder.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=31", (A,))
                        expected = _expected_member_change(before, A, 31, permissions=[])
                    holder.commit()
                except BaseException:
                    holder.rollback()
                    await pending
                    raise
            status, result = await asyncio.wait_for(pending, 10)
            assert status == 403, result
            assert _snapshot(f) == expected
    asyncio.run(scenario())


def test_whatsapp_unique_index_race_rolls_back_the_losing_complete_card_patch(cards):
    f = cards
    cookie_31 = f['session'].issue(A, 31)
    cookie_38 = f['session'].issue(A, 38)
    owner_cookie = f['session'].issue(A, 11)
    phone = '994501239999'
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            rows = [await _card(http, owner_cookie, actor) for actor in (62, 65)]
            before_globals = _globals(f)
            with f['factory']() as holder:
                holder.execute('''INSERT INTO saas_tenant_member_whatsapp_numbers
                  (tenant_id,telegram_id,phone_digits,recognition_enabled,created_by)
                  VALUES(%s::uuid,61,%s,TRUE,11)''', (A, phone))
                pending = [asyncio.create_task(_post(http, cookie, _update(row, {
                    'display_name': 'Atomic winner ' + str(actor),
                    'whatsapp_numbers': [{'phone': '+' + phone, 'recognition_enabled': True}]}, actor=actor)))
                    for cookie, row, actor in zip((cookie_31, cookie_38), rows, (31, 38))]
                try:
                    await asyncio.to_thread(_wait_blocked, f, holder.info.backend_pid, 2)
                finally:
                    holder.rollback()
                results = await asyncio.gather(*pending)
            assert sorted(status for status, _ in results) == [200, 409], results
            loser = next(index for index, (status, _) in enumerate(results) if status == 409)
            assert 'whatsapp_numbers' in results[loser][1]['fields']
            current = await _card(http, owner_cookie, (62, 65)[loser])
            for key in ('display_name', 'card_version', 'whatsapp_numbers'):
                assert current[key] == rows[loser][key], key
            with f['factory']() as conn:
                assert conn.execute('SELECT count(*) AS n FROM saas_tenant_member_whatsapp_numbers WHERE tenant_id=%s::uuid AND phone_digits=%s', (A, phone)).fetchone()['n'] == 1
            assert _globals(f) == before_globals
    asyncio.run(scenario())


@pytest.mark.parametrize('original_role', ['worker', 'manager', 'admin', 'operator', 'master'])
def test_even_owner_cannot_forge_a_new_owner_role_through_the_card_protocol(cards, original_role):
    f = cards
    cookie = f['session'].issue(A, 11)
    with f['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET role=%s WHERE tenant_id=%s::uuid AND telegram_id=62', (original_role, A))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, cookie, 62)
            before = _snapshot(f)
            # Permissions are deliberately omitted: role alone must never
            # create another owner or replace the original permission set.
            status, result = await _post(http, cookie, _update(row, {'role': 'owner'}))
            assert status == 400 and result['fields'].get('role'), result
            assert _snapshot(f) == before
    asyncio.run(scenario())


def test_owner_may_change_own_local_alias_without_changing_owner_rights_or_identity(cards):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, cookie, 11)
            before_globals = _globals(f)
            status, result = await _post(http, cookie, _update(row, {'display_name': 'Local company owner alias'}))
            assert status == 200, result
            current = await _card(http, cookie, 11)
            assert current['display_name'] == 'Local company owner alias'
            for field in ('role', 'permissions', 'active'):
                assert current[field] == row[field]
            assert _globals(f) == before_globals
    asyncio.run(scenario())


@pytest.mark.parametrize('duplicate', ['login', 'email'])
def test_concurrent_provisioning_waits_on_real_unique_index_and_reports_the_losing_field(cards, duplicate):
    f = cards
    cookies = [f['session'].issue(A, actor) for actor in (31, 38)]
    suffix = uuid.uuid4().hex[:12]
    shared_login = 'race.' + suffix
    shared_email = 'race.' + suffix + '@isolated.example'
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            commands = [_create(actor=actor,
                login=shared_login if duplicate == 'login' else 'unique.' + suffix + str(actor),
                email=shared_email if duplicate == 'email' else 'unique.' + suffix + str(actor) + '@isolated.example')
                for actor in (31, 38)]
            before = _snapshot(f)
            with f['factory']() as holder:
                holder.execute('''INSERT INTO saas_auth_users(id,actor_id,tenant_id,login,email,password_hash)
                  VALUES(%s,90061,%s::uuid,%s,%s,'synthetic-uncommitted-reservation')''',
                    (uuid.uuid4(), A, shared_login if duplicate == 'login' else 'reserved.' + suffix,
                     shared_email if duplicate == 'email' else 'reserved.' + suffix + '@isolated.example'))
                pending = [asyncio.create_task(_post(http, cookie, command)) for cookie, command in zip(cookies, commands)]
                try:
                    await asyncio.to_thread(_wait_blocked, f, holder.info.backend_pid, 2)
                finally:
                    holder.rollback()
                results = await asyncio.gather(*pending)
            assert sorted(status for status, _ in results) == [200, 409], results
            loser = next(payload for status, payload in results if status == 409)
            assert loser['fields'].get(duplicate), loser
            assert INITIAL_PASSWORD not in json.dumps(results)
            after = _snapshot(f)
            for table in ('saas_auth_users', 'saas_user_profiles', 'saas_tenant_members', 'saas_tenant_audit_events'):
                assert len(after[table]) == len(before[table]) + 1, table
            for table in set(before) - {'saas_auth_users', 'saas_user_profiles', 'saas_tenant_members', 'saas_tenant_audit_events'}:
                assert after[table] == before[table], table
    asyncio.run(scenario())


def test_contact_phone_is_not_a_unique_login_or_implicit_whatsapp_binding(cards):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for _ in range(2):
                command = _create()
                command['phone'] = '+994501230061'  # Already another employee's explicit WA number.
                status, result = await _post(http, cookie, command)
                assert status == 200, result
                row = await _card(http, cookie, result['applied']['telegram_id'])
                assert row['phone'] == command['phone'] and row['whatsapp_numbers'] == []
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', ['missing_identity', 'wrong_tenant', 'wrong_actor', 'bad_origin',
                                   'login', 'login_symbols', 'login_type', 'email', 'password_mismatch'])
def test_strict_creation_validation_and_scope_errors_leave_no_account_or_membership(cards, invalid):
    f = cards
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            command = _create()
            expected, origin = 400, ORIGIN
            if invalid == 'missing_identity': del command['expected_user_id']
            elif invalid == 'wrong_tenant': command['expected_tenant_id'] = B; expected = 409
            elif invalid == 'wrong_actor': command['expected_user_id'] = 31; expected = 409
            elif invalid == 'bad_origin': origin = None; expected = 403
            elif invalid == 'login': command['login'] = '@@'
            elif invalid == 'login_symbols': command['login'] = 'bad!login'
            elif invalid == 'login_type': command['login'] = 12345
            elif invalid == 'email': command['email'] = 'invalid-email'
            else: command['confirm_password'] = 'Different-initial-password!42'
            before = _snapshot(f)
            status, result = await _post(http, cookie, command, origin=origin)
            assert status == expected and result['code'], result
            if invalid in {'login', 'login_symbols', 'login_type', 'email'}:
                assert result['fields'].get('email' if invalid == 'email' else 'login'), result
            assert _snapshot(f) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('identity', ['home_active', 'other_company', 'missing_auth', 'disabled'])
def test_password_link_availability_is_real_read_only_self_account_scope(cards, identity):
    f = cards
    actor = 37 if identity == 'missing_auth' else 61
    tenant = B if identity == 'other_company' else A
    with f['factory']() as conn:
        if actor == 61:
            conn.execute("UPDATE saas_tenant_members SET role='admin',permissions='[\"employees:manage\"]'::jsonb WHERE telegram_id=61 AND tenant_id=ANY(%s::uuid[])", ([A, B],))
        if identity == 'disabled':
            conn.execute('UPDATE saas_auth_users SET disabled_at=now() WHERE actor_id=61')
    cookie = f['session'].issue(tenant, actor)
    before = _snapshot(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            if identity == 'disabled':
                response = await http.get('/api/platform/members', headers={'Cookie': COOKIE_NAME + '=' + cookie})
                assert response.status == 403, 'Disabled accounts cannot use an old cookie'
                assert _snapshot(f) == before
                return
            rows = await _list(http, cookie)
            current = next(row for row in rows if row['telegram_id'] == actor)
            assert current['password_change_available'] is (identity == 'home_active')
            assert all(row['password_change_available'] is False for row in rows if row['telegram_id'] != actor)
            assert all(not ({'password_hash', 'auth_tenant_id', 'account_tenant_id', 'home_tenant_id'} & set(row)) for row in rows)
            assert _snapshot(f) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('active', [True, False])
def test_retained_grants_outside_actor_scope_are_read_only_without_revoking_legacy_wa_or_deactivation(cards, active):
    f = cards
    cookie = f['session'].issue(A, 31)
    owner_cookie = f['session'].issue(A, 11)
    with f['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET active=%s WHERE tenant_id=%s::uuid AND telegram_id=61', (active, A))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, cookie, 61)
            globals_before = _globals(f)
            for field in ('display_name', 'role', 'permissions'):
                assert field not in row['editable_fields']
                assert row['read_only_reasons'][field] == 'permission_scope'
            attempts = [{'display_name': 'Forbidden alias'}, {'role': 'manager'}, {'permissions': ['tasks']}]
            if not active:
                assert 'active' not in row['editable_fields']
                assert row['read_only_reasons']['active'] == 'permission_scope'
                attempts.append({'active': True})
            for changes in attempts:
                before = _snapshot(f)
                status, result = await _post(http, cookie, _update(row, changes, actor=31))
                assert status == 403, result
                assert _snapshot(f) == before
            if active:
                assert {'active', 'whatsapp_numbers'} <= set(row['editable_fields'])
                status, result = await _post(http, cookie, _update(row, {'whatsapp_numbers': [
                    {'phone': '+994501230061', 'recognition_enabled': False}]}, actor=31))
                assert status == 200, result
                current = await _card(http, cookie, 61)
                assert current['permissions'] == row['permissions'] and current['role'] == row['role']
                status, result = await _post(http, cookie, _update(current, {'active': False}, actor=31))
                assert status == 200, result
                assert (await _card(http, cookie, 61))['active'] is False
            current = await _card(http, owner_cookie, 61)
            status, result = await _post(http, owner_cookie, _update(current, {'display_name': 'Owner permitted alias'}))
            assert status == 200, result
            final = await _card(http, owner_cookie, 61)
            assert final['active'] is False and final['permissions'] == row['permissions']
            assert final['display_name'] == 'Owner permitted alias'
            assert _globals(f) == globals_before
    asyncio.run(scenario())


@pytest.mark.parametrize('active', [True, False])
def test_visible_grants_may_be_reduced_before_editing_otherwise_read_only_membership_fields(cards, active):
    f = cards
    cookie = f['session'].issue(A, 31)
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET active=%s,permissions='[\"support\"]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=62", (active, A))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            row = await _card(http, cookie, 62)
            globals_before = _globals(f)
            for field in ('display_name', 'role'):
                assert field not in row['editable_fields']
                assert row['read_only_reasons'][field] == 'permission_scope'
            assert 'permissions' in row['editable_fields'], 'Visible rights can still be explicitly reduced under existing policy'
            forbidden = [{'display_name': 'Forbidden alias'}, {'role': 'manager'}]
            if not active:
                assert 'active' not in row['editable_fields']
                assert row['read_only_reasons']['active'] == 'permission_scope'
                forbidden.append({'active': True})
            for changes in forbidden:
                before = _snapshot(f)
                status, result = await _post(http, cookie, _update(row, changes, actor=31))
                assert status == 403, result
                assert _snapshot(f) == before
            status, result = await _post(http, cookie, _update(row, {'permissions': []}, actor=31))
            assert status == 200, result
            reduced = await _card(http, cookie, 62)
            assert reduced['permissions'] == [] and reduced['active'] is active
            assert {'display_name', 'role', 'permissions', 'active'} <= set(reduced['editable_fields'])
            assert reduced['card_version'] != row['card_version']
            status, result = await _post(http, cookie, _update(reduced, {'display_name': 'Alias after explicit rights reduction'}, actor=31))
            assert status == 200, result
            current = await _card(http, cookie, 62)
            assert current['display_name'] == 'Alias after explicit rights reduction' and current['active'] is active
            if not active:
                status, result = await _post(http, cookie, _update(current, {'active': True}, actor=31))
                assert status == 200, result
                assert (await _card(http, cookie, 62))['active'] is True
            assert _globals(f) == globals_before
    asyncio.run(scenario())


@pytest.mark.parametrize('mode,width,language', [
    ('complete', 360, 'ru'), ('complete', 390, 'az'), ('complete', 768, 'en'), ('complete', 1440, 'ru'),
    ('conflict', 390, 'ru'), ('revoked', 390, 'ru'),
    ('switch_after_write', 390, 'en'), ('postwrite_read_denied', 390, 'ru'),
])
def test_actual_app_employee_card_crud_uses_real_scoped_postgres_http(cards, mode, width, language):
    from tenant_web_assets import register_assets
    f = cards
    assert (ROOT / '.support-test-deps/node_modules/playwright').exists(), 'Required actual Chromium bridge is missing'
    actor = 37 if mode == 'switch_after_write' else 38 if mode in {'revoked', 'postwrite_read_denied'} else 11
    tokens = {name: f['session'].issue(tenant, identity) for name, tenant, identity in (
        ('initial', A, actor), ('editor', A, 38), ('company_b', B, 37), ('granular', A, 62))}
    before = _snapshot(f)
    state = {'pending_write': False, 'revoked': False, 'post_results': []}
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    loop = asyncio.new_event_loop()
    started = threading.Event()

    async def start():
        state['release'] = asyncio.Event()

        async def before_member_http(request):
            if request.method == 'POST' and mode == 'revoked':
                state['pending_write'] = True
                await state['release'].wait()

        async def after_member_http(request, response):
            if request.method != 'POST':
                return
            result = json.loads(response.text)
            state['post_results'].append({'status': response.status, 'result': result})
            if response.status == 200 and mode == 'switch_after_write':
                # The real handler has already committed. Hold only delivery
                # of its actual receipt while another company opens a form.
                state['pending_write'] = True
                await state['release'].wait()
            elif response.status == 200 and mode == 'postwrite_read_denied':
                with f['factory']() as conn:
                    conn.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=%s", (A, actor))
                state['revoked'] = True

        f['before_member_http'], f['after_member_http'] = before_member_http, after_member_http
        app = _app(f, origin)
        auth, _ = _apis(f, origin)

        async def index(_request):
            return web.FileResponse(ROOT / 'docs/tenant-app.html')

        async def select_session(request):
            name = request.query.get('identity', 'initial')
            if name not in tokens:
                raise web.HTTPBadRequest()
            response = web.json_response({'success': True})
            response.set_cookie(COOKIE_NAME, tokens[name], httponly=True, samesite='Lax', path='/')
            return response

        async def control_state(_request):
            return web.json_response({'pending_write': state['pending_write'], 'revoked': state['revoked'],
                                      'posts': len(f['posts']), 'results': len(state['post_results'])})

        async def release(_request):
            state['release'].set()
            return web.json_response({'success': True})

        async def revoke(_request):
            if mode != 'revoked' or not state['pending_write']:
                raise web.HTTPConflict()
            with f['factory']() as conn:
                conn.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=%s", (A, actor))
            state['revoked'] = True
            return web.json_response({'success': True})

        async def unrelated_read(request):
            if any(part in request.path for part in ('/platform/me', '/platform/members')):
                raise web.HTTPNotFound()
            if request.path == '/api/platform/ai/dialogs':
                return web.json_response({'success': True, 'result': {'dialogs': [], 'channels': [],
                    'agents': [], 'next_offset': None, 'total_count': 0, 'unread_count': 0}})
            return web.json_response({'success': True, 'result': {}, 'tasks': [], 'deals': [],
                'contacts': [], 'companies': [], 'pipelines': [], 'members': [], 'integrations': [],
                'notifications': [], 'unread_count': 0, 'requests': []})

        app.router.add_get('/app', index)
        register_assets(app)
        app.router.add_get('/api/platform/account', auth['account'])
        app.router.add_get('/api/platform/sessions', auth['sessions'])
        app.router.add_get('/test/session', select_session)
        app.router.add_get('/test/state', control_state)
        app.router.add_get('/test/release', release)
        app.router.add_get('/test/revoke', revoke)
        app.router.add_get('/api/{tail:.*}', unrelated_read)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.SockSite(runner, sock).start()
        state['runner'] = runner
        started.set()

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert started.wait(10)
    try:
        env = {**os.environ, 'EMPLOYEE_CARD_RUNTIME_ORIGIN': origin, 'EMPLOYEE_CARD_RUNTIME_MODE': mode,
               'EMPLOYEE_CARD_RUNTIME_WIDTH': str(width), 'EMPLOYEE_CARD_RUNTIME_LANGUAGE': language,
               'EMPLOYEE_CARD_RUNTIME_TENANT_A': A, 'EMPLOYEE_CARD_RUNTIME_TENANT_B': B,
               'EMPLOYEE_CARD_RUNTIME_LOGIN': 'card.' + uuid.uuid4().hex[:12]}
        completed = subprocess.run(['node', str(ROOT / 'tests/employee_card_http_browser.cjs')],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=70)
        assert completed.returncode == 0, completed.stdout + '\n' + completed.stderr
        assert f['blocked'] == []
        after = _snapshot(f)
        assert INITIAL_PASSWORD not in ''.join(after['saas_tenant_audit_events'])
        if mode == 'complete':
            for table in ('saas_auth_users', 'saas_user_profiles', 'saas_tenant_members'):
                assert len(after[table]) == len(before[table]) + 1
                if table != 'saas_tenant_members':
                    assert set(before[table]) <= set(after[table]), table
                else:
                    old_rows = [json.loads(item) for item in before[table]]
                    new_rows = [json.loads(item) for item in after[table]]
                    for old in old_rows:
                        new = next(item for item in new_rows if item['tenant_id'] == old['tenant_id']
                                   and item['telegram_id'] == old['telegram_id'])
                        if old['tenant_id'] == A and old['telegram_id'] == 62:
                            assert new['permissions'] == old['permissions'] == []
                            assert {k:v for k,v in new.items() if k != 'updated_at'} == {k:v for k,v in old.items() if k != 'updated_at'}
                            assert new['updated_at'] != old['updated_at']
                        else:
                            assert new == old
            # The removed page-preference save no longer creates an eighth
            # event. Assert the actual commands instead of an obsolete count.
            new_audits = set(after['saas_tenant_audit_events']) - set(before['saas_tenant_audit_events'])
            assert Counter(json.loads(item)['action'] for item in new_audits) == {
                'employee.provisioned': 1, 'employee.email.assigned': 1,
                'employee.password.assigned': 1, 'employee.card.updated': 4,
            }
            assert len(after['saas_tenant_member_whatsapp_numbers']) == len(before['saas_tenant_member_whatsapp_numbers']) + 1
            unchanged = set(before) - {'saas_auth_users', 'saas_user_profiles', 'saas_tenant_members',
                'saas_tenant_audit_events', 'saas_tenant_member_whatsapp_numbers', 'saas_tenant_workflow_policies'}
        else:
            assert _globals(f) == {table: before[table] for table in ('saas_auth_users', 'saas_user_profiles')}
            unchanged = set(before) - {'saas_tenant_members', 'saas_tenant_audit_events'}
            expected_audits = 2 if mode == 'conflict' else 0 if mode == 'revoked' else 1
            assert len(after['saas_tenant_audit_events']) == len(before['saas_tenant_audit_events']) + expected_audits
            with f['factory']() as conn:
                actual = conn.execute('SELECT display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=62', (A,)).fetchone()['display_name']
                assert actual == ('Reviewed final alias' if mode == 'conflict' else 'A simple worker' if mode == 'revoked' else 'Applied A alias')
                assert conn.execute('SELECT display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=62', (B,)).fetchone()['display_name'] == 'B simple worker'
            if mode == 'revoked':
                assert after == _expected_member_change(before, A, actor, permissions=[])
        for table in unchanged:
            assert after[table] == before[table], table
    finally:
        loop.call_soon_threadsafe(state['release'].set)
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()
        assert not thread.is_alive()


def test_managed_account_changes_use_real_signed_cookie_http_and_postgres(cards, monkeypatch):
    import tenant_email_auth as email_auth
    f=cards
    monkeypatch.setattr(email_auth, '_connect', f['factory'])
    monkeypatch.setattr(email_auth, '_send_mail', lambda *a, **k: pytest.fail('No mail allowed'))
    cookie=f['session'].issue(A,11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            command=_create()
            status,created=await _post(http,cookie,command)
            assert status==200,created
            actor=created['applied']['telegram_id']
            target_cookie=f['session'].issue(A,actor)
            row=await _card(http,cookie,actor)
            assert row['email_assign_available'] and row['password_assign_available'] and row['profile_assign_available']
            changed_email='changed.'+command['email']
            base={'expected_tenant_id':A,'expected_user_id':11,'telegram_id':actor}
            status,result=await _post(http,cookie,{**base,'action':'assign_member_email','expected_version':row['card_version'],'email':changed_email})
            assert status==200 and result['applied']['email_changed'],result
            assert f['session'].profile(type('R',(),{'cookies':{COOKIE_NAME:target_cookie}})()) is None
            row=await _card(http,cookie,actor)
            assert row['email']==changed_email
            new_password='New-synthetic-password-93!'
            status,result=await _post(http,cookie,{**base,'action':'assign_member_password','expected_version':row['card_version'],'password':new_password,'confirm_password':new_password})
            assert status==200 and result['applied']['password_changed'],result
            assert email_auth.login(email=changed_email,password=INITIAL_PASSWORD,request_ip='127.0.0.1') is None
            credentials=email_auth.login(email=changed_email,password=new_password,request_ip='127.0.0.1')
            assert credentials['actor_id']==actor
            row=await _card(http,cookie,actor)
            status,result=await _post(http,cookie,{**base,'action':'assign_member_profile','expected_version':row['card_version'],'changes':{'first_name':'Updated','login':'changed.'+command['login']}})
            assert status==200 and result['applied']['profile_changed'],result
            assert (await _card(http,cookie,actor))['first_name']=='Updated'
            import tenant_account_store as account_store
            from tenant_platform_core import TenantPlatformError
            with pytest.raises(TenantPlatformError):
                account_store.create_session(session_id=str(uuid.uuid4()),tenant_id=A,actor_id=actor,expires_at=9999999999,credential_fingerprint=credentials['_credential_fingerprint'])
    asyncio.run(scenario())
