"""Owner-session continuation HTTP contract; no live service or provider calls."""
import asyncio
import json
from unittest.mock import Mock
import tenant_knowledge_candidates_api as api
import tenant_case_continuation_ui as continuation


class Request:
    def __init__(self,method,query=None,body=None,origin='https://crm.test'):
        self.method=method;self.query=query or {};self.body=body or {};self.content_type='application/json'
        self.headers={'Origin':origin};self.match_info={'campaign_id':'campaign'}
    async def json(self):return self.body


def test_owner_session_origin_and_tenant_guards(monkeypatch):
    profile={'tenant_id':'tenant-a','telegram_id':11};calls=[]
    monkeypatch.setattr(continuation,'preview_owner',lambda *args:(calls.append(('read',args)) or {'case_count':22}))
    monkeypatch.setattr(continuation,'apply_owner',lambda *args:(calls.append(('apply',args)) or {'count':1}))
    handler=api.handler(lambda request:profile,'https://crm.test',Mock(),'campaign_case_continuation')
    async def run():
        expected={'expected_tenant_id':'tenant-a','expected_user_id':'11'}
        response=await handler(Request('GET',{**expected,'expected_version':'9'}));assert response.status==200
        assert json.loads(response.text)['result']['case_count']==22
        assert (await handler(Request('GET',{**expected,'expected_tenant_id':'tenant-b','expected_version':'9'}))).status==409
        body={**expected,'expected_version':9,'source_refs':[],'snapshot_hash':'a'*64,'request_id':'r','confirm_exact_preview':True}
        assert (await handler(Request('POST',body=body,origin='https://evil.test'))).status==403
        assert (await handler(Request('POST',body={**body,'expected_user_id':'12'}))).status==409
        assert (await handler(Request('POST',body=body))).status==200
        assert [c[0] for c in calls]==['read','apply']
        assert not {'expected_tenant_id','expected_user_id'} & set(calls[-1][1][2])
        denied=api.handler(lambda request:None,'https://crm.test',Mock(),'campaign_case_continuation')
        assert (await denied(Request('GET',{**expected,'expected_version':'9'}))).status==403
    asyncio.run(run())


def test_current_media_check_requires_same_origin_and_scoped_session(monkeypatch):
    import tenant_case_current_media as current_media
    from tenant_linear_policy import TenantLinearError
    profile={'tenant_id':'tenant-a','telegram_id':11};calls=[]
    def check(*args):calls.append(args);return {'current_bytes_checked':True,'provider':'connect'}
    monkeypatch.setattr(current_media,'verify_owner_source',check)
    handler=api.handler(lambda request:profile,'https://crm.test',Mock(),'campaign_current_media_check')
    data={'expected_tenant_id':'tenant-a','expected_user_id':'11','expected_version':9,'case_id':'c','message_id':'m','case_version':1}
    async def run():
        assert (await handler(Request('POST',body=data,origin='https://evil.test'))).status==403
        assert (await handler(Request('POST',body={**data,'expected_tenant_id':'other'}))).status==409
        response=await handler(Request('POST',body=data));assert response.status==200 and len(calls)==1
        def rate(*args):
            error=TenantLinearError('Connect: rate limit',429);error.code='connect_rate_limit';error.retry_after=12;raise error
        monkeypatch.setattr(current_media,'verify_owner_source',rate)
        response=await handler(Request('POST',body=data))
        assert response.status==429 and response.headers['Retry-After']=='12' and json.loads(response.text)['code']=='connect_rate_limit'
    asyncio.run(run())
