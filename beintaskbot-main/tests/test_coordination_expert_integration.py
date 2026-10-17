"""Real coordinator + expert + worker; isolated SQL and mocked external providers."""
import json
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database, db, A, CA, CB, AGENT, PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels
from test_group_coordination import group, config, incoming, G, groups, agents
from test_tenant_ai_expert import EXPERT, GOOD
import tenant_ai_expert_service as service
import tenant_ai_expert_policy as policy

@pytest.mark.parametrize('mode',['manual','automatic'])
@pytest.mark.parametrize('outcome',['advisory','employee','pause','reassign','forged_event'])
def test_coordinated_expert_round_trip(group,monkeypatch,mode,outcome):
    f=group;d=f['db']
    monkeypatch.setattr(service,'_connect',lambda:d)
    monkeypatch.setattr(service,'choose_api_credential',lambda *a:('synthetic','platform'))
    monkeypatch.setattr(service,'platform_model_allowed',lambda *a:True)
    monkeypatch.setattr(service,'platform_token_limit',lambda *a:0)
    d.execute('DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid',(A,))
    d.execute('UPDATE saas_ai_agents SET mode=%s WHERE tenant_id=%s::uuid',(mode,A))
    d.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,role,model) VALUES(%s::uuid,%s::uuid,'Expert',11,TRUE,'expert','test-model')",(A,EXPERT))
    d.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s::uuid',(json.dumps(policy.policy({'expert_agent_id':EXPERT})),AGENT))
    d.execute("INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,condition,actions,enabled) VALUES(%s::uuid,%s::uuid,%s::uuid,'Expert','phrase',%s::jsonb,%s::jsonb,TRUE)",(A,str(uuid.uuid4()),AGENT,json.dumps({'phrase':'Question'}),json.dumps(['call_expert'])))
    d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    def generate(*args,**kwargs):
        if outcome in {'employee','pause'}:
            agents.set_conversation_state(PROFILE,G,'human' if outcome=='employee' else 'paused',provider='connect',channel_id=CB)
        elif outcome=='reassign':
            revision=d.execute('SELECT revision FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid',(A,))[0]['revision'];d.commit()
            groups.configure(PROFILE,config(expected_revision=revision))
        return json.dumps(GOOD),{'total_tokens':12}
    generation=Mock(side_effect=generate);monkeypatch.setattr(service,'generate',generation)
    if outcome=='forged_event':
        original=service.reserve
        def corrupt(job,parent):
            return original({**job,'payload':{**job['payload'],'group_event_key':'not-a-persisted-receipt'}},parent)
        monkeypatch.setattr(service,'reserve',corrupt)
    assert incoming(f,CB,'EXPERT_SHARED_EVENT')==0
    assert incoming(f,CA,'EXPERT_SHARED_EVENT')==1
    assert incoming(f,CA,'EXPERT_SHARED_EVENT')==0
    f['process']()
    tasks=d.execute('SELECT status,result FROM saas_ai_expert_tasks');d.commit()
    if outcome=='forged_event':
        generation.assert_not_called();assert tasks==[]
    else:
        generation.assert_called_once();assert len(tasks)==1
        assert tasks[0]['status']==('completed' if outcome=='advisory' else 'stale')
    permitted=outcome in {'advisory','forged_event'}
    assert f['send'].call_count==(1 if permitted and mode=='automatic' else 0)
    assert f['generate'].call_count==int(permitted)
    drafts=d.execute('SELECT count(*) n FROM saas_ai_agent_drafts')[0]['n'];d.commit()
    assert drafts==(1 if permitted and mode=='manual' else 0)
    if outcome=='advisory':
        assert f['generate'].call_args.kwargs['extra_context']['internal_expert_advisory']['result']==GOOD
    if outcome in {'employee','pause','reassign'}:
        agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CB)
        assert incoming(f,CA,'EXPERT_SHARED_EVENT')==0
        assert incoming(f,CA,'EXPERT_NEW_AFTER_RESUME')==1
