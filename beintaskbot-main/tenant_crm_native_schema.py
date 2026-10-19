"""Additive native CRM contacts, independent of Kommo IDs."""
def migrate_native_contacts(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_crm_native_contacts (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
        display_name TEXT NOT NULL,
        phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        archived_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK (length(trim(display_name)) > 0)
    )""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_crm_native_contacts_tenant_idx
        ON saas_crm_native_contacts(tenant_id, updated_at DESC)""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS saas_crm_native_contacts_tenant_id_idx
        ON saas_crm_native_contacts(tenant_id,id)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_crm_native_deals (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
        contact_id UUID NULL,
        title TEXT NOT NULL,
        pipeline_id TEXT NOT NULL,
        stage_id TEXT NOT NULL,
        responsible_id BIGINT NULL,
        channel_id UUID NULL,
        conversation_id TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        archived_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id),
        FOREIGN KEY (tenant_id,pipeline_id,stage_id)
          REFERENCES saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id),
        FOREIGN KEY (tenant_id,responsible_id)
          REFERENCES saas_tenant_members(tenant_id,telegram_id),
        CHECK (length(trim(title)) > 0),
        CHECK ((channel_id IS NULL AND conversation_id='') OR (channel_id IS NOT NULL AND conversation_id<>''))
    )""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_crm_native_deals_tenant_idx
        ON saas_crm_native_deals(tenant_id,updated_at DESC) WHERE archived_at IS NULL""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS saas_crm_native_deals_chat_unique
        ON saas_crm_native_deals(tenant_id,channel_id,conversation_id)
        WHERE archived_at IS NULL AND channel_id IS NOT NULL""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS saas_crm_native_deals_tenant_id_idx
        ON saas_crm_native_deals(tenant_id,id)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_crm_native_tasks (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
        deal_id UUID NOT NULL,
        contact_id UUID NULL,
        channel_id UUID NULL,
        conversation_id TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL,
        due_at TIMESTAMPTZ NULL,
        responsible_id BIGINT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','completed')),
        created_by BIGINT NULL,
        idempotency_key TEXT NOT NULL DEFAULT '',
        archived_at TIMESTAMPTZ NULL,
        completed_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id,deal_id) REFERENCES saas_crm_native_deals(tenant_id,id),
        FOREIGN KEY (tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id),
        FOREIGN KEY (tenant_id,responsible_id) REFERENCES saas_tenant_members(tenant_id,telegram_id),
        FOREIGN KEY (tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id),
        CHECK (length(trim(text)) > 0),
        CHECK ((channel_id IS NULL AND conversation_id='') OR (channel_id IS NOT NULL AND conversation_id<>''))
    )""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS saas_crm_native_tasks_idempotency_idx
        ON saas_crm_native_tasks(tenant_id,idempotency_key) WHERE idempotency_key<>''""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_crm_native_tasks_list_idx
        ON saas_crm_native_tasks(tenant_id,responsible_id,status,due_at) WHERE archived_at IS NULL""")
