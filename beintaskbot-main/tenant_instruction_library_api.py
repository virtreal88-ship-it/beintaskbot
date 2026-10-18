"""Instruction HTTP boundary; no model, transport, agent settings or /webapp."""
import asyncio
from aiohttp import web
from tenant_ai_agents_api import _expected,_origin
from tenant_linear_policy import TenantLinearError
import tenant_instruction_library as store

def handler(member,origin,logger,resource='collection'):
    async def handle(request):
        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'}
        try:
            key=request.match_info.get('lesson_id');asset=request.match_info.get('asset_id')
            if resource=='public':result=await asyncio.to_thread(store.shared,request.match_info['token'],asset)
            else:
                profile=member(request)
                if not profile:raise TenantLinearError('No access',403)
                if request.method=='GET':
                    data=dict(request.query);_expected(data,profile)
                    if asset:result=await asyncio.to_thread(store.private_asset,profile,key,asset)
                    else:result=await asyncio.to_thread(store.operation,profile,'read' if key else 'list',data,key)
                else:
                    _origin(request,origin)
                    if request.content_type!='application/json':raise ValueError('json')
                    data=await request.json()
                    if not isinstance(data,dict):raise ValueError('json')
                    _expected(data,profile)
                    result=await asyncio.to_thread(store.operation,profile,data.get('action','save'),data,key)
            if asset:return web.Response(body=result,content_type='image/jpeg',headers=headers)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as e:return web.json_response({'success':False,'error':str(e)},status=e.status,headers=headers)
        except (KeyError,ValueError,TypeError):return web.json_response({'success':False,'error':'Invalid instruction data'},status=400,headers=headers)
        except Exception:
            logger.error('Instruction operation unavailable')
            return web.json_response({'success':False,'error':'Instruction operation unavailable'},status=503,headers=headers)
    return handle
