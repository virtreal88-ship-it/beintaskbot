"""Lazy, additive schema for manual AI agent configuration and knowledge."""
from threading import Lock
from tenant_platform import _ensure_schema

_ready = False
_lock = Lock()


def ensure_schema(conn):
    global _ready
    _ensure_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-agents-schema',))
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_settings(
              tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
              company_instructions TEXT NOT NULL DEFAULT '',
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              operating_mode TEXT NOT NULL DEFAULT 'test' CHECK(operating_mode IN ('test','active')),
              emergency_stop BOOLEAN NOT NULL DEFAULT FALSE,
              auto_send_enabled BOOLEAN NOT NULL DEFAULT FALSE CHECK(auto_send_enabled=FALSE),
              crm_actions_enabled BOOLEAN NOT NULL DEFAULT FALSE CHECK(crm_actions_enabled=FALSE),
              updated_by BIGINT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              FOREIGN KEY(tenant_id,updated_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_test_mode(
              tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              emergency_stop BOOLEAN NOT NULL DEFAULT FALSE,
              account_domain TEXT NOT NULL DEFAULT '',
              company_phone TEXT NOT NULL DEFAULT '',
              company_phone_attested BOOLEAN NOT NULL DEFAULT FALSE,
              client_phone TEXT NOT NULL DEFAULT '',
              channel_id TEXT NOT NULL DEFAULT '',
              lead_id BIGINT NULL,
              contact_id BIGINT NULL,
              talk_id TEXT NOT NULL DEFAULT '',
              chat_id TEXT NOT NULL DEFAULT '',
              verified BOOLEAN NOT NULL DEFAULT FALSE,
              verified_at TIMESTAMPTZ NULL,
              updated_by BIGINT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              FOREIGN KEY(tenant_id,updated_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT,
              CHECK(NOT enabled OR (verified AND company_phone_attested AND account_domain<>'' AND company_phone<>'' AND client_phone<>''
                AND channel_id<>'' AND lead_id IS NOT NULL AND contact_id IS NOT NULL AND talk_id<>'' AND chat_id<>'')))''')
            cur.execute('ALTER TABLE saas_ai_agent_test_mode ADD COLUMN IF NOT EXISTS company_phone_attested BOOLEAN NOT NULL DEFAULT FALSE')
            cur.execute('ALTER TABLE saas_ai_agent_test_mode DROP CONSTRAINT IF EXISTS saas_ai_agent_test_mode_check')
            cur.execute('''ALTER TABLE saas_ai_agent_test_mode ADD CONSTRAINT saas_ai_agent_test_mode_check CHECK(NOT enabled OR
              (verified AND company_phone_attested AND account_domain<>'' AND company_phone<>'' AND client_phone<>''
                AND channel_id<>'' AND lead_id IS NOT NULL AND contact_id IS NOT NULL AND talk_id<>'' AND chat_id<>''))''')
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS company_instructions TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS operating_mode TEXT NOT NULL DEFAULT 'test'")
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS emergency_stop BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute('ALTER TABLE saas_ai_agent_settings DROP CONSTRAINT IF EXISTS saas_ai_agent_settings_operating_mode_check')
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD CONSTRAINT saas_ai_agent_settings_operating_mode_check CHECK(operating_mode IN ('test','active'))")
            cur.execute('ALTER TABLE saas_ai_agent_settings DROP CONSTRAINT IF EXISTS saas_ai_agent_settings_auto_send_enabled_check')
            cur.execute('ALTER TABLE saas_ai_agent_settings DROP CONSTRAINT IF EXISTS saas_ai_agent_settings_crm_actions_enabled_check')
            cur.execute('ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS max_replies_per_chat INTEGER NOT NULL DEFAULT 20')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agents(
              id UUID PRIMARY KEY,
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
              instructions TEXT NOT NULL DEFAULT '',
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              mode TEXT NOT NULL DEFAULT 'manual' CHECK(mode='manual'),
              language TEXT NOT NULL DEFAULT 'auto' CHECK(language IN ('auto','az','ru','en','tr')),
              style TEXT NOT NULL DEFAULT 'balanced' CHECK(style IN ('precise','balanced','creative')),
              delay_seconds INTEGER NOT NULL DEFAULT 40 CHECK(delay_seconds BETWEEN 0 AND 300),
              schedule JSONB NOT NULL DEFAULT '{}'::jsonb,
              scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
              model TEXT NOT NULL DEFAULT '',
              safety JSONB NOT NULL DEFAULT '{}'::jsonb,
              version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
              created_by BIGINT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE(tenant_id,id),
              FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
            cur.execute('ALTER TABLE saas_ai_agents DROP CONSTRAINT IF EXISTS saas_ai_agents_mode_check')
            cur.execute("ALTER TABLE saas_ai_agents ADD CONSTRAINT saas_ai_agents_mode_check CHECK(mode IN ('manual','automatic'))")
            # This is deliberately additive: existing agents receive the
            # server-enforced default policy on their next read.
            cur.execute("ALTER TABLE saas_ai_agents ADD COLUMN IF NOT EXISTS safety JSONB NOT NULL DEFAULT '{}'::jsonb")
            # A wizard may survive a browser refresh after its first request.
            # The opaque setup key makes that retry return the same record
            # instead of creating another agent for the same employee.
            cur.execute("ALTER TABLE saas_ai_agents ADD COLUMN IF NOT EXISTS setup_key TEXT NULL")
            cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_ai_agents_setup_key_idx
              ON saas_ai_agents(tenant_id,created_by,setup_key)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_agents_tenant_idx
              ON saas_ai_agents(tenant_id,updated_at DESC)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_knowledge_sources(
              id UUID PRIMARY KEY,
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 180),
              kind TEXT NOT NULL CHECK(kind IN ('text','faq','txt','pdf','docx')),
              content TEXT NOT NULL,
              active BOOLEAN NOT NULL DEFAULT TRUE,
              content_hash TEXT NOT NULL,
              created_by BIGINT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE(tenant_id,id),
              FOREIGN KEY(tenant_id,created_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_knowledge_tenant_idx
              ON saas_ai_knowledge_sources(tenant_id,active,updated_at DESC)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_knowledge_chunks(
              tenant_id UUID NOT NULL,
              source_id UUID NOT NULL,
              chunk_no INTEGER NOT NULL CHECK(chunk_no>=0),
              content TEXT NOT NULL,
              search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple',content)) STORED,
              PRIMARY KEY(tenant_id,source_id,chunk_no),
              FOREIGN KEY(tenant_id,source_id) REFERENCES saas_ai_knowledge_sources(tenant_id,id) ON DELETE CASCADE)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_knowledge_chunks_search_idx
              ON saas_ai_knowledge_chunks USING GIN(search_vector)''')
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'kommo'")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS external_id TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS event_type TEXT NOT NULL DEFAULT 'legacy'")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'received'")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ NOT NULL DEFAULT now()")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS lease_token UUID NULL")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ NULL")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS error_code TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE saas_tenant_webhook_events ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL")
            cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_tenant_webhook_external_id_idx
              ON saas_tenant_webhook_events(tenant_id,provider,external_id) WHERE external_id<>'' ''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_tenant_webhook_queue_idx
              ON saas_tenant_webhook_events(state,available_at,received_at) WHERE state IN ('queued','processing')''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_jobs(
              tenant_id UUID NOT NULL,
              agent_id UUID NOT NULL,
              provider TEXT NOT NULL,
              conversation_id TEXT NOT NULL,
              lead_id BIGINT NOT NULL,
              talk_id TEXT NOT NULL,
              chat_id TEXT NOT NULL,
              channel_id TEXT NOT NULL DEFAULT '',
              event_fingerprint TEXT NOT NULL,
              revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
              state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','processing','drafted','skipped','failed','unknown')),
              due_at TIMESTAMPTZ NOT NULL,
              lease_token UUID NULL,
              lease_until TIMESTAMPTZ NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              error_code TEXT NOT NULL DEFAULT '',
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,agent_id,provider,conversation_id),
              FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,event_fingerprint) REFERENCES saas_tenant_webhook_events(tenant_id,fingerprint) ON DELETE CASCADE)''')
            cur.execute('ALTER TABLE saas_ai_agent_jobs DROP CONSTRAINT IF EXISTS saas_ai_agent_jobs_state_check')
            cur.execute("ALTER TABLE saas_ai_agent_jobs ADD CONSTRAINT saas_ai_agent_jobs_state_check CHECK(state IN ('queued','processing','drafted','sent','skipped','failed','unknown'))")
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_agent_jobs_ready_idx
              ON saas_ai_agent_jobs(state,due_at,updated_at) WHERE state IN ('queued','processing')''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_runs(
              tenant_id UUID NOT NULL,
              id UUID NOT NULL,
              agent_id UUID NOT NULL,
              event_fingerprint TEXT NOT NULL,
              revision INTEGER NOT NULL,
              model TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('generating','completed','unknown','skipped','failed','superseded')),
              result JSONB NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,id),
              UNIQUE(tenant_id,agent_id,event_fingerprint,revision),
              FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,event_fingerprint) REFERENCES saas_tenant_webhook_events(tenant_id,fingerprint) ON DELETE CASCADE)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_agent_runs_usage_idx
              ON saas_ai_agent_runs(tenant_id,created_at) WHERE state IN ('generating','completed','unknown')''')
            cur.execute("ALTER TABLE saas_ai_agent_runs ADD COLUMN IF NOT EXISTS reserved_calls INTEGER NOT NULL DEFAULT 1")
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_voice_receipts(
              tenant_id UUID NOT NULL,channel_id UUID NOT NULL,external_id TEXT NOT NULL,
              run_id UUID NOT NULL,model TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('processing','completed','failed')),
              error_code TEXT NOT NULL DEFAULT '',created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,channel_id,external_id),
              FOREIGN KEY(tenant_id,external_id) REFERENCES saas_crm_messages(tenant_id,external_id) ON DELETE CASCADE)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_token_usage(
              tenant_id UUID NOT NULL,
              id UUID NOT NULL,
              run_id UUID NOT NULL,
              agent_id UUID NOT NULL,
              model TEXT NOT NULL,
              input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens>=0),
              output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens>=0),
              total_tokens INTEGER NOT NULL DEFAULT 0 CHECK(total_tokens>=0),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,id),
              UNIQUE(tenant_id,run_id),
              FOREIGN KEY(tenant_id,run_id) REFERENCES saas_ai_agent_runs(tenant_id,id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_agent_token_usage_daily_idx
              ON saas_ai_agent_token_usage(tenant_id,created_at)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_drafts(
              tenant_id UUID NOT NULL,
              id UUID NOT NULL,
              agent_id UUID NOT NULL,
              run_id UUID NOT NULL,
              lead_id BIGINT NOT NULL,
              talk_id TEXT NOT NULL,
              chat_id TEXT NOT NULL,
              source_message_id TEXT NOT NULL,
              source_fingerprint TEXT NOT NULL,
              draft TEXT NOT NULL,
              source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
              handoff_after_send BOOLEAN NOT NULL DEFAULT FALSE,
              state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','superseded')),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,id),
              UNIQUE(tenant_id,run_id),
              FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,run_id) REFERENCES saas_ai_agent_runs(tenant_id,id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,source_fingerprint) REFERENCES saas_tenant_webhook_events(tenant_id,fingerprint) ON DELETE CASCADE)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_agent_drafts_pending_idx
              ON saas_ai_agent_drafts(tenant_id,state,created_at DESC)''')
            cur.execute('ALTER TABLE saas_ai_agent_drafts DROP CONSTRAINT IF EXISTS saas_ai_agent_drafts_state_check')
            cur.execute("ALTER TABLE saas_ai_agent_drafts ADD CONSTRAINT saas_ai_agent_drafts_state_check CHECK(state IN ('pending','sending','unknown','approved','rejected','superseded'))")
            cur.execute("ALTER TABLE saas_ai_agent_drafts ADD COLUMN IF NOT EXISTS handoff_after_send BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_triggers(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              id UUID NOT NULL,agent_id UUID NOT NULL,trigger_type TEXT NOT NULL
                CHECK(trigger_type IN ('new_inquiry','phone_received','phrase')),
              name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
              condition JSONB NOT NULL DEFAULT '{}'::jsonb,actions JSONB NOT NULL DEFAULT '[]'::jsonb,
              enabled BOOLEAN NOT NULL DEFAULT FALSE,version INTEGER NOT NULL DEFAULT 1,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,id),FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_agent_triggers_active_idx
              ON saas_ai_agent_triggers(tenant_id,agent_id,enabled,trigger_type)''')
            cur.execute("ALTER TABLE saas_ai_agent_triggers ADD COLUMN IF NOT EXISTS setup_key TEXT NULL")
            cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_ai_agent_triggers_setup_key_idx
              ON saas_ai_agent_triggers(tenant_id,agent_id,setup_key)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_conversation_state(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              provider TEXT NOT NULL,conversation_id TEXT NOT NULL,agent_id UUID NULL,
              state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','human','paused')),
              reason TEXT NOT NULL DEFAULT '',assigned_to BIGINT NULL,first_inbound_message_id TEXT NOT NULL DEFAULT '',last_inbound_message_id TEXT NOT NULL DEFAULT '',
              last_agent_message_id TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 1,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,provider,conversation_id),
              FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE)''')
            # Connect Test Mode is deliberately separate from the historical
            # Kommo binding.  It has no deal, pipeline or contact dependency:
            # the signed Connect webhook and tenant-owned channel are the
            # authority.  Test runs always create drafts, never send WhatsApp.
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_connect_test_mode(
              tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              emergency_stop BOOLEAN NOT NULL DEFAULT FALSE,
              channel_id TEXT NOT NULL DEFAULT '', conversation_id TEXT NOT NULL DEFAULT '',
              verified BOOLEAN NOT NULL DEFAULT FALSE, verified_at TIMESTAMPTZ NULL,
              updated_by BIGINT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              FOREIGN KEY(tenant_id,updated_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT,
              CHECK(NOT enabled OR (verified AND channel_id<>'' AND conversation_id<>'')))''')
            cur.execute("ALTER TABLE saas_ai_conversation_state ADD COLUMN IF NOT EXISTS first_inbound_message_id TEXT NOT NULL DEFAULT ''")
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_action_runs(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              id UUID NOT NULL,agent_id UUID NOT NULL,event_fingerprint TEXT NOT NULL,action_key TEXT NOT NULL,
              action_type TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('proposed','running','accepted','unknown','blocked')),
              payload JSONB NOT NULL DEFAULT '{}'::jsonb,result JSONB NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id),
              UNIQUE(tenant_id,event_fingerprint,action_key),
              FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,event_fingerprint) REFERENCES saas_tenant_webhook_events(tenant_id,fingerprint) ON DELETE CASCADE)''')
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE saas_ai_conversation_state ADD COLUMN IF NOT EXISTS resumed_at TIMESTAMPTZ")
            # NULL preserves automatic routing for existing dialogues. A token
            # records an explicit operator binding and deduplicates its retries.
            cur.execute("ALTER TABLE saas_ai_conversation_state ADD COLUMN IF NOT EXISTS agent_assignment_id UUID NULL")
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE saas_ai_knowledge_sources ADD COLUMN IF NOT EXISTS agent_id UUID")
            cur.execute("ALTER TABLE saas_ai_knowledge_sources ADD COLUMN IF NOT EXISTS setup_key TEXT NULL")
            cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_ai_knowledge_sources_setup_key_idx
              ON saas_ai_knowledge_sources(tenant_id,agent_id,setup_key)''')
            cur.execute("ALTER TABLE saas_ai_agent_runs ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE saas_ai_agent_runs ADD COLUMN IF NOT EXISTS error_code TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE saas_ai_agent_drafts ADD COLUMN IF NOT EXISTS send_started_at TIMESTAMPTZ")
            cur.execute("CREATE INDEX IF NOT EXISTS saas_ai_accepted_run_idx ON saas_crm_messages(tenant_id,(raw->>'run_id')) WHERE direction='outgoing' AND author_name='AI Agent' AND raw->>'status'='accepted'")
            cur.execute("CREATE INDEX IF NOT EXISTS saas_ai_inbound_diagnostics_idx ON saas_tenant_webhook_events(tenant_id,received_at DESC) WHERE event_type='incoming_message'")
            cur.execute("CREATE TABLE IF NOT EXISTS saas_ai_worker_health (worker TEXT PRIMARY KEY,last_seen_at TIMESTAMPTZ NOT NULL)")
        from tenant_knowledge_candidates_schema import create_schema as create_candidates_schema
        create_candidates_schema(conn)
        from tenant_ai_expert_schema import create_schema as create_expert_schema
        create_expert_schema(conn)
        from tenant_ai_testing_schema import create_schema as create_testing_schema
        create_testing_schema(conn)
        from tenant_support_inbox_schema import create_schema
        create_schema(conn)
        from tenant_ai_channel_schema import migrate
        migrate(conn)
        from tenant_ai_legacy_permit import schema as permit_schema
        with conn.cursor() as cur:
            permit_schema(cur)
            from tenant_ai_draft_review import schema as draft_review_schema
            draft_review_schema(cur)
        from tenant_ai_group_coordination import create_schema as create_group_schema
        create_group_schema(conn)
        conn.commit()
        _ready = True
