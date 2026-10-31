"""Developer marker narrows existing employee visibility; all sends mocked."""
import pytest
from test_dialog_consultations import database,db,ready,TARGET,prepared
from test_tenant_support_inbox import A,B,CA,CB,PROFILE
import tenant_dialog_consultations as flow
from tenant_linear_policy import TenantLinearError


def marker(enabled=None,conversation=TARGET,channel=CA):
    value={'channel_id':channel,'conversation_id':conversation}
    if enabled is not None:value['enabled']=enabled
    return value


def test_default_false_persistence_and_reversible_direct_group(ready):
    db,data,sender=ready
    assert flow.developer_marker(PROFILE,marker(conversation='client'))=={'enabled':False}
    assert flow.developer_marker(PROFILE,marker(True,conversation='client'))=={'enabled':True}
    assert flow.developer_marker(PROFILE,marker(conversation='client'))=={'enabled':True}
    assert len(flow.destinations(PROFILE)['destinations'])==2
    assert flow.developer_marker(PROFILE,marker(False,conversation='client'))=={'enabled':False}
    assert [r['conversation_id'] for r in flow.destinations(PROFILE)['destinations']]==[TARGET]
    flow.developer_marker(PROFILE,marker(False))
    assert flow.destinations(PROFILE)['destinations']==[]
    assert not sender.called
    assert db.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0


def test_stale_untagged_selection_and_prepared_dispatch_denied(ready):
    db,data,sender=ready
    result,_=prepared(ready)
    flow.developer_marker(PROFILE,marker(False))
    with pytest.raises(TenantLinearError,match='Developer'):flow.prepare(PROFILE,data,'https://crm.test')
    with pytest.raises(TenantLinearError,match='Developer'):flow.dispatch(PROFILE,result['id'],data['request_id'])
    assert not sender.called
    assert db.execute('SELECT state FROM saas_consultation_group_deliveries')[0]['state']=='ready'


def test_cross_tenant_and_employee_access_cannot_be_granted(ready):
    db,data,sender=ready
    before=db.execute('SELECT id,enabled FROM saas_ai_agents');db.commit()
    for profile,body in [({'tenant_id':B,'telegram_id':22},marker(True)),({'tenant_id':A,'telegram_id':999},marker(True)),(PROFILE,marker(True,channel=CB))]:
        with pytest.raises(TenantLinearError):flow.developer_marker(profile,body)
    assert flow.destinations({'tenant_id':B,'telegram_id':22})['destinations']==[]
    assert db.execute('SELECT id,enabled FROM saas_ai_agents')==before
    assert not sender.called


@pytest.mark.parametrize('value',[1,'true',None,[],{}])
def test_marker_requires_boolean(ready,value):
    with pytest.raises(TenantLinearError):flow.developer_marker(PROFILE,{**marker(),'enabled':value})


def test_tag_does_not_bypass_revoked_access_or_inactive_channel(ready):
    db,data,sender=ready
    result,_=prepared(ready)
    db.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=33",(A,));db.commit()
    employee={'tenant_id':A,'telegram_id':33}
    with pytest.raises(TenantLinearError):flow.destinations(employee)
    with pytest.raises(TenantLinearError):flow.developer_marker(employee,marker(True))
    with pytest.raises(TenantLinearError):flow.prepare(employee,data,'https://crm.test')
    db.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    assert flow.destinations(PROFILE)['destinations']==[]
    with pytest.raises(TenantLinearError):flow.developer_marker(PROFILE,marker(True))
    with pytest.raises(TenantLinearError):flow.dispatch(PROFILE,result['id'],data['request_id'])
    assert not sender.called


def test_marker_api_identity_origin_and_boolean_payload(monkeypatch):
    import asyncio
    import logging
    from unittest.mock import Mock
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from tenant_dialog_consultations_api import handler
    service=Mock(return_value={'enabled':False});monkeypatch.setattr(flow,'developer_marker',service)
    async def scenario():
        app=web.Application();app.router.add_post('/marker',handler(lambda _:PROFILE,'https://crm.test',logging.getLogger(__name__),'developer_marker'))
        async with TestClient(TestServer(app)) as client:
            payload={**marker(False),'expected_tenant_id':A,'expected_user_id':11}
            assert (await client.post('/marker',json=payload)).status==403
            assert (await client.post('/marker',json={**payload,'expected_tenant_id':B},headers={'Origin':'https://crm.test'})).status==409
            assert not service.called
            response=await client.post('/marker',json=payload,headers={'Origin':'https://crm.test'})
            assert response.status==200
            service.assert_called_once_with(PROFILE,marker(False))
    asyncio.run(scenario())
