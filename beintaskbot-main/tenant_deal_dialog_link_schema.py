"""Additive storage for deal links to existing WhatsApp messages or episodes."""


def migrate(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS saas_crm_deal_dialog_links (
            tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
            id UUID NOT NULL,
            channel_id UUID NOT NULL,
            conversation_id TEXT NOT NULL,
            lead_id BIGINT NOT NULL,
            contact_id UUID NOT NULL,
            message_id UUID,
            episode_id UUID,
            deal_source TEXT NOT NULL CHECK (deal_source IN ('native','kommo')),
            deal_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            connection_generation TEXT NOT NULL,
            created_by BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id,id),
            UNIQUE (tenant_id,message_id),
            UNIQUE (tenant_id,episode_id),
            CHECK ((message_id IS NULL) <> (episode_id IS NULL)),
            CHECK ((deal_source='native') = (account_id='' AND connection_generation=''))
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS saas_crm_deal_dialog_links_deal_idx
        ON saas_crm_deal_dialog_links (tenant_id,deal_source,deal_id,account_id,connection_generation)
    """)
