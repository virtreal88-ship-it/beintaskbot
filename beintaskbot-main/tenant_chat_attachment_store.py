"""Read one attachment through current membership and deal scope, never a client URL."""
import uuid
from tenant_platform import _connect, _read_tenant_workflow, _fernet
from tenant_policy import TenantPolicy
from tenant_ai_agent_access import tenant_status_allows_management
from tenant_linear_policy import TenantLinearError


def load(session: dict, lead_id: object, message_id: object, *, channel_id=None, conversation_id=None) -> dict:
    tenant = str(uuid.UUID(str(session['tenant_id'])))
    actor, lead = int(session['telegram_id']), int(str(lead_id))
    if not 0 < lead < 2**63 or not isinstance(message_id, str) or not 0 < len(message_id) <= 500:
        raise ValueError('Invalid attachment identity')
    is_connect = message_id.startswith('connect:')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
              JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid
              AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, actor))
            member = cur.fetchone()
            # Read-only Connect previews follow the same lifecycle gate as Dialogs.
            status_allowed = bool(member) and (tenant_status_allows_management(member['tenant_status'])
                if is_connect else member['tenant_status'] == 'active')
            if not member or member['active'] is not True or not status_allowed:
                raise TenantLinearError('İcazə yoxdur.', 403)
            profile = {**member, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)}
            cur.execute('''SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid
              AND kommo_lead_id=%s AND deleted_at IS NULL FOR SHARE''', (tenant, lead))
            deal = cur.fetchone()
            policy = TenantPolicy(profile)
            allowed = policy.allows('dialogs') if is_connect else policy.can_access_deal(deal)
            if not allowed:
                raise TenantLinearError('Bu çat üçün icazəniz yoxdur.', 403)
            cur.execute('''SELECT id,external_id,channel,direction,media_url,message_type,raw FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND external_id=%s''', (tenant, lead, message_id))
            row = cur.fetchone()
            if not row:
                raise TenantLinearError('Fayl tapılmadı.', 404)
            if is_connect:
                from tenant_connect_transport import connect_route
                reference = str(uuid.UUID(message_id.removeprefix('connect:')))
                # Read a retained attachment from an inactive channel. Sending,
                # Copilot and AI media execution still require an active route.
                route = connect_route(cur, tenant, lead, historical_read=True)
                if (not route or not route.get('api_key') or bool(channel_id) != bool(conversation_id)
                        or (channel_id and str(channel_id) != str(route['channel_id']))
                        or (conversation_id and str(conversation_id) != str(route['conversation_id']))
                        or row.get('channel') != 'connect:' + route['channel_id']
                        or row.get('message_type') not in {'image', 'audio', 'voice', 'video', 'document'}
                        or row.get('direction') not in {'incoming', 'outgoing'}):
                    raise TenantLinearError('Fayl bu dialoqda və kanalda mövcud deyil.', 404)
                raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
                if raw.get('conversationId') and raw['conversationId'] != route['conversation_id']:
                    raise TenantLinearError('Fayl bu dialoqda mövcud deyil.', 404)
                return {'provider': 'connect', 'row': dict(row), 'route': route, 'message_id': reference}
            cur.execute('''SELECT account_domain,connected_at,status,secrets FROM saas_tenant_integrations
              WHERE tenant_id=%s::uuid AND provider='kommo' FOR SHARE''', (tenant,))
            connection = cur.fetchone()
            if not connection or connection['status'] != 'connected' or not connection['secrets']:
                raise TenantLinearError('Kommo bağlantısını yoxlayın.', 409)
        conn.commit()
    return {'row': dict(row), 'connection': dict(connection)}


def token(item: dict) -> str:
    import json
    tokens = json.loads(_fernet().decrypt(bytes(item['connection']['secrets'])))
    value = tokens.get('access_token') if isinstance(tokens, dict) else None
    if not isinstance(value, str) or not value:
        raise TenantLinearError('Kommo bağlantısını yeniləyin.', 409)
    return value
