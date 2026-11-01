"""Scoped financial hook and unchanged source-flight keys; isolated SQL only."""
import hashlib
import json
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,PROFILE
from test_knowledge_candidates import ready
import tenant_ai_asr_model as asr
import tenant_ai_spend_budget as budget
import tenant_episode_media as episode
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def scoped_db(ready,monkeypatch):
    conn,_,_=ready
    monkeypatch.setattr(asr,'SHARED_ASR_TENANT_DIGEST',hashlib.sha256(('shared-dialog-asr-rollout:v1:'+A).encode()).hexdigest())
    monkeypatch.setattr(budget,'_connect',lambda:conn)
    monkeypatch.setattr(episode,'_connect',lambda:conn)
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:60000,B:1000000}))
    budget.ensure_schema(conn);conn.commit()
    return conn

@pytest.mark.parametrize('unknown',[False,True])
def test_scoped_fixed_reserve_actual_model_known_settle_or_unknown_hold(scoped_db,unknown):
    conn=scoped_db
    with conn.cursor() as cur:
        budget.reserve_media(cur,A,'shared:one','episode_media','gpt-4o-transcribe','asr',1,_shared_dialog_asr=True)
    conn.commit()
    budget.settle_media(A,'shared:one',operation='asr',attempted_units=1,unknown=unknown)
    row=conn.execute("SELECT model,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations WHERE call_key='shared:one'")[0]
    assert row=={'model':'gpt-4o-transcribe','state':'unknown' if unknown else 'settled','reserved_microusd':60000,'actual_microusd':None if unknown else 60000}
    with conn.cursor() as cur:
        with pytest.raises(TenantLinearError,match='budget_exhausted'):
            budget.reserve_media(cur,A,'shared:two','episode_media','gpt-4o-transcribe','asr',1,_shared_dialog_asr=True)

@pytest.mark.parametrize('cap',[None,0])
def test_cap_missing_zero_preserves_existing_no_ledger_semantics(scoped_db,monkeypatch,cap):
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({} if cap is None else {A:cap}))
    with scoped_db.cursor() as cur:
        assert budget.reserve_media(cur,A,'shared:uncapped','episode_media','gpt-4o-transcribe','asr',1,_shared_dialog_asr=True) is None
    assert scoped_db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    assert asr.scoped_strong_quote(A)['reserve_microusd']==60000  # Provider still independently checks.


def test_hook_cannot_change_other_tenant_or_unopted_tariffs(scoped_db):
    assert budget.media_price('gpt-4o-transcribe','asr') is None
    with scoped_db.cursor() as cur:
        with pytest.raises(TenantLinearError,match='price_unknown'):
            budget.reserve_media(cur,A,'unopted','episode_media','gpt-4o-transcribe','asr',1)
        with pytest.raises(TenantLinearError,match='scope_unavailable'):
            budget.reserve_media(cur,B,'wrong-company','episode_media','gpt-4o-transcribe','asr',1,_shared_dialog_asr=True)
    assert scoped_db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0

@pytest.mark.parametrize('raw',['{"whisper-1":{"asr":0}}','{"whisper-1":{"asr":null}}','not-json'])
def test_old_blocked_tariff_never_reenabled(scoped_db,monkeypatch,raw):
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',raw)
    assert asr.shared_audio_model('whisper-1',tenant=A,credential_source='company',context='manual_helper')=='whisper-1'
    with scoped_db.cursor() as cur:
        with pytest.raises(TenantLinearError,match='price_unknown'):
            budget.reserve_media(cur,A,'old-block','episode_media','whisper-1','asr',1)
    assert scoped_db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0

@pytest.mark.parametrize('state',['completed','unknown'])
def test_source_flight_result_is_model_and_policy_independent(scoped_db,state):
    episode._schema(scoped_db)
    auth=type('Authority',(),{'tenant':A,'check':Mock()})()
    ref={'external_id':'connect:synthetic','media_kind':'voice','body_hash':'unchanged-body','media_version':'unchanged-version','channel_id':CA}
    key,token,_=episode._flight(auth,ref)
    old={'status':'processed','transcript':'Retained synthetic speech','model':'whisper-1'} if state=='completed' else {'status':'unknown','code':'media_result_unknown'}
    scoped_db.execute('UPDATE saas_episode_media_flights SET state=%s,result=%s::jsonb WHERE tenant_id=%s::uuid',(state,json.dumps(old),A));scoped_db.commit()
    auth.effective_model='gpt-4o-transcribe';auth.request_policy={'language_policy':'auto'}
    same,new_token,saved=episode._flight(auth,ref)
    assert same==key and new_token is None and saved==old
    assert scoped_db.execute('SELECT count(*) AS n FROM saas_episode_media_flights')[0]['n']==1

@pytest.mark.parametrize('unknown',[False,True])
def test_real_shared_dispatch_uses_private_quote_and_records_real_model(scoped_db,unknown):
    from test_strong_asr_default import authority
    execution=authority(tenant=A)
    item=execution.item({'media_kind':'audio'},{})
    execution.validate=Mock();execution.admit_call=Mock();execution.record_usage=Mock()
    invoke=Mock(side_effect=TimeoutError('synthetic') if unknown else None,
                return_value=('Synthetic speech',{'total_tokens':0}))
    if unknown:
        with pytest.raises(TimeoutError):execution.transcribe_once({},item,'gpt-4o-transcribe',(b'synthetic','wav','audio/wav'),lambda *a,**kw:invoke())
    else:
        assert execution.transcribe_once({},item,'gpt-4o-transcribe',(b'synthetic','wav','audio/wav'),lambda *a,**kw:invoke())[0]=='Synthetic speech'
    invoke.assert_called_once()
    row=scoped_db.execute('SELECT model,state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations')[0]
    assert row=={'model':'gpt-4o-transcribe','state':'unknown' if unknown else 'settled','reserved_microusd':60000,'actual_microusd':None if unknown else 60000}
