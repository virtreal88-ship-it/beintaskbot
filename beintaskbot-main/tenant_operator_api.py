"""Tenant-isolated operator dialog projection and local display-name overrides."""
import asyncio
import json
from aiohttp import web
from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
from tenant_operator_dialogs import listing,history,save_display_name,mark_read
from tenant_ai_group_coordination import configure as configure_group, register_employee
from tenant_operator_assignment import options as assignment_options, assign as assign_agent, bulk_preview as assignment_bulk_preview, bulk_apply as assignment_bulk_apply
from tenant_operator_continuation import options as continuation_options, grant as grant_continuation
from tenant_operator_legacy import options as legacy_options, recover as recover_legacy
from tenant_operator_copilot import run as run_copilot
from tenant_connect_avatars import get_avatar
from tenant_read_timing import ReadTiming


def handler(member_from_request,origin,logger,resource):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        timing=ReadTiming(1 if resource=='dialogs' else 2,logger) if request.method=='GET' and resource in {'dialogs','history'} else None
        try:
            profile=timing.authenticate(member_from_request,request) if timing else member_from_request(request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            query=request.query
            if request.method=='GET':
                # Optional identity binding for newer clients; authorization remains unchanged.
                if 'X-Tenant-Id' in request.headers or 'X-User-Id' in request.headers:
                    _expected({'expected_tenant_id':request.headers.get('X-Tenant-Id'),'expected_user_id':request.headers.get('X-User-Id')},profile)
                if resource=='avatar':
                    content,mime=await get_avatar(profile,dict(query))
                    return web.Response(body=content,content_type=mime,headers={
                        'Cache-Control':'private, max-age=3600',
                        'X-Content-Type-Options':'nosniff','Vary':'Cookie'})
                elif resource=='dialogs':
                    result=await timing.run(listing,profile,query.get('search',''),query.get('channel',''),query.get('agent',''),query.get('state',''),query.get('offset',0))
                elif resource=='history':
                    result=await timing.run(history,profile,query.get('provider',''),query.get('lead_id'),query.get('channel_id',''),query.get('conversation_id',''),query.get('before'),query.get('anchor'))
                elif resource=='agent_assignment':
                    result=await asyncio.to_thread(assignment_options,profile,dict(query))
                elif resource=='agent_assignment_bulk':
                    result=await asyncio.to_thread(assignment_bulk_preview,profile,query.get('channel_id'),query.get('agent_id'))
                elif resource in {'legacy_recovery','legacy_continuation'}:
                    _expected(query,profile)
                    result=await asyncio.to_thread(continuation_options if resource=='legacy_continuation' else legacy_options,profile,dict(query))
                else:
                    raise TenantLinearError('Метод не поддерживается.',405)
            else:
                _origin(request,origin)
                if request.method!='POST' or resource not in {'display_name','agent_assignment','agent_assignment_bulk','group_coordination','group_staff','legacy_recovery','legacy_continuation','copilot','dialogs_read'} or request.content_type!='application/json':
                    raise TenantLinearError('Метод не поддерживается.',405)
                if request.content_length is not None and request.content_length>20_000:
                    raise TenantLinearError('Слишком большой запрос.',413)
                raw=await request.read()
                if len(raw)>20_000: raise TenantLinearError('Слишком большой запрос.',413)
                data=json.loads(raw)
                if not isinstance(data,dict): raise TenantLinearError('Некорректный запрос.')
                _expected(data,profile)
                payload={key:value for key,value in data.items() if key not in {'expected_tenant_id','expected_user_id'}}
                result=await asyncio.to_thread(mark_read if resource=='dialogs_read' else run_copilot if resource=='copilot' else assignment_bulk_apply if resource=='agent_assignment_bulk' else grant_continuation if resource=='legacy_continuation' else recover_legacy if resource=='legacy_recovery' else register_employee if resource=='group_staff' else configure_group if resource=='group_coordination' else assign_agent if resource=='agent_assignment' else save_display_name,profile,payload)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (TypeError,ValueError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос.'},status=400,headers=headers)
        except Exception:
            logger.exception('Operator dialog read failed')
            return web.json_response({'success':False,'error':'Диалоги временно недоступны.'},status=503,headers=headers)
        finally:
            if timing: timing.finish()
    return handle
