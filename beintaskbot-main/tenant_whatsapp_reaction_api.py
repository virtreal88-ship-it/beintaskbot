"""Same-origin employee controls for reactions to a cached WhatsApp message."""
import asyncio

from aiohttp import web

from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError
from tenant_read_session import require_read_session
from tenant_request_origin import require_json_post
from tenant_whatsapp_native_reactions import send, status


def handler(member, origin, logger):
    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            if request.method == 'POST':
                session = member(request)
                if not session:
                    raise TenantLinearError('reaction_access_denied', 403)
                require_json_post(request, origin)
                data = await request.json()
                if not isinstance(data, dict) or set(data) != {
                        'lead_id','message_id','request_id','emoji','expected_tenant_id','expected_user_id'}:
                    raise ValueError('reaction')
                if (str(data['expected_tenant_id']), str(data['expected_user_id'])) != (
                        str(session['tenant_id']), str(session['telegram_id'])):
                    raise TenantLinearError('reaction_workspace_changed', 409)
                result = await asyncio.to_thread(send, session, data['lead_id'], data['message_id'],
                                                 data['request_id'], data['emoji'])
            else:
                session = require_read_session(request, member)
                result = await asyncio.to_thread(status, session, request.query.get('lead_id'),
                                                 request.query.get('request_id'))
            return web.json_response({'success':True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)}, status=error.status, headers=headers)
        except TenantPlatformError:
            return web.json_response({'success':False,'error':'reaction_source_unavailable'}, status=409, headers=headers)
        except (TypeError, ValueError, KeyError, OverflowError):
            return web.json_response({'success':False,'error':'reaction_invalid'}, status=400, headers=headers)
        except Exception:
            logger.warning('WhatsApp reaction status unavailable')
            return web.json_response({'success':False,'error':'reaction_unconfirmed'}, status=503, headers=headers)
    return handle
