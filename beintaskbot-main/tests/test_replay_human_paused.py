"""Historical Human/Pause eligibility never grants live transport permissions."""
import uuid
from unittest.mock import Mock
import pytest
from test_historical_replay import ready
from test_tenant_support_inbox import database,db,A,CA,PROFILE
import tenant_ai_historical_replay as replay


@pytest.mark.parametrize('state',['human','paused'])
def test_five_human_paused_replays_are_isolated_and_have_no_future_context(ready,monkeypatch,state):
    d,ids=ready
    for n,mid in enumerate(ids):
        d.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state,reason) VALUES(%s::uuid,'connect',%s,%s,%s,'employee_takeover')",(A,CA,'99450123456'+str(n)+'@s.whatsapp.net',state))
        d.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at,created_at)
          SELECT %s::uuid,tenant_id,kommo_lead_id,%s,channel,'outgoing','HIDDEN FUTURE STAFF ANSWER',happened_at+interval '1 second',created_at
          FROM saas_crm_messages WHERE id=%s::uuid''',(str(uuid.uuid4()),'future-'+str(n),mid))
    d.commit()
    tables=('saas_ai_conversation_state','saas_crm_messages','saas_crm_tasks','saas_crm_deals','saas_ai_agents','saas_ai_agent_settings','saas_ai_simulation_queue','saas_ai_simulation_runs','saas_support_tickets')
    before={table:d.execute('SELECT row_to_json(t) AS row FROM '+table+' t') for table in tables};d.commit()
    def provider(*args,**kwargs):
        lines=args[3]
        assert 'HIDDEN FUTURE STAFF ANSWER' not in str(lines)
        assert lines[-1]['role']=='client' and lines[-1]['text'].startswith('ERP question')
        assert kwargs['extra_context']['external_tools']==[]
        assert d.execute("SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE state='reserved'")[0]['n']==1
        d.commit()
        return 'Virtual only',{'input_tokens':100,'output_tokens':20,'total_tokens':120}
    mocked=Mock(side_effect=provider);monkeypatch.setattr(replay,'generate',mocked)
    selected=replay.eligible_examples(PROFILE)
    assert set(selected['selected_message_ids'])==set(ids) and selected['exact_five_available']
    assert replay.preflight(PROFILE,ids)['remaining_microusd']==5_000_000
    results=[replay.one(PROFILE,mid) for mid in ids]
    assert mocked.call_count==5
    assert all(r['outcome']=='virtual_reply' and not r['sent'] and not r['crm_mutations'] for r in results)
    assert replay.preflight(PROFILE,ids)['remaining_microusd']==5_000_000-5*960
    with pytest.raises(Exception): replay.one(PROFILE,ids[0])
    assert mocked.call_count==5
    for table in tables: assert d.execute('SELECT row_to_json(t) AS row FROM '+table+' t')==before[table]
    assert not replay.eligible_examples(PROFILE,ids)['selected_message_ids']
