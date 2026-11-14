import json
import unittest
from unittest.mock import AsyncMock,Mock,patch
from tenant_customer_media_api import handler,read_handler
from tenant_linear_policy import TenantLinearError

class ApiTests(unittest.IsolatedAsyncioTestCase):
 async def test_disabled_route_never_reads_body(self):
  with patch('tenant_customer_media_api.enabled',return_value=False),patch('tenant_customer_media_api.upload_handler',return_value=AsyncMock()) as upload:
   handle=handler(Mock(),'https://crm.test',Mock());response=await handle(Mock())
  self.assertEqual(response.status,404);upload.return_value.assert_not_awaited()
 async def test_caption_and_quote_rejected_before_service(self):
  captured={}
  def factory(*args,**kwargs):captured['runner']=kwargs['runner'];return AsyncMock()
  with patch('tenant_customer_media_api.upload_handler',side_effect=factory),patch('tenant_customer_media_api.run',new=AsyncMock()) as run:
   handler(Mock(),'https://crm.test',Mock())
   for command in ({'file':{'type':'file'},'text':'x'*4097},{'file':{'type':'audio'}},{'file':{'type':'file'},'quote_message_id':'id'}):
    with self.assertRaises(TenantLinearError):await captured['runner']({},command,b'',None,None,None)
   run.assert_not_awaited()
 async def test_disabled_capability_is_false(self):
  with patch('tenant_customer_media_api.require_read_session',return_value={}),patch('tenant_customer_media_api.enabled',return_value=False),patch('tenant_customer_media_api.capability') as capability:
   response=await read_handler(Mock(),Mock())(Mock())
  self.assertEqual(json.loads(response.body)['enabled'],False);capability.assert_not_called()
 async def test_recovery_error_preserves_request(self):
  request=Mock();request.query={'lead_id':'1','request_id':'test'}
  with patch('tenant_customer_media_api.require_read_session',return_value={}),patch('tenant_customer_media_api.check',new=AsyncMock(side_effect=RuntimeError('secret'))):
   response=await read_handler(Mock(),Mock(),receipt=True)(request)
  data=json.loads(response.body);self.assertTrue(data['keep_request']);self.assertNotIn('secret',str(data));self.assertEqual(response.status,503)
