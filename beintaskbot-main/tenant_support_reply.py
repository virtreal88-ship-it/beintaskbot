"""Exact support-case route and confirmed replies sent from the case card."""
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_access import access, assert_route
from tenant_support_access import require


def require_route(cur, session, ticket_id, *, lock=False):
    tenant, _, _ = require(cur, session, ticket_id)
    cur.execute("""SELECT c.lead_id,t.channel_id::text,t.conversation_id
        FROM saas_support_tickets t JOIN saas_connect_conversations c
          ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id
          AND c.conversation_id=t.conversation_id
        WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid""" + (' FOR SHARE OF t' if lock else ''),
        (tenant, identifier(ticket_id)))
    route = cur.fetchone()
    if not route:
        raise TenantLinearError('Связанный диалог недоступен.', 404)
    return route


def require_dialog_view(cur, session, route):
    tenant, _, policy = access(cur, session, 'dialogs')
    assert_route(cur, tenant, policy, 'connect', route['conversation_id'],
                 route['channel_id'], route['lead_id'])
    return tenant


def context(session, ticket_id):
    from tenant_chat_send_store import authority, ensure_schema
    from tenant_platform import TenantPlatformError, _connect
    from tenant_support_inbox_store import public

    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            route = require_route(cur, session, ticket_id)
            try:
                tenant = require_dialog_view(cur, session, route)
            except TenantLinearError as error:
                if error.status not in (403, 404, 409):
                    raise
                return {'route': route, 'can_send': False, 'messages': []}

            try:
                access(cur, session, 'dialogs', 'update')
                _, connection, _ = authority(cur, session, route['lead_id'])
                can_send = connection == ['connect', route['channel_id'], route['conversation_id']]
            except TenantLinearError as error:
                if error.status not in (403, 404, 409, 503):
                    raise
                can_send = False
            except TenantPlatformError as error:
                if str(error) not in {'native_source_unavailable', 'native_source_changed'}:
                    raise
                can_send = False
            cur.execute("""SELECT DISTINCT m.id,m.external_id,m.direction,m.author_name,
                    m.body,m.message_type,m.happened_at,m.created_at
                FROM saas_chat_send_receipts r JOIN saas_crm_messages m
                  ON m.tenant_id=r.tenant_id AND m.external_id='connect:'||r.message_id
                  AND m.kommo_lead_id=r.lead_id
                WHERE r.tenant_id=%s::uuid AND r.lead_id=%s AND r.state='accepted'
                  AND r.route->>'support_ticket_id'=%s
                  AND m.channel=%s AND m.direction='outgoing' AND m.message_type='text'
                ORDER BY m.happened_at DESC NULLS LAST,m.id DESC LIMIT 50""",
                (tenant, route['lead_id'], identifier(ticket_id), 'connect:'+route['channel_id']))
            return {'route': route, 'can_send': can_send, 'messages': [public(row) for row in reversed(cur.fetchall())]}
