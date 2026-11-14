import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import tenant_kommo_deal_move as move
from tenant_task_flow import TaskFlowError

P={'tenant_id':'tenant-a','active':True}
PAYLOAD={'source_revision':'a'*64,'request_id':'00000000-0000-4000-8000-000000000001','lead_id':7,'expected_pipeline_id':10,'expected_status_id':100,'expected_updated_at':1000,'stage_id':101}
OPTIONS=[{'pipeline_id':10,'stage_id':100},{'pipeline_id':10,'stage_id':101}]
class MoveTests(unittest.TestCase):
 def test_normalize_exact_contract(self):
  self.assertEqual(move.normalize(PAYLOAD),PAYLOAD)
  for change in [{'pipeline_id':20},{'stage_id':True},{'lead_id':'0'},{'source_revision':'invalid'},{'source_revision':'z'*64}]:
   with self.assertRaises(ValueError):move.normalize({**PAYLOAD,**change})
 def test_no_foreign_funnel_or_connect_write(self):
  cur=Mock();cur.fetchone.return_value={'pipeline_id':20,'status_id':100,'raw':{}}
  with patch.object(move,'stage_choices',return_value=OPTIONS):
   with self.assertRaises(TaskFlowError):move.authorize_cached(cur,P,PAYLOAD)
   cur.fetchone.return_value={'pipeline_id':10,'status_id':100,'raw':{'source':'connect'}}
   with self.assertRaises(TaskFlowError):move.authorize_cached(cur,P,PAYLOAD)
   cur.fetchone.return_value={'pipeline_id':10,'status_id':100,'raw':{}}
   with self.assertRaises(TaskFlowError):move.authorize_cached(cur,P,{**PAYLOAD,'stage_id':999})
 def test_cas_and_unknown_read_verification(self):
  remote={'id':7,'pipeline_id':10,'status_id':100,'updated_at':1000}
  with patch.object(move,'authorize_cached'):
   self.assertEqual(move.prepare(Mock(),P,SimpleNamespace(tenant_id='tenant-a'),remote,PAYLOAD),{'status_id':101})
   for key,value in [('id',8),('pipeline_id',20),('status_id',102),('updated_at',1001)]:
    with self.assertRaises(TaskFlowError):move.prepare(Mock(),P,SimpleNamespace(tenant_id='tenant-a'),{**remote,key:value},PAYLOAD)
   final={**remote,'status_id':101,'updated_at':1002}
   move.prepare(Mock(),P,SimpleNamespace(tenant_id='tenant-a'),final,PAYLOAD,True)
   self.assertEqual(move.verified(final,PAYLOAD)['status_id'],101)
   with self.assertRaises(TaskFlowError):move.verified(remote,PAYLOAD)
 def test_assigned_funnel_only_and_completion_approval_preserved(self):
  policy=SimpleNamespace(kommo_pipeline_scope=lambda:[{'pipeline_id':10,'status_ids':[100,101,142]}],deal_board=lambda:[{'pipeline_id':10,'stages':[{'stage_id':100},{'stage_id':101},{'stage_id':142}]},{'pipeline_id':20,'stages':[{'stage_id':200}]}],requires_deal_approval=lambda:True)
  with patch.object(move,'TenantPolicy',return_value=policy),patch('tenant_member_access.page_capabilities',return_value={'deals':True}):
   self.assertEqual(move.stage_choices(P),OPTIONS)
   self.assertEqual(len(move.stage_choices(P,target=False)),3)
   self.assertEqual(move.stage_choices({**P,'active':False}),[])
 def test_cache_update_is_narrow_and_never_regresses(self):
  cur=Mock();policy=SimpleNamespace(deal_board=lambda:[{'pipeline_id':10,'stages':[{'stage_id':101,'name':'Next'}]}])
  with patch.object(move,'TenantPolicy',return_value=policy):move.cache_confirmed(cur,P,PAYLOAD,{'updated_at':1002})
  sql,args=cur.execute.call_args.args
  self.assertIn('source_updated_at<=%s',sql);self.assertIn('tenant_id=%s::uuid AND kommo_lead_id=%s AND pipeline_id=%s',sql);self.assertNotIn('contact_name=',sql)

if __name__=='__main__':unittest.main()

class ApiTests(unittest.TestCase):
 def request(self,body,origin='https://crm.test'):
  from unittest.mock import AsyncMock
  return SimpleNamespace(method='POST',query={},content_type='application/json',headers={'Origin':origin},json=AsyncMock(return_value=body))
 def test_origin_and_explicit_actor_binding(self):
  import asyncio
  import tenant_kommo_deal_move_api as api
  from unittest.mock import AsyncMock
  profile={'tenant_id':'tenant-a','telegram_id':7,'active':True}
  handler=api.handler(lambda _:profile,'https://crm.test',Mock(),Mock())
  base={**PAYLOAD,'expected_tenant_id':'tenant-a','expected_user_id':'7'}
  with patch.object(api,'move',new_callable=AsyncMock) as mutation:
   for request,status in [(self.request(dict(base),'https://evil.test'),403),(self.request({**base,'expected_tenant_id':'tenant-b'}),409),(self.request(dict(PAYLOAD)),409)]:
    response=asyncio.run(handler(request));self.assertEqual(response.status,status)
   mutation.assert_not_called()
 def test_exact_identity_command_and_unknown_keeps_request(self):
  import asyncio,json
  import tenant_kommo_deal_move_api as api
  from unittest.mock import AsyncMock
  profile={'tenant_id':'tenant-a','telegram_id':7,'active':True};handler=api.handler(lambda _:profile,'https://crm.test',Mock(),Mock())
  body={**PAYLOAD,'expected_tenant_id':'tenant-a','expected_user_id':'7'}
  with patch.object(api,'move',new_callable=AsyncMock,side_effect=TaskFlowError('board_outcome_unknown',409)) as mutation:
   response=asyncio.run(handler(self.request(body)));self.assertEqual(response.status,409);self.assertTrue(json.loads(response.body)['keep_request']);self.assertEqual(mutation.call_args.args[1],PAYLOAD)

class RejectedCommandTests(unittest.TestCase):
 def test_durable_rejection_allows_fresh_user_action(self):
  import asyncio,json
  import tenant_kommo_deal_move_api as api
  from unittest.mock import AsyncMock
  profile={'tenant_id':'tenant-a','telegram_id':7,'active':True}
  handler=api.handler(lambda _:profile,'https://crm.test',Mock(),Mock())
  error=TaskFlowError('kommo_deal_changed',409);error.keep_request=False
  body={**PAYLOAD,'expected_tenant_id':'tenant-a','expected_user_id':'7'}
  request=SimpleNamespace(method='POST',query={},content_type='application/json',headers={'Origin':'https://crm.test'},json=AsyncMock(return_value=body))
  with patch.object(api,'move',new_callable=AsyncMock,side_effect=error):
   response=asyncio.run(handler(request));self.assertFalse(json.loads(response.body)['keep_request'])
