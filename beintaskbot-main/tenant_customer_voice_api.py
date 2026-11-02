"""Same-origin voice recording ingress; no automatic provider retries."""
import asyncio
import json
import os
from aiohttp import web
from tenant_customer_voice import MAX_RECORDING_BYTES
from tenant_customer_voice_service import can_send, check, check_visible, prepare, run
from tenant_linear_policy import TenantLinearError
from tenant_read_session import require_read_session

SLOTS = asyncio.Semaphore(2)


def enabled():
    return os.environ.get('CUSTOMER_VOICE_ENABLED', '').lower() == 'true'


def read_session(request, member_from_request):
    member=member_from_request(request)
    if not member:
        raise TenantLinearError('İcazə yoxdur.',401)
    try:
        return require_read_session(request,lambda _:member)
    except TenantLinearError as error:
        if error.status==409:error.error_code='voice_identity_changed'
        raise


def error_data(error,keep_request):
    return {'success':False,'error':str(error),'keep_request':keep_request,
            **({'error_code':error.error_code} if getattr(error,'error_code',None) else {})}


def handler(member_from_request, origin, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            session = member_from_request(request)
            if not session:raise TenantLinearError('İcazə yoxdur.',401)
            if not enabled():
                raise TenantLinearError('Səs göndərilməsi əlçatan deyil.', 404)
            if (request.method != 'POST' or request.content_type != 'multipart/form-data'
                    or request.headers.get('Origin', '').rstrip('/') != origin.rstrip('/')):
                raise TenantLinearError('İcazə yoxdur.', 403)
            if SLOTS.locked():
                raise TenantLinearError('Səs göndərilir. Bir az sonra yoxlayın.', 429)
            async with SLOTS:
                async with asyncio.timeout(30):
                    reader = await request.multipart()
                    part = await reader.next()
                    if part is None or part.name != 'command':
                        raise ValueError()
                    chunks, length = [], 0
                    while not part.at_eof():
                        chunk = await part.read_chunk(8192); length += len(chunk)
                        if length > 32768:
                            raise ValueError()
                        chunks.append(chunk)
                    data = json.loads(b''.join(chunks))
                    if not isinstance(data, dict):
                        raise ValueError()
                    if (str(data.get('expected_tenant_id')), str(data.get('expected_user_id'))) != (str(session['tenant_id']), str(session['telegram_id'])):
                        error=TenantLinearError('Kabinet dəyişib.',409);error.error_code='voice_identity_changed';raise error
                    part = await reader.next()
                    if part is None or part.name != 'file' or part.headers.get('Content-Encoding') or part.headers.get('Content-Transfer-Encoding'):
                        raise ValueError()
                    mime = part.headers.get('Content-Type', '').split(';')[0].strip().lower()
                    chunks, length = [], 0
                    while not part.at_eof():
                        chunk = await part.read_chunk(65536); length += len(chunk)
                        if length > MAX_RECORDING_BYTES:
                            raise TenantLinearError('Səs faylı 5 MB-dan böyükdür.', 413)
                        chunks.append(chunk)
                    if await reader.next() is not None:
                        raise ValueError()
                if not await asyncio.to_thread(can_send, session, data.get('lead_id')):
                    raise TenantLinearError('Bu dialoqda səs göndərilməsi əlçatan deyil.', 403)
                command, content = await asyncio.to_thread(prepare, data, mime, b''.join(chunks))
                result = await run(session, command, content, logger)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response(error_data(error,getattr(error,'keep_request',False)), status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Səs sorğusu düzgün deyil.', 'keep_request': False}, status=400, headers=headers)
        except Exception:
            logger.error('Customer voice request unavailable')
            return web.json_response({'success': False, 'error': 'Göndərmə nəticəsini yoxlayın.', 'keep_request': True}, status=503, headers=headers)
    return handle


def receipt_handler(member_from_request, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            session = read_session(request, member_from_request)
            result = await check(session, request.query.get('lead_id'), request.query.get('request_id'))
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response(error_data(error,True), status=error.status, headers=headers)
        except Exception:
            logger.warning('Customer voice receipt read unavailable; no dispatch')
            return web.json_response({'success': False, 'error': 'Səsin vəziyyəti yoxlanmadı. Təkrar göndərməyin.', 'keep_request': True}, status=503, headers=headers)
    return handle


def capability_handler(member_from_request, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            session = read_session(request,member_from_request)
            allowed = await asyncio.to_thread(can_send, session, request.query.get('lead_id'))
            return web.json_response({'success': True, 'enabled': bool(enabled() and allowed),
                                      'recovery_enabled': bool(allowed)}, headers=headers)
        except TenantLinearError as error:
            return web.json_response(error_data(error,True), status=error.status, headers=headers)
        except Exception:
            logger.warning('Customer voice capability unavailable')
            return web.json_response({'success': False, 'error': 'Səs göndərilməsi əlçatan deyil.'}, status=503, headers=headers)
    return handle


def status_handler(member_from_request, logger):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            session=read_session(request,member_from_request)
            result=await check_visible(session,request.query.get('lead_id'),request.query.get('message_row_id'))
            return web.json_response({'success':True,**result},headers=headers)
        except TenantLinearError as error:
            return web.json_response(error_data(error,True),status=error.status,headers=headers)
        except Exception:
            logger.warning('Customer voice delivery status unavailable')
            return web.json_response({'success':False,'error':'Səs vəziyyəti yoxlanmadı.'},status=503,headers=headers)
    return handle
