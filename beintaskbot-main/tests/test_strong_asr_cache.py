"""Real isolated SQL artifact fences across an internal model transition."""
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,PROFILE
from test_knowledge_candidates import ready
import tenant_knowledge_media_cache as cache
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def cached(ready,monkeypatch):
    conn,_,_=ready
    monkeypatch.setattr(cache,'_connect',lambda:conn)
    cache.schema(conn.cursor());conn.commit()
    return conn,{'tenant':A,'key':'synthetic','route':{},'refs':[]},(b'synthetic-upload','wav','audio/wav')

@pytest.mark.parametrize('old_model',['whisper-1','gpt-4o-mini-transcribe'])
@pytest.mark.parametrize('state',['running','unknown','completed'])
def test_old_paid_artifact_cannot_be_replayed_by_new_default(cached,old_model,state):
    conn,item,media=cached
    fingerprint=cache.transcription_fingerprint(item,old_model,media)
    conn.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,transcript) VALUES(%s::uuid,%s,%s,'00000000-0000-0000-0000-000000000001','Retained synthetic transcript')",(A,fingerprint,state));conn.commit()
    invoke=Mock()
    if state=='completed':
        assert cache.transcribe_once(PROFILE,item,'gpt-transcribe',media,invoke)==('Retained synthetic transcript',{},False)
        assert item['asr_artifact_model']==old_model
    else:
        with pytest.raises(TenantLinearError):cache.transcribe_once(PROFILE,item,'gpt-transcribe',media,invoke)
    invoke.assert_not_called()
    assert conn.execute('SELECT count(*) AS n FROM saas_knowledge_media_cache')[0]['n']==1


def test_new_unknown_source_fence_blocks_changed_options(cached,monkeypatch):
    conn,item,media=cached
    invoke=Mock(side_effect=TimeoutError('synthetic timeout'))
    with pytest.raises(TimeoutError):cache.transcribe_once(PROFILE,item,'gpt-transcribe',media,invoke)
    original=cache.transcription_fingerprint
    monkeypatch.setattr(cache,'transcription_fingerprint',lambda *a,**k: original(*a,**k)+'changed-options')
    with pytest.raises(TenantLinearError):cache.transcribe_once(PROFILE,item,'gpt-transcribe',media,invoke)
    invoke.assert_called_once()
    rows=conn.execute('SELECT state FROM saas_knowledge_media_cache')
    assert len(rows)==2 and all(row['state']=='unknown' for row in rows)


def test_new_completed_receipt_reused_under_changed_policy_fingerprint(cached,monkeypatch):
    conn,item,media=cached
    item['asr_artifact_model']='stale-other-artifact'
    invoke=Mock(return_value=('Stored synthetic transcript',{'total_tokens':1}))
    assert cache.transcribe_once(PROFILE,item,'gpt-transcribe',media,invoke)[2]
    assert 'asr_artifact_model' not in item
    original=cache.transcription_fingerprint
    monkeypatch.setattr(cache,'transcription_fingerprint',lambda *a,**k: original(*a,**k)+'changed-options')
    assert cache.transcribe_once(PROFILE,item,'gpt-transcribe',media,invoke)==('Stored synthetic transcript',{},False)
    invoke.assert_called_once()

@pytest.mark.parametrize('unknown',[False,True])
def test_default_reserve_settlement_and_unknown_hold(cached,monkeypatch,unknown):
    import tenant_ai_spend_budget as budget
    conn,_,_=cached
    monkeypatch.setattr(budget,'_connect',lambda:conn)
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD','{"'+A+'":30000}')
    budget.ensure_schema(conn)
    with conn.cursor() as cur:
        budget.reserve_media(cur,A,'synthetic-strong','audio','gpt-transcribe','asr',1)
    conn.commit()
    budget.settle_media(A,'synthetic-strong',operation='asr',attempted_units=1,unknown=unknown)
    row=conn.execute("SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations WHERE call_key='synthetic-strong'")[0]
    assert row=={'state':'unknown' if unknown else 'settled','reserved_microusd':30000,'actual_microusd':None if unknown else 30000}
    with conn.cursor() as cur:
        with pytest.raises(TenantLinearError,match='budget_exhausted'):
            budget.reserve_media(cur,A,'synthetic-second','audio','gpt-transcribe','asr',1)

@pytest.mark.parametrize('price',[22499,30001])
def test_direct_reservation_cannot_bypass_quote_bound(cached,monkeypatch,price):
    import tenant_ai_spend_budget as budget
    conn,_,_=cached
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{"gpt-transcribe":{"asr":'+str(price)+'}}')
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD','{"'+A+'":1000000}')
    budget.ensure_schema(conn)
    with conn.cursor() as cur:
        with pytest.raises(TenantLinearError,match='price_unknown'):
            budget.reserve_media(cur,A,'synthetic-invalid','audio','gpt-transcribe','asr',1)
