"""Permission-checked API for native CRM tasks; no provider calls."""
import asyncio
from datetime import date,datetime
from uuid import UUID
from aiohttp import web
from tenant_crm_native_tasks import create_task,get_task,list_tasks,update_task,set_task_state,archive_task,task_options,NativeTaskAccessError,task_access_allowed
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post
from tenant_task_flow import query as flow_query,read as read_flow,TaskFlowError
from tenant_crm_request_context import native_policy_profile

def _jsonable(value):
    if isinstance(value,UUID):return str(value)
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,dict):return {k:_jsonable(v) for k,v in value.items()}
    if isinstance(value,list):return [_jsonable(v) for v in value]
    return value

def _allowed(policy,row,actor_id):
    return task_access_allowed(policy,row,actor_id)


def _expected(data,tenant,actor):
    if 'expected_tenant_id' not in data and 'expected_user_id' not in data:return
    try:
        matches=str(UUID(str(data.get('expected_tenant_id'))))==str(UUID(tenant)) and str(data.get('expected_user_id'))==str(actor)
    except (ValueError,TypeError,AttributeError):matches=False
    if not matches:raise NativeTaskAccessError('task_context_changed',409)

def handler(member_from_request,origin,logger):
    async def handle(request):
        profile=member_from_request(request)
        profile=native_policy_profile(profile) if profile else None
        policy=TenantPolicy(profile or {})
        if not profile or not (policy.allows('tasks') or any(str(item).startswith('tasks:') for item in (profile.get('permissions') or []))):
            return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
        tenant=str(profile['tenant_id']); actor=int(profile['telegram_id']); task_id=request.match_info.get('id')
        if task_id:
            try:task_id=str(UUID(task_id))
            except (TypeError,ValueError):return web.json_response({'success':False,'error':'invalid_id'},status=400)
        try:
            params=flow_query(request,profile)
            if request.method=='GET':
                _expected(request.query,tenant,actor)
                if not policy.allows_action('tasks','view'):
                    return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
                if not task_id and request.query.get('options')=='1':
                    options=await asyncio.to_thread(task_options,tenant,actor,policy.privileged,
                      deal_id=request.query.get('deal_id'),task_id=request.query.get('task_id'))
                    return web.json_response({'success':True,**_jsonable(options)},headers={'Cache-Control':'no-store'})
                if 'expected_tenant_id' in params:
                    result=await asyncio.to_thread(read_flow,profile,source='native',task_id=task_id)
                    return web.json_response({'success':True,**result},headers={'Cache-Control':'no-store'})
                if task_id:
                    row=await asyncio.to_thread(get_task,tenant,task_id)
                    if not _allowed(policy,row,actor):return web.json_response({'success':False,'error':'not_found'},status=404)
                    return web.json_response({'success':True,'task':_jsonable(row)},headers={'Cache-Control':'no-store'})
                rows=await asyncio.to_thread(list_tasks,tenant,search=request.query.get('search',''),responsible_id=None if policy.privileged else actor,scope=policy.pipeline_scope() if profile.get('role')=='manager' else None,limit=100)
                return web.json_response({'success':True,'tasks':_jsonable(rows)},headers={'Cache-Control':'no-store'})
            require_json_post(request,origin)
            data=await request.json()
            if not isinstance(data,dict):return web.json_response({'success':False,'error':'invalid_body'},status=400)
            _expected(data,tenant,actor)
            data={key:value for key,value in data.items() if key not in {'expected_tenant_id','expected_user_id'}}
            version={}
            if 'expected_updated_at' in data:
                if not task_id or not isinstance(data['expected_updated_at'],str) or not data['expected_updated_at']:
                    return web.json_response({'success':False,'error':'invalid_task_version'},status=400)
                version={'expected_updated_at':data.pop('expected_updated_at')}
            permission_action='create' if not task_id else ('delete' if data.get('action')=='archive' else 'update')
            if not policy.allows_action('tasks',permission_action):
                return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
            if not task_id:
                if set(data)-{'deal_id','text','responsible_id','due_at','request_id'}:return web.json_response({'success':False,'error':'invalid_fields'},status=400)
                responsible=int(data.get('responsible_id') or actor)
                if not policy.privileged and responsible!=actor:return web.json_response({'success':False,'error':'forbidden_assignment'},status=403)
                if not policy.privileged and policy.requires_task_approval(creator_id=actor,executor_id=responsible):return web.json_response({'success':False,'error':'approval_required'},status=403)
                row=await asyncio.to_thread(create_task,tenant,data.get('deal_id'),data.get('text'),responsible,due_at=data.get('due_at'),created_by=actor,idempotency_key=data.get('request_id'),actor_id=actor)
            else:
                current=await asyncio.to_thread(get_task,tenant,task_id)
                if not _allowed(policy,current,actor):return web.json_response({'success':False,'error':'not_found'},status=404)
                action=data.get('action')
                if action=='archive' and data=={'action':'archive'}:
                    if not policy.privileged and int(current.get('created_by') or 0)!=actor:return web.json_response({'success':False,'error':'forbidden'},status=403)
                    result=await asyncio.to_thread(archive_task,tenant,task_id,actor_id=actor,**version)
                    return web.json_response({'success':True,'archived':result})
                if action in {'complete','reopen'} and data=={'action':action}:
                    if action=='complete' and not policy.privileged and policy.requires_task_approval(creator_id=int(current.get('created_by') or 0),executor_id=int(current.get('responsible_id') or 0),completion=True):return web.json_response({'success':False,'error':'approval_required'},status=403)
                    row=await asyncio.to_thread(set_task_state,tenant,task_id,'completed' if action=='complete' else 'open',actor_id=actor,**version)
                elif action=='update' and not (set(data)-{'action','text','responsible_id','due_at'}):
                    responsible=int(data.get('responsible_id') or current['responsible_id'])
                    if not policy.privileged and responsible!=actor:return web.json_response({'success':False,'error':'forbidden_assignment'},status=403)
                    row=await asyncio.to_thread(update_task,tenant,task_id,data.get('text',current['text']),responsible,due_at=data.get('due_at',current['due_at']),actor_id=actor,**version)
                else:return web.json_response({'success':False,'error':'invalid_action'},status=400)
            return web.json_response({'success':True,'task':_jsonable(row)},headers={'Cache-Control':'no-store'})
        except NativeTaskAccessError as exc:
            return web.json_response({'success':False,'error':exc.code},status=exc.status,headers={'Cache-Control':'no-store'})
        except TaskFlowError as exc:
            return web.json_response({'success':False,'error':exc.code},status=exc.status,headers={'Cache-Control':'no-store'})
        except TenantLinearError:
            return web.json_response({'success':False,'error':'invalid_origin'},status=403)
        except (ValueError,TypeError) as exc:
            return web.json_response({'success':False,'error':str(exc)[:120]},status=400)
        except Exception:
            logger.exception('Native CRM task operation failed: tenant=%s',tenant)
            return web.json_response({'success':False,'error':'task_operation_failed'},status=503,headers={'Cache-Control':'no-store'})
    return handle
