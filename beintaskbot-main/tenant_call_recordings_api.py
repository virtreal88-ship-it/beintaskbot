"""Same-origin owner import and authenticated audio reads, never public URLs."""
import asyncio
from aiohttp import web
from tenant_ai_agents_api import _origin, _expected
from tenant_call_recording_bundle import MAX_BUNDLE
from tenant_call_recordings import capability, save, load
from tenant_chat_attachment_api import _byte_range, admit
from tenant_linear_policy import TenantLinearError

SLOTS=asyncio.Semaphore(2)


def handler(member_from_request, origin, logger, resource='import'):
    async def handle(request):
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}
        try:
            profile=member_from_request(request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            _expected(request.query,profile)
            admit(profile)
            if resource=='audio':
                if SLOTS.locked():raise TenantLinearError('Прослушивание занято. Повторите позже.',429)
                async with SLOTS:
                    data,audio_type=await asyncio.to_thread(load,profile,request.match_info['record_id'],with_type=True)
                selected=_byte_range(request.headers.get('Range'),len(data))
                extension='ogg' if audio_type=='audio/ogg' else 'wav'
                headers.update({'Accept-Ranges':'bytes','Content-Disposition':'inline; filename="call.'+extension+'"'})
                if selected is False:
                    headers['Content-Range']='bytes */'+str(len(data))
                    return web.Response(status=416,headers=headers)
                if selected:
                    first,last=selected;headers['Content-Range']=f'bytes {first}-{last}/{len(data)}'
                    return web.Response(body=data[first:last+1],status=206,content_type=audio_type,headers=headers)
                return web.Response(body=data,content_type=audio_type,headers=headers)
            route=dict(request.query)
            if request.method=='GET':
                result=await asyncio.to_thread(capability,profile,route)
            else:
                _origin(request,origin)
                await asyncio.to_thread(capability,profile,route)
                if request.content_type!='application/zip' or request.content_length is None or not 100<request.content_length<=MAX_BUNDLE:
                    raise TenantLinearError('Ожидается ограниченный файл тестовой записи .beincall.zip.',400)
                if SLOTS.locked():raise TenantLinearError('Сохранение занято. Повторите позже.',429)
                async with SLOTS:
                    parts=[];size=0
                    async for part in request.content.iter_chunked(65536):
                        size+=len(part)
                        if size>MAX_BUNDLE:raise TenantLinearError('Запись слишком большая.',413)
                        parts.append(part)
                    result=await asyncio.to_thread(save,profile,route,b''.join(parts))
            return web.json_response({'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'error':str(error)},status=error.status,headers=headers)
        except Exception:
            logger.warning('test_call_recording_request_failed')
            return web.json_response({'error':'Не удалось обработать тестовую запись.'},status=503,headers=headers)
    return handle
