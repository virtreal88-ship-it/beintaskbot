"""Native CRM scopes against isolated localhost PostgreSQL, never providers."""
import json
import uuid

import pytest
from psycopg.rows import dict_row
import psycopg

from test_company_employees_navigation_postgres import A, B, database, db, staff
import tenant_crm_native_contacts as contacts
import tenant_crm_native_companies as companies
import tenant_crm_native_deals as deals
import tenant_crm_contact_listing as listing
import tenant_member_store as members
from tenant_crm_native_access import CRMAccessError

KINDS = ('customers', 'companies', 'deals')
SERVICES = {'customers': contacts, 'companies': companies, 'deals': deals}
TABLES = {'customers': 'saas_crm_native_contacts', 'companies': 'saas_crm_native_companies',
          'deals': 'saas_crm_native_deals'}


def set_scopes(f, actor, matrix):
    with f['factory']() as conn:
        conn.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
          VALUES(%s::uuid,'members','{}') ON CONFLICT DO NOTHING''', (A,))
        conn.execute('''UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,%s::text[],%s::jsonb)
          WHERE tenant_id=%s::uuid AND policy_key='members' ''',
          ([str(actor)], json.dumps({'access_scopes': matrix}), A))


@pytest.fixture
def native(staff, monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    f = staff
    for service in (*SERVICES.values(), listing):
        monkeypatch.setattr(service, '_connect', f['factory'])
    ids = {kind: {key: str(uuid.uuid4()) for key in ('self', 'peer', 'other', 'system', 'foreign')} for kind in KINDS}
    departments = [str(uuid.uuid4()), str(uuid.uuid4())]
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenants SET modules='{\"customers\":true,\"deals\":true}'")
        for i, dept in enumerate(departments):
            conn.execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s::uuid,%s::uuid,%s)', (A, dept, 'Department '+str(i)))
        for actor, dept in ((81, departments[0]), (82, departments[0]), (84, departments[1])):
            conn.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions,department_id)
              VALUES(%s::uuid,%s,'admin',%s,'["customers","deals"]',%s::uuid)''',
              (A, actor, 'Actor '+str(actor), dept))
        for tenant in (A, B):
            conn.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s::uuid,'p','Pipeline')", (tenant,))
            conn.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'p','s','Stage')", (tenant,))
        for key, actor in (('self', 81), ('peer', 82), ('other', 84), ('system', None), ('foreign', 22)):
            tenant = B if key == 'foreign' else A
            for kind in KINDS:
                columns = 'display_name,phone' if kind == 'customers' else 'name' if kind == 'companies' else 'title,pipeline_id,stage_id'
                values = [key, 'phone-'+key] if kind == 'customers' else [key] if kind == 'companies' else [key, 'p', 's']
                placeholders = ','.join(['%s'] * len(values))
                conn.execute(f'''INSERT INTO {TABLES[kind]}(tenant_id,id,access_responsible_id,{columns})
                  VALUES(%s::uuid,%s::uuid,%s,{placeholders})''', [tenant,ids[kind][key],actor,*values])
    f.update(ids=ids, departments=departments)
    return f


def configure(f, kind, action, scope):
    matrix = {module: {op: 'none' for op in ('create', 'view', 'update', 'delete')} for module in KINDS}
    matrix[kind][action] = scope
    set_scopes(f, 81, matrix)


def read(kind, tenant, ident, actor):
    function = {'customers': contacts.get_contact, 'companies': companies.get_company, 'deals': deals.get_deal}[kind]
    return function(tenant, ident, actor_id=actor)


def update(kind, ident):
    if kind == 'customers': return contacts.update_contact(A, ident, 'changed', actor_id=81)
    if kind == 'companies': return companies.update_company(A, ident, 'changed', actor_id=81)
    return deals.update_deal(A, ident, 'changed', 'p', 's', actor_id=81)


def create(kind, **kwargs):
    if kind == 'customers': return contacts.create_contact(A, 'new', actor_id=81, **kwargs)
    if kind == 'companies': return companies.create_company(A, 'new', actor_id=81, **kwargs)
    return deals.create_deal(A, 'new', 'p', 's', actor_id=81, **kwargs)


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('action', ('create', 'view', 'update', 'delete'))
@pytest.mark.parametrize('scope,visible', [('none', set()), ('self', {'self'}), ('department', {'self','peer'}), ('all', {'self','peer','other','system'})])
def test_independent_crud_scopes_and_two_tenant_boundary(native, kind, action, scope, visible):
    f = native
    configure(f, kind, action, scope)
    if action == 'create':
        if scope == 'none':
            with pytest.raises(CRMAccessError): create(kind)
        else:
            row = create(kind)
            with f['factory']() as conn:
                owner = conn.execute(f'SELECT access_responsible_id FROM {TABLES[kind]} WHERE id=%s::uuid', (row['id'],)).fetchone()
            assert owner['access_responsible_id'] == 81
            assert 'access_responsible_id' not in row
        return
    if scope == 'none':
        with pytest.raises(CRMAccessError):
            if action == 'view': read(kind, A, f['ids'][kind]['self'], 81)
            elif action == 'update': update(kind, f['ids'][kind]['self'])
            else: getattr(SERVICES[kind], 'archive_' + ('contact' if kind=='customers' else 'company' if kind=='companies' else 'deal'))(A, f['ids'][kind]['self'], actor_id=81)
        return
    for key, ident in f['ids'][kind].items():
        if action == 'view': result = read(kind, A, ident, 81)
        elif action == 'update': result = update(kind, ident)
        else:
            function = {'customers': contacts.archive_contact, 'companies': companies.archive_company, 'deals': deals.archive_deal}[kind]
            result = function(A, ident, actor_id=81)
        assert bool(result) == (key in visible), (kind, action, scope, key, result)
    with pytest.raises(CRMAccessError):
        # Same actor ID is not membership in another tenant.
        read(kind, B, f['ids'][kind]['foreign'], 81)


@pytest.mark.parametrize('kind', KINDS)
def test_lists_search_and_actions_apply_scope_before_limit(native, kind):
    configure(native, kind, 'view', 'department')
    matrix = {kind: {'view':'department','update':'self','delete':'none','create':'self'}}
    set_scopes(native, 81, matrix)
    function = {'customers': contacts.list_contacts, 'companies': companies.list_companies, 'deals': deals.list_deals}[kind]
    rows = function(A, actor_id=81)
    assert {str(row['id']) for row in rows} == {native['ids'][kind]['self'], native['ids'][kind]['peer']}
    assert function(A, search='other', actor_id=81) == []
    for row in rows:
        assert row['actions']['update'] == (str(row['id']) == native['ids'][kind]['self'])
        assert row['actions']['archive'] is False


def test_contact_directory_counts_filtered_before_pagination(native):
    configure(native, 'customers', 'view', 'department')
    profile = members.member(A, 81)
    result = listing.directory(profile, source='native', limit=1)
    assert result['total'] == result['source_total'] == 2
    assert len(result['data']) == 1 and result['next_offset'] == 1
    assert listing.directory(profile, source='native', search='other')['total'] == 0
    with pytest.raises(listing.ContactsDirectoryError): listing.directory(profile, source='kommo')


def test_deal_keeps_linked_client_but_no_client_directory_or_edit(native):
    ids = native['ids']
    with native['factory']() as conn:
        conn.execute('UPDATE saas_crm_native_deals SET contact_id=%s::uuid,company_id=%s::uuid WHERE id=%s::uuid',
                     (ids['customers']['other'], ids['companies']['other'], ids['deals']['self']))
    configure(native, 'deals', 'view', 'self')
    row = deals.get_deal(A, ids['deals']['self'], actor_id=81)
    assert row['contact_name'] == row['company_name'] == 'other'
    assert row['contact_phone'] == 'phone-other'
    assert deals.deal_options(A, actor_id=81)['contacts'] == []
    with pytest.raises(CRMAccessError): contacts.get_contact(A, ids['customers']['other'], actor_id=81)
    with pytest.raises(CRMAccessError): contacts.update_contact(A, ids['customers']['other'], 'unauthorized', actor_id=81)


def test_company_related_lists_counts_and_link_mutations_do_not_expand_scope(native):
    ids = native['ids']
    with native['factory']() as conn:
        for kind in ('customers', 'deals'):
            conn.execute(f'UPDATE {TABLES[kind]} SET company_id=%s::uuid WHERE tenant_id=%s::uuid', (ids['companies']['self'], A))
    set_scopes(native, 81, {kind:{'view':'self','update':'self','delete':'self'} for kind in KINDS})
    row = companies.get_company(A, ids['companies']['self'], actor_id=81)
    assert len(row['contacts']) == len(row['deals']) == 1
    rows = companies.list_companies(A, actor_id=81)
    assert rows[0]['contact_count'] == rows[0]['deal_count'] == 1
    for function, company, kind, entity in (
            (companies.link_entity, 'self', 'deal', 'peer'),
            (companies.unlink_entity, 'self', 'deal', 'peer')):
        module = 'customers' if kind == 'contact' else 'deals'
        assert function(A, ids['companies'][company], kind, ids[module][entity], actor_id=81)


def test_deal_contact_links_require_only_the_deal_update_scope(native):
    ids = native['ids']
    with native['factory']() as conn:
        conn.execute('UPDATE saas_crm_native_deals SET contact_id=%s::uuid WHERE id=%s::uuid',
                     (ids['customers']['other'], ids['deals']['self']))
    configure(native, 'deals', 'update', 'self')
    # A normal edit preserves existing context even when client section is closed.
    assert deals.update_deal(A, ids['deals']['self'], 'changed', 'p', 's',
                             contact_id=ids['customers']['other'], actor_id=81)
    assert deals.update_deal(A, ids['deals']['self'], 'changed', 'p', 's',
                            contact_id=ids['customers']['self'], actor_id=81)
    assert deals.update_deal(A, ids['deals']['self'], 'changed', 'p', 's', actor_id=81)
    assert deals.update_deal(A, ids['deals']['peer'], 'changed', 'p', 's',
                            contact_id=ids['customers']['self'], actor_id=81) is None
    with pytest.raises(ValueError, match='contact unavailable'):
        deals.update_deal(A, ids['deals']['self'], 'changed', 'p', 's',
                          contact_id=ids['customers']['foreign'], actor_id=81)


def test_department_change_deactivation_and_suspension_take_effect(native):
    configure(native, 'customers', 'view', 'department')
    assert contacts.get_contact(A, native['ids']['customers']['peer'], actor_id=81)
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=82', (native['departments'][1], A))
    assert contacts.get_contact(A, native['ids']['customers']['peer'], actor_id=81) is None
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=81', (A,))
    with pytest.raises(CRMAccessError): contacts.list_contacts(A, actor_id=81)
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET active=TRUE WHERE tenant_id=%s::uuid AND telegram_id=81', (A,))
        conn.execute("UPDATE saas_tenants SET status='suspended' WHERE id=%s::uuid", (A,))
    with pytest.raises(CRMAccessError): contacts.list_contacts(A, actor_id=81)


def test_create_replay_cannot_return_someone_elses_record(native):
    for kind in KINDS:
        configure(native, kind, 'create', 'all')
        key = 'contact_id' if kind == 'customers' else 'company_id' if kind == 'companies' else 'deal_id'
        with pytest.raises(ValueError): create(kind, **{key:native['ids'][kind]['peer']})


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('operation', ('view', 'create', 'update', 'delete'))
def test_http_reloads_authority_and_never_trusts_request_profile(native, kind, operation):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    import tenant_crm_native_api as contacts_api
    import tenant_crm_native_company_api as companies_api
    import tenant_crm_native_deal_api as deals_api
    api = {'customers':contacts_api, 'companies':companies_api, 'deals':deals_api}[kind]
    configure(native, kind, operation, 'all')
    stale_profile = members.member(A, 81)
    configure(native, kind, operation, 'none')
    data = ({'title':'new','pipeline_id':'p','stage_id':'s'} if kind == 'deals' else {'name':'new'})
    if operation == 'update': data['action'] = 'update'
    if operation == 'delete': data = {'action':'archive'}
    async def body(): return data
    request = SimpleNamespace(method='GET' if operation=='view' else 'POST',
        match_info={} if operation=='create' else {'id':native['ids'][kind]['self']},
        query={}, content_type='application/json', headers={'Origin':'https://test.invalid'}, json=body)
    with patch('tenant_crm_sync_outbox.enqueue_change') as enqueue:
        response = asyncio.run(api.handler(lambda _: stale_profile, 'https://test.invalid', MagicMock())(request))
    assert response.status == 403
    enqueue.assert_not_called()


def test_permission_writer_wins_before_database_mutation(native, monkeypatch):
    import concurrent.futures
    import threading
    from test_contacts_directory_postgres import _observe_sql
    configure(native, 'customers', 'update', 'self')
    entered = threading.Event()
    def before(sql, cursor):
        if 'SELECT policy_key' in sql and 'FOR SHARE' in sql:
            entered.set()
    monkeypatch.setattr(contacts, '_connect', _observe_sql(native['factory'], before=before))
    with native['factory']() as writer, concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute('''UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,
          '{81,access_scopes,customers,update}','"none"') WHERE tenant_id=%s::uuid AND policy_key='members' ''', (A,))
        future = pool.submit(contacts.update_contact, A, native['ids']['customers']['self'], 'must not save', actor_id=81)
        assert entered.wait(5)
        assert not future.done()
        writer.commit()
        with pytest.raises(CRMAccessError): future.result(timeout=5)
    with native['factory']() as conn:
        row = conn.execute('SELECT display_name FROM saas_crm_native_contacts WHERE id=%s::uuid',
                           (native['ids']['customers']['self'],)).fetchone()
    assert row['display_name'] == 'self'


@pytest.mark.parametrize('kind', KINDS)
def test_department_creation_without_department_fails_closed(native, kind):
    configure(native, kind, 'create', 'department')
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET department_id=NULL WHERE tenant_id=%s::uuid AND telegram_id=81', (A,))
    with pytest.raises(CRMAccessError): create(kind)


@pytest.mark.parametrize('kind', ('companies', 'deals'))
def test_operational_responsible_does_not_reassign_access_ownership(native, kind):
    configure(native, kind, 'update', 'self')
    ident = native['ids'][kind]['self']
    if kind == 'companies': row = companies.update_company(A, ident, 'edited', responsible_id=84, actor_id=81)
    else: row = deals.update_deal(A, ident, 'edited', 'p', 's', responsible_id=84, actor_id=81)
    assert row['responsible_id'] == 84
    with native['factory']() as conn:
        row = conn.execute(f'SELECT access_responsible_id FROM {TABLES[kind]} WHERE id=%s::uuid', (ident,)).fetchone()
    assert row['access_responsible_id'] == 81


def test_company_create_dedupe_does_not_bypass_read_none(native, monkeypatch):
    configure(native, 'companies', 'create', 'self')
    ident = str(uuid.uuid4())
    first = companies.create_company(A, 'Private old name', '+994000001', 'private@example.invalid',
        'Private old description', company_id=ident, actor_id=81)
    with pytest.raises(ValueError, match='company already exists'):
        companies.create_company(A, 'Lookup by phone', '+994000001', actor_id=81)
    with pytest.raises(ValueError, match='company already exists'):
        companies.create_company(A, 'Lookup by email', '', 'private@example.invalid',
            company_id=ident, actor_id=81)
    replay = companies.create_company(A, 'Private old name', '+994000001', 'private@example.invalid',
        'Private old description', company_id=ident, actor_id=81)
    assert str(replay['id']) == str(first['id']) == ident
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    legacy = companies.create_company(A, 'Legacy phone dedupe', '+994000001', actor_id=81)
    assert str(legacy['id']) == ident


@pytest.mark.parametrize('scope,allowed', [('none',set()),('self',{'self'}),('department',{'self','peer'}),('all',{'self','peer','other','system'})])
def test_contact_link_and_unlink_use_only_contact_update_scope(native, scope, allowed):
    configure(native, 'customers', 'update', scope)
    target = native['ids']['deals']['other']
    for label, contact in native['ids']['customers'].items():
        if scope == 'none':
            with pytest.raises(CRMAccessError):
                contacts.set_contact_deal_link(A, contact, target, actor_id=81)
            continue
        receipt = contacts.set_contact_deal_link(A, contact, target, actor_id=81)
        assert bool(receipt) == (label in allowed)
        if receipt:
            assert set(receipt) == {'id','deal_id','linked'}
            assert receipt['linked'] is True
            assert contacts.set_contact_deal_link(A, contact, target, unlink=True, actor_id=81)['linked'] is False
    if scope != 'none':
        assert contacts.set_contact_deal_link(A, native['ids']['customers']['self'], native['ids']['deals']['foreign'], actor_id=81) is None


def test_contact_unlink_cannot_remove_another_contacts_relation(native):
    configure(native, 'customers', 'update', 'self')
    with native['factory']() as conn:
        conn.execute('UPDATE saas_crm_native_deals SET contact_id=%s::uuid WHERE id=%s::uuid',
                     (native['ids']['customers']['peer'], native['ids']['deals']['other']))
    assert contacts.set_contact_deal_link(A, native['ids']['customers']['self'], native['ids']['deals']['other'], unlink=True, actor_id=81) is None
    with native['factory']() as conn:
        row = conn.execute('SELECT contact_id FROM saas_crm_native_deals WHERE id=%s::uuid', (native['ids']['deals']['other'],)).fetchone()
    assert str(row['contact_id']) == native['ids']['customers']['peer']


def test_contact_relation_http_rechecks_current_update_and_rejects_extra_deal_fields(native):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    import tenant_crm_native_api as api
    configure(native, 'customers', 'update', 'self')
    stale = members.member(A, 81)
    data = {'action':'link_deal','deal_id':native['ids']['deals']['other']}
    async def body(): return data
    request = SimpleNamespace(method='POST',match_info={'id':native['ids']['customers']['self']},
        query={}, content_type='application/json',headers={'Origin':'https://test.invalid'},json=body)
    with patch('tenant_crm_sync_outbox.enqueue_change') as enqueue:
        response=asyncio.run(api.handler(lambda _: stale,'https://test.invalid',MagicMock())(request))
        assert response.status == 200
        assert set(json.loads(response.text)['data']) == {'id','deal_id','linked'}
        enqueue.assert_not_called()
        data['title']='must not be changed'
        assert asyncio.run(api.handler(lambda _: stale,'https://test.invalid',MagicMock())(request)).status == 400
        del data['title']
        configure(native, 'customers', 'update', 'none')
        assert asyncio.run(api.handler(lambda _: stale,'https://test.invalid',MagicMock())(request)).status == 403


def test_scoped_initial_contact_selection_requires_contact_view(native):
    configure(native, 'deals', 'create', 'self')
    with pytest.raises(CRMAccessError, match='contact_unavailable'):
        deals.create_deal(A, 'New', 'p', 's', contact_id=native['ids']['customers']['self'], actor_id=81)


@pytest.mark.parametrize('scope,allowed', [('none',set()),('self',{'self'}),('department',{'self','peer'}),('all',{'self','peer','other','system'})])
def test_contact_company_link_uses_only_contact_update_scope(native, scope, allowed):
    # Every companies cell and the contact C/R/D cells are explicitly denied.
    configure(native, 'customers', 'update', scope)
    target = native['ids']['companies']['other']
    with native['factory']() as conn:
        original = conn.execute('SELECT * FROM saas_crm_native_companies WHERE id=%s::uuid', (target,)).fetchone()
    for label, contact in native['ids']['customers'].items():
        if scope == 'none':
            with pytest.raises(CRMAccessError):
                contacts.set_contact_company_link(A, contact, target, actor_id=81)
            continue
        with native['factory']() as conn:
            before = conn.execute('SELECT * FROM saas_crm_native_contacts WHERE id=%s::uuid', (contact,)).fetchone()
        receipt = contacts.set_contact_company_link(A, contact, target, actor_id=81)
        assert bool(receipt) == (label in allowed)
        if receipt:
            assert receipt == {'id':contact, 'company_id':target, 'linked':True}
            with native['factory']() as conn:
                after = conn.execute('SELECT * FROM saas_crm_native_contacts WHERE id=%s::uuid', (contact,)).fetchone()
            assert str(after['company_id']) == target
            assert {k:v for k,v in before.items() if k not in {'company_id','updated_at'}} == {
                k:v for k,v in after.items() if k not in {'company_id','updated_at'}}
            assert contacts.set_contact_company_link(A, contact, target, unlink=True, actor_id=81) == {
                'id':contact, 'company_id':target, 'linked':False}
    with native['factory']() as conn:
        unchanged = conn.execute('SELECT * FROM saas_crm_native_companies WHERE id=%s::uuid', (target,)).fetchone()
    assert original == unchanged, 'link/unlink never mutates a company, including updated_at'
    if scope != 'none':
        assert contacts.set_contact_company_link(A, native['ids']['customers']['self'],
            native['ids']['companies']['foreign'], actor_id=81) is None


def test_contact_company_unlink_requires_exact_active_target(native):
    configure(native, 'customers', 'update', 'self')
    contact = native['ids']['customers']['self']
    target = native['ids']['companies']['other']
    assert contacts.set_contact_company_link(A, contact, target, actor_id=81)
    assert contacts.set_contact_company_link(A, contact, native['ids']['companies']['peer'], unlink=True, actor_id=81) is None
    assert contacts.set_contact_company_link(A, contact, native['ids']['companies']['foreign'], unlink=True, actor_id=81) is None
    with native['factory']() as conn:
        conn.execute('UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid',
            (native['ids']['companies']['peer'],))
    assert contacts.set_contact_company_link(A, contact, native['ids']['companies']['peer'], actor_id=81) is None
    with native['factory']() as conn:
        row = conn.execute('SELECT company_id FROM saas_crm_native_contacts WHERE id=%s::uuid', (contact,)).fetchone()
        assert str(row['company_id']) == target
        conn.execute('UPDATE saas_crm_native_contacts SET archived_at=now() WHERE id=%s::uuid', (contact,))
    assert contacts.set_contact_company_link(A, contact, target, unlink=True, actor_id=81) is None


def test_contact_company_http_narrow_receipt_tenant_and_current_update(native):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    import tenant_crm_native_api as api
    configure(native, 'customers', 'update', 'self')
    stale = members.member(A, 81)
    contact = native['ids']['customers']['self']
    company = native['ids']['companies']['other']
    data = {'action':'link_company','company_id':company}
    async def body(): return data
    request = SimpleNamespace(method='POST',match_info={'id':contact},query={},
        content_type='application/json',headers={'Origin':'https://test.invalid'},json=body)
    handle = api.handler(lambda _: stale,'https://test.invalid',MagicMock())
    with patch('tenant_crm_sync_outbox.enqueue_change') as enqueue:
        response = asyncio.run(handle(request))
        assert response.status == 200
        assert json.loads(response.text)['data'] == {'id':contact,'company_id':company,'linked':True}
        enqueue.assert_not_called()
        data['name'] = 'must not change company'
        assert asyncio.run(handle(request)).status == 400
        del data['name']
        data['company_id'] = 'invalid-uuid'
        assert asyncio.run(handle(request)).status == 400
        data['company_id'] = native['ids']['companies']['foreign']
        assert asyncio.run(handle(request)).status == 404
        data['company_id'] = company
        data['action'] = 'unlink_company'
        response = asyncio.run(handle(request))
        assert response.status == 200
        assert json.loads(response.text)['data']['linked'] is False
        configure(native, 'customers', 'update', 'none')
        data['action'] = 'link_company'
        assert asyncio.run(handle(request)).status == 403
        enqueue.assert_not_called()




@pytest.mark.parametrize('scope,allowed', [('none',set()),('self',{'self'}),('department',{'self','peer'}),('all',{'self','peer','other','system'})])
def test_company_contact_link_uses_only_company_update_scope(native, scope, allowed):
    configure(native, 'companies', 'update', scope)
    target = native['ids']['customers']['other']
    with native['factory']() as conn:
        original = conn.execute('SELECT * FROM saas_crm_native_contacts WHERE id=%s::uuid', (target,)).fetchone()
        company_rows = conn.execute('SELECT * FROM saas_crm_native_companies ORDER BY id').fetchall()
    for label, company in native['ids']['companies'].items():
        if scope == 'none':
            with pytest.raises(CRMAccessError):
                companies.link_entity(A, company, 'contact', target, actor_id=81)
            with pytest.raises(CRMAccessError):
                companies.unlink_entity(A, company, 'contact', target, actor_id=81)
            continue
        receipt = companies.link_entity(A, company, 'contact', target, actor_id=81)
        assert bool(receipt) == (label in allowed)
        if receipt:
            assert set(receipt) == {'id'} and str(receipt['id']) == target
            with native['factory']() as conn:
                row = conn.execute('SELECT * FROM saas_crm_native_contacts WHERE id=%s::uuid', (target,)).fetchone()
            assert str(row['company_id']) == company
            assert {k:v for k,v in original.items() if k not in {'company_id','updated_at'}} == {
                k:v for k,v in row.items() if k not in {'company_id','updated_at'}}
            assert companies.unlink_entity(A, company, 'contact', target, actor_id=81)
    with native['factory']() as conn:
        assert conn.execute('SELECT * FROM saas_crm_native_companies ORDER BY id').fetchall() == company_rows
    if scope != 'none':
        assert companies.link_entity(A, native['ids']['companies']['self'], 'contact',
            native['ids']['customers']['foreign'], actor_id=81) is None
        assert companies.unlink_entity(A, native['ids']['companies']['self'], 'contact',
            native['ids']['customers']['foreign'], actor_id=81) is None


def test_company_contact_unlink_requires_exact_relation_and_active_rows(native):
    configure(native, 'companies', 'update', 'all')
    company, another = (native['ids']['companies'][key] for key in ('self','peer'))
    target = native['ids']['customers']['other']
    assert companies.link_entity(A, company, 'contact', target, actor_id=81)
    assert companies.unlink_entity(A, another, 'contact', target, actor_id=81) is None
    with native['factory']() as conn:
        conn.execute('UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid', (another,))
    assert companies.link_entity(A, another, 'contact', target, actor_id=81) is None
    with native['factory']() as conn:
        row = conn.execute('SELECT company_id FROM saas_crm_native_contacts WHERE id=%s::uuid', (target,)).fetchone()
        assert str(row['company_id']) == company
        conn.execute('UPDATE saas_crm_native_contacts SET archived_at=now() WHERE id=%s::uuid', (target,))
    assert companies.link_entity(A, company, 'contact', target, actor_id=81) is None
    assert companies.unlink_entity(A, company, 'contact', target, actor_id=81) is None


def test_company_contact_http_current_scope_tenant_and_narrow_payload(native):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import tenant_crm_native_company_api as api
    configure(native, 'companies', 'update', 'self')
    stale = members.member(A, 81)
    company, target = native['ids']['companies']['self'], native['ids']['customers']['other']
    data = {'action':'link_contact','entity_id':target}
    async def body(): return data
    request = SimpleNamespace(method='POST',match_info={'id':company},query={},
        content_type='application/json',headers={'Origin':'https://test.invalid'},json=body)
    handle = api.handler(lambda _: stale,'https://test.invalid',MagicMock())
    response = asyncio.run(handle(request))
    assert response.status == 200
    assert json.loads(response.text)['data'] == {'id':target}
    data['display_name'] = 'must not change contact'
    assert asyncio.run(handle(request)).status == 400
    del data['display_name']
    data['entity_id'] = 'invalid-uuid'
    assert asyncio.run(handle(request)).status == 400
    data['entity_id'] = native['ids']['customers']['foreign']
    assert asyncio.run(handle(request)).status == 404
    data['entity_id'] = target
    data['action'] = 'unlink_contact'
    assert asyncio.run(handle(request)).status == 200
    configure(native, 'companies', 'update', 'none')
    data['action'] = 'link_contact'
    assert asyncio.run(handle(request)).status == 403
    with native['factory']() as conn:
        assert conn.execute('SELECT company_id FROM saas_crm_native_contacts WHERE id=%s::uuid', (target,)).fetchone()['company_id'] is None


def test_company_contact_department_and_membership_changes_revoke_writes(native):
    configure(native, 'companies', 'update', 'department')
    company, target = native['ids']['companies']['peer'], native['ids']['customers']['other']
    assert companies.link_entity(A, company, 'contact', target, actor_id=81)
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=82',
            (native['departments'][1], A))
    assert companies.unlink_entity(A, company, 'contact', target, actor_id=81) is None
    configure(native, 'companies', 'update', 'all')
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=81', (A,))
    with pytest.raises(CRMAccessError): companies.unlink_entity(A, company, 'contact', target, actor_id=81)
    with native['factory']() as conn:
        assert str(conn.execute('SELECT company_id FROM saas_crm_native_contacts WHERE id=%s::uuid', (target,)).fetchone()['company_id']) == company


def test_company_contact_permission_writer_wins_before_relation_mutation(native, monkeypatch):
    import concurrent.futures
    import threading
    from test_contacts_directory_postgres import _observe_sql
    configure(native, 'companies', 'update', 'self')
    entered = threading.Event()
    def before(sql, cursor):
        if 'SELECT policy_key' in sql and 'FOR SHARE' in sql:
            entered.set()
    monkeypatch.setattr(companies, '_connect', _observe_sql(native['factory'], before=before))
    with native['factory']() as writer, concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute('''UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,
          '{81,access_scopes,companies,update}','"none"') WHERE tenant_id=%s::uuid AND policy_key='members' ''', (A,))
        future = pool.submit(companies.link_entity, A, native['ids']['companies']['self'], 'contact',
            native['ids']['customers']['other'], actor_id=81)
        assert entered.wait(5)
        assert not future.done()
        writer.commit()
        with pytest.raises(CRMAccessError): future.result(timeout=5)
    with native['factory']() as conn:
        assert conn.execute('SELECT company_id FROM saas_crm_native_contacts WHERE id=%s::uuid',
            (native['ids']['customers']['other'],)).fetchone()['company_id'] is None


def test_company_contact_gate_off_retains_legacy_update_and_delete(native, monkeypatch):
    configure(native, 'companies', 'update', 'none')
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    company, target = native['ids']['companies']['peer'], native['ids']['customers']['other']
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=81',
            (json.dumps(['customers:update']), A))
    assert companies.link_entity(A, company, 'contact', target, actor_id=81)
    with pytest.raises(CRMAccessError): companies.unlink_entity(A, company, 'contact', target, actor_id=81)
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=81',
            (json.dumps(['customers:delete']), A))
    with pytest.raises(CRMAccessError): companies.link_entity(A, company, 'contact', target, actor_id=81)
    assert companies.unlink_entity(A, company, 'contact', target, actor_id=81)


@pytest.mark.parametrize('kind', ['deal', 'company'])
@pytest.mark.parametrize('unlink', [False, True])
def test_gate_off_narrow_contact_relations_cannot_bypass_legacy_checks(native, monkeypatch, kind, unlink):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    import tenant_crm_native_api as api
    configure(native, 'customers', 'update', 'self')
    stale = members.member(A, 81)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    contact = native['ids']['customers']['self']
    target = native['ids']['deals' if kind == 'deal' else 'companies']['other']
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=81',
            (json.dumps(['customers:update']), A))
        if kind == 'deal':
            conn.execute('UPDATE saas_crm_native_deals SET contact_id=%s::uuid WHERE id=%s::uuid', (contact,target))
        else:
            conn.execute('UPDATE saas_crm_native_contacts SET company_id=%s::uuid WHERE id=%s::uuid', (target,contact))
    data = {'action':('unlink_' if unlink else 'link_') + kind, kind+'_id':target}
    async def body(): return data
    request = SimpleNamespace(method='POST',match_info={'id':contact},query={},
        content_type='application/json',headers={'Origin':'https://test.invalid'},json=body)
    with patch('tenant_crm_sync_outbox.enqueue_change') as enqueue:
        response = asyncio.run(api.handler(lambda _: stale,'https://test.invalid',MagicMock())(request))
    assert response.status == 403
    assert json.loads(response.text)['error'] == 'scoped_relation_unavailable'
    enqueue.assert_not_called()
    with native['factory']() as conn:
        if kind == 'deal':
            assert str(conn.execute('SELECT contact_id FROM saas_crm_native_deals WHERE id=%s::uuid', (target,)).fetchone()['contact_id']) == contact
        else:
            assert str(conn.execute('SELECT company_id FROM saas_crm_native_contacts WHERE id=%s::uuid', (contact,)).fetchone()['company_id']) == target


@pytest.mark.parametrize('direction', ['to_external','bidirectional'])
@pytest.mark.parametrize('route', ['contact_deal','contact_company','company_contact'])
@pytest.mark.parametrize('unlink', [False, True])
def test_scoped_relations_with_outbound_sync_fail_before_mutation_or_enqueue(native, route, unlink, direction):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch
    import tenant_crm_native_api as contact_api
    import tenant_crm_native_company_api as company_api
    source_company = route == 'company_contact'
    configure(native, 'companies' if source_company else 'customers', 'update', 'self')
    contact = native['ids']['customers']['other' if source_company else 'self']
    company = native['ids']['companies']['self' if source_company else 'other']
    deal = native['ids']['deals']['other']
    entity = 'deal' if route == 'contact_deal' else 'contact'
    with native['factory']() as conn:
        conn.execute('''INSERT INTO saas_crm_provider_settings
          (tenant_id,entity_type,provider,mode,primary_provider,sync_direction,allowed_fields)
          VALUES(%s::uuid,%s,'kommo','selective','kommo',%s,%s::jsonb)''',
          (A,entity,direction,json.dumps(['title','description'] if entity=='deal' else ['name','phone','email'])))
        conn.execute('UPDATE saas_crm_native_contacts SET company_id=%s::uuid WHERE id=%s::uuid', (company,contact))
        conn.execute('UPDATE saas_crm_native_deals SET contact_id=%s::uuid WHERE id=%s::uuid', (contact,deal))
        before_contact = conn.execute('SELECT * FROM saas_crm_native_contacts WHERE id=%s::uuid', (contact,)).fetchone()
        before_deal = conn.execute('SELECT * FROM saas_crm_native_deals WHERE id=%s::uuid', (deal,)).fetchone()
        before_outbox = conn.execute('SELECT * FROM saas_crm_sync_outbox ORDER BY id').fetchall()
    action = ('unlink_' if unlink else 'link_') + ('contact' if source_company else 'deal' if entity=='deal' else 'company')
    key = 'entity_id' if source_company else 'deal_id' if entity=='deal' else 'company_id'
    data = {'action':action, key:contact if source_company else deal if entity=='deal' else company}
    async def body(): return data
    request = SimpleNamespace(method='POST',match_info={'id':company if source_company else contact},query={},
        content_type='application/json',headers={'Origin':'https://test.invalid'},json=body)
    profile = members.member(A,81)
    with patch('tenant_crm_sync_outbox.enqueue_change') as enqueue:
        response = asyncio.run((company_api if source_company else contact_api).handler(lambda _: profile,'https://test.invalid',MagicMock())(request))
    assert response.status == 409, response.text
    assert json.loads(response.text)['error'] == 'scoped_relation_sync_not_configured'
    enqueue.assert_not_called()
    with native['factory']() as conn:
        assert conn.execute('SELECT * FROM saas_crm_native_contacts WHERE id=%s::uuid', (contact,)).fetchone() == before_contact
        assert conn.execute('SELECT * FROM saas_crm_native_deals WHERE id=%s::uuid', (deal,)).fetchone() == before_deal
        assert conn.execute('SELECT * FROM saas_crm_sync_outbox ORDER BY id').fetchall() == before_outbox


@pytest.mark.parametrize('enabled', [False,True])
def test_company_contact_relation_capabilities_match_mode(native, monkeypatch, enabled):
    set_scopes(native,81,{'companies':{'view':'self','update':'self','delete':'none'}})
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', str(enabled).lower())
    with native['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s::uuid AND telegram_id=81',
            (json.dumps(['customers:view','customers:update']), A))
    row = companies.get_company(A,native['ids']['companies']['self'],actor_id=81)
    assert row['relation_actions'] == {'link_contact':True,'unlink_contact':enabled,
                                       'link_deal':enabled,'unlink_deal':enabled}
    profile = members.member(A,81)
    page = listing.directory(profile,source='native')
    assert page['actions']['scoped_relations'] is enabled


@pytest.mark.parametrize('tenant,direction,fields', [(B,'to_external',['title']), (A,'off',['title']), (A,'from_external',['title']), (A,'to_external',[])])
def test_relation_sync_blocker_only_applies_to_active_same_tenant_outbound(native,tenant,direction,fields):
    configure(native,'customers','update','self')
    with native['factory']() as conn:
        conn.execute('''INSERT INTO saas_crm_provider_settings
          (tenant_id,entity_type,provider,mode,primary_provider,sync_direction,allowed_fields)
          VALUES(%s::uuid,'deal','kommo','selective','kommo',%s,%s::jsonb)''', (tenant,direction,json.dumps(fields)))
    assert contacts.set_contact_deal_link(A,native['ids']['customers']['self'],native['ids']['deals']['other'],actor_id=81)


def test_opposite_company_contact_edits_share_contact_first_lock_order(native,monkeypatch):
    import concurrent.futures
    import threading
    from test_contacts_directory_postgres import _observe_sql
    set_scopes(native,81,{kind:{'update':'all'} for kind in KINDS})
    contact,company=native['ids']['customers']['self'],native['ids']['companies']['self']
    locked,release,waiting=threading.Event(),threading.Event(),threading.Event()
    def after_company(sql,cursor):
        if 'SELECT id FROM saas_crm_native_contacts WHERE' in sql and 'FOR UPDATE' in sql:
            locked.set()
            assert release.wait(5)
    def before_contact(sql,cursor):
        if 'FROM saas_crm_native_contacts r' in sql and 'FOR UPDATE' in sql:
            waiting.set()
    monkeypatch.setattr(companies,'_connect',_observe_sql(native['factory'],after=after_company))
    monkeypatch.setattr(contacts,'_connect',_observe_sql(native['factory'],before=before_contact))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(companies.link_entity,A,company,'contact',contact,actor_id=81)
        assert locked.wait(5)
        second=pool.submit(contacts.set_contact_company_link,A,contact,company,actor_id=81)
        try:
            assert waiting.wait(5)
            assert not second.done()
        finally:
            release.set()
        assert first.result(timeout=5)
        assert second.result(timeout=5)
