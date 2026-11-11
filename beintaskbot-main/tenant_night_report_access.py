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
    company={**dict(row),'_report_actor':actor}
    from tenant_scoped_access import scoped_access_enabled
    if scoped_access_enabled():
        from tenant_scoped_access_store import read_access_profile
        from tenant_policy import TenantPolicy
        current=read_access_profile(cur,tenant,actor)
        if not current: raise TenantLinearError('Нет доступа.',403)
        policy=TenantPolicy(current)
        company['_report_policy']=policy
        selected=policy.action_scope('night_report','view')
        if selected is not None:
            if selected=='none' or row['role']=='master':
                raise TenantLinearError('Владелец должен выдать доступ к отчёту ночной смены.',403)
            return tenant,company
    allowed=row['role']=='owner' or (row['role']!='master' and 'night_report' in set(row.get('permissions') or []))
    if not allowed and row['role']=='admin':
        cur.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='members'",(tenant,))
        settings=(cur.fetchone() or {}).get('value') or {}
        allowed=(settings.get(str(actor)) or {}).get('ai_agents_manage') is True
    if not allowed: raise TenantLinearError('Владелец должен выдать доступ к отчёту ночной смены.',403)
    # An absent report cell retains the legacy aggregate contract, not a new
    # independent grant. Explicit report scope is needed to override narrower
    # dialog/support access. The rollout-off behavior is unchanged.
    policy=company.get('_report_policy')
    if policy:
        for module in ('support','dialogs'):
            if policy.action_scope(module,'view') not in {None,'all'}:
                raise TenantLinearError('night_report_requires_unrestricted_'+module+'_read',403)
    return tenant,company


def read_scope(company):
    policy=company.get('_report_policy')
    return policy.action_scope('night_report','view') if policy else None


def source_filter(company, source='c.connect_channel_id::text'):
    """Reuse verified current source ownership, with the independent report cell.

    This adapter is local to report reads. It never changes general dialogs or
    grants a source mutation, a provider fetch, or AI management authority.
    """
    policy=company.get('_report_policy')
    if read_scope(company) is None:return 'TRUE'
    from tenant_dialog_access import sql_filter
    class ReportSourcePolicy:
        profile=policy.profile
        def action_scope(self, module, action):
            assert module=='dialogs' and action=='view'
            return policy.action_scope('night_report','view')
    return sql_filter(ReportSourcePolicy(), "'connect'", source)


def ticket_filter(company, alias='t'):
    if read_scope(company) is None:return 'TRUE',[]
    from tenant_scoped_access_store import resource_scope_sql
    return resource_scope_sql(company['_report_policy'].profile,'night_report','view',alias)



def access_version(cur,tenant,company):
    """Opaque authorized-set identity, never a substitute for row authorization.

    Pages must not append old broad rows after a department/source/permission
    change. Hash only currently authorized IDs, never a tenant-wide hidden count.
    Legacy authority has a snapshot too: adding/removing a report override must
    invalidate already-loaded broad/narrow pages in either direction.
    This also restarts pagination when an authorized row is added or removed.
    """
    selected=read_scope(company)
    import hashlib
    import json
    cur.execute("SELECT md5(COALESCE(string_agg(c.connect_channel_id::text,',' ORDER BY c.connect_channel_id),'')) AS version FROM saas_connect_channels c WHERE c.tenant_id=%s::uuid AND "+source_filter(company),(tenant,))
    source_version=cur.fetchone()['version']
    predicate,args=ticket_filter(company)
    cur.execute("SELECT md5(COALESCE(string_agg(t.id::text,',' ORDER BY t.id),'')) AS version FROM saas_support_tickets t WHERE t.tenant_id=%s::uuid AND "+predicate,(tenant,*args))
    ticket_version=cur.fetchone()['version']
    identity=[tenant,str(company['_report_actor']),selected or 'legacy',source_version,ticket_version]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def require_version(current, expected):
    if expected and current!=expected:
        raise TenantLinearError('Компания или права изменились. Откройте отчёт заново.',409)
