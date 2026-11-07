"""Primary/reserve policy stays explicit and reserve has no dispatch semantics."""
import pytest
from unittest.mock import Mock
from tenant_ai_agent_no_answer import validate_policy, selected_action
from tenant_linear_policy import TenantLinearError

PRIMARY={'channel_id':'00000000-0000-0000-0000-0000000000c9','conversation_id':'primary@g.us'}
RESERVE={**PRIMARY,'conversation_id':'reserve@g.us'}

def policy(**changes):
    return {'developer_routing':'primary_reserve','developer_destinations':[PRIMARY],
            'developer_reserve_destination':RESERVE,**changes}

def test_reserve_roundtrips_but_is_not_a_dispatch_destination():
    saved=validate_policy(policy(auto_case_dispatch_enabled=True))
    assert saved['developer_destinations']==[PRIMARY]
    assert saved['developer_reserve_destination']==RESERVE
    assert validate_policy(saved)==saved
    assert selected_action({**saved,'auto_case_dispatch_owner_id':11},'missing_knowledge')['action']=='continue'

@pytest.mark.parametrize('changes',[
    {'developer_destinations':[PRIMARY,RESERVE]},
    {'developer_reserve_destination':PRIMARY},
    {'developer_destinations':[]},
    {'developer_routing':'automatic_fallback'},
    {'developer_routing':None},
    {'developer_reserve_destination':{}},
    {'developer_reserve_destination':{**RESERVE,'timeout':60}},
    {'developer_reserve_destination':{**RESERVE,'channel_id':'bad'}},
])
def test_invalid_primary_reserve_is_rejected(changes):
    with pytest.raises(TenantLinearError):validate_policy(policy(**changes))

def test_reserve_requires_explicit_mode_and_unknown_fallback_is_rejected():
    with pytest.raises(TenantLinearError):validate_policy({'developer_reserve_destination':RESERVE})
    with pytest.raises(TenantLinearError):validate_policy({**policy(),'reserve_auto_dispatch_enabled':True})

def test_legacy_multi_recipient_policy_is_not_migrated_or_reordered():
    saved=validate_policy({'auto_case_dispatch_enabled':True,'developer_destinations':[RESERVE,PRIMARY]})
    assert saved['developer_destinations']==[RESERVE,PRIMARY]
    assert 'developer_routing' not in saved
    assert 'developer_reserve_destination' not in saved

def test_disabled_reserve_still_checks_exact_route_access(monkeypatch):
    import tenant_dialog_consultations as flow
    from tenant_ai_agents_store import _validate_case_developer_destinations
    monkeypatch.setattr(flow,'access',lambda *_:('tenant',11,Mock()))
    checked=Mock();monkeypatch.setattr(flow,'developer_target',checked)
    _validate_case_developer_destinations(Mock(),{},'tenant',validate_policy(policy()))
    assert [call.args[-2:] for call in checked.call_args_list]==[(PRIMARY['channel_id'],PRIMARY['conversation_id']),(RESERVE['channel_id'],RESERVE['conversation_id'])]
    checked.side_effect=TenantLinearError('revoked',403)
    with pytest.raises(TenantLinearError):_validate_case_developer_destinations(Mock(),{},'tenant',validate_policy(policy()))


def test_safe_stop_keeps_unchanged_routes_without_requiring_revoked_access():
    from tenant_ai_agents_store import _developer_routes_need_validation
    previous=validate_policy(policy(auto_case_dispatch_enabled=True))
    stopped={**previous,'auto_case_dispatch_enabled':False}
    assert not _developer_routes_need_validation(stopped,previous)
    assert not _developer_routes_need_validation(validate_policy(policy(developer_destinations=[],developer_reserve_destination=None)),{})
    assert _developer_routes_need_validation({**stopped,'developer_reserve_destination':{**RESERVE,'conversation_id':'changed@g.us'}},previous)
    assert _developer_routes_need_validation(previous,stopped)


# Existing isolated SQL fixture, never the production database.
from test_tenant_support_inbox import database,db,seed_job,A,CA,AGENT,PROFILE

def test_save_and_queue_only_primary_and_safe_stop_after_reserve_revoked(db,monkeypatch):
    import json
    import tenant_dialog_consultations as flow
    import tenant_ai_agents_store as agents
    import tenant_support_inbox_store as tickets
    monkeypatch.setattr(flow,'_connect',lambda:db)
    monkeypatch.setattr(agents,'_connect',lambda:db)
    flow.schema(db)
    job=seed_job(db)
    primary={**PRIMARY,'channel_id':CA};reserve={**RESERVE,'channel_id':CA}
    for index,target in enumerate((primary,reserve)):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,%s,'Synthetic developer',now())",(A,CA,target['conversation_id'],91+index))
        flow.developer_marker(PROFILE,{**target,'enabled':True})
    configured=agents.save_agent(PROFILE,{'expected_version':1,'mode':'automatic','response_policy':policy(auto_case_dispatch_enabled=True,developer_destinations=[primary],developer_reserve_destination=reserve)},agent_id=AGENT)
    assert configured['response_policy']['developer_reserve_destination']==reserve
    assert configured['response_policy']['auto_case_dispatch_owner_id']==11
    with db.cursor() as cur:
        receipt=tickets.handoff_ticket(cur,{**job,'event_fingerprint':'primary-reserve-test'},'Synthetic issue')
    db.commit()
    rows=db.execute('SELECT channel_id::text,conversation_id FROM saas_ai_case_developer_dispatches WHERE tenant_id=%s::uuid AND case_id=%s::uuid',(A,receipt['id']))
    assert rows==[primary], 'reserve is stored only, never queued alongside primary'
    flow.developer_marker(PROFILE,{**reserve,'enabled':False})
    stopped=agents.save_agent(PROFILE,{'expected_version':configured['version'],'mode':'manual'},agent_id=AGENT)
    assert stopped['response_policy']['auto_case_dispatch_enabled'] is False
    assert stopped['response_policy']['developer_reserve_destination']==reserve
    assert 'auto_case_dispatch_owner_id' not in stopped['response_policy']
    with pytest.raises(TenantLinearError):
        agents.save_agent(PROFILE,{'expected_version':stopped['version'],'response_policy':policy(developer_destinations=[primary],developer_reserve_destination={**reserve,'conversation_id':'unmarked@g.us'})},agent_id=AGENT)
