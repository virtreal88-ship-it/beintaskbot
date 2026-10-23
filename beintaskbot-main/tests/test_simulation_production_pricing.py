"""Regression for the observed Railway default/catalog mismatch; no paid calls."""
import json
import pytest
from test_tenant_support_inbox import database, db, seed_simulation, queue_simulation, A, AGENT
import tenant_ai_simulation_engine as simulation
import tenant_ai_agent_actions as actions
import tenant_ai_agent_run_store as run_store
import tenant_ai_spend_budget as budget
from tenant_linear_policy import TenantLinearError

MODEL='gpt-4.1-2025-04-14'
OLD_CATALOG={'gpt-4o-mini':{'input':150000,'output':600000}}
CATALOG={**OLD_CATALOG,MODEL:{'input':2000000,'output':8000000}}


def setup_budget(db,monkeypatch,catalog=CATALOG):
    monkeypatch.setattr(budget,'_connect',lambda:db)
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:5000000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps(catalog))
    budget.ensure_schema(db);db.commit()


@pytest.mark.parametrize('priced',[False,True])
def test_actual_default_model_stops_before_provider_until_exact_price_exists(db,monkeypatch,priced):
    source=seed_simulation(db)
    setup_budget(db,monkeypatch,CATALOG if priced else OLD_CATALOG)
    db.execute("UPDATE saas_ai_agents SET model='',mode='manual' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));db.commit()
    monkeypatch.setenv('AI_AGENTS_PLATFORM_ENABLED','true')
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS',json.dumps([{'id':MODEL,'label':'GPT-4.1'}]))
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL',MODEL)
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-key-not-a-real-credential')
    monkeypatch.setattr(simulation,'runtime_flag',lambda _:True)
    monkeypatch.setattr(actions,'enabled_triggers',lambda *_:[])
    monkeypatch.setattr(run_store,'knowledge_context',lambda *a,**k:[])
    calls=[]
    def generate(key,model,*args,**kwargs):
        assert model==MODEL and key=='synthetic-key-not-a-real-credential'
        assert kwargs['max_output_tokens']==128
        calls.append(model)
        return 'Synthetic virtual response',{'input_tokens':100,'output_tokens':20,'total_tokens':120}
    monkeypatch.setattr('tenant_chat_ai_provider.generate',generate)
    assert queue_simulation(db,source=source)
    job=simulation.claim_next();assert simulation.process(job)
    run=simulation.list_runs({'tenant_id':A,'telegram_id':11})[0]
    if not priced:
        assert calls==[] and run['virtual_stop_reason']=='ai_spend_price_unknown'
        assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    else:
        assert calls==[MODEL] and run['outcome']=='virtual_reply' and run['sent'] is False
        row=db.execute('SELECT model,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations')[0]
        assert row['model']==MODEL and row['state']=='settled'
        # Existing ledger conservatively charges total tokens at max tariff.
        assert row['actual_microusd']==960 and row['reserved_microusd']>=960
        assert budget.limit_microusd(A)==5000000
    assert db.execute('SELECT mode FROM saas_ai_agents WHERE id=%s::uuid',(AGENT,))[0]['mode']=='manual'
    assert db.execute('SELECT count(*) AS n FROM saas_ai_agent_jobs')[0]['n']==0


@pytest.mark.parametrize('kind',['active_primary','expert','simulation_primary','live_case_extraction'])
def test_exact_snapshot_tariff_reservation_settlement_and_unknown_hold(db,monkeypatch,kind):
    setup_budget(db,monkeypatch)
    assert budget.price(MODEL)==(2000000,8000000)
    with db.cursor() as cur:
        reservation=budget.reserve(cur,A,'first:'+kind,kind,MODEL,1000,100)
        assert budget.reserve(cur,A,'first:'+kind,kind,MODEL,1000,100)==reservation
    db.commit()
    budget.settle(A,'first:'+kind,{'input_tokens':100,'output_tokens':20,'total_tokens':120})
    row=db.execute('SELECT reserved_microusd,actual_microusd,state FROM saas_ai_spend_reservations')[0]
    assert row=={'reserved_microusd':2800,'actual_microusd':960,'state':'settled'}
    db.commit()
    with db.cursor() as cur:budget.reserve(cur,A,'unknown:'+kind,kind,MODEL,total_upper=1000)
    db.commit()
    budget.settle(A,'unknown:'+kind,{},unknown=True)
    assert db.execute("SELECT reserved_microusd,state FROM saas_ai_spend_reservations WHERE call_key=%s",('unknown:'+kind,))[0]=={'reserved_microusd':8000,'state':'unknown'}
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError,match='ai_spend_price_unknown'):
            budget.reserve(cur,A,'unpriced',kind,'gpt-4.1-unconfigured-snapshot',1,1)
        with pytest.raises(TenantLinearError,match='ai_spend_budget_exhausted'):
            budget.reserve(cur,A,'too-much',kind,MODEL,total_upper=1000000)
