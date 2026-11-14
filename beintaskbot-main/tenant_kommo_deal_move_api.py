"""Same-origin, actor-bound Kommo stage command and read-only verification."""
import asyncio
import uuid
from aiohttp import web
from tenant_task_flow import TaskFlowError
from tenant_crm_request_context import require_expected_context, CRMContextError
from tenant_kommo_deal_move import move, normalize, prepare, verified, authorize_cached, cache_confirmed


def handler(member, origin, bound_request, logger):
    async def handle(request):
        headers={'Cache-Control':'no-store','Vary':'Cookie'}
        try:
            profile=member(request)
            if not profile or profile.get('active') is not True:
                raise TaskFlowError('kommo_stage_forbidden',403)
            if request.method=='GET':
                if set(request.query)!={'request_id','expected_tenant_id','expected_user_id'}:
                    raise ValueError('invalid query')
                if hasattr(request.query,'getall') and any(len(request.query.getall(key))!=1 for key in request.query):
                    raise ValueError('duplicate query')
                uuid.UUID(str(request.query['request_id']))
                require_expected_context(request,profile)
                from tenant_kommo_board_commands import read_payload, execute
                payload=await asyncio.to_thread(read_payload,profile,request.query['request_id'],'deal_stage_move')
                result=await execute(profile,payload,bound_request,operation='deal_stage_move',
                    path='leads/'+str(payload['lead_id']),prepare=prepare,verified=verified,verify_only=True,authorize_cached=authorize_cached,cache_confirmed=cache_confirmed)
            else:
                if request.method!='POST' or request.content_type!='application/json' or request.headers.get('Origin','').rstrip('/')!=origin.rstrip('/'):
                    raise TaskFlowError('invalid_origin',403)
                data=await request.json()
                if not isinstance(data,dict):raise ValueError('invalid body')
                if (str(data.pop('expected_tenant_id',None)),str(data.pop('expected_user_id',None)))!=(str(profile['tenant_id']),str(profile['telegram_id'])):
                    raise TaskFlowError('kommo_context_changed',409)
                result=await move(profile,data,bound_request)
            return web.json_response({'success':True,**result},headers=headers)
        except (TaskFlowError,CRMContextError) as exc:
            keep=getattr(exc,'keep_request',True)
            return web.json_response({'success':False,'error':exc.code,'keep_request':keep},status=exc.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'invalid_stage_move','keep_request':False},status=400,headers=headers)
        except Exception:
            logger.error('Kommo stage move result unavailable')
            return web.json_response({'success':False,'error':'board_outcome_unknown','keep_request':True},status=503,headers=headers)
    return handle
