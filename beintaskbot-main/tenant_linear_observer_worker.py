"""Bounded opt-in observer + generic alerts. No news publication transport."""
import asyncio
from tenant_linear_observer import sync_one
from tenant_linear_notice_outbox import expand_events, claim_delivery, finish_delivery
from tenant_linear_observer_cleanup import cleanup
from tenant_news_ai_worker import process_one as process_news_ai
from tenant_push_worker import send_push
from tenant_notice_transport import attempt

MESSAGE = 'Linear tapşırığının statusu dəyişib. Məlumatları şirkətinizin kabinetində yoxlayın.\n\nhttps://crm.pro.az/app?view=linear'


async def observe_and_notify(bot, private_key: str, claims: dict, logger) -> None:
    # A failing company must not prevent already queued notices for others.
    for operation in (cleanup, sync_one, expand_events):
        try:
            await asyncio.to_thread(operation)
        except Exception:
            logger.warning('Tenant Linear observer phase unavailable')
    try:
        await asyncio.to_thread(process_news_ai, logger)
    except Exception:
        logger.warning('Tenant news AI queue unavailable')
    for channel in ('telegram', 'push'):
        if channel == 'push' and not private_key:
            continue
        for _ in range(3):
            try:
                item = await asyncio.to_thread(claim_delivery, channel)
            except Exception:
                logger.warning('Tenant Linear notice channel unavailable channel=%s', channel)
                break
            if item is None:
                break
            if not item['send']:
                continue
            result = await attempt(item, channel, bot, text=lambda row:MESSAGE, parts=lambda text:[text],
                                   push_sender=send_push, private_key=private_key, claims=claims)
            if result.status != 'delivered':
                logger.warning('Tenant Linear notice result=%s channel=%s', result.status, channel)
            await asyncio.to_thread(finish_delivery, item, result.status, result.message_id)
