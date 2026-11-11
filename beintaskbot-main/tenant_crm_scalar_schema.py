"""Separate immutable-provenance journal; legacy outbox contents are untouched."""

def migrate_scalar_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_scalar_outbox (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      provider TEXT NOT NULL CHECK(provider IN ('kommo','beinsystems')),
      entity_type TEXT NOT NULL CHECK(entity_type IN ('contact','deal')), internal_id UUID NOT NULL,
      actor_id BIGINT NOT NULL CHECK(actor_id>0), source_action TEXT NOT NULL CHECK(source_action IN ('create','update')),
      local_revision TIMESTAMPTZ NOT NULL, field_mask JSONB NOT NULL,
      before_values JSONB NOT NULL, values JSONB NOT NULL, binding JSONB NULL,
      external_id TEXT NULL, content_hash TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('pending','processing','synced','disabled','superseded','conflict','unknown','needs_verification')),
      error_code TEXT NOT NULL DEFAULT '', attempt_outcome TEXT NOT NULL DEFAULT 'not_started'
        CHECK(attempt_outcome IN ('not_started','sending','acknowledged')),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CHECK(jsonb_typeof(field_mask)='array'), CHECK(jsonb_typeof(values)='object'),
      CHECK(jsonb_typeof(before_values)='object'))''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_crm_scalar_pending_idx
      ON saas_crm_scalar_outbox(created_at,id) WHERE state IN ('pending','processing')''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_scalar_heads (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      entity_type TEXT NOT NULL CHECK(entity_type IN ('contact','deal')), internal_id UUID NOT NULL,
      local_revision TIMESTAMPTZ NOT NULL,
      PRIMARY KEY(tenant_id,entity_type,internal_id))''')
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_crm_legacy_delivery_attempts (
      event_id UUID PRIMARY KEY, outcome TEXT NOT NULL
        CHECK(outcome IN ('not_started','sending','unknown','completed')),
      claim_attempt INTEGER NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    cur.execute('ALTER TABLE saas_crm_legacy_delivery_attempts ADD COLUMN IF NOT EXISTS claim_attempt INTEGER NULL')
    cur.execute("""DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='saas_crm_legacy_delivery_attempts'::regclass
          AND conname='saas_crm_legacy_delivery_attempts_outcome_check'
          AND position('not_started' in pg_get_constraintdef(oid))=0) THEN
        ALTER TABLE saas_crm_legacy_delivery_attempts
          DROP CONSTRAINT saas_crm_legacy_delivery_attempts_outcome_check;
        ALTER TABLE saas_crm_legacy_delivery_attempts
          ADD CONSTRAINT saas_crm_legacy_delivery_attempts_outcome_check
          CHECK(outcome IN ('not_started','sending','unknown','completed'));
      END IF;
    END $$""")
