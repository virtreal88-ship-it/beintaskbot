import asyncio
from unittest.mock import Mock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
import tenant_connect_avatars as avatars
import tenant_call_sources as sources
from tenant_linear_policy import TenantLinearError
from test_whatsapp_source_control import active
from test_whatsapp_integration import setup
from test_whatsapp_native_rollout import staged_source
from test_call_sources import managed
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE

@pytest.mark.parametrize('status,body,expected',[(200,b'\x89PNG\r\n\x1a\nimage',200),(404,b'',404),(503,b'',502),(200,b'<html>not an image</html>',502),(200,b'\xff\xd8\xff'+b'x'*(1024*1024),502)],ids=['image','missing','temporary','html','oversize'])
def test_private_avatar_proxy_scope_size_and_sniff(monkeypatch,status,body,expected):
    scope={'tenant':'t','source':'s','generation':'g','account':'994100000001@s.whatsapp.net'}
    route={'conversation_id':'123456@g.us','native':{'scope':scope,'gateway_id':'gateway'}}
    async def run():
        async def serve(request):
            assert request.path=='/v1/sources/gateway/messages/avatar'
            assert request.headers['Authorization']=='Bearer synthetic'
            assert await request.json()=={'scope':scope,'chat':'123456@g.us'}
            return web.Response(body=body,status=status,content_type='application/octet-stream')
        app=web.Application();app.router.add_post('/v1/sources/gateway/messages/avatar',serve)
        async with TestServer(app) as server:
            monkeypatch.setattr(sources,'service',lambda:(str(server.make_url('')).rstrip('/'),'synthetic'))
            if expected==200:assert await avatars._fetch(route)==(body,'image/png')
            else:
                with pytest.raises(TenantLinearError) as error:await avatars._fetch(route)
                assert error.value.status==expected
    asyncio.run(run())

def test_native_avatar_preserves_route_and_rechecks_membership(active,monkeypatch):
    db,source,row,good,provider=active
    db.execute("UPDATE saas_connect_conversations SET connect_channel_id=%s,conversation_id='994100000002@s.whatsapp.net' WHERE tenant_id=%s",(source,A));db.commit()
    monkeypatch.setattr(avatars,'_connect',lambda:db)
    seen=[]
    async def fetch(route):
        seen.append(route)
        assert route['native']['scope']['source']==source
        return b'png','image/png'
    monkeypatch.setattr(avatars,'_fetch',fetch)
    params={'provider':'connect','channel_id':source,'conversation_id':'994100000002@s.whatsapp.net','lead_id':41}
    assert asyncio.run(avatars.get_avatar(PROFILE,params))==(b'png','image/png')
    with pytest.raises(TenantLinearError):asyncio.run(avatars.get_avatar({**PROFILE,'tenant_id':B},params))
    assert len(seen)==1
    async def revoked(route):
        db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s',(A,));db.commit()
        return b'png','image/png'
    monkeypatch.setattr(avatars,'_fetch',revoked)
    with pytest.raises(TenantLinearError):asyncio.run(avatars.get_avatar(PROFILE,params))

def test_transient_avatar_failure_is_not_cached_for_a_week(active,monkeypatch):
    db,source,row,good,provider=active
    db.execute("UPDATE saas_connect_conversations SET connect_channel_id=%s WHERE tenant_id=%s",(source,A));db.commit()
    monkeypatch.setattr(avatars,'_connect',lambda:db)
    async def fail(route):raise TenantLinearError('temporary',502)
    monkeypatch.setattr(avatars,'_fetch',fail)
    with pytest.raises(TenantLinearError):asyncio.run(avatars.get_avatar(PROFILE,{'provider':'connect','channel_id':source,'conversation_id':'client','lead_id':41}))
    value=db.execute('SELECT avatar_status,avatar_checked_at FROM saas_connect_conversations WHERE tenant_id=%s',(A,))[0];db.commit()
    assert value=={'avatar_status':'','avatar_checked_at':None}

