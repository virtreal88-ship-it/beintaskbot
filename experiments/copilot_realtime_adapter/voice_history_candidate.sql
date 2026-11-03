-- Synthetic review schema only. Not a production migration.
CREATE TABLE saas_operator_voice_sessions (
  session_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL,
  actor_id bigint NOT NULL,
  channel_id text NOT NULL,
  conversation_id text NOT NULL,
  agent_binding text NOT NULL,
  clear_revision bigint NOT NULL CHECK (clear_revision >= 0),
  lease_fence bigint NOT NULL CHECK(lease_fence > 0),
  state text NOT NULL CHECK(state IN ('active','closed')),
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz,
  next_sequence integer NOT NULL CHECK (next_sequence >= 0)
);
CREATE TABLE saas_operator_voice_turns (
  session_id uuid NOT NULL REFERENCES saas_operator_voice_sessions(session_id),
  turn_sequence integer NOT NULL CHECK (turn_sequence >= 0),
  digest text NOT NULL CHECK (length(digest) = 64),
  PRIMARY KEY (session_id,turn_sequence)
);
