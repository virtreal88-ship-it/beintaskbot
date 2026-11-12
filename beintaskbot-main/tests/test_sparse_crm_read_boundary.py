"""Sparse module scopes cannot fall through another module's legacy grant."""
import asyncio
from unittest.mock import AsyncMock, Mock
import pytest
from tenant_policy import TenantPolicy
from tenant_deal_api import DealApiContext, handlers
from test_tenant_deal_api import manager_profile, Request, PlatformError


def profile(module='deals', scope='self'):
    p=manager_profile()
    p['workflow']['policies']={'members':{'20':{'pipeline_ids':['10'],'access_scopes':{module:{'view':scope}}}}}
    return p


def api(p):
    context=DealApiContext(member=Mock(return_value=p),policy=TenantPolicy,
        sync_crm=AsyncMock(),sync_status=Mock(return_value=[]),
        list_deals=Mock(return_value=([{'kommo_lead_id':7}],1)),
        get_deal=Mock(return_value={'tenant_id':'company-a','pipeline_id':10,'status_id':100}),
        platform_error=PlatformError,logger=Mock())
    return handlers(context),context


@pytest.mark.parametrize('scope',['none','self','department'])
@pytest.mark.parametrize('route',['deals','deal'])
def test_kommo_funnel_scope_is_authority_independent_of_native_deal_rights(monkeypatch,scope,route):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=profile(scope=scope)
    assert TenantPolicy(p).allows('customers') # Reproduction: legacy fallback still exists.
    routes,c=api(p)
    response=asyncio.run(routes[route](Request({'lead_id':'7','refresh':'1'})))
    assert response.status==200
    (c.list_deals if route=='deals' else c.get_deal).assert_called_once()


@pytest.mark.parametrize('enabled',[False,True])
def test_empty_matrix_keeps_legacy_customer_only_read(monkeypatch,enabled):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED',str(enabled).lower())
    p=manager_profile();p['permissions']=['customers']
    p['workflow']['policies'][0]['value']['20']['pipeline_ids']=['10']
    routes,c=api(p)
    assert asyncio.run(routes['deals'](Request())).status==200
    c.list_deals.assert_called_once()


def test_gate_off_and_owner_keep_legacy_scope_behavior(monkeypatch):
    p=profile();monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','false')
    assert asyncio.run(api(p)[0]['deals'](Request())).status==200
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true');p['role']='owner'
    assert asyncio.run(api(p)[0]['deals'](Request())).status==200

@pytest.mark.parametrize('module',['deals','customers'])
@pytest.mark.parametrize('scope',['none','self','department'])
def test_external_contact_projection_cannot_bypass_either_read_scope(monkeypatch,module,scope):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=profile(module,scope)
    assert not TenantPolicy(p).can_read_legacy_crm('customers')


@pytest.mark.parametrize('action',['create','update','delete'])
@pytest.mark.parametrize('scope',['none','self','department','all'])
def test_action_only_scope_does_not_restrict_legacy_read(monkeypatch,action,scope):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=profile();p['workflow']['policies']['members']['20']['access_scopes']={'deals':{action:scope}}
    routes,c=api(p)
    assert asyncio.run(routes['deals'](Request())).status==200
    assert TenantPolicy(p).can_access_deal(c.get_deal.return_value)


def test_explicit_all_keeps_legacy_pipeline_and_tenant_boundary(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=profile(scope='all');routes,c=api(p)
    assert asyncio.run(routes['deals'](Request())).status==200
    policy=TenantPolicy(p)
    assert policy.can_access_deal(c.get_deal.return_value)
    assert not policy.can_access_deal({**c.get_deal.return_value,'tenant_id':'other'})
    assert not policy.can_access_deal({**c.get_deal.return_value,'pipeline_id':99})


def test_sparse_read_restriction_closes_board_and_ai_legacy_projection(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=profile();p['workflow']['policies']['members']['20']['ai_enabled']=True
    policy=TenantPolicy(p)
    assert policy.deal_board()==[]
    assert not policy.can_use_chat_ai()
    assert policy.can_access_deal({'tenant_id':'company-a','pipeline_id':10,'status_id':100})


def test_legacy_chat_fallback_cannot_use_customers_grant(monkeypatch):
    from tenant_chat_api import ChatApiContext, handlers as chat_handlers
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=profile();get=Mock();messages=Mock();sync=AsyncMock()
    c=ChatApiContext(member=Mock(return_value=p),policy=TenantPolicy,get_deal=get,
        list_messages=messages,sync_chat=sync,platform_error=PlatformError,logger=Mock())
    response=asyncio.run(chat_handlers(c)['chat'](Request({'lead_id':'7','refresh':'1'})))
    assert response.status==403
    get.assert_not_called();messages.assert_not_called();sync.assert_not_awaited()


def test_all_does_not_manufacture_external_read_grant(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=profile(scope='all');p['permissions']=[];p['workflow']['policies']['members']['20']['pipeline_ids']=[]
    assert not TenantPolicy(p).can_read_legacy_crm()
    assert asyncio.run(api(p)[0]['deals'](Request())).status==403


@pytest.mark.parametrize('path',['legacy_options','flow_options'])
@pytest.mark.parametrize('scope',[None,'none','self','department','all'])
@pytest.mark.parametrize('role,enabled',[('manager',True),('manager',False),('owner',True)])
def test_task_only_deal_pickers_obey_explicit_view_without_losing_executors(monkeypatch,path,scope,role,enabled):
    import json
    from test_tenant_task_api import TenantTaskApiTests, Request as TaskRequest
    import tenant_task_api
    import tenant_task_flow
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED',str(enabled).lower())
    p=profile(scope=scope) if scope is not None else manager_profile()
    p['permissions']=['tasks'];p['role']=role;p['display_name']='Task operator'
    restricted=enabled and role!='owner' and scope in ('none','self','department')
    if path=='legacy_options':
        fixture=TenantTaskApiTests();fixture.setUp()
        fixture.member.return_value=p
        fixture.executors.return_value=[p]
        from dataclasses import replace
        fixture.context=replace(fixture.context,policy=TenantPolicy)
        result=json.loads(asyncio.run(tenant_task_api.handlers(fixture.context)['options'](TaskRequest())).body)
        assert result['success'] is True
        assert bool(result['deals']) is (not restricted)
        assert fixture.list_deals.call_count==(0 if restricted else 1)
    else:
        cursor=Mock()
        cursor.fetchall.return_value=[{'id':'7','name':'Lead','pipeline_id':10}]
        monkeypatch.setattr(tenant_task_flow,'_attempt',lambda supplied,operation,**kwargs:operation(cursor,p,{20:p}))
        result=tenant_task_flow.external_options(p)
        assert bool(result['deals']) is (not restricted)
        assert cursor.execute.call_count==(0 if restricted else 1)
    assert len(result['executors'])==1
    assert str(result['executors'][0]['id'])==str(p['telegram_id'])
