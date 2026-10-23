"""Additive expert role and internal task receipts; no client delivery authority."""

def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE saas_ai_agents ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'primary' CHECK(role IN ('primary','expert'))")
        cur.execute("ALTER TABLE saas_ai_agents ADD COLUMN IF NOT EXISTS expert_policy JSONB NOT NULL DEFAULT '{}'::jsonb")
        cur.execute("ALTER TABLE saas_ai_agent_token_usage ADD COLUMN IF NOT EXISTS reserved_tokens INTEGER NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0)")
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_ai_expert_tasks(
          tenant_id UUID NOT NULL, id UUID NOT NULL, parent_run_id UUID NOT NULL,
          principal_agent_id UUID NOT NULL, expert_agent_id UUID NOT NULL,
          provider TEXT NOT NULL CHECK(provider IN ('connect','kommo')), channel_id TEXT NOT NULL,
          conversation_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
          expert_version INTEGER NOT NULL, result JSONB, grounded_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
          error_code TEXT NOT NULL DEFAULT '',
          deadline TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at TIMESTAMPTZ, PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,parent_run_id),
          FOREIGN KEY(tenant_id,id) REFERENCES saas_ai_agent_runs(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,parent_run_id) REFERENCES saas_ai_agent_runs(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,principal_agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,expert_agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE)""")
        cur.execute("ALTER TABLE saas_ai_expert_tasks ADD COLUMN IF NOT EXISTS grounded_sources JSONB NOT NULL DEFAULT '[]'::jsonb")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_ai_expert_dialog_idx ON saas_ai_expert_tasks(tenant_id,provider,channel_id,conversation_id,created_at DESC)")

        # Preserve old receipts while allowing one durable call per specialist.
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS saas_ai_expert_parent_specialist ON saas_ai_expert_tasks(tenant_id,parent_run_id,expert_agent_id)")
        cur.execute("ALTER TABLE saas_ai_expert_tasks DROP CONSTRAINT IF EXISTS saas_ai_expert_tasks_tenant_id_parent_run_id_key")
