"""Single server-selected lower receipt probe: synthetic SQL only."""
import asyncio
import json
import uuid
from unittest.mock import Mock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from test_manual_ai_run_diagnostics import fixture,args
from test_assigned_operator_copilot import ready
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE
from tenant_linear_policy import TenantLinearError
import tenant_manual_ai_run_diagnostics as diag
import tenant_episode_media as episode

@pytest.fixture
def lower(fixture):
    db,request,source,generated=fixture
    episode._schema(db)
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body,raw) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','image','PRIVATE CUSTOMER',%s::jsonb)",(str(uuid.uuid4()),A,source,'connect:'+CA,json.dumps({'mediaId':'current-version-different-from-historical-key','url':'https://PRIVATE'})))
    db.commit()
    return db,request,source,generated


def seed(lower,*,tenant=A,channel=CA,version='c'*64,source=None):
    db,request,actual,_=lower;source=source or actual
    payload={'status':'unknown','code':'media_result_unknown','failure_stage':'visual_analysis','failure_code':'provider_http_403_model_access','observations':[{'text':'PRIVATE TRANSCRIPT'}],'token':'PRIVATE'}
    db.execute("INSERT INTO saas_episode_media_flights(tenant_id,channel_id,external_id,source_hash,token,state,result) VALUES(%s::uuid,%s::uuid,%s,%s,%s::uuid,'unknown',%s::jsonb)",(tenant,channel,source,version,str(uuid.uuid4()),json.dumps(payload)))
    db.execute("INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,coverage,result,provenance) VALUES(%s::uuid,%s::uuid,%s,%s,'image','client','error','media_unavailable',%s::jsonb,%s::jsonb,%s::jsonb)",(tenant,channel,source,version,json.dumps({'complete':False,'private':'PRIVATE'}),json.dumps(payload),json.dumps({'processor':'conversation_episode','private':'PRIVATE'})))
    db.commit()


def read(lower,**extra):return diag.read(PROFILE,args(request_id=lower[1],include_source_receipts='1',**extra))


def test_exact_historical_key_metadata_no_writes_provider_or_text(lower,monkeypatch):
    db,request,source,generated=lower;seed(lower)
    original=db.query;sql=[]
    def record(statement,params=()):sql.append(statement);return original(statement,params)
    monkeypatch.setattr(db,'query',record)
    result=read(lower);probe=result['items'][0]['source_receipts']
    assert probe['status']=='records_present'
    assert probe['flight']['state']=='unknown' and probe['flight']['failure_code']=='provider_http_403_model_access'
    assert probe['receipt']['coverage_complete'] is False and probe['receipt']['processor']=='conversation_episode'
    assert {'source_records_not_request_linked','absence_not_dispatch_proof'}<=set(probe['limitations'])
    for private in ['PRIVATE','https://','token','@s.whatsapp.net']:assert private not in json.dumps(result)
    assert not any(x.lstrip().upper().startswith(('CREATE ','ALTER ','INSERT ','UPDATE ','DELETE ')) for x in sql)
    generated.assert_not_called()
    assert db.execute('SELECT state FROM saas_episode_media_flights WHERE tenant_id=%s::uuid AND external_id=%s',(A,source))[0]['state']=='unknown'
    assert 'source_receipts' not in diag.read(PROFILE,args(request_id=request))['items'][0]


@pytest.mark.parametrize('foreign', ['tenant','channel','hash','source'])
def test_neighbor_records_never_match_selected_run_key(lower,foreign):
    seed(lower,**{'tenant':{'tenant':B},'channel':{'channel':CB},'hash':{'version':'d'*64},'source':{'source':'connect:'+str(uuid.uuid4())}}[foreign])
    probe=read(lower)['items'][0]['source_receipts']
    assert probe['status']=='unknown' and probe['reason']=='records_absent' and probe['flight'] is None and probe['receipt'] is None


@pytest.mark.parametrize('change', ['invalid_hash','invalid_source','wrong_lead','wrong_kind','no_failure'])
def test_untrusted_or_unscoped_stored_selection_fails_closed(lower,change,monkeypatch):
    db,request,source,_=lower;seed(lower)
    if change=='wrong_lead':db.execute('UPDATE saas_crm_messages SET kommo_lead_id=999 WHERE tenant_id=%s::uuid AND external_id=%s',(A,source))
    elif change=='wrong_kind':db.execute("UPDATE saas_crm_messages SET message_type='text' WHERE tenant_id=%s::uuid AND external_id=%s",(A,source))
    else:
        path={'invalid_hash':'source_hash','invalid_source':'source_id'}.get(change)
        if path:db.execute('UPDATE saas_chat_ai_runs SET result=jsonb_set(result,%s::text[],%s::jsonb) WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(['media_failure',path],json.dumps('PRIVATE-JID'),A,request))
        else:db.execute("UPDATE saas_chat_ai_runs SET result='{}'::jsonb WHERE tenant_id=%s::uuid AND request_id=%s::uuid",(A,request))
    db.commit();sql=[];original=db.query
    def record(statement,params=()):sql.append(statement);return original(statement,params)
    monkeypatch.setattr(db,'query',record)
    probe=read(lower)['items'][0]['source_receipts']
    assert probe['status']=='unknown' and probe['reason']=='source_unverified' and probe['flight'] is None
    assert not any('FROM saas_episode_media_flights' in x or 'FROM saas_media_evidence_receipts' in x for x in sql)


def test_absent_tables_no_ddl_and_missing_run_no_probe(lower,monkeypatch):
    db,*_=lower
    db.execute('DROP TABLE saas_episode_media_flights');db.execute('DROP TABLE saas_media_evidence_receipts');db.commit()
    probe=read(lower)['items'][0]['source_receipts']
    assert probe['status']=='unknown' and probe['reason']=='records_unavailable'
    original=diag._source_receipts;probe=Mock(wraps=original);monkeypatch.setattr(diag,'_source_receipts',probe)
    assert diag.read(PROFILE,args(request_id=str(uuid.uuid4()),include_source_receipts='1'))['items']==[]
    probe.assert_not_called()


def test_lower_metadata_safe_enums_only(lower):
    db,*_=lower;seed(lower)
    db.execute("UPDATE saas_episode_media_flights SET result=%s::jsonb",(json.dumps({'status':'PRIVATE','code':'PRIVATE','failure_stage':'https://PRIVATE','failure_code':'sk-PRIVATE'}),))
    db.execute("UPDATE saas_media_evidence_receipts SET code='PRIVATE',coverage=%s::jsonb,provenance=%s::jsonb",(json.dumps({'complete':'PRIVATE'}),json.dumps({'processor':'PRIVATE'})));db.commit()
    assert 'PRIVATE' not in json.dumps(read(lower))


@pytest.mark.parametrize('bad',[{'include_source_receipts':'1'},{'include_source_receipts':'true'},{'include_source_receipts':'0'},{'include_source_receipts':'1','request_id':'bad'}])
def test_flag_input_api_fails_closed_not_global_snapshot(lower,monkeypatch,bad):
    import tenant_ai_state_api as api
    snapshot=Mock(side_effect=AssertionError('Must not fall back to global snapshot'));monkeypatch.setattr(api,'snapshot',snapshot)
    async def scenario():
        app=web.Application();app.router.add_get('/technical',api.handler(lambda _:PROFILE,Mock(),'technical'))
        async with TestClient(TestServer(app)) as client:
            response=await client.get('/technical',params={'expected_tenant_id':A,'expected_user_id':'11',**bad})
            assert response.status==400
            snapshot.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize('bad',[{'source_hash':'c'*64},{'source_id':'connect:'+str(uuid.uuid4())}])
def test_client_cannot_supply_lower_key(lower,bad):
    with pytest.raises(TenantLinearError) as caught:read(lower,**bad)
    assert caught.value.status==400


def test_valid_http_probe_and_identity_binding(lower):
    import tenant_ai_state_api as api
    seed(lower)
    async def scenario():
        app=web.Application();app.router.add_get('/technical',api.handler(lambda _:PROFILE,Mock(),'technical'))
        async with TestClient(TestServer(app)) as client:
            params=args(expected_tenant_id=A,expected_user_id='11',request_id=lower[1],include_source_receipts='1')
            response=await client.get('/technical',params=params)
            body=await response.json()
            assert response.status==200 and body['result']['items'][0]['source_receipts']['status']=='records_present'
            response=await client.get('/technical',params={**params,'expected_user_id':'22'})
            assert response.status==409
    asyncio.run(scenario())


def test_other_tenant_request_cannot_read_lower_rows(lower):
    with pytest.raises(TenantLinearError):
        diag.read({'tenant_id':B,'telegram_id':22},args(request_id=lower[1],include_source_receipts='1'))
