"""CRM notices: no old broadcasts, shared Kommo seats, cross-tenant data or retries."""
import copy
import unittest
from unittest.mock import Mock, MagicMock, AsyncMock
from datetime import datetime, timezone
from test_tenant_tasks import person
from test_tenant_hot_orders import load
from tenant_crm_notice_policy import transitions, eligible, task_route, timestamp
from tenant_push_payload import payload_for


def member(role='manager', identity=20):
    p = person(role, identity)
    p.update(tenant_status='active', permissions=['tasks','deals','customers'], modules={'tasks':True,'deals':True,'customers':True})
    p['workflow']['policies'] = {'members':{str(identity):{'notifications':{
        event:{'telegram':True,'push':True} for event in ('task_assigned','task_overdue','incoming_message','new_lead')}}}}
    return p


def task():
    return {'tenant_id':'company-a','kommo_task_id':9,'kommo_lead_id':7,'text':'Call client',
            'completed':False,'due_at':110,'raw':{'created_at':105},
            'deal':{'tenant_id':'company-a','kommo_lead_id':7,'pipeline_id':10,'status_id':100}}


class NoticePolicyTests(unittest.TestCase):
    def test_old_first_scan_is_not_broadcast(self):
        row=task();row['raw']['created_at']=90;row['due_at']=90
        state,events=transitions('task',row,None,100,120)
        self.assertEqual(events,[])
        self.assertEqual(transitions('task',row,state,100,121)[1],[])

    def test_new_task_and_overdue_only_once(self):
        row=task();state,events=transitions('task',row,None,100,120)
        self.assertEqual([e[0] for e in events],['task_assigned','task_overdue'])
        self.assertEqual(transitions('task',row,state,100,121)[1],[])
        row['completed']=True
        self.assertEqual(transitions('task',row,state,100,122)[1],[])

    def test_transfer_revokes_old_recipient_and_notifies_new_owner(self):
        row=task();state,_=transitions('task',row,None,100,106)
        self.assertTrue(eligible(member(), 'task_assigned',row,'telegram'))
        row['deal'].update(pipeline_id=11,status_id=110)
        self.assertFalse(eligible(member(),'task_overdue',row,'telegram'))
        self.assertTrue(eligible(member(identity=21),'task_overdue',row,'telegram'))
        other,events=transitions('task',row,state,100,120)
        self.assertEqual([e[0] for e in events],['task_assigned','task_overdue'])
        row['deal'].update(pipeline_id=10,status_id=100)
        self.assertNotEqual(transitions('task',row,other,100,121)[1][0][1],events[0][1])

    def test_stage_and_shared_responsible_id_are_not_reassignment(self):
        row=task();state,_=transitions('task',row,None,100,106)
        row['deal']['status_id']=101;row['responsible_id']=999
        self.assertEqual(transitions('task',row,state,100,106)[1],[])

    def test_admin_only_receives_personal_pipeline_tasks(self):
        self.assertFalse(eligible(member('admin',1),'task_assigned',task(),'telegram'))
        self.assertTrue(eligible(member('admin',20),'task_assigned',task(),'telegram'))

    def test_worker_marker_not_shared_provider_user(self):
        from tenant_policy import TenantPolicy
        row=task();p=member('worker');row['responsible_id']=20
        self.assertFalse(eligible(p,'task_assigned',row,'telegram'))
        row['text']+='\n'+TenantPolicy(p).task_marker()
        self.assertTrue(eligible(p,'task_assigned',row,'telegram'))

    def test_tenant_status_permissions_preferences_all_rechecked(self):
        for change in ({'tenant_id':'other'},{'active':False},{'tenant_status':'disabled'},
                       {'permissions':[]},{'notification_rules':{'telegram':False}}):
            p=member();p.update(change)
            self.assertFalse(eligible(p,'task_assigned',task(),'telegram'))
        p=member();p['workflow']['policies']={}
        self.assertFalse(eligible(p,'task_assigned',task(),'telegram'))

    def test_messages_only_incoming_and_success_always_hidden(self):
        row={**task(),'direction':'incoming','happened_at':105}
        self.assertTrue(eligible(member(),'incoming_message',row,'telegram'))
        row['direction']='outgoing'
        self.assertFalse(eligible(member(),'incoming_message',row,'telegram'))
        row['direction']='incoming';row['deal']['status_id']=142
        self.assertFalse(eligible(member(),'incoming_message',row,'telegram'))
        self.assertEqual(transitions('message',row,None,110,120)[1],[])

    def test_timestamp_is_finite_and_timezone_aware(self):
        for value in (None,True,float('nan'),float('inf'),'bad',datetime(2026,1,1)):
            self.assertEqual(timestamp(value),0)
        self.assertEqual(timestamp('105'),105)

    def test_push_never_contains_client_data(self):
        for event in ('task_assigned','task_overdue','new_lead','incoming_message'):
            self.assertEqual(payload_for(event)['event'],event)
            self.assertNotIn('client',str(payload_for(event)))


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.p=member();self.row=task()
        self.box=load('tenant_crm_notice_outbox',{'_connect':self.connect,'ensure_schema':Mock(),
            'profiles':Mock(return_value=[self.p]),'resource':Mock(return_value=self.row),'connection_id':Mock(return_value='connection')},
            {'tenant_platform','tenant_crm_notice_schema','tenant_crm_notice_store'})
        self.item={'tenant_id':'company-a','event_id':'event','recipient_id':20,'channel':'telegram','endpoint_hash':'',
                   'event':'task_assigned','kind':'task','source_key':'9','payload':{'connection':'connection','route':task_route(self.row)},
                   'subscription':{},'device_active':True,'owner_telegram_id':20}
        self.cur.fetchone.return_value=copy.deepcopy(self.item)

    def test_claim_commits_before_send_and_rechecks_route(self):
        self.assertTrue(self.box.claim('telegram')['send']);self.conn.commit.assert_called_once()
        self.row['deal']['pipeline_id']=11
        self.assertFalse(self.box.claim('telegram')['send'])
        self.assertEqual(self.cur.execute.call_args.args[1][0],'skipped')

    def test_disconnected_or_device_owned_by_someone_else_is_not_delivered(self):
        self.box.connection_id.return_value='different'
        self.assertFalse(self.box.claim('telegram')['send'])
        self.box.connection_id.return_value='connection'
        self.cur.fetchone.return_value={**self.item,'channel':'push','subscription':{'endpoint':'x'},'owner_telegram_id':21}
        self.assertFalse(self.box.claim('push')['send'])

    def test_expired_device_disabled_only_in_this_company(self):
        self.box.finish({**self.item,'channel':'push','endpoint_hash':'hash'},'expired')
        self.assertEqual(self.cur.execute.call_args.args[1],('company-a',20,'hash'))
        with self.assertRaises(ValueError):self.box.finish(self.item,'pending')

    def test_unknown_result_is_never_replayed(self):
        self.box.finish(self.item,'unknown')
        self.assertIn("status='sending'",self.cur.execute.call_args.args[0])
        self.assertEqual(self.cur.execute.call_args.args[1][0],'unknown')


class NoticeWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_text_and_one_delivery_attempt(self):
        worker=load('tenant_crm_notice_worker',{'observe':Mock(),'claim':Mock(),'finish':Mock(),
            'send_push':Mock()}, {'tenant_crm_notice_store','tenant_crm_notice_outbox','tenant_push_worker'})
        row=task();row['deal'].update(contact_name='Client',phone='+994123')
        item={'resource':row,'event':'task_assigned','send':True,'recipient_id':20,'tenant_name':'Company'}
        self.assertIn('Call client',worker.message(item));self.assertIn('+994123',worker.message(item))
        worker.claim.side_effect=[item,None];bot=AsyncMock();bot.send_message.side_effect=TimeoutError()
        await worker.deliver(bot,'',{},Mock())
        bot.send_message.assert_awaited_once()
        self.assertEqual(worker.finish.call_args.args[1],'unknown')
