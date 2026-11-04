-- Review-only expansion: run separately before new writers. NOT an automatic
-- production migration. Fail promptly rather than block text indefinitely.
BEGIN;
SET LOCAL lock_timeout = '250ms';
ALTER TABLE saas_operator_copilot_threads
  ADD COLUMN IF NOT EXISTS clear_revision bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS history_revision bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS voice_history_seen boolean NOT NULL DEFAULT false;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_attribute a JOIN pg_attrdef d
      ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE a.attrelid='saas_operator_copilot_threads'::regclass
      AND a.attname='clear_revision' AND a.attnum>0 AND NOT a.attisdropped
      AND a.atttypid='pg_catalog.int8'::regtype AND a.attnotnull
      AND pg_get_expr(d.adbin,d.adrelid) IN ('0','0::bigint','(0)::bigint')
  ) THEN
    RAISE EXCEPTION 'incompatible clear_revision schema';
  END IF;
END $$;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_attribute a JOIN pg_attrdef d
      ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE a.attrelid='saas_operator_copilot_threads'::regclass
      AND a.attname='history_revision' AND a.attnum>0 AND NOT a.attisdropped
      AND a.atttypid='pg_catalog.int8'::regtype AND a.attnotnull
      AND pg_get_expr(d.adbin,d.adrelid) IN ('0','0::bigint','(0)::bigint')
  ) THEN
    RAISE EXCEPTION 'incompatible history_revision schema';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_attribute a JOIN pg_attrdef d
      ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE a.attrelid='saas_operator_copilot_threads'::regclass
      AND a.attname='voice_history_seen' AND a.attnum>0 AND NOT a.attisdropped
      AND a.atttypid='pg_catalog.bool'::regtype AND a.attnotnull
      AND pg_get_expr(d.adbin,d.adrelid) IN ('false','false::boolean')
  ) THEN
    RAISE EXCEPTION 'incompatible voice_history_seen schema';
  END IF;
END $$;
CREATE OR REPLACE FUNCTION saas_operator_copilot_history_compat_fn()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='UPDATE' THEN
    -- A writer which does not advance history_revision is an old
    -- whole-snapshot writer. Let it continue only on untouched text threads.
    IF NEW.history_revision=OLD.history_revision THEN
      IF OLD.voice_history_seen OR OLD.clear_revision>0 THEN
        RAISE EXCEPTION 'legacy copilot writer fenced' USING ERRCODE='40001';
      END IF;
      NEW.history_revision:=OLD.history_revision+1;
    ELSIF NEW.history_revision<>OLD.history_revision+1 THEN
      RAISE EXCEPTION 'invalid copilot history revision' USING ERRCODE='40001';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(NEW.turns,'[]'::jsonb)) AS e(value)
               WHERE e.value ? 'voice_session_id') THEN
      NEW.voice_history_seen:=true;
    END IF;
    RETURN NEW;
  END IF;

  -- Old clear/retention paths issue a physical DELETE. Once a clear fence or
  -- voice session exists, convert that request to a durable scrub tombstone.
  IF OLD.voice_history_seen OR OLD.clear_revision>0 OR
     EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(OLD.turns,'[]'::jsonb)) AS e(value)
             WHERE e.value ? 'voice_session_id') THEN
    UPDATE saas_operator_copilot_threads SET turns='[]'::jsonb,
      clear_revision=clear_revision+1,history_revision=history_revision+1,
      updated_at=now()
      WHERE tenant_id=OLD.tenant_id AND requested_by=OLD.requested_by
        AND channel_id=OLD.channel_id AND conversation_id=OLD.conversation_id;
    RETURN NULL;
  END IF;
  RETURN OLD;
END;
$$;
DROP TRIGGER IF EXISTS saas_operator_copilot_history_compat ON saas_operator_copilot_threads;
CREATE TRIGGER saas_operator_copilot_history_compat
  BEFORE UPDATE OF turns OR DELETE ON saas_operator_copilot_threads
  FOR EACH ROW EXECUTE FUNCTION saas_operator_copilot_history_compat_fn();
COMMIT;
