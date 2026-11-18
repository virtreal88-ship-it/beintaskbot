import asyncio
from unittest.mock import Mock
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
import tenant_whatsapp_integration as store
from tenant_whatsapp_integration_api import handler


def test_origin_identity_and_exact_mutation_schema(monkeypatch):
    begin=Mock(return_value={'channel_id':'synthetic'});inventory=Mock(return_value={'channels':[]})
    monkeypatch.setattr(store,'begin',begin);monkeypatch.setattr(store,'inventory',inventory)
    profile={'tenant_id':'tenant-a','telegram_id':11}
    async def run():
        app=web.Application();app.router.add_route('*','/whatsapp',handler(lambda _:profile,'https://app.invalid',Mock()))
        async with TestClient(TestServer(app)) as client:
            url='/whatsapp?expected_tenant_id=tenant-a&expected_user_id=11'
            assert (await client.get(url)).status==200
            assert (await client.get(url.replace('tenant-a','tenant-b'))).status==409
            data={'action':'begin','phone':'994100000777','name':'Synthetic'}
            assert (await client.post(url,json=data,headers={'Origin':'https://foreign.invalid'})).status==403
            assert (await client.post(url,json={**data,'fresh':True},headers={'Origin':'https://app.invalid'})).status==400
            begin.assert_not_called()
            assert (await client.post(url,json=data,headers={'Origin':'https://app.invalid'})).status==200
            begin.assert_called_once_with(profile,data['phone'],data['name'])
    asyncio.run(run())
