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
