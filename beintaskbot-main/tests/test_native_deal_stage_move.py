import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
import tenant_crm_native_deals as store
import tenant_crm_native_deal_api as api

ID='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
STAMP=datetime(2026,11,14,tzinfo=timezone.utc)

class StageMoveTests(unittest.TestCase):
    def setup_store(self, old=None):
        conn=mock.MagicMock();cur=conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        return conn,cur

    def test_requires_revision_and_actor(self):
        with mock.patch.object(store,'_connect') as connect:
            for actor,stamp in [(None,STAMP.isoformat()),(7,'bad'),(7,'2026-01-01')]:
                with self.assertRaises(ValueError):store.move_deal_stage('t',ID,'s',expected_updated_at=stamp,actor_id=actor)
            connect.assert_not_called()

    def test_stale_cas_and_row_permission_prevent_writes(self):
        for row in [None,dict(updated_at=STAMP,pipeline_id='p',stage_id='s')]:
            conn,cur=self.setup_store()
            with mock.patch.object(store,'_connect',return_value=conn),mock.patch.object(store,'_ensure_schema'),mock.patch.object(store,'lock_admission'),mock.patch.object(store,'current_access',return_value={}),mock.patch.object(store,'locked_resource',return_value=row) as locked,mock.patch('tenant_scoped_access.scoped_access_enabled',return_value=False):
                if row:
                    with self.assertRaises(store.CRMAccessError) as error:store.move_deal_stage('t',ID,'next',expected_updated_at='2026-11-13T00:00:00+00:00',actor_id=7)
                    self.assertEqual(error.exception.status,409)
                else:self.assertIsNone(store.move_deal_stage('t',ID,'next',expected_updated_at=STAMP.isoformat(),actor_id=7))
                locked.assert_called_once_with(cur,{},'deals','update','saas_crm_native_deals','t',ID)
                self.assertFalse(any('UPDATE ' in call.args[0] for call in cur.execute.call_args_list))

    def test_only_stage_written_same_pipeline_and_sync_preserved(self):
        conn,cur=self.setup_store();previous=dict(updated_at=STAMP,pipeline_id='p',stage_id='old')
        row=dict(id=ID,updated_at=STAMP,stage_id='next')
        cur.fetchone.side_effect=[{'ok':1},row]
        with mock.patch.object(store,'_connect',return_value=conn),mock.patch.object(store,'_ensure_schema'),mock.patch.object(store,'lock_admission'),mock.patch.object(store,'current_access',return_value={}),mock.patch.object(store,'locked_resource',return_value=previous),mock.patch('tenant_scoped_access.scoped_access_enabled',return_value=False),mock.patch.object(store,'queue_edit',return_value={'mode':'scoped_scalar'}) as queue,mock.patch('tenant_crm_native_tasks.maybe_create_stage_task') as task:
            result=store.move_deal_stage('t',ID,'next',expected_updated_at=STAMP.isoformat(),actor_id=7)
        self.assertEqual(result['sync']['mode'],'scoped_scalar');task.assert_called_once();queue.assert_called_once()
        self.assertEqual(cur.execute.call_args_list[0].args[1],('t','p','next'))
        sql,args=cur.execute.call_args_list[1].args
        self.assertIn('SET stage_id=%s,updated_at=',sql);self.assertEqual(args,('next','t',ID));self.assertNotIn('title=',sql)

    def test_api_rejects_extra_fields_and_requires_context(self):
        profile={'tenant_id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','telegram_id':7,'role':'owner','active':True}
        h=api.handler(lambda _:profile,'https://crm.test',mock.MagicMock())
        for data,query,expected in [({'action':'move_stage','stage_id':'s','expected_updated_at':STAMP.isoformat()}, {},409),({'action':'move_stage','stage_id':'s','expected_updated_at':STAMP.isoformat(),'title':'overwrite'}, {},400)]:
            req=SimpleNamespace(method='POST',match_info={'id':ID},query=query,content_type='application/json',headers={'Origin':'https://crm.test'},json=mock.AsyncMock(return_value=data))
            with mock.patch.object(api,'move_deal_stage') as move:
                result=asyncio.run(h(req));self.assertEqual(result.status,expected);move.assert_not_called()

if __name__=='__main__':unittest.main()
