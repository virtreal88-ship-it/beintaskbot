"""Existing isolated PostgreSQL/WASM fixture; no network/model/production DB."""
import uuid
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
    db.commit()
    return other


def test_confirmed_observation_is_immutable_idempotent_scoped_and_reviewable(db):
    other = seed_agents(db); cmd = teaching()
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
    with db.cursor() as cur:
        learning.record(cur, db, PROFILE, corrected)
    db.commit()
    old = db.execute('SELECT content FROM saas_ai_knowledge_sources WHERE id=%s::uuid', (first['source_id'],))[0]['content']
    assert old == source['content']
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 2
    agents.delete_knowledge(PROFILE, first['source_id'])
    assert runs.knowledge_context(A, 'интернете', agent_id=AGENT) == []


def test_staff_denied_by_existing_rights_creates_no_knowledge_or_candidate(db):
    seed_agents(db)
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,77,'operator','Employee')", (A,))
    db.commit()
    with db.cursor() as cur:
        result = learning.attempt(cur, db, {'tenant_id': A, 'telegram_id': 77}, teaching())
    db.commit()
    assert result['status'] == 'denied'
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n'] == 0
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_candidates')[0]['n'] == 0
