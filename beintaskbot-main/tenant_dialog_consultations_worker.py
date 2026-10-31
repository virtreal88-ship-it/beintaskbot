"""Durable asynchronous private follow-ups; no pilot expiry or customer transport."""
import asyncio
import uuid
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
import tenant_dialog_consultations as flow


def pending(limit=10):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('saas_dialog_consultation_replies') AS ready")
            if not (cur.fetchone() or {}).get('ready'):return []
            cur.execute('''SELECT r.id::text,r.tenant_id::text,c.created_by FROM saas_dialog_consultation_replies r
              JOIN saas_internal_consultations c ON c.tenant_id=r.tenant_id AND c.id=r.consultation_id
              WHERE r.draft_state='pending' AND r.handled_at IS NULL AND c.created_by IS NOT NULL
                AND (r.draft_retry_after IS NULL OR r.draft_retry_after<=now())
              ORDER BY r.created_at LIMIT %s''',(limit,))
            return [dict(r) for r in cur.fetchall()]


def reconcile():
    """Recover correlation after webhook-before-receipt or a transient DB error.

    Only already stored inbound evidence in exact receipted destinations is read.
    The receipt and actual quote/CONSULT correlation are independently rechecked.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('saas_dialog_consultation_replies') AS ready")
            if not (cur.fetchone() or {}).get('ready'):return 0
            cur.execute('''SELECT DISTINCT d.tenant_id::text,d.channel_id::text,d.conversation_id,
              m.external_id,m.body,m.raw,m.author_name,COALESCE(m.happened_at,m.created_at) AS happened_at
              FROM saas_consultation_group_deliveries d JOIN saas_internal_consultations c
              ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id AND c.case_id IS NULL
              JOIN saas_connect_conversations v ON v.tenant_id=d.tenant_id AND v.connect_channel_id=d.channel_id AND v.conversation_id=d.conversation_id
              JOIN saas_crm_messages m ON m.tenant_id=d.tenant_id AND m.kommo_lead_id=v.lead_id AND m.channel='connect:'||d.channel_id::text
              WHERE d.state='sent' AND m.direction='incoming' AND m.message_type='text' AND m.created_at>=d.created_at
                AND (m.raw->>'quotedExternalMessageId'=d.provider_message_id OR m.body ILIKE '%%CONSULT:'||d.consultation_id::text||'%%')
                AND NOT EXISTS(SELECT 1 FROM saas_dialog_consultation_replies r WHERE r.tenant_id=d.tenant_id
                  AND r.channel_id=d.channel_id AND r.conversation_id=d.conversation_id AND r.message_id=COALESCE(m.raw->>'id',m.external_id))
              LIMIT 100''')
            count=0
            for row in cur.fetchall():
                raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
                if raw.get('ai_origin') is True or raw.get('aiOrigin') is True:continue
                count+=bool(flow.record_reply(cur,row['tenant_id'],row['channel_id'],row['conversation_id'],
                    str(raw.get('id') or row['external_id']),row['body'],raw.get('quotedExternalMessageId'),
                    row.get('author_name') or '',row['happened_at']))
        conn.commit()
        return count


async def deliver(logger):
    await asyncio.to_thread(reconcile)
    for item in await asyncio.to_thread(pending):
        try:
            # Normal Copilot revalidates membership, employee AI permission,
            # current Manual assignment, language, knowledge and company budget.
            # Its durable claim rejects concurrent/inflight/unknown requests.
            await asyncio.to_thread(flow.draft,{'tenant_id':item['tenant_id'],'telegram_id':item['created_by']},
                item['id'],{'request_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'dialog-consultation-draft:'+item['tenant_id']+':'+item['id']))})
        except TenantLinearError:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE saas_dialog_consultation_replies SET draft_retry_after=now()+interval '5 minutes' WHERE tenant_id=%s::uuid AND id=%s::uuid AND draft_state='pending'",(item['tenant_id'],item['id']))
                conn.commit()
            logger.info('Private consultation follow-up deferred by current access or assignment')
        except Exception:
            logger.warning('Private consultation follow-up needs review; no automatic retry of uncertain AI calls')
