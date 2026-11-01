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
              WHERE r.draft_state='pending' AND r.handled_at IS NULL AND c.created_by IS NOT NULL AND c.case_id IS NULL
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
            cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='saas_dialog_consultation_replies' AND column_name='source_external_id') AS ready")
            if not (cur.fetchone() or {}).get('ready'):return 0
            # A crashed provider attempt cannot remain an optimistic spinner or
            # become eligible for another POST. A later signed echo can recover it.
            cur.execute("UPDATE saas_consultation_group_deliveries SET state='unknown',updated_at=now() WHERE state='sending' AND updated_at<now()-interval '5 minutes'")
            # Recover pending/unknown sends only from server-marked signed
            # outbound evidence. Never POST again and never infer from body.
            cur.execute('''SELECT DISTINCT d.tenant_id::text,d.channel_id::text,d.conversation_id,m.raw,m.body AS observed_body
              FROM saas_consultation_group_deliveries d JOIN saas_internal_consultations c
                ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id
              JOIN saas_connect_conversations v ON v.tenant_id=d.tenant_id AND v.connect_channel_id=d.channel_id AND v.conversation_id=d.conversation_id
              JOIN saas_crm_messages m ON m.tenant_id=d.tenant_id AND m.kommo_lead_id=v.lead_id AND m.channel='connect:'||d.channel_id::text
              WHERE d.state IN ('sending','pending','unknown') AND m.direction='outgoing'
                AND m.raw->>'_crm_signed_outbound_receipt'='true'
                AND (m.raw->>'id'=d.connect_message_id OR lower(m.raw->>'originClientMessageId')=d.request_id::text OR lower(m.raw->>'clientMessageId')=d.request_id::text)
              LIMIT 100''')
            for row in cur.fetchall():
                flow.observe_outbound_receipt(cur,row['tenant_id'],row['channel_id'],row['conversation_id'],{**row['raw'],'text':row['raw'].get('text') or row['observed_body']})
            # Release delivery locks before derived replies lock consultations.
            # Dispatch locks consultation -> delivery; never invert that order.
            conn.commit()
            cur.execute('''SELECT DISTINCT d.tenant_id::text,d.channel_id::text,d.conversation_id,
              m.external_id,m.body,m.message_type,m.raw,m.author_name,COALESCE(m.happened_at,m.created_at) AS happened_at
              FROM saas_consultation_group_deliveries d JOIN saas_internal_consultations c
              ON c.tenant_id=d.tenant_id AND c.id=d.consultation_id
              JOIN saas_connect_conversations v ON v.tenant_id=d.tenant_id AND v.connect_channel_id=d.channel_id AND v.conversation_id=d.conversation_id
              JOIN saas_crm_messages m ON m.tenant_id=d.tenant_id AND m.kommo_lead_id=v.lead_id AND m.channel='connect:'||d.channel_id::text
              WHERE d.state='sent' AND m.direction='incoming' AND m.message_type IN ('text','image','video','audio','voice','document','ptt','voice_message','photo','file') AND m.created_at>=d.created_at
                AND (m.raw->>'quotedExternalMessageId'=d.provider_message_id OR m.body ILIKE '%%CONSULT:'||d.consultation_id::text||'%%' OR m.body ILIKE '%%CASE:'||d.consultation_id::text||'%%'
                  OR m.raw->>'caption' ILIKE '%%CASE:'||d.consultation_id::text||'%%' OR m.raw->>'caption' ILIKE '%%CONSULT:'||d.consultation_id::text||'%%')
                AND NOT EXISTS(SELECT 1 FROM saas_dialog_consultation_replies r WHERE r.tenant_id=d.tenant_id
                  AND r.channel_id=d.channel_id AND r.conversation_id=d.conversation_id AND (r.message_id=CASE WHEN m.external_id ~ '^connect:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN substring(m.external_id from 9)
                    WHEN jsonb_typeof(m.raw->'id')='string' AND m.raw->>'id'<>'' THEN m.raw->>'id'
                    WHEN m.external_id LIKE 'connect:%%' THEN substring(m.external_id from 9) ELSE m.external_id END
                    OR r.message_id=m.external_id OR r.source_external_id=m.external_id
                    OR (m.external_id LIKE 'connect:%%' AND r.message_id=substring(m.external_id from 9))))
              LIMIT 100''')
            count=0
            for row in cur.fetchall():
                raw=row.get('raw') if isinstance(row.get('raw'),dict) else {}
                if raw.get('ai_origin') is True or raw.get('aiOrigin') is True:continue
                count+=bool(flow.record_reply(cur,row['tenant_id'],row['channel_id'],row['conversation_id'],
                    flow.message_identity(row['external_id'],raw),row['body'] or str(raw.get('caption') or ''),raw.get('quotedExternalMessageId'),
                    row.get('author_name') or '',row['happened_at'],message_type=row['message_type'],source_external_id=row['external_id']))
        # Historical CASE replies already linked by the previous handler remain
        # visible even if their original quoted webhook metadata was not retained.
        with conn.cursor() as cur:
            cur.execute('''SELECT g.*,COALESCE(m.author_name,'') AS author,
                COALESCE(m.happened_at,m.created_at,g.created_at) AS happened_at,m.message_identity,m.external_id AS source_external_id,m.message_type AS original_type
              FROM saas_consultation_group_replies g
              JOIN saas_internal_consultations c ON c.tenant_id=g.tenant_id AND c.id=g.consultation_id
              JOIN saas_connect_conversations v ON v.tenant_id=g.tenant_id AND v.connect_channel_id=g.channel_id AND v.conversation_id=g.conversation_id
              LEFT JOIN LATERAL (SELECT m0.author_name,m0.happened_at,m0.created_at,m0.external_id,m0.message_type,
                    CASE WHEN m0.external_id ~ '^connect:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN substring(m0.external_id from 9)
                    WHEN jsonb_typeof(m0.raw->'id')='string' AND m0.raw->>'id'<>'' THEN m0.raw->>'id'
                      WHEN m0.external_id LIKE 'connect:%%' THEN substring(m0.external_id from 9) ELSE m0.external_id END AS message_identity
                  FROM saas_crm_messages m0
                WHERE m0.tenant_id=g.tenant_id AND m0.kommo_lead_id=v.lead_id AND m0.channel='connect:'||g.channel_id::text
                  AND (m0.external_id=g.message_id OR m0.external_id='connect:'||g.message_id OR m0.raw->>'id'=g.message_id)
                ORDER BY m0.created_at LIMIT 1) m ON TRUE
              WHERE EXISTS(SELECT 1 FROM saas_consultation_group_deliveries d
                WHERE d.tenant_id=g.tenant_id AND d.consultation_id=g.consultation_id AND d.channel_id=g.channel_id
                  AND d.conversation_id=g.conversation_id AND d.state='sent')
                AND NOT EXISTS(SELECT 1 FROM saas_dialog_consultation_replies r WHERE r.tenant_id=g.tenant_id
                  AND r.channel_id=g.channel_id AND r.conversation_id=g.conversation_id
                    AND (r.message_id=COALESCE(m.message_identity,g.message_id) OR r.message_id=m.external_id OR r.source_external_id=m.external_id
                    OR (m.external_id LIKE 'connect:%%' AND r.message_id=substring(m.external_id from 9))))
              ORDER BY g.created_at LIMIT 100''')
            for row in cur.fetchall():
                reply_id=flow._store_reply(cur,str(row['tenant_id']),str(row['consultation_id']),str(row['channel_id']),
                    row['conversation_id'],row.get('message_identity') or row['message_id'],row['body'],row['author'],row['happened_at'],source_kind='legacy',
                    message_type=flow._reply_kind(row.get('original_type') or 'text'),source_external_id=row.get('source_external_id') or '')
                if reply_id:
                    from tenant_ai_agents_store import _audit
                    _audit(cur,str(row['tenant_id']),None,'consultation_legacy_reply_reconciled','consultation',str(row['consultation_id']),
                        {'reply_id':reply_id,'source_message_id':row['message_id'],'source':'existing_group_reply'})
                    count+=1
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
