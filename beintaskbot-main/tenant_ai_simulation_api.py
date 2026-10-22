"""Same-origin, tenant-scoped controls and read-only results for Simulation."""
import asyncio
from aiohttp import web

from tenant_ai_simulation_engine import list_runs,read_config,save_config
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, canonical_origin, logger, resource):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.',403)
            if request.method=='GET':
                if resource=='runs':
                    result=await asyncio.to_thread(list_runs,profile,limit=request.query.get('limit',50))
                else:
                    result=await asyncio.to_thread(read_config,profile)
                return web.json_response({'success':True,'result':result},headers=headers)
            if request.headers.get('Origin','').rstrip('/') != canonical_origin.rstrip('/'):
                raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.',403)
            if request.content_type!='application/json':
                raise TenantLinearError('JSON sorğusu tələb olunur.',415)
            if request.content_length and request.content_length>64*1024:
                raise TenantLinearError('Sorğu həddən artıq böyükdür.',413)
            data=await request.json()
            if not isinstance(data,dict):
                raise TenantLinearError('Sorğu düzgün deyil.')
            if (str(data.get('expected_tenant_id') or '')!=str(profile.get('tenant_id'))
                    or str(data.get('expected_user_id') or '')!=str(profile.get('telegram_id'))):
                raise TenantLinearError('Kabinet dəyişib. Yeniləyin.',409)
            result=await asyncio.to_thread(save_config,profile,
                {key:value for key,value in data.items() if key not in {'expected_tenant_id','expected_user_id'}})
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as exc:
            return web.json_response({'success':False,'error':str(exc),'code':'simulation_request_rejected'},
                                     status=exc.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Simulation sorğusu düzgün deyil.',
                                      'code':'simulation_invalid_request'},status=400,headers=headers)
        except Exception:
            logger.error('Tenant AI Simulation API request failed')
            return web.json_response({'success':False,'error':'Simulation xidməti əlçatan deyil.',
                                      'code':'simulation_unavailable'},status=503,headers=headers)
    return handle
