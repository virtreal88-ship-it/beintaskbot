"""Single-turn monetary admission through the shared ledger; providers mocked."""
import asyncio
import json
from unittest.mock import Mock
import pytest
from test_tenant_ai_testing import database, db, sandbox, new, message, run, uid
from test_tenant_support_inbox import A, B, PROFILE
import tenant_ai_testing_store as store
import tenant_ai_testing_service as service
import tenant_ai_spend_budget as budget
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def priced(sandbox, monkeypatch):
    monkeypatch.setattr(budget, '_connect', lambda: sandbox)
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD', json.dumps({A: 5_000_000, B: 5_000_000}))
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION', json.dumps({'fixture-model': {'input': 1_000_000, 'output': 2_000_000}}))
    return sandbox


def ledger(db):
    return db.execute('SELECT tenant_id::text AS tenant_id,call_key,model,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations ORDER BY call_key')


def test_success_has_committed_reservation_before_provider_and_replay_is_free(priced, monkeypatch):
    def generate(*args, **kwargs):
        assert not priced.transaction  # reservation committed before crossing provider boundary
        row=ledger(priced)[0]
        assert row['state']=='reserved'
        assert row['model']==args[1]=='fixture-model' and row['reserved_microusd']>0
        priced.commit()
        # A runtime tariff refresh must not re-price an admitted call.
        monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION', '{}')
        return 'Answer', {'input_tokens':10,'output_tokens':5,'total_tokens':15}
    provider=Mock(side_effect=generate);monkeypatch.setattr(service,'generate',provider)
    id=new();data=message();result=run(id,data)
    row=ledger(priced)[0]
    assert row['state']=='settled' and row['actual_microusd']==30
    assert result['turns'][0]['state']=='completed'
    assert run(id,data)==result and provider.call_count==1 and len(ledger(priced))==1
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,new(),data)


@pytest.mark.parametrize('outcome',['error','cancel','missing_usage'])
def test_uncertain_attempt_retains_hold_and_never_retries(priced, monkeypatch, outcome):
    provider=Mock(side_effect=RuntimeError('network'))
    if outcome=='cancel':provider.side_effect=asyncio.CancelledError()
    if outcome=='missing_usage':provider.side_effect=None;provider.return_value=('Answer',{})
    monkeypatch.setattr(service,'generate',provider)
    id=new();data=message()
    if outcome=='cancel':
        with pytest.raises(asyncio.CancelledError):run(id,data)
    else:run(id,data)
    row=ledger(priced)[0]
    assert row['state']=='unknown' and row['actual_microusd'] is None
    run(id,data);assert provider.call_count==1 and len(ledger(priced))==1


@pytest.mark.parametrize('outcome',['error','cancel'])
def test_preprovider_campaign_stop_releases_only_unattempted_hold(priced, monkeypatch, outcome):
    import tenant_knowledge_quality as quality
    monkeypatch.setattr(quality,'reserve_budget',lambda *args:None)
    monkeypatch.setattr(quality,'check_active',Mock(side_effect=asyncio.CancelledError() if outcome=='cancel' else RuntimeError('stopped')))
    provider=Mock();monkeypatch.setattr(service,'generate',provider)
    id=new();data=message()
    if outcome=='cancel':
        with pytest.raises(asyncio.CancelledError):asyncio.run(service.turn(PROFILE,id,data,campaign_id=uid()))
    else:asyncio.run(service.turn(PROFILE,id,data,campaign_id=uid()))
    provider.assert_not_called()
    assert ledger(priced)[0]['state']=='released' and ledger(priced)[0]['actual_microusd']==0
    turn=store.read(PROFILE,id)['turns'][0]
    assert turn['state']=='superseded' and turn['reserved_tokens']==0
    run(id,data);provider.assert_not_called()


@pytest.mark.parametrize('reason',['unknown_price','exhausted'])
def test_admission_fails_closed_without_provider_or_partial_receipt(priced, monkeypatch, reason):
    if reason=='unknown_price':monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION','{}')
    else:
        with priced.cursor() as cur:budget.reserve(cur,A,'active:existing','active_primary','fixture-model',total_upper=2_500_000)
        priced.commit()
    provider=Mock();monkeypatch.setattr(service,'generate',provider)
    id=new()
    with pytest.raises(TenantLinearError,match='ai_spend_price_unknown' if reason=='unknown_price' else 'ai_spend_budget_exhausted'):run(id)
    provider.assert_not_called()
    assert not priced.execute('SELECT id FROM saas_ai_test_turns')
    assert not priced.execute('SELECT request_id FROM saas_chat_ai_runs')


def test_competing_sessions_share_cap_and_tenant_ledgers_are_isolated(priced, monkeypatch):
    # PGlite serializes connections: exercise two committed admission transactions,
    # not native PostgreSQL lock scheduling. The shared helper owns the atomic lock.
    id=new();data=message();item=store.reserve(PROFILE,id,data)
    first=ledger(priced)[0];priced.commit()
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:first['reserved_microusd']*2-1,B:5_000_000}))
    with pytest.raises(TenantLinearError,match='ai_spend_budget_exhausted'):store.reserve(PROFILE,new(),message())
    with priced.cursor() as cur:budget.reserve(cur,B,first['call_key'],'testing_primary','fixture-model',total_upper=10)
    priced.commit()
    assert len(ledger(priced))==2
    store.finish(item,None)
    assert {row['tenant_id']:row['state'] for row in ledger(priced)}=={A:'unknown',B:'reserved'}


def test_discarded_output_is_still_charged_and_finish_is_idempotent(priced):
    item=store.reserve(PROFILE,new(),message())
    priced.execute('UPDATE saas_ai_agents SET version=version+1');priced.commit()
    store.finish(item,'discard',{'total_tokens':20})
    store.finish(item,'discard',{'total_tokens':20})
    row=ledger(priced)[0]
    assert row['state']=='settled' and row['actual_microusd']==40
    assert priced.execute('SELECT state,response FROM saas_ai_test_turns')[0]=={'state':'superseded','response':''}


def test_cancellation_while_reservation_thread_commits_releases_without_attempt(priced, monkeypatch):
    import threading
    committed=threading.Event();release=threading.Event()
    real_reserve=store.reserve
    def delayed(*args, **kwargs):
        item=real_reserve(*args, **kwargs)
        committed.set()
        assert release.wait(5)
        return item
    monkeypatch.setattr(service,'reserve',delayed)
    provider=Mock();monkeypatch.setattr(service,'generate',provider)
    id=new();data=message()
    async def cancel():
        task=asyncio.create_task(service.turn(PROFILE,id,data))
        assert await asyncio.to_thread(committed.wait,5)
        task.cancel();release.set()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(cancel())
    provider.assert_not_called()
    assert ledger(priced)[0]['state']=='released'
    run(id,data);provider.assert_not_called()

@pytest.mark.parametrize('retired',['gpt-4o-mini','gpt-4o-mini-2024-07-18'])
def test_retired_model_fails_before_any_reservation_or_provider(priced,monkeypatch,retired):
    from tenant_ai_agent_key_policy import choose_api_credential
    monkeypatch.setattr(store,'choose_api_credential',choose_api_credential)
    priced.execute('UPDATE saas_ai_agents SET model=%s',(retired,));priced.commit()
    provider=Mock();monkeypatch.setattr(service,'generate',provider)
    with pytest.raises(TenantLinearError,match='configuration_required'):run(new())
    provider.assert_not_called()
    assert not ledger(priced)
    assert not priced.execute('SELECT request_id FROM saas_chat_ai_runs')
    assert not priced.execute('SELECT id FROM saas_ai_test_turns')

@pytest.mark.parametrize('agent_model,platform_ready,expected',[
    ('explicit-agent',True,'explicit-agent'),('',True,'fixture-model'),('',False,'company-model')])
def test_testing_model_priority_is_unchanged(priced,monkeypatch,agent_model,platform_ready,expected):
    monkeypatch.setattr(store,'platform_service_ready',lambda:platform_ready)
    monkeypatch.setattr(store,'platform_model_allowed',lambda model:True)
    real_config=store.config
    monkeypatch.setattr(store,'config',lambda *args:{**real_config(*args),'model':agent_model,
        'metadata':{'model':'company-model','enabled':True,'consent':True}})
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({expected:{'input':1000,'output':1000}}))
    item=store.reserve(PROFILE,new(),message())
    assert item['model']==expected
    assert ledger(priced)[0]['model']==expected
