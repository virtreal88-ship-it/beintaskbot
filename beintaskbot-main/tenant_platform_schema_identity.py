"""Additive PostgreSQL migration slice; executed inside the shared transaction."""

def migrate_identity_schema(cur) -> None:
    # Email identities are separate from provider identities.  The existing
    # integer member key remains in place so Telegram-only accounts and every
    # tenant-scoped API continue to work unchanged.
    cur.execute("CREATE SEQUENCE IF NOT EXISTS saas_auth_actor_seq AS BIGINT START WITH 1000000000000")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saas_auth_users (
            id UUID PRIMARY KEY,
            actor_id BIGINT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            company_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL DEFAULT '',
            tenant_id UUID NULL,
            verified_at TIMESTAMPTZ NULL,
            disabled_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saas_auth_tokens (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES saas_auth_users(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL CHECK(purpose IN ('confirm','reset','link_email')),
            token_hash TEXT NOT NULL UNIQUE,
            request_ip TEXT NOT NULL DEFAULT '',
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS saas_auth_tokens_rate_idx ON saas_auth_tokens(request_ip,created_at DESC)")
    # Product is presentation/continuation context only; it never grants an
    # entitlement. Existing tokens remain valid and default to Beintaskbot.
    cur.execute("ALTER TABLE saas_auth_tokens ADD COLUMN IF NOT EXISTS destination_product TEXT NOT NULL DEFAULT 'beintaskbot'")
    cur.execute("CREATE INDEX IF NOT EXISTS saas_auth_tokens_user_idx ON saas_auth_tokens(user_id,purpose,created_at DESC)")
    # Account fields are additive so existing Telegram identities remain valid.
    for column, definition in (
        ("first_name", "TEXT NOT NULL DEFAULT ''"),
        ("last_name", "TEXT NOT NULL DEFAULT ''"),
        ("login", "TEXT NOT NULL DEFAULT ''"),
        ("avatar_url", "TEXT NOT NULL DEFAULT ''"),
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("last_login_at", "TIMESTAMPTZ NULL"),
    ):
        cur.execute(f"ALTER TABLE saas_auth_users ADD COLUMN IF NOT EXISTS {column} {definition}")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS saas_auth_users_login_idx ON saas_auth_users(lower(login)) WHERE login <> ''")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saas_user_profiles (
            actor_id BIGINT PRIMARY KEY,
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            avatar_url TEXT NOT NULL DEFAULT '',
            login TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS saas_user_profiles_login_idx ON saas_user_profiles(lower(login)) WHERE login <> ''")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saas_auth_login_attempts (
            id UUID PRIMARY KEY,
            email TEXT NOT NULL,
            request_ip TEXT NOT NULL DEFAULT '',
            succeeded BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS saas_auth_login_rate_idx ON saas_auth_login_attempts(email,request_ip,created_at DESC)")
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
        CREATE TABLE IF NOT EXISTS saas_tenant_browser_sessions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
            actor_id BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS saas_browser_sessions_actor_idx ON saas_tenant_browser_sessions(tenant_id,actor_id,expires_at DESC) WHERE revoked_at IS NULL")
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
    cur.execute("ALTER TABLE saas_tenant_members ADD CONSTRAINT saas_tenant_members_role_check CHECK (role IN ('owner', 'admin', 'manager', 'operator', 'worker', 'master'))")
    cur.execute("ALTER TABLE saas_tenant_members ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ NULL")
    # Explicit WhatsApp identities are tenant-owned membership metadata. Keep
    # them outside account phone fields: a login/recovery phone is not
    # automatically proof that its owner is a WhatsApp group participant.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saas_tenant_member_whatsapp_numbers (
            tenant_id UUID NOT NULL,
            telegram_id BIGINT NOT NULL,
            phone_digits TEXT NOT NULL CHECK (phone_digits ~ '^[1-9][0-9]{7,14}$'),
            recognition_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, phone_digits),
            FOREIGN KEY (tenant_id, telegram_id) REFERENCES saas_tenant_members(tenant_id, telegram_id) ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS saas_tenant_member_whatsapp_member_idx ON saas_tenant_member_whatsapp_numbers(tenant_id,telegram_id)")
    cur.execute("ALTER TABLE saas_tenant_invites DROP CONSTRAINT IF EXISTS saas_tenant_invites_role_check")
    cur.execute("ALTER TABLE saas_tenant_invites ADD CONSTRAINT saas_tenant_invites_role_check CHECK (role IN ('admin', 'manager', 'operator', 'worker', 'master'))")
    cur.execute("ALTER TABLE saas_tenant_invites ADD COLUMN IF NOT EXISTS invited_email TEXT NOT NULL DEFAULT ''")
    cur.execute("ALTER TABLE saas_auth_users ADD COLUMN IF NOT EXISTS pending_invite_id UUID NULL")
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
