"""Full Telegram approval notice and neutral push; never replay unknown sends."""
import asyncio
from tenant_deal_notice_outbox import expand, claim, finish
from tenant_notice_transport import attempt
from tenant_notice_text import message_parts
from tenant_push_worker import send_push


def message(item):
    deal = item.get('deal') or {}
    state = item['state']
    return '\n\n'.join([
        'Sövdələşmə tamamlanması üçün təsdiq', 'Şirkət: ' + str(item.get('tenant_name') or ''),
        'Göndərən: ' + str(item.get('creator_name') or item['actor_id']),
        'Müştəri: ' + str(deal.get('contact_name') or deal.get('name') or ''),
        'Sövdələşmə #' + str(item['lead_id']),
        'Nəticə: ' + str(state.get('result_text') or ''),
        'Sorğu: ' + str(item['request_id']), 'https://crm.pro.az/app?view=approvals',
        'Təsdiqləmək üçün göstərilən şirkətin kabinetini açın.'])


async def deliver(bot, private_key, claims, logger):
    await asyncio.to_thread(expand)
    for channel in ('telegram', 'push'):
        if channel == 'push' and not private_key:
            continue
        for _ in range(10):
            item = await asyncio.to_thread(claim, channel)
            if item is None:
                break
            if not item['send']:
                continue
            result = await attempt(item, channel, bot, text=message, parts=message_parts,
                                   push_sender=send_push, private_key=private_key, claims=claims)
            await asyncio.to_thread(finish, item, result.status, result.message_id)
            if result.status != 'delivered':
                logger.warning('Tenant deal approval notice result=%s channel=%s', result.status, channel)
