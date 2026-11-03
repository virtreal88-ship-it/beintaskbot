-- OFFLINE SYNTHETIC ONLY. No live migrations or production role model.
CREATE TABLE saas_voice_bound_sender_receipt (
 operation_id UUID PRIMARY KEY,
 session_id UUID NOT NULL REFERENCES saas_voice_session_budget(session_id),
 operation TEXT NOT NULL CHECK(operation IN ('audio','response','config','context','control')),
 scope_fingerprint TEXT NOT NULL CHECK(scope_fingerprint ~ '^[0-9a-f]{64}$'),
 wire_sha256 TEXT NOT NULL CHECK(wire_sha256 ~ '^[0-9a-f]{64}$'),
 wire_bytes INTEGER NOT NULL CHECK(wire_bytes BETWEEN 1 AND 131072),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE FUNCTION voice_bound_sender_no_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'sender_receipt_immutable' USING ERRCODE='23514'; END $$;
CREATE TRIGGER voice_bound_sender_no_mutation BEFORE UPDATE OR DELETE ON saas_voice_bound_sender_receipt
FOR EACH ROW EXECUTE FUNCTION voice_bound_sender_no_mutation();
