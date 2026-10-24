"""Evidence-only technical selection: greetings are never automatic padding."""
import json,uuid
import pytest
from test_historical_replay import ready
from test_tenant_support_inbox import database,db,A,CA,AGENT,PROFILE
import tenant_ai_historical_replay as replay


@pytest.mark.parametrize('text',['Salam','Coxsagolun','Teshekkur edirem','Çox sağ olun!','Thank you','Спасибо!','https://example.test/erp'])
def test_greetings_thanks_and_bare_urls_are_not_technical(text):
    assert replay.substantive_support_reason(text) is not None

@pytest.mark.parametrize('text',['Printer işləmir','Anbar qalığı niyə görünmür?','Как исправить ошибку склада?','ERP login fails with error 403','How do I change invoice settings?'])
def test_actual_product_problem_has_explicit_technical_signal(text):
    assert replay.substantive_support_reason(text) is None


def test_does_not_pad_five_and_manual_selection_preserves_policy(ready):
    d,ids=ready
    for mid,text in zip(ids,['Salam','Coxsagolun','Teshekkur edirem','https://example.test','Printer işləmir']):
        d.execute('UPDATE saas_crm_messages SET body=%s WHERE id=%s::uuid',(text,mid))
    d.commit()
    automatic=replay.eligible_examples(PROFILE)
    assert automatic['selected_message_ids']==[ids[-1]]
    assert automatic['selection_status']=='insufficient_technical_examples' and not automatic['automatic_padding']
    manual=replay.eligible_examples(PROFILE,ids)
    assert manual['selected_message_ids']==ids and all(item['selection_mode']=='manual' for item in manual['items'])
    assert all(item['resolution_status']=='unknown' for item in manual['items'])
    d.execute("UPDATE saas_crm_messages SET message_type='audio' WHERE id=%s::uuid",(ids[0],));d.commit()
    assert not replay.eligible_examples(PROFILE,[ids[0]])['items'][0]['eligible']


def seed_confirmed_resolution(d,message_id,channel=CA):
    import tenant_knowledge_candidates_schema as schema
    schema.create_schema(d)
    extraction=str(uuid.uuid4());candidate=str(uuid.uuid4())
    d.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider) VALUES(%s::uuid,%s,'connect')",(A,extraction))
    d.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,1,'mock','completed')",(A,extraction,AGENT,extraction))
    d.execute("INSERT INTO saas_knowledge_extractions(tenant_id,id,created_by,route,source_refs,source_digest,fingerprint,request_key,status,deadline) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,'[]'::jsonb,'mock',%s,%s,'completed',now())",(A,extraction,json.dumps({'channel_id':channel,'conversation_id':'994501234560@s.whatsapp.net'}),extraction,extraction))
    d.execute("INSERT INTO saas_knowledge_candidates(tenant_id,id,extraction_id,content,content_hash,status,reviewed_by,reviewed_at) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::jsonb,%s,'approved',11,now())",(A,candidate,extraction,json.dumps({'source_message_ids':[message_id],'outcome':{'status':'confirmed'}}),candidate))
    d.commit();return candidate


def test_only_exact_reviewed_resolution_ranks_first_without_future_input(ready):
    d,ids=ready
    for mid in ids:d.execute("UPDATE saas_crm_messages SET body='ERP printer error prevents printing invoice' WHERE id=%s::uuid",(mid,))
    d.commit();case=seed_confirmed_resolution(d,ids[0])
    result=replay.eligible_examples(PROFILE)
    assert result['selected_message_ids'][0]==ids[0]
    confirmed=next(item for item in result['items'] if item['id']==ids[0])
    assert confirmed['resolution_status']=='confirmed_case' and confirmed['resolution_case_id']==case
    assert all(item['resolution_status']=='unknown' for item in result['items'] if item['id']!=ids[0])
    with d.cursor() as cur:
        _,_,lines=replay.source(cur,A,ids[0])
        assert 'confirmed_case' not in str(lines)
    d.execute("UPDATE saas_knowledge_candidates SET status='pending_review' WHERE id=%s::uuid",(case,));d.commit()
    assert all(item['resolution_status']=='unknown' for item in replay.eligible_examples(PROFILE)['items'])


def test_confirmed_case_from_other_channel_is_not_resolution_evidence(ready):
    d,ids=ready
    seed_confirmed_resolution(d,ids[0],channel=str(uuid.uuid4()))
    item=next(item for item in replay.eligible_examples(PROFILE)['items'] if item['id']==ids[0])
    assert item['resolution_status']=='unknown' and item['resolution_case_id'] is None
