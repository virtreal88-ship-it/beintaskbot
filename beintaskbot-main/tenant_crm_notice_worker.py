"""Authorized full Telegram text and neutral push. No retry loop."""
import asyncio
from tenant_crm_notice_store import observe
from tenant_crm_notice_outbox import claim, finish, dispatch_guarded
from tenant_notice_transport import attempt, DeliveryResult
from tenant_notice_text import message_parts
from tenant_push_worker import send_push

DELIVERY_TIMEOUT = 35

LABELS = {'new_lead': 'Yeni sövdələşmə', 'incoming_message': 'Yeni mesaj',
          'task_assigned': 'Yeni tapşırıq', 'task_overdue': 'Gecikmiş tapşırıq'}


def message(item: dict) -> str:
    row = item['resource']
    deal = row.get('deal') or (row if item['event'] == 'new_lead' else {})
    text = str(row.get('text') or row.get('body') or '')
    text = '\n'.join(line for line in text.splitlines() if not line.startswith('[CRM:'))
    lines = [str(item.get('tenant_name') or 'Şirkət'), LABELS[item['event']]]
    if deal:
        lines += [str(deal.get('contact_name') or deal.get('name') or ''), str(deal.get('phone') or ''),
                  'Sövdələşmə: ' + str(deal.get('kommo_lead_id') or '')]
    if text: lines.append(text)
    if item['event'].startswith('task_'):
        lines += ['Tapşırıq: ' + str(row.get('kommo_task_id') or ''), 'Tarix: ' + str(row.get('due_at') or '')]
    lines.append('https://crm.pro.az/app?view=' + ('tasks' if item['event'].startswith('task_') else 'customers'))
    return '\n\n'.join(line for line in lines if line)


async def deliver(bot, private_key: str, claims: dict, logger) -> None:
    try:
        await asyncio.to_thread(observe)
    except Exception:
        logger.error('Tenant CRM notification observation unavailable')
    for channel in ('telegram', 'push'):
        if channel == 'push' and not private_key: continue
        for _ in range(10):
            item = await asyncio.to_thread(claim, channel)
            if item is None: break
            if not item['send']: continue
            loop = asyncio.get_running_loop()
            async def bounded_attempt():
                from tenant_scoped_access import scoped_access_enabled
                if not scoped_access_enabled() or item.get('event') != 'incoming_message':
                    return await attempt(item, channel, bot, text=message, parts=message_parts,
                        push_sender=send_push, private_key=private_key, claims=claims)
                try:
                    return await asyncio.wait_for(attempt(item, channel, bot, text=message, parts=message_parts,
                        push_sender=send_push, private_key=private_key, claims=claims),timeout=DELIVERY_TIMEOUT)
                except TimeoutError:
                    return DeliveryResult('unknown')
            def send_current():
                # Keep provider I/O on its owning event loop while the worker
                # thread retains the current grant locks until it finishes.
                return asyncio.run_coroutine_threadsafe(bounded_attempt(), loop).result()
            result = await asyncio.to_thread(dispatch_guarded, item, send_current)
            if result is None: continue
            if result.status != 'delivered':
                logger.warning('Tenant CRM notice result=%s channel=%s', result.status, channel)
            await asyncio.to_thread(finish, item, result.status, result.message_id)
