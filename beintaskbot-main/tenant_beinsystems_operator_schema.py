"""Tenant-scoped, auditable manual relations to Operator identities.

The existing CRM entity-link table is one-to-one synchronization state. These
rows are identity assertions and support many ERP accounts per local record,
explicit confirmation, connection generations, and revocation history.
"""


def migrate_operator_identity_links(cur) -> None:
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
      endpoint TEXT NOT NULL CHECK(endpoint IN ('me','accounts','account_info','profile_users','task_list','auth_login')),
      bucket_start TIMESTAMPTZ NOT NULL,
      request_count INTEGER NOT NULL CHECK(request_count>0),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,actor_telegram_id,endpoint,bucket_start)
    )''')
    # The table may already exist from an earlier release with a narrower
    # endpoint constraint. Keep startup migration idempotent while allowing
    # the credential login limiter to record its own endpoint.
    cur.execute('''DO $$ BEGIN
      IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='saas_beinsystems_operator_rate_limits'::regclass
          AND conname='saas_beinsystems_operator_rate_limits_endpoint_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%auth_login%'
      ) THEN
        ALTER TABLE saas_beinsystems_operator_rate_limits
          DROP CONSTRAINT saas_beinsystems_operator_rate_limits_endpoint_check;
      END IF;
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='saas_beinsystems_operator_rate_limits'::regclass
          AND conname='saas_beinsystems_operator_rate_limits_endpoint_check'
          AND pg_get_constraintdef(oid) LIKE '%auth_login%'
      ) THEN
        ALTER TABLE saas_beinsystems_operator_rate_limits
          ADD CONSTRAINT saas_beinsystems_operator_rate_limits_endpoint_check
          CHECK(endpoint IN ('me','accounts','account_info','profile_users','task_list','auth_login'));
      END IF;
    END $$''')
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
