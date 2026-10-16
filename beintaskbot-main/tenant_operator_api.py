"""Read-only aggregation for the existing operator dialogs; mutations use current APIs."""
import asyncio
from aiohttp import web
from tenant_linear_policy import TenantLinearError
from tenant_operator_dialogs import listing,history


def handler(member_from_request,logger,resource):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            query=request.query
            if resource=='dialogs':
                result=await asyncio.to_thread(listing,profile,query.get('search',''),query.get('channel',''),query.get('agent',''),query.get('state',''),query.get('offset',0))
            else:
                result=await asyncio.to_thread(history,profile,query.get('provider',''),query.get('lead_id'),query.get('channel_id',''),query.get('conversation_id',''),query.get('before'))
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (TypeError,ValueError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос.'},status=400,headers=headers)
        except Exception:
            logger.exception('Operator dialog read failed')
            return web.json_response({'success':False,'error':'Диалоги временно недоступны.'},status=503,headers=headers)
    return handle
