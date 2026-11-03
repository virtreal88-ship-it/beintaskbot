"""CRM01: current-cookie, source-qualified contact reads on native PostgreSQL."""
import asyncio
import importlib
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
import tenant_crm_native_api as contact_api
import tenant_crm_native_contacts as contacts
import tenant_crm_store as crm
from tenant_crm_identity import record_link


NATIVE = [str(uuid.uuid5(uuid.NAMESPACE_URL, 'isolated-crm01-native-' + str(i))) for i in range(1, 7)]
FOREIGN_NATIVE = str(uuid.uuid5(uuid.NAMESPACE_URL, 'isolated-crm01-foreign-native'))
PHONE = '+994501110001'
RELATION_DEAL = str(uuid.uuid5(uuid.NAMESPACE_URL, 'isolated-crm01-browser-relation-deal'))


def _lead(f, ident, name, *, tenant=A, pipeline=10, stage=101, account='100', contact='501',
          primary_account='absent', provider='kommo', channel=None, updated='2026-01-01T00:00:00+00:00', raw_extra=None):
    primary = {'is_main': True, 'name': 'UNPUBLISHED RAW NAME',
               'custom_fields_values': [{'field_code': 'EMAIL', 'values': [{'value': 'NEVER-PUBLISH@raw.example'}]}]}
    if contact != 'absent': primary['id'] = contact
    if primary_account != 'absent': primary['account_id'] = primary_account
    raw = {'id': ident, 'provider': provider, '_embedded': {'contacts': [
        {'id': 999999, 'is_main': False, 'name': 'SECONDARY CONTACT MUST NOT BECOME A ROW'}, primary]}}
    if account != 'absent': raw['account_id'] = account
    if raw_extra: raw.update(raw_extra)
    crm.upsert_crm_deals(tenant_id=tenant, deals=[{'kommo_lead_id': ident,
        'pipeline_id': pipeline, 'status_id': stage, 'stage_name': 'Cached stage',
        'name': 'Deal ' + str(ident), 'contact_name': name, 'phone': PHONE,
        'channel': provider if channel is None else channel, 'source_updated_at': updated, 'raw': raw}])


@pytest.fixture
def directory(staff, monkeypatch):
    f = staff
    for name in ('tenant_crm_native_contacts', 'tenant_crm_store', 'tenant_crm_contact_listing',
                 'tenant_crm_native_companies', 'tenant_crm_native_deals', 'tenant_crm_sync_outbox'):
        module = importlib.import_module(name)
        if hasattr(module, '_connect'):
            monkeypatch.setattr(module, '_connect', f['factory'])
    with f['factory']() as conn:
        for tenant in (A, B):
            conn.execute("UPDATE saas_tenants SET modules='{\"customers\":true,\"deals\":true,\"tasks\":true}'::jsonb WHERE id=%s::uuid", (tenant,))
            for pipeline, stage, owner in (('10', '101', 82 if tenant == A else 22), ('20', '201', 11 if tenant == A else 22)):
                conn.execute('''INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name,owner_telegram_id)
                  VALUES(%s::uuid,%s,%s,%s)''', (tenant, pipeline, 'Pipeline ' + pipeline, owner))
                conn.execute('''INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name)
                  VALUES(%s::uuid,%s,%s,%s)''', (tenant, pipeline, stage, 'Stage ' + stage))
            conn.execute('''INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name,settings)
              VALUES(%s::uuid,'10','102','Hidden stage','{"visible":false}'::jsonb)''', (tenant,))
            conn.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'kommo','not_connected')", (tenant,))
        for tenant, actor, role, grants, active in (
            (A, 81, 'admin', ['customers'], True),
            (A, 82, 'manager', ['customers', 'deals'], True),
            (A, 83, 'operator', ['tasks'], True),
            (A, 84, 'admin', ['deals'], True),
            (A, 85, 'admin', ['customers', 'customers:view'], True),
            (A, 86, 'operator', ['customers:view'], True),
            (A, 87, 'admin', ['customers:create'], True),
            (A, 88, 'admin', ['customers'], True), (B, 88, 'admin', ['customers'], True),
            (A, 89, 'manager', ['deals'], True), (A, 90, 'admin', ['customers'], False),
        ):
            conn.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions,active)
              VALUES(%s::uuid,%s,%s,%s,%s::jsonb,%s)''',
              (tenant, actor, 'Directory actor ' + str(actor), role, json.dumps(grants), active))
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'members','{\"82\":{\"pipeline_ids\":[\"10\"]},\"89\":{\"pipeline_ids\":[]}}'::jsonb,11)", (A,))
    for ident, name, phone, email in (
        (NATIVE[0], 'Native Alpha', PHONE, 'alpha@native.example'),
        (NATIVE[1], 'Native Same Phone', PHONE, 'second@native.example'),
        (NATIVE[2], 'Native literal %_\\ marker', '+994501110003', 'literal@native.example'),
        (NATIVE[3], 'Archived local contact', '', ''),
        (NATIVE[4], 'Local fields stay readable', '+994501110005', 'local@native.example'),
        (NATIVE[5], 'Native Unicode Əlaqə', '+994501110006', 'unicode@native.example'),
    ):
        contacts.create_contact(A, name, phone, email, contact_id=ident)
    contacts.archive_contact(A, NATIVE[3])
    contacts.create_contact(B, 'B PRIVATE Native', PHONE, 'private@tenant-b.example', contact_id=FOREIGN_NATIVE)
    _lead(f, 101, 'Visible old contact')
    _lead(f, 102, 'Visible newest contact', updated='2026-02-01T00:00:00+00:00')
    _lead(f, 103, 'FORBIDDEN newer pipeline fields', pipeline=20, stage=201, updated='2026-03-01T00:00:00+00:00')
    _lead(f, 104, 'FORBIDDEN hidden-stage fields', stage=102, updated='2026-04-01T00:00:00+00:00')
    _lead(f, 105, 'Other account same contact', account='200')
    _lead(f, 106, 'Distinct same-phone contact', contact='502')
    _lead(f, 107, 'No primary contact proof', contact='absent')
    _lead(f, 108, 'Conflicting account proof', primary_account='999')
    _lead(f, 109, 'Explicit Connect cached context', provider='connect')
    _lead(f, 110, 'Unsupported provider cached context', provider='beinsystems')
    _lead(f, 111, 'HIDDEN LINK MUST NOT AUTHORIZE EXTERNAL', contact='999', pipeline=20, stage=201)
    _lead(f, 112, 'EXPLICIT CONNECT TAG MUST EXCLUDE', contact='550', channel='connect:synthetic-saved-channel')
    _lead(f, 101, 'B PRIVATE Kommo', tenant=B)
    with f['factory']() as conn:
        with conn.cursor() as cur:
            record_link(cur, tenant_id=A, provider='kommo', entity_type='contact', internal_id=NATIVE[4], external_id='999', sync_state='synced')
    f['posts'] = []
    yield f
    assert f['blocked'] == []


def _app(f, origin=ORIGIN, *, allow_related_writes=False):
    @web.middleware
    async def guard(request, handler):
        if request.method not in {'GET', 'HEAD'}:
            prefixes = ('/api/platform/crm/native/contacts',) + (('/api/platform/crm/native/companies', '/api/platform/crm/native/deals') if allow_related_writes else ())
            if request.method != 'POST' or not (request.path.startswith(prefixes) or request.path in f.get('allowed_posts', set())):
                f['blocked'].append((request.method, request.path))
                return web.json_response({'success': False, 'error': 'Unexpected directory mutation'}, status=405)
            f['posts'].append({'path': request.path, 'query': dict(request.query), 'data': await request.json()})
        return await handler(request)
    app = web.Application(middlewares=[guard])
    auth, _ = _apis(f, origin)
    handle = contact_api.handler(f['session'].profile, origin, LOGGER)
    app.router.add_get('/api/platform/me', auth['me'])
    app.router.add_get('/api/platform/crm/native/contacts', handle)
    app.router.add_post('/api/platform/crm/native/contacts', handle)
    app.router.add_get('/api/platform/crm/native/contacts/{id}', handle)
    app.router.add_post('/api/platform/crm/native/contacts/{id}', handle)
    import tenant_crm_native_company_api as company_api
    import tenant_crm_native_deal_api as deal_api
    for path, module in (('companies', company_api), ('deals', deal_api)):
        related = module.handler(f['session'].profile, origin, LOGGER)
        for suffix in ('', '/{id}'):
            app.router.add_get('/api/platform/crm/native/' + path + suffix, related)
            app.router.add_post('/api/platform/crm/native/' + path + suffix, related)
    return app


def _state(f):
    state = _snapshot(f)
    with f['factory']() as conn:
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'saas_crm_%' ORDER BY tablename").fetchall()
        for item in tables:
            rows = conn.execute(psycopg.sql.SQL('SELECT to_jsonb(t) AS value FROM {} t').format(psycopg.sql.Identifier(item['tablename']))).fetchall()
            state[item['tablename']] = sorted(json.dumps(row['value'], sort_keys=True) for row in rows)
        state['saas_tenant_integrations'] = sorted(json.dumps(row['value'], sort_keys=True) for row in conn.execute('SELECT to_jsonb(t) AS value FROM saas_tenant_integrations t').fetchall())
    return state


async def _get(http, cookie, *, source=None, path='/api/platform/crm/native/contacts', query=None, listing=True):
    params = [('listing', '1')] if listing else []
    if source is not None: params.append(('source', source))
    if query: params.extend(query.items() if isinstance(query, dict) else query)
    response = await http.get(path, params=params, headers={'Cookie': COOKIE_NAME + '=' + cookie})
    try: data = await response.json()
    except Exception: data = {'unexpected_body': await response.text()}
    return response.status, data


async def _post(http, cookie, data, *, path='/api/platform/crm/native/contacts', query=None, origin=ORIGIN):
    response = await http.post(path, params=query or {}, headers={'Cookie': COOKIE_NAME + '=' + cookie, 'Origin': origin}, json=data)
    return response.status, await response.json()


def _source(page, name):
    return next(item for item in page['sources'] if item['source'] == name)


def test_native_directory_has_one_selection_and_never_merges_phone_or_provider_links(directory):
    f = directory
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie)
            assert status == 200 and page['source'] == 'native', page
            assert page['total'] == page['source_total'] == 5 == len(page['data'])
            assert {item['key'] for item in page['data']} == {'native:' + ident for ident in NATIVE if ident != NATIVE[3]}
            assert all(item['kind'] == 'native_contact' and item['source'] == 'native' for item in page['data'])
            assert sum(item['phone'] == PHONE for item in page['data']) == 2
            linked = next(item for item in page['data'] if item['id'] == NATIVE[4])
            assert linked['display_name'] == 'Local fields stay readable' and linked['phone'] == '+994501110005'
            assert 'HIDDEN LINK' not in json.dumps(page) and 'B PRIVATE' not in json.dumps(page)
            status, filtered = await _get(http, cookie, source='native', query={'search': 'Native Alpha'})
            assert status == 200 and filtered['total'] == 1 and filtered['source_total'] == 5
            assert filtered['data'][0]['id'] == NATIVE[0]
    asyncio.run(scenario())
    assert _state(f) == before


def test_kommo_scope_precedes_identity_ranking_search_and_count_and_uses_only_safe_public_fields(directory):
    f = directory
    cookie = f['session'].issue(A, 82)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie, source='kommo')
            assert status == 200, page
            keys = {item['key'] for item in page['data']}
            assert keys == {'kommo:100:501', 'kommo:200:501', 'kommo:100:502', 'legacy-deal:107', 'legacy-deal:108'}
            assert page['total'] == page['source_total'] == len(keys) == 5
            current = next(item for item in page['data'] if item['key'] == 'kommo:100:501')
            assert current['display_name'] == 'Visible newest contact' and current['lead_id'] in (102, '102')
            assert all(item['historical'] is True for item in page['data'])
            assert all(item['actions']['update'] is False and item['actions']['archive'] is False for item in page['data'])
            assert all('raw' not in item for item in page['data'])
            serialized = json.dumps(page)
            for forbidden in ('FORBIDDEN', 'HIDDEN LINK', 'B PRIVATE', 'NEVER-PUBLISH', 'SECONDARY CONTACT', 'UNPUBLISHED RAW NAME', 'EXPLICIT CONNECT TAG'):
                assert forbidden not in serialized, forbidden
            for item in page['data']:
                if item['kind'] == 'legacy_deal_context':
                    assert not item.get('contact_id'), 'A legacy deal context is not a fabricated contact'
            for needle in ('FORBIDDEN', 'HIDDEN LINK', 'NEVER-PUBLISH'):
                status, filtered = await _get(http, cookie, source='kommo', query={'search': needle})
                assert status == 200 and filtered['total'] == 0 and filtered['data'] == []
                assert filtered['source_total'] == 5
    asyncio.run(scenario())
    assert _state(f) == before


@pytest.mark.parametrize('actor,native_allowed,kommo_allowed,default', [
    (11, True, True, 'native'), (81, True, True, 'native'), (82, True, True, 'native'),
    (84, False, True, 'kommo'), (86, True, False, 'native'), (83, False, False, None), (87, False, False, None),
])
def test_directory_source_authority_uses_actual_distinct_policy_predicates(directory, actor, native_allowed, kommo_allowed, default):
    f = directory
    cookie = f['session'].issue(A, actor)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie)
            if default is None:
                assert status == 403 and page['code'], page
            else:
                assert status == 200 and page['source'] == default, page
                assert _source(page, 'native')['allowed'] is native_allowed
                assert _source(page, 'kommo')['allowed'] is kommo_allowed
            for source, allowed in (('native', native_allowed), ('kommo', kommo_allowed)):
                status, data = await _get(http, cookie, source=source)
                assert status == (200 if allowed else 403), data
                if not allowed: assert 'data' not in data or data['data'] == []
    asyncio.run(scenario())
    assert _state(f) == before


@pytest.mark.parametrize('bad_grants', [{'customers': False, 'deals': False}, 'customers', 1, True, None])
def test_malformed_persisted_permission_json_cannot_become_directory_grants(directory, bad_grants):
    f = directory
    with f['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=81',
                     (json.dumps(bad_grants), A))
    cookie = f['session'].issue(A, 81)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f, allow_related_writes=True))) as http:
            for listing, source in ((False, None), (True, 'native'), (True, 'kommo')):
                status, response = await _get(http, cookie, listing=listing, source=source)
                assert status == 403 and 'data' not in response, response
            for resource in ('contacts', 'companies', 'deals'):
                path = '/api/platform/crm/native/' + resource
                status, response = await _get(http, cookie, path=path, listing=False)
                assert status == 403 and 'data' not in response, response
                status, response = await _post(http, cookie, {'name': 'MUST NOT CREATE',
                    'title': 'MUST NOT CREATE', 'pipeline_id': '10', 'stage_id': '101'}, path=path)
                assert status == 403 and not response.get('success'), response
    asyncio.run(scenario())
    assert _state(f) == before


def test_local_native_permission_shape_check_preserves_existing_owner_role_override(directory):
    f = directory
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET permissions='{\"customers\":false,\"deals\":false}' WHERE tenant_id=%s::uuid AND telegram_id=11", (A,))
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for source in ('native', 'kommo'):
                status, page = await _get(http, cookie, source=source)
                assert status == 200 and page['total'] > 0, page
            for resource in ('contacts', 'companies', 'deals'):
                status, response = await _get(http, cookie, path='/api/platform/crm/native/' + resource, listing=False)
                assert status == 200 and response['success'] is True, response
    asyncio.run(scenario())
    assert _state(f) == before


@pytest.mark.parametrize('proof,account,primary_account,contact,expected_key', [
    ('lead_only', '301', 'absent', '701', 'kommo:301:701'),
    ('primary_only', 'absent', '302', '702', 'kommo:302:702'),
    ('both_match', '303', 303, '703', 'kommo:303:703'),
    ('both_missing', 'absent', 'absent', '704', None),
    ('null_side', '304', None, '705', None),
    ('bool_side', True, '305', '706', None),
    ('float_side', 306.0, '306', '707', None),
    ('zero_side', '307', 0, '708', None),
    ('malformed_side', '308', 'not-an-account', '709', None),
    ('conflict', '309', '310', '710', None),
    ('missing_contact', '311', 'absent', 'absent', None),
    ('large_opaque_ids', '9007199254740993', 'absent', '9007199254740995', 'kommo:9007199254740993:9007199254740995'),
])
def test_account_and_primary_contact_proof_is_exact_lossless_or_explicit_legacy_context(directory, proof, account, primary_account, contact, expected_key):
    f = directory
    ident = 701
    _lead(f, ident, 'Proof variant ' + proof, account=account, primary_account=primary_account, contact=contact)
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie, source='kommo', query={'search': 'Proof variant ' + proof})
            assert status == 200 and page['total'] == 1, page
            row = page['data'][0]
            assert row['key'] == (expected_key or 'legacy-deal:' + str(ident))
            assert row['kind'] == ('kommo_contact' if expected_key else 'legacy_deal_context')
            if expected_key:
                assert isinstance(row['account_id'], str) and isinstance(row['contact_id'], str)
            else:
                assert not row.get('contact_id')
            status, exact = await _get(http, cookie, source='kommo', query={'row_key': row['key']})
            assert status == 200 and len(exact['data']) == 1 and exact['data'][0]['key'] == row['key']
    asyncio.run(scenario())
    assert _state(f) == before


def test_primary_contact_selection_and_explicit_nonkommo_provider_override_prevent_identity_guessing(directory):
    f = directory
    cookie = f['session'].issue(A, 82)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie, source='kommo')
            assert status == 200 and all('999999' not in row['key'] for row in page['data'])
            assert not any(row['lead_id'] in (109, '109', 110, '110', 112, '112') for row in page['data'])
            status, legacy = await _get(http, cookie, source='kommo', query={'legacy_deal_id': '109'})
            assert status == 200 and legacy['data'][0]['kind'] == 'legacy_deal_context'
            assert legacy['data'][0]['key'] == 'legacy-deal:109'
            status, legacy = await _get(http, cookie, source='kommo', query={'legacy_deal_id': '112'})
            assert status == 200 and legacy['data'][0]['kind'] == 'legacy_deal_context'
            assert legacy['data'][0]['key'] == 'legacy-deal:112'
            status, denied = await _get(http, cookie, source='kommo', query={'legacy_deal_id': '103'})
            assert status == 404, denied
    asyncio.run(scenario())


@pytest.mark.parametrize('invalid', [
    {'source': 'all'}, {'source': 'external'}, {'limit': '-1'}, {'offset': '-1'},
    {'limit': 'not-a-number'}, {'row_key': 'kommo:100:501', 'legacy_deal_id': '101'},
    {'source': ''}, {'row_key': ''}, {'legacy_deal_id': ''},
])
def test_invalid_directory_queries_fail_without_changing_crm_or_provider_state(directory, invalid):
    f = directory
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, result = await _get(http, cookie, query=invalid)
            assert status == 400 and result['code'], result
    asyncio.run(scenario())
    assert _state(f) == before


@pytest.mark.parametrize('source', ['native', 'kommo'])
def test_directory_pagination_and_literal_search_share_the_same_stable_selection(directory, source):
    f = directory
    for index in range(61):
        if source == 'native':
            contacts.create_contact(A, 'Paged person %03d' % index, PHONE,
                                    contact_id=str(uuid.UUID(int=44000 + index)))
        else:
            _lead(f, 800 + index, 'Paged person %03d' % index, contact=str(1000 + index))
    if source == 'native':
        with f['factory']() as conn:
            conn.execute("UPDATE saas_crm_native_contacts SET updated_at='2026-05-01' WHERE tenant_id=%s::uuid AND display_name LIKE 'Paged person %%'", (A,))
    else:
        _lead(f, 890, 'External literal %_\\ marker', contact='2000')
    cookie = f['session'].issue(A, 82)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            offset, keys, total = 0, [], None
            while offset is not None:
                status, page = await _get(http, cookie, source=source,
                    query={'search': 'Paged person', 'limit': '17', 'offset': str(offset)})
                assert status == 200 and page['total'] == 61 and page['limit'] == 17, page
                assert page['source_total'] >= 66
                total = page['source_total'] if total is None else total
                assert page['source_total'] == total
                keys.extend(row['key'] for row in page['data'])
                offset = page['next_offset']
            assert len(keys) == len(set(keys)) == 61
            status, repeat = await _get(http, cookie, source=source,
                query={'search': 'Paged person', 'limit': '17', 'offset': '0'})
            assert status == 200 and [row['key'] for row in repeat['data']] == keys[:17]
            status, beyond = await _get(http, cookie, source=source,
                query={'search': 'Paged person', 'limit': '17', 'offset': '999'})
            assert status == 200 and beyond['data'] == [] and beyond['total'] == 61
            assert beyond['source_total'] == total and beyond['next_offset'] is None
            status, literal = await _get(http, cookie, source=source, query={'search': '%_\\'})
            assert status == 200 and literal['total'] == 1 and len(literal['data']) == 1, literal
            assert '%_\\' in literal['data'][0]['display_name']
    asyncio.run(scenario())
    assert _state(f) == before


@pytest.mark.parametrize('change', ['permissions', 'membership', 'pipeline_policy', 'stage_policy'])
def test_fresh_external_lookup_rechecks_current_member_and_workflow_scope(directory, change):
    f = directory
    if change == 'stage_policy':
        # Keep another stage open, so this is a vanished exact entry (404),
        # separately from the entire empty pipeline scope (403) case.
        with f['factory']() as conn:
            conn.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'10','103','Other visible stage')", (A,))
    cookie = f['session'].issue(A, 82)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie, source='kommo', query={'row_key': 'kommo:100:501'})
            assert status == 200 and page['data'][0]['lead_id'] in (102, '102')
            with f['factory']() as conn:
                if change == 'permissions':
                    conn.execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=82", (A,))
                elif change == 'membership':
                    conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=82', (A,))
                elif change == 'pipeline_policy':
                    conn.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{82,pipeline_ids}','[]') WHERE tenant_id=%s::uuid AND policy_key='members'", (A,))
                    conn.execute('UPDATE saas_tenant_pipelines SET owner_telegram_id=11 WHERE tenant_id=%s::uuid AND pipeline_id=\'10\'', (A,))
                else:
                    conn.execute("UPDATE saas_tenant_pipeline_stages SET settings='{\"visible\":false}' WHERE tenant_id=%s::uuid AND pipeline_id='10' AND stage_id='101'", (A,))
            before = _state(f)
            status, denied = await _get(http, cookie, source='kommo', query={'row_key': 'kommo:100:501'})
            assert status == (404 if change == 'stage_policy' else 403), denied
            assert 'data' not in denied or denied['data'] == []
            assert 'Visible newest contact' not in json.dumps(denied)
            assert _state(f) == before
    asyncio.run(scenario())


@pytest.mark.parametrize('actor', [89, 90])
def test_empty_pipeline_scope_and_inactive_membership_never_gain_external_directory_access(directory, actor):
    f = directory
    cookie = (f['legacy'] if actor == 90 else f['session']).issue(A, actor)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, data = await _get(http, cookie, source='kommo')
            assert status == 403 and 'data' not in data, data
    asyncio.run(scenario())
    assert _state(f) == before


def test_identical_external_ids_and_shared_phone_remain_scoped_to_current_cookie_tenant(directory):
    f = directory
    cookies = {tenant: f['session'].issue(tenant, 88) for tenant in (A, B)}
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for tenant in (A, B, A):
                status, page = await _get(http, cookies[tenant], source='kommo', query={'row_key': 'kommo:100:501'})
                assert status == 200 and page['data'][0]['key'] == 'kommo:100:501', page
                assert ('B PRIVATE' in page['data'][0]['display_name']) is (tenant == B)
                status, native = await _get(http, cookies[tenant], listing=False,
                    path='/api/platform/crm/native/contacts/' + (FOREIGN_NATIVE if tenant == A else NATIVE[0]))
                assert status == 404 and 'data' not in native
    asyncio.run(scenario())
    assert _state(f) == before


@pytest.mark.parametrize('query,status', [
    ([('expected_tenant_id', A)], 400),
    ([('expected_user_id', '11')], 400),
    ([('expected_tenant_id', A), ('expected_tenant_id', A), ('expected_user_id', '11')], 400),
    ([('expected_tenant_id', A), ('expected_user_id', '11'), ('expected_user_id', '11')], 400),
    ({'expected_tenant_id': 'not-a-uuid', 'expected_user_id': '11'}, 400),
    ({'expected_tenant_id': A, 'expected_user_id': '11.0'}, 400),
    ({'expected_tenant_id': B, 'expected_user_id': '11'}, 409),
    ({'expected_tenant_id': A, 'expected_user_id': '22'}, 409),
])
def test_expected_identity_query_pair_is_validated_before_contact_reads_or_writes(directory, query, status):
    f = directory
    cookie = f['session'].issue(A, 11)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            for path in ('/api/platform/crm/native/contacts', '/api/platform/crm/native/contacts/' + NATIVE[0]):
                for listing in (False, True) if not path.endswith(NATIVE[0]) else (False,):
                    actual, data = await _get(http, cookie, path=path, listing=listing, query=query)
                    assert actual == status and data['code'], data
                actual, data = await _post(http, cookie, {'name': 'MUST NOT APPLY'}, path=path, query=query)
                assert actual == status and data['code'], data
    asyncio.run(scenario())
    assert _state(f) == before


def test_new_cookie_cannot_apply_a_create_intent_captured_in_previous_company(directory):
    f = directory
    cookie_a, cookie_b = (f['session'].issue(tenant, 88) for tenant in (A, B))
    ident = str(uuid.UUID(int=45000))
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            assert (await _get(http, cookie_a))[0] == 200
            status, data = await _post(http, cookie_b, {'id': ident, 'name': 'Old company draft'},
                query={'expected_tenant_id': A, 'expected_user_id': '88'})
            assert status == 409 and data['code'] == 'contacts_context_changed'
            status, current = await _get(http, cookie_b,
                query={'expected_tenant_id': B, 'expected_user_id': '88'})
            assert status == 200 and current['total'] == 1
    asyncio.run(scenario())
    assert _state(f) == before


def test_disconnected_cached_history_is_readable_and_empty_disconnected_source_is_unavailable(directory):
    f = directory
    cookie = f['session'].issue(A, 11)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, cached = await _get(http, cookie, source='kommo')
            assert status == 200 and cached['total'] > 0
            assert _source(cached, 'kommo')['available'] is True
            assert _source(cached, 'kommo')['reason'] == 'integration_not_connected'
            with f['factory']() as conn:
                conn.execute('DELETE FROM saas_crm_deals WHERE tenant_id=%s::uuid', (A,))
            before = _state(f)
            status, default = await _get(http, cookie)
            assert status == 200 and default['source'] == 'native'
            external = _source(default, 'kommo')
            assert external['allowed'] is True and external['available'] is False and external['state'] == 'unavailable'
            status, unavailable = await _get(http, cookie, source='kommo')
            assert status == 200 and unavailable['state'] == 'unavailable' and unavailable['data'] == []
            assert unavailable['total'] == unavailable['source_total'] == 0
            assert _state(f) == before
    asyncio.run(scenario())


def test_existing_native_crud_retains_uuid_replay_actions_and_foreign_isolation(directory):
    f = directory
    cookie = f['session'].issue(A, 11)
    viewer = f['session'].issue(A, 85)
    ident = str(uuid.UUID(int=46000))
    initial = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            # Legacy array response stays compatible beside the additive mode.
            status, legacy = await _get(http, cookie, listing=False)
            assert status == 200 and isinstance(legacy['data'], list)
            status, page = await _get(http, viewer)
            assert status == 200 and page['actions']['view'] is True
            assert all(page['actions'][key] is False for key in ('create', 'update', 'archive'))
            before_denied = _state(f)
            status, _ = await _post(http, viewer, {'id': ident, 'name': 'Viewer must not create'})
            assert status == 403 and _state(f) == before_denied
            command = {'id': ident, 'name': 'Isolated HTTP contact', 'phone': PHONE, 'email': 'synthetic@native.example'}
            for _ in range(2):
                status, created = await _post(http, cookie, command,
                    query={'expected_tenant_id': A, 'expected_user_id': '11'})
                assert status == 200 and created['data']['id'] == ident, created
            before_conflict = _state(f)
            status, _ = await _post(http, cookie, {**command, 'name': 'Changed UUID command'})
            assert status == 400 and _state(f) == before_conflict
            status, _ = await _post(http, cookie, {**command, 'id': FOREIGN_NATIVE})
            assert status == 400 and _state(f) == before_conflict
            path = '/api/platform/crm/native/contacts/' + ident
            status, changed = await _post(http, cookie, {'action': 'update', 'name': 'Edited isolated contact', 'phone': PHONE, 'email': 'edited@native.example'}, path=path)
            assert status == 200 and changed['data']['id'] == ident
            status, detail = await _get(http, cookie, path=path, listing=False)
            assert status == 200 and detail['data']['display_name'] == 'Edited isolated contact'
            status, page = await _get(http, cookie, source='native', query={'search': 'Edited isolated contact'})
            assert status == 200 and page['total'] == 1 and page['data'][0]['key'] == 'native:' + ident
            status, archived = await _post(http, cookie, {'action': 'archive'}, path=path)
            assert status == 200 and archived['data'] is True
            assert (await _get(http, cookie, path=path, listing=False))[0] == 404
            status, page = await _get(http, cookie, source='native', query={'search': 'Edited isolated contact'})
            assert status == 200 and page['total'] == 0
    asyncio.run(scenario())
    after = _state(f)
    changed_tables = {key for key in after if after[key] != initial[key]}
    assert changed_tables == {'saas_crm_native_contacts'}, changed_tables
    with f['factory']() as conn:
        row = conn.execute('SELECT display_name,archived_at FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, ident)).fetchone()
        assert row['display_name'] == 'Edited isolated contact' and row['archived_at'] is not None


@pytest.mark.parametrize('embedded,verified', [
    ([{'id': '701'}], True),
    ([{'id': '701'}, {'id': '702'}], False),
    ([{'id': '701', 'is_main': False}, {'id': '702', 'is_main': False}], False),
    ([{'id': '701', 'is_main': True}, {'id': '702', 'is_main': True}], False),
    ([{'id': '701', 'is_main': 'true'}, {'id': '702', 'is_main': False}], False),
    ([{'id': '701', 'is_main': True}, 'malformed saved contact'], False),
])
def test_ambiguous_saved_primary_contacts_stay_typed_deal_contexts(directory, embedded, verified):
    f = directory
    _lead(f, 970, 'Ambiguous primary fixture', raw_extra={'_embedded': {'contacts': embedded}})
    cookie = f['session'].issue(A, 82)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie, source='kommo', query={'search': 'Ambiguous primary fixture'})
            assert status == 200 and page['total'] == 1, page
            row = page['data'][0]
            assert row['key'] == ('kommo:100:701' if verified else 'legacy-deal:970')
            assert row['kind'] == ('kommo_contact' if verified else 'legacy_deal_context')
            assert row['lead_id'] == '970'
    asyncio.run(scenario())
    assert _state(f) == before


def test_large_legacy_lead_id_round_trips_as_opaque_text(directory):
    f = directory
    ident = '9007199254740993'
    _lead(f, int(ident), 'Opaque large cached lead', provider='connect')
    cookie = f['session'].issue(A, 82)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie, source='kommo', query={'legacy_deal_id': ident})
            assert status == 200 and len(page['data']) == 1, page
            assert page['data'][0]['lead_id'] == ident
            assert page['data'][0]['key'] == 'legacy-deal:' + ident
            assert page['data'][0]['kind'] == 'legacy_deal_context'
    asyncio.run(scenario())
    assert _state(f) == before


@pytest.mark.parametrize('actor,allowed', [(11, True), (81, True), (82, True), (89, False)])
def test_onboarding_pipeline_fallback_uses_current_server_configuration(directory, actor, allowed):
    f = directory
    with f['factory']() as conn:
        conn.execute('DELETE FROM saas_tenant_pipeline_stages WHERE tenant_id=%s::uuid', (A,))
        conn.execute('DELETE FROM saas_tenant_pipelines WHERE tenant_id=%s::uuid', (A,))
        conn.execute("UPDATE saas_tenants SET onboarding='{\"pipeline\":{\"selected\":[{\"id\":10,\"stage_ids\":[101]}]}}' WHERE id=%s::uuid", (A,))
    cookie = f['session'].issue(A, actor)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, page = await _get(http, cookie, source='kommo')
            assert status == (200 if allowed else 403), page
            if allowed:
                assert page['total'] == 5
                assert next(row for row in page['data'] if row['key'] == 'kommo:100:501')['lead_id'] == '102'
    asyncio.run(scenario())
    assert _state(f) == before


def _observe_sql(factory, *, before=None, after=None):
    """Timing observations only; every SQL statement executes on native PG."""
    class Cursor:
        def __init__(self, cursor): self.cursor = cursor
        def __enter__(self): self.cursor.__enter__(); return self
        def __exit__(self, *args): return self.cursor.__exit__(*args)
        def __getattr__(self, name): return getattr(self.cursor, name)
        def execute(self, query, *args, **kwargs):
            if before: before(str(query), self.cursor)
            result = self.cursor.execute(query, *args, **kwargs)
            if after: after(str(query), self.cursor)
            return result
    class Connection:
        def __init__(self, connection): self.connection = connection
        def __enter__(self): self.connection.__enter__(); return self
        def __exit__(self, *args): return self.connection.__exit__(*args)
        def __getattr__(self, name): return getattr(self.connection, name)
        def cursor(self, *args, **kwargs): return Cursor(self.connection.cursor(*args, **kwargs))
    return lambda: Connection(factory())


def test_directory_cannot_combine_old_pipeline_ownership_with_new_stage_visibility(directory, monkeypatch):
    import tenant_crm_contact_listing as listing
    import tenant_member_store as members
    import tenant_workflow_store as workflow
    from tenant_policy import TenantPolicy
    f = directory
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_pipelines SET owner_telegram_id=89 WHERE tenant_id=%s::uuid AND pipeline_id='10'", (A,))
    profile = members.member(A, 89)
    assert TenantPolicy(profile).pipeline_scope() == [{'pipeline_id': 10, 'status_ids': [101]}]
    writes = []
    def after(query, _cursor):
        if 'FROM saas_tenant_pipelines WHERE' in query and not writes:
            writes.append(True)
            workflow.save_tenant_workflow_config(tenant_id=A, owner_id=11,
                pipelines=[{'pipeline_id': '10', 'name': 'Reassigned', 'active': True,
                            'owner_telegram_id': 11, 'settings': {}}],
                stages=[{'pipeline_id': '10', 'stage_id': '102', 'name': 'Now visible',
                         'settings': {'visible': True}}])
            assert TenantPolicy(members.member(A, 89)).pipeline_scope() == []
    monkeypatch.setattr(listing, '_connect', _observe_sql(f['factory'], after=after))
    page = listing.directory(profile, source='kommo', search='FORBIDDEN hidden-stage fields')
    assert writes == [True]
    assert page['data'] == [] and page['total'] == 0
    # Stage 102 is inaccessible in both complete policy states. A mixture of
    # the old owner and newly visible stages must never expose saved lead104.


def test_waiting_directory_retries_current_revoked_actor_from_a_fresh_snapshot(directory, monkeypatch):
    import tenant_crm_contact_listing as listing
    f = directory
    cookie = f['session'].issue(A, 82)
    reader_pids = []
    def factory():
        conn = f['factory']()
        reader_pids.append(conn.info.backend_pid)
        return conn
    monkeypatch.setattr(listing, '_connect', factory)
    holder = f['factory']()
    holder.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=82 FOR UPDATE', (A,))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            request = asyncio.create_task(_get(http, cookie, source='kommo'))
            try:
                deadline = time.monotonic() + 8
                blocked = False
                while time.monotonic() < deadline:
                    if reader_pids:
                        with f['factory']() as observer:
                            row = observer.execute('SELECT pg_blocking_pids(%s) AS blockers', (reader_pids[0],)).fetchone()
                            blocked = holder.info.backend_pid in row['blockers']
                    if blocked: break
                    await asyncio.sleep(.02)
                assert blocked, 'The real reader must wait on the held membership row'
                holder.execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=82", (A,))
                holder.commit()
                status, response = await asyncio.wait_for(request, 8)
                assert status == 403 and response['code'] == 'contacts_forbidden', response
                assert len(reader_pids) == 2 and len(set(reader_pids)) == 2
                assert 'data' not in response
            finally:
                holder.rollback()
                if not request.done():
                    request.cancel()
    try: asyncio.run(scenario())
    finally: holder.close()


def test_repeated_real_snapshot_conflicts_fail_unavailable_without_returning_partial_contacts(directory, monkeypatch):
    import tenant_crm_contact_listing as listing
    f = directory
    cookie = f['session'].issue(A, 82)
    conflicts = []
    def before(query, _cursor):
        if 'SELECT * FROM saas_tenant_members WHERE' in query and 'FOR SHARE' in query:
            # An actual committed row revision after each RR snapshot causes
            # PostgreSQL 40001 at the production SHARE fence; no exception or
            # authority function is manufactured by this timing observer.
            conflicts.append(True)
            with f['factory']() as writer:
                writer.execute('UPDATE saas_tenant_members SET display_name=%s WHERE tenant_id=%s::uuid AND telegram_id=82',
                               ('Concurrent name revision ' + str(len(conflicts)), A))
    monkeypatch.setattr(listing, '_connect', _observe_sql(f['factory'], before=before))
    async def scenario():
        async with TestClient(TestServer(_app(f))) as http:
            status, response = await _get(http, cookie, source='kommo')
            assert status == 503 and response['code'] == 'contacts_unavailable', response
            assert len(conflicts) == 2 and 'data' not in response
    asyncio.run(scenario())


@pytest.mark.parametrize('query,status', [
    ({'expected_tenant_id': A, 'expected_user_id': '88'}, 409),
    ({'expected_tenant_id': B, 'expected_user_id': '11'}, 409),
    ({'expected_tenant_id': B}, 400),
    ({'expected_tenant_id': B, 'expected_user_id': 'bad'}, 400),
    ([('expected_tenant_id', B), ('expected_tenant_id', B), ('expected_user_id', '88')], 400),
])
def test_related_company_and_deal_reads_and_writes_reject_stale_contact_frame_identity(directory, query, status):
    import tenant_crm_native_companies as companies
    import tenant_crm_native_deals as deals
    f = directory
    company = companies.create_company(B, 'B PRIVATE RELATED COMPANY')
    deal = deals.create_deal(B, 'B PRIVATE RELATED DEAL', '10', '101', contact_id=FOREIGN_NATIVE)
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET permissions='[\"customers\",\"deals\"]' WHERE tenant_id=%s::uuid AND telegram_id=88", (B,))
    cookie = f['session'].issue(B, 88)
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app(f, allow_related_writes=True))) as http:
            for resource, ident in (('companies', str(company['id'])), ('deals', str(deal['id']))):
                base = '/api/platform/crm/native/' + resource
                # The same real cookie can read its own existing data; only
                # the immutable A-frame expectation makes this read invalid.
                good, data = await _get(http, cookie, path=base, listing=False)
                assert good == 200 and 'B PRIVATE RELATED' in json.dumps(data)
                for path in (base, base + '/' + ident):
                    actual, data = await _get(http, cookie, path=path, listing=False, query=query)
                    assert actual == status and data['code'], data
                    assert 'B PRIVATE RELATED' not in json.dumps(data)
                    actual, data = await _post(http, cookie, {'action': 'archive'}, path=path, query=query)
                    assert actual == status and data['code'], data
                if resource == 'deals':
                    options = list(query.items()) if isinstance(query, dict) else list(query)
                    actual, data = await _get(http, cookie, path=base, listing=False, query=options + [('options', '1')])
                    assert actual == status and data['code'], data
                else:
                    actual, data = await _post(http, cookie, {'action': 'link_contact', 'entity_id': FOREIGN_NATIVE},
                        path=base + '/' + ident, query=query)
                    assert actual == status and data['code'], data
    asyncio.run(scenario())
    assert _state(f) == before


@pytest.mark.parametrize('mode,actor,width,language', [
    ('ready', 11, 360, 'ru'), ('ready', 81, 390, 'az'),
    ('ready', 85, 768, 'en'), ('ready', 84, 1440, 'ru'),
    ('states', 81, 390, 'en'), ('workflow_revoke', 82, 390, 'ru'),
    ('tenant_switch', 88, 390, 'en'), ('route_aba', 82, 390, 'en'),
    ('empty', 81, 390, 'en'),
    ('native403_view', 81, 390, 'en'), ('native403_update', 81, 390, 'en'),
    ('route_origin', 88, 390, 'en'),
])
def test_actual_contacts_directory_app_uses_current_cookie_postgres_and_cached_history(directory, mode, actor, width, language):
    """Required full /app bridge: authority APIs and all projections stay real."""
    from tenant_web_assets import register_assets
    f = directory
    assert (ROOT / '.support-test-deps/node_modules/playwright').exists(), 'Required browser dependency is missing'
    for index in range(56):
        contacts.create_contact(A, 'Browser page person %02d' % index, '+99450999%04d' % index,
                                contact_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'crm01-browser-page-' + str(index))))
    if mode == 'ready' and actor == 11:
        import tenant_crm_native_deals as deals
        deals.create_deal(A, 'Synthetic contact relation deal', '10', '101',
                          responsible_id=11, description='Preserve all non-contact fields', deal_id=RELATION_DEAL)
        f['allowed_posts'] = {'/api/platform/crm/native/deals/' + RELATION_DEAL}
    if mode == 'workflow_revoke':
        with f['factory']() as conn:
            conn.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'10','103','Other visible stage')", (A,))
    if mode == 'empty':
        # A true empty current-tenant dataset, before any browser read, is a
        # different UI state from a nonempty dataset filtered to no matches.
        with f['factory']() as conn:
            conn.execute('UPDATE saas_crm_native_contacts SET archived_at=now() WHERE tenant_id=%s::uuid', (A,))
    tokens = {name: f['session'].issue(tenant, identity) for name, tenant, identity in (
        ('initial', A, actor), ('company_b', B, 88))}
    before = _state(f)
    state = {'hold': None, 'pending': 0, 'released': 0, 'fault': False, 'write_fault': False, 'changed': False,
             'read_paths': [], 'held_paths': []}
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    async def start():
        state['release'] = asyncio.Event()
        app = _app(f, origin)
        # Capture transport boundaries after the actual real-DB handler has
        # computed its response. A delayed body never replaces authority.
        @web.middleware
        async def observe(request, handler):
            response = await handler(request)
            if state['write_fault'] and request.method == 'POST' and request.path == '/api/platform/crm/native/contacts' and response.status == 200:
                state['write_fault'] = False
                # Only the HTTP confirmation is lost; the real handler has
                # committed the isolated contact and supplied its UUID.
                return web.json_response({})
            if request.path.startswith('/api/platform/crm/native/contacts') and request.method == 'GET':
                state['read_paths'].append(request.path_qs)
                exact = request.query.get('row_key') or request.query.get('legacy_deal_id')
                native = request.match_info.get('id')
                selected = ('card' if exact or native else 'list')
                if state['hold'] == selected and (selected == 'list' or exact == 'kommo:100:501' or native == NATIVE[0]):
                    state['hold'] = None
                    # Send genuine status/headers first, then hold its exact
                    # serialized DB response body. The browser has a real
                    # pending json() while current policy/identity changes.
                    streamed = web.StreamResponse(status=response.status,
                        headers={key: value for key, value in response.headers.items() if key.lower() != 'content-length'})
                    await streamed.prepare(request)
                    state['pending'] += 1
                    state['held_paths'].append(request.path_qs)
                    await state['release'].wait()
                    await streamed.write(response.body)
                    await streamed.write_eof()
                    state['released'] += 1
                    return streamed
                if selected == 'list' and state['fault']:
                    state['fault'] = False
                    # A simulated lost HTTP response exercises the UI's
                    # transport error state, after genuine DB authorization.
                    return web.json_response({'success': False, 'error': 'isolated_transport_fault'}, status=503)
            return response
        app.middlewares.insert(0, observe)
        auth, _ = _apis(f, origin)
        import tenant_crm_native_task_api as task_api
        app.router.add_get('/api/platform/account', auth['account'])
        app.router.add_get('/api/platform/sessions', auth['sessions'])
        app.router.add_get('/api/platform/crm/native/tasks', task_api.handler(f['session'].profile, origin, LOGGER))
        async def index(_request): return web.FileResponse(ROOT / 'docs/tenant-app.html')
        app.router.add_get('/app', index)
        register_assets(app)

        async def control(request):
            command = request.match_info['command']
            if command == 'session':
                token = tokens.get(request.query.get('identity', 'initial'))
                if token is None: raise web.HTTPBadRequest()
                response = web.json_response({'success': True})
                response.set_cookie(COOKIE_NAME, token, httponly=True, samesite='Lax', path='/')
                return response
            if command == 'hold':
                state['hold'] = request.query.get('kind')
                state['pending'] = state['released'] = 0
                state['held_paths'] = []
                state['release'].clear()
            elif command == 'release':
                state['hold'] = None
                state['release'].set()
            elif command == 'fault': state['fault'] = True
            elif command == 'malformed-write':
                assert mode == 'ready' and actor == 11
                state['write_fault'] = True
            elif command == 'native-revoke':
                assert mode in ('native403_view', 'native403_update')
                grants = [] if mode == 'native403_view' else ['customers', 'customers:view']
                with f['factory']() as conn:
                    conn.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=%s', (json.dumps(grants), A, actor))
                state['changed'] = True
            elif command == 'workflow-revoke':
                assert mode == 'workflow_revoke' and state['pending']
                with f['factory']() as conn:
                    conn.execute("UPDATE saas_tenant_pipeline_stages SET settings='{\"visible\":false}' WHERE tenant_id=%s::uuid AND pipeline_id='10' AND stage_id='101'", (A,))
                state['changed'] = True
            elif command != 'state': raise web.HTTPNotFound()
            return web.json_response({key: state[key] for key in ('pending', 'released', 'changed', 'read_paths', 'held_paths')})

        async def unrelated(request):
            # Only unrelated app startup consumers have empty fixture data.
            # Contacts, companies, deals, session and /me routes never land here.
            assert '/crm/native/' not in request.path
            if request.path == '/api/platform/ai/dialogs':
                return web.json_response({'success': True, 'result': {'dialogs': [], 'channels': [], 'agents': [], 'next_offset': None, 'total_count': 0, 'unread_count': 0}})
            return web.json_response({'success': True, 'result': {}, 'tasks': [], 'deals': [], 'contacts': [], 'companies': [], 'pipelines': [], 'members': [], 'integrations': [], 'notifications': [], 'unread_count': 0, 'requests': []})
        app.router.add_get('/test/{command}', control)
        app.router.add_get('/api/{tail:.*}', unrelated)
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
    assert ready.wait(10), 'The isolated HTTP fixture did not start'
    try:
        env = {**os.environ, 'CONTACTS_RUNTIME_ORIGIN': origin, 'CONTACTS_RUNTIME_MODE': mode,
               'CONTACTS_RUNTIME_ACTOR': str(actor), 'CONTACTS_RUNTIME_WIDTH': str(width),
               'CONTACTS_RUNTIME_LANGUAGE': language, 'CONTACTS_RUNTIME_TENANT_A': A,
               'CONTACTS_RUNTIME_TENANT_B': B, 'CONTACTS_RUNTIME_NATIVE_ID': NATIVE[0],
               'CONTACTS_RUNTIME_RELATION_DEAL_ID': RELATION_DEAL}
        completed = subprocess.run(['node', str(ROOT / 'tests/contacts_directory_http_browser.cjs')],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=80)
        assert completed.returncode == 0, completed.stdout + '\n' + completed.stderr
        assert f['blocked'] == []
        if mode == 'workflow_revoke':
            assert state['changed'] is True
            with f['factory']() as conn:
                conn.execute("UPDATE saas_tenant_pipeline_stages SET settings='{}' WHERE tenant_id=%s::uuid AND pipeline_id='10' AND stage_id='101'", (A,))
        if mode in ('native403_view', 'native403_update'):
            assert state['changed'] is True
            with f['factory']() as conn:
                conn.execute("UPDATE saas_tenant_members SET permissions='[\"customers\"]' WHERE tenant_id=%s::uuid AND telegram_id=%s", (A, actor))
        after = _state(f)
        changed = {key for key in after if before[key] != after[key]}
        if mode == 'ready' and actor == 11:
            assert changed == {'saas_crm_native_contacts', 'saas_crm_native_deals'}, changed
            with f['factory']() as conn:
                row = conn.execute("SELECT display_name,archived_at FROM saas_crm_native_contacts WHERE tenant_id=%s::uuid AND email='browser-isolated@native.example'", (A,)).fetchone()
                assert row and row['display_name'] == 'Browser isolated edited' and row['archived_at'] is not None
            assert len(f['posts']) == 5
            previous_deal = [json.loads(row) for row in before['saas_crm_native_deals']]
            current_deal = [json.loads(row) for row in after['saas_crm_native_deals']]
            assert len(previous_deal) == len(current_deal) == 1
            previous_deal[0].pop('updated_at')
            current_deal[0].pop('updated_at')
            assert current_deal == previous_deal, 'Attach/detach preserves every other deal field'
            relation_posts = [post for post in f['posts'] if post['path'].endswith(RELATION_DEAL)]
            assert len(relation_posts) == 2
            created_id = next(post['data']['id'] for post in f['posts'] if post['path'].endswith('/contacts'))
            assert [post['data']['contact_id'] for post in relation_posts] == [created_id, None]
        else:
            assert changed == set(), changed
            if mode in ('native403_view', 'native403_update'):
                assert len(f['posts']) == 1 and f['posts'][0]['path'].endswith(NATIVE[0])
            else:
                assert f['posts'] == []
    finally:
        loop.call_soon_threadsafe(state['release'].set)
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()
        assert not thread.is_alive()
