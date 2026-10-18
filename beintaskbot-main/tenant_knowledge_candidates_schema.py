"""Review workflow only; source messages stay in saas_crm_messages."""
def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE saas_ai_knowledge_sources ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb")
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_extractions(
          tenant_id UUID NOT NULL, id UUID NOT NULL, created_by BIGINT NOT NULL,
          route JSONB NOT NULL, source_refs JSONB NOT NULL, source_digest TEXT NOT NULL,
          fingerprint TEXT NOT NULL, request_key TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','completed','failed','unknown')),
          error_code TEXT NOT NULL DEFAULT '', deadline TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
          PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,fingerprint),
          FOREIGN KEY(tenant_id,id) REFERENCES saas_ai_agent_runs(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id))""")
        cur.execute("ALTER TABLE saas_knowledge_extractions ADD COLUMN IF NOT EXISTS media_results JSONB NOT NULL DEFAULT '[]'::jsonb")
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_candidates(
          tenant_id UUID NOT NULL,id UUID NOT NULL,extraction_id UUID NOT NULL,
          content JSONB NOT NULL,content_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending_review'
            CHECK(status IN ('pending_review','approved','rejected')),
          version INTEGER NOT NULL DEFAULT 1,reviewed_by BIGINT,reviewed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,extraction_id,content_hash),
          FOREIGN KEY(tenant_id,extraction_id) REFERENCES saas_knowledge_extractions(tenant_id,id) ON DELETE CASCADE)""")
        cur.execute("ALTER TABLE saas_knowledge_candidates ADD COLUMN IF NOT EXISTS quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb")
        cur.execute("ALTER TABLE saas_knowledge_candidates DROP CONSTRAINT IF EXISTS saas_knowledge_candidates_status_check")
        cur.execute("ALTER TABLE saas_knowledge_candidates ADD CONSTRAINT saas_knowledge_candidates_status_check CHECK(status IN ('pending_review','needs_clarification','approved','rejected'))")
        from tenant_knowledge_batch_schema import create_schema as batches_schema
        batches_schema(conn)
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_candidate_sources(
          tenant_id UUID NOT NULL,candidate_id UUID NOT NULL,agent_id UUID NOT NULL,source_id UUID NOT NULL,
          PRIMARY KEY(tenant_id,candidate_id,agent_id),
          FOREIGN KEY(tenant_id,candidate_id) REFERENCES saas_knowledge_candidates(tenant_id,id),
          FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id),
          FOREIGN KEY(tenant_id,source_id) REFERENCES saas_ai_knowledge_sources(tenant_id,id) ON DELETE CASCADE)""")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_knowledge_extraction_route_idx ON saas_knowledge_extractions(tenant_id,(route->>'channel_id'),(route->>'conversation_id'),created_at DESC)")
