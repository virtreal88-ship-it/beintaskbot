-- OFFLINE REVIEW ONLY. Caller must use one transaction; do not run in prod.
-- Requires v2 activation guard, disabled grant and empty legacy voice history.
SET LOCAL lock_timeout='3s';
SELECT pg_advisory_xact_lock(hashtextextended('saas-voice-dispatch-schema',0));
LOCK TABLE saas_voice_test_grant IN SHARE ROW EXCLUSIVE MODE;
SELECT grant_key FROM saas_voice_test_grant FOR UPDATE;
LOCK TABLE saas_voice_test_hold IN SHARE ROW EXCLUSIVE MODE;

DO $preflight$
DECLARE
 v INTEGER; expected TEXT; observed TEXT; function_id OID;
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
BEGIN
  IF (SELECT count(*) FROM saas_voice_test_grant)<>1 OR EXISTS(SELECT 1 FROM saas_voice_test_grant WHERE enabled IS DISTINCT FROM false) THEN
    RAISE EXCEPTION 'voice_session_grant_must_be_disabled' USING ERRCODE='23514';
  END IF;
  IF to_regclass('saas_voice_test_schema_versions') IS NULL THEN
    RAISE EXCEPTION 'voice_session_v2_guard_required' USING ERRCODE='23514';
  END IF;
  function_id:=to_regprocedure('public.voice_test_block_enable()');
  IF NOT EXISTS(SELECT 1 FROM saas_voice_test_schema_versions WHERE component='dispatch' AND version=2) OR
     (SELECT count(*) FROM pg_constraint WHERE conrelid='saas_voice_test_hold'::regclass AND contype='c'
       AND (SELECT attnum FROM pg_attribute WHERE attrelid='saas_voice_test_hold'::regclass AND attname='state' AND NOT attisdropped)=ANY(conkey))<>1 OR
     NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='saas_voice_test_hold'::regclass AND conname='saas_voice_test_hold_state_check'
       AND pg_get_constraintdef(oid)=$def$CHECK ((state = ANY (ARRAY['reserved'::text, 'claimed'::text, 'dispatching'::text, 'unknown'::text, 'settled'::text, 'released'::text, 'quarantined'::text])))$def$) OR
     function_id IS NULL OR NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
       WHERE p.oid=function_id AND p.prosrc=body AND p.pronargs=0 AND NOT p.prosecdef AND p.prorettype='trigger'::regtype AND l.lanname='plpgsql') OR
     NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='saas_voice_test_grant'::regclass AND tgname='voice_test_block_enable'
       AND tgfoid=function_id AND tgtype=31 AND tgenabled='O' AND tgqual IS NULL AND COALESCE(array_length(tgattr::smallint[],1),0)=0 AND NOT tgisinternal) THEN
    RAISE EXCEPTION 'voice_session_v2_guard_required' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM saas_voice_test_hold) THEN
    RAISE EXCEPTION 'voice_session_legacy_history_requires_review' USING ERRCODE='23514';
  END IF;
  IF to_regclass('saas_voice_provenance') IS NOT NULL THEN
    RAISE EXCEPTION 'voice_session_existing_provenance_requires_review' USING ERRCODE='23514';
  END IF;
  IF to_regclass('saas_voice_session_schema_version') IS NULL THEN
    IF to_regclass('saas_voice_session_budget') IS NOT NULL OR to_regclass('saas_voice_session_response') IS NOT NULL OR to_regclass('saas_voice_session_audio') IS NOT NULL OR
       to_regprocedure('public.voice_session_block_legacy()') IS NOT NULL OR
       to_regprocedure('public.voice_session_guard()') IS NOT NULL OR
       to_regprocedure('public.voice_session_response_guard()') IS NOT NULL OR to_regprocedure('public.voice_session_audio_guard()') IS NOT NULL THEN
      RAISE EXCEPTION 'voice_session_partial_schema' USING ERRCODE='23514';
    END IF;
  ELSE
    SELECT version,signature INTO v,expected FROM saas_voice_session_schema_version WHERE component='session';
    IF v IS DISTINCT FROM 1 OR expected IS NULL OR to_regclass('saas_voice_session_budget') IS NULL OR
       to_regclass('saas_voice_session_response') IS NULL OR to_regclass('saas_voice_session_audio') IS NULL THEN
      RAISE EXCEPTION 'voice_session_version_unknown' USING ERRCODE='23514';
    END IF;
    SELECT md5(COALESCE(string_agg(line,E'\n' ORDER BY line),'')) INTO observed FROM (
      SELECT 'col:'||c.relname||':'||a.attname||':'||format_type(a.atttypid,a.atttypmod)||':'||a.attnotnull::text||':'||a.atthasdef::text||':'||COALESCE(pg_get_expr(d.adbin,d.adrelid),'') AS line
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE c.oid IN ('saas_voice_session_budget'::regclass,'saas_voice_session_response'::regclass,'saas_voice_session_audio'::regclass)
          AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT 'con:'||conrelid::regclass::text||':'||conname||':'||pg_get_constraintdef(oid)
        FROM pg_constraint WHERE conrelid IN ('saas_voice_session_budget'::regclass,'saas_voice_session_response'::regclass,'saas_voice_session_audio'::regclass)
      UNION ALL SELECT 'idx:'||indexrelid::regclass::text||':'||pg_get_indexdef(indexrelid)
        FROM pg_index WHERE indrelid IN ('saas_voice_session_budget'::regclass,'saas_voice_session_response'::regclass,'saas_voice_session_audio'::regclass)
      UNION ALL SELECT 'trg:'||tgname||':'||pg_get_triggerdef(oid)||':'||tgenabled::text
        FROM pg_trigger WHERE tgrelid IN ('saas_voice_test_hold'::regclass,'saas_voice_session_budget'::regclass,'saas_voice_session_response'::regclass,'saas_voice_session_audio'::regclass)
          AND tgname IN ('voice_session_no_legacy_insert','voice_session_guard','voice_session_response_guard','voice_session_audio_guard') AND NOT tgisinternal
      UNION ALL SELECT 'fn:'||proname||':'||pg_get_functiondef(oid) FROM pg_proc WHERE oid IN (to_regprocedure('public.voice_session_block_legacy()'),to_regprocedure('public.voice_session_guard()'),to_regprocedure('public.voice_session_response_guard()'),to_regprocedure('public.voice_session_audio_guard()'))
    ) shape;
    IF observed<>expected THEN RAISE EXCEPTION 'voice_session_schema_mismatch' USING ERRCODE='23514'; END IF;
  END IF;
END $preflight$;

CREATE TABLE IF NOT EXISTS saas_voice_session_budget (
 session_id UUID PRIMARY KEY, tenant_id UUID NOT NULL, actor_id BIGINT NOT NULL,
 channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, executor TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL, response_limit SMALLINT NOT NULL CHECK(response_limit IN (1,2)),
 asr_mode TEXT NOT NULL CHECK(asr_mode='off'), model TEXT NOT NULL, quote_version TEXT NOT NULL,
 fingerprint TEXT NOT NULL, reserved_microusd BIGINT NOT NULL CHECK(reserved_microusd>0),
 actual_microusd BIGINT CHECK(actual_microusd IS NULL OR actual_microusd>=0),
 accounting_conflict BOOLEAN NOT NULL DEFAULT false,
 tenant_reservation_id UUID NOT NULL, provider_session_id TEXT, config_fingerprint TEXT,
 audio_bytes BIGINT NOT NULL DEFAULT 0 CHECK(audio_bytes BETWEEN 0 AND 5760000),
 audio_frames INTEGER NOT NULL DEFAULT 0 CHECK(audio_frames BETWEEN 0 AND 1200),
 state TEXT NOT NULL CHECK(state IN ('reserved','opening','ready','uncertain','closed','released')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,session_id), FOREIGN KEY(tenant_id,tenant_reservation_id) REFERENCES saas_ai_spend_reservations(tenant_id,id)
);
CREATE UNIQUE INDEX IF NOT EXISTS saas_voice_session_active_idx ON saas_voice_session_budget(tenant_id,actor_id,channel_id,conversation_id)
 WHERE state IN ('reserved','opening','ready','uncertain');
CREATE TABLE IF NOT EXISTS saas_voice_session_response (
 response_id UUID PRIMARY KEY, session_id UUID NOT NULL REFERENCES saas_voice_session_budget(session_id),
 ordinal SMALLINT NOT NULL CHECK(ordinal IN (1,2)), scope_fingerprint TEXT NOT NULL,
 provider_response_id TEXT,
 state TEXT NOT NULL CHECK(state IN ('claimed','dispatching','uncertain','completed')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(session_id,ordinal), UNIQUE(session_id,provider_response_id)
);
CREATE TABLE IF NOT EXISTS saas_voice_session_audio (
 chunk_id UUID PRIMARY KEY, session_id UUID NOT NULL REFERENCES saas_voice_session_budget(session_id),
 byte_count INTEGER NOT NULL CHECK(byte_count BETWEEN 2 AND 9600 AND byte_count % 2 = 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS saas_voice_session_schema_version (
 component TEXT PRIMARY KEY CHECK(component='session'),version SMALLINT NOT NULL CHECK(version=1),signature TEXT NOT NULL
);

CREATE OR REPLACE FUNCTION public.voice_session_block_legacy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'voice_session_legacy_insert_blocked' USING ERRCODE='23514'; END $$;
CREATE OR REPLACE FUNCTION public.voice_session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'voice_session_no_delete' USING ERRCODE='23514'; END IF;
 IF (to_jsonb(NEW)-'state'-'actual_microusd'-'accounting_conflict'-'provider_session_id'-'config_fingerprint'-'audio_bytes'-'audio_frames'-'updated_at') <>
    (to_jsonb(OLD)-'state'-'actual_microusd'-'accounting_conflict'-'provider_session_id'-'config_fingerprint'-'audio_bytes'-'audio_frames'-'updated_at') OR
    (OLD.actual_microusd IS NOT NULL AND (NEW.actual_microusd IS NULL OR NEW.actual_microusd<OLD.actual_microusd)) OR
    (OLD.accounting_conflict AND NOT NEW.accounting_conflict) OR
    NEW.audio_bytes<OLD.audio_bytes OR NEW.audio_frames<OLD.audio_frames OR
    ((NEW.audio_bytes<>OLD.audio_bytes OR NEW.audio_frames<>OLD.audio_frames) AND
      NOT (OLD.state='ready' AND NEW.state='ready' AND NEW.audio_frames=OLD.audio_frames+1 AND
           NEW.audio_bytes-OLD.audio_bytes BETWEEN 2 AND 9600 AND (NEW.audio_bytes-OLD.audio_bytes)%2=0)) OR
    (OLD.provider_session_id IS NOT NULL AND NEW.provider_session_id IS DISTINCT FROM OLD.provider_session_id) OR
    (OLD.config_fingerprint IS NOT NULL AND NEW.config_fingerprint IS DISTINCT FROM OLD.config_fingerprint) OR
    ((NEW.provider_session_id IS DISTINCT FROM OLD.provider_session_id OR NEW.config_fingerprint IS DISTINCT FROM OLD.config_fingerprint)
      AND NOT (OLD.state='opening' AND NEW.state='ready' AND NEW.provider_session_id IS NOT NULL AND NEW.config_fingerprint IS NOT NULL)) OR
    NOT ((OLD.state='reserved' AND NEW.state IN ('reserved','opening','released')) OR
         (OLD.state='opening' AND NEW.state IN ('opening','ready','uncertain')) OR
         (OLD.state='ready' AND NEW.state IN ('ready','uncertain','closed')) OR
         (OLD.state='uncertain' AND NEW.state='uncertain') OR
         (OLD.state='closed' AND NEW.state='closed') OR
         (OLD.state='released' AND NEW.state='released')) THEN
   RAISE EXCEPTION 'voice_session_immutable_or_transition_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION public.voice_session_audio_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'voice_audio_receipt_immutable' USING ERRCODE='23514'; END $$;
CREATE OR REPLACE FUNCTION public.voice_session_response_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'voice_response_no_delete' USING ERRCODE='23514'; END IF;
 IF (to_jsonb(NEW)-'state'-'provider_response_id'-'updated_at')<>(to_jsonb(OLD)-'state'-'provider_response_id'-'updated_at') OR
    (OLD.provider_response_id IS NOT NULL AND NEW.provider_response_id IS DISTINCT FROM OLD.provider_response_id) OR
    (NEW.provider_response_id IS DISTINCT FROM OLD.provider_response_id AND NOT (OLD.state='dispatching' AND NEW.state='completed' AND NEW.provider_response_id IS NOT NULL)) OR
    NOT ((OLD.state='claimed' AND NEW.state IN ('claimed','dispatching','uncertain')) OR
         (OLD.state='dispatching' AND NEW.state IN ('dispatching','uncertain','completed')) OR
         (OLD.state='uncertain' AND NEW.state='uncertain') OR
         (OLD.state='completed' AND NEW.state='completed')) THEN
   RAISE EXCEPTION 'voice_response_immutable_or_transition_invalid' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='saas_voice_test_hold'::regclass AND tgname='voice_session_no_legacy_insert' AND NOT tgisinternal) THEN
   CREATE TRIGGER voice_session_no_legacy_insert BEFORE INSERT ON saas_voice_test_hold
     FOR EACH ROW EXECUTE FUNCTION public.voice_session_block_legacy();
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='saas_voice_session_budget'::regclass AND tgname='voice_session_guard' AND NOT tgisinternal) THEN
   CREATE TRIGGER voice_session_guard BEFORE UPDATE OR DELETE ON saas_voice_session_budget
     FOR EACH ROW EXECUTE FUNCTION public.voice_session_guard();
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='saas_voice_session_response'::regclass AND tgname='voice_session_response_guard' AND NOT tgisinternal) THEN
   CREATE TRIGGER voice_session_response_guard BEFORE UPDATE OR DELETE ON saas_voice_session_response
     FOR EACH ROW EXECUTE FUNCTION public.voice_session_response_guard();
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='saas_voice_session_audio'::regclass AND tgname='voice_session_audio_guard' AND NOT tgisinternal) THEN
   CREATE TRIGGER voice_session_audio_guard BEFORE UPDATE OR DELETE ON saas_voice_session_audio
     FOR EACH ROW EXECUTE FUNCTION public.voice_session_audio_guard();
 END IF;
END $$;

INSERT INTO saas_voice_session_schema_version(component,version,signature)
SELECT 'session',1,md5(COALESCE(string_agg(line,E'\n' ORDER BY line),'')) FROM (
 SELECT 'col:'||c.relname||':'||a.attname||':'||format_type(a.atttypid,a.atttypmod)||':'||a.attnotnull::text||':'||a.atthasdef::text||':'||COALESCE(pg_get_expr(d.adbin,d.adrelid),'') AS line
  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE c.oid IN ('saas_voice_session_budget'::regclass,'saas_voice_session_response'::regclass,'saas_voice_session_audio'::regclass) AND a.attnum>0 AND NOT a.attisdropped
 UNION ALL SELECT 'con:'||conrelid::regclass::text||':'||conname||':'||pg_get_constraintdef(oid)
  FROM pg_constraint WHERE conrelid IN ('saas_voice_session_budget'::regclass,'saas_voice_session_response'::regclass,'saas_voice_session_audio'::regclass)
 UNION ALL SELECT 'idx:'||indexrelid::regclass::text||':'||pg_get_indexdef(indexrelid)
  FROM pg_index WHERE indrelid IN ('saas_voice_session_budget'::regclass,'saas_voice_session_response'::regclass,'saas_voice_session_audio'::regclass)
 UNION ALL SELECT 'trg:'||tgname||':'||pg_get_triggerdef(oid)||':'||tgenabled::text
  FROM pg_trigger WHERE tgrelid IN ('saas_voice_test_hold'::regclass,'saas_voice_session_budget'::regclass,'saas_voice_session_response'::regclass,'saas_voice_session_audio'::regclass)
   AND tgname IN ('voice_session_no_legacy_insert','voice_session_guard','voice_session_response_guard','voice_session_audio_guard') AND NOT tgisinternal
 UNION ALL SELECT 'fn:'||proname||':'||pg_get_functiondef(oid) FROM pg_proc WHERE oid IN (to_regprocedure('public.voice_session_block_legacy()'),to_regprocedure('public.voice_session_guard()'),to_regprocedure('public.voice_session_response_guard()'),to_regprocedure('public.voice_session_audio_guard()'))
) shape
ON CONFLICT(component) DO NOTHING;
