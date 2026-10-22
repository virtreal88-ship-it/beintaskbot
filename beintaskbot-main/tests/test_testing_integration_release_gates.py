"""Release gates: cross-channel Human Mode isolation against real PostgreSQL SQL."""
import uuid
import pytest
from test_tenant_support_inbox import database,db,A,CA,CB,AGENT,PROFILE,seed_job
import tenant_ai_agent_connect as connect
import tenant_operator_dialogs as dialogs
from tenant_linear_policy import TenantLinearError


def two_channels(db):
    first=seed_job(db)
    second=str(uuid.UUID(int=302))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')",(A,CB))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'client',43)",(A,CB))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected')",(A,))
    db.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid",(A,))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled) VALUES(%s::uuid,%s::uuid,'Second agent',11,TRUE)",(A,second))
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,chat_id,channel_id,lead_id,event_fingerprint,talk_id,due_at) VALUES(%s::uuid,%s::uuid,'connect','client','client',%s,43,'evt','',now())",(A,second,CB))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'connect',%s,'client',%s::uuid,'active')",(A,CB,second))
    db.commit()
    return first,{**first,'agent_id':second,'channel_id':CB,'lead_id':43}


def test_new_channel_scoped_dialogs_are_independent(db,monkeypatch):
    monkeypatch.setattr(connect,'runtime_flag',lambda _:True)
    first,second=two_channels(db)
    assert connect.allowed(first)
    assert connect.allowed(second)
    # Distinct channel keys allow the shared JID; separate legacy tests enforce quarantine.


def test_operator_reply_must_preserve_other_channel_job(db):
    first,second=two_channels(db)
    connect.mark_operator_reply(A,41,CA,'client')
    result=db.execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND channel_id=%s",(A,CB))[0]
    assert result['state']=='queued'


def test_each_channel_must_have_independent_conversation_state(db,monkeypatch):
    monkeypatch.setattr(dialogs,"_connect",lambda:db)
    two_channels(db)
    connect.mark_operator_reply(A,41,CA,'client')
    second=dialogs.history(PROFILE,'connect',43,CB,'client')['dialog']
    assert second['state']=='active'
