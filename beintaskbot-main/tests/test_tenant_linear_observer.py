"""Observer, retention and outbox contracts without external writes or SQL server."""
import unittest
from unittest.mock import Mock, MagicMock, AsyncMock
from types import SimpleNamespace
from datetime import datetime, timezone
import test_tenant_hot_orders as loaders
from tenant_linear_observer_policy import source_time, event_kind, event_id, news_candidate
from tenant_linear_policy import settings, TenantLinearError

TENANT='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
OTHER='bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'
DONE='cccccccc-cccc-4ccc-cccc-cccccccccccc'
BEFORE='dddddddd-dddd-4ddd-dddd-dddddddddddd'
STAMP='2026-10-08T10:00:00Z'


def config():
    return settings({'team_id':TENANT,'done_state_ids':[DONE],
        'workflow':{'enabled':True,'sync_enabled':True,'notification_state_ids':[DONE]},
        'members':{'20':{'account':'worker'}},'news':{'enabled':True,'projects':[OTHER]}})


def row(**changes):
    return {'id':OTHER,'identifier':'BS-1','title':'New feature','description':'Account: worker\n\nDetails',
        'updatedAt':STAMP,'team':{'id':TENANT},'state':{'id':DONE},'project':{'id':OTHER,'name':'Project'},
        'parent':None,'labels':{'nodes':[]},**changes}


class Decisions(unittest.TestCase):
    def test_opt_in_defaults_and_roundtrip(self):
        c=settings({});self.assertFalse(c['workflow']['sync_enabled']);self.assertFalse(c['news']['enabled'])
        self.assertEqual(settings(config()),config())

    def test_strict_settings(self):
        for change in ({'sync_enabled':'yes'},{'notification_state_ids':'all'},{'notification_state_ids':['foreign']}):
            with self.assertRaises(TenantLinearError):settings({'workflow':change})
        with self.assertRaises(TenantLinearError):settings({'news':{'enabled':'yes'}})

    def test_first_scan_and_first_seen_issue_do_not_notify(self):
        previous={'state_id':BEFORE,'source_version':source_time('2026-10-08T09:00:00Z')}
        self.assertIsNone(event_kind(config(),previous,row(),baseline=True))
        self.assertIsNone(event_kind(config(),None,row(),baseline=False))
        self.assertEqual(event_kind(config(),previous,row(),baseline=False),'linear_done')

    def test_only_new_selected_transition(self):
        previous={'state_id':DONE,'source_version':source_time(STAMP)}
        self.assertIsNone(event_kind(config(),previous,row(),baseline=False))
        previous['state_id']=BEFORE
        self.assertIsNone(event_kind(config(),previous,row(),baseline=False))
        previous['source_version']=source_time('2026-10-08T09:00:00Z')
        self.assertIsNone(event_kind(config(),previous,row(state={'id':OTHER}),baseline=False))
        c=config();c['workflow']['notification_state_ids']=[OTHER]
        self.assertEqual(event_kind(c,previous,row(state={'id':OTHER}),baseline=False),'linear_status_changed')

    def test_event_identity_is_stable_but_company_scoped(self):
        self.assertEqual(event_id(TENANT,row()),event_id(TENANT,row(updatedAt='2026-10-08T14:00:00+04:00')))
        self.assertNotEqual(event_id(TENANT,row()),event_id(OTHER,row()))
        with self.assertRaises(ValueError):source_time('2026-10-08T10:00:00')

    def test_news_main_only_and_explicit_exclusions(self):
        self.assertTrue(news_candidate(config(),row()))
        self.assertFalse(news_candidate(config(),row(parent={'id':BEFORE})))
        incomplete=row();incomplete.pop('parent');self.assertFalse(news_candidate(config(),incomplete))
        for label in ('Bug','Optimization','REFactoring'):
            self.assertFalse(news_candidate(config(),row(labels={'nodes':[{'name':label}]})))
        self.assertTrue(news_candidate(config(),row(labels={'nodes':[{'name':'Uncertain'}]})))
        self.assertFalse(news_candidate(config(),row(project={'id':BEFORE})))
        c=config();c['news']['enabled']=False;self.assertFalse(news_candidate(c,row()))


class Storage(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.context=Mock(return_value=({},config(),'private-key','v1'))
        self.query=Mock(return_value={'issues':{'nodes':[],'pageInfo':{'hasNextPage':False,'endCursor':None}}})
        self.service=loaders.load('tenant_linear_observer',{'_connect':self.connect,'ensure_observer_schema':Mock(),
            'context':self.context,'query':self.query,'FIELDS':'id'},
            {'tenant_platform','tenant_linear_context','tenant_linear_provider','tenant_linear_tasks_provider','tenant_linear_observer_schema'})

    def test_news_seen_key_prevents_reimport_and_no_publication(self):
        self.cur.fetchone.side_effect=[None,{'issue_id':OTHER}]
        self.service.store_issue(self.cur,TENANT,config(),row(),baseline=True)
        sql=str(self.cur.execute.call_args_list)
        self.assertIn('saas_linear_news_seen',sql);self.assertIn('INSERT INTO saas_linear_news(',sql)
        self.assertNotIn('saas_linear_notice_events',sql);self.assertNotIn('published',sql)
        self.cur.reset_mock();self.cur.fetchone.side_effect=[None,None]
        self.service.store_issue(self.cur,TENANT,config(),row(),baseline=True)
        self.assertNotIn('INSERT INTO saas_linear_news(',str(self.cur.execute.call_args_list))

    def test_foreign_team_and_older_data_never_stored(self):
        self.service.store_issue(self.cur,TENANT,config(),row(team={'id':OTHER}),baseline=False)
        self.cur.execute.assert_not_called()
        self.cur.fetchone.return_value={'state_id':DONE,'source_version':source_time('2026-10-09T10:00:00Z')}
        self.service.store_issue(self.cur,TENANT,config(),row(),baseline=False)
        self.assertNotIn('INSERT',str(self.cur.execute.call_args_list))

    def test_fixed_page_and_durable_window(self):
        end=datetime(2026,10,8,12,tzinfo=timezone.utc);start=datetime(2026,10,8,11,tzinfo=timezone.utc)
        state={'tenant_id':TENANT,'config_version':'v1','cursor':'next','since_at':start,'until_at':end}
        self.cur.fetchone.side_effect=[state,state,{'owner_telegram_id':20}]
        self.query.return_value['issues']['pageInfo']={'hasNextPage':True,'endCursor':'next-2'}
        result=self.service.sync_one();self.assertTrue(result['has_next'])
        key,query,variables=self.query.call_args.args
        self.assertEqual(key,'private-key');self.assertIn('first:30',query);self.assertIn('parent { id }',query)
        self.assertEqual(variables['filter']['team']['id']['eq'],TENANT);self.assertEqual(variables['after'],'next')
        update=self.cur.execute.call_args_list[-1].args[1]
        self.assertEqual(update,('v1','next-2',start,end,TENANT));self.assertEqual(self.conn.commit.call_count,2)

    def test_config_change_resets_scan_without_history_alarms(self):
        state={'tenant_id':TENANT,'config_version':'old','cursor':'old-cursor','since_at':source_time(STAMP),'until_at':None}
        self.cur.fetchone.side_effect=[state,state,{'owner_telegram_id':20}]
        self.service.sync_one();variables=self.query.call_args.args[2]
        self.assertIsNone(variables['after']);self.assertNotIn('gte',variables['filter']['updatedAt'])

    def test_provider_failure_keeps_committed_backoff(self):
        state={'tenant_id':TENANT,'config_version':'v1','cursor':None,'since_at':None,'until_at':None}
        self.cur.fetchone.side_effect=[state,state,{'owner_telegram_id':20}]
        self.query.side_effect=RuntimeError('private error')
        with self.assertRaises(RuntimeError):self.service.sync_one()
        self.conn.commit.assert_called_once();self.conn.rollback.assert_called_once()
        self.assertIn("interval '5 minutes'",str(self.cur.execute.call_args_list))

    def test_cleanup_bounded_even_when_disconnected_and_keeps_dedup(self):
        service=loaders.load('tenant_linear_observer_cleanup',{'_connect':self.connect,'ensure_observer_schema':Mock()},
            {'tenant_platform','tenant_linear_observer_schema'})
        service.cleanup();calls=self.cur.execute.call_args_list
        self.assertEqual(len(calls),3)
        for call in calls:self.assertIn('LIMIT 100',call.args[0]);self.assertIn('90 days',call.args[0])
        self.assertNotIn('saas_linear_news_seen',str(calls));self.assertNotIn('connected',str(calls))


class Worker(unittest.IsolatedAsyncioTestCase):
    async def test_sync_failure_does_not_block_queue_and_no_private_payload(self):
        sync=Mock(side_effect=RuntimeError('private provider details'));expand=Mock();cleanup=Mock()
        claim=Mock(side_effect=[{'send':True,'recipient_id':20,'channel':'telegram'},None]);finish=Mock()
        service=loaders.load('tenant_linear_observer_worker',{'sync_one':sync,'expand_events':expand,'cleanup':cleanup,
            'claim_delivery':claim,'finish_delivery':finish,'send_push':Mock()},
            {'tenant_linear_observer','tenant_linear_observer_cleanup','tenant_linear_notice_outbox','tenant_push_worker'})
        bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7)));logger=Mock()
        await service.observe_and_notify(bot,'',{},logger)
        expand.assert_called_once();cleanup.assert_called_once();finish.assert_called_once()
        self.assertEqual(finish.call_args.args[1:],('delivered',7))
        self.assertIn('/app?view=linear',bot.send_message.call_args.kwargs['text'])
        self.assertNotIn('private',str(logger.mock_calls));self.assertEqual(claim.call_count,2)

    async def test_unknown_send_is_not_retried_in_tick(self):
        item={'send':True,'recipient_id':20,'channel':'telegram'};claim=Mock(side_effect=[item,None]);finish=Mock()
        service=loaders.load('tenant_linear_observer_worker',{'sync_one':Mock(),'expand_events':Mock(),'cleanup':Mock(),
            'claim_delivery':claim,'finish_delivery':finish,'send_push':Mock()},
            {'tenant_linear_observer','tenant_linear_observer_cleanup','tenant_linear_notice_outbox','tenant_push_worker'})
        bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError('private details')))
        await service.observe_and_notify(bot,'',{},Mock())
        bot.send_message.assert_awaited_once();self.assertEqual(finish.call_args.args[1],'unknown')


if __name__=='__main__':unittest.main()
