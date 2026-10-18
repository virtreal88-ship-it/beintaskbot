"""Same-origin APIs for manual knowledge extraction and employee review."""
import asyncio
from aiohttp import web
from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
from tenant_knowledge_candidates_service import analyze
from tenant_knowledge_candidates_store import detail, listing, mutate, request_detail

def handler(member_from_request,canonical_origin,logger,resource='collection'):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile: raise TenantLinearError('Нет доступа.',403)
            if request.method=='GET':
                _expected(request.query,profile)
                if resource=='request': result=await asyncio.to_thread(request_detail,profile,request.match_info['request_id'])
                elif resource=='candidate': result=await asyncio.to_thread(detail,profile,request.match_info['candidate_id'])
                else: result=await asyncio.to_thread(listing,profile,dict(request.query))
            else:
                _origin(request,canonical_origin)
                if request.content_type!='application/json': raise TenantLinearError('Ожидается JSON.')
                data=await request.json()
                if not isinstance(data,dict): raise TenantLinearError('Некорректный запрос.')
                _expected(data,profile)
                data={k:v for k,v in data.items() if k not in ('expected_tenant_id','expected_user_id')}
                if resource=='collection': result=await asyncio.to_thread(analyze,profile,data)
                else: result=await asyncio.to_thread(mutate,profile,request.match_info['candidate_id'],data,'edit' if resource=='candidate' else resource)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Некорректные параметры.'},status=400,headers=headers)
        except Exception:
            logger.error('Knowledge candidate operation unavailable')
            return web.json_response({'success':False,'error':'Операция временно недоступна.'},status=503,headers=headers)
    return handle
