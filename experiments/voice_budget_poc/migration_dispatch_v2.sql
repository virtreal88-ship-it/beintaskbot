-- REVIEW ONLY; caller owns one transaction. Never run on production until
-- role, migration runner, coordinated worker rollout, billing and rollback
-- reviews are complete. This changes no rows and cannot enable voice.
SET LOCAL lock_timeout = '3s';
SELECT pg_advisory_xact_lock(hashtextextended('saas-voice-dispatch-schema',0));
-- Lock order: schema advisory -> grant table -> grant row -> voice hold table.
-- Without the table lock an UPDATE may hold a table lock while waiting on the
-- row and deadlock with CREATE TRIGGER. These are voice-only objects.
LOCK TABLE saas_voice_test_grant IN SHARE ROW EXCLUSIVE MODE;
SELECT grant_key FROM saas_voice_test_grant FOR UPDATE;
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
  IF (SELECT count(*) FROM saas_voice_test_grant)<>1 OR EXISTS(SELECT 1 FROM saas_voice_test_grant WHERE enabled IS DISTINCT FROM false) THEN
    RAISE EXCEPTION 'voice_dispatch_grant_must_be_disabled' USING ERRCODE='23514';
  END IF;
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

-- Durable deny-by-default gate. A future approved activation requires a
-- separate reviewed migration and verified runtime/ACL cutover. Reusing a
-- same-named function or trigger with different definition is forbidden.
DO $guard$
DECLARE
 body CONSTANT TEXT := $body$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'voice_activation_requires_review' USING ERRCODE='23514';
  ELSIF TG_OP='INSERT' THEN
    IF NEW.enabled IS TRUE THEN RAISE EXCEPTION 'voice_activation_requires_review' USING ERRCODE='23514'; END IF;
  ELSIF NEW.enabled IS TRUE AND OLD.enabled IS DISTINCT FROM TRUE THEN
    RAISE EXCEPTION 'voice_activation_requires_review' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END
$body$;
 function_id OID;
 trigger_count INTEGER;
BEGIN
  function_id := to_regprocedure('public.voice_test_block_enable()');
  IF function_id IS NULL THEN
    EXECUTE format('CREATE FUNCTION public.voice_test_block_enable() RETURNS trigger LANGUAGE plpgsql AS %L',body);
    function_id := to_regprocedure('public.voice_test_block_enable()');
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
      WHERE p.oid=function_id AND p.prosrc=body AND p.pronargs=0 AND NOT p.prosecdef
      AND p.prorettype='trigger'::regtype AND l.lanname='plpgsql') THEN
    RAISE EXCEPTION 'voice_activation_guard_function_mismatch' USING ERRCODE='23514';
  END IF;
  SELECT count(*) INTO trigger_count FROM pg_trigger WHERE tgrelid='saas_voice_test_grant'::regclass
    AND tgname='voice_test_block_enable' AND NOT tgisinternal;
  IF trigger_count=0 THEN
    CREATE TRIGGER voice_test_block_enable BEFORE INSERT OR UPDATE OR DELETE ON saas_voice_test_grant
      FOR EACH ROW EXECUTE FUNCTION public.voice_test_block_enable();
  ELSIF trigger_count<>1 OR NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='saas_voice_test_grant'::regclass
      AND tgname='voice_test_block_enable' AND tgfoid=function_id AND tgtype=31 AND tgenabled='O'
      AND tgqual IS NULL AND COALESCE(array_length(tgattr::smallint[],1),0)=0 AND NOT tgisinternal) THEN
    RAISE EXCEPTION 'voice_activation_guard_trigger_mismatch' USING ERRCODE='23514';
  END IF;
END $guard$;
