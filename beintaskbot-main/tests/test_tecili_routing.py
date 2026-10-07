"""Urgent alarms follow transferred deals, with no live notifications."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

TREE=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'))


def load(names,ns):
    nodes=[node for node in TREE.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'<urgent>','exec'),ns)
    return ns


def routing():
    return {'_rufat_marker_name':lambda text:text.split(']')[0].lstrip('[') if text.startswith('[') else '',
            'get_chat_id_by_name':lambda name:{'admin':1,'rufat':2,'worker':3}.get(name),
            'is_funnel_chat':lambda user:user in {1,2},'can_receive_staff_notification':lambda user:True,
            '_normalize_kommo_entity_type':lambda value:value,
            'get_lead_details':Mock(return_value={'pipeline_id':20}),
            'owner_name_for_pipeline':lambda pipeline:{10:'admin',20:'rufat'}.get(pipeline,''),
            'active_task_assignee_chat_id':Mock(return_value=1)}


class RecipientTests(unittest.TestCase):
    def setUp(self):
        self.ns=load({'current_tecili_recipient'},routing())
        self.resolve=self.ns['current_tecili_recipient']
        self.task={'text':'[admin] Call client','entity_type':'leads','entity_id':7,'responsible_user_id':99}

    def test_old_manager_marker_and_shared_admin_do_not_override_new_owner(self):
        self.assertEqual(self.resolve(self.task),2)
        self.assertEqual(self.resolve({**self.task,'text':'Call client'}),2)
        self.ns['active_task_assignee_chat_id'].assert_not_called()

    def test_each_alarm_reads_current_pipeline(self):
        self.ns['get_lead_details'].side_effect=[{'pipeline_id':10},{'pipeline_id':20}]
        self.assertEqual(self.resolve(self.task),1)
        self.assertEqual(self.resolve(self.task),2)

    def test_explicit_worker_without_pipeline_keeps_own_task(self):
        self.assertEqual(self.resolve({**self.task,'text':'[worker] Repair'}),3)
        self.ns['get_lead_details'].assert_not_called()

    def test_failed_read_unknown_pipeline_or_inactive_owner_does_not_notify_previous_owner(self):
        for lead in (None,{'pipeline_id':999}):
            self.ns['get_lead_details'].return_value=lead
            self.assertEqual(self.resolve(self.task),0)
        self.ns['get_lead_details'].return_value={'pipeline_id':20}
        self.ns['can_receive_staff_notification']=lambda user:user!=2
        self.assertEqual(self.resolve(self.task),0)

    def test_contact_task_preserves_existing_assignment_rules(self):
        self.assertEqual(self.resolve({**self.task,'entity_type':'contacts'}),1)
        self.ns['get_lead_details'].assert_not_called()


class AlarmTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.task={'id':8,'text':'[admin] Call client','entity_type':'leads','entity_id':7,'responsible_user_id':99}
        self.response=Mock(status_code=200);self.response.json.return_value=self.task
        self.ns=load({'current_tecili_recipient','tecili_alarm_check'},{**routing(),'asyncio':asyncio,
            '_tecili_tasks':{8:{'text':'Old task','responsible_user_id':99}},
            'datetime':SimpleNamespace(now=lambda **kw:SimpleNamespace(hour=10)),'BAKU_TZ':None,
            '_http':SimpleNamespace(get=Mock(return_value=self.response)),'KOMMO_BASE_URL':'https://crm.invalid',
            'HEADERS':{},'ContextTypes':SimpleNamespace(DEFAULT_TYPE=object),'unregister_tecili_task':Mock(),
            'get_contact_name_from_entity':lambda *args:'Client','get_phone_from_entity':lambda *args:'phone',
            'send_push_notification':Mock(),'logger':Mock()})
        self.context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    async def test_both_telegram_and_push_go_only_to_current_owner(self):
        await self.ns['tecili_alarm_check'](self.context)
        self.assertEqual(self.context.bot.send_message.call_args.args[0],2)
        self.assertEqual(self.ns['send_push_notification'].call_args.args[0],'2')

    async def test_failed_task_read_never_sends_from_stale_registry(self):
        self.response.status_code=503
        await self.ns['tecili_alarm_check'](self.context)
        self.context.bot.send_message.assert_not_called()
        self.ns['send_push_notification'].assert_not_called()

    async def test_completed_task_is_removed_without_alarm(self):
        self.response.json.return_value={**self.task,'is_completed':True}
        await self.ns['tecili_alarm_check'](self.context)
        self.ns['unregister_tecili_task'].assert_called_once_with(8)
        self.context.bot.send_message.assert_not_called()


if __name__=='__main__':unittest.main()
