"""A small, permission-filtered revision for WhatsApp dialog refreshes."""
from tenant_dialog_access import sql_filter
from tenant_dialog_lifecycle import enabled, visibility_sql
from tenant_operator_access import access
from tenant_platform import _connect


def visible_revision(cur, tenant, policy, selected=None):
    source = 'c.connect_channel_id::text'
    allowed = sql_filter(policy, "'connect'", source)
    visible = visibility_sql(cur, policy, "'connect'", source, 'c.conversation_id')
    changed = 'c.updated_at'
    join = ''
    if enabled(cur, tenant):
        # Group assignment changes share the existing list refresh stream.
        # Apply the same exact route and ACL used for ordinary message changes.
        changed = 'GREATEST(c.updated_at,group_change.updated_at)'
        join = """LEFT JOIN LATERAL (
          SELECT GREATEST(e.accepted_at,e.closed_at,w.updated_at) AS updated_at
          FROM saas_dialog_episodes e
          LEFT JOIN saas_dialog_workflows w ON w.tenant_id=e.tenant_id AND w.id=e.id
            AND w.channel_id=e.group_leader_channel_id AND w.conversation_id=e.conversation_id
          WHERE e.tenant_id=c.tenant_id AND e.conversation_id=c.conversation_id
            AND e.group_leader_channel_id IS NOT NULL
            AND c.conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$'
          ORDER BY e.accepted_at DESC LIMIT 1
        ) group_change ON TRUE"""
    cur.execute(f"""SELECT COALESCE(MAX({changed})::text,'0') AS revision,
        MAX({changed}) FILTER(WHERE c.connect_channel_id=%s::uuid AND c.conversation_id=%s)
          = MAX({changed}) AS selected_latest
        FROM saas_connect_conversations c
        JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id
          AND ch.connect_channel_id=c.connect_channel_id AND ch.active
        {join}
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
