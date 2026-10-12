"""Additive PostgreSQL migration slice; executed inside the shared transaction."""

def migrate_operations_schema(cur) -> None:
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
