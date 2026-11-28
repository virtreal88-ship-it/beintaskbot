"""Bounded, coalesced refresh; fresh authorization before and after provider IO."""
import asyncio
from time import perf_counter
from aiohttp import web
from worker_crm_capacity import dialog_io, session_io
from tenant_chat_history_store import page
from tenant_read_session import require_read_session
from tenant_linear_policy import TenantLinearError


def handler(context):
    flights = {}

    async def handle(request, lead_id):
        headers = {'Cache-Control': 'no-store'}
        try:
            profile = await session_io.run(require_read_session, request, context.member)
            size = max(1, min(50, int(request.query.get('limit', '40'))))
            start = perf_counter()
            await dialog_io.run(page, profile, lead_id, limit=1)
            deal = await dialog_io.run(context.get_deal, tenant_id=profile['tenant_id'], kommo_lead_id=lead_id)
            authorized = perf_counter()
            warning = ''
            if context.policy(profile).can_read_kommo_chat(deal) and (deal.get('raw') or {}).get('source') != 'connect':
                key = (str(profile['tenant_id']), str(profile['telegram_id']), lead_id)
                task = flights.get(key)
                if task is None:
                    task = asyncio.create_task(context.sync_chat(profile, lead_id, recent=True))
                    flights[key] = task
                    def finished(done, key=key):
                        if flights.get(key) is done:
                            flights.pop(key, None)
                        if not done.cancelled():
                            done.exception()  # consume if all HTTP waiters disconnected
                    task.add_done_callback(finished)
                try:
                    await asyncio.shield(task)
                except Exception:
                    # No provider error bodies, message content or credentials.
                    context.logger.warning('Bounded CRM chat refresh unavailable')
                    warning = 'Yeniləmə alınmadı. Saxlanmış tarixçə köhnəlmiş ola bilər.'
            refreshed = perf_counter()
            # Recheck session identity AND current DB membership/channel grants.
            profile = await session_io.run(require_read_session, request, context.member)
            result = await dialog_io.run(page, profile, lead_id, limit=size)
            finished = perf_counter()
            headers['Server-Timing'] = f'authorize;dur={(authorized-start)*1000:.1f}, provider_refresh;dur={(refreshed-authorized)*1000:.1f}, cache;dur={(finished-refreshed)*1000:.1f}'
            return web.json_response({'success': True, **result, 'stale': bool(warning), 'warning': warning}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except (ValueError, TypeError):
            return web.json_response({'success': False, 'error': 'Tarixçə səhifəsi düzgün deyil.'}, status=400, headers=headers)
        except Exception:
            context.logger.warning('Bounded CRM chat read unavailable')
            return web.json_response({'success': False, 'error': 'Tarixçə yüklənmədi.'}, status=503, headers=headers)
    return handle
