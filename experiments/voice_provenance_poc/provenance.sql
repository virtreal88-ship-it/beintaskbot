-- ISOLATED REVIEW ONLY. Never run on production.
-- One-shot transaction on empty voice history. No backfill of legacy rows.
-- Quiesce affected writers first; lock_timeout bounds a lock wait but does not
-- prove safety in a mixed-worker deployment or prevent the DB aborting a peer.
SET LOCAL lock_timeout = '3s';
SELECT pg_advisory_xact_lock(hashtextextended('saas-voice-dispatch-schema',0));
LOCK TABLE saas_voice_test_grant IN SHARE ROW EXCLUSIVE MODE;
SELECT grant_key FROM saas_voice_test_grant FOR UPDATE;
LOCK TABLE saas_voice_test_hold IN SHARE ROW EXCLUSIVE MODE;
DO $provenance$
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
BEGIN
  IF to_regclass('saas_voice_test_schema_versions') IS NULL THEN
    RAISE EXCEPTION 'voice_dispatch_schema_v2_required' USING ERRCODE='23514';
  END IF;
  IF NOT EXISTS(SELECT 1 FROM saas_voice_test_schema_versions WHERE component='dispatch' AND version=2) THEN
    RAISE EXCEPTION 'voice_dispatch_schema_v2_required' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*) FROM pg_constraint WHERE conrelid='saas_voice_test_hold'::regclass AND contype='c'
        AND (SELECT attnum FROM pg_attribute WHERE attrelid='saas_voice_test_hold'::regclass AND attname='state' AND NOT attisdropped)=ANY(conkey))<>1 OR
     NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='saas_voice_test_hold'::regclass AND conname='saas_voice_test_hold_state_check'
       AND pg_get_constraintdef(oid)=$def$CHECK ((state = ANY (ARRAY['reserved'::text, 'claimed'::text, 'dispatching'::text, 'unknown'::text, 'settled'::text, 'released'::text, 'quarantined'::text])))$def$) THEN
    RAISE EXCEPTION 'voice_dispatch_schema_v2_mismatch' USING ERRCODE='23514';
  END IF;
  IF (SELECT count(*) FROM saas_voice_test_grant)<>1 OR EXISTS(SELECT 1 FROM saas_voice_test_grant WHERE enabled IS DISTINCT FROM false) THEN
    RAISE EXCEPTION 'voice_provenance_grant_must_be_disabled' USING ERRCODE='23514';
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
  IF EXISTS(SELECT 1 FROM saas_voice_test_hold) THEN
    RAISE EXCEPTION 'legacy_voice_rows_require_review' USING ERRCODE='23514';
  END IF;
END $provenance$;

CREATE TABLE saas_voice_provenance (
 request_id UUID PRIMARY KEY REFERENCES saas_voice_test_hold(request_id),
 tenant_id UUID NOT NULL, reservation_id UUID NOT NULL,
 call_key TEXT NOT NULL, kind TEXT NOT NULL, model TEXT NOT NULL,
 quote_version TEXT NOT NULL, initial_reserved BIGINT NOT NULL,
 fingerprint TEXT NOT NULL,
 UNIQUE(tenant_id,reservation_id), UNIQUE(tenant_id,call_key),
 FOREIGN KEY(tenant_id,reservation_id) REFERENCES saas_ai_spend_reservations(tenant_id,id)
);

CREATE FUNCTION voice_provenance_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'voice_provenance_immutable' USING ERRCODE='23514'; END $$;
CREATE TRIGGER voice_provenance_no_change BEFORE UPDATE OR DELETE ON saas_voice_provenance
  FOR EACH ROW EXECUTE FUNCTION voice_provenance_immutable();

CREATE FUNCTION voice_provenance_capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE h saas_ai_spend_reservations%ROWTYPE;
BEGIN
  SELECT * INTO h FROM saas_ai_spend_reservations
    WHERE tenant_id=NEW.tenant_id AND id=NEW.tenant_reservation_id FOR UPDATE;
  IF NOT FOUND OR h.call_key<>'voice-test:'||NEW.request_id::text OR
     h.kind<>'realtime_voice_test' OR h.model<>NEW.model OR h.state<>'reserved' OR
     h.actual_microusd IS NOT NULL OR h.reserved_microusd<>NEW.reserved_microusd OR
     NEW.state<>'reserved' OR NEW.actual_microusd IS NOT NULL OR NEW.tenant_repair_state<>'intact' THEN
    RAISE EXCEPTION 'voice_provenance_initial_scope_conflict' USING ERRCODE='23514';
  END IF;
  INSERT INTO saas_voice_provenance(request_id,tenant_id,reservation_id,call_key,kind,model,quote_version,initial_reserved,fingerprint)
    VALUES(NEW.request_id,NEW.tenant_id,h.id,h.call_key,h.kind,h.model,NEW.quote_version,h.reserved_microusd,NEW.fingerprint);
  RETURN NEW;
END $$;
CREATE TRIGGER voice_provenance_on_insert AFTER INSERT ON saas_voice_test_hold
  FOR EACH ROW EXECUTE FUNCTION voice_provenance_capture();

CREATE FUNCTION voice_provenance_hold_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p saas_voice_provenance%ROWTYPE; voice_state TEXT;
BEGIN
  SELECT * INTO p FROM saas_voice_provenance WHERE tenant_id=OLD.tenant_id AND reservation_id=OLD.id;
  IF NOT FOUND THEN
    IF TG_OP='UPDATE' AND NEW.kind='realtime_voice_test' THEN
      RAISE EXCEPTION 'unbound_voice_hold_transition' USING ERRCODE='23514';
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
  END IF;
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'voice_owned_hold_delete' USING ERRCODE='23514'; END IF;
  -- The server's pre-claim cancel first changes the voice request to released
  -- in the SAME transaction. Generic release alone cannot do so. This avoids
  -- retaining an unused hold while still denying release after claim.
  IF OLD.state='reserved' AND NEW.state='released' THEN
    SELECT state INTO voice_state FROM saas_voice_test_hold WHERE request_id=p.request_id;
    IF voice_state='released' AND NEW.actual_microusd=0 AND NEW.reserved_microusd=OLD.reserved_microusd AND
       (to_jsonb(NEW)-'state'-'actual_microusd'-'completed_at')=
       (to_jsonb(OLD)-'state'-'actual_microusd'-'completed_at') THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'voice_owned_hold_release_denied' USING ERRCODE='23514';
  END IF;
  IF NEW.tenant_id<>p.tenant_id OR NEW.id<>p.reservation_id OR NEW.call_key<>p.call_key OR
     NEW.kind<>p.kind OR NEW.model<>p.model OR NEW.reserved_microusd<OLD.reserved_microusd OR
     NEW.reserved_microusd<p.initial_reserved OR NEW.actual_microusd IS DISTINCT FROM OLD.actual_microusd OR
     (to_jsonb(NEW)-'state'-'reserved_microusd')<>(to_jsonb(OLD)-'state'-'reserved_microusd') OR
     NOT ((OLD.state='reserved' AND NEW.state IN ('reserved','unknown')) OR
          (OLD.state='unknown' AND NEW.state='unknown')) THEN
    RAISE EXCEPTION 'voice_owned_hold_immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER voice_provenance_hold_guard_update BEFORE UPDATE ON saas_ai_spend_reservations
  FOR EACH ROW WHEN (OLD.kind='realtime_voice_test' OR NEW.kind='realtime_voice_test') EXECUTE FUNCTION voice_provenance_hold_guard();
CREATE TRIGGER voice_provenance_hold_guard_delete BEFORE DELETE ON saas_ai_spend_reservations
  FOR EACH ROW WHEN (OLD.kind='realtime_voice_test') EXECUTE FUNCTION voice_provenance_hold_guard();

CREATE FUNCTION voice_provenance_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'voice_owned_request_delete' USING ERRCODE='23514'; END IF;
  IF (to_jsonb(NEW)-'state'-'actual_microusd'-'updated_at'-'tenant_repair_state')<>
     (to_jsonb(OLD)-'state'-'actual_microusd'-'updated_at'-'tenant_repair_state') OR
     (OLD.actual_microusd IS NOT NULL AND (NEW.actual_microusd IS NULL OR NEW.actual_microusd<OLD.actual_microusd)) OR
     (OLD.tenant_repair_state='conflict' AND NEW.tenant_repair_state<>'conflict') OR
     NOT ((OLD.state='reserved' AND NEW.state IN ('reserved','claimed','released')) OR
          (OLD.state='claimed' AND NEW.state IN ('claimed','dispatching','unknown','quarantined')) OR
          (OLD.state='dispatching' AND NEW.state IN ('dispatching','unknown','quarantined')) OR
          (OLD.state='unknown' AND NEW.state IN ('unknown','quarantined')) OR
          (OLD.state='settled' AND NEW.state IN ('settled','quarantined')) OR
          (OLD.state='quarantined' AND NEW.state IN ('quarantined','settled')) OR
          (OLD.state='released' AND NEW.state='released')) OR
     (OLD.tenant_repair_state='conflict' AND NEW.state='settled') THEN
    RAISE EXCEPTION 'voice_owned_request_immutable' USING ERRCODE='23514';
  END IF;
  -- Defend the direct CAS path too: the separate test-only wrapper is not
  -- itself an enforcement boundary. A real deployment still needs DB role
  -- controls so writers cannot disable the trigger or bypass the gateway.
  IF OLD.state='claimed' AND NEW.state='dispatching' AND NOT EXISTS(
    SELECT 1 FROM saas_voice_provenance p JOIN saas_ai_spend_reservations h
      ON h.tenant_id=p.tenant_id AND h.id=p.reservation_id
    WHERE p.request_id=OLD.request_id AND p.tenant_id=OLD.tenant_id AND p.reservation_id=OLD.tenant_reservation_id
      AND p.call_key='voice-test:'||OLD.request_id::text AND p.kind='realtime_voice_test'
      AND p.model=OLD.model AND p.quote_version=OLD.quote_version AND p.initial_reserved=OLD.reserved_microusd
      AND p.fingerprint=OLD.fingerprint AND h.call_key=p.call_key AND h.kind=p.kind AND h.model=p.model
      AND h.state='unknown' AND h.reserved_microusd=p.initial_reserved AND h.actual_microusd IS NULL
  ) THEN
    RAISE EXCEPTION 'voice_dispatch_provenance_conflict' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER voice_provenance_request_guard BEFORE UPDATE OR DELETE ON saas_voice_test_hold
  FOR EACH ROW EXECUTE FUNCTION voice_provenance_request_guard();
