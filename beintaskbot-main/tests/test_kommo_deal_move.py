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
  cur=Mock();cur.rowcount=1;policy=SimpleNamespace(deal_board=lambda:[{'pipeline_id':10,'stages':[{'stage_id':101,'name':'Next'}]}])
  with patch.object(move,'TenantPolicy',return_value=policy):move.cache_confirmed(cur,P,PAYLOAD,{'updated_at':1002})
  sql,args=cur.execute.call_args.args
  self.assertIn('source_updated_at<=%s',sql);self.assertIn('tenant_id=%s::uuid AND kommo_lead_id=%s',sql);self.assertIn('(pipeline_id=%s AND status_id=%s) OR (pipeline_id=%s AND status_id=%s)',sql);self.assertNotIn('contact_name=',sql)

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

class PipelineMoveTests(unittest.TestCase):
 def setUp(self):
  self.payload={**PAYLOAD,'target_pipeline_id':20,'stage_id':201}
  self.cur=Mock();self.cur.fetchone.return_value={'pipeline_id':10,'status_id':100,'raw':{}};self.cur.rowcount=1
  self.options=[*OPTIONS,{'pipeline_id':20,'stage_id':201},{'pipeline_id':20,'stage_id':142}]
 def test_optional_target_preserves_legacy_fingerprint(self):
  self.assertEqual(move.normalize(PAYLOAD),PAYLOAD)
  self.assertEqual(move.normalize(self.payload),self.payload)
  for value in (True,0,-1,'bad'):
   with self.assertRaises(ValueError):move.normalize({**self.payload,'target_pipeline_id':value})
 def test_both_grants_and_exact_mapping_required(self):
  with patch.object(move,'stage_choices',return_value=self.options):
   move.authorize_cached(self.cur,P,self.payload)
   for changes in ({'target_pipeline_id':30},{'stage_id':101},{'stage_id':142},{'expected_pipeline_id':30}):
    with self.assertRaises(TaskFlowError):move.authorize_cached(self.cur,P,{**self.payload,**changes})
  for removed in ({'pipeline_id':10,'stage_id':100},{'pipeline_id':20,'stage_id':201}):
   with patch.object(move,'stage_choices',return_value=[t for t in self.options if t!=removed]):
    with self.assertRaises(TaskFlowError):move.authorize_cached(self.cur,P,self.payload,unknown=True)
 def test_unknown_synced_target_only_and_fresh_cache_conflict(self):
  with patch.object(move,'stage_choices',return_value=self.options):
   self.cur.fetchone.return_value={'pipeline_id':20,'status_id':201,'raw':{}}
   with self.assertRaises(TaskFlowError):move.authorize_cached(self.cur,P,self.payload)
   move.authorize_cached(self.cur,P,self.payload,unknown=True)
   for pipe,stage in [(20,100),(10,201),(30,201)]:
    self.cur.fetchone.return_value={'pipeline_id':pipe,'status_id':stage,'raw':{}}
    with self.assertRaises(TaskFlowError):move.authorize_cached(self.cur,P,self.payload,unknown=True)
 def test_remote_cas_target_receipt_and_binding(self):
  binding=SimpleNamespace(tenant_id='tenant-a');remote={'id':7,'pipeline_id':10,'status_id':100,'updated_at':1000}
  with patch.object(move,'authorize_cached'):
   self.assertEqual(move.prepare(self.cur,P,binding,remote,self.payload),{'pipeline_id':20,'status_id':201})
   for change in ({'pipeline_id':20},{'status_id':101},{'updated_at':1001}):
    with self.assertRaises(TaskFlowError):move.prepare(self.cur,P,binding,{**remote,**change},self.payload)
   final={**remote,'pipeline_id':20,'status_id':201,'updated_at':1001}
   move.prepare(self.cur,P,binding,final,self.payload,unknown=True)
   self.assertEqual(move.verified(final,self.payload)['pipeline_id'],20)
   with self.assertRaises(TaskFlowError):move.verified(remote,self.payload)
   with self.assertRaises(TaskFlowError):move.prepare(self.cur,P,SimpleNamespace(tenant_id='other'),remote,self.payload)
 def test_cache_requires_write_and_updates_pipeline(self):
  policy=SimpleNamespace(deal_board=lambda:[{'pipeline_id':20,'name':'Other','stages':[{'stage_id':201,'name':'Next'}]}])
  with patch.object(move,'TenantPolicy',return_value=policy):
   move.cache_confirmed(self.cur,P,self.payload,{'updated_at':1002})
   sql,args=self.cur.execute.call_args.args;self.assertEqual(args[:3],(20,201,'Next'));self.assertNotIn('pipeline_name=',sql)
   self.cur.rowcount=0
   with self.assertRaises(TaskFlowError):move.cache_confirmed(self.cur,P,self.payload,{'updated_at':1002})

class PipelineExecutionTests(unittest.TestCase):
 def test_lost_patch_response_then_synced_destination_is_get_only(self):
  from unittest.mock import MagicMock
  from dataclasses import dataclass
  @dataclass
  class ProviderBinding:
   tenant_id:str
   provider:str
   account_id:str
   connection_generation:str
  import tenant_kommo_board_commands as commands
  binding=ProviderBinding('tenant-a','kommo','example.kommo.com','generation-1')
  payload={**PAYLOAD,'target_pipeline_id':20,'stage_id':201}
  store=MagicMock();store.state={};store.save.side_effect=lambda state:setattr(store,'state',state)
  cur=store.conn.cursor.return_value.__enter__.return_value;cur.rowcount=1
  cached={'pipeline_id':10,'status_id':100,'raw':{}}
  def sql(statement,*args):
   cur.fetchone.return_value=cached if 'SELECT pipeline_id,status_id,raw' in statement else None
  cur.execute.side_effect=sql
  policy=SimpleNamespace(deal_board=lambda:[{'pipeline_id':20,'name':'Other','stages':[{'stage_id':201,'name':'Next'}]}])
  remote={'id':7,'pipeline_id':10,'status_id':100,'updated_at':1000};calls=[]
  def request(binding,method,path,**kwargs):
   calls.append(method)
   if method=='PATCH':
    self.assertEqual(kwargs['json_body'],{'pipeline_id':20,'status_id':201})
    remote.update(pipeline_id=20,status_id=201,updated_at=1001)
    raise TimeoutError('provider accepted but response lost')
   return dict(remote)
  with patch.object(commands,'TaskCommandStore',return_value=store),patch.object(commands,'current_binding',return_value=binding),patch.object(commands,'_profiles',return_value=(P,None)),patch.object(commands,'_require_schema'),patch('tenant_kommo_board_provenance.require',return_value='a'*64),patch('tenant_kommo_board_provenance.refresh_after_command',return_value='b'*64),patch.object(move,'stage_choices',return_value=[*OPTIONS,{'pipeline_id':20,'stage_id':201}]),patch.object(move,'TenantPolicy',return_value=policy):
   kwargs=dict(operation='deal_stage_move',path='leads/7',prepare=move.prepare,verified=move.verified,authorize_cached=move.authorize_cached,cache_confirmed=move.cache_confirmed)
   with self.assertRaises(TaskFlowError) as error:commands.execute_sync({**P,'telegram_id':7},payload,request,**kwargs)
   self.assertTrue(error.exception.keep_request);self.assertEqual(calls,['GET','PATCH'])
   cached.update(pipeline_id=20,status_id=201)
   result=commands.execute_sync({**P,'telegram_id':7},payload,request,verify_only=True,**kwargs)
   self.assertEqual(result['pipeline_id'],20);self.assertEqual(result['status_id'],201);self.assertEqual(calls,['GET','PATCH','GET'])
   # Completed command still verifies only; a revoked source prevents any provider request.
   with patch.object(move,'stage_choices',return_value=[{'pipeline_id':20,'stage_id':201}]):
    with self.assertRaises(TaskFlowError):commands.execute_sync({**P,'telegram_id':7},payload,request,verify_only=True,**kwargs)
   self.assertEqual(calls,['GET','PATCH','GET'])

class PipelineBoundTransportTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_prepare_body_reaches_exact_single_attempt_transport(self):
  import tenant_kommo
  from tenant_crm_relation_contract import ProviderBinding
  tenant='11111111-1111-4111-8111-111111111111'
  binding=ProviderBinding(tenant,'kommo','test.kommo.com','kommo_manual:test')
  context=SimpleNamespace(platform_error=RuntimeError,credentials=Mock(return_value={'account_domain':'test.kommo.com','auth_mode':'manual','access_token':'fixture-only'}))
  service=tenant_kommo.TenantKommo(context)
  payload={**PAYLOAD,'target_pipeline_id':20,'stage_id':201}
  remote={'id':7,'pipeline_id':10,'status_id':100,'updated_at':1000}
  with patch.object(move,'authorize_cached'):
   body=move.prepare(Mock(),{**P,'tenant_id':tenant},binding,remote,payload)
  self.assertEqual(body,{'pipeline_id':20,'status_id':201})
  response=SimpleNamespace(status_code=200,content=b'{}',json=lambda:{})
  with patch.object(tenant_kommo,'wait_slot'),patch.object(tenant_kommo.requests,'request',return_value=response) as send:
   await service.request_bound(binding,'PATCH','leads/7',json_body=body)
   send.assert_called_once();self.assertEqual(send.call_args.args,('PATCH','https://test.kommo.com/api/v4/leads/7'));self.assertEqual(send.call_args.kwargs['json'],body);self.assertFalse(send.call_args.kwargs['allow_redirects'])
   send.reset_mock();send.side_effect=TimeoutError('response lost')
   with self.assertRaises(TimeoutError):await service.request_bound(binding,'PATCH','leads/7',json_body=body)
   send.assert_called_once()
 async def test_pair_is_narrow_positive_integer_and_leads_only(self):
  import tenant_kommo
  from tenant_crm_relation_contract import ProviderBinding
  binding=ProviderBinding('11111111-1111-4111-8111-111111111111','kommo','test.kommo.com','kommo_manual:test')
  context=SimpleNamespace(platform_error=RuntimeError,credentials=Mock())
  service=tenant_kommo.TenantKommo(context)
  pair={'pipeline_id':20,'status_id':201}
  cases=[('leads/7',{'pipeline_id':20}),('contacts/7',pair),('tasks/7',pair)]
  for key in ('pipeline_id','status_id'):
   for value in (True,False,0,-1,'20',20.5,None):cases.append(('leads/7',{**pair,key:value}))
  for key,value in [('name','other'),('custom_fields_values',[]),('responsible_user_id',7),('text','other')]:cases.append(('leads/7',{**pair,key:value}))
  with patch.object(tenant_kommo.requests,'request') as send:
   for path,body in cases:
    with self.subTest(path=path,body=body):
     with self.assertRaisesRegex(RuntimeError,'Scalar CRM fields are invalid'):await service.request_bound(binding,'PATCH',path,json_body=body)
   send.assert_not_called();context.credentials.assert_not_called()
