import json
from unittest.mock import Mock
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,B,CA,CB,PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels,AGENT2
import tenant_connect_retirement as retirement
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def retiring(channels,monkeypatch):
    db=channels['db'];monkeypatch.setattr(retirement,'_connect',lambda:db)
    monkeypatch.setattr(retirement,'TARGET_TENANT',A);monkeypatch.setattr(retirement,'TARGET_CHANNEL',CB)
    db.execute("UPDATE saas_connect_channels SET active=FALSE,status='disconnected' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,CB))
    db.execute('UPDATE saas_ai_agents SET enabled=FALSE WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT2))
    db.execute("UPDATE saas_tenant_integrations SET metadata=%s::jsonb WHERE tenant_id=%s::uuid AND provider='connect'",(json.dumps({'channel_ids':[CA,CB],'other':'retained'}),A));db.commit()
    state={'id':CB,'status':'disconnected','phone':'+994102135103','retirementPolicy':'retain_history_v1'}
    calls=[]
    def remote(key,method,path,*args,**kwargs):
        calls.append((method,path))
        assert CB in path and CA not in path
        if method=='DELETE':state['status']='disabled'
        if method=='POST':state['status']='disconnected'
        return dict(state)
    monkeypatch.setattr(transport,'_request',remote)
    return db,state,calls


def test_authorized_retirement_is_idempotent_retains_history_and_removes_only_old_binding(retiring):
    db,state,calls=retiring
    before={table:db.execute('SELECT row_to_json(t) AS row FROM '+table+' t') for table in ['saas_crm_messages','saas_connect_conversations','saas_ai_agents']};db.commit()
    result=retirement.retire(PROFILE,CB,confirmed=True)
    assert CB not in [str(x['connect_channel_id']) for x in result['channels']]
    assert db.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA))[0]['active'] is True
    assert db.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'",(A,))[0]['metadata']=={'channel_ids':[CA],'other':'retained'}
    for table in before:assert db.execute('SELECT row_to_json(t) AS row FROM '+table+' t')==before[table]
    db.commit()
    retirement.retire(PROFILE,CB,confirmed=True)
    assert sum(method=='DELETE' for method,_ in calls)==1
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='connect_channel_retired'",(A,))[0]['n']==1;db.commit()


@pytest.mark.parametrize('case',['foreign','keep_channel','confirm','non_owner','active','enabled_agent','old_server','wrong_phone','identity_conflict'])
def test_unsafe_retirement_fails_closed_without_delete(retiring,case):
    db,state,calls=retiring;profile=PROFILE;channel=CB;confirm=True
    if case=='foreign':profile={**PROFILE,'tenant_id':B}
    if case=='keep_channel':channel=CA
    if case=='confirm':confirm=False
    if case=='non_owner':db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
    if case=='active':db.execute('UPDATE saas_connect_channels SET active=TRUE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CB))
    if case=='enabled_agent':db.execute('UPDATE saas_ai_agents SET enabled=TRUE WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT2))
    if case=='old_server':state.pop('retirementPolicy')
    if case=='wrong_phone':state['phone']='994102135101'
    if case=='identity_conflict':state['phoneConflict']=True
    db.commit()
    with pytest.raises(TenantLinearError):retirement.retire(profile,channel,confirmed=confirm)
    assert not any(method in {'DELETE','POST'} for method,_ in calls)
    assert db.execute('SELECT status FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CB))[0]['status']=='disconnected';db.commit()


def test_unknown_remote_delete_keeps_local_history_and_binding_for_reconciliation(retiring,monkeypatch):
    db,state,calls=retiring
    before=db.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'",(A,));db.commit()
    def remote(key,method,path):
        if method=='DELETE':raise TimeoutError('synthetic unknown result')
        return state
    monkeypatch.setattr(transport,'_request',remote)
    with pytest.raises(TimeoutError):retirement.retire(PROFILE,CB,confirmed=True)
    assert db.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'",(A,))==before
    assert db.execute('SELECT status FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CB))[0]['status']=='disconnected';db.commit()
