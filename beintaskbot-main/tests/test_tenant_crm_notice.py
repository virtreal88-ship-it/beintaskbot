"""CRM notices: no old broadcasts, shared Kommo seats, cross-tenant data or retries."""
import copy
import json
import unittest
from unittest.mock import Mock, MagicMock, AsyncMock
from datetime import datetime, timezone
from test_tenant_tasks import person
from test_tenant_hot_orders import load
from tenant_crm_notice_policy import transitions, eligible, task_route, timestamp
from tenant_crm_notice_store import connect_dialog_route, plan_connect_message
from tenant_push_payload import payload_for


def member(role='manager', identity=20):
    p = person(role, identity)
    p.update(tenant_status='active', permissions=['tasks','deals','customers','dialogs'], modules={'tasks':True,'deals':True,'customers':True})
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

    def test_connect_incoming_push_requires_dialog_permission_and_current_tenant(self):
        row={'tenant_id':'company-a','channel':'connect:00000000-0000-0000-0000-000000000201',
             'direction':'incoming','raw':{'channelId':'00000000-0000-0000-0000-000000000201',
             'conversationId':'994501234567@s.whatsapp.net'}}
        self.assertTrue(eligible(member('worker'),'incoming_message',row,'push'))
        denied=member('worker');denied['permissions'].remove('dialogs')
        self.assertFalse(eligible(denied,'incoming_message',row,'push'))
        self.assertFalse(eligible(member('worker'),'incoming_message',{**row,'tenant_id':'company-b'},'push'))
        self.assertFalse(eligible(member('worker'),'incoming_message',{**row,'direction':'outgoing'},'push'))

    def test_connect_route_comes_only_from_persisted_matching_message(self):
        channel='00000000-0000-0000-0000-000000000201'
        row={'kommo_lead_id':41,'channel':'connect:'+channel,'direction':'incoming',
             'raw':{'channelId':channel,'conversationId':'group@g.us'}}
        self.assertEqual(connect_dialog_route(row),{'provider':'connect','lead_id':41,
            'channel_id':channel,'conversation_id':'group@g.us'})
        self.assertIsNone(connect_dialog_route({**row,'direction':'outgoing'}))
        self.assertIsNone(connect_dialog_route({**row,'channel':'connect:00000000-0000-0000-0000-000000000202'}))


class ConnectPushOutboxTests(unittest.TestCase):
    def test_connect_message_uses_existing_push_outbox_once_without_telegram(self):
        from tenant_crm_notice_store import plan_connect_message as enqueue
        from unittest.mock import patch
        channel='00000000-0000-0000-0000-000000000201'
        row={'channelId':channel,'conversationId':'994501234567@s.whatsapp.net','text':'private message'}
        cur=MagicMock();cur.fetchone.return_value={'id':'event'}
        with patch('tenant_crm_notice_store.profiles',return_value=[member('worker')]), \
             patch('tenant_crm_notice_store.connection_id',return_value=''), \
             patch('tenant_crm_notice_store.eligible',return_value=True):
            enqueue(cur,'company-a',channel,row['conversationId'],41,'connect:message-1',row)
        inserts=[call for call in cur.execute.call_args_list if 'INSERT INTO saas_crm_notice_deliveries' in call.args[0]]
        self.assertEqual(len(inserts),1)
        self.assertIn("'push'",inserts[0].args[0])
        event_insert=cur.execute.call_args_list[0]
        payload=json.loads(event_insert.args[1][-1])
        self.assertEqual(payload['connect_dialog'],{'provider':'connect','lead_id':41,
            'channel_id':channel,'conversation_id':row['conversationId']})
        self.assertNotIn('private message',str(payload))

    def test_duplicate_connect_notice_event_does_not_make_a_second_delivery(self):
        from tenant_crm_notice_store import plan_connect_message as enqueue
        from unittest.mock import patch
        channel='00000000-0000-0000-0000-000000000201';row={'channelId':channel,'conversationId':'client@s.whatsapp.net'}
        cur=MagicMock();cur.fetchone.return_value=None
        with patch('tenant_crm_notice_store.profiles',return_value=[member('worker')]), \
             patch('tenant_crm_notice_store.connection_id',return_value=''), \
             patch('tenant_crm_notice_store.eligible',return_value=True):
            enqueue(cur,'company-a',channel,row['conversationId'],41,'connect:message-1',row)
        self.assertFalse(any('INSERT INTO saas_crm_notice_deliveries' in call.args[0] for call in cur.execute.call_args_list))

    def test_timestamp_is_finite_and_timezone_aware(self):
        for value in (None,True,float('nan'),float('inf'),'bad',datetime(2026,1,1)):
            self.assertEqual(timestamp(value),0)
        self.assertEqual(timestamp('105'),105)

    def test_push_never_contains_client_data(self):
        for event in ('task_assigned','task_overdue','new_lead','incoming_message'):
            self.assertEqual(payload_for(event)['event'],event)
            self.assertNotIn('client',str(payload_for(event)))

    def test_connect_push_target_is_bounded_and_visible_copy_stays_neutral(self):
        target='/app?view=dialogs&open_dialog=1&provider=connect&lead_id=41&channel_id=00000000-0000-0000-0000-000000000201&conversation_id=client%40s.whatsapp.net'
        payload=payload_for('incoming_message',target)
        self.assertEqual(payload['url'],target)
        self.assertEqual(payload['body'],'Yeni mesaj var. Kabinetdə yoxlayın.')
        self.assertEqual(payload_for('incoming_message','https://evil.test/collect')['url'],'/app?view=customers')


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.p=member();self.row=task()
        self.box=load('tenant_crm_notice_outbox',{'_connect':self.connect,'ensure_schema':Mock(),
            'profiles':Mock(return_value=[self.p]),'resource':Mock(return_value=self.row),'connection_id':Mock(return_value='connection'),
            'connect_dialog_route':connect_dialog_route},
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

    def test_connect_push_revalidates_exact_current_route_and_builds_app_deep_link(self):
        route={'provider':'connect','lead_id':41,'channel_id':'00000000-0000-0000-0000-000000000201','conversation_id':'client@g.us'}
        item={**self.item,'channel':'push','event':'incoming_message','kind':'message','source_key':'connect:msg-1',
              'payload':{'connection':'','connect_dialog':route},'subscription':{'endpoint':'private'},
              'device_active':True,'owner_telegram_id':20}
        row={'tenant_id':'company-a','kommo_lead_id':41,'external_id':'connect:msg-1',
             'channel':'connect:'+route['channel_id'],'direction':'incoming',
             'raw':{'channelId':route['channel_id'],'conversationId':route['conversation_id']}}
        self.box.profiles.return_value=[member('worker')];self.box.resource.return_value=row
        self.box.connection_id.return_value='';self.box.eligible.return_value=True
        self.cur.fetchone.side_effect=[copy.deepcopy(item),{'present':1}]
        result=self.box.claim('push')
        self.assertTrue(result['send'])
        self.assertEqual(result['target_url'],'/app?view=dialogs&open_dialog=1&provider=connect&lead_id=41&channel_id=00000000-0000-0000-0000-000000000201&conversation_id=client%40g.us')
        route_sql=next(call for call in self.cur.execute.call_args_list if 'JOIN saas_connect_channels' in call.args[0])
        self.assertIn('c.tenant_id=%s::uuid',route_sql.args[0])
        self.assertEqual(route_sql.args[1],('company-a',route['channel_id'],route['conversation_id'],41))

    def test_connect_push_is_skipped_when_stored_route_or_channel_is_ambiguous(self):
        route={'provider':'connect','lead_id':41,'channel_id':'00000000-0000-0000-0000-000000000201','conversation_id':'client@g.us'}
        bad_route={**route,'channel_id':'00000000-0000-0000-0000-000000000202'}
        item={**self.item,'channel':'push','event':'incoming_message','kind':'message','source_key':'connect:msg-1',
              'payload':{'connection':'','connect_dialog':bad_route},'subscription':{'endpoint':'private'},
              'device_active':True,'owner_telegram_id':20}
        row={'tenant_id':'company-a','kommo_lead_id':41,'channel':'connect:'+route['channel_id'],'direction':'incoming',
             'raw':{'channelId':route['channel_id'],'conversationId':route['conversation_id']}}
        self.box.profiles.return_value=[member('worker')];self.box.resource.return_value=row
        self.box.connection_id.return_value='';self.box.eligible.return_value=True
        self.cur.fetchone.return_value=copy.deepcopy(item)
        result=self.box.claim('push')
        self.assertFalse(result['send']);self.assertEqual(result['target_url'],'')
        self.assertFalse(any('JOIN saas_connect_channels' in call.args[0] for call in self.cur.execute.call_args_list))


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
