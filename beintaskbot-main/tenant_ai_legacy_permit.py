"""Channel-local exceptions to quarantine, never migration of legacy state."""
def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_legacy_permits(
      tenant_id UUID NOT NULL,channel_id TEXT NOT NULL,conversation_id TEXT NOT NULL,
      agent_id UUID NOT NULL,legacy_revision INTEGER NOT NULL,channel_revision INTEGER NOT NULL,
      granted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),actor BIGINT NOT NULL,
      PRIMARY KEY(tenant_id,channel_id,conversation_id))''')
    cur.execute('''CREATE OR REPLACE FUNCTION ai_legacy_blocked(t UUID,ch TEXT,c TEXT,fp TEXT DEFAULT NULL)
      RETURNS BOOLEAN LANGUAGE SQL STABLE AS $$
      SELECT EXISTS(SELECT 1 FROM saas_ai_conversation_state old
        WHERE old.tenant_id=t AND old.provider='connect' AND old.channel_id='' AND old.conversation_id=c
        AND NOT EXISTS(SELECT 1 FROM saas_ai_legacy_permits p
          JOIN saas_ai_conversation_state s ON s.tenant_id=p.tenant_id AND s.provider='connect'
            AND s.channel_id=p.channel_id AND s.conversation_id=p.conversation_id
          WHERE p.tenant_id=t AND p.channel_id=ch AND p.conversation_id=c
            AND p.legacy_revision=old.revision AND p.channel_revision=s.revision AND p.agent_id=s.agent_id
            AND (fp IS NULL OR EXISTS(SELECT 1 FROM saas_tenant_webhook_events e
              JOIN saas_connect_conversations route ON route.tenant_id=t AND route.connect_channel_id::text=ch AND route.conversation_id=c
              JOIN saas_crm_messages m ON m.tenant_id=t AND m.channel='connect:'||ch
                AND m.kommo_lead_id=route.lead_id AND m.external_id='connect:'||(e.payload->>'message_id')
              WHERE e.tenant_id=t AND e.fingerprint=fp AND e.provider='connect'
                AND e.payload->>'channel_id'=ch AND e.payload->>'conversation_id'=c
                AND e.received_at>p.granted_at AND e.received_at>s.resumed_at
                AND m.created_at>p.granted_at AND m.happened_at>s.resumed_at AND m.direction='incoming'))))
      $$''')


def renew(cur, tenant, channel, conversation):
    """Only called after a validated Resume/new inbound/successful send under the dialogue lock.
    All other revision changes revoke the exception, including takeover/Pause.
    """
    cur.execute('''UPDATE saas_ai_legacy_permits p SET channel_revision=s.revision
      FROM saas_ai_conversation_state s,saas_ai_conversation_state old
      WHERE p.tenant_id=%s::uuid AND p.channel_id=%s AND p.conversation_id=%s
        AND s.tenant_id=p.tenant_id AND s.provider='connect' AND s.channel_id=p.channel_id
        AND s.conversation_id=p.conversation_id AND s.agent_id=p.agent_id AND s.state='active'
        AND s.revision=p.channel_revision+1 AND old.tenant_id=p.tenant_id AND old.provider='connect'
        AND old.channel_id='' AND old.conversation_id=p.conversation_id AND old.revision=p.legacy_revision''',
      (tenant,channel,conversation))
