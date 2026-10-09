"""Explicit same-origin bounded import; no sends, AI or media credentials."""
from aiohttp import web
from tenant_chat_import_service import run
from tenant_chat_import_store import authorize
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, origin, provider, member, upsert, normalize, channel, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            session = member_from_request(request)
            if not session or request.method != 'POST' or request.content_type != 'application/json' or request.headers.get('Origin', '').rstrip('/') != origin.rstrip('/'):
                raise TenantLinearError('İcazə yoxdur.', 403)
            data = await request.json()
            if not isinstance(data, dict): raise ValueError()
            if (str(data.get('expected_tenant_id')), str(data.get('expected_user_id'))) != (str(session['tenant_id']), str(session['telegram_id'])):
                raise TenantLinearError('Kabinet dəyişib.', 409)
            lead, cursor = data.get('lead_id'), data.get('cursor', '')
            if type(lead) is not int or not 0 < lead < 2**63 or not isinstance(cursor, str) or len(cursor) > 2048: raise ValueError()
            result = await run(session, lead, cursor, provider, authorize, member, upsert, normalize, channel)
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError):
            return web.json_response({'success': False, 'error': 'Tarixçə sorğusu düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            logger.error('Tenant manual history import unavailable')
            return web.json_response({'success': False, 'error': 'Tarixçə yüklənmədi. Eyni səhifəni yenidən yoxlayın.'}, status=503, headers=headers)
    return handle
