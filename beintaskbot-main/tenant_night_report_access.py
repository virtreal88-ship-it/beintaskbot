"""Read-only company report authority; no AI-configuration permission is granted."""
from tenant_ai_agent_access import tenant_status_allows_management
from tenant_linear_policy import TenantLinearError, identifier


def authorize(cur,profile):
    if not isinstance(profile,dict): raise TenantLinearError('Нет доступа.',403)
    tenant=identifier(profile.get('tenant_id'))
    try: actor=int(profile.get('telegram_id'))
    except (TypeError,ValueError): raise TenantLinearError('Нет доступа.',403) from None
    cur.execute('''SELECT m.role,m.active,m.permissions,t.status,t.onboarding,t.name
      FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s''',(tenant,actor))
    row=cur.fetchone()
    if not row or row.get('active') is not True or not tenant_status_allows_management(row.get('status')):
        raise TenantLinearError('Нет доступа к активной компании.',403)
    allowed=row['role']=='owner' or (row['role']!='master' and 'night_report' in set(row.get('permissions') or []))
    if not allowed and row['role']=='admin':
        cur.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='members'",(tenant,))
        settings=(cur.fetchone() or {}).get('value') or {}
        allowed=(settings.get(str(actor)) or {}).get('ai_agents_manage') is True
    if not allowed: raise TenantLinearError('Владелец должен выдать доступ к отчёту ночной смены.',403)
    return tenant,dict(row)
