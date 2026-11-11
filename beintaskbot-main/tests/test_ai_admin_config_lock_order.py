"""Actual configuration validator lock order on the isolated database."""
import pytest
from test_tenant_support_inbox import database,db,A,CA,AGENT,seed_simulation
from test_ai_learning_mcp import _setup
import tenant_ai_learning_mcp as mcp
from tenant_ai_administrator_apply import validate_config_proposal
from tenant_linear_policy import TenantLinearError


@pytest.mark.parametrize('gate',['false','true'])
def test_validation_authority_then_mutex_then_parent_row(db,monkeypatch,gate):
    seed_simulation(db);_setup(monkeypatch,db)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED',gate)
    cfg=mcp._require_cfg();cfg.update(actor_id=11,AI_LEARNING_MCP_AGENT_ID=AGENT)
    statements=[]
    with db.cursor() as cur:
        class Observed:
            def execute(self,sql,params=None):
                statements.append((sql,params))
                return cur.execute(sql,params)
            def __getattr__(self,name):return getattr(cur,name)
        proposal={'agent_id':AGENT,'channel_id':CA,'expected_agent_version':1,'patch':{'language':'ru'}}
        body,_=validate_config_proposal(Observed(),cfg,proposal)
        assert body['patch']=={'language':'ru'}
        membership=next(i for i,(sql,_) in enumerate(statements) if 'saas_tenant_members' in sql and 'FOR SHARE' in sql)
        mutex=next(i for i,(sql,args) in enumerate(statements) if args and str(args[0]).startswith('ai-agent-config:'))
        agent=next(i for i,(sql,_) in enumerate(statements) if 'SELECT * FROM saas_ai_agents' in sql and 'FOR UPDATE' in sql)
        assert membership < mutex < agent
    db.commit()
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=11',(A,));db.commit()
    statements.clear()
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError) as error:validate_config_proposal(Observed(),cfg,proposal)
        assert error.value.status==403
    assert not any(args and str(args[0]).startswith('ai-agent-config:') for _,args in statements)
