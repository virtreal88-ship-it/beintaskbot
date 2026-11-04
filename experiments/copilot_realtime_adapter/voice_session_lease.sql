-- REVIEW ONLY: this file is not imported or executed by application startup.
-- Apply once to an isolated, approved PostgreSQL test database only. Do not
-- apply to production until lease fencing and expiry behavior are accepted.
BEGIN;
SET LOCAL lock_timeout = '250ms';
CREATE TABLE saas_operator_voice_lease (
  tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
  channel_id UUID NOT NULL,
  conversation_id TEXT NOT NULL CHECK(length(conversation_id) BETWEEN 1 AND 200),
  session_id UUID NOT NULL UNIQUE,
  actor_id BIGINT NOT NULL CHECK(actor_id > 0),
  owner_id UUID NOT NULL,
  fence BIGINT NOT NULL CHECK(fence > 0),
  state TEXT NOT NULL CHECK(state IN ('starting','active','closed')),
  lease_until TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ,
  PRIMARY KEY(tenant_id,channel_id,conversation_id)
);
CREATE INDEX saas_operator_voice_lease_expiry_idx
  ON saas_operator_voice_lease(lease_until) WHERE state IN ('starting','active');
COMMIT;
