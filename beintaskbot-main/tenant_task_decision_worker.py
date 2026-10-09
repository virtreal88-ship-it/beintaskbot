"""Own approval results: full Telegram text, neutral push payload."""
import asyncio
from tenant_task_decision_outbox import expand_events, claim_delivery, finish_delivery
from tenant_notification_worker import message_parts
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
            status,message_id='delivered',None
            try:
                if channel=='telegram':
                    for text in message_parts(message(item)):
                        sent=await bot.send_message(chat_id=item['recipient_id'],text=text,parse_mode=None,disable_web_page_preview=True)
                        message_id=sent.message_id
                else:
                    await asyncio.to_thread(send_push,item,private_key,claims)
            except Exception as error:
                code=getattr(getattr(error,'response',None),'status_code',None)
                status='expired' if channel=='push' and code in {404,410} else 'unknown'
                logger.warning('Tenant task decision delivery result=%s channel=%s',status,channel)
            await asyncio.to_thread(finish_delivery,item,status,message_id)
