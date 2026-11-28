"""HTTP boundary for tenant-owned Connect channel configuration and webhooks."""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import threading
from aiohttp import web

from tenant_connect_transport import (ConnectWebhookError, channel_status, channels, configure, diagnostics,
                                      deactivate_stale_webhooks, disconnect, disconnect_channel, ingest, status)
from worker_crm_capacity import WorkPool, CapacityUnavailable
from tenant_platform import TenantPlatformError
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post


# A provider backlog must not occupy asyncio's shared executor: login reads,
# CRM lists and aiohttp FileResponse all need that same pool. Keep ingestion
# on a small dedicated pool without acknowledging before its transaction ends.
_webhook_io = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix='connect-webhook',
)


_webhook_slots = threading.BoundedSemaphore(12)
_webhook_deadline = 20
_settings_io = WorkPool('crm-connect', 3, 6, 35, 15000)


async def _run_webhook_ingest(raw, signature, data):
    if not _webhook_slots.acquire(blocking=False):
        raise ConnectWebhookError('webhook_busy', 'Connect müvəqqəti məşğuldur.', 503)
    try:
        work = _webhook_io.submit(contextvars.copy_context().run, ingest, raw, signature, data)
    except BaseException:
        _webhook_slots.release()
        raise
    work.add_done_callback(lambda _: _webhook_slots.release())
    try:
        return await asyncio.wait_for(asyncio.wrap_future(work), _webhook_deadline)
    except TimeoutError:
        raise ConnectWebhookError('webhook_unconfirmed', 'Qəbul təsdiqlənmədi.', 503) from None


def _policy_error_response(web_module, exc: TenantLinearError) -> web.Response:
    return web_module.json_response(
        {'success': False, 'error': str(exc)}, status=exc.status,
        headers={'Cache-Control': 'no-store'},
    )


def settings_handler(member_from_request, origin: str, logger):
    async def handle(request: web.Request) -> web.Response:
        try:
            profile = await _settings_io.run(member_from_request, request)
            if not profile:
                raise TenantPlatformError('İcazə yoxdur.')
            if request.method == 'GET':
                result = await _settings_io.run(status, profile)
            else:
                require_json_post(request, origin)
                data = await request.json()
                if not isinstance(data, dict):
                    raise TenantPlatformError('Sorğu düzgün deyil.')
                if data.get('action') in {'pairing_start','pairing_status','pairing_refresh','pairing_complete'}:
                    from tenant_connect_pairing import pairing
                    result = await _settings_io.run(pairing, profile, data, origin.rstrip('/') + '/api/platform/connect/webhook')
                elif data.get('action') == 'disconnect':
                    result = await _settings_io.run(disconnect, profile, authorization=data)
                elif data.get('action') == 'disconnect_channel':
                    result = await _settings_io.run(disconnect_channel, profile, str(data.get('channel_id') or ''), authorization=data)
                elif data.get('action') == 'retire_channel':
                    if set(data)!={'action','channel_id','confirm'}:
                        raise TenantLinearError('channel_retirement_request_invalid',400)
                    from tenant_connect_retirement import retire
                    result=await _settings_io.run(retire,profile,str(data.get('channel_id') or ''),confirmed=data['confirm'] is True)
                elif data.get('action') == 'direct_identity_preview':
                    if set(data)-{'action','channel_id'}: raise TenantLinearError('direct_merge_request_invalid',400)
                    from tenant_connect_direct_reconcile import preview
                    result=await _settings_io.run(preview,profile,str(data.get('channel_id') or ''))
                elif data.get('action') == 'direct_identity_reconcile':
                    from tenant_connect_direct_reconcile import apply
                    result=await _settings_io.run(apply,profile,{key:value for key,value in data.items() if key!='action'})
                elif data.get('action') in {'channel_status', 'reconnect_channel'}:
                    result = await _settings_io.run(
                        channel_status, profile, str(data.get('channel_id') or ''),
                        reconnect=data.get('action') == 'reconnect_channel',
                        callback_url=origin.rstrip('/') + '/api/platform/connect/webhook', authorization=data,
                    )
                elif data.get('action') == 'channels':
                    result = {'channels': await _settings_io.run(channels, profile, str(data.get('api_key') or ''), authorization=data)}
                else:
                    result = await _settings_io.run(configure, profile, data, origin.rstrip('/') + '/api/platform/connect/webhook')
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except CapacityUnavailable:
            return web.json_response({'success':False,'error':'Connect cavabı təsdiqlənmədi.'},
                                     status=503, headers={'Cache-Control':'no-store'})
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
            profile = await _settings_io.run(member_from_request, request)
            if not profile:
                raise TenantPlatformError('İcazə yoxdur.')
            result = await _settings_io.run(diagnostics, profile)
            result['canDeactivateStaleWebhooks'] = bool(is_platform_admin(profile))
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except CapacityUnavailable:
            return web.json_response({'success':False,'error':'Connect cavabı təsdiqlənmədi.'},
                                     status=503, headers={'Cache-Control':'no-store'})
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
            profile = await _settings_io.run(member_from_request, request)
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
            result = await _settings_io.run(deactivate_stale_webhooks, profile, ids,
                                             confirmed=payload['confirm'] is True, platform_admin=True)
            return web.json_response({'success': True, **result}, headers={'Cache-Control': 'no-store'})
        except TenantLinearError as exc:
            return _policy_error_response(web, exc)
        except CapacityUnavailable:
            return web.json_response({'success':False,'error':'Connect cavabı təsdiqlənmədi.'},
                                     status=503, headers={'Cache-Control':'no-store'})
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
            accepted = await _run_webhook_ingest(raw, signature, data)
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
