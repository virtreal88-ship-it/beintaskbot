"""Tenant-scoped, auditable manual relations to Operator identities.

The existing CRM entity-link table is one-to-one synchronization state. These
rows are identity assertions and support many ERP accounts per local record,
explicit confirmation, connection generations, and revocation history.
"""


def migrate_customer_links(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_operator_customer_links (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      instance_id UUID NOT NULL, generation TEXT NOT NULL, account TEXT NOT NULL,
      encrypted_url BYTEA, requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,instance_id,generation,account))""")


def migrate_company_actions(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_operator_company_actions (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      request_id UUID NOT NULL, actor BIGINT NOT NULL, company_id UUID NOT NULL,
      kind TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id, request_id))""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS operator_action_pending
      ON saas_operator_company_actions(tenant_id, fingerprint) WHERE state='unconfirmed'""")


def migrate_widget_queue(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_widget_sources (
        tenant_id uuid NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, instance uuid NOT NULL,
        started_at timestamptz NOT NULL DEFAULT clock_timestamp(), refreshed_at timestamptz,
        generation text NOT NULL, PRIMARY KEY(tenant_id,instance))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_widget_dialogs (
        tenant_id uuid NOT NULL, instance uuid NOT NULL, chat_id bigint NOT NULL, identity text NOT NULL,
        revision text NOT NULL, title text NOT NULL, upstream_at text NOT NULL, fetched_at text,
        visible boolean NOT NULL, writable boolean NOT NULL, checked_at timestamptz,
        active_snapshot bigint, before_id bigint, high_id bigint NOT NULL DEFAULT 0,
        latest_in bigint, latest_at timestamptz, closed_at timestamptz, marked_in bigint,
        state text NOT NULL DEFAULT 'archive' CHECK(state IN ('archive','waiting','accepted','closed')),
        operator_id bigint, version bigint NOT NULL DEFAULT 0,
        PRIMARY KEY(tenant_id,instance,chat_id,identity),
        FOREIGN KEY(tenant_id,instance) REFERENCES saas_widget_sources(tenant_id,instance) ON DELETE CASCADE)""")
    cur.execute("ALTER TABLE saas_widget_dialogs ADD COLUMN IF NOT EXISTS operator_account text")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_widget_messages (
        tenant_id uuid NOT NULL, instance uuid NOT NULL, chat_id bigint NOT NULL, identity text NOT NULL,
        mid bigint NOT NULL, sender text NOT NULL, author text NOT NULL, body text NOT NULL,
        attachment boolean NOT NULL, at timestamptz NOT NULL, fresh boolean NOT NULL,
        PRIMARY KEY(tenant_id,instance,chat_id,identity,mid),
        FOREIGN KEY(tenant_id,instance,chat_id,identity) REFERENCES saas_widget_dialogs ON DELETE CASCADE)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_widget_reads (
        tenant_id uuid NOT NULL, instance uuid NOT NULL, chat_id bigint NOT NULL, identity text NOT NULL,
        actor bigint NOT NULL, through_id bigint NOT NULL,
        PRIMARY KEY(tenant_id,instance,chat_id,identity,actor),
        FOREIGN KEY(tenant_id,instance,chat_id,identity) REFERENCES saas_widget_dialogs ON DELETE CASCADE)""")


def migrate_operator_identity_links(cur) -> None:
    migrate_widget_queue(cur)
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_operator_widget_replies (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      request_id UUID NOT NULL, actor BIGINT NOT NULL, signature TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('unknown','saved','rejected')), message_id TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,request_id))""")
    cur.execute("""ALTER TABLE saas_operator_widget_replies
        ADD COLUMN IF NOT EXISTS provider_status TEXT,
        ADD COLUMN IF NOT EXISTS assigned_operator TEXT""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS operator_widget_reply_uncertain
      ON saas_operator_widget_replies(tenant_id,signature) WHERE state='unknown'""")
    migrate_customer_links(cur)
    migrate_company_actions(cur)
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_operator_dialog_accounts (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      provider TEXT NOT NULL CHECK(provider IN ('connect','kommo')),
      lead_id BIGINT NOT NULL CHECK(lead_id>0),
      channel_id TEXT NOT NULL CHECK(length(channel_id)<=256),
      conversation_id TEXT NOT NULL CHECK(length(conversation_id)<=256),
      id UUID NOT NULL,
      account TEXT CHECK(length(account) BETWEEN 2 AND 120),
      generation TEXT,
      instance_id UUID,
      updated_by BIGINT NOT NULL CHECK(updated_by>0),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,provider,lead_id,channel_id,conversation_id),
      CHECK((account IS NULL AND generation IS NULL AND instance_id IS NULL) OR
            (account IS NOT NULL AND generation IS NOT NULL AND instance_id IS NOT NULL))
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_links (
      id UUID PRIMARY KEY,
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      provider TEXT NOT NULL DEFAULT 'beinsystems_operator'
        CHECK(provider='beinsystems_operator'),
      connection_instance_id UUID NOT NULL,
      connection_generation TEXT NOT NULL CHECK(connection_generation<>''),
      local_entity_type TEXT NOT NULL
        CHECK(local_entity_type IN ('company','contact','support_ticket')),
      local_entity_id UUID NOT NULL,
      external_entity_type TEXT NOT NULL CHECK(external_entity_type IN ('account','profile_user')),
      external_id TEXT NOT NULL CHECK(external_id<>'' AND length(external_id)<=240),
      external_scope_id TEXT NOT NULL DEFAULT '' CHECK(length(external_scope_id)<=240),
      verification_basis TEXT NOT NULL CHECK(verification_basis<>''),
      evidence_summary TEXT NOT NULL CHECK(length(evidence_summary) BETWEEN 8 AND 500),
      verified_by BIGINT NOT NULL CHECK(verified_by>0),
      verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      revoked_at TIMESTAMPTZ NULL,
      version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CHECK((external_entity_type='account' AND external_scope_id='') OR
            (external_entity_type='profile_user' AND external_scope_id<>'')),
      CHECK((local_entity_type='company' AND external_entity_type='account') OR
            (local_entity_type='contact' AND external_entity_type='profile_user') OR
            local_entity_type='support_ticket')
    )''')
    cur.execute('''ALTER TABLE saas_beinsystems_operator_links
      ADD COLUMN IF NOT EXISTS connection_instance_id UUID''')
    # A confirmed ERP account is referenced by its CRM company and copied onto
    # support cases as a context snapshot. Enforce uniqueness for the company
    # mapping and ERP user identity separately; case snapshots may reuse account.
    cur.execute('''DROP INDEX IF EXISTS saas_operator_active_external_identity_idx''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_operator_active_profile_user_idx
      ON saas_beinsystems_operator_links(tenant_id,connection_generation,
        external_scope_id,external_id)
      WHERE revoked_at IS NULL AND external_entity_type='profile_user' ''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_operator_active_company_account_idx
      ON saas_beinsystems_operator_links(tenant_id,connection_generation,external_id)
      WHERE revoked_at IS NULL AND local_entity_type='company' AND external_entity_type='account' ''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_operator_active_case_account_idx
      ON saas_beinsystems_operator_links(tenant_id,connection_generation,local_entity_id)
      WHERE revoked_at IS NULL AND local_entity_type='support_ticket' AND external_entity_type='account' ''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_operator_active_local_identity_idx
      ON saas_beinsystems_operator_links(tenant_id,connection_generation,
        local_entity_type,local_entity_id,verified_at DESC)
      WHERE revoked_at IS NULL''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_operator_link_history_idx
      ON saas_beinsystems_operator_links(tenant_id,local_entity_type,local_entity_id,
        updated_at DESC)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_rate_limits (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      actor_telegram_id BIGINT NOT NULL CHECK(actor_telegram_id>0),
      endpoint TEXT NOT NULL CHECK(endpoint IN ('me','accounts','account_info','profile_users','task_list','auth_login','widget')),
      bucket_start TIMESTAMPTZ NOT NULL,
      request_count INTEGER NOT NULL CHECK(request_count>0),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,actor_telegram_id,endpoint,bucket_start)
    )''')
    # The table may already exist from an earlier release with a narrower
    # endpoint constraint. Keep startup migration idempotent while allowing
    # separate buckets for credential login and widget reads/replies.
    cur.execute('''DO $$ BEGIN
      IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='saas_beinsystems_operator_rate_limits'::regclass
          AND conname='saas_beinsystems_operator_rate_limits_endpoint_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%widget%'
      ) THEN
        ALTER TABLE saas_beinsystems_operator_rate_limits
          DROP CONSTRAINT saas_beinsystems_operator_rate_limits_endpoint_check;
      END IF;
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='saas_beinsystems_operator_rate_limits'::regclass
          AND conname='saas_beinsystems_operator_rate_limits_endpoint_check'
          AND pg_get_constraintdef(oid) LIKE '%widget%'
      ) THEN
        ALTER TABLE saas_beinsystems_operator_rate_limits
          ADD CONSTRAINT saas_beinsystems_operator_rate_limits_endpoint_check
          CHECK(endpoint IN ('me','accounts','account_info','profile_users','task_list','auth_login','widget'));
      END IF;
    END $$''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_pending_auth (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      owner_telegram_id BIGINT NOT NULL CHECK(owner_telegram_id>0),
      token_ciphertext BYTEA NOT NULL CHECK(octet_length(token_ciphertext)>0),
      received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,owner_telegram_id),
      FOREIGN KEY(tenant_id,owner_telegram_id)
        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_pending_auth_v2 (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      owner_telegram_id BIGINT NOT NULL CHECK(owner_telegram_id>0),
      token_ciphertext BYTEA NOT NULL CHECK(octet_length(token_ciphertext)>0),
      received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,owner_telegram_id),
      FOREIGN KEY(tenant_id,owner_telegram_id)
        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE
    )''')
    cur.execute('''ALTER TABLE saas_beinsystems_operator_pending_auth_v2
      ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ NULL''')
    cur.execute('''ALTER TABLE saas_beinsystems_operator_pending_auth_v2
      ADD COLUMN IF NOT EXISTS me_verified_at TIMESTAMPTZ NULL''')
    cur.execute('''ALTER TABLE saas_beinsystems_operator_pending_auth_v2
      ADD COLUMN IF NOT EXISTS accounts_access_verified_at TIMESTAMPTZ NULL''')
    cur.execute('''ALTER TABLE saas_beinsystems_operator_pending_auth_v2
      ADD COLUMN IF NOT EXISTS verified_identifier TEXT NULL''')
    cur.execute('''ALTER TABLE saas_beinsystems_operator_pending_auth_v2
      ADD COLUMN IF NOT EXISTS verified_role TEXT NULL''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_account_catalog (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      external_account TEXT NOT NULL CHECK(external_account<>'' AND length(external_account)<=240),
      first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,external_account)
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_company_imports (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      external_account TEXT NOT NULL CHECK(external_account<>'' AND length(external_account)<=240),
      company_id UUID NOT NULL,
      created_by BIGINT NOT NULL CHECK(created_by>0),
      updated_by BIGINT NOT NULL CHECK(updated_by>0),
      last_seen_connection_instance_id UUID NOT NULL,
      last_seen_generation TEXT NOT NULL CHECK(last_seen_generation<>''),
      last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,external_account),
      FOREIGN KEY(tenant_id,created_by)
        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT,
      FOREIGN KEY(tenant_id,updated_by)
        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT
    )''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_operator_company_import_lookup_idx
      ON saas_beinsystems_operator_company_imports(tenant_id,company_id)''')
    # A pending JWT may be explicitly approved for narrowly scoped, one-account
    # CRM additions without becoming the generic Operator integration. The JWT
    # remains only in the existing encrypted pending-auth row.
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_accounts_capabilities (
      tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
      owner_telegram_id BIGINT NOT NULL CHECK(owner_telegram_id>0),
      candidate_received_at TIMESTAMPTZ NOT NULL,
      operator_identifier_fingerprint TEXT NOT NULL
        CHECK(operator_identifier_fingerprint ~ '^[0-9a-f]{64}$'),
      connection_generation TEXT NOT NULL CHECK(connection_generation<>''),
      connection_instance_id UUID NOT NULL,
      expires_at TIMESTAMPTZ NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      FOREIGN KEY(tenant_id,owner_telegram_id)
        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE
    )''')
    # The Operator identity migration is also exercised on isolated databases that
    # may not have the native CRM schema yet. Install the tenant-scoped foreign
    # key as soon as that table exists; normal app migration order creates it
    # before reaching this point.
    cur.execute('''DO $operator_company_import$ BEGIN
      IF to_regclass('saas_crm_native_companies') IS NOT NULL
         AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conrelid='saas_beinsystems_operator_company_imports'::regclass
             AND conname='saas_operator_company_import_company_fk'
         ) THEN
        ALTER TABLE saas_beinsystems_operator_company_imports
          ADD CONSTRAINT saas_operator_company_import_company_fk
          FOREIGN KEY(tenant_id,company_id)
          REFERENCES saas_crm_native_companies(tenant_id,id) ON DELETE RESTRICT;
      END IF;
    END; $operator_company_import$;''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_dialog_company_bindings (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      channel_id UUID NOT NULL,
      conversation_id TEXT NOT NULL CHECK(conversation_id<>'' AND length(conversation_id)<=160),
      company_id UUID NOT NULL,
      created_by BIGINT NOT NULL CHECK(created_by>0),
      updated_by BIGINT NOT NULL CHECK(updated_by>0),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,channel_id,conversation_id),
      FOREIGN KEY(tenant_id,created_by)
        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE,
      FOREIGN KEY(tenant_id,updated_by)
        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE
    )''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_operator_dialog_company_lookup_idx
      ON saas_beinsystems_operator_dialog_company_bindings(tenant_id,company_id)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_beinsystems_operator_employee_links (
      id UUID PRIMARY KEY,
      tenant_id UUID NOT NULL,
      provider TEXT NOT NULL DEFAULT 'beinsystems_operator'
        CHECK(provider='beinsystems_operator'),
      connection_instance_id UUID NOT NULL,
      connection_generation TEXT NOT NULL CHECK(connection_generation<>''),
      member_telegram_id BIGINT NOT NULL CHECK(member_telegram_id>0),
      operator_account TEXT NOT NULL CHECK(operator_account<>'' AND length(operator_account)<=240),
      operator_login TEXT NOT NULL CHECK(operator_login<>'' AND length(operator_login)<=240),
      verification_basis TEXT NOT NULL CHECK(verification_basis<>''),
      evidence_summary TEXT NOT NULL CHECK(length(evidence_summary) BETWEEN 8 AND 500),
      verified_by BIGINT NOT NULL CHECK(verified_by>0),
      verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      revoked_at TIMESTAMPTZ NULL,
      version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      FOREIGN KEY(tenant_id,member_telegram_id)
        REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE
    )''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_operator_active_member_link_idx
      ON saas_beinsystems_operator_employee_links(tenant_id,connection_generation,member_telegram_id)
      WHERE revoked_at IS NULL''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_operator_active_staff_identity_idx
      ON saas_beinsystems_operator_employee_links(tenant_id,connection_generation,operator_account,operator_login)
      WHERE revoked_at IS NULL''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_operator_member_link_history_idx
      ON saas_beinsystems_operator_employee_links(tenant_id,member_telegram_id,updated_at DESC)''')
