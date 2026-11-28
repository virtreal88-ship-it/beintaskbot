"""HTTP tests with real locks and signed sessions on a disposable PostgreSQL."""
import ast
import asyncio
from dataclasses import replace
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import psycopg
import pytest

import legacy_document_storage as documents
import tenant_account_store as accounts
import tenant_auth_api as auth
import tenant_chat_send_api as chat_send
import tenant_crm_native_api as contacts
import tenant_member_store as members
import tenant_operator_api as operator
import tenant_platform_schema
from tenant_browser_session import COOKIE_NAME, TTL_SECONDS, TenantBrowserSession
from tenant_platform_core import TenantPlatformError, _connect
from test_startup_transaction_budget_postgres import database


LOGGER = logging.getLogger(__name__)
ORIGIN = 'https://crm.test'
TENANT = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'
ACTOR = 82


@pytest.fixture
def workspace(database, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', database)
    monkeypatch.setattr(tenant_platform_schema, '_schema_ready', False)
    with _connect() as conn:
        tenant_platform_schema._ensure_schema(conn)
        for ident, label in ((TENANT, 'one'), (OTHER, 'two')):
            conn.execute("""INSERT INTO saas_tenants(id, slug, name, status, owner_telegram_id, modules)
                            VALUES(%s, %s, %s, 'active', %s, '{"customers":true,"ai_agents":true}')""",
                         (ident, label, label, ACTOR))
            conn.execute("""INSERT INTO saas_tenant_members(tenant_id, telegram_id, role, display_name)
                            VALUES(%s, %s, 'owner', %s)""", (ident, ACTOR, label))
            conn.execute("""INSERT INTO saas_crm_native_contacts(id, tenant_id, display_name)
                            VALUES(%s, %s, %s)""", (uuid4(), ident, label))
    browser = TenantBrowserSession(
        secret=lambda: 'synthetic-secret', member=members.member,
        identity_allowed=lambda *_: False, membership_error=TenantPlatformError,
        create_session=accounts.create_session, session_active=accounts.session_is_active,
        legacy_session_allowed=accounts.legacy_session_is_allowed,
    )
    return browser


def headers_for(cookie):
    return {'Cookie': f'{COOKIE_NAME}={cookie}'}


def app_with_dialogs(member, monkeypatch):
    app = web.Application()

    async def shell(_request):
        return web.Response(text='CRM shell')

    def listing(profile, *_args):
        with _connect() as conn:
            conn.execute('SELECT 1')
        return {'dialogs': [], 'tenant_id': str(profile['tenant_id'])}

    monkeypatch.setattr(operator, 'listing', listing)
    app.router.add_get('/app', shell)
    app.router.add_get('/dialogs', operator.handler(member, ORIGIN, LOGGER, 'dialogs'))
    return app


async def observe_waiters(dsn, blocker_pid, application, count=1):
    def count_waiters():
        with psycopg.connect(dsn, connect_timeout=2) as conn:
            return conn.execute("""SELECT count(*) FROM pg_stat_activity
                WHERE application_name=%s AND wait_event_type='Lock'
                  AND %s = ANY(pg_blocking_pids(pid))""", (application, blocker_pid)).fetchone()[0]

    async with asyncio.timeout(5):
        while await asyncio.to_thread(count_waiters) < count:
            await asyncio.sleep(.02)


def test_session_lock_keeps_other_dialog_reads_responsive_and_send_fail_closed(database, workspace, monkeypatch):
    send = AsyncMock(return_value={'state': 'accepted'})
    monkeypatch.setattr(chat_send, 'run', send)
    revoke = Mock(wraps=accounts.revoke_session)
    old_cookie = replace(workspace, create_session=None).issue(TENANT, ACTOR)
    active_cookie = workspace.issue(TENANT, ACTOR)
    app = app_with_dialogs(workspace.profile, monkeypatch)
    auth_routes = auth.handlers(auth.AuthApiContext(
        member=workspace.profile, telegram_identity=Mock(), issue_session=workspace.issue,
        tenants_for_user=members.tenants_for_user, create_tenant=Mock(),
        list_integrations=Mock(), list_members=Mock(), capabilities=Mock(), disable_browser_push=Mock(),
        role_permissions={}, platform_error=TenantPlatformError, origin=ORIGIN,
        cookie_name=COOKIE_NAME, cookie_ttl=TTL_SECONDS, base_dir='.', logger=LOGGER,
        session_id=workspace.session_id, revoke_session=revoke,
    ))
    app.router.add_post('/api/platform/logout', auth_routes['logout'])
    app.router.add_post('/api/platform/crm/chat/send', chat_send.handler(workspace.profile, ORIGIN, None, None, LOGGER))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            with psycopg.connect(database) as blocker:
                blocker.execute('LOCK saas_tenant_browser_sessions IN ACCESS EXCLUSIVE MODE')
                request_headers = {**headers_for(active_cookie), 'Origin': ORIGIN}
                logout = asyncio.create_task(client.post('/api/platform/logout', headers=request_headers))
                delivery = asyncio.create_task(client.post('/api/platform/crm/chat/send', headers=request_headers,
                    json={'expected_tenant_id': TENANT, 'expected_user_id': str(ACTOR)}))
                try:
                    await observe_waiters(database, blocker.info.backend_pid, 'crm-session', count=2)
                    assert (await client.get('/app')).status == 200
                    available = await client.get('/dialogs', headers=headers_for(old_cookie))
                    assert available.status == 200
                    assert (await available.json())['result']['tenant_id'] == TENANT
                    responses = await asyncio.wait_for(asyncio.gather(logout, delivery), 5)
                    assert [res.status for res in responses] == [503, 503]
                    assert 'Set-Cookie' not in responses[0].headers
                    assert (await responses[1].json())['keep_request'] is True
                finally:
                    blocker.rollback()
                    await asyncio.gather(logout, delivery, return_exceptions=True)
            send.assert_not_awaited()
            revoke.assert_not_called()
            assert (await client.get('/dialogs', headers=headers_for(active_cookie))).status == 200
            assert (await client.post('/api/platform/logout', headers=request_headers)).status == 200
            assert (await client.get('/dialogs', headers=headers_for(active_cookie))).status == 403
            revoke.assert_called_once()
    asyncio.run(scenario())


def test_crm_record_lock_returns_503_while_dialogs_work_and_preserves_tenant_scope(database, workspace, monkeypatch):
    cookie = workspace.issue(TENANT, ACTOR)
    app = app_with_dialogs(workspace.profile, monkeypatch)
    path = '/api/platform/crm/native/contacts'
    app.router.add_get(path, contacts.handler(workspace.profile, ORIGIN, LOGGER))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            request_headers = headers_for(cookie)
            with psycopg.connect(database) as blocker:
                blocker.execute('LOCK saas_crm_native_contacts IN ACCESS EXCLUSIVE MODE')
                waiting = asyncio.create_task(client.get(path, headers=request_headers))
                try:
                    await observe_waiters(database, blocker.info.backend_pid, 'crm-record-read')
                    assert (await client.get('/dialogs', headers=request_headers)).status == 200
                    assert (await asyncio.wait_for(waiting, 5)).status == 503
                finally:
                    blocker.rollback()
                    await asyncio.gather(waiting, return_exceptions=True)
            result = await client.get(path, headers=request_headers)
            assert result.status == 200
            assert [item['display_name'] for item in (await result.json())['data']] == ['one']
            mismatched = await client.get(path, headers=request_headers,
                                          params={'expected_tenant_id': OTHER, 'expected_user_id': str(ACTOR)})
            assert mismatched.status == 409
    asyncio.run(scenario())


def load_bot_functions(namespace, *names):
    script = Path(__file__).resolve().parents[1] / 'bot.py'
    module = ast.parse(script.read_text(encoding='utf-8-sig'))
    module.body = [node for node in module.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    exec(compile(module, str(script), 'exec'), namespace)


def test_legacy_middleware_fails_closed_during_postgres_lock(database, monkeypatch):
    # Import the legacy authorization policy without any live credentials.
    for name in ('TELEGRAM_TOKEN', 'KOMMO_TOKEN', 'VAPID_PRIVATE_KEY', 'VAPID_PUBLIC_KEY'):
        monkeypatch.setenv(name, 'unused-in-test')
    import legacy_employee_access as employees

    # Exercise the production middleware without starting bot.py.
    namespace = {
        'web': web, '_is_public_api_request': lambda _: False,
        '_web_session_user_id': lambda req: ACTOR if req.cookies.get('synthetic-session') else None,
        '_telegram_webapp_user_id': lambda _: None, '_WEB_SESSION_COOKIE': 'synthetic-session',
        '_make_web_session': lambda _: 'signed', '_WEB_SESSION_TTL_SEC': 60,
        'employee_access_profile': employees.employee_access_profile,
        'employee_has_permission': employees.employee_has_permission,
    }
    load_bot_functions(namespace, 'telegram_auth_middleware', '_legacy_api_access', '_required_api_permission')
    monkeypatch.setattr(documents, '_DATABASE_URL', database)
    monkeypatch.setattr(documents, '_pg_schema_ready', True)
    monkeypatch.setattr(documents, '_cache', {})
    monkeypatch.setattr(documents, '_cache_sha', {})
    monkeypatch.setattr(employees, '_employee_access_cache', None)
    monkeypatch.setattr(employees, '_employee_access_cache_at', 0)
    fallback = Mock(side_effect=AssertionError('Authorization must not read GitHub on a PostgreSQL error'))
    monkeypatch.setattr(documents, '_github_load_file', fallback)
    with psycopg.connect(database) as conn:
        conn.execute('CREATE TABLE app_documents (key text PRIMARY KEY, value jsonb)')
        conn.execute('INSERT INTO app_documents VALUES (%s,%s::jsonb)',
                     ('employee_access.json', json.dumps({str(ACTOR): {'active': True, 'permissions': ['tasks'],
                                                                      'approval_permissions_version': 1,
                                                                      'linear_create_permission_version': 1}})))

    calls = []
    app = web.Application(middlewares=[namespace['telegram_auth_middleware']])

    async def route(req):
        calls.append(req.path)
        return web.json_response({'success':True})

    for path in ('/health', '/api/notifications', '/api/chats/pulse'):
        app.router.add_get(path, route)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            cookies = {'Cookie': 'synthetic-session=signed'}
            with psycopg.connect(database) as blocker:
                blocker.execute('LOCK app_documents IN ACCESS EXCLUSIVE MODE')
                request = asyncio.create_task(client.get('/api/notifications', headers=cookies))
                try:
                    await observe_waiters(database, blocker.info.backend_pid, 'crm-legacy-access')
                    assert (await client.get('/health')).status == 200
                    assert (await client.get('/api/notifications')).status == 401
                    failure = await asyncio.wait_for(request, 5)
                    assert failure.status == 503
                    assert 'Set-Cookie' not in failure.headers
                    assert calls == ['/health']
                finally:
                    blocker.rollback()
                    await asyncio.gather(request, return_exceptions=True)
            assert (await client.get('/api/notifications', headers=cookies)).status == 200
            assert (await client.get('/api/chats/pulse', headers=cookies)).status == 403
            fallback.assert_not_called()
    asyncio.run(scenario())


def test_legacy_kommo_reply_cannot_send_while_deal_access_waits(database, workspace, monkeypatch):
    def authorize(*_):
        with _connect() as conn:
            conn.execute('SELECT 1 FROM budget_markers')
        return {'id': 101}, None

    provider = Mock(side_effect=AssertionError('No provider call while access is unverified'))
    namespace = {'web': web, 'asyncio': asyncio, '_deal_request_user': lambda _: ACTOR,
                 '_authorized_deal_lead': authorize, '_resolve_channel_talk_id': provider}
    load_bot_functions(namespace, '_authorized_legacy_deal', 'handle_api_deal_kommo_reply')
    app = app_with_dialogs(workspace.profile, monkeypatch)
    path = '/api/deal/chat/kommo-reply'
    app.router.add_post(path, namespace['handle_api_deal_kommo_reply'])

    async def scenario():
        cookie = workspace.issue(TENANT, ACTOR)
        async with TestClient(TestServer(app)) as client:
            with psycopg.connect(database) as blocker:
                blocker.execute('LOCK budget_markers IN ACCESS EXCLUSIVE MODE')
                waiting = asyncio.create_task(client.post(path, json={'lead_id': 101, 'text': 'Synthetic'}))
                try:
                    await observe_waiters(database, blocker.info.backend_pid, 'crm-legacy-deal')
                    assert (await client.get('/dialogs', headers=headers_for(cookie))).status == 200
                    assert (await asyncio.wait_for(waiting, 5)).status == 503
                    provider.assert_not_called()
                finally:
                    blocker.rollback()
                    await asyncio.gather(waiting, return_exceptions=True)
    asyncio.run(scenario())
