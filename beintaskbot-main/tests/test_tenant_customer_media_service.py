"""Synthetic media transport, durability and immutable receipt tests."""
import logging
import unittest
from unittest.mock import patch, AsyncMock
from tenant_chat_upload_policy import prepare
from tenant_customer_media_service import _checked_receipt, input_hash, run, check, _submit
from tenant_linear_policy import TenantLinearError

TENANT='a9c11a16-01d5-469a-a6c1-9a726975ae42'
CHANNEL='9644b2f2-786c-43f4-b58e-2b9b5f69d554'
REQUEST='79d6467b-3ca6-4557-9e12-b9da059c0611'
MESSAGE='6b9f1ac1-d693-4f9f-a400-d9b8372306f8'
JID='120363000001@g.us'
SESSION={'tenant_id':TENANT,'telegram_id':20}
CONTENT=b'%PDF-1.4\nsynthetic'
COMMAND=prepare({'request_id':REQUEST,'lead_id':7,'text':'salam'},'report.pdf','application/pdf',CONTENT)
DIGEST=input_hash(COMMAND['file'],COMMAND['text'],CONTENT,20,7)
ROUTE={'channel_id':CHANNEL,'conversation_id':JID,'api_key':'synthetic','author_id':20,'crm_conversation_id':7,'media_type':'document','input_hash':DIGEST}
RECEIPT={'id':MESSAGE,'operationId':MESSAGE,'channelId':CHANNEL,'conversationId':JID,'clientMessageId':REQUEST,'authorId':'20','crmConversationId':'7','retrySafe':False,'status':'sent','providerAccepted':True,'delivered':False,'externalMessageId':'PROVIDER123','type':'document','inputHash':DIGEST,'participantJid':'994501234567@s.whatsapp.net'}

class ReceiptContract(unittest.TestCase):
 def test_scope_and_hash_are_mandatory(self):
  good=_checked_receipt(RECEIPT,ROUTE,REQUEST)
  self.assertEqual(good['participant_jid'],RECEIPT['participantJid'])
  for key,value in [('channelId','other'),('conversationId','other'),('clientMessageId',MESSAGE),('authorId','21'),('crmConversationId','8'),('inputHash','a'*64),('type','image'),('retrySafe',True)]:
   with self.subTest(key=key),self.assertRaises(ValueError):_checked_receipt({**RECEIPT,key:value},ROUTE,REQUEST)
 def test_unknown_is_not_acceptance(self):
  result=_checked_receipt({**RECEIPT,'providerAccepted':False},ROUTE,REQUEST)
  self.assertEqual(result['state'],'unknown');self.assertNotIn('provider_message_id',result)
 def test_unproven_delivery_and_invalid_participant(self):
  result=_checked_receipt({**RECEIPT,'status':'read','participantJid':'123@lid'},ROUTE,REQUEST)
  self.assertEqual(result['delivery_state'],'sent');self.assertNotIn('participant_jid',result)
 def test_hash_changes_for_content_name_caption_actor(self):
  for file,text,data,actor in [({**COMMAND['file'],'name':'other.pdf'},'salam',CONTENT,20),(COMMAND['file'],'other',CONTENT,20),(COMMAND['file'],'salam',CONTENT+b'x',20),(COMMAND['file'],'salam',CONTENT,21)]:
   self.assertNotEqual(DIGEST,input_hash(file,text,data,actor,7))
 def test_submit_passes_base64_once_no_urls(self):
  with patch('tenant_customer_media_service._request',return_value=RECEIPT) as request:
   _submit(ROUTE,REQUEST,COMMAND['file'],COMMAND['text'],CONTENT)
  request.assert_called_once();body=request.call_args.args[3];self.assertEqual(body['type'],'document');self.assertNotIn('mediaUrl',body)

class MediaService(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.item={'key':(TENANT,20,REQUEST),'command':COMMAND,'connection':['connect',CHANNEL,JID]}
  patch('tenant_customer_media_service.dispatch_guarded',side_effect=lambda item,route,callback,*args:callback(route,*args)).start()
  self.reserve=patch('tenant_customer_media_service.reserve',return_value=self.item).start()
  self.mark=patch('tenant_customer_media_service.mark_sending',return_value=(SESSION,ROUTE)).start()
  self.submit=patch('tenant_customer_media_service._submit',return_value=_checked_receipt(RECEIPT,ROUTE,REQUEST)).start()
  self.record=patch('tenant_customer_media_service.record_acceptance').start()
  self.finish=patch('tenant_customer_media_service.finish').start()
  self.addCleanup(patch.stopall)
 async def test_send_persists_safe_recovery_metadata(self):
  result=await run(SESSION,COMMAND,CONTENT,logging.getLogger('test'));self.assertTrue(result['provider_accepted']);self.submit.assert_called_once();self.record.assert_called_once()
  metadata=self.mark.call_args.args[1];self.assertEqual(metadata['mime'],'application/pdf');self.assertEqual(metadata['file_name'],'report.pdf');self.assertEqual(metadata['input_hash'],DIGEST);self.assertNotIn('content',metadata)
 async def test_timeout_stays_unknown_no_retry(self):
  self.submit.side_effect=TimeoutError()
  with self.assertRaises(TenantLinearError) as error:await run(SESSION,COMMAND,CONTENT,logging.getLogger('test'))
  self.assertTrue(error.exception.keep_request);self.submit.assert_called_once();self.finish.assert_called_once_with(self.item,'unknown')
 async def test_receipt_persistence_failure_stays_unknown(self):
  self.record.side_effect=RuntimeError('db')
  with self.assertRaises(TenantLinearError):await run(SESSION,COMMAND,CONTENT,logging.getLogger('test'))
  self.submit.assert_called_once();self.finish.assert_called_once_with(self.item,'unknown')
 async def test_cached_only_checks_never_posts(self):
  self.reserve.return_value={'cached_result':{'state':'accepted'}}
  with patch('tenant_customer_media_service.check',new=AsyncMock(return_value={'state':'accepted'})) as checker:
   await run(SESSION,COMMAND,CONTENT,logging.getLogger('test'));checker.assert_awaited_once()
  self.submit.assert_not_called()
 async def test_invalid_content_never_reserves(self):
  with self.assertRaises(TenantLinearError):await run(SESSION,COMMAND,b'not-pdf',logging.getLogger('test'))
  self.reserve.assert_not_called();self.submit.assert_not_called()
 async def test_long_caption_never_reserves(self):
  with self.assertRaises(TenantLinearError):await run(SESSION,{**COMMAND,'text':'x'*4097},CONTENT,logging.getLogger('test'))
  self.reserve.assert_not_called()
 async def test_recovery_reauthorizes_unknown_without_post(self):
  item={**self.item,'credentials':ROUTE}
  with patch('tenant_customer_media_service.own_receipt',return_value=item) as own,patch('tenant_customer_media_service._connect_receipt',return_value={'provider_accepted':False,'state':'unknown'}):
   await check(SESSION,7,REQUEST);self.assertEqual(own.call_count,2)
  self.submit.assert_not_called()
