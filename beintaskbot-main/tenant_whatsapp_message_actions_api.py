"""Authenticated same-origin edit/delete actions for existing WhatsApp rows."""
import asyncio
import uuid

from aiohttp import web

from tenant_linear_policy import TenantLinearError
from tenant_read_session import require_read_session
from tenant_request_origin import require_json_post
from tenant_whatsapp_message_edit import delete, save, status


def _session(request, member, origin):
    session = member(request)
    if not session:
        raise TenantLinearError('message_access_denied', 403)
    require_json_post(request, origin)
    return session


async def _body(request, session):
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError()
    if (str(data.get('expected_tenant_id')), str(data.get('expected_user_id'))) != (
            str(session['tenant_id']), str(session['telegram_id'])):
        raise TenantLinearError('message_workspace_changed', 409)
    return data


def _ids(data):
    lead = int(data['lead_id'])
    if not 0 < lead < 2**63:
        raise ValueError()
    return lead, str(uuid.UUID(str(data['message_id']))), str(uuid.UUID(str(data['request_id'])))


def handler(member, origin, logger, *, action='edit'):
    if action not in {'edit', 'delete'}:
        raise ValueError('invalid_message_action')

    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            if request.method == 'GET':
                session = require_read_session(request, member)
                data = request.query
                if (str(data.get('expected_tenant_id')), str(data.get('expected_user_id'))) != (
                        str(session['tenant_id']), str(session['telegram_id'])):
                    raise TenantLinearError('message_workspace_changed', 409)
                lead, message_id, request_id = _ids(data)
                result = await asyncio.to_thread(status, session, lead, message_id, request_id, action)
            else:
                session = _session(request, member, origin)
                data = await _body(request, session)
                fields = {'lead_id', 'message_id', 'request_id', 'version',
                          'expected_tenant_id', 'expected_user_id'}
                if action == 'edit':
                    fields.add('text')
                if set(data) != fields:
                    raise ValueError()
                lead, message_id, request_id = _ids(data)
                if action == 'edit':
                    result = await asyncio.to_thread(save, session, lead, message_id, request_id,
                                                     data['version'], data['text'])
                else:
                    result = await asyncio.to_thread(delete, session, lead, message_id, request_id,
                                                     data['version'])
            return web.json_response({'success': True, **result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError, KeyError, OverflowError):
            return web.json_response({'success': False, 'error': 'message_invalid'}, status=400, headers=headers)
        except Exception:
            logger.warning('WhatsApp message action status unavailable')
            return web.json_response({'success': False, 'error': 'message_unconfirmed'}, status=503, headers=headers)
    return handle
