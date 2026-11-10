import json
from dataclasses import replace
from unittest.mock import Mock
import pytest
from test_tenant_members_api import MembersApiTests
from tenant_members_api import MemberApiContext, handlers
from types import SimpleNamespace
from unittest.mock import AsyncMock

@pytest.mark.asyncio
@pytest.mark.parametrize('action,extra,receipt', [
 ('assign_member_profile',{'changes':{'first_name':'Updated'}},'profile_changed')])
async def test_assign_endpoint(action,extra,receipt):
    profile={'tenant_id':'company','telegram_id':1,'role':'owner','active':True}
    store=Mock(return_value={'telegram_id':22,'card_version':'v2',receipt:True})
    source={'tenant_id':'company','actor_id':1,'session_id':'synthetic'}
    ctx=MemberApiContext(member=lambda r:profile,telegram_identity=Mock(),telegram_name=Mock(),send_telegram=Mock(),
        origin='https://isolated.example',list_members=Mock(),upsert_member=Mock(),deactivate_member=Mock(),
        create_invite=Mock(),request_invite_acceptance=Mock(),list_invite_requests=Mock(),decide_invite_request=Mock(),
        platform_error=ValueError,source_session=lambda r:source,assign_member_profile=store)
    api=handlers(ctx)['members']
    command={'action':action,'telegram_id':22,'expected_version':'v1','expected_tenant_id':'company','expected_user_id':1,**extra}
    def req(data=command,origin='https://isolated.example'):
        return SimpleNamespace(method='POST',headers={'Origin':origin},json=AsyncMock(return_value=data))
    response=await api(req());assert response.status==200
    assert json.loads(response.text)=={'success':True,'applied':store.return_value}
    assert all(secret not in response.text for secret in ['synthetic-password','new@isolated.example'])
    assert store.call_args.kwargs['source_session']==source
    for field,value in [('expected_tenant_id','other'),('expected_user_id',3),('telegram_id',True),('expected_version','')]:
        before=store.call_count;assert (await api(req({**command,field:value}))).status in (400,409);assert store.call_count==before
    before=store.call_count;assert (await api(req(origin='https://other.example'))).status==403;assert store.call_count==before
    profile['role']='admin';assert (await api(req())).status==403;assert store.call_count==before
    profile['role']='owner';profile['active']=False;assert (await api(req())).status==403;assert store.call_count==before
    profile['active']=True;source=None;assert (await api(req())).status==401;assert store.call_count==before
