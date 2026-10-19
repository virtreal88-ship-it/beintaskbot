"""Authenticated HTTP boundary for tenant-native CRM contacts."""
import asyncio
from datetime import date, datetime
from uuid import UUID
from aiohttp import web
from tenant_crm_native_contacts import create_contact, get_contact, list_contacts, update_contact, archive_contact
from tenant_request_origin import require_json_post
from tenant_linear_policy import TenantLinearError


def _jsonable(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def handler(member_from_request, origin, logger):
    async def handle(request):
        profile = member_from_request(request)
        if not profile or profile.get('role') not in ('owner', 'admin', 'manager'):
            return web.json_response({'success': False, 'error': 'forbidden'}, status=403,
                                     headers={'Cache-Control': 'no-store'})
        tenant_id = str(profile['tenant_id'])
        contact_id = request.match_info.get('id')
        if contact_id:
            try:
                UUID(contact_id)
            except (TypeError, ValueError):
                return web.json_response({'success': False, 'error': 'invalid_id'}, status=400)
        try:
            if request.method == 'GET':
                if contact_id:
                    result = await asyncio.to_thread(get_contact, tenant_id, contact_id)
                else:
                    result = await asyncio.to_thread(list_contacts, tenant_id,
                        request.query.get('search', ''), 50)
                if contact_id and result is None:
                    return web.json_response({'success': False, 'error': 'not_found'}, status=404)
            else:
                require_json_post(request, origin)
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError('invalid body')
                if request.method == 'POST' and not contact_id:
                    if set(body) - {'name', 'phone', 'email', 'id'}:
                        raise ValueError('unknown fields')
                    result = await asyncio.to_thread(create_contact, tenant_id,
                        body.get('name'), body.get('phone', ''), body.get('email', ''),
                        contact_id=body.get('id'))
                elif request.method == 'POST' and contact_id and body.get('action') == 'update':
                    if set(body) - {'action', 'name', 'phone', 'email'} or 'name' not in body:
                        raise ValueError('invalid fields')
                    result = await asyncio.to_thread(update_contact, tenant_id, contact_id,
                        body['name'], body.get('phone', ''), body.get('email', ''))
                elif request.method == 'POST' and contact_id and body.get('action') == 'archive':
                    if body != {'action': 'archive'}:
                        raise ValueError('invalid archive action')
                    result = await asyncio.to_thread(archive_contact, tenant_id, contact_id)
                else:
                    return web.json_response({'success': False, 'error': 'method_not_allowed'}, status=405)
                if result is None or result is False:
                    return web.json_response({'success': False, 'error': 'not_found'}, status=404)
            return web.json_response({'success': True, 'data': _jsonable(result)},
                                     headers={'Cache-Control': 'no-store'})
        except TenantLinearError:
            return web.json_response({'success': False, 'error': 'invalid_origin'}, status=403,
                                     headers={'Cache-Control': 'no-store'})
        except ValueError:
            return web.json_response({'success': False, 'error': 'invalid_contact'}, status=400,
                                     headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.exception('Native CRM contact operation failed')
            return web.json_response({'success': False, 'error': 'contact_operation_failed'}, status=503,
                                     headers={'Cache-Control': 'no-store'})
    return handle
