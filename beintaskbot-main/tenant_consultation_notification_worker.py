"""Telegram delivery for explicitly opted-in AI developer consultations."""
import asyncio
from tenant_platform import _connect
from tenant_internal_consultations import schema


def claim_delivery():
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_consultation_telegram_deliveries SET status='unknown',updated_at=now() WHERE status='sending' AND updated_at<now()-interval '5 minutes'")
            cur.execute('''SELECT d.tenant_id,d.consultation_id,d.recipient_id,d.chat_id,c.status
              FROM saas_consultation_telegram_deliveries d JOIN saas_internal_consultations c
              ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id WHERE d.status='pending'
              ORDER BY d.created_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED''')
            row=cur.fetchone()
            if row: cur.execute("UPDATE saas_consultation_telegram_deliveries SET status='sending',updated_at=now() WHERE tenant_id=%s AND consultation_id=%s",(row['tenant_id'],row['consultation_id']))
        conn.commit()
    return dict(row) if row else None


def finish_delivery(item,status,message_id=None):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_consultation_telegram_deliveries SET status=%s,message_id=%s,updated_at=now()
              WHERE tenant_id=%s AND consultation_id=%s AND status='sending' ''',(status,message_id,item['tenant_id'],item['consultation_id']))
            cur.execute('''UPDATE saas_internal_consultations SET delivery_state=%s WHERE tenant_id=%s AND id=%s''',
                        ('telegram_delivered' if status=='delivered' else 'telegram_unknown',item['tenant_id'],item['consultation_id']))
        conn.commit()


async def deliver_consultation_notifications(bot,logger):
    for _ in range(10):
        item=await asyncio.to_thread(claim_delivery)
        if not item: break
        try:
            message=await bot.send_message(chat_id=item['chat_id'],text='AI developer consultation requires your review.\nhttps://crm.pro.az/app?view=support',disable_web_page_preview=True)
            await asyncio.to_thread(finish_delivery,item,'delivered',getattr(message,'message_id',None))
        except Exception:
            logger.warning('Consultation Telegram delivery unknown: tenant=%s consultation=%s recipient=%s',item['tenant_id'],item['consultation_id'],item['recipient_id'])
            await asyncio.to_thread(finish_delivery,item,'unknown')
