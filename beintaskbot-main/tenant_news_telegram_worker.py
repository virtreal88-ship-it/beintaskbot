"""Read-only preflight then durable reservation and one send; unknown is never retried."""
import asyncio
from tenant_news_telegram_queue import candidate,claim,finish
from tenant_news_telegram_policy import verify,message


async def deliver(bot,logger):
    item=await asyncio.to_thread(candidate)
    if not item:return
    verified=None
    try:verified=await verify(bot,item['chat_id'],item['verified_by'])
    except Exception:logger.warning('Tenant news channel preflight unavailable; send blocked')
    claimed=await asyncio.to_thread(claim,item,verified)
    if not claimed:return
    status,message_id='unknown',None
    try:
        sent=await bot.send_message(chat_id=claimed['chat_id'],text=message(claimed['payload']),parse_mode='HTML',disable_web_page_preview=True)
        message_id=sent.message_id;status='sent'
    except Exception:logger.warning('Tenant news Telegram send result unknown; no automatic retry')
    await asyncio.to_thread(finish,claimed,status,message_id)
