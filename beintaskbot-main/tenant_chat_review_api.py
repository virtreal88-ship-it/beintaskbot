"""Read-only listing and same-origin, identity-bound manual attestation."""
import asyncio
from aiohttp import web
from tenant_chat_review_store import list_receipts, resolve
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, canonical_origin, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            session = member_from_request(request)
            if not session:
                raise TenantLinearError('İcazə yoxdur.', 403)
            if request.method == 'GET':
                result = await asyncio.to_thread(list_receipts, session, request.query.get('lead_id'))
            else:
                if request.method != 'POST' or request.content_type != 'application/json' or request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/'):
                    raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403)
                data = await request.json()
                if not isinstance(data, dict):
                    raise ValueError('Invalid body')
                if str(data.get('expected_tenant_id', '')) != str(session['tenant_id']) or str(data.get('expected_user_id', '')) != str(session['telegram_id']):
                    raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
                result = await asyncio.to_thread(resolve, session, data)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Sorğu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant chat review unavailable')
            return web.json_response({'success': False, 'error': 'Nəticə yoxlanmadı. Eyni sorğunu təkrar yoxlayın.'}, status=503, headers=headers)
    return handle
