"""HTTP identity/origin fences and serializable independent consultation replies."""
import asyncio
import logging
from unittest.mock import Mock
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
import tenant_dialog_consultations as flow
from tenant_dialog_consultations_api import handler
from tenant_linear_policy import TenantLinearError

PROFILE={'tenant_id':'tenant-a','telegram_id':33}


def test_explicit_http_dispatch_identity_and_origin_fences(monkeypatch):
    dispatch=Mock(return_value={'state':'unknown'})
    monkeypatch.setattr(flow,'dispatch',dispatch)
    async def scenario():
        app=web.Application();current={'profile':PROFILE}
        app.router.add_post('/{id}/dispatch',handler(lambda _:current['profile'],'https://crm.test',logging.getLogger(__name__),'dispatch'))
        async with TestClient(TestServer(app)) as client:
            payload={'expected_tenant_id':'tenant-a','expected_user_id':33,'request_id':'uuid'}
            response=await client.post('/consult/dispatch',json=payload);assert response.status==403
            response=await client.post('/consult/dispatch',json={**payload,'expected_tenant_id':'tenant-b'},headers={'Origin':'https://crm.test'});assert response.status==409
            assert not dispatch.called
            response=await client.post('/consult/dispatch',json=payload,headers={'Origin':'https://crm.test'});assert response.status==200
            assert (await response.json())['result']['state']=='unknown'
            dispatch.assert_called_once_with(PROFILE,'consult','uuid')
            current['profile']=None
            response=await client.post('/consult/dispatch',json=payload,headers={'Origin':'https://crm.test'});assert response.status==403
    asyncio.run(scenario())


def test_reply_inbox_checks_identity_and_preserves_forbidden(monkeypatch):
    inbox=Mock(return_value={'replies':[],'unhandled':0,'unread':0});monkeypatch.setattr(flow,'inbox',inbox)
    async def scenario():
        app=web.Application();app.router.add_get('/replies',handler(lambda _:PROFILE,'https://crm.test',logging.getLogger(__name__),'replies'))
        async with TestClient(TestServer(app)) as client:
            response=await client.get('/replies',headers={'X-Tenant-Id':'tenant-b','X-User-Id':'33'});assert response.status==409
            assert not inbox.called
            response=await client.get('/replies',headers={'X-Tenant-Id':'tenant-a','X-User-Id':'33'});assert response.status==200
            assert (await response.json())['result']['unhandled']==0
            inbox.side_effect=TenantLinearError('No access',403)
            response=await client.get('/replies');assert response.status==403
    asyncio.run(scenario())


def test_question_draft_has_origin_identity_and_post_only_fences(monkeypatch):
    import tenant_developer_question_draft as questions
    generate=Mock(return_value={'text':'Question?', 'developer_question_draft':True,'customer_message_sent':False})
    monkeypatch.setattr(questions,'question',generate)
    async def scenario():
        current={'profile':PROFILE}
        app=web.Application()
        app.router.add_post('/question-draft',handler(lambda _:current['profile'],'https://crm.test',logging.getLogger(__name__),'question_draft'))
        async with TestClient(TestServer(app)) as client:
            payload={'expected_tenant_id':'tenant-a','expected_user_id':33,'request_id':'uuid'}
            assert (await client.get('/question-draft')).status==405
            assert (await client.post('/question-draft',json=payload)).status==403
            assert (await client.post('/question-draft',json={**payload,'expected_user_id':44},headers={'Origin':'https://crm.test'})).status==409
            generate.assert_not_called()
            response=await client.post('/question-draft',json=payload,headers={'Origin':'https://crm.test'})
            assert response.status==200 and response.headers['Cache-Control']=='no-store'
            assert (await response.json())['result']['customer_message_sent'] is False
            generate.assert_called_once_with(PROFILE,{'request_id':'uuid'})
            current['profile']=None
            assert (await client.post('/question-draft',json=payload,headers={'Origin':'https://crm.test'})).status==403
    asyncio.run(scenario())
