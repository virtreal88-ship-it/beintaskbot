import ast
import asyncio
import hashlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
from test_tenant_push import ApiResponse


def load(name,namespace):
    tree=ast.parse((Path(__file__).resolve().parents[1]/(name+'.py')).read_text(encoding='utf-8'))
    tree.body=[node for node in tree.body if not isinstance(node,ast.ImportFrom) or node.module not in
               {'tenant_platform','tenant_push','tenant_push_outbox','tenant_notification_outbox'}]
    module=ModuleType(name);module.__dict__.update(namespace);exec(compile(tree,name,'exec'),module.__dict__);return module


def delivery():
    return {'tenant_id':'company-a','actor_id':20,'request_id':'request-a','recipient_id':1,
            'endpoint_hash':'a'*64,'send':True,'event':'task_completion_requested','subscription':b'ENCRYPTED',
            'device_active':True,'owner_telegram_id':1,'state':{'step':'waiting_approval'}}


class PushQueueTests(unittest.TestCase):
    def setUp(self):
        self.conn=MagicMock();self.conn.__enter__.return_value=self.conn
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.profiles=Mock(return_value=[{'role':'owner'}]);self.eligible=Mock(return_value=True)
        self.module=load('tenant_push_outbox',{'_connect':Mock(return_value=self.conn),'_ensure_schema':Mock(),
                                             '_profiles':self.profiles,'eligible':self.eligible})

    def test_claim_is_locked_and_rechecks_device_and_company(self):
        item=delivery();self.cur.fetchone.return_value=item
        self.assertTrue(self.module.claim_push()['send'])
        self.assertIn('SKIP LOCKED',self.cur.execute.call_args_list[1].args[0])
        self.profiles.assert_called_once_with(self.cur,'company-a',1)
        self.eligible.assert_called_once_with({'role':'owner'},'task_completion_requested','company-a','push')
        self.assertEqual(self.cur.execute.call_args.args[1][0],'sending');self.conn.commit.assert_called_once()

    def test_disabled_foreign_resolved_or_revoked_delivery_is_skipped(self):
        for change in ({'device_active':False},{'owner_telegram_id':2},{'state':{'step':'done'}}):
            self.cur.fetchone.return_value={**delivery(),**change}
            self.assertFalse(self.module.claim_push()['send'])
            self.assertEqual(self.cur.execute.call_args.args[1][0],'skipped')
        self.cur.fetchone.return_value=delivery();self.eligible.return_value=False
        self.assertFalse(self.module.claim_push()['send'])

    def test_expired_device_is_disabled_only_for_its_owner(self):
        self.module.finish_push(delivery(),'expired')
        self.assertIn("status='sending'",self.cur.execute.call_args_list[0].args[0])
        self.assertEqual(self.cur.execute.call_args.args[1],('a'*64,1))
        with self.assertRaises(ValueError):self.module.finish_push(delivery(),'pending')


class PushPlanningTests(unittest.TestCase):
    def test_push_only_preference_does_not_plan_telegram(self):
        conn=MagicMock();conn.__enter__.return_value=conn
        cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value=[delivery()]
        module=load('tenant_notification_outbox',{'_connect':Mock(return_value=conn),'_ensure_schema':Mock()})
        module._profiles=Mock(return_value=[{'telegram_id':1}])
        module.eligible=lambda profile,event,tenant,channel='telegram':channel=='push'
        self.assertEqual(module.expand_events(),1)
        queries=[call.args[0] for call in cur.execute.call_args_list]
        self.assertTrue(any('INSERT INTO saas_approval_push_deliveries' in sql for sql in queries))
        self.assertFalse(any('INSERT INTO saas_approval_notification_deliveries' in sql for sql in queries))
        self.assertTrue(any('p.owner_telegram_id=b.telegram_id' in sql for sql in queries))
        conn.commit.assert_called_once()

    def test_logout_storage_targets_only_own_browser_across_companies(self):
        conn=MagicMock();conn.__enter__.return_value=conn
        cur=conn.cursor.return_value.__enter__.return_value
        module=load('tenant_push',{'_connect':Mock(return_value=conn),'_ensure_schema':Mock()})
        module.disable_browser_device({'tenant_id':'company-a','telegram_id':1,'active':True},'a'*64)
        query,params=cur.execute.call_args.args
        self.assertEqual(params,(1,'a'*64,1))
        self.assertIn('d.owner_telegram_id=%s',query)
        self.assertNotIn('b.tenant_id=',query)


class PushTransportTests(unittest.IsolatedAsyncioTestCase):
    def test_transport_never_follows_provider_redirects(self):
        module=load('tenant_push_worker',{'claim_push':Mock(),'finish_push':Mock()})
        with patch.object(module.requests.Session,'request',return_value=SimpleNamespace(status_code=302)) as request:
            with module.NoRedirectSession() as session:
                session.post('https://fcm.googleapis.com/fcm/send/token',allow_redirects=True)
        self.assertIs(request.call_args.kwargs['allow_redirects'],False)

    def setUp(self):
        self.claim=Mock(side_effect=[delivery(),None]);self.finish=Mock();self.log=Mock()
        self.module=load('tenant_push_worker',{'claim_push':self.claim,'finish_push':self.finish})
        self.send=Mock();self.module.send_push=self.send

    async def test_success_and_unknown_failure_are_single_attempts(self):
        await self.module.deliver_push_notifications('key',{},self.log)
        self.assertEqual(self.finish.call_args.args[1],'delivered');self.send.assert_called_once()
        self.claim.side_effect=[delivery(),None];self.send.reset_mock();self.send.side_effect=TimeoutError('SECRET_ENDPOINT')
        await self.module.deliver_push_notifications('key',{},self.log)
        self.assertEqual(self.finish.call_args.args[1],'unknown');self.send.assert_called_once()
        self.assertNotIn('SECRET',str(self.log.warning.call_args))

    async def test_410_expires_and_missing_key_does_not_claim(self):
        error=RuntimeError();error.response=SimpleNamespace(status_code=410);self.send.side_effect=error
        await self.module.deliver_push_notifications('key',{},self.log)
        self.assertEqual(self.finish.call_args.args[1],'expired')
        self.claim.reset_mock();await self.module.deliver_push_notifications('',{},self.log);self.claim.assert_not_called()

    async def test_batch_is_bounded_and_skipped_rows_are_not_sent(self):
        self.claim.side_effect=None;self.claim.return_value={**delivery(),'send':False}
        await self.module.deliver_push_notifications('key',{},self.log)
        self.assertEqual(self.claim.call_count,10);self.send.assert_not_called()

    def test_external_payload_has_no_private_text_or_identifiers(self):
        endpoint='https://fcm.googleapis.com/fcm/send/token'
        sub={'endpoint':endpoint,'keys':{}}
        cipher=Mock();cipher.decrypt.return_value=json.dumps(sub).encode()
        transport=Mock(return_value=SimpleNamespace(status_code=201));session=MagicMock()
        module=load('tenant_push_worker',{'_fernet':Mock(return_value=cipher),'normalize_subscription':lambda value:value,
                                        'claim_push':Mock(),'finish_push':Mock()})
        module.webpush=transport;module.NoRedirectSession=Mock(return_value=session)
        item={**delivery(),'endpoint_hash':hashlib.sha256(endpoint.encode()).hexdigest()}
        module.send_push(item,'key',{})
        payload=json.loads(transport.call_args.kwargs['data'])
        self.assertEqual(payload['url'],'/app')
        for private in ('company-a','request-a','recipient_id','actor_id','subscription','state'):
            self.assertNotIn(private,transport.call_args.kwargs['data'])
        self.assertEqual(transport.call_args.kwargs['timeout'],15)


if __name__=='__main__':unittest.main()
