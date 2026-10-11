"""Multipart parsing is bounded and rejected before provider writes."""
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from test_tenant_hot_orders import load
from tenant_chat_upload_policy import MAX_BYTES


class Part:
    def __init__(self,name,chunks,filename=None,mime='application/json'):
        self.name=name;self.chunks=list(chunks);self.filename=filename;self.headers={'Content-Type':mime}
    def at_eof(self):return not self.chunks
    async def read_chunk(self,size):return self.chunks.pop(0)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session={'tenant_id':'company-a','telegram_id':20}
        self.run=AsyncMock(return_value={'state':'accepted','message_id':'id'})
        self.mod=load('tenant_chat_upload_api',{'run':self.run,
            'web':SimpleNamespace(json_response=lambda data,**kwargs:{'data':data,**kwargs})},
            {'tenant_chat_upload_service','aiohttp'})
        self.handle=self.mod.handler(lambda _:self.session,'https://crm.pro.az',Mock(),Mock(),Mock())
        self.data={'request_id':str(uuid.uuid4()),'lead_id':7,'text':'caption',
            'expected_tenant_id':'company-a','expected_user_id':'20'}
        raw=json.dumps(self.data).encode()
        self.file=Part('file',[b'\x89PNG\r\n\x1a\n',b'data'],'a.png','image/png')
        self.reader=SimpleNamespace(next=AsyncMock(side_effect=[Part('command',[raw[:10],raw[10:]]),self.file,None]))
        self.request=SimpleNamespace(method='POST',content_type='multipart/form-data',headers={'Origin':'https://crm.pro.az'},
            multipart=AsyncMock(return_value=self.reader))

    async def test_split_metadata_and_file_success_bound_to_session(self):
        result=await self.handle(self.request)
        self.assertEqual(result['data']['state'],'accepted');self.assertEqual(result['headers']['Cache-Control'],'no-store')
        self.assertEqual(self.run.call_args.args[0],self.session)
        self.assertEqual(self.run.call_args.args[1]['file']['size'],12)

    async def test_foreign_origin_never_reads_body(self):
        self.request.headers={'Origin':'https://crm.pro.az.evil'}
        self.assertEqual((await self.handle(self.request))['status'],403)
        self.request.multipart.assert_not_awaited();self.run.assert_not_awaited()

    async def test_wrong_company_stops_before_file(self):
        raw=json.dumps({**self.data,'expected_tenant_id':'other'}).encode()
        self.reader.next.side_effect=[Part('command',[raw]),self.file,None]
        self.assertEqual((await self.handle(self.request))['status'],409)
        self.assertEqual(self.reader.next.await_count,1);self.run.assert_not_awaited()

    async def test_oversized_file_is_rejected_before_service(self):
        self.file.chunks=[b'x'*(MAX_BYTES+1)]
        self.assertEqual((await self.handle(self.request))['status'],413)
        self.run.assert_not_awaited()

    async def test_extra_fields_rejected(self):
        raw=json.dumps(self.data).encode()
        self.reader.next.side_effect=[Part('command',[raw]),self.file,Part('extra',[b'bad'])]
        self.assertEqual((await self.handle(self.request))['status'],400);self.run.assert_not_awaited()

    async def test_encoded_file_rejected(self):
        self.file.headers['Content-Transfer-Encoding']='base64'
        self.assertEqual((await self.handle(self.request))['status'],400);self.run.assert_not_awaited()
