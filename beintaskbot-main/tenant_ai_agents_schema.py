"""Lazy, additive schema for manual AI agent configuration and knowledge."""
from threading import Lock
from tenant_platform import _ensure_schema

_ready = False
_lock = Lock()


def ensure_schema(conn):
    global _ready
    _ensure_schema(conn)
    # Monetary reservations are additive and shared by every AI execution
    # path.  Creating them with the core AI schema avoids worker-specific
    # ledgers or migrations.
    from tenant_ai_spend_budget import ensure_schema as ensure_spend_schema
    ensure_spend_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-agents-schema',))
            from tenant_ai_pilot import schema as pilot_schema
            pilot_schema(cur)
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_agent_settings(
              tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
              company_instructions TEXT NOT NULL DEFAULT '',
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              emergency_stop BOOLEAN NOT NULL DEFAULT FALSE,
              auto_send_enabled BOOLEAN NOT NULL DEFAULT FALSE CHECK(auto_send_enabled=FALSE),
              crm_actions_enabled BOOLEAN NOT NULL DEFAULT FALSE CHECK(crm_actions_enabled=FALSE),
              operating_mode TEXT NULL CHECK(operating_mode IS NULL OR operating_mode IN ('manual','active','simulation')),
              support_case_extraction_enabled BOOLEAN NOT NULL DEFAULT FALSE,
              support_case_extraction_mode TEXT NOT NULL DEFAULT 'off' CHECK(support_case_extraction_mode IN ('off','manual','automatic')),
              support_case_extraction_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
              support_case_extraction_started_at TIMESTAMPTZ NULL,
              updated_by BIGINT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              FOREIGN KEY(tenant_id,updated_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT)''')
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS company_instructions TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS emergency_stop BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS operating_mode TEXT NULL")
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS support_case_extraction_enabled BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS support_case_extraction_mode TEXT NOT NULL DEFAULT 'off'")
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS support_case_extraction_channels JSONB NOT NULL DEFAULT '[]'::jsonb")
            cur.execute('ALTER TABLE saas_ai_agent_settings DROP CONSTRAINT IF EXISTS saas_ai_agent_settings_support_case_extraction_mode_check')
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD CONSTRAINT saas_ai_agent_settings_support_case_extraction_mode_check CHECK(support_case_extraction_mode IN ('off','manual','automatic'))")
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS support_case_extraction_started_at TIMESTAMPTZ NULL")
            # Boolean-only pilot settings predate tenant channel selection.
            # Preserve their narrow environment-scoped behaviour as Manual;
            # never infer that every current Connect channel was approved.
            cur.execute("""UPDATE saas_ai_agent_settings
              SET support_case_extraction_mode='manual',support_case_extraction_channels='[]'::jsonb
              WHERE support_case_extraction_enabled=TRUE
                AND support_case_extraction_mode='off'""")
            cur.execute('ALTER TABLE saas_ai_agent_settings DROP CONSTRAINT IF EXISTS saas_ai_agent_settings_auto_send_enabled_check')
            cur.execute('ALTER TABLE saas_ai_agent_settings DROP CONSTRAINT IF EXISTS saas_ai_agent_settings_crm_actions_enabled_check')
            cur.execute('ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS max_replies_per_chat INTEGER NOT NULL DEFAULT 20')
            # Native CRM writes have their own tenant opt-in, defaulting off;
            # legacy Kommo and agent settings are not changed by this rollout.
            cur.execute('ALTER TABLE saas_ai_agent_settings ADD COLUMN IF NOT EXISTS native_crm_actions_enabled BOOLEAN NOT NULL DEFAULT FALSE')
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
              response_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
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
            cur.execute("ALTER TABLE saas_ai_agents ADD COLUMN IF NOT EXISTS response_policy JSONB NOT NULL DEFAULT '{}'::jsonb")
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
            cur.execute("ALTER TABLE saas_ai_conversation_state ADD COLUMN IF NOT EXISTS first_inbound_message_id TEXT NOT NULL DEFAULT ''")
            # Per-employee, per-route read cursor for the operator inbox. Existing
            # messages are initialized at the latest message seen in the listing;
            # only messages arriving after that snapshot are counted as unread.
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_operator_dialog_reads(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              telegram_id BIGINT NOT NULL,
              provider TEXT NOT NULL CHECK(provider IN ('connect','kommo')),
              channel_id TEXT NOT NULL DEFAULT '',conversation_id TEXT NOT NULL DEFAULT '',lead_id BIGINT NOT NULL,
              last_read_at TIMESTAMPTZ NOT NULL,last_read_message_id UUID NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,telegram_id,provider,channel_id,conversation_id,lead_id),
              FOREIGN KEY(tenant_id,telegram_id) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE)''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_operator_dialog_reads_member_idx
              ON saas_operator_dialog_reads(tenant_id,telegram_id,updated_at DESC)''')
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
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_no_answer_state(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              agent_id UUID NOT NULL,
              provider TEXT NOT NULL CHECK(provider IN ('connect','kommo')),
              channel_id TEXT NOT NULL DEFAULT '',
              conversation_id TEXT NOT NULL,
              clarification_count INTEGER NOT NULL DEFAULT 0 CHECK(clarification_count BETWEEN 0 AND 3),
              last_inbound_id TEXT NOT NULL DEFAULT '',
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,agent_id,provider,channel_id,conversation_id),
              FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id) ON DELETE CASCADE)''')
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
        from tenant_knowledge_campaign_schema import create_schema as create_campaign_schema
        create_campaign_schema(conn)
        with conn.cursor() as cur:
            from tenant_knowledge_media_cache import schema as media_cache_schema
            from tenant_knowledge_quality import schema as quality_schema
            media_cache_schema(cur)
            quality_schema(cur)
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
        from tenant_ai_group_coordination import create_schema as create_group_schema
        create_group_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_compat_migrations(
              version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
            cur.execute('SELECT 1 FROM saas_ai_compat_migrations WHERE version=1')
            if not cur.fetchone():
                # Retire legacy company Test Mode without dropping historical
                # columns/tables. Preserve its no-send guarantee and quarantine
                # work admitted only through old test bindings.
                cur.execute("""DO $$ BEGIN
                  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='saas_ai_agent_settings' AND column_name='operating_mode') THEN
                    EXECUTE 'UPDATE saas_ai_agent_settings SET auto_send_enabled=FALSE WHERE operating_mode=''test''';
                    EXECUTE 'UPDATE saas_ai_agent_jobs SET state=''skipped'',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code=''legacy_test_mode_retired'',updated_at=now() WHERE state IN (''queued'',''processing'') AND tenant_id IN (SELECT tenant_id FROM saas_ai_agent_settings WHERE operating_mode=''test'')';
                    EXECUTE 'UPDATE saas_tenant_webhook_events e SET state=''skipped'',completed_at=now(),error_code=''legacy_test_mode_retired'' WHERE state IN (''queued'',''processing'') AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE error_code=''legacy_test_mode_retired'')';
                    EXECUTE 'UPDATE saas_ai_agent_drafts d SET state=''rejected'' FROM saas_ai_agent_settings s JOIN saas_ai_agents a ON a.tenant_id=s.tenant_id WHERE d.tenant_id=s.tenant_id AND d.agent_id=a.id AND d.state=''pending'' AND a.mode=''automatic'' AND s.operating_mode=''test''';
                  END IF;
                END $$""")
                cur.execute('''UPDATE saas_ai_agent_jobs j SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,
                  error_code='legacy_test_mode_retired',updated_at=now()
                  WHERE j.state IN ('queued','processing') AND EXISTS (
                    SELECT 1 FROM saas_tenant_webhook_events e WHERE e.tenant_id=j.tenant_id
                      AND e.fingerprint=j.event_fingerprint AND e.payload->>'test_mode'='true')''')
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='legacy_test_mode_retired' WHERE state IN ('queued','processing') AND payload->>'test_mode'='true'")
                cur.execute("UPDATE saas_ai_agent_drafts SET state='rejected' WHERE state='pending' AND source_fingerprint IN (SELECT fingerprint FROM saas_tenant_webhook_events WHERE payload->>'test_mode'='true')")
                cur.execute('INSERT INTO saas_ai_compat_migrations(version) VALUES(1) ON CONFLICT DO NOTHING')
            # Old Test Mode is first quarantined above, then mapped to Manual
            # with automatic sending disabled. This works for both nullable
            # and NOT NULL variants of the deployed schema.
            # A previous deployed schema made this column NOT NULL. Keep the
            # legacy Test Mode retirement compatible with that shape and map
            # it to the safest explicit company mode. NULL is only a legacy
            # compatibility value in schemas that permit it; it is not needed
            # to retire Test Mode and must never abort application startup.
            # Older deployed constraints allowed `test` but not `manual`, so
            # remove the legacy check before performing the compatibility update.
            cur.execute("ALTER TABLE saas_ai_agent_settings DROP CONSTRAINT IF EXISTS saas_ai_agent_settings_operating_mode_check")
            cur.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=FALSE,operating_mode='manual' WHERE operating_mode='test' RETURNING tenant_id")
            legacy_test_tenants=[str(row['tenant_id']) for row in cur.fetchall()]
            if legacy_test_tenants:
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='legacy_test_mode_retired',updated_at=now() WHERE state IN ('queued','processing') AND tenant_id=ANY(%s::uuid[])", (legacy_test_tenants,))
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='legacy_test_mode_retired' WHERE state IN ('queued','processing') AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=ANY(%s::uuid[]) AND error_code='legacy_test_mode_retired')", (legacy_test_tenants,))
                cur.execute("UPDATE saas_ai_agent_drafts SET state='rejected' WHERE state='pending' AND tenant_id=ANY(%s::uuid[]) AND source_fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=ANY(%s::uuid[]) AND error_code='legacy_test_mode_retired')", (legacy_test_tenants,legacy_test_tenants))
            cur.execute("ALTER TABLE saas_ai_agent_settings ADD CONSTRAINT saas_ai_agent_settings_operating_mode_check CHECK(operating_mode IS NULL OR operating_mode IN ('manual','active','simulation'))")
        conn.commit()
        _ready = True
