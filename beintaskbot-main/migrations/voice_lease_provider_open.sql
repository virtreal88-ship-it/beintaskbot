-- Manual, additive migration for the owner-scoped Realtime voice lease.
-- Do not run from ensure_schema or application startup. Apply after review
-- and before deploying code that queries provider_opened_at.
-- Existing rows receive NULL and intentionally remain fail-closed: their
-- provider-open outcome cannot be reconstructed from the old lease fields.
BEGIN;
SET LOCAL lock_timeout = '250ms';
ALTER TABLE saas_operator_voice_lease
  ADD COLUMN IF NOT EXISTS provider_opened_at TIMESTAMPTZ;
COMMIT;
