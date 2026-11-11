"""Additive, separate journal for narrow CRM relationship delivery."""


def migrate_relation_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_relation_identities (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      provider TEXT NOT NULL CHECK(provider='kommo'), account_id TEXT NOT NULL,
      connection_generation TEXT NOT NULL, entity_type TEXT NOT NULL
        CHECK(entity_type IN ('deal','contact','company')), internal_id UUID NOT NULL,
      external_id TEXT NOT NULL CHECK(external_id ~ '^[1-9][0-9]*$'),
      provenance TEXT NOT NULL CHECK(provenance IN ('created','imported','owner_verified')),
      evidence_id TEXT NOT NULL CHECK(evidence_id<>''), verified_by BIGINT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,provider,account_id,connection_generation,entity_type,internal_id),
      UNIQUE(tenant_id,provider,account_id,connection_generation,entity_type,external_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_relation_outbox (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      changed_type TEXT NOT NULL CHECK(changed_type IN ('deal','contact')),
      changed_id UUID NOT NULL, relation TEXT NOT NULL
        CHECK(relation IN ('deal_contact','contact_company')),
      intent JSONB NOT NULL CHECK(jsonb_typeof(intent)='object'),
      intent_hash TEXT NOT NULL, remote_before TEXT NULL,
      state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN
        ('pending','processing','synced','superseded','disabled','conflict','unknown')),
      plan JSONB NULL, step_index INTEGER NOT NULL DEFAULT 0 CHECK(step_index>=0),
      attempt_outcome TEXT NOT NULL DEFAULT 'not_started' CHECK(attempt_outcome IN
        ('not_started','sending','acknowledged')),
      error_code TEXT NOT NULL DEFAULT '', observation JSONB NULL,
      next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE(tenant_id,id))''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_crm_relation_pending_idx
      ON saas_crm_relation_outbox(next_attempt_at,created_at)
      WHERE state IN ('pending','processing','unknown')''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_relation_heads (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      changed_type TEXT NOT NULL, changed_id UUID NOT NULL, relation TEXT NOT NULL,
      event_id UUID NOT NULL, PRIMARY KEY(tenant_id,changed_type,changed_id,relation),
      FOREIGN KEY(tenant_id,event_id) REFERENCES saas_crm_relation_outbox(tenant_id,id))''')
