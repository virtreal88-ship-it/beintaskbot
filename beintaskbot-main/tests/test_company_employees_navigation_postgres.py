"""NAV01 uses current cookie membership and the existing employee authority."""
import asyncio
import importlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import threading
from unittest.mock import AsyncMock, Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import psycopg
from psycopg.rows import dict_row
import pytest
import requests

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A, B, db
import tenant_account_store as accounts
import tenant_auth_api as auth_api
from tenant_browser_session import COOKIE_NAME, TTL_SECONDS, TenantBrowserSession
import tenant_integration_store as integrations
import tenant_invite_store as invites
import tenant_member_store as members
from tenant_members_api import MemberApiContext, handlers as member_handlers
from tenant_platform_core import TenantPlatformError
from tenant_policy import ROLE_PERMISSIONS, TenantPolicy
import tenant_push


ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)
ORIGIN = 'https://synthetic-company-employees.example'
SESSION_SECRET = 'isolated-nav01-cookie-signing-secret'
A_SENTINEL = 'A-PRIVATE-EMPLOYEE'
B_SENTINEL = 'B-PRIVATE-EMPLOYEE'
STATE_TABLES = (
    'saas_tenants', 'saas_tenant_members', 'saas_tenant_workflow_policies',
    'saas_user_profiles', 'saas_auth_users', 'saas_tenant_browser_sessions',
    'saas_tenant_member_whatsapp_numbers', 'saas_tenant_audit_events',
    'saas_tenant_invites', 'saas_tenant_invite_requests', 'saas_tenant_notifications',
    'saas_ai_agent_jobs', 'saas_ai_agent_runs', 'saas_support_tickets',
    'saas_internal_consultations', 'saas_dialog_consultation_replies',
)


@pytest.fixture
def staff(db, monkeypatch):
    factory = lambda: psycopg.connect(db.dsn, row_factory=dict_row)
    for name in ('tenant_platform', 'tenant_platform_core', 'tenant_member_store',
                 'tenant_workflow_store', 'tenant_account_store', 'tenant_integration_store',
                 'tenant_invite_store', 'tenant_push', 'tenant_crm_native_tasks'):
        module = importlib.import_module(name)
        if hasattr(module, '_connect'):
            monkeypatch.setattr(module, '_connect', factory)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://invalid.invalid/disabled')
    forbidden = Mock(side_effect=AssertionError('Unexpected external provider or transport'))
    async_forbidden = AsyncMock(side_effect=AssertionError('Unexpected async external provider'))
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    if importlib.util.find_spec('httpx'):
        import httpx
        monkeypatch.setattr(httpx.Client, 'request', forbidden)
        monkeypatch.setattr(httpx.AsyncClient, 'request', async_forbidden)
    rows = (
        (A, 31, 'admin', ['employees:manage'], 'A management without Settings'),
        (A, 32, 'operator', ['tasks'], 'A ordinary operator'),
        (A, 33, 'operator', ['employees:manage'], 'A explicitly authorized operator'),
        (A, 34, 'master', ['employees:manage'], 'A explicitly authorized master'),
        (A, 35, 'admin', ['employees', 'employees:view'], 'A employee viewer'),
        (A, 36, 'admin', ['settings'], 'A settings without employee management'),
        (A, 37, 'admin', ['employees:manage'], 'A shared account'),
        (B, 37, 'admin', ['employees:manage'], 'B shared account'),
        (A, 38, 'admin', ['employees:manage', 'tasks'], 'A task manager without Settings'),
        (A, 41, 'worker', ['tasks'], A_SENTINEL),
        (B, 42, 'worker', ['tasks'], B_SENTINEL),
    )
    for tenant, actor, role, permissions, name in rows:
        db.execute('''INSERT INTO saas_tenant_members
          (tenant_id,telegram_id,role,permissions,display_name)
          VALUES(%s::uuid,%s,%s,%s::jsonb,%s)''',
                   (tenant, actor, role, json.dumps(permissions), name))
    for actor, suffix in ((41, 'a'), (42, 'b')):
        db.execute('''INSERT INTO saas_user_profiles(actor_id,first_name,last_name,phone,login)
          VALUES(%s,%s,'Synthetic',%s,%s) ON CONFLICT(actor_id) DO UPDATE SET
          first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,
          phone=EXCLUDED.phone,login=EXCLUDED.login''',
                   (actor, 'Private-' + suffix, '+9940000000' + str(actor), 'only-' + suffix + '-contact'))
    db.execute("UPDATE saas_tenants SET modules='{\"tasks\":true}'::jsonb WHERE id=%s::uuid", (A,))
    db.commit()
    session = TenantBrowserSession(secret=lambda: SESSION_SECRET, member=members.member,
        identity_allowed=lambda *_: False, membership_error=TenantPlatformError,
        create_session=accounts.create_session, session_active=accounts.session_is_active)
    # Existing signed legacy-cookie parsing also has to reject foreign or
    # missing memberships; it does not manufacture a membership/session row.
    legacy = TenantBrowserSession(secret=lambda: SESSION_SECRET, member=members.member,
        identity_allowed=lambda *_: False, membership_error=TenantPlatformError)
    yield {'db': db, 'factory': factory, 'session': session, 'legacy': legacy,
           'forbidden': forbidden, 'blocked': [], 'requests': []}
    forbidden.assert_not_called()
    async_forbidden.assert_not_called()


def _capabilities(profile):
    # This is the production /me composition in bot._register_tenant_web_routes.
    return {**TenantPolicy(profile).public_capabilities(),
            'can_manage_employees': TenantPolicy(profile).allows_action('employees', 'manage')}


def _apis(f, origin=ORIGIN):
    principal = f['session'].profile
    auth = auth_api.handlers(auth_api.AuthApiContext(
        member=principal, telegram_identity=f['forbidden'], issue_session=f['session'].issue,
        tenants_for_user=members.tenants_for_user, create_tenant=members.create_tenant,
        list_integrations=integrations.list_integrations, list_members=members.list_members,
        capabilities=_capabilities, disable_browser_push=tenant_push.disable_browser_device,
        role_permissions=ROLE_PERMISSIONS, platform_error=TenantPlatformError,
        origin=origin, cookie_name=COOKIE_NAME, cookie_ttl=TTL_SECONDS,
        base_dir=str(ROOT), logger=LOGGER, account_profile=accounts.profile,
        list_sessions=accounts.list_sessions, revoke_session=accounts.revoke_session,
        session_id=f['session'].session_id))
    employee = member_handlers(MemberApiContext(
        member=principal, telegram_identity=f['forbidden'], telegram_name=auth_api.telegram_display_name,
        send_telegram=f['forbidden'], origin=origin, list_members=members.list_members,
        upsert_member=members.upsert_member, deactivate_member=members.deactivate_member,
        create_invite=invites.create_invite, request_invite_acceptance=invites.request_invite_acceptance,
        list_invite_requests=invites.list_invite_requests, decide_invite_request=invites.decide_invite_request,
        platform_error=TenantPlatformError, provision_employee_account=members.provision_employee_account,
        can_manage=lambda profile: TenantPolicy(profile).allows_action('employees', 'manage'),
        set_member_whatsapp_numbers=members.set_member_whatsapp_numbers,
        add_member_whatsapp_number=members.add_member_whatsapp_number))
    return auth, employee


def _app(f):
    @web.middleware
    async def no_mutations(request, handler):
        f['requests'].append((request.method, request.path))
        if request.method not in {'GET', 'HEAD'}:
            f['blocked'].append((request.method, request.path))
            return web.json_response({'success': False, 'error': 'Read-only NAV01 fixture'}, status=405)
        return await handler(request)
    app = web.Application(middlewares=[no_mutations])
    auth, employee = _apis(f)
    app.router.add_get('/api/platform/me', auth['me'])
    app.router.add_get('/api/platform/members', employee['members'])
    return app


def _snapshot(f):
    result = {}
    with f['factory']() as conn:
        for table in STATE_TABLES:
            rows = conn.execute(psycopg.sql.SQL('SELECT to_jsonb(t) AS value FROM {} AS t').format(
                psycopg.sql.Identifier(table))).fetchall()
            result[table] = sorted(json.dumps(row['value'], sort_keys=True, default=str) for row in rows)
    return result


async def _get(http, path, cookie=None, **kwargs):
    headers = {'Cookie': COOKIE_NAME + '=' + cookie} if cookie else {}
    response = await http.get(path, headers=headers, **kwargs)
    return response.status, await response.json(), response.headers


def _assert_tenant_rows(payload, tenant):
    assert payload['success'] is True
    assert {row['tenant_id'] for row in payload['members']} == {tenant}
    serialized = json.dumps(payload)
    assert (A_SENTINEL if tenant == A else B_SENTINEL) in serialized
    assert (B_SENTINEL if tenant == A else A_SENTINEL) not in serialized
    assert ('only-b-contact' if tenant == A else 'only-a-contact') not in serialized
    assert all(not ({'password', 'password_hash', 'secrets', 'token_hash'} & set(row))
               for row in payload['members'])


@pytest.mark.parametrize('actor', [11, 31, 33, 34, 38])
def test_existing_manage_authority_opens_real_members_without_granting_settings(staff, actor):
    f = staff
    cookie = f['session'].issue(A, actor)
    before = _snapshot(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, profile, _ = await _get(http, '/api/platform/me', cookie)
            assert status == 200 and profile['member']['telegram_id'] == actor
            assert profile['capabilities']['can_manage_employees'] is True
            _assert_tenant_rows(profile, A)
            if actor != 11:
                assert profile['capabilities']['modules']['settings'] is False
            if actor == 34:
                # The existing granular management API is intentionally more
                # specific than the legacy master module visibility predicate.
                assert profile['capabilities']['modules']['employees'] is False
            status, listed, headers = await _get(http, '/api/platform/members', cookie)
            assert status == 200
            assert headers['Cache-Control'] == 'no-store' and headers['Vary'] == 'Cookie'
            _assert_tenant_rows(listed, A)
    asyncio.run(scenario())
    assert _snapshot(f) == before and f['blocked'] == []


@pytest.mark.parametrize('actor', [32, 35, 36])
def test_members_navigation_cannot_substitute_a_role_view_grant_or_settings_for_manage(staff, actor):
    f = staff
    cookie = f['session'].issue(A, actor)
    before = _snapshot(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, profile, _ = await _get(http, '/api/platform/me', cookie)
            assert status == 200 and profile['capabilities']['can_manage_employees'] is False
            assert profile['members'] == []
            if actor == 35:
                assert profile['capabilities']['modules']['employees'] is True
            status, denied, _ = await _get(http, '/api/platform/members', cookie)
            assert status == 403 and denied['success'] is False and 'members' not in denied
            assert A_SENTINEL not in json.dumps(denied) and B_SENTINEL not in json.dumps(denied)
    asyncio.run(scenario())
    assert _snapshot(f) == before and f['blocked'] == []


@pytest.mark.parametrize('change', ['manage_revoked', 'membership_inactive', 'session_revoked'])
def test_current_cookie_authority_is_rechecked_after_the_employees_screen_was_opened(staff, change):
    f = staff
    cookie = f['session'].issue(A, 31)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            assert (await _get(http, '/api/platform/members', cookie))[0] == 200
            with f['factory']() as conn:
                if change == 'manage_revoked':
                    conn.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=31", (A,))
                elif change == 'membership_inactive':
                    conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=31', (A,))
                else:
                    conn.execute('UPDATE saas_tenant_browser_sessions SET revoked_at=now() WHERE tenant_id=%s::uuid AND actor_id=31', (A,))
            before = _snapshot(f)
            status, profile, _ = await _get(http, '/api/platform/me', cookie)
            if change == 'manage_revoked':
                assert status == 200 and profile['capabilities']['can_manage_employees'] is False
                assert profile['members'] == []
            else:
                assert status == 401 and 'member' not in profile
            status, denied, _ = await _get(http, '/api/platform/members', cookie)
            assert status == 403 and 'members' not in denied
            assert _snapshot(f) == before
    asyncio.run(scenario())
    assert f['blocked'] == []


@pytest.mark.parametrize('identity', ['none', 'foreign_actor', 'missing_membership', 'tampered', 'expired'])
def test_no_current_company_membership_or_valid_cookie_cannot_read_employee_data(staff, identity):
    f = staff
    if identity == 'none':
        cookie = None
    elif identity == 'foreign_actor':
        cookie = f['legacy'].issue(A, 22)  # The actual actor exists only in B.
    elif identity == 'missing_membership':
        cookie = f['legacy'].issue(A, 999)
    elif identity == 'tampered':
        cookie = f['session'].issue(A, 31)
        cookie = cookie[:-1] + ('0' if cookie[-1] != '0' else '1')
    else:
        expired = TenantBrowserSession(secret=lambda: SESSION_SECRET, member=members.member,
            identity_allowed=lambda *_: False, membership_error=TenantPlatformError, clock=lambda: 1)
        cookie = expired.issue(A, 31)
    before = _snapshot(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for path, expected in (('/api/platform/me', 401), ('/api/platform/members', 403)):
                status, denied, _ = await _get(http, path, cookie)
                assert status == expected and denied['success'] is False
                assert 'members' not in denied and 'member' not in denied
                assert A_SENTINEL not in json.dumps(denied) and B_SENTINEL not in json.dumps(denied)
    asyncio.run(scenario())
    assert _snapshot(f) == before and f['blocked'] == []


def test_switching_real_company_cookies_keeps_same_actor_members_and_contacts_tenant_local(staff):
    f = staff
    cookie_a = f['session'].issue(A, 37)
    cookie_b = f['session'].issue(B, 37)
    before = _snapshot(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for tenant, cookie, wrong in ((A, cookie_a, B), (B, cookie_b, A), (A, cookie_a, B)):
                # Existing GET authority comes from the cookie, never query
                # identity hints supplied by a stale view or another company.
                status, profile, _ = await _get(http, '/api/platform/me', cookie,
                    params={'tenant_id': wrong, 'expected_tenant_id': wrong, 'expected_user_id': 22})
                assert status == 200 and profile['member']['tenant_id'] == tenant
                assert profile['member']['telegram_id'] == 37
                _assert_tenant_rows(profile, tenant)
                status, listed, _ = await _get(http, '/api/platform/members', cookie,
                    params={'tenant_id': wrong, 'expected_tenant_id': wrong, 'expected_user_id': 22})
                assert status == 200
                _assert_tenant_rows(listed, tenant)
    asyncio.run(scenario())
    assert _snapshot(f) == before and f['blocked'] == []


@pytest.mark.parametrize('mode,actor,width,language', [
    ('ready', 11, 360, 'ru'), ('ready', 31, 390, 'az'),
    ('ready', 33, 768, 'en'), ('ready', 34, 1440, 'ru'),
    ('ready', 38, 390, 'ru'),
    ('denied', 32, 390, 'ru'), ('denied', 35, 768, 'en'),
    ('initial_pending', 31, 390, 'ru'), ('auth_pending', 31, 390, 'ru'),
    ('revoked', 31, 390, 'ru'), ('tenant_switch', 37, 390, 'en'),
    ('actor_switch', 31, 390, 'ru'),
])
def test_actual_app_company_navigation_uses_real_cookie_and_members_http(staff, mode, actor, width, language):
    """Required built /app bridge; member/session/capability APIs stay real."""
    import tenant_crm_native_task_api as task_api
    from tenant_web_assets import register_assets

    f = staff
    assert (ROOT / '.support-test-deps/node_modules/playwright').exists(), 'Required Chromium bridge dependency is missing'
    tokens = {name: f['session'].issue(tenant, identity) for name, tenant, identity in (
        ('initial', A, actor), ('company_b', B, 37), ('ordinary', A, 32))}
    before = _snapshot(f)
    state = {'pending_members': 0, 'released_members': 0, 'pending_me': 0,
             'hold_members': False, 'hold_me': mode == 'initial_pending', 'revoked': False}
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def start():
        state['release_members'] = asyncio.Event()
        state['release_me'] = asyncio.Event()
        if not state['hold_me']:
            state['release_me'].set()

        @web.middleware
        async def read_only(request, handler):
            f['requests'].append((request.method, request.path))
            if request.method not in {'GET', 'HEAD'}:
                f['blocked'].append((request.method, request.path))
                return web.json_response({'success': False, 'error': 'Read-only NAV01 fixture'}, status=405)
            return await handler(request)

        app = web.Application(middlewares=[read_only])
        auth, employee = _apis(f, origin)

        async def index(_request):
            return web.FileResponse(ROOT / 'docs/tenant-app.html')

        async def me(request):
            response = await auth['me'](request)
            if state['hold_me']:
                state['pending_me'] += 1
                await state['release_me'].wait()
            return response

        async def employee_list(request):
            response = await employee['members'](request)
            identity = f['session'].identity(request)
            if state['hold_members'] and identity and identity[0] == A:
                state['pending_members'] += 1
                await state['release_members'].wait()
                state['released_members'] += 1
            return response

        async def select_session(request):
            name = request.query.get('identity', 'initial')
            if name not in tokens:
                raise web.HTTPBadRequest()
            response = web.json_response({'success': True})
            response.set_cookie(COOKIE_NAME, tokens[name], httponly=True, samesite='Lax', path='/')
            return response

        async def hold_members(_request):
            state['hold_members'] = True
            state['pending_members'] = state['released_members'] = 0
            state['release_members'].clear()
            return web.json_response({'success': True})

        async def hold_me(_request):
            state['hold_me'] = True
            state['pending_me'] = 0
            state['release_me'].clear()
            return web.json_response({'success': True})

        async def release(_request):
            state['hold_members'] = state['hold_me'] = False
            state['release_members'].set()
            state['release_me'].set()
            return web.json_response({'success': True})

        async def revoke(_request):
            if mode != 'revoked' or not state['pending_members']:
                raise web.HTTPConflict()
            with f['factory']() as conn:
                conn.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=%s", (A, actor))
            state['revoked'] = True
            return web.json_response({'success': True})

        async def control_state(_request):
            return web.json_response({key: state[key] for key in (
                'pending_me', 'pending_members', 'released_members', 'revoked')})

        async def unrelated_empty_read(request):
            # Only unrelated startup modules use empty fixture data. Current
            # session, employee listing and the task-link consumer stay real.
            if any(part in request.path for part in ('/platform/me', '/platform/members', '/native/tasks')):
                raise web.HTTPNotFound()
            if request.path == '/api/platform/ai/dialogs':
                return web.json_response({'success': True, 'result': {
                    'dialogs': [], 'channels': [], 'agents': [], 'next_offset': None,
                    'total_count': 0, 'unread_count': 0}})
            return web.json_response({'success': True, 'result': {}, 'tasks': [], 'deals': [],
                'contacts': [], 'companies': [], 'pipelines': [], 'members': [], 'integrations': [],
                'notifications': [], 'unread_count': 0, 'requests': []})

        app.router.add_get('/app', index)
        register_assets(app)
        app.router.add_get('/api/platform/me', me)
        app.router.add_get('/api/platform/members', employee_list)
        app.router.add_get('/api/platform/account', auth['account'])
        app.router.add_get('/api/platform/sessions', auth['sessions'])
        app.router.add_get('/api/platform/crm/native/tasks', task_api.handler(f['session'].profile, origin, LOGGER))
        app.router.add_get('/test/session', select_session)
        app.router.add_get('/test/hold-members', hold_members)
        app.router.add_get('/test/hold-me', hold_me)
        app.router.add_get('/test/release', release)
        app.router.add_get('/test/revoke', revoke)
        app.router.add_get('/test/state', control_state)
        app.router.add_get('/api/{tail:.*}', unrelated_empty_read)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.SockSite(runner, sock).start()
        state['runner'] = runner
        ready.set()

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(10), 'Isolated HTTP fixture did not start'
    try:
        env = {**os.environ, 'COMPANY_EMPLOYEES_RUNTIME_ORIGIN': origin,
               'COMPANY_EMPLOYEES_RUNTIME_MODE': mode, 'COMPANY_EMPLOYEES_RUNTIME_ACTOR': str(actor),
               'COMPANY_EMPLOYEES_RUNTIME_WIDTH': str(width), 'COMPANY_EMPLOYEES_RUNTIME_LANGUAGE': language,
               'COMPANY_EMPLOYEES_RUNTIME_TENANT_A': A, 'COMPANY_EMPLOYEES_RUNTIME_TENANT_B': B}
        completed = subprocess.run(['node', str(ROOT / 'tests/company_employees_navigation_http_browser.cjs')],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=65)
        assert completed.returncode == 0, completed.stdout + '\n' + completed.stderr
        assert f['blocked'] == [], 'Navigation must never mutate any API'
        after = _snapshot(f)
        if mode == 'revoked':
            assert state['revoked'] is True
            expected = dict(before)
            with f['factory']() as conn:
                current = conn.execute('SELECT permissions FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s', (A, actor)).fetchone()
                assert current['permissions'] == []
                # Account-control setup is the sole intentional database
                # change; rendering/retrying/late responses may change none.
                conn.execute("UPDATE saas_tenant_members SET permissions='[\"employees:manage\"]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=%s", (A, actor))
            assert _snapshot(f) == expected
        else:
            assert after == before
    finally:
        loop.call_soon_threadsafe(state['release_members'].set)
        loop.call_soon_threadsafe(state['release_me'].set)
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()
        assert not thread.is_alive()
