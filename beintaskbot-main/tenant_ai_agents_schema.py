"""Lazy, additive schema for manual AI agent configuration and knowledge."""
from threading import Lock
from tenant_platform import _ensure_schema

# Compatibility hint for older startup callers; never a readiness authority.
_ready = False
_lock = Lock()


# Keep this contract aligned with the core and nested additive migrations.
# Read PostgreSQL catalogs on every call: readiness belongs to this database,
# search_path and transaction, never to a process-local Boolean.
_SCHEMA_COLUMNS = {
    'saas_ai_agent_action_runs': (
        'action_key action_type agent_id created_at event_fingerprint id payload result state '
        'tenant_id updated_at '
    ).split(),
    'saas_ai_agent_drafts': (
        'agent_id chat_id created_at draft handoff_after_send id lead_id run_id '
        'send_started_at source_fingerprint source_ids source_message_id state talk_id '
        'tenant_id '
    ).split(),
    'saas_ai_agent_history_identities': (
        'deleted_at deleted_by display_name id tenant_id '
    ).split(),
    'saas_ai_agent_jobs': (
        'agent_id attempts channel_id chat_id conversation_id due_at error_code '
        'event_fingerprint lead_id lease_token lease_until provider revision scope_channel_id '
        'state talk_id tenant_id updated_at '
    ).split(),
    'saas_ai_agent_runs': (
        'agent_id created_at error_code event_fingerprint generated_at id model '
        'reserved_calls result revision state tenant_id updated_at '
    ).split(),
    'saas_ai_agent_settings': (
        'auto_send_enabled company_instructions crm_actions_enabled emergency_stop enabled '
        'max_replies_per_chat native_crm_actions_enabled operating_mode '
        'support_case_extraction_channels support_case_extraction_enabled '
        'support_case_extraction_mode support_case_extraction_started_at tenant_id updated_at '
        'updated_by '
    ).split(),
    'saas_ai_agent_token_usage': (
        'agent_id created_at id input_tokens model output_tokens reserved_tokens run_id '
        'tenant_id total_tokens '
    ).split(),
    'saas_ai_agent_triggers': (
        'actions agent_id condition created_at enabled id name setup_key tenant_id '
        'trigger_type updated_at version '
    ).split(),
    'saas_ai_agents': (
        'archived_at archived_by archived_replaced_by created_at created_by delay_seconds '
        'enabled expert_policy id instructions language mode model name response_policy role '
        'safety schedule scopes setup_key style tenant_id updated_at version '
    ).split(),
    'saas_ai_case_developer_dispatches': (
        'agent_id case_id channel_id consultation_id conversation_id created_at '
        'destination_key event_key id owner_id reason request_id source_assignment_id '
        'source_revision state tenant_id updated_at '
    ).split(),
    'saas_ai_channel_legacy_state': (
        'captured_at conversation_id original provider reason resolved_channel tenant_id '
    ).split(),
    'saas_ai_compat_migrations': (
        'applied_at version '
    ).split(),
    'saas_ai_conversation_state': (
        'agent_assignment_id agent_id assigned_to channel_id conversation_id '
        'first_inbound_message_id last_agent_message_id last_inbound_message_id provider '
        'reason resumed_at revision state tenant_id updated_at '
    ).split(),
    'saas_ai_expert_tasks': (
        'channel_id completed_at conversation_id created_at deadline error_code '
        'expert_agent_id expert_version grounded_sources id parent_run_id principal_agent_id '
        'provider result status tenant_id '
    ).split(),
    'saas_ai_group_channel_legacy_permits': (
        'actor agent_id channel_id granted_at group_jid legacy_revision preview_hash provider '
        'tenant_id '
    ).split(),
    'saas_ai_group_coordination': (
        'assigned_to assignments group_jid provider reason resumed_at revision state '
        'tenant_id updated_at '
    ).split(),
    'saas_ai_group_deliveries': (
        'created_at event_key group_jid provider revision run_id tenant_id '
    ).split(),
    'saas_ai_group_events': (
        'channel_id claimed created_at event_key group_jid provider revision tenant_id '
    ).split(),
    'saas_ai_group_staff': (
        'channel_id jid member_id tenant_id verified_at verified_by '
    ).split(),
    'saas_ai_knowledge_chunks': (
        'chunk_no content search_vector source_id tenant_id '
    ).split(),
    'saas_ai_knowledge_sources': (
        'active agent_id content content_hash created_at created_by id kind provenance '
        'setup_key tenant_id title updated_at '
    ).split(),
    'saas_ai_legacy_permits': (
        'actor agent_id channel_id channel_revision conversation_id granted_at '
        'legacy_revision tenant_id '
    ).split(),
    'saas_ai_media_grants': (
        'approved_at approved_by campaign_id cap_microusd created_at created_by id revoked_at '
        'status tenant_id '
    ).split(),
    'saas_ai_no_answer_state': (
        'agent_id channel_id clarification_count conversation_id last_inbound_id provider '
        'tenant_id updated_at '
    ).split(),
    'saas_ai_pilot_confirmations': (
        'channel_id conversation_id kwid_confirmed_at kwid_confirmed_by kwid_expires_at '
        'owner_approved_at owner_approved_by owner_expires_at scope_fingerprint tenant_id '
        'updated_at '
    ).split(),
    'saas_ai_pilot_episode_cutoffs': (
        'channel_id conversation_id reset_at reset_by tenant_id '
    ).split(),
    'saas_ai_pilot_episode_resets': (
        'channel_id conversation_id request_id reset_at reset_by tenant_id '
    ).split(),
    'saas_ai_scope_migrations': (
        'applied_at version '
    ).split(),
    'saas_ai_test_sessions': (
        'agent_id created_at created_by id pending_turn reason reply_count revision scope '
        'state tenant_id updated_at '
    ).split(),
    'saas_ai_test_turns': (
        'client_text created_at id input_hash reserved_tokens response session_id snapshot '
        'state tenant_id total_tokens trace updated_at '
    ).split(),
    'saas_ai_worker_health': (
        'last_seen_at worker '
    ).split(),
    'saas_connect_voice_receipts': (
        'channel_id created_at error_code external_id model run_id state tenant_id updated_at '
    ).split(),
    'saas_knowledge_batch_items': (
        'batch_id error_code extraction_id message_count ordinal route status tenant_id '
    ).split(),
    'saas_knowledge_batches': (
        'active_until completed_at continuations created_at created_by deadline id params '
        'request_key routes routes_truncated status tenant_id '
    ).split(),
    'saas_knowledge_campaign_fragments': (
        'campaign_id created_at error_code extraction_id id lease_token lease_until '
        'reservation result route source_key status tenant_id updated_at '
    ).split(),
    'saas_knowledge_campaign_sources': (
        'campaign_id channel_id conversation_id history_mode next_start period_end '
        'period_start source_key status tenant_id '
    ).split(),
    'saas_knowledge_campaigns': (
        'analysis_agent_id approved_at approved_by created_at created_by id max_calls '
        'max_cost_microusd max_tokens media_policy photo_model progress reserved_calls '
        'reserved_kinds reserved_tokens sources status tenant_id updated_at version '
        'video_model '
    ).split(),
    'saas_knowledge_candidate_knowledge_reviews': (
        'candidate_id created_at reviewed_by source_hash source_id source_title tenant_id '
    ).split(),
    'saas_knowledge_candidate_sources': (
        'agent_id candidate_id source_id tenant_id '
    ).split(),
    'saas_knowledge_candidates': (
        'archived_at archived_by archived_from_status content content_hash created_at '
        'extraction_id id quality_flags reviewed_at reviewed_by status tenant_id updated_at '
        'version '
    ).split(),
    'saas_knowledge_case_source_reviews': (
        'candidate_id claims_snapshot created_at extraction_id message_id prior_ref '
        'receipt_source_hash review_note reviewed_by reviewed_ref revision source_hash '
        'tenant_id '
    ).split(),
    'saas_knowledge_extractions': (
        'completed_at created_at created_by deadline error_code fingerprint id media_results '
        'request_key route source_digest source_refs status tenant_id '
    ).split(),
    'saas_knowledge_media_cache': (
        'created_at fingerprint metadata state tenant_id token transcript usage '
    ).split(),
    'saas_knowledge_quality_checks': (
        'agent_id campaign_id created_at id response_state retrieval_state source_id '
        'source_updated_at state tenant_id '
    ).split(),
    'saas_media_evidence_receipts': (
        'channel_id code coverage created_at direction external_id media_kind provenance '
        'result source_hash status tenant_id updated_at '
    ).split(),
    'saas_operator_dialog_reads': (
        'channel_id conversation_id last_read_at last_read_message_id lead_id provider '
        'telegram_id tenant_id updated_at '
    ).split(),
    'saas_support_completions': (
        'actor_id channel_id conversation_id created_at input_hash request_id tenant_id '
        'ticket_id '
    ).split(),
    'saas_support_events': (
        'event_key tenant_id ticket_id '
    ).split(),
    'saas_support_ticket_messages': (
        'created_at event_key message_id tenant_id ticket_id '
    ).split(),
    'saas_support_tickets': (
        'assigned_developer_channel_id assigned_developer_conversation_id assigned_to '
        'channel_id completion_note completion_outcome conversation_id created_at created_by '
        'description history_scope id knowledge_candidate_requested next_steps reason '
        'resolved_at source status suggestions summary summary_agent_binding '
        'summary_scope_hash summary_state tenant_id updated_at '
    ).split(),
    'saas_crm_messages': 'id tenant_id external_id direction author_name raw'.split(),
    'saas_tenant_members': 'tenant_id telegram_id'.split(),
    'saas_tenants': ['id'],
    'saas_tenant_webhook_events': (
        'tenant_id fingerprint received_at attempts available_at completed_at error_code event_type external_id lease_token '
        'lease_until payload provider state '
    ).split(),
}
_SCHEMA_INDEXES = [
    'saas_ai_accepted_run_idx',
    'saas_ai_agent_drafts_pending_idx',
    'saas_ai_agent_jobs_ready_idx',
    'saas_ai_agent_runs_usage_idx',
    'saas_ai_agent_token_usage_daily_idx',
    'saas_ai_agent_triggers_active_idx',
    'saas_ai_agent_triggers_setup_key_idx',
    'saas_ai_agents_active_idx',
    'saas_ai_agents_setup_key_idx',
    'saas_ai_agents_tenant_idx',
    'saas_ai_expert_dialog_idx',
    'saas_ai_expert_parent_specialist',
    'saas_ai_inbound_diagnostics_idx',
    'saas_ai_knowledge_chunks_search_idx',
    'saas_ai_knowledge_sources_setup_key_idx',
    'saas_ai_knowledge_tenant_idx',
    'saas_ai_media_grants_once_idx',
    'saas_ai_test_session_actor_idx',
    'saas_ai_test_token_day_idx',
    'saas_ai_test_turn_session_idx',
    'saas_knowledge_campaign_active_idx',
    'saas_knowledge_campaign_pending_idx',
    'saas_knowledge_extraction_route_idx',
    'saas_knowledge_queue_all_idx',
    'saas_knowledge_review_queue_idx',
    'saas_media_evidence_lookup_idx',
    'saas_operator_dialog_reads_member_idx',
    'saas_support_open_route_idx',
    'saas_support_queue_idx',
    'saas_support_ticket_messages_order_idx',
    'saas_tenant_webhook_external_id_idx',
    'saas_tenant_webhook_queue_idx',
]


def _schema_current(conn):
    """Certify the selected schema without DDL, locks on business rows or commits."""
    from tenant_schema_preflight import ready
    from tenant_ai_spend_budget import _SCHEMA_COLUMNS as spend_columns, _SCHEMA_INDEXES as spend_indexes
    from tenant_connect_transport import _SCHEMA_COLUMNS as connect_columns, _SCHEMA_INDEXES as connect_indexes
    from tenant_ai_agent_deletion_schema import _HISTORICAL_AGENT_FKS
    import json
    columns = {**_SCHEMA_COLUMNS, **spend_columns, **connect_columns}
    indexes = [*_SCHEMA_INDEXES, *spend_indexes, *connect_indexes]
    with conn.cursor() as cur:
        if not ready(cur, columns, indexes):
            return False
        # Column presence alone cannot certify the channel-key migration,
        # retirement of Test Mode, archive/history foreign keys or their trigger.
        history_fks = {table: [f'{table}_{column}_history_identity_fkey' for column in names]
                       for table, names in _HISTORICAL_AGENT_FKS.items()}
        cur.execute("""SELECT
          EXISTS(SELECT 1 FROM saas_ai_scope_migrations WHERE version=1)
          AND EXISTS(SELECT 1 FROM saas_ai_compat_migrations WHERE version=1)
          AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_settings
            WHERE operating_mode='test' OR
              (support_case_extraction_enabled AND support_case_extraction_mode='off'))
          AND NOT EXISTS (
            SELECT 1 FROM jsonb_each(%s::jsonb) AS expected(relation, names)
            CROSS JOIN LATERAL jsonb_array_elements_text(expected.names) AS name(value)
            WHERE NOT EXISTS (
              SELECT 1 FROM pg_constraint c
              WHERE c.conrelid=to_regclass(expected.relation) AND c.conname=name.value::name
                AND c.contype='f' AND c.convalidated
                AND c.confrelid='saas_ai_agent_history_identities'::regclass))
          AND EXISTS(SELECT 1 FROM pg_trigger
            WHERE tgrelid='saas_ai_agents'::regclass
              AND tgname='saas_ai_agent_history_identity_sync' AND tgenabled IN ('O','A'))
          AND position('saas_ai_group_channel_legacy_permits' in
            pg_get_functiondef(to_regprocedure('ai_legacy_blocked(uuid,text,text,text)')))>0
          AND EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='saas_support_tickets'::regclass AND conname='saas_support_developer_route_fk'
              AND confrelid='saas_connect_conversations'::regclass AND contype='f' AND convalidated)
          AND EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='saas_support_tickets'::regclass AND conname='saas_support_developer_pair_check'
              AND contype='c' AND convalidated)
          AND NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='saas_ai_agent_settings'::regclass AND conname IN
              ('saas_ai_agent_settings_auto_send_enabled_check',
               'saas_ai_agent_settings_crm_actions_enabled_check'))
          AND NOT EXISTS(SELECT 1 FROM pg_constraint
            WHERE conrelid='saas_ai_expert_tasks'::regclass
              AND conname='saas_ai_expert_tasks_tenant_id_parent_run_id_key')
          AND NOT EXISTS (
            SELECT 1 FROM (VALUES
              ('saas_ai_agents','saas_ai_agents_mode_check','automatic'),
              ('saas_ai_agent_jobs','saas_ai_agent_jobs_state_check','sent'),
              ('saas_ai_agent_drafts','saas_ai_agent_drafts_state_check','sending'),
              ('saas_ai_agent_settings','saas_ai_agent_settings_operating_mode_check','simulation'),
              ('saas_ai_agent_settings','saas_ai_agent_settings_support_case_extraction_mode_check','automatic'),
              ('saas_knowledge_candidates','saas_knowledge_candidates_status_check','archived')
            ) AS expected(relation, name, value)
            WHERE NOT EXISTS(SELECT 1 FROM pg_constraint c
              WHERE c.conrelid=to_regclass(expected.relation) AND c.conname=expected.name
                AND c.convalidated AND position(expected.value in pg_get_constraintdef(c.oid))>0)) AS ready""",
            (json.dumps(history_fks),))
        return (cur.fetchone() or {}).get('ready') is True


def _ensure_platform_schema(conn):
    _ensure_schema(conn)
    # The platform's compatibility cache is process-local. A worker may also
    # initialize another database or namespace; it must still receive its own
    # foundation rather than inheriting readiness from the first connection.
    from tenant_schema_preflight import ready
    with conn.cursor() as cur:
        if ready(cur, {
            'saas_tenants': ['id'],
            'saas_tenant_members': ['tenant_id', 'telegram_id'],
            'saas_tenant_webhook_events': ['tenant_id', 'fingerprint', 'received_at'],
            'saas_crm_messages': ['id', 'tenant_id', 'external_id'],
        }, []):
            return
        from tenant_platform_schema import (migrate_identity_schema,
                                            migrate_operations_schema,
                                            migrate_crm_schema)
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('tenant-platform-schema',0))")
        migrate_identity_schema(cur)
        migrate_operations_schema(cur)
        migrate_crm_schema(cur)


def ensure_schema(conn):
    global _ready
    if _schema_current(conn):
        return
    with _lock:
        # Missing-schema bootstrap shares Connect/Spend's transaction lock and
        # takes it before any application DDL. Recheck after cross-process wait.
        from tenant_schema_preflight import lock_schema
        with conn.cursor() as cur:
            lock_schema(cur)
        if _schema_current(conn):
            return
        _ensure_platform_schema(conn)
        # The legacy platform initializer can commit its own bootstrap. Restore
        # the common migration lock before installing the remaining AI schema.
        with conn.cursor() as cur:
            lock_schema(cur)
        if _schema_current(conn):
            return
        # Monetary reservations are shared by every AI execution path.
        from tenant_ai_spend_budget import ensure_schema as ensure_spend_schema
        ensure_spend_schema(conn)
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
            # Agents are retained for audit, conversation history and spend
            # accounting.  Archiving is the only supported removal path.
            cur.execute("ALTER TABLE saas_ai_agents ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL")
            cur.execute("ALTER TABLE saas_ai_agents ADD COLUMN IF NOT EXISTS archived_by BIGINT NULL")
            cur.execute("ALTER TABLE saas_ai_agents ADD COLUMN IF NOT EXISTS archived_replaced_by UUID NULL")
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_agents_active_idx
              ON saas_ai_agents(tenant_id,created_at,id) WHERE archived_at IS NULL''')
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
        from tenant_ai_agent_deletion_schema import create_schema as create_deletion_schema
        create_deletion_schema(conn)
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
