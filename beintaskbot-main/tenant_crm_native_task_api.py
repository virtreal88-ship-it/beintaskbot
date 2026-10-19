"""Permission-checked API for native CRM tasks; no provider calls."""
import asyncio
from datetime import date,datetime
from uuid import UUID
from aiohttp import web
from tenant_crm_native_tasks import create_task,get_task,list_tasks,update_task,set_task_state,archive_task,task_options
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post

def _jsonable(value):
    if isinstance(value,UUID):return str(value)
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,dict):return {k:_jsonable(v) for k,v in value.items()}
    if isinstance(value,list):return [_jsonable(v) for v in value]
    return value

def _allowed(policy,row,actor_id):
    if not row:return False
    if policy.privileged:return True
    own=int(row.get('responsible_id') or 0)==actor_id or int(row.get('created_by') or 0)==actor_id
    if not own:return False
    if policy.profile.get('role')=='manager':
        return any(str(row.get('pipeline_id'))==str(item['pipeline_id']) and (not item['status_ids'] or str(row.get('stage_id')) in {str(value) for value in item['status_ids']}) for item in policy.pipeline_scope())
    return True

def handler(member_from_request,origin,logger):
    async def handle(request):
        profile=member_from_request(request); policy=TenantPolicy(profile or {})
        if not profile or not policy.allows('tasks'):
            return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
        tenant=str(profile['tenant_id']); actor=int(profile['telegram_id']); task_id=request.match_info.get('id')
        if task_id:
            try:UUID(task_id)
            except (TypeError,ValueError):return web.json_response({'success':False,'error':'invalid_id'},status=400)
        try:
            if request.method=='GET':
                if not task_id and request.query.get('options')=='1':
                    options=await asyncio.to_thread(task_options,tenant,actor,policy.privileged)
                    return web.json_response({'success':True,**_jsonable(options)},headers={'Cache-Control':'no-store'})
                if task_id:
                    row=await asyncio.to_thread(get_task,tenant,task_id)
                    if not _allowed(policy,row,actor):return web.json_response({'success':False,'error':'not_found'},status=404)
                    return web.json_response({'success':True,'task':_jsonable(row)},headers={'Cache-Control':'no-store'})
                rows=await asyncio.to_thread(list_tasks,tenant,search=request.query.get('search',''),responsible_id=None if policy.privileged else actor,scope=policy.pipeline_scope() if profile.get('role')=='manager' else None,limit=100)
                return web.json_response({'success':True,'tasks':_jsonable(rows)},headers={'Cache-Control':'no-store'})
            require_json_post(request,origin)
            data=await request.json()
            if not isinstance(data,dict):return web.json_response({'success':False,'error':'invalid_body'},status=400)
            if not task_id:
                if set(data)-{'deal_id','text','responsible_id','due_at','request_id'}:return web.json_response({'success':False,'error':'invalid_fields'},status=400)
                responsible=int(data.get('responsible_id') or actor)
                if not policy.privileged and responsible!=actor:return web.json_response({'success':False,'error':'forbidden_assignment'},status=403)
                if not policy.privileged and policy.requires_task_approval(creator_id=actor,executor_id=responsible):return web.json_response({'success':False,'error':'approval_required'},status=403)
                row=await asyncio.to_thread(create_task,tenant,data.get('deal_id'),data.get('text'),responsible,due_at=data.get('due_at'),created_by=actor,idempotency_key=data.get('request_id'))
            else:
                current=await asyncio.to_thread(get_task,tenant,task_id)
                if not _allowed(policy,current,actor):return web.json_response({'success':False,'error':'not_found'},status=404)
                action=data.get('action')
                if action=='archive' and data=={'action':'archive'}:
                    if not policy.privileged and int(current.get('created_by') or 0)!=actor:return web.json_response({'success':False,'error':'forbidden'},status=403)
                    result=await asyncio.to_thread(archive_task,tenant,task_id)
                    return web.json_response({'success':True,'archived':result})
                if action in {'complete','reopen'} and data=={'action':action}:
                    if action=='complete' and not policy.privileged and policy.requires_task_approval(creator_id=int(current.get('created_by') or 0),executor_id=int(current.get('responsible_id') or 0),completion=True):return web.json_response({'success':False,'error':'approval_required'},status=403)
                    row=await asyncio.to_thread(set_task_state,tenant,task_id,'completed' if action=='complete' else 'open')
                elif action=='update' and not (set(data)-{'action','text','responsible_id','due_at'}):
                    responsible=int(data.get('responsible_id') or current['responsible_id'])
                    if not policy.privileged and responsible!=actor:return web.json_response({'success':False,'error':'forbidden_assignment'},status=403)
                    row=await asyncio.to_thread(update_task,tenant,task_id,data.get('text',current['text']),responsible,due_at=data.get('due_at',current['due_at']))
                else:return web.json_response({'success':False,'error':'invalid_action'},status=400)
            return web.json_response({'success':True,'task':_jsonable(row)},headers={'Cache-Control':'no-store'})
        except TenantLinearError:
            return web.json_response({'success':False,'error':'invalid_origin'},status=403)
        except (ValueError,TypeError) as exc:
            return web.json_response({'success':False,'error':str(exc)[:120]},status=400)
        except Exception:
            logger.exception('Native CRM task operation failed: tenant=%s',tenant)
            return web.json_response({'success':False,'error':'task_operation_failed'},status=503,headers={'Cache-Control':'no-store'})
    return handle
