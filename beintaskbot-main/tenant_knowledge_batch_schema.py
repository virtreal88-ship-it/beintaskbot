"""Bounded on-demand batch receipts, no conversation copy or new knowledge store."""
def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_batches(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,id UUID NOT NULL,
          created_by BIGINT NOT NULL,params JSONB NOT NULL,request_key TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('running','completed','partial','failed','interrupted')),
          routes JSONB NOT NULL,continuations JSONB NOT NULL DEFAULT '[]',routes_truncated BOOLEAN NOT NULL DEFAULT FALSE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),deadline TIMESTAMPTZ NOT NULL DEFAULT now()+interval '180 seconds',
          completed_at TIMESTAMPTZ,PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,request_key),
          FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_batch_items(
          tenant_id UUID NOT NULL,batch_id UUID NOT NULL,ordinal INTEGER NOT NULL,
          route JSONB NOT NULL,extraction_id UUID,status TEXT NOT NULL,error_code TEXT NOT NULL DEFAULT '',
          message_count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(tenant_id,batch_id,ordinal),
          FOREIGN KEY(tenant_id,batch_id) REFERENCES saas_knowledge_batches(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,extraction_id) REFERENCES saas_knowledge_extractions(tenant_id,id))""")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_knowledge_review_queue_idx ON saas_knowledge_candidates(tenant_id,status,created_at DESC,id DESC)")

        cur.execute("CREATE INDEX IF NOT EXISTS saas_knowledge_queue_all_idx ON saas_knowledge_candidates(tenant_id,created_at DESC,id DESC)")
