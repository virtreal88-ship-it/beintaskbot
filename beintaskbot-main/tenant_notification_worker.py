"""Telegram transport for new SaaS task approval requests only."""
import asyncio
from tenant_notification_outbox import expand_events, claim_delivery, finish_delivery
from tenant_notice_text import message_parts
from tenant_notice_transport import attempt


def approval_message(item: dict) -> str:
    completing = item['event'] == 'task_completion_requested'
    task = item['state'].get('completion_input' if completing else 'task_input') or {}
    lines = ['Tapşırıq tamamlanması üçün təsdiq' if completing else 'Yeni tapşırıq üçün təsdiq',
             'Şirkət: ' + str(item.get('tenant_name') or ''),
             'Göndərən: ' + str(item.get('creator_name') or item['actor_id']),
             'Tapşırıq: ' + str(task.get('text') or '')]
    if completing:
        lines.append('Nəticə: ' + str(task.get('result_text') or ''))
        lines.append('Tapşırıq #' + str(task.get('task_id') or ''))
    elif task.get('due_at'):
        lines.append('Son tarix: ' + str(task['due_at']))
    if task.get('lead_id'):
        lines.append('Sövdələşmə #' + str(task['lead_id']))
    lines += ['Sorğu: ' + str(item['request_id']),
              'https://crm.pro.az/app', 'Təsdiqləmək üçün yuxarıda göstərilən şirkətin kabinetini açın.']
    return '\n\n'.join(lines)


async def deliver_approval_notifications(bot, logger) -> None:
    await asyncio.to_thread(expand_events)
    for _ in range(10):
        item = await asyncio.to_thread(claim_delivery)
        if item is None:
            break
        if not item['send']:
            continue
        result = await attempt(item, 'telegram', bot, text=approval_message, parts=message_parts)
        if result.status != 'delivered':
            # Never log private task/result text or Telegram error bodies.
            logger.warning('Tenant approval notice result unknown: tenant=%s request=%s recipient=%s',
                           item['tenant_id'], item['request_id'], item['recipient_id'])
            await asyncio.to_thread(finish_delivery, item, status='unknown')
        else:
            await asyncio.to_thread(finish_delivery, item, status='delivered', message_id=result.message_id)
