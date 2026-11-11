"""Finance scope checks use disposable PostgreSQL and synthetic ledger entries."""
import asyncio
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from unittest.mock import Mock

import psycopg
from psycopg.rows import dict_row
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tenant_finance as finance
import tenant_finance_schema as schema
import tenant_finance_api as api
import tenant_member_store as members
from tenant_finance_policy import (finance_policy, finance_member_allowed,
    finance_capabilities, TenantFinanceError)
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B

ORIGIN='https://finance-scopes.example'


@pytest.fixture
def enabled(monkeypatch):
    import tenant_scoped_access
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')


def profile(scope='none', actor=41, department='one', **extra):
    return dict(tenant_id=A,telegram_id=actor,active=True,role='operator',department_id=department,
        permissions=[],modules={'finance':True},workflow={'policies':{'members':{
            str(actor):{'access_scopes':{'finance':{'view':scope}}}}}},**extra)


@pytest.mark.parametrize('scope,target,department,tenant',list(product(
    ('none','self','department','all'),(41,42),('one','two',None),(A,B))))
def test_read_scope_never_implies_admin_writes(enabled,scope,target,department,tenant):
    current=profile(scope)
    if scope=='none':
        with pytest.raises(TenantFinanceError):finance_policy(current)
        return
    policy=finance_policy(current)
    expected=tenant==A and (scope=='all' or scope=='self' and target==41
        or scope=='department' and department=='one')
    assert finance_member_allowed(policy,dict(tenant_id=tenant,telegram_id=target,department_id=department)) is expected
    assert finance_capabilities(policy)['can_write'] is False
    with pytest.raises(TenantFinanceError):finance_policy(current,write=True)


def configure(f, actor, scope):
    patch={str(actor):{'access_scopes':{'finance':{'view':scope}}}}
    f['db'].execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
        VALUES(%s::uuid,'members',%s::jsonb,11)
        ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=saas_tenant_workflow_policies.value || excluded.value''',(A,json.dumps(patch)))
    f['db'].commit()


@pytest.fixture
def ledger(db,monkeypatch,enabled):
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(finance,'_connect',factory)
    monkeypatch.setattr(members,'_connect',factory)
    schema._ready=False
    schema.ensure_finance_schema(db)
    departments=[str(uuid.uuid4()),str(uuid.uuid4())]
    for department in departments:
        db.execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s::uuid,%s::uuid,%s)',(A,department,'Synthetic '+department))
    for actor,department,role in ((41,departments[0],'operator'),(42,departments[0],'operator'),(43,departments[1],'operator'),(44,None,'admin')):
        db.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,permissions,department_id)
            VALUES(%s::uuid,%s,%s,%s,%s::jsonb,%s::uuid)''',(A,actor,'Synthetic '+str(actor),role,json.dumps(['finance'] if actor==44 else []),department))
    db.execute("UPDATE saas_tenants SET modules='{\"finance\":true}'::jsonb WHERE id=%s::uuid",(A,));db.commit()
    f={'db':db,'factory':factory,'departments':departments}
    owner=members.member(A,11)
    for actor in (41,42,43,44):
        finance.record(owner,dict(action='credit',member_id=actor,amount=str(actor),note='Synthetic ledger only',request_id=str(uuid.uuid4())))
    foreign=members.member(B,22)
    finance.record(foreign,dict(action='credit',member_id=22,amount='999.00',note='Foreign synthetic private ledger',request_id=str(uuid.uuid4())))
    configure(f,41,'self')
    yield f
    schema._ready=False


def app(cached=None):
    def authenticate(request):
        tenant=request.headers.get('X-Tenant',A);actor=int(request.headers.get('X-Actor','41'))
        return cached if cached and tenant==cached['tenant_id'] and actor==cached['telegram_id'] else members.member(tenant,actor)
    application=web.Application();application.router.add_route('*','/finance',api.finance_handler(authenticate,ORIGIN,Mock()))
    return application


async def get(http,**params):
    response=await http.get('/finance',params=params)
    return response.status,await response.json()


@pytest.mark.parametrize('scope,ids', [('self',{41}),('department',{41,42}),('all',{11,41,42,43,44}),('none',set())])
def test_http_history_picker_balances_and_counts_are_scoped(ledger,scope,ids):
    configure(ledger,41,scope)
    async def scenario():
        async with TestClient(TestServer(app())) as http:
            status,listing=await get(http,members='1',limit='2')
            if scope=='none':
                assert status==403 and 'members' not in listing
            else:
                assert status==200 and listing['total']==len(ids),listing
                found={r['telegram_id'] for r in listing['members']}
                for offset in range(2,len(ids),2):
                    status,page=await get(http,members='1',limit='2',offset=str(offset))
                    assert status==200;found|={r['telegram_id'] for r in page['members']}
                assert found==ids
                assert listing['can_write'] is False
                assert listing['can_select_members']==(scope in {'department','all'})
            for member in (41,42,43,22):
                status,body=await get(http,member_id=str(member))
                if scope=='none':assert status==403
                elif member not in ids:assert status==404 and 'balance' not in body and 'entries' not in body
                else:
                    assert status==200 and body['balance']==str(member)+'.00' and body['total']==1,body
                    assert all(e['member_id']==member and e['tenant_id']==A for e in body['entries'])
                    assert body['can_write'] is False
    asyncio.run(scenario())


def test_fresh_membership_and_department_revoke_stale_reads(ledger):
    f=ledger;configure(f,41,'department');cached=members.member(A,41)
    assert finance.history(cached,member_id=42)['balance']=='42.00'
    f['db'].execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=42',(f['departments'][1],A));f['db'].commit()
    with pytest.raises(TenantFinanceError) as error:finance.history(cached,member_id=42)
    assert error.value.status==404
    configure(f,41,'none')
    for call in (lambda:finance.history(cached),lambda:finance.list_members(cached)):
        with pytest.raises(TenantFinanceError) as error:call()
        assert error.value.status==403
    configure(f,41,'all')
    f['db'].execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=41',(A,));f['db'].commit()
    with pytest.raises(TenantFinanceError):finance.history(cached)


def test_scoped_admin_read_does_not_create_financial_write_authority(ledger):
    f=ledger;configure(f,44,'all')
    f['db'].execute("UPDATE saas_tenant_members SET permissions='[]' WHERE tenant_id=%s::uuid AND telegram_id=44",(A,));f['db'].commit()
    actor=members.member(A,44)
    assert finance.history(actor,member_id=42)['can_write'] is False
    with pytest.raises(TenantFinanceError):finance.record(actor,dict(action='credit',member_id=42,amount='1.00',note='Disallowed',request_id=str(uuid.uuid4())))


def test_write_revalidates_after_concurrent_admin_revocation(ledger,monkeypatch):
    f=ledger;cached=members.member(A,44);started=threading.Event();original=finance._current_policy
    def waiting(*args,**kwargs):
        if kwargs.get('write'):started.set()
        return original(*args,**kwargs)
    monkeypatch.setattr(finance,'_current_policy',waiting)
    with f['factory']() as holder,ThreadPoolExecutor(max_workers=1) as executor:
        holder.execute('SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=44 FOR UPDATE',(A,))
        future=executor.submit(finance.record,cached,dict(action='credit',member_id=41,amount='1000.00',note='Revoked admin',request_id=str(uuid.uuid4())))
        assert started.wait(10)
        holder.execute("UPDATE saas_tenant_members SET role='operator' WHERE tenant_id=%s::uuid AND telegram_id=44",(A,));holder.commit()
        with pytest.raises(TenantFinanceError):future.result(timeout=10)
    configure(f,41,'self')
    assert finance.history(members.member(A,41))['balance']=='41.00'


def test_write_and_reverse_cannot_target_foreign_tenant(ledger):
    actor=members.member(A,44)
    for data in (dict(action='credit',member_id=22,amount='1.00'),
        dict(action='reverse',member_id=41,entry_id=str(uuid.uuid4()))):
        with pytest.raises(TenantFinanceError):finance.record(actor,{**data,'note':'Wrong target','request_id':str(uuid.uuid4())})


def test_department_without_department_has_no_self_or_tenant_fallback(ledger):
    configure(ledger,44,'department')
    actor=members.member(A,44)
    listing=finance.list_members(actor)
    assert listing['members']==[] and listing['total']==0
    with pytest.raises(TenantFinanceError) as error:finance.history(actor)
    assert error.value.status==404


def test_legacy_admin_write_authority_is_not_redefined_by_read_scope(ledger):
    configure(ledger,44,'none')
    actor=members.member(A,44)
    with pytest.raises(TenantFinanceError):finance.history(actor,member_id=41)
    result=finance.record(actor,dict(action='credit',member_id=41,amount='1.00',note='Synthetic existing admin action',request_id=str(uuid.uuid4())))
    assert result['amount']=='1.00' and result['member_id']==41


def test_malformed_legacy_permission_object_cannot_grant_write(enabled):
    actor=profile('all')
    actor.update(role='admin',permissions={'finance':False})
    assert finance_capabilities(finance_policy(actor))['can_write'] is False
    with pytest.raises(TenantFinanceError):finance_policy(actor,write=True)


@pytest.mark.parametrize('operation', ['history','members'])
@pytest.mark.parametrize('revocation', ['scope','department','active','tenant'])
def test_scoped_read_rechecks_after_pending_revocation(ledger,monkeypatch,operation,revocation):
    f=ledger;configure(f,41,'department');cached=members.member(A,41)
    started=threading.Event();original=finance._current_policy
    def waiting(*args,**kwargs):
        started.set()
        return original(*args,**kwargs)
    monkeypatch.setattr(finance,'_current_policy',waiting)
    with f['factory']() as holder,ThreadPoolExecutor(max_workers=1) as executor:
        if revocation=='scope':
            holder.execute("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s::uuid AND policy_key='members'",(json.dumps({'41':{'access_scopes':{'finance':{'view':'none'}}}}),A))
        elif revocation=='department':
            holder.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=42',(f['departments'][1],A))
        elif revocation=='active':
            holder.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=41',(A,))
        else:
            holder.execute("UPDATE saas_tenants SET status='disabled' WHERE id=%s::uuid",(A,))
        call=(lambda:finance.history(cached,member_id=42)) if operation=='history' else (lambda:finance.list_members(cached))
        future=executor.submit(call);assert started.wait(10)
        holder.commit()
        if revocation=='department' and operation=='members':
            assert [person['telegram_id'] for person in future.result(timeout=10)['members']]==[41]
        else:
            with pytest.raises(TenantFinanceError) as error:future.result(timeout=10)
            assert error.value.status==(404 if revocation=='department' else 403)


@pytest.mark.parametrize('operation', ['history','members'])
@pytest.mark.parametrize('revocation', ['scope','department'])
def test_scoped_read_fences_revocation_until_result_is_built(ledger,monkeypatch,operation,revocation):
    import time
    f=ledger;configure(f,41,'department');cached=members.member(A,41)
    locked=threading.Event();release=threading.Event();writer_ready=threading.Event();writer_pid=[]
    original=finance._current_policy
    def hold_after_lock(*args,**kwargs):
        policy=original(*args,**kwargs)
        locked.set();assert release.wait(10)
        return policy
    monkeypatch.setattr(finance,'_current_policy',hold_after_lock)
    def revoke():
        with f['factory']() as conn:
            writer_pid.append(conn.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']);writer_ready.set()
            if revocation=='scope':
                conn.execute("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s::uuid AND policy_key='members'",(json.dumps({'41':{'access_scopes':{'finance':{'view':'none'}}}}),A))
            else:
                conn.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=42',(f['departments'][1],A))
    with ThreadPoolExecutor(max_workers=2) as executor:
        call=(lambda:finance.history(cached,member_id=42)) if operation=='history' else (lambda:finance.list_members(cached))
        reader=executor.submit(call);assert locked.wait(10)
        writer=executor.submit(revoke);assert writer_ready.wait(10)
        try:
            deadline=time.monotonic()+5
            with f['factory']() as inspector:
                while time.monotonic()<deadline:
                    blocked=inspector.execute('SELECT pg_blocking_pids(%s) AS blockers',(writer_pid[0],)).fetchone()['blockers']
                    if blocked:break
                    time.sleep(.01)
            assert blocked and not writer.done(),'Revocation must wait for the authorized read transaction'
        finally:release.set()
        result=reader.result(timeout=10);writer.result(timeout=10)
        if operation=='history':assert result['member_id']==42
        else:assert {person['telegram_id'] for person in result['members']}=={41,42}


def test_denied_finance_capabilities_do_not_claim_read_all(enabled):
    from tenant_policy import TenantPolicy
    actor=profile('all');actor.update(role='admin',active=False)
    result=finance_capabilities(TenantPolicy(actor))
    assert result==dict(can_write=False,can_view_all=False,can_select_members=False,read_scope='none')
