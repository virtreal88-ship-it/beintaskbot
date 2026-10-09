"""Generic hot-order alerts: no customer/task/company data in external payloads."""
import asyncio
from tenant_hot_order_outbox import expand_events, claim_delivery, finish_delivery
from tenant_push_worker import send_push
from tenant_notice_transport import attempt

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
            result=await attempt(item,channel,bot,text=lambda row:MESSAGES.get(row.get('event'),MESSAGE),
                                 parts=lambda text:[text],push_sender=send_push,private_key=private_key,claims=claims)
            if result.status!='delivered':
                logger.warning('Tenant hot-order notice result=%s channel=%s',result.status,channel)
            await asyncio.to_thread(finish_delivery,item,result.status,result.message_id)
