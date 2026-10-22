"""Bounded batch and review queue regressions against isolated PostgreSQL."""
import json
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE
from test_knowledge_candidates import ready,model_card,candidate,GROUP
import tenant_knowledge_batches as batches
import tenant_knowledge_candidates_store as store
import tenant_knowledge_candidates_service as service
from tenant_linear_policy import TenantLinearError

START='2026-10-18T00:00:00Z';END='2026-10-19T00:00:00Z'


def test_expired_batch_does_not_interrupt_admitted_media(batch):
    d,generation,data=batch
    receipt=batches.start(PROFILE,data)
    d.execute("UPDATE saas_knowledge_batches SET deadline=now()-interval '1 second',active_until=now()+interval '90 seconds'");d.commit()
    assert batches.get(PROFILE,receipt['id'])['batch']['status']=='running'
    # Admission deadline still forbids another call despite the active child.
    batches.process(PROFILE,receipt['id'])
    generation.assert_not_called()
    assert batches.get(PROFILE,receipt['id'])['batch']['status']=='running'


def test_dead_batch_becomes_interrupted_without_retry(batch):
    d,generation,data=batch
    receipt=batches.start(PROFILE,data)
    d.execute("UPDATE saas_knowledge_batches SET deadline=now()-interval '1 second',active_until=now()-interval '1 second'");d.commit()
    assert batches.get(PROFILE,receipt['id'])['batch']['status']=='interrupted'
    batches.process(PROFILE,receipt['id'])
    generation.assert_not_called()

@pytest.fixture
def batch(ready,monkeypatch):
    d,generation,_=ready;monkeypatch.setattr(batches,'_connect',lambda:d)
    d.execute("UPDATE saas_crm_messages SET happened_at='2026-10-18T01:00:00Z'::timestamptz+(CASE external_id WHEN 'response-0' THEN 1 WHEN 'response-1' THEN 2 ELSE 0 END * interval '1 second') WHERE tenant_id=%s::uuid",(A,));d.commit()
    def generate(*args,**kwargs):
        refs=[m['ref'] for m in args[3]]
        card=model_card(refs=refs[:2]+([refs[-1]] if len(refs)>2 else []))
        card['outcome']['message_ids']=[refs[-1]]
        return json.dumps({'cards':[card,{**card,'title':'Второй кейс'}]},ensure_ascii=False),{'total_tokens':20}
    generation.side_effect=generate
    return d,generation,{'request_id':str(uuid.uuid4()),'channel_id':CA,'period_start':START,'period_end':END,'conversation_id':'client','analysis_agent_id':AGENT}

def run(batch):
    d,generation,data=batch;receipt=batches.start(PROFILE,data)
    if receipt['created']:batches.process(PROFILE,receipt['id'])
    return batches.get(PROFILE,receipt['id'])['batch']

def test_multiple_cases_manual_batch_replay_and_knowledge_compatibility(batch):
    d,generation,data=batch;result=run(batch)
    assert result['status']=='completed'
    cards=store.listing(PROFILE,{'batch_id':result['id']})['candidates']
    assert len(cards)==2 and all(v['status']=='pending_review' for v in cards)
    assert not d.execute('SELECT id FROM saas_ai_knowledge_sources');d.commit()
    assert batches.start(PROFILE,data)=={'id':result['id'],'created':False}
    assert batches.start(PROFILE,{**data,'request_id':str(uuid.uuid4())})['id']==result['id']
    generation.assert_called_once()
    confirm=next(v for v in cards if v['outcome']['status']=='confirmed')
    assert store.mutate(PROFILE,confirm['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')['candidate']['status']=='approved'

def test_batch_summary_is_tenant_scoped_and_counts_unique_usage(batch):
    d,_,_=batch;result=run(batch);summary=result['summary']
    assert summary['dialog_count']==1 and summary['new_candidates']==2
    assert summary['total_tokens']==20
    assert summary['input_tokens'] is not None and summary['output_tokens'] is not None
    d.execute("""INSERT INTO saas_knowledge_batch_items(tenant_id,batch_id,ordinal,route,extraction_id,status,error_code,message_count)
      SELECT tenant_id,batch_id,99,route,extraction_id,status,error_code,message_count
      FROM saas_knowledge_batch_items WHERE tenant_id=%s::uuid AND batch_id=%s::uuid ORDER BY ordinal LIMIT 1""",(A,result['id']));d.commit()
    assert batches.get(PROFILE,result['id'])['batch']['summary']['total_tokens']==20,'repeated extraction references do not inflate exact token usage'
    with pytest.raises(TenantLinearError):batches.get({'tenant_id':B,'telegram_id':22},result['id'])

def test_period_half_open_and_timezone_equivalence(batch):
    d,generation,data=batch
    d.execute("UPDATE saas_crm_messages SET happened_at='2026-10-19T00:00:00Z',body='OUTSIDE END' WHERE external_id='response-0'")
    d.execute("UPDATE saas_crm_messages SET happened_at='2026-10-17T23:59:59Z',body='OUTSIDE START' WHERE external_id='m1' AND tenant_id=%s::uuid",(A,));d.commit()
    result=run(batch);sent=generation.call_args.args[3]
    assert len(sent)==1 and 'OUTSIDE' not in json.dumps(sent)
    equivalent={**data,'request_id':str(uuid.uuid4()),'period_start':'2026-10-18T04:00:00+04:00','period_end':'2026-10-19T04:00:00+04:00'}
    assert batches.start(PROFILE,equivalent)['id']==result['id']

@pytest.mark.parametrize('patch',[{'period_start':'2026-10-18'},{'period_end':'2026-10-17T00:00:00Z'},{'period_end':'2027-01-01T00:00:00Z'},{'channel_id':CB}])
def test_invalid_period_channel_never_calls_model(batch,patch):
    d,generation,data=batch
    with pytest.raises(TenantLinearError):batches.start(PROFILE,{**data,**patch})
    generation.assert_not_called()

def test_no_text_and_model_error_have_visible_item_codes(batch):
    d,generation,data=batch;generation.side_effect=RuntimeError('PRIVATE PROVIDER ERROR')
    result=run(batch)
    assert result['status']=='failed' and result['items'][0]['error_code']=='model_unavailable'
    assert 'PRIVATE' not in json.dumps(result)
    generation.assert_called_once()

def test_company_limit_and_isolation(batch,monkeypatch):
    d,generation,data=batch
    monkeypatch.setattr(service,'platform_token_limit',lambda *a:1)
    result=run(batch)
    assert result['status']=='failed' and result['items'][0]['error_code']=='limit_exceeded'
    generation.assert_not_called()
    foreign={'tenant_id':B,'telegram_id':22}
    with pytest.raises(TenantLinearError):batches.get(foreign,result['id'])
    assert not store.listing(foreign,{'batch_id':result['id']})['candidates']
    d.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s::uuid",(A,));d.commit()
    with pytest.raises(TenantLinearError):batches.get(PROFILE,result['id'])

def test_queue_filters_keyset_and_needs_clarification(batch):
    d,generation,data=batch;result=run(batch)
    rows=store.listing(PROFILE,{'limit':1,'status':'pending_review'})
    assert len(rows['candidates'])==1 and rows['next_before']
    page=store.listing(PROFILE,{'limit':1,'status':'pending_review','before':rows['next_before']})
    assert len(page['candidates'])==1 and page['candidates'][0]['id']!=rows['candidates'][0]['id']
    item=rows['candidates'][0]
    updated=store.mutate(PROFILE,item['id'],{'expected_version':1,'clarification_note':'Please confirm the result.'},'clarify')['candidate']
    assert updated['status']=='needs_clarification' and updated['version']==2
    assert len(store.listing(PROFILE,{'status':'needs_clarification','channel_id':CA,'batch_id':result['id']})['candidates'])==1
    changed=store.mutate(PROFILE,item['id'],{'expected_version':2,'title':'Уточнённая проблема'})['candidate']
    assert changed['version']==3
    assert store.mutate(PROFILE,item['id'],{'expected_version':3},'reject')['candidate']['status']=='rejected'

def test_bounded_windows_do_not_skip_messages_trimmed_by_context(batch):
    d,generation,data=batch
    d.execute('DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))
    for i in range(100):
        d.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming',%s,'2026-10-18T01:00:00Z'::timestamptz+(%s * interval '1 second'))",
            (A,str(uuid.uuid4()),'large-'+str(i),'connect:'+CA,'Всё работает, помогло. '+'данные '*40,i))
    d.commit();result=run(batch)
    assert generation.call_count<=4 and result['message_count']<=320
    # Every analyzed source ID appears once; rest are explicitly resumable.
    ids=[]
    for item in result['items']:
        if item['extraction_id']: ids.extend(store.request_detail(PROFILE,item['extraction_id'])['request']['source_message_ids'])
    assert len(ids)==len(set(ids))
    assert len(ids)==100 or (result['status']=='partial' and result['continuations'])
    assert result['status'] in {'completed','partial'}

def test_group_two_channel_copies_are_deduplicated_within_period(batch):
    d,generation,data=batch
    d.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'second')",(A,CB))
    for channel,lead in ((CA,1001),(CB,1002)):
        d.execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,%s)',(A,channel,GROUP,lead))
        for i in range(3):
            raw={'channelId':channel,'conversationId':GROUP,'externalMessageId':'EVENT_'+str(i),'participantJid':'994500000001@s.whatsapp.net','participantName':'Иван Иванов'}
            d.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s,'text',%s::jsonb,'2026-10-18T01:00:00Z'::timestamptz+(%s * interval '1 second'))",
                (A,str(uuid.uuid4()),lead,str(uuid.uuid4()),'connect:'+channel,'Всё работает, помогло',json.dumps(raw),i))
    d.commit();batch=(d,generation,{**data,'conversation_id':GROUP,'history_mode':'unified_group'})
    result=run(batch)
    assert result['message_count']==3 and len(generation.call_args.args[3])==3
    assert len(store.listing(PROFILE,{'batch_id':result['id']})['candidates'])==2
    assert 'Иван' not in json.dumps(generation.call_args.args[3],ensure_ascii=False)
    # Discover through the selected channel even when only its linked group copy has activity.
    d.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=1001",(A,))
    d.execute("UPDATE saas_crm_messages SET happened_at='2026-10-18T02:00:00Z' WHERE tenant_id=%s::uuid AND kommo_lead_id=1002",(A,));d.commit()
    discovered={k:v for k,v in data.items() if k!='conversation_id'}
    discovered.update(request_id=str(uuid.uuid4()),history_mode='unified_group',period_start='2026-10-18T02:00:00Z',period_end='2026-10-18T03:00:00Z')
    result=run((d,generation,discovered))
    assert len(result['items'])==1 and result['items'][0]['route']['conversation_id']==GROUP
    assert result['message_count']==3


def test_channel_discovery_is_bounded_and_does_not_require_open_dialog(batch):
    d,generation,data=batch
    for i in range(5):
        lead=2000+i;conv='client-'+str(i)
        d.execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,%s)',(A,CA,conv,lead))
        d.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','Всё работает, помогло','2026-10-18T02:00:00Z')",(A,str(uuid.uuid4()),lead,str(uuid.uuid4()),'connect:'+CA))
    d.commit();data={k:v for k,v in data.items() if k!='conversation_id'}
    result=run((d,generation,data))
    assert len(result['items'])==4 and result['routes_truncated'] and result['status']=='partial'
    assert generation.call_count==4

def test_restart_does_not_replay_uncertain_batch(batch):
    d,generation,data=batch;receipt=batches.start(PROFILE,data)
    d.execute("UPDATE saas_knowledge_batches SET deadline=now()-interval '1 second'");d.commit()
    assert batches.get(PROFILE,receipt['id'])['batch']['status']=='interrupted'
    assert batches.start(PROFILE,data)['created'] is False
    batches.process(PROFILE,receipt['id']);generation.assert_not_called()

def test_clarify_cannot_publish_unconfirmed_result(batch):
    d,generation,data=batch
    generation.side_effect=lambda *a,**k:(json.dumps({'cards':[model_card(confirmed=False)]}),{'total_tokens':10})
    result=run(batch);item=store.listing(PROFILE,{'batch_id':result['id']})['candidates'][0]
    value=store.mutate(PROFILE,item['id'],{'expected_version':1,'clarification_note':'Please confirm the result.'},'clarify')['candidate']
    assert not value['can_approve']
    with pytest.raises(TenantLinearError,match='confirmation_required'):store.mutate(PROFILE,item['id'],{'expected_version':2,'agent_ids':[AGENT]},'approve')

def test_overlapping_period_reuses_evidence_without_duplicate_cards(batch):
    d,generation,data=batch;first=run(batch)
    narrower={**data,'request_id':str(uuid.uuid4()),'period_start':'2026-10-18T00:30:00Z','period_end':'2026-10-18T02:00:00Z'}
    second=run((d,generation,narrower))
    assert first['id']!=second['id']
    assert first['items'][0]['extraction_id']==second['items'][0]['extraction_id']
    assert len(store.listing(PROFILE,{})['candidates'])==2
    generation.assert_called_once()

def test_explicit_http_dispatch_is_finite_and_idempotent(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import tenant_knowledge_candidates_api as api
    async def scenario():
        count=[0];work=[]
        def start(*args):
            count[0]+=1;return {'id':str(uuid.UUID(int=991)),'created':count[0]==1}
        monkeypatch.setattr(api.batches,'start',start)
        monkeypatch.setattr(api.batches,'get',lambda *args:{'batch':{'status':'running'}})
        monkeypatch.setattr(api.batches,'process',lambda *args:work.append(args))
        payload={'expected_tenant_id':A,'expected_user_id':11,'request_id':str(uuid.uuid4()),'channel_id':CA,'period_start':START,'period_end':END}
        async def body():return payload
        request=SimpleNamespace(method='POST',headers={'Origin':'https://test.local'},content_type='application/json',json=body,match_info={})
        handler=api.handler(lambda _:PROFILE,'https://test.local',Mock(),'batches')
        assert (await handler(request)).status==202
        assert (await handler(request)).status==202
        await asyncio.gather(*list(api._batch_tasks))
        await asyncio.sleep(0)
        assert len(work)==1 and not api._batch_tasks
        request.headers={'Origin':'https://evil.local'}
        assert (await handler(request)).status==403
        assert count[0]==2
    asyncio.run(scenario())

def test_status_constraint_upgrade_preserves_existing_candidates(batch):
    import tenant_knowledge_candidates_schema as schema
    d,generation,data=batch;result=run(batch)
    ids=[r['id'] for r in store.listing(PROFILE,{})['candidates']]
    d.execute('ALTER TABLE saas_knowledge_candidates DROP CONSTRAINT saas_knowledge_candidates_status_check')
    d.execute("ALTER TABLE saas_knowledge_candidates ADD CONSTRAINT saas_knowledge_candidates_status_check CHECK(status IN ('pending_review','approved','rejected'))")
    d.commit();schema.create_schema(d);d.commit()
    assert {r['id'] for r in store.listing(PROFILE,{})['candidates']}==set(ids)
    assert store.mutate(PROFILE,ids[0],{'expected_version':1,'clarification_note':'Please confirm the result.'},'clarify')['candidate']['status']=='needs_clarification'
