-- Apply after voice_lease_provider_open.sql and before any hybrid worker.
-- Old rows deliberately remain unknown; no provider history is inferred.
BEGIN;
SET LOCAL lock_timeout = '250ms';
ALTER TABLE saas_operator_voice_lease
  ADD COLUMN IF NOT EXISTS provider_kind text NOT NULL DEFAULT 'unknown';
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='saas_operator_voice_lease'::regclass
                 AND conname='voice_lease_provider_kind_valid') THEN
    ALTER TABLE saas_operator_voice_lease ADD CONSTRAINT voice_lease_provider_kind_valid
      CHECK (provider_kind IN ('unknown','openai_realtime','openai_elevenlabs_tts'));
  END IF;
END $$;

CREATE OR REPLACE FUNCTION protect_voice_lease_provider_kind()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  owner_changed boolean := (NEW.session_id IS DISTINCT FROM OLD.session_id OR
                            NEW.owner_id IS DISTINCT FROM OLD.owner_id OR
                            NEW.fence IS DISTINCT FROM OLD.fence);
  scope_changed boolean := (NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
                            NEW.actor_id IS DISTINCT FROM OLD.actor_id OR
                            NEW.channel_id IS DISTINCT FROM OLD.channel_id OR
                            NEW.conversation_id IS DISTINCT FROM OLD.conversation_id);
BEGIN
  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.actor_id IS DISTINCT FROM OLD.actor_id THEN
    RAISE EXCEPTION 'voice tenant and actor are immutable' USING ERRCODE='55000';
  END IF;
  IF scope_changed AND NOT owner_changed THEN
    RAISE EXCEPTION 'voice scope is immutable for owner' USING ERRCODE='55000';
  END IF;
  IF owner_changed OR scope_changed THEN
    -- Legacy workers may reclaim expired active rows. A marker from another
    -- provider cannot establish an OpenAI lifetime bound.
    IF OLD.state = 'starting' OR
       (OLD.state = 'active' AND (
          OLD.provider_kind <> 'openai_realtime' OR
          OLD.provider_opened_at IS NULL OR
          OLD.provider_opened_at > clock_timestamp() - interval '65 minutes')) THEN
      RAISE EXCEPTION 'voice provider ownership cannot be reclaimed' USING ERRCODE='55000';
    END IF;
    NEW.provider_opened_at := NULL;
    NEW.provider_kind := 'unknown';
  ELSE
    IF NEW.provider_opened_at IS DISTINCT FROM OLD.provider_opened_at THEN
      IF OLD.state <> 'starting' OR OLD.provider_opened_at IS NOT NULL OR
         NEW.state <> 'active' OR NEW.provider_opened_at IS DISTINCT FROM transaction_timestamp() THEN
        RAISE EXCEPTION 'voice provider open marker is immutable' USING ERRCODE='55000';
      END IF;
    END IF;
    IF NEW.provider_kind IS DISTINCT FROM OLD.provider_kind THEN
    -- Only a newly claimed owner, before provider startup, may establish the
    -- kind once. Unknown legacy owners that have opened remain fail-closed.
    IF OLD.provider_kind <> 'unknown' OR OLD.state <> 'starting' OR
       NEW.state <> 'starting' OR OLD.provider_opened_at IS NOT NULL OR
       NEW.provider_opened_at IS NOT NULL THEN
      RAISE EXCEPTION 'voice provider kind is immutable' USING ERRCODE='55000';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS protect_voice_lease_provider_kind ON saas_operator_voice_lease;
CREATE TRIGGER protect_voice_lease_provider_kind BEFORE UPDATE ON saas_operator_voice_lease
  FOR EACH ROW EXECUTE FUNCTION protect_voice_lease_provider_kind();
COMMIT;
