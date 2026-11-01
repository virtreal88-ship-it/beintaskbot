"""Committed manual sources survive new native PostgreSQL connections; no AI."""
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, AGENT, PROFILE
import tenant_ai_agents_store as agents
from tenant_linear_policy import TenantLinearError


@pytest.mark.parametrize('expert',[False,True])
def test_manual_source_commit_readback_and_reload_preserve_audience(db,monkeypatch,expert):
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,role) VALUES(%s::uuid,%s::uuid,'Expert',11,'expert')",(A,AGENT));db.commit()
    data={'title':'Synthetic manual source','kind':'text','content':'Synthetic verified instruction','active':True}
    if expert:data['agent_id']=AGENT
    result=agents.save_knowledge(PROFILE,data)
    assert result['id'] and result['agent_id']==(AGENT if expert else None)
    # Every read opens a new psycopg connection to this disposable test database.
    monkeypatch.setattr(agents,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    listed=agents.list_knowledge(PROFILE)
    assert len(listed)==1 and listed[0]['id']==result['id'] and listed[0]['ai_available'] is True
    loaded=agents.list_knowledge(PROFILE,result['id'])
    assert loaded['content']==data['content'] and loaded['agent_id']==(AGENT if expert else None)
    assert agents.list_knowledge({'tenant_id':B,'telegram_id':22})==[]
    with pytest.raises(TenantLinearError):agents.list_knowledge({'tenant_id':B,'telegram_id':22},result['id'])
    with pytest.raises(TenantLinearError):agents.save_knowledge(PROFILE,{**data,'content':''})
    assert len(agents.list_knowledge(PROFILE))==1
