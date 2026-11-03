"""Additive schema for explicitly attributed customer memory; no activation."""


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_links (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      contact_id UUID NOT NULL, channel_id UUID NOT NULL, conversation_id TEXT NOT NULL,
      lead_id BIGINT NOT NULL, crm_link_revision BIGINT NOT NULL DEFAULT -1,
      subject_token TEXT NOT NULL DEFAULT '', subject_key_id TEXT NOT NULL DEFAULT '', route_token TEXT NOT NULL DEFAULT '', state TEXT NOT NULL CHECK (state IN ('confirmed','revoked')),
      version BIGINT NOT NULL DEFAULT 1, confirmed_by BIGINT NOT NULL, confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      revoked_by BIGINT, revoked_at TIMESTAMPTZ,
      UNIQUE (tenant_id,id,contact_id),
      FOREIGN KEY (tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id) ON DELETE CASCADE,
      CHECK (state <> 'revoked' OR revoked_at IS NOT NULL))''')
    cur.execute("ALTER TABLE saas_customer_memory_links ADD COLUMN IF NOT EXISTS crm_link_revision BIGINT NOT NULL DEFAULT -1")
    cur.execute("ALTER TABLE saas_customer_memory_links ADD COLUMN IF NOT EXISTS subject_token TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_customer_memory_links ADD COLUMN IF NOT EXISTS subject_key_id TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_customer_memory_links ADD COLUMN IF NOT EXISTS route_token TEXT NOT NULL DEFAULT ''")
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_erased_subjects (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      subject_token TEXT NOT NULL, token_key_id TEXT NOT NULL,
      PRIMARY KEY(tenant_id,subject_token,token_key_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_erased_channels (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, channel_id UUID NOT NULL,
      PRIMARY KEY(tenant_id,channel_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_erased_routes (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      route_token TEXT NOT NULL, token_key_id TEXT NOT NULL,
      PRIMARY KEY(tenant_id,route_token,token_key_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_erased_sources (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      source_token TEXT NOT NULL, token_key_id TEXT NOT NULL,
      PRIMARY KEY(tenant_id,source_token,token_key_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_subject_revisions (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, contact_id UUID NOT NULL,
      revision BIGINT NOT NULL DEFAULT 1, erasure_epoch BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(tenant_id,contact_id),
      FOREIGN KEY(tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id) ON DELETE CASCADE)''')
    cur.execute("ALTER TABLE saas_customer_memory_subject_revisions ADD COLUMN IF NOT EXISTS erasure_epoch BIGINT NOT NULL DEFAULT 0")
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_source_generations (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, contact_id UUID NOT NULL,
      source_message_id UUID NOT NULL, erasure_epoch BIGINT NOT NULL,
      PRIMARY KEY(tenant_id,contact_id,source_message_id),
      FOREIGN KEY(tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id) ON DELETE CASCADE)''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_customer_memory_route_source_lookup
      ON saas_customer_memory_links(tenant_id,lead_id) WHERE state='confirmed' ''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_crm_link_revisions (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, provider TEXT NOT NULL,
      channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, revision BIGINT NOT NULL DEFAULT 1,
      PRIMARY KEY(tenant_id,provider,channel_id,conversation_id))''')
    cur.execute("""CREATE OR REPLACE FUNCTION customer_memory_crm_link_changed() RETURNS trigger
      LANGUAGE plpgsql AS $$ DECLARE key_changed boolean; BEGIN
        IF TG_OP='UPDATE' THEN
          key_changed := (OLD.tenant_id,OLD.provider,OLD.channel_id,OLD.conversation_id)
            IS DISTINCT FROM (NEW.tenant_id,NEW.provider,NEW.channel_id,NEW.conversation_id);
          IF NOT key_changed AND OLD.contact_id IS NOT DISTINCT FROM NEW.contact_id THEN RETURN NEW; END IF;
        ELSE key_changed:=false; END IF;
        IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND key_changed) THEN
          INSERT INTO saas_customer_memory_crm_link_revisions(tenant_id,provider,channel_id,conversation_id,revision)
            VALUES(OLD.tenant_id,OLD.provider,OLD.channel_id,OLD.conversation_id,1)
          ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE
            SET revision=saas_customer_memory_crm_link_revisions.revision+1;
        END IF;
        IF TG_OP='INSERT' OR TG_OP='UPDATE' THEN
          INSERT INTO saas_customer_memory_crm_link_revisions(tenant_id,provider,channel_id,conversation_id,revision)
            VALUES(NEW.tenant_id,NEW.provider,NEW.channel_id,NEW.conversation_id,1)
          ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE
            SET revision=saas_customer_memory_crm_link_revisions.revision+1;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END $$""")
    cur.execute("""CREATE OR REPLACE TRIGGER customer_memory_crm_link_changed
      AFTER INSERT OR UPDATE OF tenant_id,provider,channel_id,conversation_id,contact_id OR DELETE
      ON saas_crm_native_contact_conversations FOR EACH ROW EXECUTE FUNCTION customer_memory_crm_link_changed()""")
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_customer_memory_active_route
      ON saas_customer_memory_links(tenant_id,channel_id,conversation_id) WHERE state='confirmed' ''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_customer_memory_active_route_token
      ON saas_customer_memory_links(tenant_id,channel_id,route_token) WHERE state='confirmed' AND route_token<>'' ''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_facts (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      contact_id UUID NOT NULL, link_id UUID NOT NULL, link_version BIGINT NOT NULL,
      source_message_id UUID NOT NULL, source_version TEXT NOT NULL,
      source_token TEXT NOT NULL DEFAULT '', token_key_id TEXT NOT NULL DEFAULT '', claim_token TEXT NOT NULL DEFAULT '', claim_group_id UUID NULL, evidence_quote_token TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL CHECK(category IN ('profile','feature','topic','problem','agreement')),
      value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 1200),
      source_kind TEXT NOT NULL CHECK(source_kind IN ('customer_statement','employee_observation','technical_result')),
      reliability TEXT NOT NULL CHECK(reliability IN ('confirmed','needs_review','outdated','refuted')),
      event_at TIMESTAMPTZ, observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      valid_from TIMESTAMPTZ, valid_to TIMESTAMPTZ, last_confirmed_at TIMESTAMPTZ,
      version BIGINT NOT NULL DEFAULT 1, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id,link_id,contact_id) REFERENCES saas_customer_memory_links(tenant_id,id,contact_id) ON DELETE CASCADE,
      CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from))''')
    cur.execute("ALTER TABLE saas_customer_memory_facts ADD COLUMN IF NOT EXISTS source_token TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_customer_memory_facts ADD COLUMN IF NOT EXISTS token_key_id TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_customer_memory_facts ADD COLUMN IF NOT EXISTS claim_token TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_customer_memory_facts ADD COLUMN IF NOT EXISTS claim_group_id UUID NULL")
    cur.execute("ALTER TABLE saas_customer_memory_facts ADD COLUMN IF NOT EXISTS evidence_quote_token TEXT NOT NULL DEFAULT ''")
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_customer_memory_source_lookup
      ON saas_customer_memory_facts(tenant_id,source_message_id)''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_customer_memory_contact_facts
      ON saas_customer_memory_facts(tenant_id,contact_id,updated_at DESC,id)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_fact_versions (
      fact_id UUID NOT NULL REFERENCES saas_customer_memory_facts(id) ON DELETE CASCADE,
      version BIGINT NOT NULL, value TEXT NOT NULL, reliability TEXT NOT NULL, claim_token TEXT NOT NULL DEFAULT '', evidence_quote_token TEXT NOT NULL DEFAULT '',
      actor BIGINT NOT NULL, changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(fact_id,version))''')
    cur.execute("ALTER TABLE saas_customer_memory_fact_versions ADD COLUMN IF NOT EXISTS claim_token TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_customer_memory_fact_versions ADD COLUMN IF NOT EXISTS evidence_quote_token TEXT NOT NULL DEFAULT ''")
    # Identifiers only: do not retain deleted values, their hashes or secrets.
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_suppressions (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      contact_id UUID NOT NULL, source_message_id UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      source_token TEXT NOT NULL DEFAULT '', token_key_id TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(tenant_id,contact_id,source_message_id),
      FOREIGN KEY (tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id) ON DELETE CASCADE)''')

    cur.execute("ALTER TABLE saas_customer_memory_suppressions ADD COLUMN IF NOT EXISTS source_token TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_customer_memory_suppressions ADD COLUMN IF NOT EXISTS token_key_id TEXT NOT NULL DEFAULT ''")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_customer_memory_suppressed_token ON saas_customer_memory_suppressions(tenant_id,contact_id,source_token) WHERE source_token<>''")
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_claim_suppressions (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, contact_id UUID NOT NULL,
      claim_token TEXT NOT NULL, token_key_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,contact_id,claim_token),
      FOREIGN KEY(tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id) ON DELETE CASCADE)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_group_fences (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, contact_id UUID NOT NULL,
      claim_group_id UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,contact_id,claim_group_id),
      FOREIGN KEY(tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id) ON DELETE CASCADE)''')
    # Deleting source evidence must atomically erase its derivatives. Keep only
    # opaque IDs to fence replay; value history disappears via ON DELETE CASCADE.
    cur.execute("""CREATE OR REPLACE FUNCTION customer_memory_source_deleted() RETURNS trigger
      LANGUAGE plpgsql AS $$ DECLARE item record; BEGIN
        FOR item IN SELECT tenant_id,contact_id,source_message_id,
          CASE WHEN count(DISTINCT source_token)=1 AND count(DISTINCT token_key_id)=1 THEN max(source_token) ELSE '' END AS source_token,
          CASE WHEN count(DISTINCT source_token)=1 AND count(DISTINCT token_key_id)=1 THEN max(token_key_id) ELSE '' END AS token_key_id
          FROM saas_customer_memory_facts WHERE tenant_id=OLD.tenant_id AND source_message_id=OLD.id
          GROUP BY tenant_id,contact_id,source_message_id ORDER BY tenant_id,contact_id,source_message_id LOOP
          BEGIN
            -- Contact deletion takes the contact row before cascading into the
            -- revision table. Use the same order to avoid an FK lock cycle.
            PERFORM 1 FROM saas_crm_native_contacts c WHERE c.tenant_id=item.tenant_id
              AND c.id=item.contact_id FOR KEY SHARE;
            IF NOT FOUND THEN CONTINUE; END IF;
            INSERT INTO saas_customer_memory_subject_revisions(tenant_id,contact_id,revision)
              VALUES(item.tenant_id,item.contact_id,1) ON CONFLICT(tenant_id,contact_id) DO UPDATE
              SET revision=saas_customer_memory_subject_revisions.revision+1,
                  erasure_epoch=saas_customer_memory_subject_revisions.erasure_epoch+1;
            -- A source erasure fences all future writes for this subject until
            -- a reviewed recovery flow can distinguish independent evidence.
            INSERT INTO saas_customer_memory_claim_suppressions(tenant_id,contact_id,claim_token,token_key_id)
              VALUES(item.tenant_id,item.contact_id,'','') ON CONFLICT(tenant_id,contact_id,claim_token)
              DO UPDATE SET created_at=clock_timestamp();
            INSERT INTO saas_customer_memory_group_fences(tenant_id,contact_id,claim_group_id)
              SELECT DISTINCT tenant_id,contact_id,claim_group_id FROM saas_customer_memory_facts
              WHERE tenant_id=item.tenant_id AND contact_id=item.contact_id
                AND source_message_id=item.source_message_id AND claim_group_id IS NOT NULL
              ON CONFLICT DO NOTHING;
            INSERT INTO saas_customer_memory_suppressions(tenant_id,contact_id,source_message_id,source_token,token_key_id)
              VALUES(item.tenant_id,item.contact_id,item.source_message_id,item.source_token,item.token_key_id)
            ON CONFLICT(tenant_id,contact_id,source_message_id) DO UPDATE SET
              source_token=CASE WHEN saas_customer_memory_suppressions.source_token=EXCLUDED.source_token AND saas_customer_memory_suppressions.token_key_id=EXCLUDED.token_key_id THEN EXCLUDED.source_token ELSE '' END,
              token_key_id=CASE WHEN saas_customer_memory_suppressions.source_token=EXCLUDED.source_token AND saas_customer_memory_suppressions.token_key_id=EXCLUDED.token_key_id THEN EXCLUDED.token_key_id ELSE '' END;
          EXCEPTION WHEN foreign_key_violation THEN
            -- Concurrent personal-data deletion makes this subject disappear;
            -- no suppression for that deleted subject can or should survive.
            IF EXISTS(SELECT 1 FROM saas_crm_native_contacts c WHERE c.tenant_id=item.tenant_id AND c.id=item.contact_id) THEN RAISE; END IF;
          END;
        END LOOP;
        DELETE FROM saas_customer_memory_facts WHERE tenant_id=OLD.tenant_id AND source_message_id=OLD.id;
        DELETE FROM saas_customer_memory_source_generations WHERE tenant_id=OLD.tenant_id AND source_message_id=OLD.id;
        RETURN OLD;
      END $$""")
    cur.execute("""DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='customer_memory_source_deleted'
        AND tgrelid='saas_crm_messages'::regclass AND NOT tgisinternal) THEN
        CREATE TRIGGER customer_memory_source_deleted AFTER DELETE ON saas_crm_messages
          FOR EACH ROW EXECUTE FUNCTION customer_memory_source_deleted();
      END IF;
      END $$""")

    # Capture only a non-content revision for new Connect messages. A source
    # inserted before an erasure commit observes the earlier epoch and cannot
    # later be treated as new evidence, even if its worker commits afterwards.
    cur.execute("""CREATE OR REPLACE FUNCTION customer_memory_source_received() RETURNS trigger
      LANGUAGE plpgsql AS $$ DECLARE old_timeout text; BEGIN
        IF NEW.channel NOT LIKE 'connect:%' THEN RETURN NEW; END IF;
        old_timeout:=current_setting('lock_timeout');
        BEGIN
          IF old_timeout='0' OR old_timeout::interval > interval '50 milliseconds' THEN
            PERFORM set_config('lock_timeout','50ms',true);
          END IF;
          INSERT INTO saas_customer_memory_source_generations(tenant_id,contact_id,source_message_id,erasure_epoch)
          SELECT l.tenant_id,l.contact_id,NEW.id,r.erasure_epoch FROM saas_customer_memory_links l
            JOIN saas_customer_memory_subject_revisions r ON r.tenant_id=l.tenant_id AND r.contact_id=l.contact_id
            WHERE l.tenant_id=NEW.tenant_id AND l.lead_id=NEW.kommo_lead_id AND l.state='confirmed'
              AND NEW.channel='connect:'||l.channel_id::text ON CONFLICT DO NOTHING;
        EXCEPTION WHEN OTHERS THEN
          -- Optional memory bookkeeping may not block existing message ingest.
          -- Missing generation later denies use as post-erasure evidence.
          NULL;
        END;
        PERFORM set_config('lock_timeout',old_timeout,true);
        RETURN NEW;
      END $$""")
    cur.execute("""DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='customer_memory_source_received'
        AND tgrelid='saas_crm_messages'::regclass AND NOT tgisinternal) THEN
        CREATE TRIGGER customer_memory_source_received AFTER INSERT ON saas_crm_messages
          FOR EACH ROW EXECUTE FUNCTION customer_memory_source_received();
      END IF;
      END $$""")

    # Retain only keyed, opaque erasure fences when a CRM contact is deleted.
    # They stop an old source from rehydrating memory under a recreated UUID or
    # a newly linked contact; key rotation remains closed until reconciled.
    cur.execute("""CREATE OR REPLACE FUNCTION customer_memory_contact_erased() RETURNS trigger
      LANGUAGE plpgsql AS $$ DECLARE locked_channel record; BEGIN
        IF NOT EXISTS(SELECT 1 FROM saas_tenants WHERE id=OLD.tenant_id) THEN RETURN OLD; END IF;
        -- Never wait while holding the CRM contact row: a concurrent link
        -- confirmation holds the channel lock before it checks this contact.
        FOR locked_channel IN SELECT DISTINCT channel_id FROM saas_customer_memory_links
          WHERE tenant_id=OLD.tenant_id AND contact_id=OLD.id ORDER BY channel_id LOOP
          IF NOT pg_try_advisory_xact_lock(hashtextextended('customer-memory-channel:'||OLD.tenant_id::text||':'||locked_channel.channel_id::text,0)) THEN
            RAISE EXCEPTION 'customer memory channel busy' USING ERRCODE='55P03';
          END IF;
        END LOOP;
        INSERT INTO saas_customer_memory_erased_subjects(tenant_id,subject_token,token_key_id)
          SELECT DISTINCT tenant_id,subject_token,subject_key_id FROM saas_customer_memory_links
          WHERE tenant_id=OLD.tenant_id AND contact_id=OLD.id ON CONFLICT DO NOTHING;
        -- Until alias-history recovery exists, fence the channel too: a later
        -- provider split must not erase the prior JID/LID relationship.
        INSERT INTO saas_customer_memory_erased_channels(tenant_id,channel_id)
          SELECT DISTINCT tenant_id,channel_id FROM saas_customer_memory_links
          WHERE tenant_id=OLD.tenant_id AND contact_id=OLD.id ON CONFLICT DO NOTHING;
        INSERT INTO saas_customer_memory_erased_routes(tenant_id,route_token,token_key_id)
          SELECT DISTINCT tenant_id,route_token,subject_key_id FROM saas_customer_memory_links
          WHERE tenant_id=OLD.tenant_id AND contact_id=OLD.id ON CONFLICT DO NOTHING;
        INSERT INTO saas_customer_memory_erased_sources(tenant_id,source_token,token_key_id)
          SELECT tenant_id,source_token,token_key_id FROM saas_customer_memory_facts
            WHERE tenant_id=OLD.tenant_id AND contact_id=OLD.id
          UNION SELECT tenant_id,source_token,token_key_id FROM saas_customer_memory_suppressions
            WHERE tenant_id=OLD.tenant_id AND contact_id=OLD.id ON CONFLICT DO NOTHING;
        RETURN OLD;
      END $$""")
    cur.execute("""DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='customer_memory_contact_erased'
        AND tgrelid='saas_crm_native_contacts'::regclass AND NOT tgisinternal) THEN
        CREATE TRIGGER customer_memory_contact_erased BEFORE DELETE ON saas_crm_native_contacts
          FOR EACH ROW EXECUTE FUNCTION customer_memory_contact_erased();
      END IF;
      END $$""")
