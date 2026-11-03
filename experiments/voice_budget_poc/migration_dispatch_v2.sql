-- REVIEW ONLY; caller owns one transaction. Never run on production until
-- role, migration runner, coordinated worker rollout, billing and rollback
-- reviews are complete. This changes no rows and cannot enable voice.
SET LOCAL lock_timeout = '3s';
SELECT pg_advisory_xact_lock(hashtextextended('saas-voice-dispatch-schema',0));
LOCK TABLE saas_voice_test_hold IN ACCESS EXCLUSIVE MODE;
CREATE TABLE IF NOT EXISTS saas_voice_test_schema_versions (
 component TEXT PRIMARY KEY, version SMALLINT NOT NULL CHECK(version BETWEEN 1 AND 2)
);
DO $migration$
DECLARE
 definition TEXT;
 v SMALLINT;
 state_att SMALLINT;
 relevant_count INTEGER;
 old_def CONSTANT TEXT := $$CHECK ((state = ANY (ARRAY['reserved'::text, 'claimed'::text, 'unknown'::text, 'settled'::text, 'released'::text, 'quarantined'::text])))$$;
 new_def CONSTANT TEXT := $$CHECK ((state = ANY (ARRAY['reserved'::text, 'claimed'::text, 'dispatching'::text, 'unknown'::text, 'settled'::text, 'released'::text, 'quarantined'::text])))$$;
BEGIN
  SELECT attnum INTO state_att FROM pg_attribute WHERE attrelid='saas_voice_test_hold'::regclass AND attname='state' AND NOT attisdropped;
  IF state_att IS NULL THEN RAISE EXCEPTION 'voice_dispatch_schema_unknown' USING ERRCODE='23514'; END IF;
  SELECT count(*), max(pg_get_constraintdef(oid)) FILTER(WHERE conname='saas_voice_test_hold_state_check')
    INTO relevant_count,definition FROM pg_constraint
    WHERE conrelid='saas_voice_test_hold'::regclass AND contype='c' AND state_att=ANY(conkey);
  IF relevant_count<>1 OR definition IS NULL OR (definition<>old_def AND definition<>new_def) THEN
    RAISE EXCEPTION 'voice_dispatch_schema_unknown' USING ERRCODE='23514';
  END IF;
  SELECT version INTO v FROM saas_voice_test_schema_versions WHERE component='dispatch' FOR UPDATE;
  IF v IS NOT NULL AND v NOT IN (1,2) THEN RAISE EXCEPTION 'voice_dispatch_version_unknown' USING ERRCODE='23514'; END IF;
  IF v=2 AND definition<>new_def THEN RAISE EXCEPTION 'voice_dispatch_version_mismatch' USING ERRCODE='23514'; END IF;
  IF v=1 AND definition<>old_def THEN RAISE EXCEPTION 'voice_dispatch_version_mismatch' USING ERRCODE='23514'; END IF;
  IF definition=old_def THEN
    ALTER TABLE saas_voice_test_hold ADD CONSTRAINT saas_voice_test_hold_state_v2_check
      CHECK(state IN ('reserved','claimed','dispatching','unknown','settled','released','quarantined')) NOT VALID;
    ALTER TABLE saas_voice_test_hold VALIDATE CONSTRAINT saas_voice_test_hold_state_v2_check;
    ALTER TABLE saas_voice_test_hold DROP CONSTRAINT saas_voice_test_hold_state_check;
    ALTER TABLE saas_voice_test_hold RENAME CONSTRAINT saas_voice_test_hold_state_v2_check TO saas_voice_test_hold_state_check;
  END IF;
  INSERT INTO saas_voice_test_schema_versions(component,version) VALUES('dispatch',2)
    ON CONFLICT(component) DO UPDATE SET version=EXCLUDED.version;
END $migration$;
