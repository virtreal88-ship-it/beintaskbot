"""Independent checks of b4 task-search merge against real isolated PostgreSQL."""
import json
import pytest
from test_task_read_save_flow_postgres import task_flow,staff,db,database,A,B
from tenant_task_flow import read,TaskFlowError
from tenant_policy import TenantPolicy
import tenant_member_store as members


@pytest.mark.parametrize('needle',["Literal % _ \\\\ ' OR TRUE --",'ordinary merge search'])
def test_search_keeps_tenant_marker_pipeline_and_deleted_boundaries(task_flow,needle):
    f=task_flow
    marker=TenantPolicy(members.member(A,83)).task_marker()
    with f['factory']() as conn:
        for lead,pipeline,status,deleted in ((102,20,201,False),(103,10,102,False),(104,10,101,True)):
            conn.execute('''INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,pipeline_id,status_id,name,deleted_at)
                VALUES(%s::uuid,%s,%s,%s,'Synthetic independent lead',CASE WHEN %s THEN now() ELSE NULL END)''',(A,lead,pipeline,status,deleted))
        for tenant,ident,lead,text in (
            (A,81001,101,needle+'\n'+marker),(A,81002,102,needle),
            (A,81003,103,needle),(B,81004,101,needle+'\n'+marker),
            (A,81005,104,needle+'\n'+marker)):
            conn.execute('''INSERT INTO saas_crm_tasks(tenant_id,kommo_task_id,kommo_lead_id,text,responsible_id)
                VALUES(%s::uuid,%s,%s,%s,999)''',(tenant,ident,lead,text))
    for tenant,actor,expected in ((A,11,{'81001','81002','81003'}),(A,86,{'81001','81002','81003'}),(A,82,{'81001'}),(A,83,{'81001'}),(B,22,{'81004'})):
        result=read({'tenant_id':tenant,'telegram_id':actor},source='kommo',search=needle)
        assert {row['id'] for row in result['tasks']}==expected
        assert result['shown']==len(expected) and result['has_more'] is False
    for actor in (85,):
        with pytest.raises(TaskFlowError) as error:read({'tenant_id':A,'telegram_id':actor},source='kommo',search=needle)
        assert error.value.status==403
    assert f['provider'].calls==[]


def test_native_scopes_do_not_widen_external_search(task_flow,monkeypatch):
    f=task_flow;monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    scopes={'83':{'access_scopes':{'tasks':{'create':'all','view':'all','update':'all','delete':'all'}}}}
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_workflow_policies SET value=value||%s::jsonb WHERE tenant_id=%s::uuid AND policy_key='members'",(json.dumps(scopes),A))
    external=read({'tenant_id':A,'telegram_id':83},source='kommo',search='cached task')
    assert {row['id'] for row in external['tasks']}=={'502'}
    assert all(external['sources'][1][key] is False for key in ('create','complete','sync'))
    native=read({'tenant_id':A,'telegram_id':83},source='native',search='A native task')
    assert native['tasks']
    for row in native['tasks']:
        assert 'access_responsible_id' not in row and 'access_responsible_department_id' not in row
        assert row['source']=='native' and row['capabilities']['view'] is True
    assert f['provider'].calls==[]
