import pytest
from test_employee_card_scopes import actor
from tenant_employee_policy import employee_action_allowed, creation_permission_allowed
from tenant_member_store import _creation_department, MemberCardError
from tenant_scoped_access import validate_access_scopes
from tenant_member_access import access_snapshot
from tenant_policy import TenantPolicy

D1='00000000-0000-0000-0000-000000009301'
D2='00000000-0000-0000-0000-000000009302'


def creator(scope):
    p=actor('none','none');p['department_id']=D1
    p['workflow']['policies'][0]['value']['2']['access_scopes']['employees']['create']=scope
    return p


class DepartmentCursor:
    def __init__(self, available=True): self.available=available;self.calls=[]
    def execute(self, sql, args): self.calls.append((sql,args))
    def fetchone(self): return {'id':D1} if self.available else None


@pytest.mark.parametrize('scope,department,allowed', [
    ('department',D1,True),('department',D2,False),('department',None,False),
    ('all',D1,True),('all',D2,True),('all',None,True),('none',D1,False),('self',D1,False)])
def test_creation_scope_and_department_predicate(monkeypatch,scope,department,allowed):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    cur=DepartmentCursor()
    if allowed:
        assert _creation_department(cur,'a',creator(scope),department)==department
        if department: assert 'FOR SHARE' in cur.calls[0][0]
    else:
        with pytest.raises(MemberCardError): _creation_department(cur,'a',creator(scope),department)


def test_department_missing_or_archived_and_forged_self_fail_closed(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('department');p['department_id']=None
    assert not employee_action_allowed(p,'create')
    with pytest.raises(MemberCardError): _creation_department(DepartmentCursor(False),'a',creator('department'),D1)
    with pytest.raises(ValueError): validate_access_scopes({'employees':{'create':'self'}})
    assert not employee_action_allowed(creator('self'),'create')
    assert access_snapshot(creator('all'))['action_scope_levels']['employees']['create']==['none','department','all']
    assert TenantPolicy(creator('all')).public_capabilities()['pages']['employees']


def test_creation_cannot_delegate_unrestricted_masked_grants(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('all');p['permissions']=['employees','tasks','support']
    p['workflow']['policies'][0]['value']['2']['access_scopes']['tasks']={'view':'self'}
    assert not creation_permission_allowed(p,'employees')
    assert not creation_permission_allowed(p,'tasks')
    assert not creation_permission_allowed(p,'finance')
    assert creation_permission_allowed(p,'support')


def test_gate_off_and_sparse_create_preserve_legacy(monkeypatch):
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED',raising=False)
    assert employee_action_allowed(creator('none'),'create')
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    assert employee_action_allowed(actor('none','none'),'create')
    p=actor('none','none');p['permissions']=[]
    assert not employee_action_allowed(p,'create')


def test_native_boolean_restrictions_cannot_be_delegated_as_broad_grants(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('all');p['role']='admin';p['permissions']=['employees','tasks'];p['modules']={'tasks':True}
    settings=p['workflow']['policies'][0]['value']['2']
    settings['native_crm_actions']={'tasks':{'delete':False}}
    assert not creation_permission_allowed(p,'tasks')
    del settings['native_crm_actions']
    assert creation_permission_allowed(p,'tasks')


def test_sparse_creation_preserves_admission_not_restricted_grant_elevation(monkeypatch):
    from tenant_member_store import _creation_grants
    p=creator('all');p['permissions']=['employees','tasks'];p['modules']={'tasks':True}
    settings=p['workflow']['policies'][0]['value']['2']
    del settings['access_scopes']['employees']['create']
    settings['access_scopes']['tasks']={'view':'self','create':'none','update':'none','delete':'none'}
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    assert employee_action_allowed(p,'create')
    assert not creation_permission_allowed(p,'tasks')
    assert not creation_permission_allowed(p,'employees')
    with pytest.raises(MemberCardError): _creation_grants(p,['tasks'])
    _creation_grants(p,[])
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED',raising=False)
    assert employee_action_allowed(p,'create')
    assert creation_permission_allowed(p,'tasks')


def test_customers_alias_does_not_delegate_denied_company_authority(monkeypatch):
    from tenant_member_store import _creation_grants
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('all');p['permissions']=['employees','customers'];p['modules']={'customers':True}
    p['workflow']['policies'][0]['value']['2']['access_scopes']['companies']={
        'view':'none','create':'none','update':'none','delete':'none'}
    recipient={**p,'telegram_id':3,'role':'manager','permissions':['customers']}
    assert not TenantPolicy(p).allows_action('companies','update')
    assert TenantPolicy(recipient).allows_action('companies','update')
    assert not creation_permission_allowed(p,'customers')
    with pytest.raises(MemberCardError):_creation_grants(p,['customers'],role='manager')


def test_u_attenuates_new_grants_role_expansion_but_preserves_retained_rights(monkeypatch):
    from tenant_member_store import _selected_membership, _card_access
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('all');p['permissions']=['employees','tasks','customers'];p['modules']={'tasks':True,'customers':True}
    scopes=p['workflow']['policies'][0]['value']['2']['access_scopes']
    scopes['employees']['update']='all'
    scopes['tasks']={'view':'self','update':'none','create':'none','delete':'none'}
    scopes['companies']={'view':'none','update':'none','create':'none','delete':'none'}
    target={'tenant_id':'a','telegram_id':3,'role':'worker','active':True,'permissions':[]}
    with pytest.raises(MemberCardError):_selected_membership(p,target,'worker',['tasks'],target=3,strict=True)
    target['permissions']=['customers']
    with pytest.raises(MemberCardError):_selected_membership(p,target,'manager',['customers'],target=3,strict=True)
    target['permissions']=['tasks']
    assert _selected_membership(p,target,'worker',['tasks'],target=3,strict=True)==('worker',['tasks'])
    fields,_=_card_access(p,target)
    assert 'display_name' in fields
    assert 'access_settings' not in fields and 'permission_changes' not in fields


def test_ai_inheritance_is_evaluated_without_copying_creator_admin_flags(monkeypatch):
    from tenant_employee_policy import delegated_permissions_allowed
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('all');p['role']='admin';p['permissions']=['employees','ai_agents','dialogs','support','night_report']
    settings=p['workflow']['policies'][0]['value']['2'];settings['ai_agents_manage']=True
    settings['access_scopes']['dialogs']={'view':'self','update':'none'}
    settings['access_scopes']['support']={'view':'none','create':'none','update':'none'}
    assert not delegated_permissions_allowed(p,['dialogs','support'],role='manager')
    # The administrative role itself is blocked by the store; its flag is not
    # silently copied to any new account, even when raw AI permission is held.
    fresh={**p,'telegram_id':3,'permissions':['ai_agents']}
    assert not TenantPolicy(fresh).legacy_allows('ai_agents')


def test_hot_order_defaults_and_approval_bypass_cannot_be_delegated(monkeypatch):
    from tenant_employee_policy import delegated_permissions_allowed
    from tenant_hot_order_policy import HotOrderPolicy
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('all');p['permissions']=['employees','hot_orders'];p['modules']={'hot_orders':True}
    settings=p['workflow']['policies'][0]['value']['2'];settings['hot_order_create']=False
    p['workflow']['policies'].append({'policy_key':'hot_orders','value':{'create_roles':['manager']}})
    assert not HotOrderPolicy(TenantPolicy(p)).allowed('create')
    assert HotOrderPolicy(TenantPolicy({**p,'telegram_id':3})).allowed('create')
    assert not delegated_permissions_allowed(p,['hot_orders'],role='manager')
    settings['hot_order_create']=True;settings['hot_order_completion_requires_admin']=True
    p['workflow']['policies'][-1]['value']['completion_requires_admin']=False
    assert not delegated_permissions_allowed(p,['hot_orders'],role='manager')


def test_provider_markers_pipelines_and_task_approval_are_not_implicitly_copied(monkeypatch):
    from tenant_employee_policy import delegated_permissions_allowed
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('all');p['role']='operator';p['permissions']=['employees','tasks'];p['modules']={'tasks':True}
    assert TenantPolicy(p).task_scope()['kind']=='marker'
    assert not delegated_permissions_allowed(p,['tasks'],role='worker')
    p['role']='manager';p['permissions'].append('customers');p['modules']['customers']=True
    p['workflow']['pipelines']=[{'pipeline_id':11,'owner_telegram_id':2},{'pipeline_id':22,'owner_telegram_id':3}]
    assert not delegated_permissions_allowed(p,['customers'],role='manager',recipient_id=3)
    p['role']='admin';p['workflow']['policies'][0]['value']['2']['completion_requires_admin']=True
    assert not delegated_permissions_allowed(p,['tasks'],role='worker')


def test_equivalent_all_grants_remain_delegable_without_narrower_restrictions(monkeypatch):
    from tenant_employee_policy import delegated_permissions_allowed, member_permission_change_allowed
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=creator('all');p['role']='admin';p['permissions']=['employees','tasks','customers'];p['modules']={'tasks':True,'customers':True}
    scopes=p['workflow']['policies'][0]['value']['2']['access_scopes']
    for module in ['tasks','customers','companies']:
        scopes[module]={action:'all' for action in ['view','create','update','delete']}
    assert delegated_permissions_allowed(p,['tasks','customers'],role='manager')
    assert creation_permission_allowed(p,'tasks')
    target={'tenant_id':'a','telegram_id':3,'role':'worker','active':True,'permissions':[]}
    assert member_permission_change_allowed(p,target,'worker',['tasks'])
    scopes['companies']['update']='department'
    assert not delegated_permissions_allowed(p,['customers'],role='manager')


@pytest.mark.parametrize('enabled,lock',[('true','FOR UPDATE'),('false','FOR SHARE')])
def test_member_write_fences_policy_defaults_before_members(monkeypatch,enabled,lock):
    from tenant_member_store import _locked_members
    from test_tenant_member_store import Connection
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED',enabled)
    target={'tenant_id':'tenant-a','telegram_id':22,'role':'worker','active':True,'permissions':[]}
    conn=Connection(target)
    _locked_members(conn.cursor_value,'tenant-a',1,22)
    assert conn.cursor_value.queries[0][0].endswith(lock)
    assert 'saas_tenants' in conn.cursor_value.queries[0][0]
