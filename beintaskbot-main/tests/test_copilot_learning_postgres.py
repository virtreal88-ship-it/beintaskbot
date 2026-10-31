"""Existing isolated PostgreSQL/WASM fixture; no network/model/production DB."""
import uuid
import json
import pytest
import tenant_operator_copilot as copilot
from tenant_linear_policy import TenantLinearError
import tenant_operator_copilot_learning as learning
import tenant_ai_agents_store as agents
import tenant_ai_agent_run_store as runs
from test_tenant_support_inbox import database, db, A, B, CA, AGENT, PROFILE


def teaching():
    return {'mode': 'chat', 'prompt': 'Проверили после подключения: причина в интернете.',
        'request_id': str(uuid.uuid4()), '_agent_id': AGENT, 'channel_id': CA,
        'conversation_id': 'client', 'lead_id': 41}


def seed_agents(db):
    other = str(uuid.uuid4())
    for tenant, agent, owner in ((A, AGENT, 11), (A, other, 11)):
        db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Support',%s)", (tenant, agent, owner))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,agent_id,state) VALUES(%s::uuid,'connect','client',%s,%s::uuid,'human')",(A,CA,AGENT))
    db.commit()
    return other


def receipt(db, cmd, actor=11):
    with db.cursor() as cur:
        context = copilot._assigned_context(cur, A, cmd)
    cmd['_agent_binding'] = copilot._agent_binding(context)
    db.execute('''INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,provider,channel_id,conversation_id)
      VALUES(%s::uuid,%s::uuid,%s,41,%s,'test','sending',1,'connect',%s,'client')''',
      (A,cmd['request_id'],actor,copilot._hash(cmd),CA))
    db.commit()
    return cmd


def test_confirmed_observation_is_immutable_idempotent_scoped_and_reviewable(db):
    other = seed_agents(db); cmd = receipt(db, teaching())
    with db.cursor() as cur:
        first = learning.record(cur, db, PROFILE, cmd)
        second = learning.record(cur, db, PROFILE, cmd)
    db.commit()
    assert first == second and first['status'] == 'pending_review'
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 1
    source = db.execute('SELECT * FROM saas_ai_knowledge_sources')[0]
    assert source['agent_id'] == AGENT and source['active'] is False
    assert source['provenance']['employee_id'] == 11
    assert source['provenance']['employee_turn_id'] == cmd['request_id']
    assert runs.knowledge_context(A, 'интернете', agent_id=AGENT) == []
    # Publication continues to use the existing authorized Knowledge service.
    agents.save_knowledge(PROFILE, {'active': True}, source_id=first['source_id'])
    found = runs.knowledge_context(A, 'интернете', agent_id=AGENT)
    assert len(found) == 1 and 'причина в интернете' in found[0]['content']
    assert runs.knowledge_context(A, 'интернете', agent_id=other) == []
    assert runs.knowledge_context(B, 'интернете', agent_id=AGENT) == []
    corrected = {**cmd, 'request_id': str(uuid.uuid4()),
        'prompt': 'Исправление: проверили роутер, причина в кабеле этого клиента.'}
    receipt(db, corrected)
    with db.cursor() as cur:
        learning.record(cur, db, PROFILE, corrected)
    db.commit()
    old = db.execute('SELECT content FROM saas_ai_knowledge_sources WHERE id=%s::uuid', (first['source_id'],))[0]['content']
    assert old == source['content']
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 2
    agents.delete_knowledge(PROFILE, first['source_id'])
    assert runs.knowledge_context(A, 'интернете', agent_id=AGENT) == []



def employee(db, permissions=('support', 'dialogs'), ai=True):
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions) VALUES(%s::uuid,77,'operator','Employee',%s::jsonb)", (A,json.dumps(permissions)))
    db.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members',%s::jsonb)", (A,json.dumps({'77':{'ai_enabled':ai}})))
    db.commit()
    return {'tenant_id':A,'telegram_id':77}


def test_staff_private_observation_is_saved_without_general_knowledge_rights(db):
    seed_agents(db); staff = employee(db); cmd = receipt(db, teaching(),77)
    with db.cursor() as cur:
        result = learning.attempt(cur, db, staff, cmd)
    db.commit()
    assert result['status'] == 'pending_review'
    row = db.execute('SELECT active,agent_id,created_by,provenance FROM saas_ai_knowledge_sources')[0]
    assert row['active'] is False and row['agent_id'] == AGENT and row['created_by'] == 77
    assert row['provenance']['employee_id'] == 77
    with pytest.raises(TenantLinearError):
        agents.save_knowledge(staff, {'active':True},source_id=result['source_id'])
    with pytest.raises(TenantLinearError):
        agents.save_knowledge(staff, {'agent_id':AGENT,'title':'forged','kind':'text','content':'arbitrary','active':False})
    with pytest.raises(TenantLinearError):
        agents.list_knowledge(staff)
    assert runs.knowledge_context(A,'интернете',agent_id=AGENT) == []


@pytest.mark.parametrize('permissions,ai', [(('dialogs',),True),(('support',),True),(('dialogs','support'),False)])
def test_staff_submission_requires_both_rights_and_employee_ai_opt_in(db,permissions,ai):
    seed_agents(db); staff = employee(db,permissions,ai); cmd = receipt(db,teaching(),77)
    with db.cursor() as cur:
        result = learning.attempt(cur,db,staff,cmd)
    db.commit()
    assert result['status'] == 'denied'
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 0


@pytest.mark.parametrize('tamper', ['agent','tenant','prompt','employee','missing_receipt','stale_assignment'])
def test_private_submission_rejects_wrong_agent_tenant_turn_and_stale_assignment(db,tamper):
    other = seed_agents(db); staff = employee(db); cmd = receipt(db,teaching(),77)
    if tamper == 'agent':
        cmd['_agent_id'] = other
    elif tamper == 'tenant':
        staff = {'tenant_id':B,'telegram_id':22}
    elif tamper == 'prompt':
        cmd['prompt'] = 'Проверили: другая подтверждённая причина этого случая.'
    elif tamper == 'employee':
        # Current member cannot claim another authenticated member's turn.
        staff = PROFILE
    elif tamper == 'missing_receipt':
        cmd['request_id'] = str(uuid.uuid4())
    elif tamper == 'stale_assignment':
        db.execute("UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE tenant_id=%s::uuid",(A,));db.commit()
    with db.cursor() as cur:
        result = learning.attempt(cur,db,staff,cmd)
    db.commit()
    assert result['status'] in {'denied','failed'}
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 0


def test_capability_cannot_publish_edit_list_or_change_bound_payload(db):
    seed_agents(db); staff = employee(db); cmd = receipt(db,teaching(),77)
    scoped = {**staff,'_copilot_teaching_capability':learning._SUBMISSION_CAPABILITY,
              '_copilot_teaching_command':cmd}
    payload = learning._payload(cmd)
    for patch in ({'active':True},{'agent_id':str(uuid.uuid4())},{'content':'arbitrary instructions'}):
        with pytest.raises(TenantLinearError):
            agents.save_knowledge(scoped,{**payload,**patch})
    forged = {**staff,'_copilot_teaching_capability':'_SUBMISSION_CAPABILITY',
              '_copilot_teaching_command':cmd}
    with pytest.raises(TenantLinearError):
        agents.save_knowledge(forged,payload)
    with db.cursor() as cur:
        created = learning.record(cur,db,staff,cmd)
        duplicate = learning.record(cur,db,staff,cmd)
    db.commit()
    assert created == duplicate
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 1
    with pytest.raises(TenantLinearError):
        agents.save_knowledge(scoped,{'active':True},source_id=created['source_id'])
    with pytest.raises(TenantLinearError):
        agents.list_knowledge(scoped)


@pytest.mark.parametrize('mode,prompt',[('reply','Проверили: причина интернет.'),
    ('chat','Может интернет?'),('chat','Попроси AnyDesk'),
    ('chat','Клиент написал: проверили интернет')])
def test_hypotheses_client_content_and_reply_requests_never_create_review_material(db,mode,prompt):
    seed_agents(db); staff = employee(db)
    cmd = {**teaching(),'mode':mode,'prompt':prompt}
    with db.cursor() as cur:
        result = learning.attempt(cur,db,staff,cmd)
    db.commit()
    assert result['status'] == 'not_applicable'
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 0


@pytest.mark.parametrize('failure',[False,True])
def test_real_private_copilot_preserves_staff_teaching_before_model_io_and_retry(db,monkeypatch,failure):
    from types import SimpleNamespace
    from unittest.mock import Mock
    seed_agents(db); staff = employee(db)
    from tenant_chat_send_schema import ensure_schema as send_schema
    send_schema(db)
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata) VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)",
        (A,b'synthetic-cipher',json.dumps({'enabled':True,'consent':True,'copilot_enabled':True,'copilot_model':'configured-model','daily_limit':100})))
    db.commit()
    monkeypatch.setattr(copilot,'_connect',lambda:db)
    monkeypatch.setattr(copilot,'_fernet',lambda:SimpleNamespace(decrypt=lambda _:json.dumps({'tenant_id':A,'provider':'chat_ai','api_key':'synthetic-test-key'}).encode()))
    def response(*args,**kwargs):
        # Teaching is already durable before the single provider invocation.
        row = db.execute('SELECT active,created_by FROM saas_ai_knowledge_sources')[0]
        assert row['active'] is False and row['created_by'] == 77
        if failure:
            raise TimeoutError('synthetic timeout')
        return ('İnternet bağlantısını yoxlayıb yenidən cəhd edin.',{'total_tokens':10})
    generated = Mock(side_effect=response)
    monkeypatch.setattr(copilot,'generate',generated)
    data = {'provider':'connect','mode':'chat','prompt':teaching()['prompt'],
        'request_id':str(uuid.uuid4()),'channel_id':CA,'conversation_id':'client','lead_id':41}
    if failure:
        with pytest.raises(TenantLinearError,match='сохранён на разбор'):
            copilot.run(staff,data)
        with pytest.raises(TenantLinearError):
            copilot.run(staff,data)
    else:
        first = copilot.run(staff,data)
        assert first['learning']['status'] == 'pending_review'
        assert copilot.run(staff,data) == first
    assert generated.call_count == 1
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 1
    assert db.execute('SELECT count(*) AS n FROM saas_chat_send_receipts')[0]['n'] == 0
