"""Additive PostgreSQL schema for the tenant SaaS platform."""
from threading import Lock

from tenant_crm_deletion_schema import migrate_crm_deletions

_schema_ready = False
_schema_lock = Lock()

def _ensure_schema(conn) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenants (
                    id UUID PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    industry TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'onboarding',
                    owner_telegram_id BIGINT NOT NULL,
                    modules JSONB NOT NULL DEFAULT '{}'::jsonb,
                    notification_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
                    onboarding JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_members (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    telegram_id BIGINT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL CHECK (role IN ('owner', 'manager', 'worker', 'master')),
                    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    invited_by BIGINT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, telegram_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_invites (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL CHECK (role IN ('manager', 'worker', 'master')),
                    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_by BIGINT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    accepted_by BIGINT NULL,
                    accepted_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # Existing SaaS databases were created before the master role was
            # introduced. Replace only the generated role constraints; data is
            # preserved and the migration is safe to run on every startup.
            cur.execute("ALTER TABLE saas_tenant_members DROP CONSTRAINT IF EXISTS saas_tenant_members_role_check")
            cur.execute("ALTER TABLE saas_tenant_members ADD CONSTRAINT saas_tenant_members_role_check CHECK (role IN ('owner', 'admin', 'manager', 'worker', 'master'))")
            cur.execute("ALTER TABLE saas_tenant_invites DROP CONSTRAINT IF EXISTS saas_tenant_invites_role_check")
            cur.execute("ALTER TABLE saas_tenant_invites ADD CONSTRAINT saas_tenant_invites_role_check CHECK (role IN ('admin', 'manager', 'worker', 'master'))")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_integrations (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'not_connected',
                    account_domain TEXT NOT NULL DEFAULT '',
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    connected_at TIMESTAMPTZ NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, provider)
                )
            """)
            cur.execute("ALTER TABLE saas_tenant_integrations ADD COLUMN IF NOT EXISTS secrets BYTEA NULL")
            # Tenant-owned workflow configuration. External Kommo IDs are
            # stored as text because providers do not share one identifier
            # type. Nothing in the runtime should need a customer-specific
            # Python constant after these tables are populated.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_pipelines (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    pipeline_id TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    owner_telegram_id BIGINT NULL,
                    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, pipeline_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_pipeline_stages (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    pipeline_id TEXT NOT NULL,
                    stage_id TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    stage_type TEXT NOT NULL DEFAULT 'open',
                    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, pipeline_id, stage_id),
                    FOREIGN KEY (tenant_id, pipeline_id) REFERENCES saas_tenant_pipelines(tenant_id, pipeline_id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_workflow_policies (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    policy_key TEXT NOT NULL,
                    value JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_by BIGINT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, policy_key)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS saas_tenant_pipeline_owner_idx ON saas_tenant_pipelines(tenant_id, owner_telegram_id, active)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_oauth_states (
                    id TEXT PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    nonce_hash TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS saas_member_telegram_idx ON saas_tenant_members(telegram_id, active)")
            cur.execute("CREATE INDEX IF NOT EXISTS saas_invite_active_idx ON saas_tenant_invites(token_hash, expires_at) WHERE accepted_at IS NULL")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_invite_requests (
                    id UUID PRIMARY KEY,
                    invite_id UUID NOT NULL REFERENCES saas_tenant_invites(id) ON DELETE CASCADE,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    telegram_id BIGINT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                    decided_by BIGINT NULL,
                    decided_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (invite_id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS saas_invite_request_tenant_idx ON saas_tenant_invite_requests(tenant_id, status, created_at DESC)")
            # CRM records for self-service companies are deliberately kept in
            # separate tables from the original BeinSystems workspace.  The
            # tenant id is part of every key, so an accidental query without
            # an application filter cannot join two companies' data.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_crm_deals (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    kommo_lead_id BIGINT NOT NULL,
                    pipeline_id BIGINT NOT NULL DEFAULT 0,
                    status_id BIGINT NOT NULL DEFAULT 0,
                    stage_name TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    contact_name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT '',
                    last_message TEXT NOT NULL DEFAULT '',
                    last_message_at TIMESTAMPTZ NULL,
                    source_updated_at TIMESTAMPTZ NULL,
                    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, kommo_lead_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_crm_tasks (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    kommo_task_id BIGINT NOT NULL,
                    kommo_lead_id BIGINT NOT NULL DEFAULT 0,
                    text TEXT NOT NULL DEFAULT '',
                    due_at TIMESTAMPTZ NULL,
                    responsible_id BIGINT NOT NULL DEFAULT 0,
                    completed BOOLEAN NOT NULL DEFAULT FALSE,
                    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, kommo_task_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_task_commands (
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    actor_id BIGINT NOT NULL,
                    request_id UUID NOT NULL,
                    fingerprint TEXT NOT NULL,
                    state JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, actor_id, request_id)
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS saas_task_commands_approval_cursor_idx
                ON saas_task_commands (tenant_id, created_at, actor_id, request_id)
                WHERE state ? 'approval' AND state->>'step' NOT IN ('done', 'rejected')
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_approval_notification_events (
                    tenant_id UUID NOT NULL,
                    actor_id BIGINT NOT NULL,
                    request_id UUID NOT NULL,
                    event TEXT NOT NULL CHECK (event IN ('task_approval_requested', 'task_completion_requested')),
                    expanded BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, actor_id, request_id),
                    FOREIGN KEY (tenant_id, actor_id, request_id)
                        REFERENCES saas_task_commands (tenant_id, actor_id, request_id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS saas_approval_events_pending_idx
                ON saas_approval_notification_events (created_at) WHERE expanded=FALSE
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_approval_notification_deliveries (
                    tenant_id UUID NOT NULL,
                    actor_id BIGINT NOT NULL,
                    request_id UUID NOT NULL,
                    recipient_id BIGINT NOT NULL,
                    channel TEXT NOT NULL CHECK (channel='telegram'),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sending', 'delivered', 'skipped', 'unknown')),
                    message_id BIGINT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, actor_id, request_id, recipient_id, channel),
                    FOREIGN KEY (tenant_id, actor_id, request_id)
                        REFERENCES saas_approval_notification_events (tenant_id, actor_id, request_id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS saas_approval_deliveries_pending_idx
                ON saas_approval_notification_deliveries (updated_at) WHERE status='pending'
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS saas_approval_deliveries_sending_idx
                ON saas_approval_notification_deliveries (updated_at) WHERE status='sending'
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_push_devices (
                    endpoint_hash TEXT PRIMARY KEY,
                    owner_telegram_id BIGINT NOT NULL,
                    subscription BYTEA NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_member_push_devices (
                    tenant_id UUID NOT NULL,
                    telegram_id BIGINT NOT NULL,
                    endpoint_hash TEXT NOT NULL REFERENCES saas_push_devices(endpoint_hash) ON DELETE CASCADE,
                    label TEXT NOT NULL DEFAULT '',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id,telegram_id,endpoint_hash),
                    FOREIGN KEY (tenant_id,telegram_id)
                        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_approval_push_deliveries (
                    tenant_id UUID NOT NULL,
                    actor_id BIGINT NOT NULL,
                    request_id UUID NOT NULL,
                    recipient_id BIGINT NOT NULL,
                    endpoint_hash TEXT NOT NULL REFERENCES saas_push_devices(endpoint_hash) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','sending','delivered','skipped','unknown','expired')),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id,actor_id,request_id,recipient_id,endpoint_hash),
                    FOREIGN KEY (tenant_id,actor_id,request_id)
                      REFERENCES saas_approval_notification_events(tenant_id,actor_id,request_id) ON DELETE CASCADE
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS saas_push_deliveries_work_idx
                ON saas_approval_push_deliveries(status,updated_at) WHERE status IN ('pending','sending')
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_crm_messages (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    kommo_lead_id BIGINT NOT NULL,
                    external_id TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'incoming',
                    channel TEXT NOT NULL DEFAULT '',
                    author_name TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    message_type TEXT NOT NULL DEFAULT 'text',
                    media_url TEXT NOT NULL DEFAULT '',
                    happened_at TIMESTAMPTZ NULL,
                    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (tenant_id, external_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_notifications (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    telegram_id BIGINT NOT NULL,
                    event_key TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    target_url TEXT NOT NULL DEFAULT '',
                    read_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS saas_tenant_audit_events (
                    id UUID PRIMARY KEY,
                    tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                    actor_telegram_id BIGINT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL DEFAULT '',
                    entity_id TEXT NOT NULL DEFAULT '',
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crm_linear_accounts (
                    id UUID PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            # Public product news is a durable snapshot of selected Linear
            # issues.  Linear issues may later be deleted or moved out of
            # Done, so the site keeps its own copy for a short, predictable
            # retention window.
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
        conn.commit()
        _schema_ready = True
