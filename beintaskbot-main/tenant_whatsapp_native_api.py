"""Opt-in private inbox delivery. Never starts a device or changes a source."""
import asyncio
import os
from aiohttp import web
from tenant_browser_calls_api import HEADERS, body
from tenant_linear_policy import TenantLinearError
import tenant_browser_calls
import tenant_whatsapp_native_inbox as inbox

SLOTS = asyncio.Semaphore(2)


async def _finish_thread(function, *args):
    """A disconnected HTTP caller cannot free a slot while its DB write runs.

    Cancelling to_thread does not stop the underlying synchronous transaction.
    Keep the permit until it actually finishes, including repeated cancellation;
    the sender will retry its persisted event if it did not receive a receipt.
    """
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    try:
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                cancelled = True
                if task.done():
                    # Retrieve any exception without logging the DB payload.
                    task.result()
                    break
    finally:
        if cancelled:
            raise asyncio.CancelledError
    return result


def handler(member, origin, logger, *, message_updates=None):
    async def handle(request):
        try:
            if os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX') != '1':
                raise TenantLinearError('native_inbox_disabled', 404)
            # This is an engine-to-Worker protocol, never a browser credential
            # shortcut. Existing source credentials remain the authority.
            if request.headers.get('Origin') or request.headers.get('Cookie'):
                raise TenantLinearError('gateway_access_denied', 403)
            if SLOTS.locked():
                raise TenantLinearError('native_inbox_busy', 429)
            async with SLOTS:
                authorization = request.headers.get('Authorization', '')
                cfg = await _finish_thread(tenant_browser_calls.gateway_auth, authorization)
                if cfg.get('managed') is not True:
                    raise TenantLinearError('native_source_denied', 403)
                if request.content_type != 'application/json' or request.headers.get('Content-Encoding'):
                    raise TenantLinearError('native_delivery_type_invalid', 400)
                raw = await asyncio.wait_for(body(request, inbox.MAX_DELIVERY), 10)
                # store_delivery repeats authentication and locks/rechecks the
                # source in its commit transaction, including credential revoke.
                receipt = await _finish_thread(inbox.store_delivery, authorization, raw)
                if os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_PROJECTION') == '1':
                    from tenant_whatsapp_native_consumer import project_stored
                    projected = await _finish_thread(project_stored, receipt)
                    if projected and message_updates:
                        # Only an authenticated, committed projection can wake a
                        # browser. The browser revision read rechecks visibility.
                        message_updates.publish({'tenant_id': receipt['scope']['tenant']}, '')
            return web.json_response(receipt, headers=HEADERS)
        except TenantLinearError as error:
            return web.json_response({'error': str(error)}, status=error.status, headers=HEADERS)
        except asyncio.TimeoutError:
            return web.json_response({'error': 'native_delivery_timeout'}, status=408, headers=HEADERS)
        except Exception:
            # No exception string, payload, identifiers, SQL or credentials.
            logger.warning('native_whatsapp_delivery_failed stage=store code=internal')
            return web.json_response({'error': 'native_delivery_unconfirmed'}, status=503, headers=HEADERS)
    return handle
