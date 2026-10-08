"""Re-read membership, module policy and encrypted connection for each operation."""
import json
from tenant_platform import _fernet, _read_tenant_workflow
from tenant_policy import TenantPolicy
from tenant_linear_policy import TenantLinearError


def context(cur, session):
    if not session:
        raise TenantLinearError('İcazə yoxdur.', 403)
    tenant, user = str(session['tenant_id']), int(session['telegram_id'])
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
      JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, user))
    profile = cur.fetchone()
    if not profile or not profile['active'] or profile['tenant_status'] != 'active':
        raise TenantLinearError('İcazə yoxdur.', 403)
    profile = {**profile, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)}
    if not TenantPolicy(profile).allows('linear'):
        raise TenantLinearError('Linear səhifəsinə icazə yoxdur.', 403)
    cur.execute("SELECT status,secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='linear' FOR SHARE", (tenant,))
    connection = cur.fetchone()
    config = (connection or {}).get('metadata', {}).get('settings', {})
    if not connection or connection['status'] != 'connected' or not config.get('team_id') or config.get('workflow', {}).get('enabled') is not True:
        raise TenantLinearError('Linear iş qaydaları hələ aktiv deyil.', 409)
    try:
        payload = json.loads(_fernet().decrypt(bytes(connection['secrets'])))
        if payload['tenant_id'] != tenant or payload['provider'] != 'linear':
            raise ValueError('Credential owner mismatch')
        key = payload['api_key']
    except Exception:
        raise TenantLinearError('Linear təhlükəsizlik ayarlarını yoxlayın.', 503) from None
    return profile, config, key, str(connection['updated_at'])
