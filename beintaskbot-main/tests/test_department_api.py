"""Department commands use the same current owner/context envelope as cards."""
import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from tenant_members_api import MemberApiContext, handlers


def setup():
    profile={'tenant_id':'company-a','telegram_id':11,'role':'owner','active':True}
    operation=Mock(return_value={'id':'department-a','name':'Support','active':True,'version':'v1'})
    context=MemberApiContext(member=lambda request:profile,telegram_identity=Mock(),telegram_name=Mock(),
        send_telegram=Mock(),origin='https://fixture.test',list_members=Mock(),upsert_member=Mock(),
        deactivate_member=Mock(),create_invite=Mock(),request_invite_acceptance=Mock(),
        list_invite_requests=Mock(),decide_invite_request=Mock(),platform_error=ValueError,
        upsert_department=operation)
    return profile,operation,handlers(context)['members']


def request(**data):
    value={'action':'create_department','expected_tenant_id':'company-a','expected_user_id':11,'name':'Support',**data}
    return Mock(method='POST',headers={'Origin':'https://fixture.test'},json=AsyncMock(return_value=value))


@pytest.mark.asyncio
async def test_department_create_and_update_are_scoped_and_versioned():
    profile,operation,handle=setup()
    response=await handle(request())
    assert response.status==200
    assert json.loads(response.text)['department']['id']=='department-a'
    assert operation.call_args.kwargs=={'tenant_id':'company-a','owner_id':11,'name':'Support','department_id':None,'expected_version':None,'active':None}
    response=await handle(request(action='update_department',department_id='department-a',expected_version='v1'))
    assert response.status==200
    assert operation.call_args.kwargs['expected_version']=='v1'


@pytest.mark.asyncio
@pytest.mark.parametrize('changes',[{'action':'update_department'},{'action':'archive_department'},
    {'action':'update_department','department_id':'a'}, {'expected_tenant_id':'company-b'},
    {'expected_user_id':22},{'unexpected':True}])
async def test_invalid_catalog_commands_never_reach_store(changes):
    profile,operation,handle=setup()
    response=await handle(request(**changes))
    assert response.status in {400,409}
    operation.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_requires_active_owner_and_same_origin():
    profile,operation,handle=setup()
    profile['active']=False
    assert (await handle(request())).status==403
    profile.update(active=True,role='admin')
    assert (await handle(request())).status==403
    profile['role']='owner'
    forged=request();forged.headers={'Origin':'https://other.test'}
    assert (await handle(forged)).status==403
    operation.assert_not_called()
