import asyncio
import json
import logging
import uuid
from unittest.mock import Mock
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from test_historical_replay import ready
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,B,CA,CB,AGENT,PROFILE
import tenant_ai_historical_replay as replay
import tenant_ai_historical_replay_api as api
from scripts import whatsapp_replay as batch
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def boundary(ready,monkeypatch,tmp_path):
    db,ids=ready
    monkeypatch.setattr(api,'_connect',lambda:db)
    monkeypatch.setattr(batch,'_connect',lambda:db)
    monkeypatch.setattr(batch,'PILOT_TENANT',A)
    monkeypatch.setattr(api.tempfile,'gettempdir',lambda:str(tmp_path))
    calls=[]
    def provider(*args,**kwargs):
        calls.append(1);return 'Virtual only',{'input_tokens':50,'output_tokens':20,'total_tokens':70}
    monkeypatch.setattr(replay,'generate',provider)
    return db,ids,calls


def test_owner_http_preflight_execute_and_duplicate_use_existing_shared_ledger(boundary):
    db,ids,calls=boundary;request_id=str(uuid.uuid4())
    for author,direction,raw in [('AI Agent','outgoing',{'run_id':'synthetic'}),('Operator','outgoing',{}),('Employee','incoming',{'verified_employee_id':11})]:
        mid=str(uuid.uuid4())
        db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type,happened_at,raw)
          SELECT %s::uuid,tenant_id,kommo_lead_id,%s,channel,%s,%s,'Future comparison only','text',clock_timestamp()+interval '1 second',%s::jsonb
          FROM saas_crm_messages WHERE id=%s::uuid''',(mid,'connect:'+mid,direction,author,json.dumps(raw),ids[0]))
    db.commit()
    before=db.execute('SELECT row_to_json(m) AS r FROM saas_crm_messages m');db.commit()
    async def run():
        app=web.Application();app.router.add_route('*','/replay',api.handler(lambda request:PROFILE,'https://crm.test',logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            headers={'Origin':'https://crm.test'}
            result=await client.post('/replay',json={'action':'preflight','message_ids':ids},headers=headers)
            assert result.status==200 and (await result.json())['result']['cap_microusd']==5_000_000
            assert not calls
            data={'action':'execute','message_ids':ids,'request_id':request_id}
            result=await client.post('/replay',json=data,headers=headers)
            assert result.status==200
            report=(await result.json())['result']
            assert len(report['results'])==5 and all(not item['sent'] and not item['crm_mutations'] for item in report['results'])
            assert all(item['quality_assessment']['verdict']=='not_reviewed' for item in report['results'])
            assert all(item['quality_assessment']['model_call_success_is_not_quality_proof'] for item in report['results'])
            assert [row['author_type'] for row in report['results'][0]['comparison_only_employee_replies']]==['operator','employee']
            assert (await client.post('/replay',json=data,headers=headers)).status==200
            assert (await client.get('/replay',params={'request_id':request_id})).status==200
            assert (await client.post('/replay',json={**data,'message_ids':list(reversed(ids))},headers=headers)).status==409
    asyncio.run(run())
    assert len(calls)==5
    assert db.execute('SELECT row_to_json(m) AS r FROM saas_crm_messages m')==before
    assert db.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==5
    assert not db.execute('SELECT * FROM saas_ai_simulation_queue')
    assert db.execute('SELECT mode FROM saas_ai_agents WHERE id=%s::uuid',(AGENT,))[0]['mode']=='manual';db.commit()


@pytest.mark.parametrize('kind',['anonymous','foreign','non_owner','origin','too_many','actor_injection','oversize','path'])
def test_http_boundary_denies_unauthorized_or_invalid_requests_without_paid_calls(boundary,kind):
    db,ids,calls=boundary;profile=PROFILE
    if kind=='anonymous':profile=None
    if kind=='foreign':profile={'tenant_id':B,'telegram_id':22}
    if kind=='non_owner':
        db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    async def run():
        app=web.Application();app.router.add_route('*','/replay',api.handler(lambda request:profile,'https://crm.test',logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            data={'action':'preflight','message_ids':ids}
            headers={'Origin':'https://evil.test' if kind=='origin' else 'https://crm.test'}
            if kind=='too_many':data['message_ids']=ids+[str(uuid.uuid4())]
            if kind=='actor_injection':data['actor']=11
            if kind=='oversize':data['padding']='x'*5000
            if kind=='path':response=await client.get('/replay',params={'request_id':'../private'})
            else:response=await client.post('/replay',json=data,headers=headers)
            assert response.status in {400,403,413}
    asyncio.run(run());assert not calls
    assert not db.execute('SELECT * FROM saas_ai_spend_reservations');db.commit()


def test_partial_or_unknown_report_is_readable_but_never_reexecuted(boundary,monkeypatch):
    db,ids,calls=boundary
    provider=Mock(side_effect=TimeoutError('synthetic provider timeout'));monkeypatch.setattr(replay,'generate',provider)
    request_id=str(uuid.uuid4());data={'action':'execute','message_ids':ids,'request_id':request_id}
    with pytest.raises(TimeoutError):api.operate(PROFILE,data)
    report=api.operate(PROFILE,data)
    assert report['stopped']['paid_outcome_requires_ledger_check'] and not report['results']
    assert provider.call_count==1
    assert db.execute('SELECT state FROM saas_ai_spend_reservations')[0]['state']=='unknown';db.commit()
