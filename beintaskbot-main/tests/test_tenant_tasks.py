"""Task commands with simulated Kommo; never write to production accounts."""
import copy
import ast
from pathlib import Path
from types import ModuleType, SimpleNamespace
from datetime import datetime, timedelta, timezone
import unittest
import uuid
from unittest.mock import Mock, AsyncMock, patch

from tenant_policy import TenantPolicy
class TenantPlatformError(RuntimeError):
    pass


def load_service(filename, namespace):
    """Avoid database driver installation and side effects for unit tests."""
    module=ModuleType(filename)
    module.__dict__.update(namespace)
    tree=ast.parse((Path(__file__).resolve().parents[1]/(filename+'.py')).read_text(encoding='utf-8-sig'))
    tree.body=[node for node in tree.body if not isinstance(node,ast.ImportFrom) or node.module not in {'tenant_platform','tenant_task_commands','tenant_tasks','tenant_task_completion'}]
    exec(compile(tree,filename,'exec'),module.__dict__)
    return module


commands=load_service('tenant_task_commands',{'TenantPlatformError':TenantPlatformError,'_connect':Mock(),'_ensure_schema':Mock()})
TaskCommandPending=commands.TaskCommandPending
TaskCommandStore=commands.TaskCommandStore
service=load_service('tenant_tasks',{'TenantPlatformError':TenantPlatformError,'TaskCommandPending':TaskCommandPending,
    'TaskCommandStore':TaskCommandStore,**{name:Mock() for name in ('member','get_crm_deal','upsert_crm_deals','upsert_crm_tasks','append_audit_event')}})


def person(role='manager', identity=20):
    return {'tenant_id':'company-a','telegram_id':identity,'role':role,'active':True,
            'permissions':['tasks'],'modules':{'tasks':True},'workflow':{
                'pipelines':[{'pipeline_id':10,'owner_telegram_id':20,'active':True},
                             {'pipeline_id':11,'owner_telegram_id':21,'active':True}],
                'stages':[{'pipeline_id':10,'stage_id':100,'sort_order':10},
                          {'pipeline_id':10,'stage_id':101,'sort_order':20},
                          {'pipeline_id':11,'stage_id':110,'sort_order':10}], 'policies':[]}}


class MemoryStore:
    rows = {}
    def __init__(self, tenant, actor, request, fingerprint):
        self.key=(tenant,actor,request)
        previous=self.rows.get(self.key)
        if previous and previous[0] != fingerprint:
            raise TenantPlatformError('different payload')
        self.state=copy.deepcopy(previous[1] if previous else {})
        self.fingerprint=fingerprint
    def save(self, state):
        state={**{key:self.state[key] for key in ('approval','task_input','completion_input') if key in self.state},**state}
        self.state=copy.deepcopy(state);self.rows[self.key]=(self.fingerprint,copy.deepcopy(state))
    def close(self):
        pass
    def lock_deal(self, lead_id):
        pass
    def lock_task(self, task_id):
        pass
    def check_task_completion(self, task_id):
        for key, (_, state) in self.rows.items():
            if key != self.key and key[0] == self.key[0] and state.get('completion_input', {}).get('task_id') == task_id and state.get('step') not in {'done', 'rejected'}:
                raise TenantPlatformError('Already pending')


class TaskServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        MemoryStore.rows={};self.actor=person();self.executor=person();self.calls=[]
        self.lead={'id':7,'pipeline_id':10,'status_id':101,'name':'Existing'}
        self.body={'request_id':str(uuid.uuid4()),'text':'Call client',
                   'due_at':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()}
        self.fail_tasks=False
        self.patchers=[patch.object(service,'TaskCommandStore',MemoryStore),
                       patch.object(service,'member',side_effect=lambda tenant,identity:self.executor if identity==self.executor['telegram_id'] else None),
                       patch.object(service,'get_crm_deal',side_effect=lambda **kwargs:dict(self.lead)),
                       patch.object(service,'upsert_crm_deals'),patch.object(service,'upsert_crm_tasks'),patch.object(service,'append_audit_event')]
        for patcher in self.patchers: patcher.start();self.addCleanup(patcher.stop)

    async def request(self, tenant, method, path, **kwargs):
        self.calls.append((method,path,kwargs))
        if path=='account':return {'current_user_id':999}
        if path=='leads/pipelines':return {'_embedded':{'pipelines':[
            {'id':10,'_embedded':{'statuses':[{'id':100,'name':'New','type':0}]}},
            {'id':11,'_embedded':{'statuses':[{'id':110,'name':'New','type':0}]}}]}}
        if method=='GET' and path.startswith('leads/'):return dict(self.lead)
        if method=='POST' and path=='leads':return {'_embedded':{'leads':[{'id':8}]}}
        if method=='PATCH':return {'id':7}
        if path=='tasks':
            if self.fail_tasks:raise TimeoutError('Response lost')
            return {'_embedded':{'tasks':[{'id':9}]}}
        raise AssertionError((method,path))

    async def test_without_deal_creates_lead_then_task_on_single_admin(self):
        result=await service.create_task(self.actor,self.body,self.request)
        self.assertEqual(result['lead_id'],8)
        writes=[call for call in self.calls if call[0]=='POST']
        self.assertEqual([call[1] for call in writes],['leads','tasks'])
        lead_body=writes[0][2]['json_body'][0];task_body=writes[1][2]['json_body'][0]
        self.assertEqual((lead_body['pipeline_id'],lead_body['status_id']),(10,100))
        self.assertEqual(task_body['entity_id'],8);self.assertEqual(task_body['responsible_user_id'],999)
        self.assertNotIn('[CRM:',task_body['text'])

    async def test_self_task_does_not_reset_current_stage_or_request_review(self):
        self.body['lead_id']=7
        self.executor['workflow']['policies']=[{'policy_key':'task_approval','value':{'creation_requires_admin':True}}]
        await service.create_task(self.actor,self.body,self.request)
        self.assertFalse(any(method=='PATCH' for method,_,_ in self.calls))
        self.assertEqual(self.calls[-1][1],'tasks')

    def require_review(self):
        self.executor['workflow']['policies']=[{'policy_key':'task_approval','value':
            {'creation_requires_admin':True,'self_created_exempt':False}}]

    async def test_review_queue_is_durable_and_retry_does_not_contact_kommo(self):
        self.require_review()
        first=await service.create_task(self.actor,self.body,self.request)
        second=await service.create_task(self.actor,self.body,self.request)
        self.assertTrue(first['approval_pending']);self.assertEqual(first,second)
        self.assertEqual(self.calls,[])
        state=MemoryStore.rows[('company-a',20,self.body['request_id'])][1]
        self.assertEqual(state['step'],'waiting_approval');self.assertEqual(state['task_input']['text'],self.body['text'])

    async def test_reviewed_request_is_created_once_and_keeps_reviewer(self):
        self.require_review()
        await service.create_task(self.actor,self.body,self.request)
        reviewer=person('owner',1)
        result=await service.create_task(self.actor,self.body,self.request,reviewer=reviewer)
        count=len(self.calls)
        self.assertEqual(await service.create_task(self.actor,self.body,self.request,reviewer=reviewer),result)
        self.assertEqual(len(self.calls),count)
        state=MemoryStore.rows[('company-a',20,self.body['request_id'])][1]
        self.assertEqual(state['approval']['reviewer_id'],1)
        self.assertIn('task_input',state)

    async def test_review_authority_cannot_be_forged_by_task_body_or_other_tenant(self):
        self.require_review();self.body['reviewer']={'role':'owner'}
        self.assertTrue((await service.create_task(self.actor,self.body,self.request))['approval_pending'])
        for reviewer in (person('manager',21),{**person('owner',1),'tenant_id':'company-b'},
                         {**person('admin',1),'permissions':[]},{**person('owner',1),'active':False}):
            with self.assertRaises(TenantPlatformError):
                await service.create_task(self.actor,self.body,self.request,reviewer=reviewer)
        self.assertEqual(self.calls,[])

    async def test_review_cannot_bypass_current_pipeline_access(self):
        self.require_review();self.body['lead_id']=7
        await service.create_task(self.actor,self.body,self.request)
        self.lead['pipeline_id']=11
        with self.assertRaises(TenantPlatformError):
            await service.create_task(self.actor,self.body,self.request,reviewer=person('owner',1))
        self.assertFalse(any(method!='GET' for method,_,_ in self.calls))
        self.assertEqual(MemoryStore.rows[('company-a',20,self.body['request_id'])][1]['step'],'waiting_approval')

    async def test_rejected_request_never_reposts(self):
        self.require_review()
        await service.create_task(self.actor,self.body,self.request)
        key=('company-a',20,self.body['request_id']);fingerprint,state=MemoryStore.rows[key]
        MemoryStore.rows[key]=(fingerprint,{**state,'step':'rejected'})
        with self.assertRaises(TenantPlatformError):await service.create_task(self.actor,self.body,self.request)
        self.assertEqual(self.calls,[])

    async def test_lost_review_write_response_remains_uncertain_without_reposting(self):
        self.require_review()
        await service.create_task(self.actor,self.body,self.request)
        self.fail_tasks=True
        reviewer=person('owner',1)
        with self.assertRaises(TaskCommandPending):await service.create_task(self.actor,self.body,self.request,reviewer=reviewer)
        count=len(self.calls)
        with self.assertRaises(TaskCommandPending):await service.create_task(self.actor,self.body,self.request,reviewer=reviewer)
        self.assertEqual(len(self.calls),count)
        state=MemoryStore.rows[('company-a',20,self.body['request_id'])][1]
        self.assertEqual(state['step'],'task_creating');self.assertIn('approval',state)

    async def test_admin_routes_to_executor_pipeline_before_creating_task(self):
        self.actor=person('owner',1);self.executor=person('manager',21)
        self.body.update(lead_id=7,executor_id=21)
        await service.create_task(self.actor,self.body,self.request)
        writes=[call for call in self.calls if call[0]!='GET']
        self.assertEqual(writes[0],('PATCH','leads/7',{'json_body':{'pipeline_id':11,'status_id':110}}))
        self.assertEqual(writes[1][1],'tasks')

    async def test_admin_assigning_worker_keeps_pipeline_and_marks_task(self):
        self.actor=person('owner',1);self.executor=person('worker',30)
        self.body.update(lead_id=7,executor_id=30)
        await service.create_task(self.actor,self.body,self.request)
        self.assertFalse(any(method=='PATCH' for method,_,_ in self.calls))
        self.assertIn(TenantPolicy(self.executor).task_marker(),self.calls[-1][2]['json_body'][0]['text'])

    async def test_incoming_stage_is_skipped_when_selecting_first_open_stage(self):
        async def request(tenant,method,path,**kwargs):
            if path=='leads/pipelines':
                return {'_embedded':{'pipelines':[{'id':10,'_embedded':{'statuses':[
                    {'id':100,'type':1,'name':'Incoming'},{'id':101,'type':0,'name':'New'}]}}]}}
            return await self.request(tenant,method,path,**kwargs)
        await service.create_task(self.actor,self.body,request)
        body=next(call for call in self.calls if call[:2]==('POST','leads'))[2]['json_body'][0]
        self.assertEqual(body['status_id'],101)

    async def test_worker_standalone_task_is_marked_without_creating_deal(self):
        self.actor=self.executor=person('worker',30)
        await service.create_task(self.actor,self.body,self.request)
        task=self.calls[-1][2]['json_body'][0]
        self.assertNotIn('entity_id',task);self.assertIn(TenantPolicy(self.actor).task_marker(),task['text'])
        self.assertFalse(any(path=='leads' for _,path,_ in self.calls))

    async def test_foreign_executor_or_live_pipeline_denied_before_writes(self):
        self.body['executor_id']=21
        with self.assertRaises(TenantPlatformError): await service.create_task(self.actor,self.body,self.request)
        self.assertEqual(self.calls,[])
        self.body.pop('executor_id');self.body['lead_id']=7;self.lead['pipeline_id']=11
        with self.assertRaises(TenantPlatformError): await service.create_task(self.actor,self.body,self.request)
        self.assertFalse(any(method!='GET' for method,_,_ in self.calls))

    async def test_cached_own_deal_moved_elsewhere_is_denied(self):
        self.body['lead_id']=7
        with patch.object(service,'get_crm_deal',return_value={'pipeline_id':10,'status_id':101}):
            self.lead['pipeline_id']=11
            with self.assertRaises(TenantPlatformError): await service.create_task(self.actor,self.body,self.request)
        self.assertFalse(any(method!='GET' for method,_,_ in self.calls))

    async def test_completed_command_is_returned_without_reposting(self):
        first=await service.create_task(self.actor,self.body,self.request);count=len(self.calls)
        second=await service.create_task(self.actor,self.body,self.request)
        self.assertEqual(first,second);self.assertEqual(len(self.calls),count)
        with patch.object(service.time,'time',return_value=datetime.now(timezone.utc).timestamp()+10*86400):
            self.assertEqual(await service.create_task(self.actor,self.body,self.request),first)
        self.body['text']='Different'
        with self.assertRaises(TenantPlatformError): await service.create_task(self.actor,self.body,self.request)

    async def test_lost_response_is_not_replayed_or_new_lead_created(self):
        self.fail_tasks=True
        with self.assertRaises(TaskCommandPending):await service.create_task(self.actor,self.body,self.request)
        count=len(self.calls)
        with self.assertRaises(TaskCommandPending):await service.create_task(self.actor,self.body,self.request)
        self.assertEqual(len(self.calls),count)
        self.assertEqual(sum(method=='POST' and path=='leads' for method,path,_ in self.calls),1)

    async def test_cache_failure_after_success_does_not_duplicate_task(self):
        with patch.object(service,'upsert_crm_tasks',side_effect=RuntimeError('cache down')), self.assertLogs('tenant_tasks',level='ERROR'):
            result=await service.create_task(self.actor,self.body,self.request)
        self.assertEqual(result['task_id'],9)
        await service.create_task(self.actor,self.body,self.request)
        self.assertEqual(sum(method=='POST' and path=='tasks' for method,path,_ in self.calls),1)

    def test_invalid_text_and_deadline_rejected(self):
        for change in [{'text':''},{'due_at':'bad'},{'text':'[CRM:forged]'}]:
            with self.assertRaises(TenantPlatformError):service.normalize_task_input({**self.body,**change},20)

    async def test_new_overdue_task_does_not_write_to_provider(self):
        self.body['due_at']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        with self.assertRaises(TenantPlatformError):await service.create_task(self.actor,self.body,self.request)
        self.assertEqual(self.calls,[])


class TaskCommandStorageTests(unittest.TestCase):
    def test_advisory_lock_failure_closes_connection(self):
        from unittest.mock import MagicMock
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchone.return_value={'locked':False}
        with patch.object(commands,'_connect',return_value=conn),patch.object(commands,'_ensure_schema'):
            with self.assertRaises(TaskCommandPending):TaskCommandStore('tenant',20,str(uuid.uuid4()),'hash')
        conn.close.assert_called_once();conn.commit.assert_not_called()

    def test_checkpoints_commit_without_dropping_session_lock(self):
        from unittest.mock import MagicMock
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect=[{'locked':True},{'fingerprint':'hash','state':{}}]
        with patch.object(commands,'_connect',return_value=conn),patch.object(commands,'_ensure_schema'):
            store=TaskCommandStore('tenant',20,str(uuid.uuid4()),'hash')
            store.save({'step':'task_creating'});store.close()
        self.assertEqual(conn.commit.call_count,2);conn.close.assert_called_once()
        self.assertIn('pg_try_advisory_lock',cur.execute.call_args_list[0].args[0])
        self.assertFalse(any('unlock' in call.args[0] for call in cur.execute.call_args_list))


class TaskEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'))
        tree.body=[node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='handle_platform_task_create']
        self.create=AsyncMock(return_value={'task_id':9})
        self.profile=person()
        self.ns={'web':SimpleNamespace(Request=dict,Response=dict,json_response=lambda data,status=200:(status,data)),
                 '_tenant_member_from_request':lambda request:self.profile,'TenantPolicy':TenantPolicy,
                 'create_tenant_task':self.create,'_tenant_kommo_request':Mock(),'CANONICAL_WEB_ORIGIN':'https://crm.pro.az',
                 'TaskCommandPending':TaskCommandPending,'TenantPlatformError':TenantPlatformError,'logger':Mock()}
        exec(compile(tree,'<task-api>','exec'),self.ns)
        self.request=SimpleNamespace(content_type='application/json',headers={'Origin':'https://crm.pro.az'},json=AsyncMock(return_value={}))

    async def test_missing_permissions_and_foreign_origin_are_rejected(self):
        self.profile['permissions']=[]
        self.assertEqual((await self.ns['handle_platform_task_create'](self.request))[0],403)
        self.profile['permissions']=['tasks'];self.request.headers['Origin']='https://evil.example'
        self.assertEqual((await self.ns['handle_platform_task_create'](self.request))[0],403)
        self.create.assert_not_awaited()

    async def test_unknown_write_result_is_explicitly_marked_for_same_request(self):
        self.create.side_effect=TaskCommandPending('check request')
        status,body=await self.ns['handle_platform_task_create'](self.request)
        self.assertEqual(status,409);self.assertTrue(body['retry_same_request'])

    async def test_valid_command_is_forwarded(self):
        status,body=await self.ns['handle_platform_task_create'](self.request)
        self.assertEqual(status,200);self.assertEqual(body['task_id'],9)
        self.create.assert_awaited_once()


if __name__=='__main__':unittest.main()
