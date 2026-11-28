"""A small, permission-filtered revision for WhatsApp dialog refreshes."""
from tenant_dialog_access import sql_filter
from tenant_dialog_lifecycle import visibility_sql
from tenant_operator_access import access
from tenant_platform import _connect


def visible_revision(cur, tenant, policy, selected=None):
    source = 'c.connect_channel_id::text'
    allowed = sql_filter(policy, "'connect'", source)
    visible = visibility_sql(cur, policy, "'connect'", source, 'c.conversation_id')
    cur.execute(f"""SELECT COALESCE(MAX(c.updated_at)::text,'0') AS revision,
        MAX(c.updated_at) FILTER(WHERE c.connect_channel_id=%s::uuid AND c.conversation_id=%s)
          = MAX(c.updated_at) AS selected_latest
        FROM saas_connect_conversations c
        JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id
          AND ch.connect_channel_id=c.connect_channel_id AND ch.active
        WHERE c.tenant_id=%s::uuid AND ({allowed}) AND ({visible})
        AND NOT EXISTS (SELECT 1 FROM saas_operator_dialog_removals removed
            WHERE removed.tenant_id=c.tenant_id AND removed.provider='connect'
              AND removed.channel_id=c.connect_channel_id AND removed.conversation_id=c.conversation_id
              AND removed.lead_id=c.lead_id
              AND (c.last_message_at IS NULL OR removed.removed_at >= c.last_message_at))""",
        (*(selected or (None,None)),tenant))
    row = cur.fetchone()
    return {'revision':row['revision'],**({'selected_latest':row['selected_latest'] is True} if selected else {})}


def read_revision(profile, selected=None):
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '1500ms'")
        cur.execute("SET LOCAL lock_timeout = '250ms'")
        tenant, _, policy = access(cur, profile, 'dialogs')
        return visible_revision(cur, tenant, policy, selected)
