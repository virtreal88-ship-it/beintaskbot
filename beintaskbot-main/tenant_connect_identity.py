"""Read-only gate for provider-confirmed split direct-chat identities.

Historical messages and assignments are never merged here. The signed Connect
receipt is the evidence; names, avatars and numeric suffixes are not identities.
"""

def conflicted(cur, tenant, channel, conversation):
    cur.execute("""SELECT 1 FROM saas_crm_messages m
      WHERE m.tenant_id=%s::uuid AND m.channel=%s
        AND m.raw->>'conversationIdentityConflict'='true'
        AND (m.raw->>'conversationId'=%s OR m.raw->'directIdentity'->>'lid'=%s
             OR m.raw->'directIdentity'->>'jid'=%s)
      LIMIT 1""", (tenant, 'connect:' + str(channel), str(conversation), str(conversation), str(conversation)))
    return bool(cur.fetchone())
