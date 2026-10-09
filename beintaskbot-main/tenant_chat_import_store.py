"""Fresh read authority and connection identity; never exports credentials."""
import uuid
from tenant_platform import _connect, _ensure_schema, _read_tenant_workflow
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError


def authorize(session: dict, lead: int) -> str:
    tenant, actor = str(uuid.UUID(str(session['tenant_id']))), int(session['telegram_id'])
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
              JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s''', (tenant, actor))
            member = cur.fetchone()
            if not member or member['active'] is not True or member['tenant_status'] != 'active':
                raise TenantLinearError('İcazə yoxdur.', 403)
            policy = TenantPolicy({**member, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)})
            cur.execute('SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND deleted_at IS NULL', (tenant, lead))
            if not policy.can_access_deal(cur.fetchone()):
                raise TenantLinearError('Çat tarixçəsi üçün icazəniz yoxdur.', 403)
            cur.execute("SELECT account_domain,connected_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected'", (tenant,))
            integration = cur.fetchone()
            if not integration or not integration.get('account_domain'):
                raise TenantLinearError('Kommo qoşulmayıb.', 409)
    return str(integration['account_domain']) + ':' + str(integration['connected_at'])
