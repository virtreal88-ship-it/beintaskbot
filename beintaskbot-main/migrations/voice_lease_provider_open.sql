-- Manual, additive migration for the owner-scoped Realtime voice lease.
-- Do not run from ensure_schema or application startup. Apply after review
-- and before deploying code that queries provider_opened_at.
-- Existing rows receive NULL and intentionally remain fail-closed: their
-- provider-open outcome cannot be reconstructed from the old lease fields.
BEGIN;
SET LOCAL lock_timeout = '250ms';
ALTER TABLE saas_operator_voice_lease
  ADD COLUMN IF NOT EXISTS provider_opened_at TIMESTAMPTZ;

-- Compatibility fence for mixed-version Railway rollouts. The version on
-- main before this migration reclaims any expired `starting` row by changing
-- its session and fencing token. New code deliberately treats `starting` as
-- an uncertain provider-open outcome. Block only that unsafe legacy takeover;
-- normal renewals, confirmed owner releases, closed-row claims and new-code
-- provider-expiry recovery remain unaffected.
CREATE OR REPLACE FUNCTION reject_uncertain_voice_start_reclaim()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  -- Older writers do not know about provider_opened_at and therefore do not
  -- clear it when reusing a closed row. Never let a new owner inherit the
  -- prior session's provider-open timestamp as recovery evidence.
  IF NEW.session_id IS DISTINCT FROM OLD.session_id
     OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
     OR NEW.fence IS DISTINCT FROM OLD.fence THEN
    NEW.provider_opened_at := NULL;
  END IF;

  IF OLD.state = 'starting'
     AND OLD.provider_opened_at IS NULL
     AND OLD.lease_until <= clock_timestamp()
     AND NEW.state = 'starting'
     AND NEW.fence > OLD.fence THEN
    RAISE EXCEPTION 'uncertain voice startup cannot be reclaimed'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS reject_uncertain_voice_start_reclaim
  ON saas_operator_voice_lease;
CREATE TRIGGER reject_uncertain_voice_start_reclaim
  BEFORE UPDATE ON saas_operator_voice_lease
  FOR EACH ROW EXECUTE FUNCTION reject_uncertain_voice_start_reclaim();
COMMIT;
