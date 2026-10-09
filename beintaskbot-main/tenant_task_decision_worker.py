"""Own approval results: full Telegram text, neutral push payload."""
import asyncio
from tenant_task_decision_outbox import expand_events, claim_delivery, finish_delivery
from tenant_notice_text import message_parts
from tenant_notice_transport import attempt
from tenant_push_worker import send_push


def message(item: dict) -> str:
    state=item['state']; task=state.get('completion_input') or state.get('task_input') or {}
    result=state.get('result') or {}
    lines=['Tapşırıq təsdiqinin nəticəsi', 'Şirkət: '+str(item.get('tenant_name') or ''),
           'Qərar: '+('Təsdiqləndi' if item['outcome']=='approved' else 'Rədd edildi'),
           'Əməliyyat: '+('Tamamlama' if state.get('completion_input') else 'Yaradılma'),
           'Tapşırıq: '+str(task.get('text') or '')]
    if task.get('result_text'): lines.append('Nəticə: '+str(task['result_text']))
    if result.get('task_id') or task.get('task_id'): lines.append('Tapşırıq #'+str(result.get('task_id') or task['task_id']))
    if result.get('lead_id') or task.get('lead_id'): lines.append('Sövdələşmə #'+str(result.get('lead_id') or task['lead_id']))
    lines += ['Sorğu: '+str(item['request_id']), 'https://crm.pro.az/app?view=tasks',
              'Yuxarıda göstərilən şirkətin kabinetini açın.']
    return '\n\n'.join(lines)


async def deliver(bot, private_key: str, claims: dict, logger) -> None:
    await asyncio.to_thread(expand_events)
    for channel in ('telegram','push'):
        if channel=='push' and not private_key: continue
        for _ in range(10):
            item=await asyncio.to_thread(claim_delivery,channel)
            if item is None: break
            if not item['send']: continue
            result=await attempt(item,channel,bot,text=message,parts=message_parts,
                                 push_sender=send_push,private_key=private_key,claims=claims)
            if result.status!='delivered':
                logger.warning('Tenant task decision delivery result=%s channel=%s',result.status,channel)
            await asyncio.to_thread(finish_delivery,item,result.status,result.message_id)
