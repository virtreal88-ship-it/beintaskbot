"""Read-only union of exact WhatsApp group routes. Never grants send authority."""
from tenant_ai_group_coordination import group_jid


def page(cur, tenant, conversation, *, before=None, limit=51, through=None, channel_ids=None, selected_row_id=None, transcript_only=False):
    if not group_jid(conversation):
        return []
    # Only signed, route-consistent provider IDs AND exact participant identities
    # prove a duplicate. Event-scoped provider alias pairs may equate LID/phone JID.
    # Conflicting or missing alias evidence never joins different identities.
    # Prefer an outbound representative to preserve company authorship/media route.
    selected_clause=('event_identity=(SELECT event_identity FROM scoped WHERE id=%s::uuid) AND ' if selected_row_id else '')
    cur.execute("""SELECT to_regclass('saas_whatsapp_capture_generations') IS NOT NULL
      AND to_regclass('saas_whatsapp_native_inbox') IS NOT NULL AS ready""")
    has_native_history = cur.fetchone()['ready']
    # Old native send receipts lack participantJid. Only recover the sender
    # when the source has used one account across its capture generations.
    accounts = '''SELECT channel_id,min(account_jid) AS jid FROM saas_whatsapp_capture_generations
      WHERE tenant_id=%s::uuid GROUP BY channel_id HAVING count(DISTINCT account_jid)=1''' if has_native_history else (
        'SELECT NULL::uuid AS channel_id,NULL::text AS jid WHERE FALSE')
    # Group media receipts can identify the exact sending generation.
    media_account = '''(SELECT min(g.account_jid)
      FROM saas_whatsapp_native_inbox i JOIN saas_whatsapp_capture_generations g USING (tenant_id,channel_id,generation)
      WHERE i.tenant_id=m.tenant_id AND i.channel_id=c.connect_channel_id AND i.event_id=m.raw->>'nativeEventId'
      HAVING count(DISTINCT g.account_jid)=1)''' if has_native_history else 'NULL::text'
    cur.execute('''WITH accounts AS ('''+accounts+'''), source_rows AS (
      SELECT m.*,c.connect_channel_id::text AS source_channel_id,c.lead_id AS source_lead_id,
        ch.name AS source_channel_name,c.conversation_id,
        COALESCE(m.happened_at,m.created_at) AS sort_at,
        CASE WHEN m.raw->>'participantJid' ~ '^[0-9]{5,22}@(s[.]whatsapp[.]net|lid)$' THEN m.raw->>'participantJid'
          WHEN COALESCE(m.raw->>'participantJid','')='' AND m.direction='outgoing' AND m.raw->'fromMe'='true'::jsonb
          THEN CASE WHEN m.raw->'_crm_native_send_receipt'='true'::jsonb THEN accounts.jid
            WHEN m.raw->'_crm_group_send_receipt'='true'::jsonb AND m.raw->>'nativeEventId' ~ '^[0-9a-f]{64}$'
            THEN '''+media_account+''' END END AS verified_participant
      FROM saas_connect_conversations c
      JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      JOIN saas_crm_messages m ON m.tenant_id=c.tenant_id AND m.kommo_lead_id=c.lead_id
        AND m.channel='connect:'||c.connect_channel_id::text
      LEFT JOIN accounts ON accounts.channel_id=c.connect_channel_id
      WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s
        AND (%s::uuid[] IS NULL OR c.connect_channel_id=ANY(%s::uuid[]))
        AND (%s::timestamptz IS NULL OR COALESCE(m.happened_at,m.created_at)<=%s::timestamptz)
        AND (NOT %s OR m.direction IN ('incoming','outgoing'))
    ), base AS (
      SELECT m.*,
        CASE WHEN m.raw->>'conversationId'=m.conversation_id
          AND m.raw->>'channelId'=m.source_channel_id
          AND m.raw->>'externalMessageId' ~ '^[A-Za-z0-9_-]{5,200}$'
          AND m.verified_participant ~ '^[0-9]{5,22}@(s[.]whatsapp[.]net|lid)$'
          THEN m.raw->>'externalMessageId' END AS provider_event
      FROM source_rows m
    ), pairs AS (
      SELECT DISTINCT provider_event,message_type,
        raw#>>'{participantIdentity,lid}' AS lid,raw#>>'{participantIdentity,jid}' AS jid
      FROM base WHERE provider_event IS NOT NULL
        AND raw#>>'{participantIdentity,lid}' ~ '^[0-9]{5,22}@lid$'
        AND raw#>>'{participantIdentity,jid}' ~ '^[0-9]{5,22}@s[.]whatsapp[.]net$'
        AND raw->>'participantJid' IN (raw#>>'{participantIdentity,lid}',raw#>>'{participantIdentity,jid}')
      UNION
      SELECT provider_event,message_type,
        CASE WHEN verified_participant LIKE '%%@lid' THEN verified_participant ELSE raw->>'participantAlt' END,
        CASE WHEN verified_participant LIKE '%%@lid' THEN raw->>'participantAlt' ELSE verified_participant END
      FROM base WHERE provider_event IS NOT NULL AND raw->>'transport'='beintaskbot_whatsapp'
        AND direction='incoming' AND raw->'fromMe'='false'::jsonb
        AND ((verified_participant LIKE '%%@lid' AND raw->>'participantAlt' ~ '^[0-9]{5,22}@s[.]whatsapp[.]net$')
          OR (verified_participant LIKE '%%@s.whatsapp.net' AND raw->>'participantAlt' ~ '^[0-9]{5,22}@lid$'))
    ), verified AS (
      SELECT p.* FROM pairs p WHERE NOT EXISTS (
        SELECT 1 FROM pairs q WHERE q.provider_event=p.provider_event AND q.message_type=p.message_type
          AND ((q.lid=p.lid AND q.jid<>p.jid) OR (q.jid=p.jid AND q.lid<>p.lid)))
    ), scoped AS (
      SELECT b.*,CASE WHEN provider_event IS NULL THEN 'row:'||b.id::text
        ELSE 'event:'||provider_event||':'||COALESCE((
          SELECT v.jid FROM verified v WHERE v.provider_event=b.provider_event AND v.message_type=b.message_type
            AND v.lid=b.verified_participant),b.verified_participant)||':'||b.message_type END AS event_identity
      FROM base b
    ), ranked AS (
      SELECT scoped.*,row_number() OVER(PARTITION BY event_identity ORDER BY (direction='outgoing') DESC,created_at,id) AS copy_rank,
        jsonb_agg(jsonb_build_object('channel_id',source_channel_id,'channel_name',source_channel_name,
          'lead_id',source_lead_id,'external_id',external_id,'direction',direction,'author_name',author_name))
          OVER(PARTITION BY event_identity ORDER BY created_at,id
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS sources
      FROM scoped
    ), canonical AS (SELECT * FROM ranked WHERE copy_rank=1)
    SELECT * FROM canonical WHERE '''+selected_clause+'''(%s::uuid IS NULL OR (sort_at,id)<(
      SELECT x.sort_at,x.id FROM canonical x JOIN scoped anchor ON anchor.event_identity=x.event_identity
      WHERE anchor.id=%s::uuid))
    ORDER BY sort_at DESC,id DESC LIMIT %s''',
    (*((tenant,) if has_native_history else ()),tenant,conversation,channel_ids,channel_ids,through,through,
     transcript_only,*((selected_row_id,) if selected_row_id else ()),before,before,min(max(int(limit),1),81)))
    fields={'id','external_id','direction','channel','author_name','body','message_type','media_url',
            'happened_at','created_at','raw','source_channel_id','source_channel_name','source_lead_id','sources'}
    return [{key:value for key,value in dict(row).items() if key in fields} for row in cur.fetchall()]
