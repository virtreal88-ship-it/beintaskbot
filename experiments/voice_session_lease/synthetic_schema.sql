-- Synthetic disposable test schema only. Not a production migration.
CREATE TABLE experiment_voice_session_lease (
  tenant_id uuid NOT NULL,
  actor_id bigint NOT NULL CHECK (actor_id > 0),
  channel_id uuid NOT NULL,
  conversation_id text NOT NULL,
  session_id uuid NOT NULL,
  fence_hash bytea NOT NULL CHECK (octet_length(fence_hash)=32),
  owner_hash bytea NOT NULL CHECK (octet_length(owner_hash)=32),
  state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','quarantined')),
  heartbeat_until timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, actor_id),
  UNIQUE (session_id)
);
