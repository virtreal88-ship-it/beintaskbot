"""Channel binding/schedule/delivery isolation, no real Telegram messages."""
import json
import sys
import unittest
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import Mock,MagicMock,AsyncMock,patch
import test_tenant_hot_orders as loaders
from test_tenant_news import TENANT,OTHER,SESSION,row
from tenant_linear_policy import TenantLinearError
from tenant_news_telegram_policy import settings,address,due,verify,message

CHAT=-100123456789;BOT=99


def channel(**changes):
    return {'tenant_id':TENANT,'chat_id':CHAT,'bot_id':BOT,'verified_by':20,'title':'Channel','binding_version':OTHER,
        'config':settings({'enabled':True,'timezone':'UTC','start':'00:00','end':'23:59'}),'next_at':None,
        'updated_at':'v1','owner_telegram_id':20,'owner_active':True,'owner_role':'owner',**changes}


def verified():return {k:channel()[k] for k in ('chat_id','bot_id','verified_by','title')}


def item(**changes):
    return {'tenant_id':TENANT,'news_id':OTHER,'requested_by':20,'chat_id':CHAT,'binding_version':OTHER,
        'status':'pending','created_at':datetime.now(timezone.utc),'payload':{'project_name':'AKUL','identifier':'BS-10',
        'title':'Feature','summary':'Details','url':''},**changes}


class Policy(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_channel_owner_and_bot_checked_without_posting(self):
        bot=SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=CHAT,type='channel',title='Name')),
            get_me=AsyncMock(return_value=SimpleNamespace(id=BOT)),get_chat_member=AsyncMock(return_value=SimpleNamespace(status='administrator',can_post_messages=True)),send_message=AsyncMock())
        result=await verify(bot,'@channel',20);self.assertEqual(result['chat_id'],CHAT)
        self.assertEqual(bot.get_chat_member.await_count,2);bot.send_message.assert_not_called()
        bot.get_chat_member.return_value=SimpleNamespace(status='member')
        with self.assertRaises(TenantLinearError):await verify(bot,'@channel',20)

    async def test_bot_must_be_allowed_to_post_and_group_not_channel(self):
        bot=SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=CHAT,type='channel',title='Name')),
            get_me=AsyncMock(return_value=SimpleNamespace(id=BOT)),get_chat_member=AsyncMock(side_effect=[SimpleNamespace(status='creator'),SimpleNamespace(status='administrator',can_post_messages=False)]))
        with self.assertRaises(TenantLinearError):await verify(bot,CHAT,20)
        bot.get_chat.return_value.type='supergroup'
        with self.assertRaises(TenantLinearError):await verify(bot,CHAT,20)

    async def test_hours_include_last_minute_and_utc_interval(self):
        cfg=settings({'enabled':True});cfg['next_at']=None
        # Baku 09:00 and 19:00; last minute included, 19:01 excluded.
        for hour,minute,expected in ((4,59,False),(5,0,True),(15,0,True),(15,1,False)):
            self.assertEqual(due(cfg,datetime(2026,10,8,hour,minute,tzinfo=timezone.utc)),expected)
        cfg['next_at']=datetime(2026,10,8,6,tzinfo=timezone.utc)
        self.assertFalse(due(cfg,datetime(2026,10,8,5,59,tzinfo=timezone.utc)))

    async def test_invalid_config_and_plain_safe_message(self):
        for changes in ({'interval_minutes':59},{'interval_minutes':True},{'timezone':'bad/zone'},{'start':'20:00','end':'09:00'}):
            with self.assertRaises(TenantLinearError):settings({'enabled':True,**changes})
        with self.assertRaises(TenantLinearError):address('https://t.me/channel')
        text=message({'project_name':'AKUL','title':'<script>','summary':'A & B','url':'+994501234567'})
        self.assertIn('<b>&lt;script&gt;</b>',text);self.assertIn('A &amp; B',text);self.assertIn('+994501234567',text)
        self.assertNotIn('akul.az',text);self.assertNotIn('MANUAL',text)


class Storage(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value;self.access=Mock(return_value=(TENANT,20))
        self.queue=loaders.load('tenant_news_telegram_queue',{'_connect':self.connect,'ensure_schema':Mock()},
            {'tenant_platform','tenant_news_telegram_schema'})
        self.setting=loaders.load('tenant_news_telegram_settings',{'_connect':self.connect,'ensure_schema':Mock(),
            'authorize':self.access,'access':self.access}, {'tenant_platform','tenant_news_ai_settings','tenant_news','tenant_news_telegram_schema'})

    def test_exclusive_channel_and_current_settings_version(self):
        data={**channel()['config'],'expected_updated_at':'v1'}
        self.cur.fetchone.side_effect=[channel(),{'tenant_id':OTHER}]
        with self.assertRaises(TenantLinearError) as caught:self.setting.save(SESSION,data,verified())
        self.assertEqual(caught.exception.status,409);self.conn.commit.assert_not_called()
        self.cur.fetchone.side_effect=[channel(updated_at='new')]
        with self.assertRaises(TenantLinearError):self.setting.save(SESSION,data,verified())

    def test_channel_change_blocks_old_pending_without_retargeting(self):
        data={**channel()['config'],'expected_updated_at':'v1'};target={**verified(),'chat_id':CHAT-1}
        self.cur.fetchone.side_effect=[channel(),None,channel(chat_id=CHAT-1)]
        self.setting.save(SESSION,data,target)
        last=self.cur.execute.call_args;self.assertIn("status='blocked'",last.args[0]);self.assertNotIn('SET chat_id',last.args[0])
        self.conn.commit.assert_called_once()

    def test_schedule_update_preserves_binding_and_minimum_interval(self):
        next_at=datetime.now(timezone.utc)+timedelta(minutes=60)
        self.cur.fetchone.side_effect=[channel(next_at=next_at),None,channel()]
        self.setting.save(SESSION,{**channel()['config'],'interval_minutes':120,'expected_updated_at':'v1'},verified())
        insert=self.cur.execute.call_args.args[1];self.assertEqual(insert[5],OTHER);self.assertEqual(insert[-1],next_at+timedelta(minutes=60))

    def test_only_explicit_approved_payload_is_queued_and_no_raw_source(self):
        self.cur.fetchone.side_effect=[channel(),None]
        self.assertEqual(self.queue.enqueue(self.cur,TENANT,20,row(status='published'),{'confirm_telegram':True,'channel_version':OTHER}),'pending')
        args=self.cur.execute.call_args.args[1];self.assertEqual(args[:4],(TENANT,OTHER,20,CHAT))
        self.assertNotIn('PRIVATE',args[-1]);self.assertEqual(json.loads(args[-1])['url'],'')
        with self.assertRaises(TenantLinearError):self.queue.enqueue(self.cur,TENANT,20,row(),{'confirm_telegram':True})

    def test_sent_unknown_cancelled_receipts_never_automatically_requeued(self):
        for status in ('sent','unknown','pending','cancelled'):
            self.cur.reset_mock();self.cur.fetchone.side_effect=[channel(),{'status':status}]
            self.assertEqual(self.queue.enqueue(self.cur,TENANT,20,row(status='published'),{'confirm_telegram':True,'channel_version':OTHER,'action':'publish'}),status)
            self.assertNotIn('INSERT INTO',str(self.cur.execute.call_args_list))

    def test_reserved_slot_and_sending_committed_before_network(self):
        self.cur.fetchone.side_effect=[channel(),item()]
        with patch.dict(sys.modules,{'tenant_news':SimpleNamespace(access=self.access)}):claimed=self.queue.claim(item(),verified())
        self.assertTrue(claimed);self.conn.commit.assert_called_once()
        sql=str(self.cur.execute.call_args_list);self.assertIn("status='sending'",sql);self.assertIn('next_at=%s',sql)
        self.assertGreater(self.cur.execute.call_args.args[1][0],datetime.now(timezone.utc)+timedelta(minutes=59))

    def test_changed_binding_and_revoked_member_never_claimed(self):
        self.cur.fetchone.side_effect=[channel(binding_version=TENANT),item()]
        with patch.dict(sys.modules,{'tenant_news':SimpleNamespace(access=self.access)}):self.assertIsNone(self.queue.claim(item(),verified()))
        self.assertIn("status='blocked'",self.cur.execute.call_args.args[0])
        self.access.side_effect=TenantLinearError('Denied',403)
        with patch.dict(sys.modules,{'tenant_news':SimpleNamespace(access=self.access)}):self.assertIsNone(self.queue.claim(item(),verified()))

    def test_retention_keeps_receipt_and_unknown_never_selected(self):
        self.cur.fetchone.return_value=None;self.assertIsNone(self.queue.candidate())
        sql=str(self.cur.execute.call_args_list);self.assertIn("SET status='unknown'",sql);self.assertIn("q.status='pending'",sql)
        self.assertIn('LIMIT 100',sql);self.assertIn("payload='{}'",sql);self.assertNotIn('DELETE FROM',sql)


class Worker(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_never_automatically_retries(self):
        candidate=Mock(return_value=item());claim=Mock(return_value=item());finish=Mock();verify=AsyncMock(return_value=verified())
        service=loaders.load('tenant_news_telegram_worker',{'candidate':candidate,'claim':claim,'finish':finish,'verify':verify,'message':message},
            {'tenant_news_telegram_queue','tenant_news_telegram_policy'})
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError('PRIVATE')));logger=Mock()
        await service.deliver(bot,logger);bot.send_message.assert_awaited_once();self.assertEqual(finish.call_args.args[1],'unknown')
        self.assertNotIn('PRIVATE',str(logger.mock_calls));candidate.assert_called_once()

    async def test_preflight_failure_does_not_send(self):
        claim=Mock(return_value=None);verify=AsyncMock(side_effect=RuntimeError())
        service=loaders.load('tenant_news_telegram_worker',{'candidate':Mock(return_value=item()),'claim':claim,'finish':Mock(),'verify':verify,'message':message},
            {'tenant_news_telegram_queue','tenant_news_telegram_policy'})
        bot=SimpleNamespace(send_message=AsyncMock());await service.deliver(bot,Mock());bot.send_message.assert_not_called()
        self.assertIsNone(claim.call_args.args[1])


if __name__=='__main__':unittest.main()
