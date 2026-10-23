"""Pilot scope/backlog fences against isolated PostgreSQL-compatible PGlite."""
import uuid

import pytest
pytest_plugins = ('test_tenant_support_inbox',)
from test_tenant_support_inbox import db, A, B, CA, CB
import tenant_knowledge_live_queue as live

AFTER = '2026-10-20T12:00:00+00:00'


@pytest.fixture
def pilot_queue(db, monkeypatch):
    monkeypatch.setattr(live, '_connect', lambda: db)
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', 'true')
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_TENANT_ID', A)
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_CHANNEL_ID', CA)
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_CONVERSATION_ID', 'client')
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_START_AFTER', AFTER)
    with db.cursor() as cur:
        live.create_schema(db)
    db.execute("INSERT INTO saas_ai_agent_settings(tenant_id,enabled,support_case_extraction_enabled,support_case_extraction_started_at) VALUES(%s::uuid,true,true,%s::timestamptz),(%s::uuid,true,false,NULL)", (A, AFTER, B))
    db.commit()
    return db


def add_message(db, tenant, channel, lead, external_id, stamp):
    message_id = str(uuid.uuid4())
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','synthetic',%s::timestamptz)""",
      (message_id, tenant, lead, external_id, 'connect:' + channel, stamp))
    return message_id


def test_claim_ignores_pre_pilot_and_foreign_backlog_then_accepts_fresh_message(pilot_queue):
    db = pilot_queue
    stale = add_message(db, A, CA, 41, 'stale', '2026-10-20T11:59:00Z')
    foreign_tenant = add_message(db, B, CB, 42, 'foreign-tenant', '2026-10-20T13:00:00Z')
    foreign_channel = add_message(db, A, CA, 41, 'foreign-conversation', '2026-10-20T13:00:00Z')
    db.execute("""INSERT INTO saas_knowledge_live_queue(tenant_id,channel_id,conversation_id,lead_id,last_message_id,state,next_attempt_at)
      VALUES(%s::uuid,%s::uuid,'client',41,%s::uuid,'pending',now()-interval '1 minute'),
        (%s::uuid,%s::uuid,'client',42,%s::uuid,'pending',now()-interval '1 minute'),
        (%s::uuid,%s::uuid,'other-chat',41,%s::uuid,'pending',now()-interval '1 minute')""",
      (A, CA, stale, B, CB, foreign_tenant, A, CA, foreign_channel))
    db.commit()
    assert live.claim() is None
    assert db.execute("SELECT count(*) AS n FROM saas_knowledge_live_queue WHERE state='pending'")[0]['n'] == 3

    fresh = add_message(db, A, CA, 41, 'fresh', '2026-10-20T13:00:00Z')
    db.commit()
    live.enqueue(db.cursor(), A, CA, 'client', 41, fresh)
    db.execute("UPDATE saas_knowledge_live_queue SET next_attempt_at=now()-interval '1 second' WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id='client'", (A, CA))
    db.commit()
    claimed = live.claim()
    assert claimed['tenant_id'] == A and claimed['channel_id'] == CA
    assert claimed['conversation_id'] == 'client' and claimed['last_message_id'] == fresh
    assert claimed['last_message_at'] >= AFTER
    assert db.execute("SELECT state FROM saas_knowledge_live_queue WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id='other-chat'", (A, CA))[0]['state'] == 'pending'


def test_legacy_scope_does_not_override_disabled_tenant_extraction(pilot_queue):
    db=pilot_queue
    db.execute('UPDATE saas_ai_agent_settings SET support_case_extraction_enabled=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    fresh=add_message(db,A,CA,41,'disabled-tenant','2026-10-20T13:00:00Z');db.commit()
    live.enqueue(db.cursor(),A,CA,'client',41,fresh)
    assert not db.execute('SELECT 1 FROM saas_knowledge_live_queue WHERE tenant_id=%s::uuid',(A,))
    assert live.claim() is None
