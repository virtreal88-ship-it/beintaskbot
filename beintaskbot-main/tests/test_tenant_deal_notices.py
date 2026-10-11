"""Approval events and deliveries are tenant-local and checkpointed."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, AsyncMock
from test_tenant_hot_orders import load
from test_tenant_crm_notice import member
from tenant_push_payload import payload_for


class PolicyTests(unittest.TestCase):
    def setUp(self):
        policy_module = load('tenant_notification_outbox', {}, {'tenant_platform'})
        self.mod = load('tenant_deal_notice_outbox', {'eligible':policy_module.eligible,'_profiles':Mock()},
                        {'tenant_platform', 'tenant_deal_completion_store','tenant_notification_outbox'})
        self.profile = member('owner', 1)
        self.profile['workflow']['policies']['members']['1']['notifications']['deal_completion_requested'] = {'telegram':True,'push':False}
        self.deal = {'tenant_id':'company-a','kommo_lead_id':7,'pipeline_id':10,'status_id':100}

    def test_explicit_preference_and_resource_required(self):
        self.assertTrue(self.mod.allowed(self.profile,self.deal,{'step':'waiting_approval'},'company-a','telegram'))
        self.assertFalse(self.mod.allowed(self.profile,self.deal,{'step':'waiting_approval'},'company-a','push'))
        self.assertFalse(self.mod.allowed(self.profile,self.deal,{'step':'done'},'company-a','telegram'))
        self.assertFalse(self.mod.allowed(self.profile,{**self.deal,'deleted_at':'now'},{'step':'waiting_approval'},'company-a','telegram'))
        self.assertFalse(self.mod.allowed(self.profile,self.deal,{'step':'waiting_approval'},'company-b','telegram'))

    def test_revoked_or_nonadmin_never_receives(self):
        for change in ({'active':False},{'role':'manager'},{'tenant_status':'inactive'}):
            self.assertFalse(self.mod.allowed({**self.profile,**change},self.deal,{'step':'waiting_approval'},'company-a','telegram'))

    def test_default_preferences_do_not_subscribe(self):
        self.profile['workflow']['policies']['members']['1']['notifications'] = {}
        self.assertFalse(self.mod.allowed(self.profile,self.deal,{'step':'waiting_approval'},'company-a','telegram'))

    def test_push_payload_contains_no_client_or_result(self):
        payload=payload_for('deal_completion_requested')
        self.assertEqual(payload['url'],'/app?view=approvals')
        self.assertNotIn('result_text',payload)

    def test_event_is_written_in_same_commit_only_on_transition(self):
        module=load('tenant_deal_completion_store',{}, {'tenant_platform'})
        store=module.DealCommandStore.__new__(module.DealCommandStore)
        store.key=('company-a',20,'request');store.state={};store.conn=MagicMock()
        cur=store.conn.cursor.return_value.__enter__.return_value
        store.save({'step':'waiting_approval'})
        self.assertEqual(cur.execute.call_count,2);store.conn.commit.assert_called_once()
        self.assertIn('saas_deal_notice_events',cur.execute.call_args.args[0])
        cur.reset_mock();store.save({'step':'waiting_approval'})
        self.assertEqual(cur.execute.call_count,1)

    def test_delivery_rechecks_resource_and_commits_checkpoint(self):
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
        item={'tenant_id':'company-a','actor_id':20,'request_id':'request','recipient_id':1,
            'channel':'telegram','endpoint_hash':'','lead_id':7,'state':{'step':'waiting_approval'}}
        cur.fetchone.side_effect=[item,self.deal]
        self.mod.claim.__globals__.update(_connect=Mock(return_value=MagicMock(__enter__=Mock(return_value=conn))),
            ensure_schema=Mock(),_profiles=Mock(return_value=[self.profile]))
        result=self.mod.claim('telegram')
        self.assertTrue(result['send']);conn.commit.assert_called_once()
        write=cur.execute.call_args
        self.assertEqual(write.args[1][0],'sending');self.assertEqual(write.args[1][1],'company-a')
        self.assertIn('status=',write.args[0])


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item={'send':True,'event':'deal_completion_requested','tenant_id':'company-a','recipient_id':1,
            'actor_id':20,'lead_id':7,'request_id':'request','tenant_name':'Company','creator_name':'Person',
            'state':{'result_text':'FULL RESULT'},'deal':{'contact_name':'Client'}}
        self.claim=Mock(side_effect=[self.item,None]);self.finish=Mock();self.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=9)))
        self.mod=load('tenant_deal_notice_worker',{'expand':Mock(),'claim':self.claim,'finish':self.finish,'send_push':Mock()},
            {'tenant_deal_notice_outbox','tenant_push_worker'})

    async def test_full_text_sent_once_and_committed(self):
        await self.mod.deliver(self.bot,'',{},Mock())
        text=self.bot.send_message.call_args.kwargs['text']
        self.assertIn('FULL RESULT',text);self.assertIn('Client',text);self.assertIn('view=approvals',text)
        self.finish.assert_called_once_with(self.item,'delivered',9)

    async def test_timeout_is_unknown_not_replayed(self):
        self.bot.send_message.side_effect=TimeoutError()
        await self.mod.deliver(self.bot,'',{},Mock())
        self.bot.send_message.assert_awaited_once();self.finish.assert_called_once_with(self.item,'unknown',None)

    async def test_skipped_delivery_never_calls_provider(self):
        self.item['send']=False
        await self.mod.deliver(self.bot,'',{},Mock())
        self.bot.send_message.assert_not_awaited();self.finish.assert_not_called()
