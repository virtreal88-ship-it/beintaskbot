"""Additive support inbox schema; installed in the AI Agents schema transaction."""

def create_schema(conn):
    from tenant_connect_transport import _schema
    _schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_support_tickets(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          id UUID NOT NULL,channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,
          source TEXT NOT NULL CHECK(source IN ('manual','ai_handoff')),
          reason TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',
          summary TEXT NOT NULL DEFAULT '',suggestions TEXT NOT NULL DEFAULT '',next_steps TEXT NOT NULL DEFAULT '',
          summary_state TEXT NOT NULL DEFAULT 'pending' CHECK(summary_state IN ('pending','generating','completed','unavailable')),
          status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','waiting','in_progress','resolved')),
          assigned_to BIGINT NULL,created_by BIGINT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),resolved_at TIMESTAMPTZ NULL,
          PRIMARY KEY(tenant_id,id),
          FOREIGN KEY(tenant_id,channel_id,conversation_id)
            REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,assigned_to) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_support_queue_idx ON saas_support_tickets(tenant_id,status,assigned_to,created_at)')
        cur.execute("ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS completion_outcome TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS completion_note TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS knowledge_candidate_requested BOOLEAN NOT NULL DEFAULT FALSE")
        # Existing rows keep their prior intended scope. The reader distinguishes
        # true episode membership from old attachment/share additions without
        # rewriting history; new cases choose an explicit scope on creation.
        cur.execute("ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS history_scope TEXT NOT NULL DEFAULT 'legacy'")
        cur.execute("ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS summary_agent_binding TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS summary_scope_hash TEXT NOT NULL DEFAULT ''")
        cur.execute('ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS assigned_developer_channel_id UUID NULL')
        cur.execute('ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS assigned_developer_conversation_id TEXT NULL')
        cur.execute('''DO $$ BEGIN
          IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='saas_support_tickets'::regclass AND conname='saas_support_developer_route_fk') THEN
            ALTER TABLE saas_support_tickets ADD CONSTRAINT saas_support_developer_route_fk
              FOREIGN KEY(tenant_id,assigned_developer_channel_id,assigned_developer_conversation_id)
              REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE RESTRICT;
          END IF;
          IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='saas_support_tickets'::regclass AND conname='saas_support_developer_pair_check') THEN
            ALTER TABLE saas_support_tickets ADD CONSTRAINT saas_support_developer_pair_check
              CHECK((assigned_developer_channel_id IS NULL)=(assigned_developer_conversation_id IS NULL));
          END IF;
        END $$''')
        cur.execute('DROP INDEX IF EXISTS saas_support_open_route_idx')
        cur.execute("CREATE UNIQUE INDEX saas_support_open_route_idx ON saas_support_tickets(tenant_id,channel_id,conversation_id) WHERE status<>'resolved' AND completion_outcome<>'test_stopped'")
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_support_completions(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          request_id UUID NOT NULL,actor_id BIGINT NOT NULL,channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,
          input_hash TEXT NOT NULL,ticket_id UUID NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,request_id),
          FOREIGN KEY(tenant_id,actor_id) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,ticket_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE RESTRICT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_support_events(
          tenant_id UUID NOT NULL,event_key TEXT NOT NULL,ticket_id UUID NOT NULL,
          PRIMARY KEY(tenant_id,event_key),
          FOREIGN KEY(tenant_id,ticket_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE CASCADE)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_support_ticket_messages(
          tenant_id UUID NOT NULL,ticket_id UUID NOT NULL,message_id UUID NOT NULL,
          event_key TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,ticket_id,message_id),
          FOREIGN KEY(tenant_id,ticket_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(message_id) REFERENCES saas_crm_messages(id) ON DELETE CASCADE)''')
        # Durable fan-out intent for newly-created AI cases. This belongs in
        # schema bootstrap, never in the webhook/case mutation transaction.
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_case_developer_dispatches(
          tenant_id UUID NOT NULL, id UUID NOT NULL, case_id UUID NOT NULL, agent_id UUID NOT NULL,
          event_key TEXT NOT NULL, destination_key TEXT NOT NULL, channel_id UUID NULL, conversation_id TEXT NULL,
          owner_id BIGINT NULL, request_id UUID NOT NULL, source_revision BIGINT NULL,
          source_assignment_id UUID NULL, state TEXT NOT NULL DEFAULT 'queued', reason TEXT NOT NULL DEFAULT '',
          consultation_id UUID NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,event_key,destination_key), UNIQUE(tenant_id,request_id),
          FOREIGN KEY(tenant_id,case_id) REFERENCES saas_support_tickets(tenant_id,id) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE,
          CHECK(state IN ('queued','preparing','sent','pending','unknown','blocked')) )''')
        cur.execute('ALTER TABLE saas_ai_case_developer_dispatches ADD COLUMN IF NOT EXISTS source_revision BIGINT NULL')
        cur.execute('ALTER TABLE saas_ai_case_developer_dispatches ADD COLUMN IF NOT EXISTS source_assignment_id UUID NULL')
        cur.execute('''CREATE INDEX IF NOT EXISTS saas_support_ticket_messages_order_idx
          ON saas_support_ticket_messages(tenant_id,ticket_id,created_at,message_id)''')
