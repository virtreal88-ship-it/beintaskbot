"""Additive PostgreSQL migration slice; executed inside the shared transaction."""

from tenant_crm_deletion_schema import migrate_crm_deletions
from tenant_crm_identity_schema import migrate_crm_identity_schema

def migrate_crm_schema(cur) -> None:
    migrate_crm_identity_schema(cur)
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
    cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_tasks_list_idx ON saas_crm_tasks(tenant_id, responsible_id, completed, due_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_crm_messages_list_idx ON saas_crm_messages(tenant_id, kommo_lead_id, happened_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_notifications_list_idx ON saas_tenant_notifications(tenant_id, telegram_id, read_at, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS crm_linear_news_project_idx ON crm_linear_news(project_key, done_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS crm_linear_news_expiry_idx ON crm_linear_news(expires_at)")
    migrate_crm_deletions(cur)
