"""Dormant AI document scopes: isolated PGlite, no provider or live DB."""
import json
import uuid
from unittest.mock import Mock

import pytest
import requests
from test_tenant_support_inbox import database, db, A, B
from tenant_linear_policy import TenantLinearError
import tenant_ai_agents_store as store
from tenant_scoped_access import SCOPE_ACTION_CATALOG

ACTOR = {'tenant_id': A, 'telegram_id': 31, 'role': 'owner'}  # forged cached role


def grants(db, actions, *, manage=True, dialogs=None):
    settings = {'ai_agents_manage': manage, 'access_scopes': {'ai_agents': actions}}
    if dialogs is not None:
        settings['access_scopes']['dialogs'] = {'view': dialogs}
    db.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value", (A,json.dumps({'31': settings})))
    db.commit()


@pytest.fixture
def scoped(db, monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    monkeypatch.setenv('DATABASE_URL','postgresql://invalid.invalid/disabled')
    forbidden = Mock(side_effect=AssertionError('provider traffic forbidden'))
    monkeypatch.setattr(requests.sessions.Session,'request',forbidden)
    monkeypatch.setattr(store,'platform_service_ready',lambda:True)
    dep, other = str(uuid.uuid4()), str(uuid.uuid4())
    for ident in (dep, other):
        db.execute('INSERT INTO saas_tenant_departments(tenant_id,id,name) VALUES(%s,%s,%s)',(A,ident,ident))
    for actor, department in ((31,dep),(32,dep),(33,other)):
        db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,department_id) VALUES(%s,%s,'admin',%s)",(A,actor,department))
    ids = {}
    for name, tenant, responsible in (('self',A,31),('peer',A,32),('other',A,33),('system',A,None),('foreign',B,22)):
        ids[name]=str(uuid.uuid4())
        db.execute('INSERT INTO saas_ai_agents(tenant_id,id,name,access_responsible_id,created_by) VALUES(%s,%s,%s,%s,%s)',(tenant,ids[name],name,responsible,11 if tenant==A else 22))
    db.commit()
    yield db,ids
    forbidden.assert_not_called()


@pytest.mark.parametrize('scope,names',[('self',{'self'}),('department',{'self','peer'}),('all',{'self','peer','other','system'})])
def test_list_scope_and_hidden_projections(scoped,scope,names):
    db,ids=scoped;grants(db,{'view':scope})
    rows=store.list_agents(ACTOR)
    assert {row['name'] for row in rows}==names
    assert all(not {'access_responsible_id','expert_policy','assigned_dialog_count'} & row.keys() for row in rows)


@pytest.mark.parametrize('action',['view','create','delete'])
def test_other_action_never_implies_update(scoped,action):
    db,ids=scoped;grants(db,{action:'all','update':'none'})
    with pytest.raises(TenantLinearError):store.save_agent(ACTOR,{'name':'denied','expected_version':1},agent_id=ids['self'])
    assert db.execute('SELECT name FROM saas_ai_agents WHERE id=%s',(ids['self'],))[0]['name']=='self'


def test_update_checks_current_owner_and_department(scoped):
    db,ids=scoped;grants(db,{'update':'department','view':'none'})
    assert store.save_agent(ACTOR,{'name':'changed','expected_version':1},agent_id=ids['peer'])['id']==ids['peer']
    db.execute('UPDATE saas_tenant_members SET department_id=NULL WHERE tenant_id=%s AND telegram_id=32',(A,));db.commit()
    with pytest.raises(TenantLinearError):store.save_agent(ACTOR,{'name':'stale','expected_version':2},agent_id=ids['peer'])
    with pytest.raises(TenantLinearError):store.save_agent(ACTOR,{'name':'foreign','expected_version':1},agent_id=ids['foreign'])


def test_create_is_independent_and_responsible_is_authenticated(scoped):
    db,ids=scoped;grants(db,{'create':'self','view':'none','update':'none','delete':'none'},manage=False)
    row=store.save_agent(ACTOR,{'name':'Created','instructions':'Synthetic instructions'})
    actual=db.execute('SELECT access_responsible_id,created_by,enabled FROM saas_ai_agents WHERE id=%s',(row['id'],))[0]
    assert actual=={'access_responsible_id':31,'created_by':31,'enabled':False}
    with pytest.raises(TenantLinearError):store.list_agents(ACTOR)


@pytest.mark.parametrize('actions',[{'view':'all'}, {'create':'all'}, {'update':'all'}, {'delete':'all'},dict.fromkeys(('view','create','update','delete'),'all')])
def test_shared_operations_do_not_inherit_any_crud(scoped,actions):
    db,ids=scoped;grants(db,actions)
    for operation in (store.read_settings,store.list_knowledge,store.list_handoffs):
        with pytest.raises(TenantLinearError) as error:operation(ACTOR)
        assert error.value.status==403


def test_revocation_and_current_membership(scoped):
    db,ids=scoped;grants(db,{'view':'self','update':'self'})
    assert store.list_agents(ACTOR)
    grants(db,{'view':'none','update':'none'})
    with pytest.raises(TenantLinearError):store.list_agents(ACTOR)
    grants(db,{'view':'all'})
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=31',(A,));db.commit()
    with pytest.raises(TenantLinearError):store.list_agents(ACTOR)


def test_shared_reference_and_narrow_dialogs_block_mutation(scoped):
    db,ids=scoped;grants(db,{'view':'all','update':'self'})
    with pytest.raises(TenantLinearError):store.save_agent(ACTOR,{'expert_policy':{'expert_agent_ids':[ids['peer']]},'expected_version':1},agent_id=ids['self'])
    grants(db,{'update':'self'},dialogs='self')
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s',(json.dumps([{'provider':'connect','channel_id':str(uuid.uuid4())}]),ids['self']));db.commit()
    with pytest.raises(TenantLinearError):store.save_agent(ACTOR,{'name':'blocked','expected_version':1},agent_id=ids['self'])


def test_owner_and_gate_off_legacy_unchanged(scoped,monkeypatch):
    db,ids=scoped;grants(db,dict.fromkeys(('view','create','update','delete'),'none'))
    assert len(store.list_agents({'tenant_id':A,'telegram_id':11}))==4
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    assert len(store.list_agents(ACTOR))==4
    grants(db,{'view':'all'},manage=False)
    with pytest.raises(TenantLinearError):store.list_agents(ACTOR)
    assert SCOPE_ACTION_CATALOG['ai_agents']==('view','create','update','delete')


def test_enabled_is_update_not_create(scoped):
    db,ids=scoped;grants(db,{'create':'none','view':'none','update':'self','delete':'none'})
    db.execute('INSERT INTO saas_ai_agent_settings(tenant_id,enabled) VALUES(%s,TRUE)',(A,));db.commit()
    assert store.save_agent(ACTOR,{'enabled':True,'expected_version':1},agent_id=ids['self'])=={'id':ids['self'],'version':2}
    assert store.save_agent(ACTOR,{'enabled':False,'expected_version':2},agent_id=ids['self'])=={'id':ids['self'],'version':3}
    grants(db,{'create':'all','update':'none'})
    with pytest.raises(TenantLinearError):store.save_agent(ACTOR,{'enabled':True,'expected_version':3},agent_id=ids['self'])


def test_setup_retry_rechecks_responsible_not_historical_creator(scoped):
    db,ids=scoped;grants(db,{'create':'self'})
    data={'name':'Retry','instructions':'Synthetic instructions','setup_key':str(uuid.uuid4())}
    row=store.save_agent(ACTOR,data)
    assert store.save_agent(ACTOR,data)['id']==row['id']
    db.execute('UPDATE saas_ai_agents SET access_responsible_id=32 WHERE id=%s',(row['id'],));db.commit()
    with pytest.raises(TenantLinearError):store.save_agent(ACTOR,data)


def test_trigger_reads_use_parent_view_and_strip_other_agent_ids(scoped):
    db,ids=scoped;grants(db,{'view':'self'})
    for name in ('self','other'):
        db.execute("INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,condition) VALUES(%s,%s,%s,'Trigger','phrase',%s::jsonb)",(A,str(uuid.uuid4()),ids[name],json.dumps({'expert_agent_ids':[ids['peer']]})))
    db.commit()
    result=store.list_triggers(ACTOR,ids['self'])
    assert result and 'condition' not in result[0] and 'actions' not in result[0]
    with pytest.raises(TenantLinearError):store.list_triggers(ACTOR,ids['other'])


@pytest.mark.parametrize('actions',[None,{'view':True},{'view':'unexpected'}])
def test_malformed_scope_never_falls_back_to_legacy(scoped,actions):
    db,ids=scoped;grants(db,actions)
    with pytest.raises(TenantLinearError):store.list_agents(ACTOR)


def test_delete_grant_preserves_owner_only_archive_and_purge(scoped):
    db,ids=scoped;grants(db,{'delete':'all'})
    with pytest.raises(TenantLinearError):store.delete_agent(ACTOR,ids['self'],replacement_agent_id=ids['peer'],expected_version=1)
    with pytest.raises(TenantLinearError):store.permanently_delete_agent(ACTOR,ids['self'],expected_version=1,confirm_agent_id=ids['self'])
    assert db.execute('SELECT archived_at FROM saas_ai_agents WHERE id=%s',(ids['self'],))[0]['archived_at'] is None


def test_create_only_cannot_bind_unscoped_helpers(scoped):
    db,ids=scoped;grants(db,{'create':'self'},manage=False)
    with pytest.raises(TenantLinearError) as error:
        store.save_agent(ACTOR,{'name':'Indirect','instructions':'Synthetic','expert_policy':{'expert_agent_ids':[ids['other']]}})
    assert str(error.value)=='ai_cross_agent_scope_not_ready'


def test_mutation_receipts_do_not_reveal_old_document(scoped):
    db,ids=scoped;grants(db,{'view':'none','update':'self','create':'self'})
    db.execute("UPDATE saas_ai_agents SET instructions='PRIVATE OLD INSTRUCTION',response_policy=%s::jsonb WHERE id=%s",(json.dumps({'stop_reply':'PRIVATE OLD RESPONSE'}),ids['self']));db.commit()
    receipt=store.save_agent(ACTOR,{'name':'new name','expected_version':1},agent_id=ids['self'])
    assert receipt=={'id':ids['self'],'version':2}
    assert set(store.save_agent(ACTOR,{'name':'Created','instructions':'Known'}))=={'id','version'}


def test_configurable_cru_has_separate_navigation_not_shared_management(scoped):
    db,ids=scoped;grants(db,{'create':'self','view':'none','update':'none'},manage=False)
    from tenant_scoped_access_store import read_access_profile
    from tenant_policy import TenantPolicy
    with db.cursor() as cur: current=read_access_profile(cur,A,31)
    caps=TenantPolicy(current).public_capabilities()
    assert caps['ai_documents']=={'scoped':True,'create':True,'view':False,'update':False,'delete':False}
    assert caps['pages']['ai_agents'] is True
    assert caps['modules']['ai_agents'] is False
    assert all(caps['pages'][page] is False for page in ('ai_dashboard','ai_testing','ai_teams','ai_administrator','knowledge_center'))


def assign_sources(db):
    from test_tenant_support_inbox import CA
    other=str(uuid.uuid4())
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Private other source')",(A,other))
    db.execute("INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by) VALUES(%s,%s,31,11)",(A,CA))
    db.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{31,access_scopes,dialogs}',%s::jsonb) WHERE tenant_id=%s AND policy_key='members'",(json.dumps({'view':'self','update':'self'}),A));db.commit()
    return CA,other


def test_options_and_source_mutations_use_current_source_bindings(scoped):
    db,ids=scoped;grants(db,{'view':'self','create':'self','update':'self'},manage=False)
    own,other=assign_sources(db)
    options=store.configuration_options(ACTOR)
    assert options['scoped'] is True
    assert [row['id'] for row in options['connect_channels']]==[own]
    assert options['channels']==[]
    for channel in (other,str(uuid.uuid4())):
        with pytest.raises(TenantLinearError):store.save_agent(ACTOR,{'name':'denied','instructions':'x','scopes':[{'provider':'connect','channel_id':channel}]})
    receipt=store.save_agent(ACTOR,{'name':'allowed','instructions':'x','scopes':[{'provider':'connect','channel_id':own}]})
    db.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s AND employee_id=31',(A,));db.commit()
    assert store.configuration_options(ACTOR)['connect_channels']==[]
    row=next(row for row in store.list_agents(ACTOR) if row['id']==receipt['id'])
    assert 'scopes' not in row and row['capabilities']['update'] is False
    with pytest.raises(TenantLinearError):store.save_agent(ACTOR,{'name':'revoked','expected_version':1},agent_id=receipt['id'])


def test_nested_shared_fields_never_project_to_agent_reader(scoped):
    db,ids=scoped;grants(db,{'view':'self','update':'self'})
    db.execute("UPDATE saas_ai_agents SET response_policy=%s::jsonb,expert_policy=%s::jsonb WHERE id=%s",(json.dumps({'developer_id':33,'nested':{'agent_id':ids['other']}}),json.dumps({'expert_agent_ids':[ids['other']]}),ids['self']));db.commit()
    row=store.list_agents(ACTOR)[0]
    assert 'response_policy' not in row and 'expert_policy' not in row
    assert ids['other'] not in json.dumps(row)
    assert row['capabilities']['update'] is False


def test_trigger_writes_are_parent_update_and_only_safe_handoff(scoped):
    db,ids=scoped;grants(db,{'view':'none','update':'self','create':'none'})
    data={'name':'Stop on phrase','trigger_type':'phrase','condition':{'phrase':'operator'},'actions':['handoff'],'enabled':True}
    receipt=store.save_trigger(ACTOR,ids['self'],data)
    assert set(receipt)=={'id','version'}
    with pytest.raises(TenantLinearError):store.save_trigger(ACTOR,ids['other'],data)
    with pytest.raises(TenantLinearError):store.save_trigger(ACTOR,ids['self'],{**data,'actions':['create_task'],'condition':{'phrase':'x','task_text':'outside'}})
    assert store.delete_trigger(ACTOR,ids['self'],receipt['id'])=={'deleted':True}
    grants(db,{'view':'all','create':'all','update':'none'})
    with pytest.raises(TenantLinearError):store.save_trigger(ACTOR,ids['self'],data)


def test_creation_cannot_implicitly_activate(scoped):
    db,ids=scoped;grants(db,{'create':'all','update':'none'})
    with pytest.raises(TenantLinearError) as error:store.save_agent(ACTOR,{'name':'active','instructions':'x','enabled':True})
    assert str(error.value)=='ai_create_disabled_then_update'


def test_options_scoped_never_fetch_provider():
    from types import SimpleNamespace
    from test_tenant_ai_agents_api_diagnostics import load_handler, run_without_event_loop
    from unittest.mock import AsyncMock
    remote=AsyncMock(side_effect=AssertionError('No provider request allowed'))
    refresh=Mock(side_effect=AssertionError('No provider refresh allowed'))
    handler,_=load_handler(configuration_options=lambda profile:{'scoped':True,'models':[]},refresh_configuration_catalog=refresh)
    response=run_without_event_loop(handler(Mock(return_value=ACTOR),'https://synthetic.invalid',Mock(),'options',request_provider=remote)(SimpleNamespace(method='GET',headers={},query={})))
    assert response.status==200
    remote.assert_not_called()
    refresh.assert_not_called()


def test_operator_projection_does_not_reintroduce_other_agent_metadata(scoped,monkeypatch):
    db,ids=scoped;grants(db,{'view':'self','update':'self'})
    from test_tenant_support_inbox import CA
    import tenant_operator_dialogs as dialogs
    import tenant_operator_list_snapshot as snapshots
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    monkeypatch.setattr(snapshots,'ListSnapshot',Mock(side_effect=AssertionError('No broad AI enrichment')))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s,'connect',%s,'client',%s,'paused')",(A,CA,ids['other']));db.commit()
    listing=dialogs.listing(ACTOR)
    assert listing['agents']==[] and listing['attention_count'] is None
    assert ids['other'] not in json.dumps(listing)
    assert listing['dialogs'][0]['controls_allowed'] is False
    history=dialogs.history(ACTOR,'connect',41,CA,'client')
    assert ids['other'] not in json.dumps(history)
    with pytest.raises(TenantLinearError):dialogs.listing(ACTOR,agent=ids['other'])


def test_indirect_assignment_and_case_learning_are_closed_for_scoped_ai(scoped,monkeypatch):
    db,ids=scoped;grants(db,{'view':'all','update':'all'})
    from test_tenant_support_inbox import CA
    import tenant_operator_assignment as assignment
    import tenant_knowledge_candidates_store as candidates
    from tenant_operator_case_submission import scoped as submission
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):assignment.context(cur,ACTOR,{'provider':'connect','channel_id':CA,'conversation_id':'client','lead_id':41})
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):candidates.authorize(cur,submission(ACTOR))


@pytest.mark.parametrize('actions,condition',[
    (['create_task'],{'task_text':'Shared CRM task'}),
    (['native_create_task'],{'native_task_text':'Shared native task'}),
    (['call_expert'],{'expert_agent_ids':['00000000-0000-0000-0000-000000000999']}),
    (['handoff'],{'phrase':'operator','native_task_id':'00000000-0000-0000-0000-000000000999'}),
])
def test_scoped_trigger_delete_checks_persisted_shared_contract(scoped,actions,condition):
    db,ids=scoped;grants(db,{'view':'none','update':'self'})
    trigger=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,condition,actions) VALUES(%s,%s,%s,'Shared trigger','new_inquiry',%s::jsonb,%s::jsonb)",(A,trigger,ids['self'],json.dumps(condition),json.dumps(actions)));db.commit()
    with pytest.raises(TenantLinearError) as denied:
        store.delete_trigger(ACTOR,ids['self'],trigger)
    assert denied.value.status==403
    with pytest.raises(TenantLinearError) as rewrite_denied:
        store.save_trigger(ACTOR,ids['self'],{'expected_version':1,'actions':['handoff'],'condition':{'phrase':'safe replacement'}},trigger_id=trigger)
    assert rewrite_denied.value.status==403
    assert db.execute('SELECT id FROM saas_ai_agent_triggers WHERE id=%s',(trigger,))
    assert not db.execute("SELECT id FROM saas_tenant_audit_events WHERE entity_id=%s AND action='ai_agent_trigger_deleted'",(trigger,))
    assert store.delete_trigger({'tenant_id':A,'telegram_id':11},ids['self'],trigger)=={'deleted':True}
