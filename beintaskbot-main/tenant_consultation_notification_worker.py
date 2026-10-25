"""Fail-closed Telegram delivery for opted-in AI developer consultations."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from tenant_platform import _connect
from tenant_internal_consultations import schema
from tenant_member_store import member
from tenant_notifications import notification_channels
from tenant_policy import TenantPolicy
from tenant_telegram_binding import binding_snapshot


def _hours() -> bool:
    now = datetime.now(ZoneInfo('Asia/Baku'))
    return 8 <= now.hour < 20


def _binding_version(value: object) -> str:
    """Canonicalize PostgreSQL/driver timestamp representations for compare."""
    if hasattr(value, 'isoformat'):
        value = value.isoformat()
    raw = str(value or '').strip().replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(raw).isoformat()
    except ValueError:
        return raw


def _current_delivery_permission(item: dict) -> bool:
    """Recheck mutable authorization immediately before network I/O."""
    if not _hours():
        return False
    profile = member(str(item['tenant_id']), int(item['recipient_id']))
    if not profile or profile.get('active') is False:
        return False
    if profile.get('role') not in {'owner', 'admin'} and 'support' not in (profile.get('permissions') or []):
        return False
    binding = binding_snapshot(tenant_id=str(item['tenant_id']), actor_id=int(item['recipient_id']))
    if not binding or int(binding['chat_id']) != int(item['chat_id']):
        return False
    if _binding_version(item.get('binding_verified_at')) != _binding_version(binding.get('verified_at')):
        return False
    return 'telegram' in notification_channels(TenantPolicy(profile), event='ai_developer_consultation', tenant_id=str(item['tenant_id']))


def claim_delivery():
    """Claim only an in-hours actionable consultation; leave after-hours pending."""
    if not _hours():
        return None
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_consultation_telegram_deliveries SET status='unknown',updated_at=now() WHERE status='sending' AND updated_at<now()-interval '5 minutes'")
            cur.execute('''SELECT d.tenant_id,d.consultation_id,d.recipient_id,d.chat_id,d.binding_verified_at,c.status
              FROM saas_consultation_telegram_deliveries d JOIN saas_internal_consultations c
              ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id WHERE d.status='pending'
                AND c.status IN ('waiting_specialist','queued_after_hours')
              ORDER BY d.created_at LIMIT 1 FOR UPDATE OF d,c SKIP LOCKED''')
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE saas_consultation_telegram_deliveries SET status='sending',updated_at=now() WHERE tenant_id=%s AND consultation_id=%s", (row['tenant_id'], row['consultation_id']))
                if row['status'] == 'queued_after_hours':
                    cur.execute("UPDATE saas_internal_consultations SET status='waiting_specialist' WHERE tenant_id=%s AND id=%s AND status='queued_after_hours'", (row['tenant_id'], row['consultation_id']))
        conn.commit()
    return dict(row) if row else None


def skip_delivery(item: dict) -> None:
    """Terminally cancel an invalid recipient; never turn it into a retry."""
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_consultation_telegram_deliveries SET status='skipped',updated_at=now()
              WHERE tenant_id=%s AND consultation_id=%s AND status='sending' ''', (item['tenant_id'], item['consultation_id']))
            cur.execute('''UPDATE saas_internal_consultations SET delivery_state='internal_inbox'
              WHERE tenant_id=%s AND id=%s AND delivery_state='telegram_pending' ''', (item['tenant_id'], item['consultation_id']))
        conn.commit()


def finish_delivery(item, status, message_id=None):
    if status not in {'delivered', 'unknown'}:
        raise ValueError('Invalid consultation delivery state')
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_consultation_telegram_deliveries SET status=%s,message_id=%s,updated_at=now()
              WHERE tenant_id=%s AND consultation_id=%s AND status='sending' ''', (status, message_id, item['tenant_id'], item['consultation_id']))
            cur.execute('''UPDATE saas_internal_consultations SET delivery_state=%s WHERE tenant_id=%s AND id=%s''',
                        ('telegram_delivered' if status == 'delivered' else 'telegram_unknown', item['tenant_id'], item['consultation_id']))
        conn.commit()


async def deliver_consultation_notifications(bot, logger):
    for _ in range(10):
        item = await asyncio.to_thread(claim_delivery)
        if not item:
            break
        if not await asyncio.to_thread(_current_delivery_permission, item):
            await asyncio.to_thread(skip_delivery, item)
            continue
        try:
            message = await bot.send_message(chat_id=item['chat_id'], text='AI developer consultation requires your review.\nhttps://crm.pro.az/app?view=support', disable_web_page_preview=True)
            await asyncio.to_thread(finish_delivery, item, 'delivered', getattr(message, 'message_id', None))
        except asyncio.CancelledError:
            raise
        except Exception:
            # The provider may have accepted the request before timing out;
            # do not retry and risk duplicate private notifications.
            logger.warning('Consultation Telegram delivery unknown: tenant=%s consultation=%s recipient=%s', item['tenant_id'], item['consultation_id'], item['recipient_id'])
            await asyncio.to_thread(finish_delivery, item, 'unknown')
