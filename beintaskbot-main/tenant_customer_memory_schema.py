"""Additive schema for explicitly attributed customer memory; no activation."""


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_links (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      contact_id UUID NOT NULL, channel_id UUID NOT NULL, conversation_id TEXT NOT NULL,
      lead_id BIGINT NOT NULL, state TEXT NOT NULL CHECK (state IN ('confirmed','revoked')),
      version BIGINT NOT NULL DEFAULT 1, confirmed_by BIGINT NOT NULL, confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      revoked_by BIGINT, revoked_at TIMESTAMPTZ,
      UNIQUE (tenant_id,id,contact_id),
      FOREIGN KEY (tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id) ON DELETE CASCADE,
      CHECK (state <> 'revoked' OR revoked_at IS NOT NULL))''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_customer_memory_active_route
      ON saas_customer_memory_links(tenant_id,channel_id,conversation_id) WHERE state='confirmed' ''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_facts (
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      contact_id UUID NOT NULL, link_id UUID NOT NULL, link_version BIGINT NOT NULL,
      source_message_id UUID NOT NULL, source_version TEXT NOT NULL,
      category TEXT NOT NULL CHECK(category IN ('profile','feature','topic','problem','agreement')),
      value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 1200),
      source_kind TEXT NOT NULL CHECK(source_kind IN ('customer_statement','employee_observation','technical_result')),
      reliability TEXT NOT NULL CHECK(reliability IN ('confirmed','needs_review','outdated','refuted')),
      event_at TIMESTAMPTZ, observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      valid_from TIMESTAMPTZ, valid_to TIMESTAMPTZ, last_confirmed_at TIMESTAMPTZ,
      version BIGINT NOT NULL DEFAULT 1, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id,link_id,contact_id) REFERENCES saas_customer_memory_links(tenant_id,id,contact_id) ON DELETE CASCADE,
      CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from))''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_customer_memory_one_fact_per_source
      ON saas_customer_memory_facts(tenant_id,contact_id,source_message_id)''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_customer_memory_contact_facts
      ON saas_customer_memory_facts(tenant_id,contact_id,updated_at DESC,id)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_fact_versions (
      fact_id UUID NOT NULL REFERENCES saas_customer_memory_facts(id) ON DELETE CASCADE,
      version BIGINT NOT NULL, value TEXT NOT NULL, reliability TEXT NOT NULL,
      actor BIGINT NOT NULL, changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(fact_id,version))''')
    # Identifiers only: do not retain deleted values, their hashes or secrets.
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_customer_memory_suppressions (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      contact_id UUID NOT NULL, source_message_id UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,contact_id,source_message_id),
      FOREIGN KEY (tenant_id,contact_id) REFERENCES saas_crm_native_contacts(tenant_id,id) ON DELETE CASCADE)''')
