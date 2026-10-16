"""Versioned channel-key migration. Unresolved legacy state is retained, never guessed."""

def migrate(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('ai-channel-scope-migration',0))")
        cur.execute('CREATE TABLE IF NOT EXISTS saas_ai_scope_migrations(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        cur.execute('SELECT 1 FROM saas_ai_scope_migrations WHERE version=1')
        if cur.fetchone():return
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_channel_legacy_state(
          tenant_id UUID NOT NULL,provider TEXT NOT NULL,conversation_id TEXT NOT NULL,
          original JSONB NOT NULL,resolved_channel TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL,
          captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,provider,conversation_id))''')
        cur.execute("ALTER TABLE saas_ai_conversation_state ADD COLUMN IF NOT EXISTS channel_id TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_ai_agent_jobs ADD COLUMN IF NOT EXISTS scope_channel_id TEXT GENERATED ALWAYS AS (CASE WHEN provider='connect' THEN channel_id ELSE '' END) STORED")
        cur.execute('''INSERT INTO saas_ai_channel_legacy_state(tenant_id,provider,conversation_id,original,reason)
          SELECT tenant_id,provider,conversation_id,to_jsonb(cs),'legacy_channel_ambiguous'
          FROM saas_ai_conversation_state cs WHERE provider='connect' AND channel_id='' ON CONFLICT DO NOTHING''')
        # Only one persisted route, with no job contradicting that channel/lead, is assignable.
        cur.execute('''UPDATE saas_ai_channel_legacy_state old SET resolved_channel=c.channel_id,reason='unique_persisted_route'
          FROM (SELECT tenant_id,conversation_id,min(connect_channel_id::text) AS channel_id,min(lead_id) AS lead_id
            FROM saas_connect_conversations GROUP BY tenant_id,conversation_id HAVING count(*)=1) c
          WHERE old.tenant_id=c.tenant_id AND old.conversation_id=c.conversation_id
          AND NOT EXISTS (SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=c.tenant_id AND j.provider='connect'
            AND j.conversation_id=c.conversation_id AND (j.channel_id<>c.channel_id OR j.lead_id<>c.lead_id))
          AND NOT EXISTS (SELECT 1 FROM saas_tenant_webhook_events e WHERE e.tenant_id=c.tenant_id AND e.provider='connect'
            AND e.payload->>'conversation_id'=c.conversation_id
            AND COALESCE(e.payload->>'channel_id','')<>c.channel_id)''')
        cur.execute('ALTER TABLE saas_ai_conversation_state DROP CONSTRAINT saas_ai_conversation_state_pkey')
        cur.execute('ALTER TABLE saas_ai_conversation_state ADD PRIMARY KEY(tenant_id,provider,channel_id,conversation_id)')
        cur.execute('ALTER TABLE saas_ai_agent_jobs DROP CONSTRAINT saas_ai_agent_jobs_pkey')
        cur.execute('ALTER TABLE saas_ai_agent_jobs ADD PRIMARY KEY(tenant_id,agent_id,provider,scope_channel_id,conversation_id)')
        cur.execute('''UPDATE saas_ai_conversation_state cs SET channel_id=old.resolved_channel
          FROM saas_ai_channel_legacy_state old WHERE old.tenant_id=cs.tenant_id AND old.provider=cs.provider
          AND old.conversation_id=cs.conversation_id AND cs.channel_id='' AND old.resolved_channel<>'' ''')
        cur.execute('INSERT INTO saas_ai_scope_migrations(version) VALUES(1)')


def unresolved(cur):
    """Read-only deployment gate; no secrets or message bodies."""
    cur.execute("SELECT tenant_id,conversation_id FROM saas_ai_conversation_state WHERE provider='connect' AND channel_id='' ORDER BY tenant_id,conversation_id")
    return cur.fetchall()


def rollback_keys(conn):
    """Offline-only guarded rollback; never collapse/delete per-channel records.

    Stop all writers first. Keep added columns and legacy snapshots. Refuse once
    multi-channel routes or duplicate old identities exist: old binaries cannot
    safely represent those data. A normal forward fix is preferred after rollout.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('ai-channel-scope-migration',0))")
        cur.execute("LOCK TABLE saas_ai_conversation_state,saas_ai_agent_jobs,saas_connect_conversations IN ACCESS EXCLUSIVE MODE")
        cur.execute("""SELECT 1 FROM (
          SELECT tenant_id,provider,conversation_id FROM saas_ai_conversation_state
            GROUP BY tenant_id,provider,conversation_id HAVING count(*)>1
          UNION ALL SELECT tenant_id,provider,conversation_id FROM saas_ai_agent_jobs
            GROUP BY tenant_id,agent_id,provider,conversation_id HAVING count(*)>1
          UNION ALL SELECT tenant_id,'connect',conversation_id FROM saas_connect_conversations
            GROUP BY tenant_id,conversation_id HAVING count(*)>1) conflicts LIMIT 1""")
        if cur.fetchone():
            raise RuntimeError('Unsafe rollback: old keys cannot represent channel-isolated dialogues')
        cur.execute('ALTER TABLE saas_ai_conversation_state DROP CONSTRAINT saas_ai_conversation_state_pkey')
        cur.execute('ALTER TABLE saas_ai_conversation_state ADD PRIMARY KEY(tenant_id,provider,conversation_id)')
        cur.execute('ALTER TABLE saas_ai_agent_jobs DROP CONSTRAINT saas_ai_agent_jobs_pkey')
        cur.execute('ALTER TABLE saas_ai_agent_jobs ADD PRIMARY KEY(tenant_id,agent_id,provider,conversation_id)')
        cur.execute('DELETE FROM saas_ai_scope_migrations WHERE version=1')
