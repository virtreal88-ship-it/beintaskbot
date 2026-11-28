import asyncio
import json
from aiohttp import web
from worker_crm_capacity import session_io
from tenant_ai_agents_api import _expected,_origin
from tenant_browser_calls_api import HEADERS,body
from tenant_linear_policy import TenantLinearError
import tenant_whatsapp_integration as store


def handler(member,origin,logger):
    async def handle(request):
        try:
            profile=await session_io.run(member, request)
            if not profile:raise TenantLinearError('Войдите в программу.',403)
            _expected(request.query,profile)
            if request.method=='GET':
                source=request.query.get('source')
                result=await asyncio.to_thread(store.pairing,profile,source) if source else await asyncio.to_thread(store.inventory,profile)
            else:
                _origin(request,origin)
                data=json.loads(await body(request,2048))
                if not isinstance(data,dict):raise ValueError()
                if set(data)=={'action','phone','name'} and data['action']=='begin':
                    result=await asyncio.to_thread(store.begin,profile,data['phone'],data['name'])
                elif set(data)=={'action','source'} and data['action']=='activate':
                    result=await asyncio.to_thread(store.activate,profile,data['source'])
                elif set(data)=={'action','source'} and data['action']=='continue':
                    result=await asyncio.to_thread(store.continue_pairing,profile,data['source'])
                elif set(data)=={'action','source'} and data['action']=='remove':
                    from tenant_whatsapp_source_control import remove
                    result=await asyncio.to_thread(remove,profile,data['source'])
                elif set(data)=={'action','source'} and data['action']=='resume':
                    from tenant_whatsapp_source_control import resume
                    result=await asyncio.to_thread(resume,profile,data['source'])
                elif set(data)=={'action','sources'} and data['action'] in ('disconnect','disconnect_all'):
                    from tenant_whatsapp_source_control import disconnect
                    if data['action']=='disconnect' and (not isinstance(data['sources'],list) or len(data['sources'])!=1):raise ValueError()
                    result=await asyncio.to_thread(disconnect,profile,data['sources'],all_sources=data['action']=='disconnect_all')
                else:raise ValueError()
            return web.json_response({'success':True,'result':result},headers=HEADERS)
        except TenantLinearError as exc:
            return web.json_response({'success':False,'error':str(exc)},status=exc.status,headers=HEADERS)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос подключения.'},status=400,headers=HEADERS)
        except Exception:
            logger.warning('whatsapp_integration_request_failed')
            return web.json_response({'success':False,'error':'Не удалось подтвердить подключение. Повторите проверку; история сохранена.'},status=503,headers=HEADERS)
    return handle
