"""Additive PostgreSQL migration slice; executed inside the shared transaction."""

from tenant_crm_deletion_schema import migrate_crm_deletions

def migrate_crm_schema(cur) -> None:
    # New provider-neutral snapshot plane.  It intentionally coexists with
    # the numeric Kommo snapshot tables while existing chat/Talk views still
    # depend on Kommo lead IDs.  UUID based providers can start storing CRM
    # data immediately without a destructive primary-key migration.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saas_crm_provider_entities (
            tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
            provider TEXT NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_-]{0,63}$'),
            entity_type TEXT NOT NULL CHECK (entity_type ~ '^[a-z][a-z0-9_-]{0,63}$'),
            external_id TEXT NOT NULL CHECK (length(external_id) BETWEEN 1 AND 180),
            parent_entity_type TEXT NOT NULL DEFAULT '',
            parent_external_id TEXT NOT NULL DEFAULT '',
            source_updated_at TIMESTAMPTZ NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, provider, entity_type, external_id),
            CHECK ((parent_entity_type = '') = (parent_external_id = ''))
        )
    """)
    # Seed the common plane from the existing Kommo snapshots.  The operation
    # is idempotent and never overwrites a newer provider-neutral record.
    cur.execute("""
        INSERT INTO saas_crm_provider_entities
        (tenant_id, provider, entity_type, external_id, source_updated_at, data, synced_at)
        SELECT tenant_id, 'kommo', 'deals', kommo_lead_id::text, source_updated_at, raw, synced_at
        FROM saas_crm_deals
        ON CONFLICT (tenant_id, provider, entity_type, external_id) DO NOTHING
    """)
    cur.execute("""
        INSERT INTO saas_crm_provider_entities
        (tenant_id, provider, entity_type, external_id, parent_entity_type,
         parent_external_id, source_updated_at, data, synced_at)
        SELECT tenant_id, 'kommo', 'tasks', kommo_task_id::text,
               CASE WHEN kommo_lead_id > 0 THEN 'deals' ELSE '' END,
               CASE WHEN kommo_lead_id > 0 THEN kommo_lead_id::text ELSE '' END,
               NULL, raw, synced_at
        FROM saas_crm_tasks
        ON CONFLICT (tenant_id, provider, entity_type, external_id) DO NOTHING
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS crm_linear_news (
            id UUID PRIMARY KEY,
            source_issue_id TEXT NOT NULL UNIQUE,
            identifier TEXT NOT NULL DEFAULT '',
            project_key TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            account TEXT NOT NULL DEFAULT '',
            environment TEXT NOT NULL DEFAULT '',
            source_created_at TIMESTAMPTZ NULL,
            done_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            raw JSONB NOT NULL DEFAULT '{}'::jsonb,
            telegram_message_id BIGINT NULL,
            telegram_published_at TIMESTAMPTZ NULL,
            approval_status TEXT NOT NULL DEFAULT 'approved',
            media_url TEXT NOT NULL DEFAULT '',
            media_type TEXT NOT NULL DEFAULT '',
            reviewed_at TIMESTAMPTZ NULL,
            first_published_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Keep existing installations compatible with the Telegram news
    # publisher without requiring a destructive migration.
    cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT NULL")
    cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS telegram_published_at TIMESTAMPTZ NULL")
    cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'approved'")
    cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS media_url TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS media_type TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ NULL")
    cur.execute("ALTER TABLE crm_linear_news ADD COLUMN IF NOT EXISTS first_published_at TIMESTAMPTZ NULL")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saas_tenant_webhook_events (
            tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
            fingerprint TEXT NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, fingerprint)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS crm_linear_news_migrations (
            migration_key TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_deals_list_idx ON saas_crm_deals(tenant_id, pipeline_id, status_id, last_message_at DESC NULLS LAST, synced_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_provider_entities_list_idx ON saas_crm_provider_entities(tenant_id, provider, entity_type, source_updated_at DESC NULLS LAST, synced_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_provider_entities_parent_idx ON saas_crm_provider_entities(tenant_id, provider, parent_entity_type, parent_external_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_tasks_list_idx ON saas_crm_tasks(tenant_id, responsible_id, completed, due_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_messages_list_idx ON saas_crm_messages(tenant_id, kommo_lead_id, happened_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_notifications_list_idx ON saas_tenant_notifications(tenant_id, telegram_id, read_at, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS crm_linear_news_project_idx ON crm_linear_news(project_key, done_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS crm_linear_news_expiry_idx ON crm_linear_news(expires_at)")
    migrate_crm_deletions(cur)
