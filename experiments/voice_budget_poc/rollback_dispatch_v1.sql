-- REVIEW ONLY; caller owns transaction. Requires externally verified provider
-- shutdown and stopped/new-worker cutover; SQL alone cannot prove either.
SET LOCAL lock_timeout = '3s';
SELECT pg_advisory_xact_lock(hashtextextended('saas-voice-dispatch-schema',0));
LOCK TABLE saas_voice_test_grant IN SHARE ROW EXCLUSIVE MODE;
SELECT grant_key FROM saas_voice_test_grant FOR UPDATE;
LOCK TABLE saas_voice_test_hold IN ACCESS EXCLUSIVE MODE;
DO $rollback$
DECLARE
 definition TEXT;
 v SMALLINT;
 relevant_count INTEGER;
 state_att SMALLINT;
 old_def CONSTANT TEXT := $$CHECK ((state = ANY (ARRAY['reserved'::text, 'claimed'::text, 'unknown'::text, 'settled'::text, 'released'::text, 'quarantined'::text])))$$;
 new_def CONSTANT TEXT := $$CHECK ((state = ANY (ARRAY['reserved'::text, 'claimed'::text, 'dispatching'::text, 'unknown'::text, 'settled'::text, 'released'::text, 'quarantined'::text])))$$;
 body CONSTANT TEXT := $body$
BEGIN
  IF to_regclass('saas_voice_session_budget') IS NOT NULL OR to_regclass('saas_voice_session_response') IS NOT NULL OR
     to_regclass('saas_voice_session_audio') IS NOT NULL THEN
    RAISE EXCEPTION 'voice_session_remove_requires_explicit_review' USING ERRCODE='23514';
  END IF;
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
BEGIN
  IF to_regclass('saas_voice_provenance') IS NOT NULL THEN
    RAISE EXCEPTION 'voice_provenance_remove_requires_explicit_review' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM saas_voice_test_grant WHERE enabled IS DISTINCT FROM false) OR
     (SELECT count(*) FROM saas_voice_test_grant)<>1 THEN
    RAISE EXCEPTION 'voice_dispatch_rollback_grant_not_disabled' USING ERRCODE='23514';
  END IF;
  function_id := to_regprocedure('public.voice_test_block_enable()');
  IF function_id IS NULL OR NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
      WHERE p.oid=function_id AND p.prosrc=body AND p.pronargs=0 AND NOT p.prosecdef
      AND p.prorettype='trigger'::regtype AND l.lanname='plpgsql') OR
     NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='saas_voice_test_grant'::regclass
       AND tgname='voice_test_block_enable' AND tgfoid=function_id AND tgtype=31 AND tgenabled='O'
       AND tgqual IS NULL AND COALESCE(array_length(tgattr::smallint[],1),0)=0 AND NOT tgisinternal) THEN
    RAISE EXCEPTION 'voice_activation_guard_missing_or_changed' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM saas_voice_test_hold WHERE state NOT IN ('released','settled')) THEN
    RAISE EXCEPTION 'voice_dispatch_rollback_unresolved_holds' USING ERRCODE='23514';
  END IF;
  SELECT attnum INTO state_att FROM pg_attribute WHERE attrelid='saas_voice_test_hold'::regclass AND attname='state' AND NOT attisdropped;
  SELECT count(*), max(pg_get_constraintdef(oid)) FILTER(WHERE conname='saas_voice_test_hold_state_check')
    INTO relevant_count,definition FROM pg_constraint
    WHERE conrelid='saas_voice_test_hold'::regclass AND contype='c' AND state_att=ANY(conkey);
  IF relevant_count<>1 OR definition IS NULL OR (definition<>old_def AND definition<>new_def) THEN
    RAISE EXCEPTION 'voice_dispatch_schema_unknown' USING ERRCODE='23514';
  END IF;
  SELECT version INTO v FROM saas_voice_test_schema_versions WHERE component='dispatch' FOR UPDATE;
  IF v IS NULL OR v NOT IN (1,2) OR (v=1 AND definition<>old_def) OR (v=2 AND definition<>new_def) THEN
    RAISE EXCEPTION 'voice_dispatch_version_mismatch' USING ERRCODE='23514';
  END IF;
  IF definition=new_def THEN
    ALTER TABLE saas_voice_test_hold ADD CONSTRAINT saas_voice_test_hold_state_v1_check
      CHECK(state IN ('reserved','claimed','unknown','settled','released','quarantined')) NOT VALID;
    ALTER TABLE saas_voice_test_hold VALIDATE CONSTRAINT saas_voice_test_hold_state_v1_check;
    ALTER TABLE saas_voice_test_hold DROP CONSTRAINT saas_voice_test_hold_state_check;
    ALTER TABLE saas_voice_test_hold RENAME CONSTRAINT saas_voice_test_hold_state_v1_check TO saas_voice_test_hold_state_check;
  END IF;
  UPDATE saas_voice_test_schema_versions SET version=1 WHERE component='dispatch';
END $rollback$;
