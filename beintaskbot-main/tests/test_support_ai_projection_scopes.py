"""Support documents must not restore shared AI data hidden by agent scopes."""
import json
import pytest
from test_tenant_support_inbox import db, database, A, PROFILE, Cursor
from test_support_inbox_scopes import seeded, configure
from tenant_policy import TenantPolicy
import tenant_support_inbox_store as store


@pytest.mark.parametrize('scope',['none','self','department','all'])
def test_support_list_and_detail_hide_shared_ai(db,seeded,monkeypatch,scope):
    keys,_,_=seeded
    profile=configure(db,view='self',update='none')
    settings={'31':{'ai_agents_manage':True,'access_scopes':{
        'support':{'view':'self','update':'none'},'ai_agents':{'view':scope}}}}
    db.execute("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s AND policy_key='members'",(json.dumps(settings),A))
    db.execute("UPDATE saas_support_tickets SET summary='Ticket summary',summary_agent_binding='private-binding' WHERE id=%s",(keys[0],));db.commit()
    # Populate joined projection values without invoking any provider/assignment.
    monkeypatch.setattr(store,'_SELECT',store._SELECT.replace('a.name AS agent_name',"'Private agent' AS agent_name").replace('cs.agent_id AS agent_id',"'00000000-0000-4000-8000-000000000901'::uuid AS agent_id"))
    import tenant_dialog_ai_context
    monkeypatch.setattr(tenant_dialog_ai_context,'resolve',lambda *_:pytest.fail('shared assignment must not be resolved'))
    original=Cursor.execute
    def guarded(self,sql,params=None):
        assert 'SELECT e.id,e.status,e.result' not in sql, 'dialog-wide expert results must not be queried'
        return original(self,sql,params)
    monkeypatch.setattr(Cursor,'execute',guarded)
    import tenant_support_summary_history
    monkeypatch.setattr(tenant_support_summary_history,'versions',lambda *_:[{
        'id':'audit','run_id':'private-run','agent_binding':'private-binding','applied_content':{'summary':'Ticket summary'}}])
    for item in (store.listing(profile)['tickets'][0],store.detail(profile,keys[0])['ticket']):
        assert 'agent_id' not in item and 'agent_name' not in item
        assert not any(k.startswith(('agent_','company_','latest_job_')) for k in item)
        assert 'summary_agent_binding' not in item
        assert item['summary']=='Ticket summary'
        assert item['summary_available'] is False
    result=store.detail(profile,keys[0])
    assert result['expert_results']==[]
    assert result['summary_history']==[{'id':'audit','applied_content':{'summary':'Ticket summary'}}]
    assert result['ticket']['id']==keys[0]


def test_support_ai_projection_retains_owner_legacy_and_gate_off(monkeypatch):
    row={'agent_id':'agent','agent_name':'Agent','agent_enabled':True,'summary_agent_binding':'binding',
         'company_enabled':True,'latest_job_state':'completed','summary':'Ticket text'}
    base={'tenant_id':A,'telegram_id':31,'active':True,'role':'admin','permissions':['support'],
          'workflow':{'policies':{'members':{'31':{'access_scopes':{'ai_agents':{'view':'none'}}}}}}}
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED',raising=False)
    assert store._ticket_public(row,TenantPolicy(base))==store.public(row)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    assert store._ticket_public(row,TenantPolicy({**base,'role':'owner'}))==store.public(row)
    assert store._ticket_public(row,TenantPolicy({**base,'workflow':{}}))==store.public(row)
    assert 'agent_id' not in store._ticket_public(row,TenantPolicy(base))
