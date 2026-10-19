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
