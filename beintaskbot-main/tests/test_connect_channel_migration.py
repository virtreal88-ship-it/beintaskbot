"""Transactional migration and rollback gates against real PostgreSQL SQL/WASM."""
import json
import pytest
from test_tenant_support_inbox import Database, A, B, CA, CB, AGENT
import tenant_ai_channel_schema as schema

@pytest.fixture
def legacy():
    db=Database()
    db.execute("CREATE TABLE saas_ai_conversation_state(tenant_id UUID,provider TEXT,conversation_id TEXT,state TEXT,reason TEXT,PRIMARY KEY(tenant_id,provider,conversation_id))")
    db.execute("CREATE TABLE saas_ai_agent_jobs(tenant_id UUID,agent_id UUID,provider TEXT,conversation_id TEXT,channel_id TEXT NOT NULL DEFAULT '',lead_id BIGINT,PRIMARY KEY(tenant_id,agent_id,provider,conversation_id))")
    db.execute("CREATE TABLE saas_connect_conversations(tenant_id UUID,connect_channel_id UUID,conversation_id TEXT,lead_id BIGINT)")
    db.execute("CREATE TABLE saas_tenant_webhook_events(tenant_id UUID,provider TEXT,payload JSONB)")
    db.execute("INSERT INTO saas_ai_conversation_state VALUES(%s::uuid,'connect','client','human','manager_replied'),(%s::uuid,'kommo','client','paused','pause')",(A,A))
    db.execute("INSERT INTO saas_connect_conversations VALUES(%s::uuid,%s::uuid,'client',41)",(A,CA));db.commit()
    yield db
    db.rollback();db.process.stdin.close();db.process.wait(timeout=10)

@pytest.mark.parametrize('ambiguity',['none','two_routes','wrong_job','foreign_event','missing_route'])
def test_migration_preserves_states_and_quarantines_unproven_scope(legacy,ambiguity):
    db=legacy
    if ambiguity=='two_routes':db.execute("INSERT INTO saas_connect_conversations VALUES(%s::uuid,%s::uuid,'client',43)",(A,CB))
    if ambiguity=='wrong_job':db.execute("INSERT INTO saas_ai_agent_jobs VALUES(%s::uuid,%s::uuid,'connect','client',%s,43)",(A,AGENT,CB))
    if ambiguity=='foreign_event':db.execute("INSERT INTO saas_tenant_webhook_events VALUES(%s::uuid,'connect',%s::jsonb)",(A,json.dumps({'channel_id':CB,'conversation_id':'client'})))
    if ambiguity=='missing_route':db.execute('DELETE FROM saas_connect_conversations')
    # Same contact in another tenant must never make the first tenant ambiguous.
    db.execute("INSERT INTO saas_connect_conversations VALUES(%s::uuid,%s::uuid,'client',42)",(B,CB));db.commit()
    schema.migrate(db);db.commit();schema.migrate(db);db.commit()
    rows=db.execute('SELECT * FROM saas_ai_conversation_state ORDER BY provider');db.commit()
    assert len(rows)==2
    assert rows[0]['state']=='human' and rows[0]['reason']=='manager_replied'
    assert rows[0]['channel_id']==(CA if ambiguity=='none' else '')
    assert rows[1]['provider']=='kommo' and rows[1]['state']=='paused' and rows[1]['channel_id']==''
    old=db.execute('SELECT * FROM saas_ai_channel_legacy_state')[0];db.commit()
    assert old['original']['state']=='human' and old['original']['reason']=='manager_replied'
    with db.cursor() as cur: assert len(schema.unresolved(cur))==(0 if ambiguity=='none' else 1)


def test_transaction_rollback_and_guarded_downgrade_keep_data(legacy):
    db=legacy
    schema.migrate(db);db.rollback()
    assert not db.execute("SELECT 1 FROM information_schema.columns WHERE table_name='saas_ai_conversation_state' AND column_name='channel_id'");db.commit()
    schema.migrate(db);db.commit();schema.rollback_keys(db);db.commit()
    assert db.execute('SELECT count(*) n FROM saas_ai_conversation_state')[0]['n']==2
    assert db.execute('SELECT count(*) n FROM saas_ai_channel_legacy_state')[0]['n']==1
    db.commit();schema.migrate(db);db.commit()
    assert db.execute("SELECT channel_id FROM saas_ai_conversation_state WHERE provider='connect'")[0]['channel_id']==CA


def test_downgrade_refuses_multi_channel_without_merging_or_deleting(legacy):
    db=legacy;schema.migrate(db);db.commit()
    db.execute("INSERT INTO saas_connect_conversations VALUES(%s::uuid,%s::uuid,'client',43)",(A,CB))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,state,reason) VALUES(%s::uuid,'connect','client',%s,'active','new')",(A,CB));db.commit()
    with pytest.raises(RuntimeError,match='Unsafe rollback'):schema.rollback_keys(db)
    db.rollback()
    assert db.execute("SELECT count(*) n FROM saas_ai_conversation_state WHERE provider='connect'")[0]['n']==2
