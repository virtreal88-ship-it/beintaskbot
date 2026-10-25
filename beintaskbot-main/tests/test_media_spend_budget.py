"""Isolated ledger checks for priced media branches; no provider calls."""
import json

import pytest

pytest_plugins = ('test_tenant_support_inbox',)
from test_tenant_support_inbox import A, db
from tenant_linear_policy import TenantLinearError
import tenant_ai_spend_budget as budget


@pytest.fixture
def priced(db, monkeypatch):
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD', json.dumps({A: 1000}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD', json.dumps({
        'asr-model': {'asr': 300}, 'vision-model': {'vision_low': 400},
    }))
    monkeypatch.setattr(budget, '_connect', lambda: db)
    budget.ensure_schema(db); db.commit()
    return db


def test_media_price_unknown_fails_before_reservation(priced):
    with priced.cursor() as cur:
        with pytest.raises(TenantLinearError, match='ai_spend_media_price_unknown'):
            budget.reserve_media(cur,A,'media:unknown','knowledge_asr','unknown','asr',1)
    assert priced.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0


def test_media_reservations_share_cap_and_are_idempotent(priced):
    with priced.cursor() as cur:
        first=budget.reserve_media(cur,A,'media:asr','knowledge_asr','asr-model','asr',1)
        assert budget.reserve_media(cur,A,'media:asr','knowledge_asr','asr-model','asr',1)==first
        with pytest.raises(TenantLinearError,match='ai_spend_budget_exhausted'):
            budget.reserve_media(cur,A,'media:vision','knowledge_vision','vision-model','vision_low',2)
    priced.commit()
    assert priced.execute("SELECT reserved_microusd,state FROM saas_ai_spend_reservations WHERE call_key='media:asr'")[0]=={'reserved_microusd':300,'state':'reserved'}


def test_second_worker_observes_committed_media_reservation(priced):
    """A competing request must see an already committed reservation under the shared cap.

    PGlite has one process/connection, so this is deliberately a two-transaction
    regression rather than a claim of native PostgreSQL lock-concurrency coverage.
    """
    with priced.cursor() as cur:
        budget.reserve_media(cur,A,'media:first','knowledge_asr','asr-model','asr',3)
    priced.commit()

    with priced.cursor() as cur:
        with pytest.raises(TenantLinearError,match='ai_spend_budget_exhausted'):
            budget.reserve_media(cur,A,'media:second','knowledge_vision','vision-model','vision_low',1)
    priced.rollback()

    assert priced.execute(
        "SELECT reserved_microusd,state FROM saas_ai_spend_reservations WHERE call_key='media:first'"
    )[0]=={'reserved_microusd':900,'state':'reserved'}


def test_media_settlement_releases_unused_and_retains_unknown(priced):
    with priced.cursor() as cur:
        budget.reserve_media(cur,A,'media:unused','knowledge_asr','asr-model','asr',1)
        budget.reserve_media(cur,A,'media:unknown','knowledge_vision','vision-model','vision_low',1)
    priced.commit()
    budget.settle_media(A,'media:unused',operation='asr',attempted_units=0)
    budget.settle_media(A,'media:unknown',operation='vision_low',attempted_units=1,unknown=True)
    rows={r['call_key']:(r['state'],r['actual_microusd']) for r in priced.execute('SELECT call_key,state,actual_microusd FROM saas_ai_spend_reservations')}
    assert rows['media:unused']==('released',0)
    assert rows['media:unknown']==('unknown',None)
