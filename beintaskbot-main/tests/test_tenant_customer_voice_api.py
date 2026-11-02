"""Voice ingress rejects wrong scope before decoding or provider submission."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from test_tenant_hot_orders import load
from tenant_customer_voice import MAX_RECORDING_BYTES


class Part:
    def __init__(self, name, chunks, mime='application/json'):
        self.name = name; self.chunks = list(chunks); self.headers = {'Content-Type': mime}
    def at_eof(self): return not self.chunks
    async def read_chunk(self, size): return self.chunks.pop(0)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = {'tenant_id':'company-a', 'telegram_id':20}
        self.send = AsyncMock(return_value={'state':'accepted'})
        self.can = Mock(return_value=True); self.prepare = Mock(return_value=({'request_id':'synthetic'},b'voice'))
        self.mod = load('tenant_customer_voice_api', {'run':self.send, 'can_send':self.can, 'prepare':self.prepare,
            'enabled':lambda:True, 'web':SimpleNamespace(json_response=lambda data,**kwargs:{'data':data,**kwargs})},
            {'tenant_customer_voice_service','aiohttp'})
        self.mod.handler.__globals__['enabled'] = lambda: True
        self.handle = self.mod.handler(lambda _:self.session, 'https://crm.pro.az', Mock())
        self.data = {'request_id':'synthetic', 'lead_id':7, 'expected_tenant_id':'company-a', 'expected_user_id':'20'}
        self.file = Part('file',[b'voice'],'audio/ogg')
        self.reader = SimpleNamespace(next=AsyncMock(side_effect=[Part('command',[json.dumps(self.data).encode()]),self.file,None]))
        self.request = SimpleNamespace(method='POST',content_type='multipart/form-data',headers={'Origin':'https://crm.pro.az'},
                                       multipart=AsyncMock(return_value=self.reader))

    async def test_valid_request_checks_scope_before_decoding_and_sending(self):
        result = await self.handle(self.request)
        self.assertEqual(result['data']['state'],'accepted')
        self.can.assert_called_once_with(self.session,7); self.prepare.assert_called_once(); self.send.assert_awaited_once()

    async def test_foreign_origin_and_company_do_not_send(self):
        self.request.headers = {'Origin':'https://crm.pro.az.evil'}
        self.assertEqual((await self.handle(self.request))['status'],403); self.request.multipart.assert_not_awaited()
        self.send.assert_not_awaited()
        self.request.headers = {'Origin':'https://crm.pro.az'}
        raw = json.dumps({**self.data,'expected_tenant_id':'other'}).encode()
        self.reader.next.side_effect = [Part('command',[raw]),self.file,None]
        self.assertEqual((await self.handle(self.request))['status'],409)
        self.can.assert_not_called(); self.prepare.assert_not_called(); self.send.assert_not_awaited()

    async def test_denied_scope_and_oversize_never_decode(self):
        self.can.return_value = False
        self.assertEqual((await self.handle(self.request))['status'],403)
        self.prepare.assert_not_called(); self.send.assert_not_awaited()
        self.reader.next.side_effect = [Part('command',[json.dumps(self.data).encode()]),Part('file',[b'x'*(MAX_RECORDING_BYTES+1)],'audio/ogg'),None]
        self.assertEqual((await self.handle(self.request))['status'],413); self.send.assert_not_awaited()

    async def test_disabled_feature_does_not_read_the_body(self):
        self.mod.handler.__globals__['enabled']=lambda:False
        self.assertEqual((await self.handle(self.request))['status'],404)
        self.request.multipart.assert_not_awaited(); self.send.assert_not_awaited()
