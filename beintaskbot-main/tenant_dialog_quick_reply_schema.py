"""Personal dismissal timestamps, with no message text stored in preferences."""


def migrate(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_quick_reply_dismissals (
      tenant_id UUID NOT NULL, actor_id BIGINT NOT NULL, channel_id UUID NOT NULL,
      reply_key TEXT NOT NULL, dismissed_through TIMESTAMPTZ NOT NULL,
      PRIMARY KEY (tenant_id, actor_id, channel_id, reply_key),
      FOREIGN KEY (tenant_id, actor_id) REFERENCES saas_tenant_members(tenant_id, telegram_id) ON DELETE CASCADE
    )''')
