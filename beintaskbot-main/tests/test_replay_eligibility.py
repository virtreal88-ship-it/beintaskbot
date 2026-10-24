"""Replay eligibility is read-only, bounded and shares the executor's identity gates."""
import json
from unittest.mock import Mock
import pytest
from test_historical_replay import ready
from test_tenant_support_inbox import database,db,A,B,CA,AGENT,PROFILE
import tenant_ai_historical_replay as replay
import tenant_ai_learning_mcp as mcp


def test_selects_five_without_returning_personal_data_or_calling_provider(ready,monkeypatch):
    d,ids=ready; provider=Mock(side_effect=AssertionError('No paid calls'))
    monkeypatch.setattr(replay,'generate',provider)
    result=replay.eligible_examples(PROFILE)
    assert set(result['selected_message_ids'])==set(ids) and result['exact_five_available']
    assert all(item['eligible'] for item in result['items'] if item['id'] in ids)
    assert not any(secret in json.dumps(result) for secret in ('ERP question','@s.whatsapp.net','99450','conversation_id','question'))
    assert not provider.called
    assert d.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
    assert replay.preflight(PROFILE,result['selected_message_ids'])['cap_microusd']==5_000_000
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'AI_LEARNING_MCP_AGENT_ID':AGENT,'AI_LEARNING_MCP_CHANNEL_ID':CA,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE]}
    with d.cursor() as cur:
        assert set(mcp._tools(cur,cfg,'administrator_historical_replay_candidates',{})['selected_message_ids'])==set(ids)
        with pytest.raises(Exception): mcp._tools(cur,{**cfg,'granted_scopes':[]},'administrator_historical_replay_candidates',{})


@pytest.mark.parametrize('fault,reason',[
    ('group','replay_group_not_allowed'),('lid','replay_direct_identity_unverified'),
    ('employee','replay_employee_message_excluded'),('old','replay_message_too_old'),
    ('future','replay_future_timestamp'),('audio','replay_text_required'),
    ('foreign','replay_message_unavailable'),('channel','replay_message_unavailable'),
    ('conflict','replay_identity_conflict'),('paused','replay_conversation_human_or_paused')])
def test_rejection_guidance_preserves_scope_and_identity_guards(ready,fault,reason):
    d,ids=ready; key=ids[0]
    if fault in ('group','lid'):
        value='12345@g.us' if fault=='group' else '12345@lid'
        d.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s::uuid AND lead_id=100',(value,A))
    elif fault=='employee': d.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps({'verified_employee_id':11}),key))
    elif fault=='old': d.execute("UPDATE saas_crm_messages SET happened_at=now()-interval '8 days' WHERE id=%s::uuid",(key,))
    elif fault=='future': d.execute("UPDATE saas_crm_messages SET happened_at=now()+interval '1 day' WHERE id=%s::uuid",(key,))
    elif fault=='audio': d.execute("UPDATE saas_crm_messages SET message_type='audio' WHERE id=%s::uuid",(key,))
    elif fault=='foreign': d.execute('UPDATE saas_crm_messages SET tenant_id=%s::uuid WHERE id=%s::uuid',(B,key))
    elif fault=='channel': d.execute("UPDATE saas_crm_messages SET channel='connect:other' WHERE id=%s::uuid",(key,))
    elif fault=='conflict': d.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps({'conversationIdentityConflict':True,'conversationId':'994501234560@s.whatsapp.net'}),key))
    elif fault=='paused': d.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state,reason) VALUES(%s::uuid,'connect',%s,'994501234560@s.whatsapp.net','paused','manual')",(A,CA))
    d.commit()
    result=replay.eligible_examples(PROFILE,[key])
    assert result['items']==[{'id':key,'eligible':False,'reason':reason}]
    assert not result['selected_message_ids']


def test_candidates_owner_scope_exact_five_and_budget(ready,monkeypatch):
    d,ids=ready
    with pytest.raises(Exception): replay.eligible_examples({'tenant_id':B,'telegram_id':22})
    with pytest.raises(Exception): replay.eligible_examples(PROFILE,[ids[0],ids[0]])
    with pytest.raises(Exception): replay.preflight(PROFILE,ids[:4])
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION','{}')
    with pytest.raises(Exception): replay.eligible_examples(PROFILE)


def test_owner_api_diagnosis_returns_actionable_preflight_error(ready,monkeypatch):
    import asyncio,logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_ai_historical_replay_api as api
    d,ids=ready; monkeypatch.setattr(api,'_connect',lambda:d)
    d.execute("UPDATE saas_connect_conversations SET conversation_id='12345@g.us' WHERE tenant_id=%s::uuid AND lead_id=100",(A,)); d.commit()
    async def check():
        app=web.Application(); app.router.add_post('/replay',api.handler(lambda _:PROFILE,'https://crm.test',logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            result=await client.post('/replay',json={'action':'eligible_examples','message_ids':ids},headers={'Origin':'https://crm.test'})
            assert result.status==200
            assert len((await result.json())['result']['items'])==5
            result=await client.post('/replay',json={'action':'preflight','message_ids':ids},headers={'Origin':'https://crm.test'})
            assert result.status==409
            assert (await result.json())['details']=={'message_id':ids[0],'reason':'replay_group_not_allowed','diagnostic_tool':'administrator_historical_replay_candidates'}
    asyncio.run(check())


def test_schema_prevents_multiple_routes_for_one_source_lead(ready):
    from psycopg.errors import UniqueViolation
    d,ids=ready
    with pytest.raises(UniqueViolation):
        d.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'99999999999@s.whatsapp.net',100)",(A,CA))
    d.rollback()
    assert replay.eligible_examples(PROFILE,[ids[0]])['items'][0]['eligible']
