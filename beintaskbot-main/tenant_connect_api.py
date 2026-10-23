"""HTTP boundary for tenant-owned Connect channel configuration and webhooks."""
from __future__ import annotations

import asyncio
import json
from aiohttp import web

from tenant_connect_transport import (ConnectWebhookError, channel_status, channels, configure, diagnostics,
                                      deactivate_stale_webhooks, disconnect, disconnect_channel, ingest, status)
from tenant_platform import TenantPlatformError
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post


def _policy_error_response(web_module, exc: TenantLinearError) -> web.Response:
    return web_module.json_response(
        {'success': False, 'error': str(exc)}, status=exc.status,
        headers={'Cache-Control': 'no-store'},
    )


def settings_handler(member_from_request, origin: str, logger):
    async def handle(request: web.Request) -> web.Response:
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantPlatformError('İcazə yoxdur.')
            if request.method == 'GET':
                result = await asyncio.to_thread(status, profile)
            else:
                require_json_post(request, origin)
                data = await request.json()
                if not isinstance(data, dict):
                    raise TenantPlatformError('Sorğu düzgün deyil.')
                if data.get('action') == 'disconnect':
                    result = await asyncio.to_thread(disconnect, profile)
                elif data.get('action') == 'disconnect_channel':
                    result = await asyncio.to_thread(disconnect_channel, profile, str(data.get('channel_id') or ''))
                elif data.get('action') == 'retire_channel':
                    if set(data)!={'action','channel_id','confirm'}:
                        raise TenantLinearError('channel_retirement_request_invalid',400)
                    from tenant_connect_retirement import retire
                    result=await asyncio.to_thread(retire,profile,str(data.get('channel_id') or ''),confirmed=data['confirm'] is True)
                elif data.get('action') in {'channel_status', 'reconnect_channel'}:
                    result = await asyncio.to_thread(
                        channel_status, profile, str(data.get('channel_id') or ''),
                        reconnect=data.get('action') == 'reconnect_channel',
                        callback_url=origin.rstrip('/') + '/api/platform/connect/webhook',
                    )
                elif data.get('action') == 'channels':
                    result = {'channels': await asyncio.to_thread(channels, profile, str(data.get('api_key') or ''))}
                else:
                    result = await asyncio.to_thread(configure, profile, data, origin.rstrip('/') + '/api/platform/connect/webhook')
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TenantPlatformError as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=403, headers={'Cache-Control': 'no-store'})
        except TenantLinearError as exc:
            # The shared origin guard raises this policy exception. Treating it
            # as an unknown failure hid the real reason behind a misleading 503.
            return _policy_error_response(web, exc)
        except Exception:
            logger.exception('Connect settings request failed')
            return web.json_response({'success': False, 'error': 'Connect ayarları saxlanmadı.'}, status=503, headers={'Cache-Control': 'no-store'})
    return handle


def diagnostics_handler(member_from_request, logger, is_platform_admin=lambda _profile: False):
    async def handle(request: web.Request) -> web.Response:
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantPlatformError('İcazə yoxdur.')
            result = await asyncio.to_thread(diagnostics, profile)
            result['canDeactivateStaleWebhooks'] = bool(is_platform_admin(profile))
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TenantPlatformError as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=403, headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.exception('Connect diagnostics request failed')
            return web.json_response({'success': False, 'error': 'Connect diaqnostikası oxunmadı.'}, status=503,
                                     headers={'Cache-Control': 'no-store'})
    return handle



def deactivate_webhooks_handler(member_from_request, origin: str, logger, is_platform_admin=lambda _profile: False):
    async def handle(request: web.Request) -> web.Response:
        try:
            profile = member_from_request(request)
            if not profile:
                raise TenantPlatformError('İcazə yoxdur.')
            if not is_platform_admin(profile):
                raise TenantPlatformError('Bu əməliyyat yalnız platform administratoru üçündür.')
            require_json_post(request, origin)
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {'webhook_ids', 'confirm'}:
                raise TenantPlatformError('Webhook deaktivasiya sorğusu düzgün deyil.')
            ids = payload['webhook_ids']
            if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
                raise TenantPlatformError('Webhook ID siyahısı düzgün deyil.')
            result = await asyncio.to_thread(deactivate_stale_webhooks, profile, ids,
                                             confirmed=payload['confirm'] is True, platform_admin=True)
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TenantLinearError as exc:
            return _policy_error_response(web, exc)
        except TenantPlatformError as exc:
            return web.json_response({'success': False, 'error': str(exc)}, status=403,
                                     headers={'Cache-Control': 'no-store'})
        except Exception:
            logger.exception('Connect webhook deactivation failed')
            return web.json_response({'success': False, 'error': 'Webhook deaktivasiya baş tutmadı.'},
                                     status=503, headers={'Cache-Control': 'no-store'})
    return handle


def webhook_handler(logger):
    async def handle(request: web.Request) -> web.Response:
        raw = await request.read()
        signature = str(request.headers.get('x-connect-signature-256') or '')
        try:
            if not signature:
                raise ConnectWebhookError('missing_signature_header', 'Connect webhook imzası yoxdur.', 401)
            try:
                data = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ConnectWebhookError('invalid_json', 'Connect webhook formatı düzgün deyil.', 400) from None
            if not isinstance(data, dict):
                raise ConnectWebhookError('invalid_event', 'Connect webhook formatı düzgün deyil.', 400)
            accepted = await asyncio.to_thread(ingest, raw, signature, data)
            return web.json_response({'accepted': accepted})
        except ConnectWebhookError as exc:
            # Never log the body, signature, API key, or tenant identifiers.
            # A short reason code lets operators distinguish auth failures
            # from malformed/spoofed requests and internal scope conflicts.
            logger.warning('Connect webhook rejected code=%s status=%s', exc.code, exc.status)
            return web.json_response({'error': exc.code}, status=exc.status)
        except TenantPlatformError:
            logger.exception('Connect webhook platform failure')
            return web.json_response({'error': 'webhook_failed'}, status=500)
        except Exception:
            logger.exception('Connect webhook failed')
            return web.json_response({'error': 'webhook_failed'}, status=500)
    return handle
