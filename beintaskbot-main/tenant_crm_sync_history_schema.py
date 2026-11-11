"""Indexes for bounded source-card history reads; no event contents are changed."""


def migrate_history_indexes(cur):
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_crm_scalar_history_idx
      ON saas_crm_scalar_outbox(tenant_id,entity_type,internal_id,created_at DESC,id DESC)''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_crm_relation_source_history_idx
      ON saas_crm_relation_outbox(tenant_id,(intent->'source'->>'entity_type'),
        (intent->'source'->>'internal_id'),created_at DESC,id DESC)''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_crm_legacy_history_idx
      ON saas_crm_sync_outbox(tenant_id,entity_type,internal_id,created_at DESC,id DESC)''')
