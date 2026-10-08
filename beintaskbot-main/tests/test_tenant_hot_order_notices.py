"""Outbox privacy, tenant rights, atomic enqueue and bounded transport."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, AsyncMock
import test_tenant_hot_orders as loaders
import test_tenant_hot_order_policy as fixtures


def person():
    row=fixtures.person();row['tenant_status']='active'
    row['workflow']['policies']['members']['20']['notifications']={'hot_order_available':{'telegram':True,'push':True}}
    return row


def item(channel='telegram'):
    return {'tenant_id':'company-a','event_id':'event-1','recipient_id':20,'channel':channel,
            'endpoint_hash':'' if channel=='telegram' else 'endpoint', 'order_version':123,'current_version':123,
            'order_status':'open','service_id':'repair','device_active':True,'owner_telegram_id':20,
            'subscription':b'ENCRYPTED','send':True}


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.conn=MagicMock();self.conn.__enter__.return_value=self.conn
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.module=loaders.load('tenant_hot_order_outbox',{'_connect':Mock(return_value=self.conn),
            '_read_tenant_workflow':Mock(),'ensure_hot_order_schema':Mock()},
            {'tenant_platform','tenant_hot_order_schema'})
        self.people=Mock(return_value=[person()])

    def mocked_profiles(self):
        self.module.claim_delivery.__globals__['profiles']=self.people

    def test_eligibility_requires_service_channel_role_and_same_company(self):
        p=person();order=fixtures.order()
        self.assertTrue(self.module.eligible(p,order,'telegram'))
        changes=[('active',False),('permissions',[]),('tenant_id','foreign'),('tenant_status','disabled')]
        for key,value in changes:
            test=copy.deepcopy(p);test[key]=value
            self.assertFalse(self.module.eligible(test,order,'telegram'))
        for service in ('old','unknown'):
            self.assertFalse(self.module.eligible(p,fixtures.order(service_id=service),'telegram'))
        p['workflow']['policies']['members']['20']['notifications']['hot_order_available']['telegram']=False
        self.assertFalse(self.module.eligible(p,order,'telegram'));self.assertTrue(self.module.eligible(p,order,'push'))

    def test_claim_rechecks_membership_and_order_before_sending(self):
        self.mocked_profiles();self.cur.fetchone.return_value=item()
        self.assertTrue(self.module.claim_delivery('telegram')['send'])
        self.people.assert_called_once_with(self.cur,'company-a',recipient=20)
        self.assertIn('SKIP LOCKED',self.cur.execute.call_args_list[1].args[0])
        self.assertEqual(self.cur.execute.call_args.args[1][0],'sending');self.conn.commit.assert_called_once()

    def test_reopened_new_version_claimed_or_cancelled_notice_is_skipped(self):
        self.mocked_profiles()
        for change in ({'current_version':124},{'order_status':'claimed'},{'order_status':'cancelled'}):
            self.cur.fetchone.return_value={**item(),**change}
            self.assertFalse(self.module.claim_delivery('telegram')['send'])
            self.assertEqual(self.cur.execute.call_args.args[1][0],'skipped')
        self.people.return_value=[];self.cur.fetchone.return_value=item()
        self.assertFalse(self.module.claim_delivery('telegram')['send'])

    def test_push_requires_active_device_owned_by_this_member(self):
        self.mocked_profiles()
        for change in ({'device_active':False},{'owner_telegram_id':99},{'subscription':None}):
            self.cur.fetchone.return_value={**item('push'),**change}
            self.assertFalse(self.module.claim_delivery('push')['send'])

    def test_finished_delivery_cannot_be_replayed_and_expiry_is_tenant_scoped(self):
        self.module.finish_delivery(item('push'),'expired')
        calls=self.cur.execute.call_args_list
        self.assertIn("status='sending'",calls[0].args[0])
        self.assertEqual(calls[1].args[1],('company-a',20,'endpoint'))
        self.assertIn('tenant_id=%s::uuid',calls[1].args[0])

    def test_expansion_is_paged_and_deduplicates_each_channel(self):
        self.mocked_profiles()
        event={'tenant_id':'company-a','id':'event-1','status':'open','service_id':'repair',
               'order_version':123,'current_version':123,'recipient_cursor':0}
        self.cur.fetchall.return_value=[event]
        self.assertEqual(self.module.expand_events(),1)
        sql=[call.args[0] for call in self.cur.execute.call_args_list]
        self.assertTrue(any("'telegram'" in q and 'ON CONFLICT DO NOTHING' in q for q in sql))
        self.assertTrue(any('owner_telegram_id=b.telegram_id' in q for q in sql))
        self.assertEqual(self.cur.execute.call_args.args[1][:2],(True,20))
        self.people.return_value=[person() for _ in range(100)]
        self.module.expand_events();self.assertFalse(self.cur.execute.call_args.args[1][0])

    def test_outdated_events_do_not_expand_to_new_recipients(self):
        self.mocked_profiles();self.cur.fetchall.return_value=[{'tenant_id':'company-a','id':'e',
            'status':'claimed','service_id':'repair','current_version':124,'order_version':123,'recipient_cursor':0}]
        self.module.expand_events();self.people.assert_not_called()

    def test_profile_lookup_is_tenant_scoped_and_bounded(self):
        self.cur.fetchall.return_value=[];self.module.profiles(self.cur,'company-a',after=40)
        sql,params=self.cur.execute.call_args.args
        self.assertIn('LIMIT 100',sql);self.assertIn('m.active=TRUE',sql)
        self.assertEqual(params,('company-a',40,None,None))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.claim=Mock(side_effect=[item(),None]);self.finish=Mock();self.push=Mock()
        self.worker=loaders.load('tenant_hot_order_notification_worker',{'expand_events':Mock(),
            'claim_delivery':self.claim,'finish_delivery':self.finish,'send_push':self.push},
            {'tenant_hot_order_outbox','tenant_push_worker'})
        self.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7)))
        self.log=Mock()

    async def test_telegram_payload_is_generic_and_success_is_checkpointed(self):
        await self.worker.deliver_hot_order_notifications(self.bot,'',{},self.log)
        data=self.bot.send_message.await_args.kwargs
        self.assertIn('/app?view=hot_orders',data['text']);self.assertIsNone(data['parse_mode'])
        for private in ('company-a','event-1','repair','description','client_name'):
            self.assertNotIn(private,data['text'])
        self.assertEqual(self.finish.call_args.args[1:],('delivered',7))
        self.assertEqual([c.args[0] for c in self.claim.call_args_list],['telegram','telegram'])

    async def test_unknown_send_is_not_retried_or_logged_with_private_details(self):
        self.bot.send_message.side_effect=TimeoutError('SECRET CUSTOMER')
        await self.worker.deliver_hot_order_notifications(self.bot,'',{},self.log)
        self.bot.send_message.assert_awaited_once();self.assertEqual(self.finish.call_args.args[1],'unknown')
        self.assertNotIn('SECRET',str(self.log.warning.call_args))

    async def test_worker_is_bounded_and_missing_push_key_does_not_claim_push(self):
        self.claim.side_effect=None;self.claim.return_value={**item(),'send':False}
        await self.worker.deliver_hot_order_notifications(self.bot,'',{},self.log)
        self.assertEqual(self.claim.call_count,10);self.push.assert_not_called()

    async def test_expired_push_has_its_own_checkpoint(self):
        self.claim.side_effect=[None,item('push'),None]
        error=RuntimeError();error.response=SimpleNamespace(status_code=410);self.push.side_effect=error
        await self.worker.deliver_hot_order_notifications(self.bot,'key',{},self.log)
        self.assertEqual(self.finish.call_args.args[1],'expired');self.bot.send_message.assert_not_awaited()
