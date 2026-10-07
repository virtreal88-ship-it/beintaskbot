import ast
import asyncio
import base64
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, AsyncMock

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from test_tenant_tasks import load_service, TenantPlatformError, person


class ApiResponse(tuple):
    def __new__(cls,data,status=200,headers=None):
        result=super().__new__(cls,(status,data,headers));result.cookies={};return result
    def set_cookie(self,name,value,**kwargs):self.cookies[name]=(value,kwargs)
    def del_cookie(self,name,**kwargs):self.cookies[name]=None


def subscription():
    key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)
    encode=lambda raw:base64.urlsafe_b64encode(raw).decode().rstrip('=')
    return {'endpoint':'https://fcm.googleapis.com/fcm/send/private-device-token',
            'keys':{'p256dh':encode(key),'auth':encode(b'x'*16)}}


class PushStorageTests(unittest.TestCase):
    def setUp(self):
        self.conn=MagicMock();self.conn.__enter__.return_value=self.conn
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.cipher=Mock();self.cipher.encrypt.return_value=b'ENCRYPTED'
        self.module=load_service('tenant_push',{'_connect':Mock(return_value=self.conn),'_ensure_schema':Mock(),
                                             '_fernet':Mock(return_value=self.cipher),'TenantPlatformError':TenantPlatformError})
        self.profile=person();self.sub=subscription()

    def test_normalization_keeps_only_expected_keys_and_canonical_host(self):
        self.sub['tenant_id']='foreign';self.sub['endpoint']=self.sub['endpoint'].replace('fcm.googleapis.com','FCM.GOOGLEAPIS.COM:443')
        normalized=self.module.normalize_subscription(self.sub)
        self.assertEqual(set(normalized),{'endpoint','keys'})
        self.assertEqual(normalized['endpoint'],'https://fcm.googleapis.com/fcm/send/private-device-token')

    def test_unsafe_destinations_rejected_before_storage(self):
        for endpoint in ('http://fcm.googleapis.com/path','https://127.0.0.1/x','https://localhost/x',
                         'https://fcm.googleapis.com.attacker.test/x','https://user@fcm.googleapis.com/x',
                         'https://fcm.googleapis.com:444/x','https://fcm.googleapis.com/x#fragment',
                         'https://fcm.googleapis.com/x?different=1','https://fcm.googleapis.com/%78',
                         'https://fcm.googleapis.com\\evil/x','https://fcm.googleapis.com/',
                         'https://fcm.googleapis.com/\nprivate'):
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):
                self.module.normalize_subscription({**self.sub,'endpoint':endpoint})
        self.module._connect.assert_not_called()

    def test_invalid_keys_rejected(self):
        for keys in ({},[],{'p256dh':'bad','auth':'bad'},
                     {**self.sub['keys'],'auth':base64.urlsafe_b64encode(b'x'*15).decode()},
                     {**self.sub['keys'],'p256dh':base64.urlsafe_b64encode(b'\x04'+b'x'*64).decode()}):
            with self.subTest(keys=keys),self.assertRaises(ValueError):
                self.module.normalize_subscription({**self.sub,'keys':keys})

    def test_subscription_is_encrypted_and_bound_to_server_identity(self):
        self.cur.fetchone.side_effect=[{'active':True},{'endpoint_hash':'hash'}];self.cur.fetchall.return_value=[]
        result=self.module.register_device(self.profile,self.sub,'Phone')
        writes=self.cur.execute.call_args_list
        self.assertIn('FOR UPDATE',writes[0].args[0]);self.assertEqual(writes[0].args[1],('company-a',20))
        self.assertEqual(writes[2].args[1][1:],(20,b'ENCRYPTED'))
        self.assertIn('owner_telegram_id=EXCLUDED.owner_telegram_id',writes[2].args[0])
        self.assertEqual(writes[3].args[1][:2],('company-a',20))
        self.assertNotIn('endpoint',result);self.assertNotIn('keys',result)
        self.conn.commit.assert_called_once()

    def test_duplicate_is_idempotent_at_device_limit(self):
        digest=self.module.hashlib.sha256(self.sub['endpoint'].encode()).hexdigest()
        self.cur.fetchone.side_effect=[{'active':True},{'endpoint_hash':digest}]
        self.cur.fetchall.return_value=[{'endpoint_hash':digest},*[{'endpoint_hash':str(i)} for i in range(4)]]
        self.module.register_device(self.profile,self.sub)
        self.conn.commit.assert_called_once()

    def test_new_device_above_limit_and_stale_membership_fail_without_commit(self):
        self.cur.fetchone.return_value={'active':True}
        self.cur.fetchall.return_value=[{'endpoint_hash':str(i)} for i in range(5)]
        with self.assertRaises(TenantPlatformError):self.module.register_device(self.profile,self.sub)
        self.assertEqual(self.cur.execute.call_count,2);self.conn.commit.assert_not_called()
        self.cur.fetchone.return_value={'active':False}
        with self.assertRaises(TenantPlatformError):self.module.register_device(self.profile,self.sub)
        self.conn.commit.assert_not_called()

    def test_other_telegram_identity_cannot_take_over_device(self):
        self.cur.fetchone.side_effect=[{'active':True},None];self.cur.fetchall.return_value=[]
        with self.assertRaises(TenantPlatformError):self.module.register_device(self.profile,self.sub)
        self.assertEqual(self.cur.execute.call_count,3);self.conn.commit.assert_not_called()

    def test_list_has_no_subscription_secret_and_limits_query(self):
        self.cur.fetchall.return_value=[{'device_id':'hash','label':'Phone','updated_at':datetime.now(timezone.utc)}]
        rows=self.module.list_devices(self.profile)
        sql,params=self.cur.execute.call_args.args
        self.assertIn('LIMIT 5',sql);self.assertIn('m.active=TRUE',sql);self.assertEqual(params,('company-a',20))
        self.assertEqual(set(rows[0]),{'device_id','label','updated_at'})

    def test_disable_is_company_user_and_device_scoped(self):
        self.module.disable_device(self.profile,'a'*64)
        self.assertEqual(self.cur.execute.call_args.args[1],('company-a',20,'a'*64))
        self.assertIn('m.active=TRUE',self.cur.execute.call_args.args[0])
        with self.assertRaises(ValueError):self.module.disable_device(self.profile,'invalid')

    def test_inactive_profile_and_missing_encryption_fail_closed(self):
        inactive={**self.profile,'active':False}
        for name,args in [('list_devices',()),('register_device',(self.sub,)),('disable_device',('a'*64,))]:
            with self.assertRaises(TenantPlatformError):getattr(self.module,name)(inactive,*args)
        self.module._connect.assert_not_called()
        for identity in (-1,True,'invalid'):
            with self.assertRaises(TenantPlatformError):self.module.list_devices({**self.profile,'telegram_id':identity})
        self.module._fernet.side_effect=TenantPlatformError('Encryption unavailable')
        with self.assertRaises(TenantPlatformError):self.module.register_device(self.profile,self.sub)
        self.module._connect.assert_not_called()


class PushApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source=(Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig')
        nodes=[node for node in ast.parse(source).body if isinstance(node,ast.AsyncFunctionDef) and node.name=='handle_platform_push_devices']
        self.profile=person();self.register=Mock(return_value={'device_id':'hash'});self.disable=Mock();self.list=Mock(return_value=[])
        self.log=Mock()
        namespace={'TENANT_PUSH_MAX_DEVICES':5,'web':SimpleNamespace(Request=object,Response=object,json_response=ApiResponse),
                   '_tenant_member_from_request':lambda _:self.profile,'asyncio':asyncio,'VAPID_PUBLIC_KEY':'public','VAPID_PRIVATE_KEY':'private',
                   'CANONICAL_WEB_ORIGIN':'https://crm.pro.az','TenantPlatformError':TenantPlatformError,'logger':self.log,
                   'register_tenant_push_device':self.register,'disable_tenant_push_device':self.disable,'list_tenant_push_devices':self.list}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<push-api>','exec'),namespace)
        self.api=namespace['handle_platform_push_devices']
        self.request=SimpleNamespace(method='POST',content_type='application/json',headers={'Origin':'https://crm.pro.az'},
                                     json=AsyncMock(return_value={'action':'subscribe','subscription':{},'tenant_id':'other','telegram_id':99}))

    async def test_identity_is_session_only_and_response_is_no_store(self):
        status,data,headers=await self.api(self.request)
        self.assertEqual(status,200);self.register.assert_called_once_with(self.profile,{},'')
        self.assertTrue(data['delivery_enabled']);self.assertEqual(headers['Cache-Control'],'no-store')

    async def test_missing_foreign_origin_and_wrong_type_deny_before_storage(self):
        for headers in ({},{'Origin':'https://evil.test'}):
            self.request.headers=headers
            self.assertEqual((await self.api(self.request))[0],403)
        self.register.assert_not_called()
        self.request.headers={'Origin':'https://crm.pro.az'};self.request.content_type='text/plain'
        self.assertEqual((await self.api(self.request))[0],403)

    async def test_inactive_profile_denied(self):
        self.profile['active']=False
        self.assertEqual((await self.api(self.request))[0],403);self.register.assert_not_called()

    async def test_invalid_request_and_action_are_rejected(self):
        for value in ([],{'action':'unknown'}):
            self.request.json.return_value=value
            self.assertEqual((await self.api(self.request))[0],400)
        self.register.assert_not_called()

    async def test_get_only_returns_current_user_devices_and_public_key(self):
        self.request.method='GET'
        status,data,headers=await self.api(self.request)
        self.assertEqual(status,200);self.list.assert_called_once_with(self.profile)
        self.assertEqual(data['public_key'],'public');self.assertEqual(data['max_devices'],5)
        self.assertTrue(data['delivery_enabled'])

    async def test_profile_change_precondition_prevents_rebinding_to_other_company(self):
        self.request.json.return_value={'action':'subscribe','subscription':{},'expected_tenant_id':'old-company','expected_user_id':20}
        self.assertEqual((await self.api(self.request))[0],409);self.register.assert_not_called()

    async def test_subscribe_sets_only_http_only_browser_device_cookie(self):
        response=await self.api(self.request)
        self.assertEqual(response.cookies['tenant_push_device'][0],'hash')
        self.assertTrue(response.cookies['tenant_push_device'][1]['httponly'])
        self.assertTrue(response.cookies['tenant_push_device'][1]['secure'])

    async def test_disable_scoped_to_current_profile(self):
        self.request.json.return_value={'action':'unsubscribe','device_id':'a'*64,'tenant_id':'other'}
        self.assertEqual((await self.api(self.request))[0],200)
        self.disable.assert_called_once_with(self.profile,'a'*64)

    async def test_unexpected_error_does_not_log_secrets(self):
        self.register.side_effect=RuntimeError('SECRET_ENDPOINT_AUTH_KEY')
        self.assertEqual((await self.api(self.request))[0],500)
        self.assertNotIn('SECRET',str(self.log.error.call_args))


if __name__=='__main__':unittest.main()
