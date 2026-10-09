from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
import test_tenant_hot_orders as loaders
from tenant_task_decision_policy import outcome, eligible, EVENT
from test_tenant_chat_send import TENANT, SESSION, INPUT

STATE={'step':'done','approval':{'status':'approved','reviewer_id':1},
       'completion_input':{'text':'Full task','result_text':'Full result','task_id':7},'result':{'task_id':7}}


def person():
    return {**SESSION,'active':True,'tenant_status':'active','role':'worker','permissions':['tasks'],
        'modules':{'tasks':True},'workflow':{'policies':{'members':{'20':{'notifications':{EVENT:{'telegram':True,'push':True}}}}}}}


class Policy(unittest.TestCase):
    def test_final_states_only_not_external_checkpoint(self):
        self.assertEqual(outcome(STATE),'approved')
        for step in ['waiting_approval','approval_accepted','task_completing','task_creating']:
            self.assertIsNone(outcome({**STATE,'step':step}))
        self.assertIsNone(outcome({**STATE,'approval':{'status':'not_required'}}))
        self.assertIsNone(outcome({**STATE,'approval':{'status':'approved','reviewer_id':0}}))
        self.assertEqual(outcome({**STATE,'step':'rejected','approval':{'status':'rejected','reviewer_id':1}}),'rejected')

    def test_only_own_tenant_command_explicit_channels(self):
        item={'tenant_id':TENANT,'actor_id':20,'state':STATE,'outcome':'approved'}
        self.assertTrue(eligible(person(),item,'telegram'));self.assertTrue(eligible(person(),item,'push'))
        for change in [{'tenant_id':'other'},{'telegram_id':21},{'active':False},{'tenant_status':'suspended'},
                       {'permissions':[]},{'workflow':{}},{'modules':{'tasks':False}}]:
            self.assertFalse(eligible({**person(),**change},item,'telegram'))
        self.assertFalse(eligible(person(),{**item,'outcome':'rejected'},'telegram'))


class Store(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.module=loaders.load('tenant_task_decision_outbox',{'_connect':self.connect,'ensure_schema':Mock(),
            '_read_tenant_workflow':Mock(return_value=person()['workflow'])}, {'tenant_platform','tenant_task_decision_schema'})
        self.item={'tenant_id':TENANT,'actor_id':20,'recipient_id':20,'request_id':INPUT['request_id'],
            'channel':'telegram','endpoint_hash':'','state':STATE,'outcome':'approved',
            'subscription':b'encrypted','owner_telegram_id':20,'device_active':True}

    def test_expand_own_recipient_both_channels_once(self):
        self.cur.fetchall.return_value=[self.item];self.cur.fetchone.return_value=person()
        self.assertEqual(self.module.expand_events(),1)
        insertions=[call for call in self.cur.execute.call_args_list if call.args[0].startswith('INSERT')]
        self.assertEqual(len(insertions),2)
        self.assertEqual(insertions[0].args[1],(TENANT,20,INPUT['request_id'],20))
        self.assertIn('p.owner_telegram_id=b.telegram_id',insertions[1].args[0])
        self.assertTrue(all('ON CONFLICT DO NOTHING' in call.args[0] for call in insertions))

    def test_claim_checkpoints_and_commit_before_transport(self):
        self.cur.fetchone.side_effect=[self.item,person()]
        item=self.module.claim_delivery('telegram');self.assertTrue(item['send'])
        self.assertEqual(self.cur.execute.call_args.args[1][0],'sending');self.conn.commit.assert_called_once()
        self.assertIn("status='sending'",self.cur.execute.call_args_list[0].args[0])

    def test_revoked_membership_or_foreign_device_skipped(self):
        for member,device in [(None,self.item),(person(),{**self.item,'channel':'push','owner_telegram_id':21})]:
            self.cur.fetchone.side_effect=[device,member]
            item=self.module.claim_delivery(device['channel']);self.assertFalse(item['send'])
            self.assertEqual(self.cur.execute.call_args.args[1][0],'skipped')

    def test_finish_cas_and_expire_device_scoped(self):
        self.module.finish_delivery({**self.item,'channel':'push','endpoint_hash':'hash'},'expired')
        calls=self.cur.execute.call_args_list
        self.assertIn("AND status='sending'",calls[0].args[0]);self.assertEqual(calls[-1].args[1],(TENANT,20,'hash'))

    def test_empty_queue_commits_stale_transition(self):
        self.cur.fetchone.return_value=None
        self.assertIsNone(self.module.claim_delivery('telegram'));self.conn.commit.assert_called_once()


class Command(unittest.TestCase):
    def test_decision_event_is_same_transaction_and_terminal_retry_is_quiet(self):
        import test_tenant_tasks as task_tests
        store=task_tests.commands.TaskCommandStore.__new__(task_tests.commands.TaskCommandStore)
        store.conn=MagicMock();cur=store.conn.cursor.return_value.__enter__.return_value
        store.key=(TENANT,20,INPUT['request_id']);store.state={**STATE,'step':'waiting_approval'}
        schema=Mock()
        with patch.dict(sys.modules, {'tenant_task_decision_schema':SimpleNamespace(ensure_schema=schema)}):
            store.save({'step':'done','result':{'task_id':7}})
            self.assertTrue(any('INSERT INTO saas_task_decision_events' in c.args[0] for c in cur.execute.call_args_list))
            schema.assert_called_once_with(store.conn);store.conn.commit.assert_called_once()
            cur.reset_mock();store.save({'step':'done','result':{'task_id':7}})
            self.assertFalse(any('saas_task_decision_events' in c.args[0] for c in cur.execute.call_args_list))

    def test_failed_event_insert_cannot_commit_final_state(self):
        import test_tenant_tasks as task_tests
        store=task_tests.commands.TaskCommandStore.__new__(task_tests.commands.TaskCommandStore)
        store.conn=MagicMock();cur=store.conn.cursor.return_value.__enter__.return_value
        store.key=(TENANT,20,INPUT['request_id']);store.state={**STATE,'step':'waiting_approval'}
        cur.execute.side_effect=[None,RuntimeError('event')]
        with patch.dict(sys.modules, {'tenant_task_decision_schema':SimpleNamespace(ensure_schema=Mock())}):
            with self.assertRaises(RuntimeError):store.save({'step':'done'})
        store.conn.commit.assert_not_called();self.assertEqual(store.state['step'],'waiting_approval')


class Worker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item={'tenant_id':TENANT,'actor_id':20,'recipient_id':20,'request_id':INPUT['request_id'],
            'send':True,'outcome':'approved','state':STATE,'tenant_name':'Company','event':EVENT}
        self.finish=Mock();self.claim=Mock(side_effect=[self.item,None,self.item,None]);self.push=Mock()
        self.module=loaders.load('tenant_task_decision_worker',{'expand_events':Mock(),'claim_delivery':self.claim,
            'finish_delivery':self.finish,'send_push':self.push,
            'message_parts':loaders.load('tenant_notification_worker',{}, {'tenant_notification_outbox'}).message_parts},
            {'tenant_task_decision_outbox','tenant_push_worker','tenant_notification_worker'})
        self.bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=3)))

    async def test_full_text_result_link_and_both_channels(self):
        text=self.module.message(self.item)
        for expected in ['Full task','Full result','Company','Təsdiqləndi','https://crm.pro.az/app?view=tasks']:self.assertIn(expected,text)
        await self.module.deliver(self.bot,'key',{},Mock())
        self.bot.send_message.assert_awaited_once();self.push.assert_called_once();self.assertEqual(self.finish.call_count,2)
        self.assertIsNone(self.bot.send_message.call_args.kwargs['parse_mode'])

    async def test_unknown_telegram_attempt_is_not_retried(self):
        self.claim.side_effect=[self.item,None];self.bot.send_message.side_effect=RuntimeError('timeout')
        await self.module.deliver(self.bot,'',{},Mock())
        self.finish.assert_called_once_with(self.item,'unknown',None);self.bot.send_message.assert_awaited_once()

    async def test_skipped_and_missing_vapid_never_send(self):
        self.claim.side_effect=[{**self.item,'send':False},None]
        await self.module.deliver(self.bot,'',{},Mock())
        self.bot.send_message.assert_not_awaited();self.push.assert_not_called();self.finish.assert_not_called()


if __name__=='__main__':unittest.main()
