"""Generic hot-order alerts: no customer/task/company data in external payloads."""
import asyncio
from tenant_hot_order_outbox import expand_events, claim_delivery, finish_delivery
from tenant_push_worker import send_push

MESSAGE = 'Yeni isti sifariş var. Məlumatları şirkətinizin kabinetində yoxlayın.\n\nhttps://crm.pro.az/app?view=hot_orders'
MESSAGES = {
    'hot_order_completion_requested':'İsti sifarişin tamamlanması təsdiq gözləyir. Kabinetdə yoxlayın.\n\nhttps://crm.pro.az/app?view=approvals',
    'hot_order_completion_decided':'İsti sifarişin tamamlanması üzrə qərar verilib. Kabinetdə yoxlayın.\n\nhttps://crm.pro.az/app?view=hot_orders',
}


async def deliver_hot_order_notifications(bot, private_key: str, claims: dict, logger) -> None:
    await asyncio.to_thread(expand_events)
    for channel in ('telegram','push'):
        if channel=='push' and not private_key:
            continue
        for _ in range(10):
            item = await asyncio.to_thread(claim_delivery,channel)
            if item is None:
                break
            if not item['send']:
                continue
            status,message_id = 'delivered',None
            try:
                if channel=='telegram':
                    message = await bot.send_message(chat_id=item['recipient_id'],text=MESSAGES.get(item.get('event'),MESSAGE),
                                                     parse_mode=None,disable_web_page_preview=True)
                    message_id = message.message_id
                else:
                    await asyncio.to_thread(send_push,item,private_key,claims)
            except Exception as exc:
                code = getattr(getattr(exc,'response',None),'status_code',None)
                status = 'expired' if channel=='push' and code in {404,410} else 'unknown'
                logger.warning('Tenant hot-order notice result=%s channel=%s',status,channel)
            await asyncio.to_thread(finish_delivery,item,status,message_id)
