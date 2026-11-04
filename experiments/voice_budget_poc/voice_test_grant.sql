-- REVIEW CANDIDATE ONLY. Do not run on production until migration, security,
-- single-authority, provider bounds and worker cutover have been reviewed.
CREATE TABLE saas_voice_test_grant (
  grant_key TEXT PRIMARY KEY CHECK(grant_key='voice-test-owner-5usd-2026'),
  cap_microusd BIGINT NOT NULL CHECK(cap_microusd=5000000),
  enabled BOOLEAN NOT NULL DEFAULT false,
  frozen BOOLEAN NOT NULL DEFAULT false
);
INSERT INTO saas_voice_test_grant(grant_key,cap_microusd,enabled)
VALUES('voice-test-owner-5usd-2026',5000000,false);
-- One whole-pilot escrow. Never release automatically: reconciliation of
-- provider usage, including pre-response input, must first be proven.
CREATE TABLE saas_voice_test_session_escrow (
  grant_key TEXT PRIMARY KEY REFERENCES saas_voice_test_grant(grant_key),
  session_id UUID NOT NULL UNIQUE,
  fingerprint TEXT NOT NULL,
  reserved_microusd BIGINT NOT NULL CHECK(reserved_microusd=5000000),
  state TEXT NOT NULL CHECK(state IN ('reserved','unknown')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE saas_voice_test_session (
  session_id UUID PRIMARY KEY, tenant_id UUID NOT NULL, actor_id BIGINT NOT NULL,
  channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, executor TEXT NOT NULL,
  fingerprint TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE saas_voice_test_hold (
  request_id UUID PRIMARY KEY, tenant_id UUID NOT NULL, session_id UUID NOT NULL,
  actor_id BIGINT NOT NULL, channel_id UUID NOT NULL, conversation_id TEXT NOT NULL,
  executor TEXT NOT NULL,
  response_number SMALLINT NOT NULL CHECK(response_number BETWEEN 1 AND 2),
  fingerprint TEXT NOT NULL, quote_version TEXT NOT NULL, model TEXT NOT NULL,
  reserved_microusd BIGINT NOT NULL CHECK(reserved_microusd>0), actual_microusd BIGINT,
  tenant_repair_state TEXT NOT NULL DEFAULT 'intact' CHECK(tenant_repair_state IN ('intact','restored','recreated','conflict')),
  tenant_reservation_id UUID NOT NULL, state TEXT NOT NULL CHECK(state IN ('reserved','claimed','dispatching','unknown','settled','released','quarantined')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,session_id,response_number),
  FOREIGN KEY(session_id) REFERENCES saas_voice_test_session(session_id),
  FOREIGN KEY(tenant_id,tenant_reservation_id) REFERENCES saas_ai_spend_reservations(tenant_id,id)
);
