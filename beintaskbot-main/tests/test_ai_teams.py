"""Targeted teams regressions. Isolated SQL; no real tools or customer transport."""
import asyncio,json,uuid
from datetime import datetime,timezone
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE
from test_tenant_ai_expert import expert,EXPERT,GOOD,service,policy,agents
from tenant_linear_policy import TenantLinearError
from tenant_ai_agent_schedule import overlaps,current_candidates
E2=str(uuid.UUID(int=402))


def team(expert,*,tokens=12000):
    d,job,parent=expert
    d.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,role,model) VALUES(%s::uuid,%s::uuid,'Second specialist',11,TRUE,'expert','test-model')",(A,E2))
    cfg=policy.policy({'expert_agent_ids':[EXPERT,E2],'max_tokens':tokens})
    d.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s::uuid',(json.dumps(cfg),AGENT));d.commit()
    return d,job,parent


def test_sequential_team_one_result_and_dedup(expert,monkeypatch):
    d,job,parent=team(expert);seen=[]
    def generate(*a,**kw):
        seen.append(kw['extra_context']);return json.dumps(GOOD),{'total_tokens':100}
    monkeypatch.setattr(service,'generate',generate)
    run=lambda:asyncio.run(service.request_expert(job,parent,problem='help',lines=[],triggers=[{'actions':['call_expert']}]))
    value=run();assert value['status']=='completed' and len(value['specialists'])==2
    assert len(seen)==2 and run()==value and len(seen)==2
    assert d.execute('SELECT count(*) n FROM saas_ai_expert_tasks')[0]['n']==2
    assert d.execute('SELECT count(*) n FROM saas_ai_agent_drafts')[0]['n']==0


def test_shared_token_budget_not_multiplied(expert):
    d,job,parent=team(expert)
    one=service.reserve(job,parent,EXPERT)
    service._settle(job,parent,one,GOOD,{'total_tokens':11000},'completed','')
    with pytest.raises(TenantLinearError): service.reserve(job,parent,E2)
    assert d.execute('SELECT count(*) n FROM saas_ai_expert_tasks')[0]['n']==1


def test_running_receipt_blocks_next_specialist(expert):
    d,job,parent=team(expert)
    service.reserve(job,parent,EXPERT)
    with pytest.raises(TenantLinearError):service.reserve(job,parent,E2)
    assert service.reserve(job,parent,EXPERT)['existing']['status']=='running'


def test_individual_allowlist_tools_limits_and_revoke(expert,monkeypatch):
    d,job,parent=expert
    internal=policy.policy({'internal':True,'allowed_primary_ids':[], 'tools':['system_status'],'max_tokens':5000})
    d.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s::uuid',(json.dumps(internal),EXPERT));d.commit()
    with pytest.raises(TenantLinearError):service.reserve(job,parent)
    internal['allowed_primary_ids']=[AGENT]
    d.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s::uuid',(json.dumps(internal),EXPERT));d.commit()
    receipt=service.reserve(job,parent)
    assert receipt['config']['max_tokens']==5000 and receipt['config']['tools']==['system_status']
    d.execute('UPDATE saas_ai_agents SET enabled=FALSE,version=version+1 WHERE id=%s::uuid',(EXPERT,));d.commit()
    from unittest.mock import Mock
    mock=Mock();monkeypatch.setattr(service,'generate',mock)
    assert service.execute(job,parent,receipt,'help',[])['status']=='stale';mock.assert_not_called()


def test_unbound_or_foreign_specialist_never_called(expert):
    d,job,parent=expert
    with pytest.raises(TenantLinearError):service.reserve(job,parent,E2)
    with pytest.raises(TenantLinearError):policy.policy({'internal':True,'expert_agent_ids':[EXPERT]})
    cfg={'role':'expert','mode':'automatic','scopes':[],'expert_policy':{}}
    with d.cursor() as cur:
        with pytest.raises(TenantLinearError):policy.validate_binding(cur,A,EXPERT,cfg)
        with pytest.raises(TenantLinearError):policy.validate_binding(cur,A,EXPERT,{**cfg,'mode':'manual','expert_policy':policy.policy({'internal':True,'allowed_primary_ids':[str(uuid.UUID(int=999))]})})


def test_human_after_first_stops_remaining_helpers(expert,monkeypatch):
    d,job,parent=team(expert);calls=[]
    def generate(*a,**kw):
        calls.append(1);d.execute("UPDATE saas_ai_conversation_state SET state='human',revision=revision+1 WHERE tenant_id=%s::uuid",(A,));d.commit()
        return json.dumps(GOOD),{'total_tokens':12}
    monkeypatch.setattr(service,'generate',generate)
    value=asyncio.run(service.request_expert(job,parent,problem='help',lines=[],triggers=[{'actions':['call_expert']}]))
    assert value['status']=='stale' and len(calls)==1


def shift(start,end,zone='Asia/Baku',days=None):return {'timezone':zone,'days':days or list(range(7)),'start':start,'end':end}

@pytest.mark.parametrize('a,b,expected',[
    (shift('08:00','20:00'),shift('20:00','08:00'),False),
    (shift('22:00','06:00'),shift('05:00','08:00'),True),
    (shift('08:00','12:00','UTC'),shift('16:00','20:00','Asia/Baku'),False),
    (shift('08:00','12:00','UTC'),shift('15:00','20:00','Asia/Baku'),True),
    (shift('08:00','09:00','Europe/Berlin'),shift('07:00','08:00','UTC'),True),
    ({},shift('20:00','08:00'),True)])
def test_schedule_timezone_and_midnight(a,b,expected):assert overlaps(a,b,now=datetime(2026,1,1,tzinfo=timezone.utc)) is expected


def test_server_activation_allows_shifts_but_not_conflict(expert):
    d,job,parent=expert
    d.execute('UPDATE saas_ai_agents SET schedule=%s::jsonb WHERE id=%s::uuid',(json.dumps(shift('08:00','20:00')),AGENT));d.commit()
    scopes=[{'provider':'connect','channel_id':CA}]
    d.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid',(json.dumps(scopes),AGENT));d.commit()
    with d.cursor() as cur:
        agents._ensure_no_active_scope_overlap(cur,A,scopes,schedule=shift('20:00','08:00'))
        with pytest.raises(TenantLinearError):agents._ensure_no_active_scope_overlap(cur,A,scopes,schedule=shift('19:00','08:00'))
        agents._ensure_no_active_scope_overlap(cur,B,scopes,schedule={})


def test_runtime_only_current_shift_and_fail_closed_ambiguity():
    scope=[{'provider':'connect','channel_id':CA}]
    day={'id':'day','scopes':scope,'schedule':shift('08:00','20:00','UTC')}
    night={'id':'night','scopes':scope,'schedule':shift('20:00','08:00','UTC')}
    now=datetime(2026,1,1,12,tzinfo=timezone.utc)
    assert current_candidates([day,night],now=now)==[day]
    assert current_candidates([day,{**night,'schedule':{}}],now=now)==[]
    assert len(current_candidates([day,{**night,'scopes':[{'provider':'connect','channel_id':CB}]}],now=now))==2

def test_trigger_selects_specific_helper_and_rejects_unbound(expert,monkeypatch):
    d,job,parent=team(expert);called=[]
    def generate(*a,**kw):called.append(1);return json.dumps(GOOD),{'total_tokens':100}
    monkeypatch.setattr(service,'generate',generate)
    run=lambda selected:asyncio.run(service.request_expert(job,parent,problem='help',lines=[],triggers=[{'actions':['call_expert'],'condition':{'expert_agent_ids':selected}}]))
    assert run([str(uuid.UUID(int=999))])['status']=='unavailable' and not called
    assert run([E2])['status']=='completed' and len(called)==1
    assert str(d.execute('SELECT expert_agent_id FROM saas_ai_expert_tasks')[0]['expert_agent_id'])==E2


def test_new_internal_defaults_and_no_permission_downgrade():
    from tenant_ai_agents_policy import agent
    new=agent({'role':'expert','name':'Internal'},creating=True)
    assert new['mode']=='manual' and not new['enabled'] and new['expert_policy']['allowed_primary_ids']==[]
    with pytest.raises(TenantLinearError):policy.validate_binding(None,A,EXPERT,{**new,'expert_policy':{}},previous=new)


def test_trigger_targets_checked_by_existing_api(expert):
    d,job,parent=team(expert)
    data={'name':'Tech','trigger_type':'phrase','condition':{'phrase':'sync','expert_agent_ids':[E2]},'actions':['call_expert'],'enabled':True}
    assert agents.save_trigger(PROFILE,AGENT,data)['condition']['expert_agent_ids']==[E2]
    with pytest.raises(TenantLinearError):agents.save_trigger(PROFILE,AGENT,{**data,'condition':{'phrase':'sync','expert_agent_ids':[str(uuid.UUID(int=999))]}})
    with pytest.raises(TenantLinearError):agents.save_trigger(PROFILE,EXPERT,data)


def test_first_helper_revoked_during_second_discards_team(expert,monkeypatch):
    d,job,parent=team(expert);calls=[]
    def generate(*a,**kw):
        calls.append(1)
        if len(calls)==2:
            d.execute('UPDATE saas_ai_agents SET enabled=FALSE,version=version+1 WHERE id=%s::uuid',(EXPERT,));d.commit()
        return json.dumps(GOOD),{'total_tokens':100}
    monkeypatch.setattr(service,'generate',generate)
    value=asyncio.run(service.request_expert(job,parent,problem='help',lines=[],triggers=[{'actions':['call_expert']}]))
    assert value['status']=='stale' and value['result'] is None and len(calls)==2
