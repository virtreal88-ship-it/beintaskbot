"""Independent task scopes and the narrowly bounded executor exception."""
from itertools import product
from unittest.mock import Mock, patch
import asyncio
import json
import uuid

import pytest
from aiohttp.test_utils import TestClient, TestServer
from tenant_policy import TenantPolicy
import tenant_native_task_access as access
import tenant_crm_native_tasks as tasks
import tenant_task_flow as task_flow
from test_native_task_assignee_access import native, _app, _get, _post, _command
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')


def profile(actor=41, *, scope='none', department='one', **extra):
    return {'tenant_id': A, 'telegram_id': actor, 'active': True,
        'role': 'operator', 'permissions': [], 'modules': {'tasks': True},
        'department_id': department, 'workflow': {'policies': {'members': {
            str(actor): {'access_scopes': {'tasks': {action: scope for action in
                ('view', 'create', 'update', 'delete')}}}}}}, **extra}


def row(owner=42, executor=43, department='one', tenant=A):
    return {'tenant_id': tenant, 'access_responsible_id': owner, 'created_by': 999,
        'responsible_id': executor, 'access_responsible_department_id': department}


@pytest.mark.parametrize('scope,owner,department,tenant', list(product(
    ('none','self','department','all'), (41,42), ('one','two',None), (A,B))))
def test_all_crud_cells_check_responsible_not_assignee(enabled, scope, owner, department, tenant):
    policy=TenantPolicy(profile(scope=scope))
    task=row(owner=owner,department=department,tenant=tenant)
    expected=tenant==A and (scope=='all' or scope=='self' and owner==41
        or scope=='department' and department=='one')
    for operation in ('view','update','delete','reopen','complete'):
        assert access.allows(policy,task,41,operation) is expected


@pytest.mark.parametrize('operation,expected', [('view',True),('complete',True),
    ('update',False),('reopen',False),('delete',False),('create',False)])
def test_executor_exception_is_not_edit_authority(enabled, operation, expected):
    policy=TenantPolicy(profile())
    assert access.allows(policy,row(executor=41),41,operation) is expected
    assert not access.allows(policy,row(executor=41,tenant=B),41,operation)
    assert not access.allows(TenantPolicy(profile(active=False)),row(executor=41),41,operation)


def test_default_gate_preserves_legacy_denial(monkeypatch):
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED',raising=False)
    assert not access.allows(TenantPolicy(profile()),row(executor=41),41,'view')
    assert not access.allows(TenantPolicy(profile()),row(executor=41),41,'complete')


def _scopes(f, actor, *, view='none', create='none', update='none', delete='none'):
    setting={str(actor): {'access_scopes': {'tasks': dict(view=view,create=create,update=update,delete=delete)}}}
    f['db'].execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
        VALUES(%s::uuid,'members',%s::jsonb,11)
        ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=saas_tenant_workflow_policies.value || excluded.value''',
        (A,json.dumps(setting)))
    f['db'].commit()


def test_http_executor_reads_and_completes_without_general_rights(native, enabled, monkeypatch):
    f=native
    monkeypatch.setattr(task_flow,'_connect',f['factory'])
    _scopes(f,41)
    f['db'].execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by) VALUES(%s::uuid,'task_approval','{\"completion_requires_admin\":true}',11)",(A,));f['db'].commit()
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status,created=await _post(http,_command(f,41))
            assert status==200,created
            task=created['task'];ident=task['id']
            assert not {'access_responsible_id','access_responsible_department_id'} & task.keys()
            assert task['responsible_id']==41 and task['created_by']==11
            stored=f['db'].execute('SELECT access_responsible_id FROM saas_crm_native_tasks WHERE id=%s::uuid',(ident,))[0]
            assert stored['access_responsible_id']==11
            for query in ({},{'expected_tenant_id':A,'expected_user_id':'41'}):
                status,listing=await _get(http,actor=41,**query)
                assert status==200 and [item['id'] for item in listing['tasks']]==[ident],listing
                assert not {'access_responsible_id','access_responsible_department_id'} & listing['tasks'][0].keys()
                assert listing['tasks'][0]['capabilities']==dict(view=True,update=False,complete=True,reopen=False,delete=False)
            status,result=await _get(http,'/tasks/'+ident,actor=41)
            assert status==200,result
            assert not {'access_responsible_id','access_responsible_department_id'} & result['task'].keys()
            for payload in ({'action':'update','text':'forbidden'}, {'action':'archive'},
                            {'action':'reopen'}, {'action':'complete','text':'smuggled'}):
                status,result=await _post(http,payload,'/tasks/'+ident,actor=41)
                assert status in {400,403,404},result
            status,result=await _post(http,{'action':'complete'},'/tasks/'+ident,actor=41)
            assert status==200 and result['task']['status']=='completed',result
            assert not {'access_responsible_id','access_responsible_department_id'} & result['task'].keys()
            status,result=await _get(http,'/tasks/'+ident,actor=46)
            assert status==404,result
            status,result=await _get(http,'/tasks/'+ident,actor=22,tenant=B)
            assert status==404,result
    asyncio.run(scenario())


def test_update_is_independent_of_read_and_keeps_other_assignee(native, enabled):
    f=native
    _scopes(f,41,create='self',update='self')
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status,created=await _post(http,_command(f,42))
            assert status==200,created
            ident=created['task']['id']
            # Fixture simulates an existing document owned by 41 but assigned to 42.
            f['db'].execute('UPDATE saas_crm_native_tasks SET access_responsible_id=41 WHERE id=%s::uuid',(ident,));f['db'].commit()
            assert (await _get(http,'/tasks/'+ident,actor=41))[0]==404
            status,result=await _post(http,{'action':'update','text':'Own document edit'},'/tasks/'+ident,actor=41)
            assert status==200 and result['task']['responsible_id']==42,result
            status,result=await _post(http,{'action':'archive'},'/tasks/'+ident,actor=41)
            assert status==403,result
    asyncio.run(scenario())


def test_stale_session_does_not_survive_scoped_revocation(native, enabled):
    f=native
    _scopes(f,41,view='all',update='all',delete='all')
    import tenant_member_store
    cached=tenant_member_store.member(A,41)
    _scopes(f,41)
    async def scenario():
        async with TestClient(TestServer(_app(cached=cached))) as http:
            status,created=await _post(http,_command(f,42))
            assert status==200,created
            ident=created['task']['id']
            assert (await _get(http,'/tasks/'+ident,actor=41))[0]==404
            for action in ('update','complete','archive'):
                payload={'action':action,**({'text':'Stale edit'} if action=='update' else {})}
                status,result=await _post(http,payload,'/tasks/'+ident,actor=41)
                assert status in {403,404},result
    asyncio.run(scenario())


def test_sql_department_read_search_update_and_delete_scopes(native, enabled, monkeypatch):
    f=native
    monkeypatch.setattr(task_flow,'_connect',f['factory'])
    departments=[str(uuid.uuid4()),str(uuid.uuid4())]
    for ident in departments:
        f['db'].execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s::uuid,%s::uuid,%s)',(A,ident,'Synthetic '+ident))
    for actor,department in ((41,departments[0]),(43,departments[0]),(42,departments[1])):
        f['db'].execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=%s',(department,A,actor))
    f['db'].commit()
    _scopes(f,41,view='department',update='department',delete='self')
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            ids={}
            for responsible in (41,43,42):
                status,result=await _post(http,{**_command(f,50),'text':f'Synthetic search token {responsible}'})
                assert status==200,result
                ids[responsible]=result['task']['id']
                f['db'].execute('UPDATE saas_crm_native_tasks SET access_responsible_id=%s WHERE id=%s::uuid',(responsible,ids[responsible]))
                f['db'].commit()
            for query in ({},{'expected_tenant_id':A,'expected_user_id':'41'}):
                status,result=await _get(http,actor=41,search='search token',**query)
                assert status==200 and {t['id'] for t in result['tasks']}=={ids[41],ids[43]},result
                status,result=await _get(http,actor=41,search='search token 42',**query)
                assert status==200 and result['tasks']==[],result
            for responsible,expected in ((41,200),(43,200),(42,404)):
                status,result=await _post(http,{'action':'update','text':'Changed allowed task'},'/tasks/'+ids[responsible],actor=41)
                assert status==expected,result
                status,result=await _post(http,{'action':'archive'},'/tasks/'+ids[responsible],actor=41)
                assert status==(200 if responsible==41 else 404),result
            # Current responsible's department, not a cached document field.
            f['db'].execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=43',(departments[1],A));f['db'].commit()
            assert (await _get(http,'/tasks/'+ids[43],actor=41))[0]==404
            assert (await _post(http,{'action':'update','text':'Revoked department'},'/tasks/'+ids[43],actor=41))[0]==404
    asyncio.run(scenario())


def test_mutation_rechecks_scope_after_waiting_for_task_lock(native, enabled, monkeypatch):
    import concurrent.futures
    import threading
    f=native
    _scopes(f,41,update='all')
    task=tasks.create_task(A,f['deals'][0],'Original task',42,created_by=11,actor_id=11,idempotency_key=str(uuid.uuid4()))
    started=threading.Event()
    original=tasks._locked_task
    def waiting(*args,**kwargs):
        started.set()
        return original(*args,**kwargs)
    monkeypatch.setattr(tasks,'_locked_task',waiting)
    with f['factory']() as holder, concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        holder.execute('SELECT id FROM saas_crm_native_tasks WHERE id=%s::uuid FOR UPDATE',(task['id'],))
        future=executor.submit(tasks.update_task,A,str(task['id']),'Must not save',42,actor_id=41)
        assert started.wait(10)
        holder.execute("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s::uuid AND policy_key='members'",
            (json.dumps({'41':{'access_scopes':{'tasks':{'update':'none'}}}}),A))
        holder.commit()
        with pytest.raises(tasks.NativeTaskAccessError):future.result(timeout=10)
    assert tasks.get_task(A,str(task['id']))['text']=='Original task'


@pytest.mark.parametrize('scope,department,expected', [
    ('none',False,403),('self',False,200),('department',False,403),
    ('department',True,200),('all',False,200)])
def test_creation_scope_uses_creator_not_executor(native, enabled, scope, department, expected):
    f=native
    f['db'].execute('UPDATE saas_crm_native_deals SET responsible_id=41 WHERE id=%s::uuid',(f['deals'][0],))
    if department:
        ident=str(uuid.uuid4())
        f['db'].execute("INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s::uuid,%s::uuid,'Synthetic creation department')",(A,ident))
        f['db'].execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=41',(ident,A))
    f['db'].commit()
    _scopes(f,41,create=scope)
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status,result=await _post(http,_command(f,41),actor=41)
            assert status==expected,result
            if status==200:
                ident=result['task']['id']
                stored=f['db'].execute('SELECT access_responsible_id,created_by,responsible_id FROM saas_crm_native_tasks WHERE id=%s::uuid',(ident,))[0]
                assert stored==dict(access_responsible_id=41,created_by=41,responsible_id=41)
    asyncio.run(scenario())


def _task_and_deal_scopes(f,actor,deal_view):
    setting={str(actor):{'access_scopes':{
        'tasks':dict(create='self',view='none',update='self',delete='none'),
        'deals':dict(create='none',view=deal_view,update='none',delete='none')}}}
    f['db'].execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
        VALUES(%s::uuid,'members',%s::jsonb,11)
        ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=saas_tenant_workflow_policies.value || excluded.value''',
        (A,json.dumps(setting)));f['db'].commit()


def test_task_creation_and_picker_cannot_bypass_closed_deal_read(native,enabled):
    f=native;_task_and_deal_scopes(f,31,'none')
    assert tasks.task_options(A,31)['deals']==[]
    for operation in (lambda:tasks.task_options(A,31,deal_id=f['deals'][0]),
        lambda:tasks.create_task(A,f['deals'][0],'Forbidden new link',31,created_by=31,actor_id=31,idempotency_key=str(uuid.uuid4()))):
        with pytest.raises(tasks.NativeTaskAccessError) as error:operation()
        assert error.value.status==404
    assert f['db'].execute('SELECT count(*) AS n FROM saas_crm_native_tasks')[0]['n']==0


def test_task_picker_does_not_enumerate_other_deals_when_editing_allowed_task(native,enabled):
    f=native
    # Existing related data remains part of the task, even when the general
    # deal section is closed. It does not authorize unrelated picker results.
    task=tasks.create_task(A,f['deals'][0],'Existing related task',42,created_by=31,actor_id=31,idempotency_key=str(uuid.uuid4()))
    _task_and_deal_scopes(f,31,'none')
    picker=tasks.task_options(A,31,task_id=str(task['id']))
    assert [str(deal['id']) for deal in picker['deals']]==[f['deals'][0]]
    assert picker['selected_deal_id']==f['deals'][0]
    saved=tasks.update_task(A,str(task['id']),'Still editable task',42,actor_id=31)
    assert saved['text']=='Still editable task'


@pytest.mark.parametrize('scope,allowed', [('none',set()),('self',{0}),('department',{0,1}),('all',{0,1,2,4})])
def test_task_picker_applies_explicit_deal_read_scopes(native,enabled,scope,allowed):
    f=native;department=str(uuid.uuid4());other_department=str(uuid.uuid4())
    for ident in (department,other_department):
        f['db'].execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s::uuid,%s::uuid,%s)',(A,ident,'Synthetic '+ident))
    for actor,dep in ((31,department),(41,department),(42,other_department)):
        f['db'].execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=%s',(dep,A,actor))
    for index,owner in ((0,31),(1,41),(2,42),(4,42)):
        f['db'].execute('UPDATE saas_crm_native_deals SET access_responsible_id=%s WHERE id=%s::uuid',(owner,f['deals'][index]))
    f['db'].commit();_task_and_deal_scopes(f,31,scope)
    picker=tasks.task_options(A,31)
    assert {str(deal['id']) for deal in picker['deals']}=={f['deals'][i] for i in allowed}
    for index in (0,1,2,4):
        if index in allowed:
            created=tasks.create_task(A,f['deals'][index],'Authorized new link',31,created_by=31,actor_id=31,idempotency_key=str(uuid.uuid4()))
            assert str(created['deal_id'])==f['deals'][index]
        else:
            with pytest.raises(tasks.NativeTaskAccessError):
                tasks.create_task(A,f['deals'][index],'Forbidden new link',31,created_by=31,actor_id=31,idempotency_key=str(uuid.uuid4()))
