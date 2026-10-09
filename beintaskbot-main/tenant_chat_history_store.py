"""Fresh membership/deal authority and bounded history in one transaction."""
import uuid
from tenant_platform import _connect, _read_tenant_workflow
from tenant_chat_history_schema import ensure_schema
from tenant_chat_history_cursor import encode, decode
from tenant_crm_queries import public_crm_row
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError


def page(session: dict, lead_id: object, cursor: str = '', limit: object = 120) -> dict:
    lead, size = int(str(lead_id)), int(str(limit))
    tenant, actor = str(uuid.UUID(str(session['tenant_id']))), int(session['telegram_id'])
    if not 0 < lead < 2**63 or not 1 <= size <= 120:
        raise ValueError('Invalid page')
    position = decode(cursor, session, lead)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
              JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, actor))
            profile = cur.fetchone()
            if not profile or profile['active'] is not True or profile['tenant_status'] != 'active':
                raise TenantLinearError('İcazə yoxdur.', 403)
            policy = TenantPolicy({**profile, 'tenant_id':tenant, 'workflow':_read_tenant_workflow(cur, tenant)})
            cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL FOR SHARE', (tenant, lead))
            if not policy.can_access_deal(cur.fetchone()):
                raise TenantLinearError('Çat tarixçəsi üçün icazəniz yoxdur.', 403)
            condition = ' AND (COALESCE(happened_at,created_at),external_id)<(%s::timestamptz,%s)' if position else ''
            values = [tenant, lead, *(position or ()), size + 1]
            cur.execute('''SELECT external_id,direction,channel,author_name,body,message_type,media_url,happened_at,created_at,
              COALESCE(happened_at,created_at) AS position_at FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s''' + condition + '''
              ORDER BY COALESCE(happened_at,created_at) DESC,external_id DESC LIMIT %s''', values)
            rows = cur.fetchall()
        conn.commit()
    more = len(rows) > size
    selected = rows[:size]
    next_cursor = encode(session, lead, selected[-1]) if more else None
    messages = []
    for row in reversed(selected):
        payload = {key:value for key,value in row.items() if key != 'position_at'}
        messages.append(public_crm_row(payload))
    return {'messages':messages, 'has_more':more, 'next_cursor':next_cursor, 'source':'cache'}
