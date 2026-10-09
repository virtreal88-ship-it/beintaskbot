"""Same-origin, company/user-bound chat send endpoint."""
from aiohttp import web
from tenant_chat_send_service import run
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, canonical_origin, request_provider, wait_slot, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            session = member_from_request(request)
            if not session:
                raise TenantLinearError('İcazə yoxdur.', 403)
            if request.method != 'POST' or request.content_type != 'application/json' or request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/'):
                raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403)
            data = await request.json()
            if not isinstance(data, dict):
                raise TenantLinearError('Sorğu düzgün deyil.')
            if str(data.get('expected_tenant_id') or '') != str(session['tenant_id']) or str(data.get('expected_user_id') or '') != str(session['telegram_id']):
                raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
            result = await run(session, data, request_provider, wait_slot, logger)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error), 'keep_request': getattr(error, 'keep_request', False)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant chat send service unavailable')
            return web.json_response({'success': False, 'error': 'Göndərmə vəziyyəti yoxlanmadı. Sorğunu dəyişmədən yenidən yoxlayın.', 'keep_request': True}, status=503, headers=headers)
    return handle
