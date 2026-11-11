"""New upstream analyst tools stay owner-only despite independent AI grants."""
import pytest
from test_tenant_support_inbox import database,db,A
from test_tenant_operator_workspace import operator_db
from test_dialog_source_scopes import scoped,grants
from tenant_linear_policy import TenantLinearError
import tenant_ai_business_analytics as analytics

NEW_TOOLS=[
    ('search_support_cases',{'query':'synthetic'}),
    ('get_support_case_detail',{'candidate_id':'00000000-0000-0000-0000-000000000999'}),
    ('search_tasks',{'query':'synthetic','source':'all'}),
    ('get_task_timeline',{'task_id':'00000000-0000-0000-0000-000000000999','source':'native'}),
    ('search_knowledge',{'query':'synthetic'}),
]


@pytest.mark.parametrize('view',['none','department','all'])
@pytest.mark.parametrize('tool,args',NEW_TOOLS)
def test_new_analyst_tools_deny_scoped_admin_even_with_ai_management(scoped,tool,args,view):
    connection,person=scoped;grants(connection,33,view,'all')
    connection.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s AND telegram_id=33",(A,))
    connection.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{33,ai_agents_manage}','true') WHERE tenant_id=%s AND policy_key='members'",(A,));connection.commit()
    # The authenticated identity is current-DB checked; cached/client role or
    # source scope must not turn an employee into the company owner.
    with pytest.raises(TenantLinearError) as denied:
        analytics.execute({**person,'role':'owner','permissions':['dialogs','ai_agents_manage']},tool,args)
    assert denied.value.status==403
    assert not connection.execute("SELECT 1 FROM saas_tenant_audit_events WHERE tenant_id=%s AND action='ai_business_analytics_tool_read'",(A,))
