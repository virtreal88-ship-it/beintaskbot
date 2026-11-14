"""Bounded existing multipart ingress, scoped recovery, no automatic retries."""
import asyncio
import os
from aiohttp import web
from tenant_chat_upload_api import handler as upload_handler
from tenant_customer_media_service import run,check,capability
from tenant_linear_policy import TenantLinearError
from tenant_read_session import require_read_session


def enabled():
    return os.environ.get('CUSTOMER_MEDIA_ENABLED','').lower()=='true'


def handler(member,origin,logger):
    async def media_run(session,command,content,_request,_wait_slot,_logger):
        if command.get('file',{}).get('type') not in {'picture','video','file'} or command.get('quote_message_id') or len(command.get('text',''))>4096:
            raise TenantLinearError('JPEG, PNG, WebP, MP4 və PDF dəstəklənir.',400)
        return await run(session,command,content,logger)
    inner=upload_handler(member,origin,None,None,logger,runner=media_run)
    async def handle(request):
        if not enabled():return web.json_response({'success':False,'error':'Fayl göndərilməsi əlçatan deyil.','keep_request':False},status=404,headers={'Cache-Control':'no-store'})
        return await inner(request)
    return handle


def read_handler(member,logger,*,receipt=False):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            session=require_read_session(request,member)
            if receipt: result=await check(session,request.query.get('lead_id'),request.query.get('request_id'))
            elif not enabled():result={'enabled':False}
            else:result=await asyncio.to_thread(capability,session,request.query.get('lead_id'))
            return web.json_response({'success':True,**result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error),'keep_request':True},status=error.status,headers=headers)
        except Exception:
            logger.warning('Customer media status unavailable; no dispatch')
            return web.json_response({'success':False,'error':'Faylın vəziyyəti yoxlanmadı. Təkrar göndərməyin.','keep_request':True},status=503,headers=headers)
    return handle
