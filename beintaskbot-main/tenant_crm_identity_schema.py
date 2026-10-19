"""Additive provider-neutral CRM identity and ownership-mode schema."""


def migrate_crm_identity_schema(cur) -> None:
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_provider_settings (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      entity_type TEXT NOT NULL CHECK(entity_type IN ('contact','deal','task','pipeline','stage')),
      mode TEXT NOT NULL CHECK(mode IN ('beintaskbot','external','selective')),
      primary_provider TEXT NOT NULL CHECK(primary_provider IN ('beintaskbot','kommo','beinsystems')),
      allowed_fields JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(allowed_fields)='array'),
      updated_by BIGINT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,entity_type),
      CHECK((mode='beintaskbot' AND primary_provider='beintaskbot') OR
            (mode='external' AND primary_provider IN ('kommo','beinsystems')) OR mode='selective')
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_crm_entity_links (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      provider TEXT NOT NULL CHECK(provider IN ('kommo','beinsystems')),
      entity_type TEXT NOT NULL CHECK(entity_type IN ('contact','deal','task','pipeline','stage')),
      internal_id UUID NOT NULL,
      external_id TEXT NOT NULL CHECK(external_id<>''),
      sync_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(sync_state IN ('pending','synced','conflict','failed','disabled')),
      last_synced_at TIMESTAMPTZ NULL,
      last_origin_id TEXT NOT NULL DEFAULT '',
      last_error_code TEXT NOT NULL DEFAULT '',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,provider,entity_type,internal_id),
      UNIQUE(tenant_id,provider,entity_type,external_id)
    )''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_crm_entity_links_state_idx
      ON saas_crm_entity_links(tenant_id,sync_state,updated_at)''')
