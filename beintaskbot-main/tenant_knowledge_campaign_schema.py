"""Durable, opt-in history campaigns. No messages or secrets are stored here."""
def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_campaigns(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id), id UUID NOT NULL,
          created_by BIGINT NOT NULL, sources JSONB NOT NULL, analysis_agent_id UUID NOT NULL,
          status TEXT NOT NULL DEFAULT 'awaiting_budget' CHECK(status IN
            ('awaiting_budget','running','paused','stopped','budget_exhausted','blocked')),
          progress JSONB NOT NULL DEFAULT '{}',
          version INTEGER NOT NULL DEFAULT 1, max_calls INTEGER NOT NULL CHECK(max_calls>0),
          max_tokens BIGINT NOT NULL CHECK(max_tokens>0), reserved_calls INTEGER NOT NULL DEFAULT 0,
          reserved_tokens BIGINT NOT NULL DEFAULT 0, approved_by BIGINT, approved_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id),
          FOREIGN KEY(tenant_id,analysis_agent_id) REFERENCES saas_ai_agents(tenant_id,id),
          CHECK(reserved_calls>=0 AND reserved_calls<=max_calls),
          CHECK(reserved_tokens>=0 AND reserved_tokens<=max_tokens))""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS saas_knowledge_campaign_active_idx
          ON saas_knowledge_campaigns(tenant_id) WHERE status<>'stopped'""")
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_campaign_fragments(
          tenant_id UUID NOT NULL,campaign_id UUID NOT NULL,id UUID NOT NULL,
          source_key TEXT NOT NULL,route JSONB NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
            ('pending','running','completed','partial','failed','unknown','blocked')),
          lease_token UUID,lease_until TIMESTAMPTZ,extraction_id UUID,
          result JSONB NOT NULL DEFAULT '{}',error_code TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,campaign_id,source_key),
          FOREIGN KEY(tenant_id,campaign_id) REFERENCES saas_knowledge_campaigns(tenant_id,id),
          FOREIGN KEY(tenant_id,extraction_id) REFERENCES saas_knowledge_extractions(tenant_id,id))""")
        cur.execute("""CREATE INDEX IF NOT EXISTS saas_knowledge_campaign_pending_idx
          ON saas_knowledge_campaign_fragments(tenant_id,campaign_id,created_at,id) WHERE status='pending'""")

        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_campaign_sources(
          tenant_id UUID NOT NULL,campaign_id UUID NOT NULL,source_key TEXT NOT NULL,
          channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,history_mode TEXT NOT NULL,
          period_start TIMESTAMPTZ NOT NULL,period_end TIMESTAMPTZ NOT NULL,
          next_start TIMESTAMPTZ NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
          PRIMARY KEY(tenant_id,campaign_id,source_key),
          FOREIGN KEY(tenant_id,campaign_id) REFERENCES saas_knowledge_campaigns(tenant_id,id))""")

        cur.execute("ALTER TABLE saas_knowledge_campaigns ADD COLUMN IF NOT EXISTS media_policy JSONB NOT NULL DEFAULT '{}'")
        cur.execute("ALTER TABLE saas_knowledge_campaigns ADD COLUMN IF NOT EXISTS reserved_kinds JSONB NOT NULL DEFAULT '{}'")
        cur.execute("ALTER TABLE saas_knowledge_campaign_fragments ADD COLUMN IF NOT EXISTS reservation JSONB NOT NULL DEFAULT '{}'")
