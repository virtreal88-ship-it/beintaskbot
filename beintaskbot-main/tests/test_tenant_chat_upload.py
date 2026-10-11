"""File policy, Drive destinations, journal and single external send."""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from test_tenant_hot_orders import load
from tenant_chat_upload_policy import prepare, MAX_BYTES
from tenant_chat_upload_provider import upload, upload_path
from tenant_linear_policy import TenantLinearError

FILE=str(uuid.uuid4());VERSION=str(uuid.uuid4())
DATA={'request_id':str(uuid.uuid4()),'lead_id':7,'text':''}
BODY=b'\x89PNG\r\n\x1a\nDATA'


class PolicyTests(unittest.TestCase):
    def test_empty_caption_validated_and_hash_bound(self):
        first=prepare(DATA,'a.png','image/png',BODY)
        second=prepare(DATA,'a.png','image/png',BODY+b'changed')
        self.assertEqual(first['text'],'');self.assertNotEqual(first['file']['sha256'],second['file']['sha256'])
        self.assertNotIn('content',first)

    def test_unsafe_names_types_content_and_size_rejected(self):
        for name,mime,body in (('../a.png','image/png',BODY),('a\n.png','image/png',BODY),
                               ('a.svg','image/svg+xml',BODY),('a.png','image/png',b'<html>'),
                               ('a.png','image/png',b''),('a.png','image/png',b'x'*(MAX_BYTES+1))):
            with self.assertRaises(TenantLinearError):prepare(DATA,name,mime,body)


class ProviderTests(unittest.TestCase):
    def test_signed_upload_must_be_account_drive(self):
        for url in ('https://evil.invalid/upload/token','https://drive-g.kommo.com/admin',
                    'http://drive-g.kommo.com/upload/token','https://drive-g.kommo.com/upload/'):
            with self.assertRaises(ValueError):upload_path(url,'drive-g.kommo.com')

    def test_session_bearer_not_forwarded_to_signed_upload(self):
        descriptor=prepare(DATA,'a.png','image/png',BODY)['file']
        with patch('tenant_chat_upload_provider.call',side_effect=[
            {'max_file_size':MAX_BYTES,'max_part_size':131072,'upload_url':'https://drive-g.kommo.com/upload/token'},
            {'uuid':VERSION,'file_uuid':FILE}]) as call:
            result=upload('https://drive-g.kommo.com','secret',descriptor,BODY)
        self.assertEqual(result,{'drive_uuid':FILE,'drive_version_uuid':VERSION,'type':'picture'})
        self.assertEqual(call.call_args_list[0].kwargs['bearer'],'secret')
        self.assertNotIn('bearer',call.call_args_list[1].kwargs)

    def test_version_uuid_alone_is_not_assumed_to_be_file_uuid(self):
        descriptor=prepare(DATA,'a.png','image/png',BODY)['file']
        with patch('tenant_chat_upload_provider.call',side_effect=[
            {'max_file_size':MAX_BYTES,'max_part_size':131072,'upload_url':'https://drive-g.kommo.com/upload/token'},
            {'uuid':VERSION},{'uuid':FILE,'_embedded':{'versions':[]}}]):
            with self.assertRaises(ValueError):upload('https://drive-g.kommo.com','secret',descriptor,BODY)

    def test_explicit_file_and_version_fields_accepted_without_guessing(self):
        descriptor=prepare(DATA,'a.png','image/png',BODY)['file']
        with patch('tenant_chat_upload_provider.call',side_effect=[
            {'max_file_size':MAX_BYTES,'max_part_size':131072,'upload_url':'https://drive-g.kommo.com/upload/token'},
            {'uuid':FILE,'version_uuid':VERSION}]) as call:
            result=upload('https://drive-g.kommo.com','secret',descriptor,BODY)
        self.assertEqual(result['drive_uuid'],FILE);self.assertEqual(result['drive_version_uuid'],VERSION)
        self.assertEqual(call.call_count,2)

    def test_version_only_resolved_by_exact_version_not_name(self):
        descriptor=prepare(DATA,'a.png','image/png',BODY)['file']
        with patch('tenant_chat_upload_provider.call',side_effect=[
            {'max_file_size':MAX_BYTES,'max_part_size':131072,'upload_url':'https://drive-g.kommo.com/upload/token'},
            {'uuid':VERSION}, {'_embedded':{'files':[
                {'uuid':str(uuid.uuid4()),'version_uuid':str(uuid.uuid4()),'name':'a.png'},
                {'uuid':FILE,'version_uuid':VERSION,'name':'a.png'}]}}]) as call:
            result=upload('https://drive-g.kommo.com','secret',descriptor,BODY)
        self.assertEqual(result['drive_uuid'],FILE)
        self.assertTrue(call.call_args.args[1].startswith('/v1.0/files?'))
        self.assertEqual(call.call_args.kwargs['method'],'GET')
        self.assertEqual(call.call_args.kwargs['bearer'],'secret')

    def test_timeout_never_retries_part(self):
        descriptor=prepare(DATA,'a.png','image/png',BODY)['file']
        with patch('tenant_chat_upload_provider.call',side_effect=[
            {'max_file_size':MAX_BYTES,'max_part_size':131072,'upload_url':'https://drive-g.kommo.com/upload/token'},TimeoutError()]) as call:
            with self.assertRaises(TimeoutError):upload('https://drive-g.kommo.com','secret',descriptor,BODY)
        self.assertEqual(call.call_count,2)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.command=prepare(DATA,'a.png','image/png',BODY)
        self.item={'key':('company-a',20,DATA['request_id']),'command':self.command}
        self.reserve=Mock(return_value=self.item);self.upload=Mock(return_value={'drive_uuid':FILE,'drive_version_uuid':VERSION,'type':'picture'})
        self.send=Mock(return_value='message');self.finish=Mock();self.route=AsyncMock(return_value={'talk_id':3,'origin':'instagram'})
        self.mark=Mock(return_value=({},{}))
        self.mod=load('tenant_chat_upload_service',{'reserve':self.reserve,'upload_credentials':Mock(return_value={'access_token':'key'}),
            'mark_sending':self.mark,'finish':self.finish,'resolve_route':self.route,'upload':self.upload,'send':self.send,
            'TenantPolicy':lambda _:SimpleNamespace(can_access_deal=lambda deal:deal['pipeline_id']==10)},
            {'tenant_chat_send_store','tenant_chat_send_policy','tenant_chat_upload_provider','tenant_chat_send_provider','tenant_policy'})
        self.provider=AsyncMock(side_effect=[{'drive_url':'https://drive-g.kommo.com'},{'id':7,'pipeline_id':10}])

    async def invoke(self):
        return await self.mod.run({},self.command,BODY,self.provider,Mock(),Mock())

    async def test_cached_receipt_never_uploads_or_sends(self):
        self.reserve.return_value={'cached_result':{'state':'accepted'}}
        self.assertEqual((await self.invoke())['state'],'accepted')
        self.upload.assert_not_called();self.send.assert_not_called()

    async def test_route_rechecked_after_upload_and_attachment_passed_once(self):
        self.assertEqual((await self.invoke())['state'],'accepted')
        self.assertEqual(self.route.await_count,2);self.send.assert_called_once()
        self.assertEqual(self.send.call_args.kwargs['attachment']['drive_uuid'],FILE)
        self.finish.assert_called_once_with(self.item,'accepted','message')

    async def test_changed_deal_blocks_message_after_upload(self):
        self.provider.side_effect=[{'drive_url':'drive'},{'id':7,'pipeline_id':11}]
        with self.assertRaises(TenantLinearError):await self.invoke()
        self.send.assert_not_called();self.finish.assert_called_once_with(self.item,'blocked')

    async def test_send_timeout_unknown_does_not_retry(self):
        self.send.side_effect=TimeoutError()
        with self.assertRaises(TenantLinearError) as raised:await self.invoke()
        self.assertTrue(raised.exception.keep_request);self.send.assert_called_once()
        self.finish.assert_called_once_with(self.item,'unknown')

    async def test_upload_error_never_attempts_message(self):
        self.upload.side_effect=ValueError()
        with self.assertRaises(TenantLinearError):await self.invoke()
        self.send.assert_not_called();self.finish.assert_called_once_with(self.item,'blocked')
