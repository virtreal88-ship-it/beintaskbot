"""Minimal, non-secret identity records that keep agent history referentially intact."""


_HISTORICAL_AGENT_FKS = {
    'saas_ai_agent_jobs': ('agent_id',),
    'saas_ai_agent_runs': ('agent_id',),
    'saas_ai_agent_token_usage': ('agent_id',),
    'saas_ai_agent_drafts': ('agent_id',),
    'saas_ai_agent_action_runs': ('agent_id',),
    'saas_ai_expert_tasks': ('principal_agent_id', 'expert_agent_id'),
    'saas_ai_test_sessions': ('agent_id',),
    # Campaigns are billing/audit history once stopped; keep their agent label
    # resolvable without retaining the agent's live configuration.
    'saas_knowledge_campaigns': ('analysis_agent_id',),
    'saas_knowledge_candidate_sources': ('agent_id',),
}


def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_history_identities(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          id UUID NOT NULL,
          display_name TEXT NOT NULL,
          deleted_at TIMESTAMPTZ,
          deleted_by BIGINT,
          PRIMARY KEY(tenant_id,id))''')
        cur.execute('''INSERT INTO saas_ai_agent_history_identities(tenant_id,id,display_name)
          SELECT tenant_id,id,name FROM saas_ai_agents
          ON CONFLICT(tenant_id,id) DO UPDATE SET display_name=EXCLUDED.display_name
          WHERE saas_ai_agent_history_identities.deleted_at IS NULL''')
        cur.execute('''CREATE OR REPLACE FUNCTION saas_ai_agent_history_identity_sync_fn() RETURNS trigger
          LANGUAGE plpgsql AS $$ BEGIN
            INSERT INTO saas_ai_agent_history_identities(tenant_id,id,display_name)
              VALUES(NEW.tenant_id,NEW.id,NEW.name)
              ON CONFLICT(tenant_id,id) DO UPDATE SET display_name=EXCLUDED.display_name
              WHERE saas_ai_agent_history_identities.deleted_at IS NULL;
            RETURN NEW;
          END $$''')
        cur.execute('DROP TRIGGER IF EXISTS saas_ai_agent_history_identity_sync ON saas_ai_agents')
        cur.execute('''CREATE TRIGGER saas_ai_agent_history_identity_sync
          AFTER INSERT OR UPDATE OF name ON saas_ai_agents
          FOR EACH ROW EXECUTE FUNCTION saas_ai_agent_history_identity_sync_fn()''')
        for table, columns in _HISTORICAL_AGENT_FKS.items():
            for column in columns:
                constraint = f'{table}_{column}_history_identity_fkey'
                cur.execute('''SELECT conname FROM pg_constraint
                  WHERE conrelid=%s::regclass AND confrelid='saas_ai_agents'::regclass AND contype='f'
                    AND %s=ANY(ARRAY(SELECT a.attname FROM unnest(conkey) k(attnum)
                      JOIN pg_attribute a ON a.attrelid=conrelid AND a.attnum=k.attnum))''',
                    (table, column))
                old = [row['conname'] for row in cur.fetchall()]
                for name in old:
                    cur.execute(f'ALTER TABLE {table} DROP CONSTRAINT {name}')
                cur.execute('''SELECT 1 FROM pg_constraint WHERE conrelid=%s::regclass AND conname=%s''',
                            (table, constraint))
                if not cur.fetchone():
                    cur.execute(f'''ALTER TABLE {table} ADD CONSTRAINT {constraint}
                      FOREIGN KEY(tenant_id,{column}) REFERENCES saas_ai_agent_history_identities(tenant_id,id)''')
