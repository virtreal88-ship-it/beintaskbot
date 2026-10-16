"""Isolated virtual sessions/turns; real-dialogue state is never reused."""

def create_schema(conn):
    with conn.cursor() as cur:
        for table in ('saas_ai_agent_test_mode','saas_ai_agent_connect_test_mode'):
            cur.execute('ALTER TABLE '+table+' ADD COLUMN IF NOT EXISTS agent_id UUID NULL')
            constraint=table+'_testing_agent_fk'
            cur.execute('ALTER TABLE '+table+' DROP CONSTRAINT IF EXISTS '+constraint)
            cur.execute('ALTER TABLE '+table+' ADD CONSTRAINT '+constraint+' FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_test_sessions(
          tenant_id UUID NOT NULL, id UUID NOT NULL, agent_id UUID NOT NULL, created_by BIGINT NOT NULL,
          scope JSONB NOT NULL DEFAULT '{}'::jsonb,state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','paused','human')),
          reason TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1,reply_count INTEGER NOT NULL DEFAULT 0,
          pending_turn UUID NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_test_turns(
          tenant_id UUID NOT NULL,id UUID NOT NULL,session_id UUID NOT NULL,input_hash TEXT NOT NULL,
          client_text TEXT NOT NULL,response TEXT NOT NULL DEFAULT '',trace JSONB NOT NULL DEFAULT '{}'::jsonb,
          state TEXT NOT NULL CHECK(state IN ('generating','completed','blocked','unknown','superseded')),
          reserved_tokens BIGINT NOT NULL DEFAULT 0 CHECK(reserved_tokens>=0),total_tokens BIGINT NOT NULL DEFAULT 0 CHECK(total_tokens>=0),
          snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,session_id) REFERENCES saas_ai_test_sessions(tenant_id,id) ON DELETE CASCADE)''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_test_session_actor_idx ON saas_ai_test_sessions(tenant_id,created_by,created_at DESC)')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_test_turn_session_idx ON saas_ai_test_turns(tenant_id,session_id,created_at,id)')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_test_token_day_idx ON saas_ai_test_turns(tenant_id,created_at)')
