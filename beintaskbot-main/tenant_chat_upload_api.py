"""Bounded multipart ingress; only authenticated account Drive is used."""
import asyncio
import json
from contextlib import AsyncExitStack
from io import BytesIO
from aiohttp import web
from tenant_chat_upload_policy import prepare, MAX_BYTES
from tenant_chat_upload_service import run
from tenant_linear_policy import TenantLinearError

SLOTS = asyncio.Semaphore(2)
LARGE_VIDEO_SLOTS = asyncio.Semaphore(1)


def handler(member_from_request, origin, provider, wait_slot, logger, *, runner=run, video_limit=None):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            session = member_from_request(request)
            if (not session or request.method != 'POST' or request.content_type != 'multipart/form-data'
                    or request.headers.get('Origin','').rstrip('/') != origin.rstrip('/')):
                raise TenantLinearError('İcazə yoxdur.', 403)
            if SLOTS.locked():
                raise TenantLinearError('Fayllar göndərilir. Bir az sonra yoxlayın.', 429)
            async with SLOTS, AsyncExitStack() as upload:
                async with asyncio.timeout(30) as deadline:
                    reader = await request.multipart()
                    part = await reader.next()
                    if part is None or part.name != 'command':
                        raise ValueError()
                    parts, length = [], 0
                    while not part.at_eof():
                        chunk = await part.read_chunk(8192)
                        length += len(chunk)
                        if length > 32768:
                            raise ValueError()
                        parts.append(chunk)
                    raw = b''.join(parts)
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError()
                    if (str(data.get('expected_tenant_id')), str(data.get('expected_user_id'))) != (str(session['tenant_id']), str(session['telegram_id'])):
                        raise TenantLinearError('Kabinet dəyişib.', 409)
                    part = await reader.next()
                    if part is None or part.name != 'file' or part.headers.get('Content-Encoding') or part.headers.get('Content-Transfer-Encoding'):
                        raise ValueError()
                    mime = part.headers.get('Content-Type','').split(';')[0]
                    limit = MAX_BYTES
                    if mime == 'video/mp4' and video_limit is not None:
                        limit = await video_limit(session, data.get('lead_id'))
                        if limit > MAX_BYTES:
                            if LARGE_VIDEO_SLOTS.locked():
                                raise TenantLinearError('Başqa video göndərilir. Bir az sonra yoxlayın.', 429)
                            await upload.enter_async_context(LARGE_VIDEO_SLOTS)
                            deadline.reschedule(asyncio.get_running_loop().time()+210)
                    with BytesIO() as buffer:
                        while not part.at_eof():
                            chunk = await part.read_chunk(65536)
                            if buffer.tell()+len(chunk)>limit:
                                raise TenantLinearError(f'Fayl {limit // (1024 * 1024)} MB-dan böyükdür.', 413)
                            buffer.write(chunk)
                        content = buffer.getvalue()
                    command = prepare(data, part.filename, mime, content, video_limit=limit)
                    if await reader.next() is not None:
                        raise ValueError()
                result = await runner(session, command, content, provider, wait_slot, logger)
            return web.json_response({'success':True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error),'keep_request':getattr(error,'keep_request',False)},status=error.status,headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success':False,'error':'Fayl sorğusu düzgün deyil.','keep_request':False},status=400,headers=headers)
        except Exception:
            logger.error('Tenant file API unavailable')
            return web.json_response({'success':False,'error':'Göndərmə nəticəsini yoxlayın.','keep_request':True},status=503,headers=headers)
    return handle
