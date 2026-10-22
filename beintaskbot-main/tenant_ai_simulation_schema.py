"""Additive schema for the isolated AI Simulation queue and virtual ledger."""
from threading import Lock
from tenant_platform import _ensure_schema

_ready = False
_lock = Lock()


def ensure_schema(conn):
    global _ready
    _ensure_schema(conn)
    from tenant_ai_agents_schema import ensure_schema as ensure_agents_schema
    ensure_agents_schema(conn)
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ('tenant-ai-simulation-schema',))
            # CRM message IDs are globally unique today; the composite index
            # makes tenant ownership an explicit FK invariant for simulation.
            cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS saas_crm_messages_tenant_id_idx ON saas_crm_messages(tenant_id,id)')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_simulation_configs(
              tenant_id UUID PRIMARY KEY REFERENCES saas_tenants(id) ON DELETE CASCADE,
              enabled BOOLEAN NOT NULL DEFAULT FALSE,
              emergency_stop BOOLEAN NOT NULL DEFAULT FALSE,
              agent_id UUID NULL,
              channel_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
              conversation_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
              starts_at TIMESTAMPTZ NULL,
              ends_at TIMESTAMPTZ NULL,
              token_budget BIGINT NOT NULL DEFAULT 0 CHECK(token_budget>=0),
              message_limit INTEGER NOT NULL DEFAULT 0 CHECK(message_limit>=0),
              max_parallel INTEGER NOT NULL DEFAULT 1 CHECK(max_parallel BETWEEN 1 AND 10),
              max_calls_per_event INTEGER NOT NULL DEFAULT 3 CHECK(max_calls_per_event BETWEEN 1 AND 8),
              max_output_tokens INTEGER NOT NULL DEFAULT 800 CHECK(max_output_tokens BETWEEN 32 AND 1800),
              version INTEGER NOT NULL DEFAULT 1,
              updated_by BIGINT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              FOREIGN KEY(tenant_id,updated_by) REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE RESTRICT
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_simulation_queue(
              id UUID PRIMARY KEY,
              tenant_id UUID NOT NULL,
              channel_id UUID NOT NULL,
              conversation_id TEXT NOT NULL,
              lead_id BIGINT NOT NULL,
              source_message_id UUID NOT NULL,
              source_event_id TEXT NOT NULL,
              event_fingerprint TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','processing','completed','skipped','failed','unknown')),
              attempts INTEGER NOT NULL DEFAULT 0,
              lease_token UUID NULL,
              lease_until TIMESTAMPTZ NULL,
              provider_started_at TIMESTAMPTZ NULL,
              error_code TEXT NOT NULL DEFAULT '',
              available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              completed_at TIMESTAMPTZ NULL,
              UNIQUE(tenant_id,channel_id,conversation_id,source_message_id),
              UNIQUE(tenant_id,id),
              FOREIGN KEY(tenant_id) REFERENCES saas_tenants(id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,source_message_id) REFERENCES saas_crm_messages(tenant_id,id) ON DELETE CASCADE
            )''')
            cur.execute("CREATE INDEX IF NOT EXISTS saas_ai_simulation_queue_due_idx ON saas_ai_simulation_queue(available_at,created_at) WHERE state='queued'")
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_simulation_runs(
              id UUID PRIMARY KEY,
              tenant_id UUID NOT NULL,
              queue_id UUID NOT NULL UNIQUE,
              channel_id UUID NOT NULL,
              conversation_id TEXT NOT NULL,
              source_message_id UUID NOT NULL,
              primary_agent_id UUID NULL,
              outcome TEXT NOT NULL,
              virtual_reply TEXT NOT NULL DEFAULT '',
              virtual_stop_reason TEXT NOT NULL DEFAULT '',
              virtual_triggers JSONB NOT NULL DEFAULT '[]'::jsonb,
              knowledge_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
              expert_results JSONB NOT NULL DEFAULT '[]'::jsonb,
              events JSONB NOT NULL DEFAULT '[]'::jsonb,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              FOREIGN KEY(tenant_id,queue_id) REFERENCES saas_ai_simulation_queue(tenant_id,id) ON DELETE CASCADE,
              FOREIGN KEY(tenant_id,source_message_id) REFERENCES saas_crm_messages(tenant_id,id) ON DELETE CASCADE
            )''')
            cur.execute('''CREATE INDEX IF NOT EXISTS saas_ai_simulation_runs_tenant_idx
              ON saas_ai_simulation_runs(tenant_id,created_at DESC)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_simulation_dialog_state(
              tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
              channel_id UUID NOT NULL,
              conversation_id TEXT NOT NULL,
              session_started_at TIMESTAMPTZ NOT NULL,
              revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
              last_queue_id UUID NOT NULL,
              virtual_state JSONB NOT NULL DEFAULT '{}'::jsonb,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY(tenant_id,channel_id,conversation_id),
              FOREIGN KEY(tenant_id,last_queue_id) REFERENCES saas_ai_simulation_queue(tenant_id,id) ON DELETE CASCADE
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_simulation_usage(
              tenant_id UUID NOT NULL,
              queue_id UUID NOT NULL,
              call_no INTEGER NOT NULL,
              agent_id UUID NOT NULL,
              call_kind TEXT NOT NULL CHECK(call_kind IN ('primary','expert')),
              reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens>0),
              actual_tokens INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL CHECK(state IN ('reserved','succeeded','failed','unknown','released')),
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              completed_at TIMESTAMPTZ NULL,
              PRIMARY KEY(tenant_id,queue_id,call_no),
              FOREIGN KEY(tenant_id,queue_id) REFERENCES saas_ai_simulation_queue(tenant_id,id) ON DELETE CASCADE
            )''')
        _ready = True
