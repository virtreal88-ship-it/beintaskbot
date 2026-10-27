"""Isolated PostgreSQL/WASM; no production or paid provider access."""
import json
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,seed_job
import tenant_knowledge_candidates_store as store
import tenant_knowledge_candidates_service as service
import tenant_knowledge_candidates_policy as policy
import tenant_knowledge_live_queue as live
import tenant_ai_agents_store as agents
import tenant_ai_agent_run_store as runs
from tenant_linear_policy import TenantLinearError
from tenant_ai_testing_budget import TOKEN_USAGE_SQL

EXPERT=str(uuid.UUID(int=401));GROUP='120363000000000@g.us'

def model_card(refs=('m1','m2','m3'),confirmed=True,title='Нет связи'):
    return {'title':title,'symptoms':['Синхронизация остановилась'],'cause':{'status':'suspected','text':'Соединение недоступно'},
        'resolution_steps':['Проверить соединение','Повторить синхронизацию'],'outcome':{'status':'confirmed' if confirmed else 'unconfirmed','text':'Клиент подтвердил результат' if confirmed else 'Нужна проверка','message_ids':[refs[-1]]},
        'source_message_ids':list(refs),'product':'Bein','product_version':'2.1','clarification_note':'','case_relation':'new_request','work_status':'resolved' if confirmed else 'unresolved'}

@pytest.fixture
def ready(db,monkeypatch):
    for module in (store,service,runs):monkeypatch.setattr(module,'_connect',lambda:db)
    seed_job(db)
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,role) VALUES(%s::uuid,%s::uuid,'Expert',11,'expert')",(A,EXPERT))
    db.execute("UPDATE saas_crm_messages SET body='Синхронизация не работает' WHERE tenant_id=%s::uuid",(A,))
    for index,(body,direction) in enumerate([('Проверьте соединение и повторите синхронизацию','outgoing'),('Всё работает, помогло','incoming')]):
        db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,now()+(%s * interval '1 second'))",
            (A,str(uuid.UUID(int=800+index)),'response-'+str(index),'connect:'+CA,direction,body,index+1))
    db.commit()
    monkeypatch.setattr(service,'choose_api_credential',lambda *a:('synthetic-key','platform'))
    monkeypatch.setattr(service,'platform_model_allowed',lambda *a:True)
    monkeypatch.setattr(service,'platform_token_limit',lambda *a:0)
    generation=Mock(return_value=(json.dumps({'cards':[model_card()]},ensure_ascii=False),{'input_tokens':40,'output_tokens':20,'total_tokens':60}))
    monkeypatch.setattr(service,'generate',generation)
    return db,generation,{'request_id':str(uuid.uuid4()),'provider':'connect','channel_id':CA,'conversation_id':'client','analysis_agent_id':AGENT}

def candidate(ready):
    db,generation,data=ready
    return service.analyze(PROFILE,data)['candidates'][0]


@pytest.mark.parametrize('legacy_format,changed',[(False,True),(True,False)])
def test_owner_can_read_stale_attachment_diagnostics_without_reattributing_saved_analysis(ready,monkeypatch,legacy_format,changed):
    db,generation,_=ready;value=candidate(ready)
    run=db.execute('SELECT * FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]
    refs=run['source_refs'];ref=refs[0]
    facts={key:None for key in store._MEDIA_IDENTITY_FIELDS};facts.update({'mediaId':'old','fileLength':10})
    if legacy_format:
        import hashlib
        old_version=hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest()
        current=facts
    else:
        old_version=store.digest(facts);current={**facts,'mediaId':'replacement','fileLength':14} if changed else facts
    ref.update(media_kind='image',media_version=old_version)
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps(refs),json.dumps([{'id':ref['id'],'status':'processed','code':'vision_observation','observation':'OLD_ATTACHMENT_SECRET_OBSERVATION'}]),A,value['extraction_id']))
    db.execute('UPDATE saas_crm_messages SET message_type=\'image\',raw=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps({**current,'mediaKey':'SECRET_MEDIA_KEY','url':'https://example.invalid/?token=SECRET'}),A,ref['id']))
    db.commit()
    opened=store.detail(PROFILE,value['id'])
    diagnostic=opened['candidate']['media_source_diagnostics'][0]
    assert diagnostic['saved_version']==old_version and diagnostic['current_version']==store.digest(current)
    assert diagnostic['current_kind']=='image' and diagnostic['identity_fields']['mediaId']=={'original_value_retained':False,'current_present':True}
    assert opened['evidence'][0]['attachment_evidence_status']=='stale_unverified'
    assert 'OLD_ATTACHMENT_SECRET_OBSERVATION' not in opened['evidence'][0]['text']
    assert 'SECRET_MEDIA_KEY' not in json.dumps(opened) and 'example.invalid' not in json.dumps(opened)
    assert opened['candidate']['can_approve'] is False
    assert opened['candidate']['source_claims_unverified'] is True
    assert opened['candidate']['approval_block_reason']=='source_attachment_changed_review_required'
    assert generation.call_count==1
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps({**current,'mediaId':'changed_after_read','mediaKey':'SECRET_MEDIA_KEY'}),A,ref['id']))
    db.commit()
    with pytest.raises(TenantLinearError,match='Вложение исходного сообщения изменилось'):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[EXPERT]},'approve')


def test_stale_attachment_diagnostics_remain_tenant_scoped(ready):
    db,_,_=ready;value=candidate(ready)
    with pytest.raises(TenantLinearError):
        store.detail({'tenant_id':B,'telegram_id':22},value['id'])


def test_owner_source_review_diagnostic_uses_only_case_claim_message_ids(ready):
    import tenant_media_evidence as media_evidence
    db,generation,_=ready;value=candidate(ready)
    run=db.execute('SELECT * FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]
    refs=run['source_refs'];assert len(refs)>=2
    unrelated,target=refs[:2]
    old_facts={key:None for key in store._MEDIA_IDENTITY_FIELDS};old_facts.update({'mediaId':'old','fileLength':10})
    current_facts={**old_facts,'mediaId':'current','fileLength':16}
    for ref in (unrelated,target):
        ref.update(media_kind='image',media_version=store.digest(old_facts))
        db.execute("UPDATE saas_crm_messages SET message_type='image',raw=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
          (json.dumps(current_facts),A,ref['id']))
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps(refs),A,value['extraction_id']))
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]['content']
    content['source_message_ids']=[target['id']];content['start_message_id']=target['id'];content['end_message_id']=target['id']
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps(content),A,value['id']));db.commit()

    opened=store.detail(PROFILE,value['id'])
    assert [item['id'] for item in opened['candidate']['media_source_diagnostics']]==[target['id']]
    assert target['id'] in opened['candidate']['source_message_ids']
    assert unrelated['id'] not in opened['candidate']['media_source_diagnostics']
    with pytest.raises(TenantLinearError,match='Вложение не входит в доказательства этого кейса'):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'message_id':unrelated['id'],'review_note':'Reviewed the current attachment as a separate source.','acknowledge_new_source':True},'source_review')
    reviewed=store.mutate(PROFILE,value['id'],{'expected_version':1,'message_id':opened['candidate']['media_source_diagnostics'][0]['id'],
      'review_note':'Reviewed the current attachment as a separate source.','acknowledge_new_source':True},'source_review')
    assert reviewed['candidate']['media_source_review']['message_id']==target['id']
    assert reviewed['candidate']['media_source_review']['revision']==1
    assert generation.call_count==1


def test_owner_source_review_preflight_is_read_only_and_shares_mutation_membership_predicate(ready,monkeypatch):
    db,generation,_=ready;value=candidate(ready)
    run=db.execute('SELECT * FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]
    target,unclaimed=run['source_refs'][:2];target['media_kind']='image';unclaimed['media_kind']='image'
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps(run['source_refs']),A,value['extraction_id']))
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]['content']
    content['source_message_ids']=[target['id']]
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(json.dumps(content),A,value['id']))
    db.commit()
    before=db.execute('SELECT version,status FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]
    audit_before=db.execute('SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND entity_id=%s',(A,value['id']))[0]['n']
    reviews_before=db.execute('SELECT count(*) AS n FROM saas_knowledge_case_source_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid',(A,value['id']))[0]['n']

    ready_check=store.source_review_preflight(PROFILE,value['id'],target['id'],1)
    assert ready_check=={'build_marker':'source-review-preflight-v1','candidate_id':value['id'],'extraction_id':value['extraction_id'],'candidate_version':1,
      'expected_version':1,'message_id':target['id'],'in_case_evidence':True,'in_extraction_media':True,
      'version_matches':True,'case_reviewable':True,'status':'eligible','reason':'membership_and_version_match'}
    blocked=store.source_review_preflight(PROFILE,value['id'],unclaimed['id'],1)
    assert blocked['status']=='blocked' and blocked['reason']=='not_case_evidence' and blocked['in_extraction_media'] is True
    stale=store.source_review_preflight(PROFILE,value['id'],target['id'],2)
    assert stale['reason']=='stale_version' and stale['candidate_version']==1
    with pytest.raises(TenantLinearError):
        store.source_review_preflight({'tenant_id':B,'telegram_id':22,'role':'owner'},value['id'],target['id'],1)
    from types import SimpleNamespace
    monkeypatch.setattr(store,'authorize',lambda *args:(A,11,SimpleNamespace(profile={'role':'admin'})))
    with pytest.raises(TenantLinearError,match='Только владелец'):
        store.source_review_preflight(PROFILE,value['id'],target['id'],1)

    after=db.execute('SELECT version,status FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]
    assert after==before
    assert db.execute('SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND entity_id=%s',(A,value['id']))[0]['n']==audit_before
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_case_source_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid',(A,value['id']))[0]['n']==reviews_before
    assert generation.call_count==1


@pytest.mark.parametrize('has_current_receipt',[True,False])
def test_owner_reviews_changed_media_as_new_version_without_rewriting_extraction_or_carrying_claims(ready,has_current_receipt):
    import tenant_media_evidence as media_evidence
    db,generation,_=ready;value=candidate(ready)
    run=db.execute('SELECT * FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]
    old_refs=run['source_refs'];prior=old_refs[0]
    old_facts={key:None for key in store._MEDIA_IDENTITY_FIELDS};old_facts.update({'mediaId':'old-media','fileLength':10})
    new_facts={**old_facts,'mediaId':'new-media','fileLength':15}
    new_external='review-'+str(uuid.uuid4())
    prior.update(media_kind='image',media_version=store.digest(old_facts))
    current_ref={**prior,'external_id':new_external,
      'media_kind':'image','media_version':media_evidence.media_version(new_facts)}
    source_digest=media_evidence.source_hash(current_ref)
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb,media_results=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps(old_refs),json.dumps([{'id':prior['id'],'status':'processed','code':'vision_observation','observation':'OLD OBSERVATION'}]),A,value['extraction_id']))
    db.execute('UPDATE saas_crm_messages SET message_type=\'image\',raw=%s::jsonb,external_id=%s WHERE id=%s::uuid',(json.dumps(new_facts),new_external,prior['id']))
    db.execute('''INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,result)
      VALUES(%s::uuid,%s::uuid,%s,%s,'image','incoming','processed','vision_observation',%s::jsonb)''',
      (A,CA,current_ref['external_id'],media_evidence.source_hash(prior),json.dumps({'observation':'OLD RECEIPT OBSERVATION'})))
    if has_current_receipt:
        db.execute('''INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,result)
          VALUES(%s::uuid,%s::uuid,%s,%s,'image','incoming','processed','vision_observation',%s::jsonb)''',
          (A,CA,current_ref['external_id'],source_digest,json.dumps({'observation':'CURRENT OBSERVATION'})))
    db.commit()
    with pytest.raises(TenantLinearError,match='изменилась'):
        store.mutate(PROFILE,value['id'],{'expected_version':0,'message_id':prior['id'],'review_note':'Reviewed owner@example.com as a separate source.','acknowledge_new_source':True},'source_review')
    reviewed=store.mutate(PROFILE,value['id'],{'expected_version':1,'message_id':prior['id'],'review_note':'Reviewed owner@example.com as a separate source.','acknowledge_new_source':True},'source_review')
    card=reviewed['candidate']
    assert card['version']==2 and card['status']=='needs_clarification' and card['media_source_review']['revision']==1
    assert card['media_source_review']['receipt_reused'] is has_current_receipt and card['can_approve'] is False
    assert card['title']=='Review the current support request' and card['symptoms']==['Review the current customer request before saving']
    assert card['cause']=={'status':'unknown','text':''} and card['outcome']['status']=='unconfirmed'
    assert card['resolution_steps']==[] and card['operator_actions']==[]
    evidence_text=next(item['text'] for item in reviewed['evidence'] if item['id']==prior['id'])
    assert ('CURRENT OBSERVATION' in evidence_text) is has_current_receipt
    assert 'OLD OBSERVATION' not in json.dumps(reviewed)
    assert 'OLD RECEIPT OBSERVATION' not in json.dumps(reviewed)
    saved_refs=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]['source_refs']
    assert saved_refs==run['source_refs'] and saved_refs[0]['media_version']==store.digest(old_facts)
    revision=db.execute('SELECT * FROM saas_knowledge_case_source_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid',(A,value['id']))[0]
    assert revision['claims_snapshot']['outcome']==value['outcome']
    assert revision['reviewed_ref']['media_version']==media_evidence.media_version(new_facts)
    assert '@' not in revision['review_note'] and '[EMAIL]' in revision['review_note']
    assert db.execute("SELECT action FROM saas_tenant_audit_events WHERE entity_id=%s ORDER BY created_at DESC LIMIT 1",(value['id'],))[0]['action']=='knowledge_candidate_source_reviewed'
    assert generation.call_count==1
    with pytest.raises(TenantLinearError,match='Карточка изменилась'):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'message_id':prior['id'],'review_note':'Reviewed owner@example.com as a separate source.','acknowledge_new_source':True},'source_review')
    with pytest.raises(TenantLinearError,match='confirmation_required'):
        store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[EXPERT]},'approve')
    changed_raw={**new_facts,'mediaId':'changed-again'}
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',(json.dumps(changed_raw),prior['id']));db.commit()
    with pytest.raises(TenantLinearError,match='review it again'):
        store.mutate(PROFILE,value['id'],{'expected_version':2,'title':'New reviewed title'},'edit')


@pytest.mark.parametrize('sibling_claimed',[False,True])
@pytest.mark.parametrize(('confirmation_text','should_publish'),[
    ('Duzelib chox sagolun. Bu nece olmushdu ki? Neyi sehv yazmishiq bele gosterirdi',True),
    ('Duzelib chox sagolun, internet duzelib.',True),
    ('Duzelib chox sagolun? Bu nece olmushdu ki? Neyi sehv yazmishiq bele gosterirdi',False),
    ('Duzelib chox sagolun. Amma problem həll edilməyib?',False),
    ('Duzelib chox sagolun. Əgər internet düzəlibsə?',False),
    ('O yazdı: "Duzelib chox sagolun"',False),
    ('Kaş Duzelib chox sagolun.',False),
    ('Duzelib amma problem qalıb.',False),
    ('Duzelib amma problem qalacaq.',False),
    ('Duzelib amma problem yenə də qalacaq.',False),
    ('Duzelib amma problem gələcəkdə həll olunmayacaq.',False),
    ('Duzelib amma problem həll edilməyib.',False),
    ('Duzelib amma problem duzelmedi.',False),
    ('Duzelib amma problem duzelmir.',False),
    ('Duzelib amma problem duzelmeyecek.',False),
    ('Duzelib amma problem həll edilmədi.',False),
    ('Duzelib amma problem qayıtdı.',False),
    ('Duzelib amma problem yenidən başladı.',False),
    ('Duzelib amma problem yenidən qayıtdı.',False),
    ('Duzelib amma problem geri qayıtdı.',False),
    ('Duzelib amma problem təkrarlanıb.',False),
    ('Duzelib amma problem yenə baş verir.',False),
    ('Duzelib amma problem yenə yaranır.',False),
    ('Duzelib amma problem yenə ortaya çıxır.',False),
    ('Duzelib amma problem yenidən baş verdi.',False),
    ('Duzelib amma problem təkrar baş verib.',False),
    ('Duzelib amma problem yenidən yarandı.',False),
    ('Duzelib amma problem yenidən ortaya çıxdı.',False),
    ('Duzelib amma problem yenə gələcək.',False),
    ('Duzelib amma problem yenidən çıxa bilər.',False),
    ('Duzelib amma problem təkrarlana bilər.',False),
    ('Duzelib amma problem təkrar edə bilər.',False),
    ('Duzelib amma problem yenə qayıda bilər.',False),
    ('Duzelib amma problem yeniden cixa biler.',False),
    ('Duzelib amma problem tekrar ede biler.',False),
    ('Duzelib amma problem gələcək.',False),
    ('Duzelib amma problem çıxacaq.',False),
    ('Duzelib amma problem cixacaq.',False),
    ('Duzelib amma problem çıxa bilər.',False),
    ('Duzelib amma problem cixa biler.',False),
    ('Duzelib amma problem qayıdacaq.',False),
    ('Duzelib amma problem qayidacaq.',False),
    ('Duzelib amma problem yenidən ortaya çıxacaq.',False),
    ('Duzelib amma problem yeniden ortaya cixacaq.',False),
    ('Duzelib amma problem yenidən baş verəcək.',False),
    ('Duzelib amma problem təkrar baş verəcək.',False),
    ('Duzelib amma problem yenidən yaranacaq.',False),
    ('Duzelib amma problem təkrar qayıdacaq.',False),
    ('Duzelib amma problem təkrar çıxacaq.',False),
    ('Duzelib amma problem bir də qayıdacaq.',False),
    ('Duzelib amma problem yenidən gələcəkdə qalacaq.',False),
    ('Duzelib amma problem bir daha qayıdacaq.',False),
    ('Duzelib amma problem bir daha baş verəcək.',False),
    ('Duzelib amma problem təkrar geri qayıdacaq.',False),
    ('Duzelib amma problem yenidən geri qayıdacaq.',False),
    ('Duzelib amma problem qayıdır.',False),
    ('Duzelib amma problem yenidən qayıdır.',False),
    ('Duzelib amma problem geri qayıdır.',False),
    ('Duzelib amma problem yenə başladı.',False),
    ('Duzelib amma problem yenə başlayıb.',False),
    ('Duzelib amma problem təkrar başladı.',False),
    ('Duzelib amma problem yenə var.',False),
    ('Duzelib ola biler.',False),
])
def test_recovered_candidate_scopes_stale_media_and_requires_unambiguous_customer_confirmation(
        ready,sibling_claimed,confirmation_text,should_publish):
    import tenant_media_evidence as media_evidence
    db,generation,_=ready
    customer_confirmation=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,41,'customer-confirmation',%s,'incoming','Client',%s,'text',now()+(20 * interval '1 second'))",
      (A,customer_confirmation,'connect:'+CA,confirmation_text));db.commit()
    value=candidate(ready)
    run=db.execute('SELECT * FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]
    refs=run['source_refs'];assert len(refs)>=2
    target,sibling=refs[:2]
    confirmation=next(ref for ref in refs if ref['id']==customer_confirmation)
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]['content']
    source_ids=[target['id'],sibling['id'],confirmation['id']] if sibling_claimed else [target['id'],confirmation['id']]
    content.update(source_message_ids=source_ids,start_message_id=target['id'],end_message_id=confirmation['id'])
    old_facts={key:None for key in store._MEDIA_IDENTITY_FIELDS};old_facts.update({'mediaId':'old','fileLength':10})
    current_facts={**old_facts,'mediaId':'current','fileLength':15}
    current_ref=None
    for ref in (target,sibling):
        ref.update(media_kind='image',media_version=store.digest(old_facts))
        external_id='current-'+str(uuid.uuid4())
        db.execute("UPDATE saas_crm_messages SET message_type='image',raw=%s::jsonb,external_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid",
          (json.dumps(current_facts),external_id,A,ref['id']))
        if ref is target:
            current_ref={**ref,'external_id':external_id,'media_kind':'image','media_version':media_evidence.media_version(current_facts)}
    db.execute('UPDATE saas_knowledge_extractions SET source_refs=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps(refs),A,value['extraction_id']))
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps(content),A,value['id']))
    current_hash=media_evidence.source_hash(current_ref)
    db.execute('''INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,result)
      VALUES(%s::uuid,%s::uuid,%s,%s,'image','incoming','processed','vision_observation',%s::jsonb)''',
      (A,CA,current_ref['external_id'],current_hash,json.dumps({'observation':'CURRENT OBSERVATION'})))
    db.commit()

    reviewed=store.mutate(PROFILE,value['id'],{'expected_version':1,'message_id':target['id'],
      'review_note':'Reviewed the changed attachment as a separate source.','acknowledge_new_source':True},'source_review')['candidate']
    assert reviewed['version']==2 and reviewed['media_source_review']['message_id']==target['id']
    assert [item['id'] for item in reviewed.get('media_source_diagnostics',[])]==([sibling['id']] if sibling_claimed else [])
    assert reviewed['source_message_ids']==source_ids

    before=db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid',(A,))[0]['n']
    if sibling_claimed:
        with pytest.raises(TenantLinearError,match='Вложение исходного сообщения изменилось'):
            store.mutate(PROFILE,value['id'],{'expected_version':2,'title':'Reviewed current support request'},'edit')
        with pytest.raises(TenantLinearError,match='Вложение исходного сообщения изменилось'):
            store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[EXPERT]},'approve')
        unchanged=db.execute('SELECT version,content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]
        assert unchanged['version']==2 and unchanged['content']['title']=='Review the current support request'
    else:
        saved=store.mutate(PROFILE,value['id'],{'expected_version':2,'title':'Reviewed current support request',
          'symptoms':['Customer reported an attachment issue'],'resolution_steps':['Applied the reviewed fix'],
          'outcome':{'status':'confirmed','text':'Customer confirmed the fix works.','message_ids':[confirmation['id']]},
          'work_status':'resolved'},'edit')['candidate']
        assert saved['version']==3 and saved['title']=='Reviewed current support request'
        assert saved['cause']=={'status':'unknown','text':''}
        assert saved['outcome']['status']==('confirmed' if should_publish else 'unconfirmed')
        saved_refs=db.execute('SELECT source_refs FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]['source_refs']
        assert saved_refs==refs and saved_refs[0]['media_version']==store.digest(old_facts)
        if should_publish:
            published=store.mutate(PROFILE,value['id'],{'expected_version':3,'agent_ids':[EXPERT]},'approve')['candidate']
            assert published['status']=='approved' and [item['agent_id'] for item in published['publications']]==[EXPERT]
            provenance=db.execute("SELECT provenance FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND provenance->>'candidate_id'=%s",(A,value['id']))[0]['provenance']
            assert provenance['media_source_review']['revision']==1
        else:
            with pytest.raises(TenantLinearError,match='confirmation_required'):
                store.mutate(PROFILE,value['id'],{'expected_version':3,'agent_ids':[EXPERT]},'approve')
    after=db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid',(A,))[0]['n']
    assert after==before+(1 if should_publish and not sibling_claimed else 0)
    assert generation.call_count==1


def confirmed_case(ready):
    db,generation,data=ready;ticket_id=str(uuid.uuid4())
    db.execute("INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,status,completion_outcome,completion_note,created_by) VALUES(%s::uuid,%s::uuid,%s::uuid,'client','manual','resolved','resolved_confirmed','Customer confirmed the fix',11)",(A,ticket_id,CA));db.commit()
    return db,generation,{**data,'ticket_id':ticket_id}


def test_case_customer_and_company_are_read_from_tenant_native_crm(ready):
    db,_,_=ready;value=candidate(ready)
    company=str(uuid.UUID(int=9901));contact=str(uuid.UUID(int=9902))
    db.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name) VALUES(%s::uuid,%s::uuid,'Go party')",(company,A))
    db.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone,company_id) VALUES(%s::uuid,%s::uuid,'Nicat Xaliqov','994501234567',%s::uuid)",(contact,A,company))
    db.execute("INSERT INTO saas_crm_native_contact_conversations(tenant_id,provider,channel_id,conversation_id,contact_id) VALUES(%s::uuid,'connect',%s::uuid,'client',%s::uuid)",(A,CA,contact))
    db.commit()
    listed=store.listing(PROFILE,{'limit':25})['candidates'][0]
    opened=store.detail(PROFILE,value['id'])['candidate']
    assert (listed['customer_name'],listed['customer_phone'],listed['company_name'])==('Nicat Xaliqov','+994501234567','Go party')
    assert (opened['customer_name'],opened['customer_phone'],opened['company_name'])==(listed['customer_name'],listed['customer_phone'],listed['company_name'])
    # A second tenant may store the same external conversation key; it cannot override tenant A's contact.
    foreign=str(uuid.UUID(int=9903))
    db.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone) VALUES(%s::uuid,%s::uuid,'Foreign','994999999999')",(foreign,B))
    db.execute("INSERT INTO saas_crm_native_contact_conversations(tenant_id,provider,channel_id,conversation_id,contact_id) VALUES(%s::uuid,'connect',%s::uuid,'client',%s::uuid)",(B,CB,foreign));db.commit()
    assert store.listing(PROFILE,{'limit':25})['candidates'][0]['customer_name']=='Nicat Xaliqov'


def test_case_customer_without_crm_link_uses_verified_connect_phone(ready):
    db,_,_=ready;value=candidate(ready)
    db.execute("UPDATE saas_connect_conversations SET phone='+994 50 123 4567' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'",(A,CA));db.commit()
    listed=store.listing(PROFILE,{'limit':25})['candidates'][0]
    opened=store.detail(PROFILE,value['id'])['candidate']
    assert listed['customer_name']=='Client' and listed['customer_phone']=='+994501234567' and listed['company_name']==''
    assert (opened['customer_name'],opened['customer_phone'])==(listed['customer_name'],listed['customer_phone'])


def test_case_customer_without_phone_or_company_reports_missing_values(ready):
    db,_,_=ready;value=candidate(ready);contact=str(uuid.UUID(int=9904))
    db.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone) VALUES(%s::uuid,%s::uuid,'Aysel Məmmədova','')",(contact,A))
    db.execute("INSERT INTO saas_crm_native_contact_conversations(tenant_id,provider,channel_id,conversation_id,contact_id) VALUES(%s::uuid,'connect',%s::uuid,'client',%s::uuid)",(A,CA,contact))
    db.execute("UPDATE saas_connect_conversations SET phone='',contact_name='',display_name='' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'",(A,CA));db.commit()
    listed=store.listing(PROFILE,{'limit':25})['candidates'][0]
    opened=store.detail(PROFILE,value['id'])['candidate']
    assert listed['customer_name']=='Aysel Məmmədova' and listed['customer_phone']=='' and listed['company_name']==''
    assert (opened['customer_name'],opened['customer_phone'],opened['company_name'])==(listed['customer_name'],listed['customer_phone'],listed['company_name'])


def test_case_conversation_link_resolves_only_bound_tenant_evidence(ready):
    db,_,_=ready;value=candidate(ready);anchor=value['source_message_ids'][0]
    link=store.conversation_link(PROFILE,value['id'],anchor)
    assert link['case_id']==value['id'] and link['message_id']==anchor
    assert link['route']=={'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client'}
    direct=store.conversation_link(PROFILE,value['id'])
    assert direct['case_id']==value['id'] and direct['message_id'] in value['source_message_ids']
    with pytest.raises(TenantLinearError):
        store.conversation_link({'tenant_id':B,'telegram_id':22},value['id'],anchor)
    with pytest.raises(TenantLinearError):
        store.conversation_link(PROFILE,value['id'],str(uuid.uuid4()))
    db.execute("UPDATE saas_knowledge_candidates SET status='archived' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,value['id']));db.commit()
    with pytest.raises(TenantLinearError,match='archived'):
        store.conversation_link(PROFILE,value['id'],anchor)


def test_internal_forward_without_customer_evidence_cannot_become_support_case(ready):
    db,_,_=ready;value=candidate(ready)
    db.execute('''UPDATE saas_crm_messages SET direction='outgoing',raw='{}'::jsonb
      WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])''',(A,value['source_message_ids']))
    db.commit()
    opened=store.detail(PROFILE,value['id'])['candidate']
    assert opened['source_customer_proven'] is False
    assert opened['source_provenance']=='original_customer_unproven'
    assert opened['can_approve'] is False and opened['approval_block_reason']=='original_customer_unproven'
    with pytest.raises(TenantLinearError,match='original_customer_unproven'):
        store.mutate(PROFILE,value['id'],{'expected_version':opened['version'],'agent_ids':[EXPERT]},'approve')


def test_group_case_never_displays_group_jid_or_arbitrarily_picks_sender(ready):
    db,_,_=ready;value=candidate(ready);group='120363000000000@g.us'
    db.execute("UPDATE saas_knowledge_extractions SET route=jsonb_set(route,'{conversation_id}',to_jsonb(%s::text)) WHERE tenant_id=%s::uuid AND id=%s::uuid",(group,A,value['extraction_id']))
    db.execute("UPDATE saas_connect_conversations SET conversation_id=%s,conversation_kind='group',phone='',display_name='Printer Support' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND lead_id=41",(group,A,CA))
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND external_id='m1'",(json.dumps({'participantJid':'994501111111@s.whatsapp.net','participantName':'Aysel'}),A))
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND external_id='response-1'",(json.dumps({'participantJid':'994502222222@s.whatsapp.net','participantName':'Murad'}),A));db.commit()
    result=store.listing(PROFILE,{'limit':25})['candidates'][0]
    assert result['customer_phone']=='' and result['customer_name']=='' and result['company_name']==''
    assert result['conversation_name']=='Printer Support'
    assert '@' not in result['customer_name']
    assert store.detail(PROFILE,value['id'])['candidate']['conversation_name']=='Printer Support'
    # A shared group JID can exist on multiple tenant channels. Each case uses
    # its own persisted channel title; opening a case never calls Evolution.
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')",(A,CB))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,display_name,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,420,'Other group title','group')",(A,CB,group));db.commit()
    assert store.listing(PROFILE,{'limit':25})['candidates'][0]['conversation_name']=='Printer Support'
    db.execute("UPDATE saas_connect_conversations SET display_name='Renamed support group' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s",(A,CA,group));db.commit()
    assert store.listing(PROFILE,{'limit':25})['candidates'][0]['conversation_name']=='Renamed support group'
    db.execute("UPDATE saas_connect_conversations SET manual_display_name=%s WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s",(group,A,CA,group));db.commit()
    assert store.detail(PROFILE,value['id'])['candidate']['conversation_name']=='Renamed support group'


def test_boundary_adjustment_uses_exact_dialog_and_returns_human_summary(ready):
    db,_,_=ready;value=candidate(ready);earlier=str(uuid.UUID(int=9910))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,'older-boundary',%s,'incoming','Earlier customer context',now()-interval '1 hour')",(earlier,A,'connect:'+CA));db.commit()
    result=store.mutate(PROFILE,value['id'],{'expected_version':1,'start_message_id':earlier,'end_message_id':value['end_message_id']},'boundaries')['candidate']
    assert result['version']==2 and result['start_message_id']==earlier
    assert result['source_message_ids']==value['source_message_ids']
    summary=result['boundary_summary']
    assert summary['available'] and summary['start']['text']=='Earlier customer context' and summary['message_count']>=len(value['source_message_ids'])+1
    clarified=store.mutate(PROFILE,value['id'],{'expected_version':2,'clarification_note':'Need additional context'},'clarify')['candidate']
    assert clarified['version']==3 and clarified['start_message_id']==earlier


def test_boundary_adjustment_cannot_drop_outcome_evidence_or_reverse_range(ready):
    value=candidate(ready)
    with pytest.raises(TenantLinearError,match='подтверждение.*результата'):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'start_message_id':value['start_message_id'],'end_message_id':value['start_message_id']},'boundaries')
    with pytest.raises(TenantLinearError,match='предшествовать'):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'start_message_id':value['end_message_id'],'end_message_id':value['start_message_id']},'boundaries')
    assert store.detail(PROFILE,value['id'])['candidate']['version']==1


def test_boundary_adjustment_rejects_foreign_source_and_employee(ready):
    db,_,_=ready;value=candidate(ready)
    foreign=str(uuid.UUID(int=9911))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,42,'foreign-boundary','connect:'||%s,'incoming','Foreign')",(foreign,B,CB))
    db.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':1,'start_message_id':foreign,'end_message_id':value['end_message_id']},'boundaries')
    db.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    with pytest.raises(TenantLinearError,match='недоступно'):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'start_message_id':foreign,'end_message_id':value['end_message_id']},'boundaries')


def test_boundary_adjustment_requires_owner_or_admin(ready):
    db,_,_=ready;value=candidate(ready);earlier=str(uuid.UUID(int=9912))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,'role-boundary',%s,'incoming','Earlier',now()-interval '1 hour')",(earlier,A,'connect:'+CA))
    db.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    with pytest.raises(TenantLinearError):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'start_message_id':earlier,'end_message_id':value['end_message_id']},'boundaries')
    db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    db.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members','{\"11\":{\"ai_agents_manage\":true}}'::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,));db.commit()
    assert store.mutate(PROFILE,value['id'],{'expected_version':1,'start_message_id':earlier,'end_message_id':value['end_message_id']},'boundaries')['candidate']['version']==2


def test_published_case_boundaries_are_immutable(ready):
    ready=confirmed_case(ready)
    value=candidate(ready)
    approved=store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')['candidate']
    with pytest.raises(TenantLinearError,match='опубликованного кейса'):
        store.mutate(PROFILE,value['id'],{'expected_version':approved['version'],'start_message_id':approved['start_message_id'],'end_message_id':approved['end_message_id']},'boundaries')

def test_private_pending_review_edit_approve_agent_scoped_idempotent(ready):
    ready=confirmed_case(ready)
    db,generation,data=ready
    before=db.execute('SELECT enabled,mode,scopes,version FROM saas_ai_agents ORDER BY id');db.commit()
    first=service.analyze(PROFILE,data);value=first['candidates'][0]
    assert value['status']=='pending_review' and value['outcome']['status']=='confirmed'
    listed=store.listing(PROFILE,data)['candidates'][0]
    assert listed['id']==value['id']
    assert listed['source_route']=={'provider':'connect','channel_id':CA,'conversation_id':'client'}
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources');db.commit()
    assert service.analyze(PROFILE,data)['request']['id']==first['request']['id']
    assert service.analyze(PROFILE,dict(reversed(list(data.items()))))['request']['id']==first['request']['id']
    assert service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4())})['request']['id']==first['request']['id']
    generation.assert_called_once()
    changed=store.mutate(PROFILE,value['id'],{'expected_version':1,'title':'Проверка синхронизации'})['candidate']
    assert changed['version']==2
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':1,'title':'Old'})
    result=store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[AGENT,EXPERT]},'approve')['candidate']
    assert result['status']=='approved' and len(result['publications'])==2
    again=store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[EXPERT,AGENT]},'approve')['candidate']
    assert again['publications']==result['publications']
    assert len(db.execute('SELECT * FROM saas_ai_knowledge_sources'))==2
    assert all(row['provenance']['candidate_id']==value['id'] for row in db.execute('SELECT provenance FROM saas_ai_knowledge_sources'))
    assert runs.knowledge_context(A,'синхронизации',agent_id=AGENT)
    assert not runs.knowledge_context(B,'синхронизации',agent_id=AGENT)
    assert db.execute('SELECT enabled,mode,scopes,version FROM saas_ai_agents ORDER BY id')==before

@pytest.mark.parametrize('confirmation',['Предлагаю перезапустить','AI считает проблему решённой','Не всё работает','not fixed'])
def test_unconfirmed_and_ai_only_never_publish(ready,confirmation):
    db,generation,data=ready
    db.execute("UPDATE saas_crm_messages SET body=%s WHERE external_id='response-1'",(confirmation,));db.commit()
    value=candidate(ready)
    assert value['outcome']['status']=='unconfirmed' and not value['can_approve']
    with pytest.raises(TenantLinearError,match='confirmation_required'):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources')

def test_ai_confirmation_does_not_prove_resolution(ready):
    db,generation,data=ready
    db.execute("UPDATE saas_crm_messages SET direction='outgoing',author_name='AI Agent',raw=%s::jsonb WHERE external_id='response-1'",(json.dumps({'run_id':str(uuid.uuid4())}),));db.commit()
    assert candidate(ready)['outcome']['status']=='unconfirmed'

def test_rejection_and_foreign_employee_permissions(ready):
    db,generation,data=ready;value=candidate(ready)
    foreign={'tenant_id':B,'telegram_id':22}
    for operation in (lambda:store.detail(foreign,value['id']),lambda:store.mutate(foreign,value['id'],{'expected_version':1},'reject'),lambda:service.analyze(PROFILE,{**data,'channel_id':CB})):
        with pytest.raises(TenantLinearError):operation()
    db.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):store.detail(PROFILE,value['id'])
    db.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid",(A,));db.commit()
    assert store.mutate(PROFILE,value['id'],{'expected_version':1},'reject')['candidate']['status']=='rejected'
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[AGENT]},'approve')

def test_personal_data_and_secrets_removed_before_provider(ready):
    db,generation,data=ready
    sensitive='Иван Иванов +994501234567 user@example.com\nпароль: test-secret-password\nhttps://private.local/?token=abcdef\nадрес: Баку дом 99'
    db.execute("UPDATE saas_crm_messages SET body=%s,author_name='Иван Иванов' WHERE external_id='m1' AND tenant_id=%s::uuid",(sensitive,A));db.commit()
    candidate(ready)
    prompt=json.dumps(generation.call_args.args[3],ensure_ascii=False)
    for value in ('Иван','994501234567','user@example.com','test-secret-password','private.local','Баку'):
        assert value not in prompt
    assert '[SECRET]' in prompt and '[EMAIL]' in prompt
    assert all(set(m)=={'ref','role','text'} for m in generation.call_args.args[3])

def test_invalid_foreign_evidence_is_rejected_but_usage_recorded(ready):
    db,generation,data=ready
    value=model_card();value['source_message_ids']=['m999']
    generation.return_value=(json.dumps({'cards':[value]}),{'total_tokens':17})
    response=service.analyze(PROFILE,data)
    assert not response['candidates'] and response['request']['error_code']=='invalid_model_result'
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==17

def test_model_failure_unknown_budget_retained_without_retries(ready):
    db,generation,data=ready;generation.side_effect=RuntimeError('SECRET provider diagnostic')
    first=service.analyze(PROFILE,data)
    assert first['request']['status']=='unknown' and not first['candidates']
    service.analyze(PROFILE,data);generation.assert_called_once()
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==policy.MAX_TOKENS
    assert 'SECRET' not in json.dumps(first)

@pytest.mark.parametrize('kind',['tokens','daily_calls','emergency','archived'])
def test_company_limits_and_stop_before_model(ready,monkeypatch,kind):
    db,generation,data=ready
    if kind=='tokens':monkeypatch.setattr(service,'platform_token_limit',lambda *a:100)
    elif kind=='daily_calls':
        db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata) VALUES(%s::uuid,'chat_ai','connected',%s::jsonb)",(A,json.dumps({'daily_limit':1})))
        db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test','completed')",(A,str(uuid.uuid4()),AGENT))
    elif kind=='emergency':db.execute('UPDATE saas_ai_agent_settings SET emergency_stop=TRUE')
    else:db.execute("UPDATE saas_tenants SET status='archived'")
    db.commit()
    with pytest.raises(TenantLinearError):service.analyze(PROFILE,data)
    generation.assert_not_called()

def test_revoked_access_during_model_discards_cards_but_charges_usage(ready):
    db,generation,data=ready
    def revoke(*a,**kw):
        db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
        return json.dumps({'cards':[model_card()]}),{'total_tokens':19}
    generation.side_effect=revoke
    with pytest.raises(TenantLinearError):service.analyze(PROFILE,data)
    assert not db.execute('SELECT id FROM saas_knowledge_candidates')
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==19

def test_atomic_publication_rollback_foreign_agent(ready):
    ready=confirmed_case(ready)
    db,generation,data=ready;value=candidate(ready)
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT,str(uuid.uuid4())]},'approve')
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources')
    assert store.detail(PROFILE,value['id'])['candidate']['status']=='pending_review'

def test_source_mutation_blocks_publish_and_cross_route_selection(ready):
    db,generation,data=ready;value=candidate(ready)
    with pytest.raises(TenantLinearError):service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4()),'message_ids':[str(uuid.uuid4())]})
    db.execute("UPDATE saas_crm_messages SET body='Changed' WHERE external_id='response-1'");db.commit()
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')

def test_unified_group_two_channels_dedup_multiple_problems(ready):
    db,generation,data=ready
    db.execute('DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'second')",(A,CB))
    for channel,lead in ((CA,1001),(CB,1002)):
        db.execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,%s)',(A,channel,GROUP,lead))
        for index,body in enumerate(['Проблема синхронизации','Проверьте соединение','Всё работает, помогло']):
            raw={'channelId':channel,'conversationId':GROUP,'externalMessageId':'EVENT_'+str(index),'participantJid':'994500000001@s.whatsapp.net','participantName':'Иван Иванов'}
            db.execute('INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,now()+(%s * interval \'1 second\'))',
                (A,str(uuid.uuid4()),lead,str(uuid.uuid4()),'connect:'+channel,'outgoing' if index==1 else 'incoming',body,'text',json.dumps(raw),index))
    db.commit()
    generation.return_value=(json.dumps({'cards':[model_card(),model_card(title='Ошибка кассы')]}),{'total_tokens':30})
    response=service.analyze(PROFILE,{**data,'conversation_id':GROUP,'history_mode':'unified_group'})
    assert len(response['candidates'])==2
    assert len(generation.call_args.args[3])==3
    assert set(response['request']['route']['source_channels'])=={CA,CB}
    assert all(len(c['source_message_ids'])==3 for c in response['candidates'])
    assert len(db.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,)))==6
    # Channel-only remains a separate exact route, never an implicit cross-channel merge.
    generation.reset_mock()
    response=service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4()),'conversation_id':GROUP})
    assert response['request']['route']['source_channels']==[CA]

def test_empty_result_is_valid_and_no_training_or_send_imports(ready):
    from pathlib import Path
    db,generation,data=ready;generation.return_value=('{"cards":[]}',{'total_tokens':7})
    assert service.analyze(PROFILE,data)['candidates']==[]
    source=Path(service.__file__).read_text(encoding='utf-8-sig')
    assert not any(word in source for word in ('send_ai_reply','requests.post','subprocess','fine_tun'))

def test_approval_publication_is_one_transaction(ready,monkeypatch):
    ready=confirmed_case(ready)
    db,generation,data=ready;value=candidate(ready);original=store.save_knowledge;count=[0]
    def save(*a,**kw):
        count[0]+=1
        if count[0]==2:raise RuntimeError('synthetic second-source failure')
        return original(*a,**kw)
    monkeypatch.setattr(store,'save_knowledge',save)
    with pytest.raises(RuntimeError):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT,EXPERT]},'approve')
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources')
    assert store.detail(PROFILE,value['id'])['candidate']['status']=='pending_review'

@pytest.mark.parametrize('value',['Проверьте, теперь работает?','Check if it works','Возможно, помогло','if fixed','unresolved','заработало бы'])
def test_hypotheses_and_questions_not_confirmation(value):
    assert not policy.positive_confirmation(value)

@pytest.mark.parametrize(('value','expected'),[
    ('Duzelib chox sagolun. Bu nece olmushdu ki? Neyi sehv yazmishiq bele gosterirdi',True),
    ('Duzelib chox sagolun',True),
    ('DÜZƏLİB, ÇOX SAĞ OLUN',True),
    ('Düzəlib, internet yenə işləyir',True),
    ('Düzəldi, çox sağ olun',True),
    ('duzelmeyib',False),
    ('düzəlməyib',False),
    ('duzelib?',False),
    ('Duzelib chox sagolun?',False),
    ('O yazdı: "Duzelib chox sagolun"',False),
    ('Duzelib chox sagolun. Əgər internet düzəlibsə?',False),
    ('Duzelib chox sagolun. Amma problem həll edilməyib?',False),
    ('Kaş Duzelib chox sagolun.',False),
    ('Duzelib amma problem qalir',False),
    ('Duzelib amma problem qalıb',False),
    ('Duzelib amma problem qalib',False),
    ('Düzəlib, amma problem hələ də qalır',False),
    ('Düzəlib, amma problem hələ də qalıb',False),
    ('Duzelib amma problem qalacaq',False),
    ('Düzəlib, amma problem gələcəkdə qalacaq',False),
    ('Düzəlib, amma problem davam edəcək',False),
    ('Duzelib, amma problem davam edecek',False),
    ('Duzelib amma problem yenə də qalacaq',False),
    ('Duzelib amma problem yenə var',False),
    ('Duzelib amma problem hələ qalır',False),
    ('Duzelib amma problem hele qalir',False),
    ('Duzelib amma problem yenə qalır',False),
    ('Düzəlib, amma problem gələcəkdə həll olunmayacaq',False),
    ('Düzəlib, amma problem həll edilməyib',False),
    ('Duzelib amma problem hell edilmeyib',False),
    ('Duzelib amma problem duzelmedi',False),
    ('Düzəlib, amma problem düzəlmədi',False),
    ('Duzelib amma problem duzelmir',False),
    ('Duzelib amma problem duzelmeyecek',False),
    ('Duzelib amma problem həll edilmədi',False),
    ('Duzelib amma problem hell edilmedi',False),
    ('Duzelib amma problem qayıtdı',False),
    ('Duzelib amma problem qayitdi',False),
    ('Duzelib amma problem qayıdır',False),
    ('Duzelib amma problem qayidir',False),
    ('Duzelib amma problem yenidən qayıdır',False),
    ('Duzelib amma problem yeniden qayidir',False),
    ('Duzelib amma problem geri qayidir',False),
    ('Duzelib amma problem yenə başladı',False),
    ('Duzelib amma problem yene basladi',False),
    ('Duzelib amma problem yenə başlayıb',False),
    ('Duzelib amma problem yene baslayib',False),
    ('Duzelib amma problem təkrar başladı',False),
    ('Duzelib amma problem tekrar baslayib',False),
    ('Duzelib amma problem yene bas verir',False),
    ('Duzelib amma problem yeniden yaranir',False),
    ('Duzelib amma problem yene ortaya cixir',False),
    ('Duzelib amma problem yeniden bas verib',False),
    ('Duzelib amma problem tekrar bas verib',False),
    ('Duzelib amma problem yeniden yaranib',False),
    ('Duzelib amma problem yeniden ortaya cixib',False),
    ('Duzelib amma problem yene gelecek',False),
    ('Duzelib amma problem yeniden cixa biler',False),
    ('Duzelib amma problem tekrarlana biler',False),
    ('Duzelib amma problem tekrar ede biler',False),
    ('Duzelib amma problem yene qayida biler',False),
    ('Duzelib amma problem gelecek',False),
    ('Duzelib amma problem cixacaq',False),
    ('Duzelib amma problem cixa biler',False),
    ('Duzelib amma problem qayidacaq',False),
    ('Duzelib amma problem yeniden ortaya cixacaq',False),
    ('Duzelib amma problem yeniden bas verecek',False),
    ('Duzelib amma problem tekrar bas verecek',False),
    ('Duzelib amma problem yeniden yaranacaq',False),
    ('Duzelib amma problem tekrar qayidacaq',False),
    ('Duzelib amma problem tekrar cixacaq',False),
    ('Duzelib amma problem birde qayidacaq',False),
    ('Duzelib amma problem yeniden gelecekde qalacaq',False),
    ('Duzelib amma problem birdaha qayidacaq',False),
    ('Duzelib amma problem birdaha bas verecek',False),
    ('Duzelib amma problem tekrar geri qayidacaq',False),
    ('Duzelib amma problem yeniden geri qayidacaq',False),
    ('Duzelib amma problem yenidən başladı',False),
    ('Duzelib amma problem yeniden basladi',False),
    ('Duzelib amma problem yeniden qayitdi',False),
    ('Duzelib amma problem geri qayitdi',False),
    ('Duzelib amma problem tekrarlandi',False),
    ('Duzelib, amma internet islemir',False),
    ('Duzelib, əgər internet geri qayıtsa',False),
    ('Duzelib ola biler',False),
    ('Düzəlib olsaydı, yaxşı olardı',False),
])
def test_azerbaijani_completed_fix_forms_preserve_negative_and_uncertain_meaning(value,expected):
    assert policy.positive_confirmation(value) is expected

def test_extraction_daily_cap_and_concurrency(ready):
    db,generation,data=ready
    item=service.prepare(PROFILE,data)
    db.execute("UPDATE saas_crm_messages SET body='Changed fragment' WHERE external_id='m1' AND tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError,match='завершения'):service.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4())})
    db.execute("UPDATE saas_knowledge_extractions SET status='unknown'");db.commit()
    # Unknown paid attempts still consume daily extraction capacity.
    for n in range(9):
        rid=str(uuid.uuid4());event='test-cap-'+str(n)
        db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider) VALUES(%s::uuid,%s,'connect')",(A,event))
        db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,1,'test','unknown')",(A,rid,AGENT,event))
        db.execute("INSERT INTO saas_knowledge_extractions(tenant_id,id,created_by,route,source_refs,source_digest,fingerprint,request_key,status,deadline) VALUES(%s::uuid,%s::uuid,11,'{}','[]','hash',%s,'test-request-key','unknown',now())",(A,rid,event))
    db.commit()
    with pytest.raises(TenantLinearError,match='10 в сутки'):service.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4())})
    generation.assert_not_called()

def test_live_queue_is_idempotent_and_channel_scoped(ready,monkeypatch):
    _enable_live_pilot(monkeypatch)
    db,generation,data=ready
    db.execute("UPDATE saas_ai_agent_settings SET support_case_extraction_enabled=TRUE,support_case_extraction_started_at='2026-10-20T12:00:00Z' WHERE tenant_id=%s::uuid",(A,));db.commit()
    monkeypatch.setattr(live,'_connect',lambda:db)
    live.create_schema(db)
    first=db.execute("SELECT id::text AS id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id='m1'",(A,))[0]['id']
    db.execute("UPDATE saas_crm_messages SET happened_at='2026-10-20T13:00:00Z' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,first))
    second=str(uuid.UUID(int=9902))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,'new-live',%s,'incoming','new', '2026-10-20T13:01:00Z')",(second,A,'connect:'+CA))
    db.commit()
    with db.cursor() as cur:
        live.enqueue(cur,A,CA,'client',41,first,actor_id=11)
        live.enqueue(cur,A,CA,'client',41,first,actor_id=11)
    db.commit()
    rows=db.execute('SELECT channel_id::text AS channel_id,generation,last_message_id::text AS last_message_id FROM saas_knowledge_live_queue WHERE tenant_id=%s::uuid',(A,))
    assert len(rows)==1 and rows[0]['generation']==1 and rows[0]['last_message_id']==first
    with db.cursor() as cur:
        live.enqueue(cur,A,CA,'client',41,second,actor_id=11)
        live.enqueue(cur,A,CB,'client',41,first,actor_id=11)
    db.commit()
    rows=db.execute('SELECT channel_id::text AS channel_id,generation,last_message_id::text AS last_message_id FROM saas_knowledge_live_queue WHERE tenant_id=%s::uuid ORDER BY channel_id',(A,))
    assert len(rows)==1 and rows[0]['channel_id']==CA
    assert rows[0]['generation']==2 and rows[0]['last_message_id']==second
    assert generation.call_count==0


def test_tenant_channel_scope_never_reuses_legacy_pilot_as_all_channels(ready,monkeypatch):
    # New settings work with no legacy scope variables, but only the explicitly
    # selected Connect channel is admitted.  A pre-existing boolean setting is
    # therefore not silently widened during the migration.
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','true')
    for name in live.SCOPE_ENV.values():
        monkeypatch.delenv(name,raising=False)
    db,generation,_=ready
    db.execute("""UPDATE saas_ai_agent_settings
      SET support_case_extraction_enabled=TRUE,support_case_extraction_mode='automatic',
          support_case_extraction_channels=%s::jsonb,support_case_extraction_started_at='2026-10-20T12:00:00Z'
      WHERE tenant_id=%s::uuid""",(json.dumps([CA]),A))
    first=db.execute("SELECT id::text AS id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id='m1'",(A,))[0]['id']
    db.execute("UPDATE saas_crm_messages SET happened_at='2026-10-20T13:00:00Z' WHERE id=%s::uuid",(first,))
    foreign=str(uuid.UUID(int=9911))
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at)
      VALUES(%s::uuid,%s::uuid,41,'other-channel',%s,'incoming','new','2026-10-20T13:00:00Z')""",(foreign,A,'connect:'+CB))
    db.commit();monkeypatch.setattr(live,'_connect',lambda:db);live.create_schema(db)
    with db.cursor() as cur:
        live.enqueue(cur,A,CA,'client',41,first,actor_id=11)
        live.enqueue(cur,A,CB,'other',41,foreign,actor_id=11)
    db.commit()
    rows=db.execute("SELECT channel_id::text AS channel_id FROM saas_knowledge_live_queue WHERE tenant_id=%s::uuid",(A,))
    assert [row['channel_id'] for row in rows]==[CA]
    assert generation.call_count==0


def _enable_live_pilot(monkeypatch):
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','true')
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_TENANT_ID',A)
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_CHANNEL_ID',CA)
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_CONVERSATION_ID','client')
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_START_AFTER','2026-10-20T12:00:00Z')


def test_live_queue_obeys_company_ai_emergency_stop(ready,monkeypatch):
    _enable_live_pilot(monkeypatch)
    db,generation,data=ready
    db.execute("UPDATE saas_ai_agent_settings SET support_case_extraction_enabled=TRUE,support_case_extraction_started_at='2026-10-20T12:00:00Z' WHERE tenant_id=%s::uuid",(A,));db.commit()
    monkeypatch.setattr(live,'_connect',lambda:db)
    live.create_schema(db)
    message=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,'pilot-message',%s,'incoming','new','2026-10-20T13:00:00Z')",(message,A,'connect:'+CA));db.commit()
    with db.cursor() as cur:live.enqueue(cur,A,CA,'client',41,message,actor_id=11)
    db.execute("UPDATE saas_knowledge_live_queue SET next_attempt_at=now()-interval '1 second' WHERE tenant_id=%s::uuid",(A,))
    db.execute("UPDATE saas_ai_agent_settings SET emergency_stop=TRUE WHERE tenant_id=%s::uuid",(A,));db.commit()
    monkeypatch.setattr(live,'_connect',lambda:db)
    assert live.claim() is None
    db.execute("UPDATE saas_ai_agent_settings SET emergency_stop=FALSE WHERE tenant_id=%s::uuid",(A,));db.commit()
    claimed=live.claim()
    assert claimed and claimed['state']=='pending' and claimed['reviewer_id']==11
    assert db.execute("SELECT state FROM saas_knowledge_live_queue WHERE tenant_id=%s::uuid",(A,))[0]['state']=='running'
    assert generation.call_count==0

def test_deactivate_published_source_stops_agent_retrieval(ready):
    ready=confirmed_case(ready)
    db,generation,data=ready;value=candidate(ready)
    result=store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')['candidate']
    assert runs.knowledge_context(A,'Симптомы',agent_id=AGENT)
    agents.save_knowledge(PROFILE,{'active':False},source_id=result['publications'][0]['source_id'])
    assert not runs.knowledge_context(A,'Симптомы',agent_id=AGENT)

def test_candidate_rejected_after_publication_is_excluded_from_retrieval(ready):
    db,generation,data=ready;value=candidate(ready)
    source_id=str(uuid.uuid4())
    db.execute("UPDATE saas_knowledge_candidates SET status='approved',reviewed_by=11,reviewed_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,value['id']))
    provenance=json.dumps({'candidate_id':value['id'],'approved_by':'11','review_status':'approved'})
    db.execute("INSERT INTO saas_ai_knowledge_sources(id,tenant_id,agent_id,title,kind,content,active,content_hash,created_by,provenance) VALUES(%s::uuid,%s::uuid,%s::uuid,'Published case','text','Symptoms and verified resolution',TRUE,'case-hash',11,%s::jsonb)",(source_id,A,AGENT,provenance))
    db.execute("INSERT INTO saas_knowledge_candidate_sources(tenant_id,candidate_id,agent_id,source_id) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid)",(A,value['id'],AGENT,source_id))
    db.execute("INSERT INTO saas_ai_knowledge_chunks(tenant_id,source_id,chunk_no,content) VALUES(%s::uuid,%s::uuid,0,'Symptoms and verified resolution')",(A,source_id));db.commit()
    assert runs.knowledge_context(A,'Symptoms',agent_id=AGENT)
    db.execute("UPDATE saas_knowledge_candidates SET status='rejected',reviewed_by=11,reviewed_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,value['id']));db.commit()
    assert not runs.knowledge_context(A,'Симптомы',agent_id=AGENT)

def test_approved_case_without_publication_binding_is_excluded_from_retrieval(ready):
    db,generation,data=ready;value=candidate(ready);source_id=str(uuid.uuid4())
    db.execute("UPDATE saas_knowledge_candidates SET status='approved',reviewed_by=11,reviewed_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,value['id']))
    provenance=json.dumps({'candidate_id':value['id'],'approved_by':'11','review_status':'approved'})
    db.execute("INSERT INTO saas_ai_knowledge_sources(id,tenant_id,agent_id,title,kind,content,active,content_hash,created_by,provenance) VALUES(%s::uuid,%s::uuid,%s::uuid,'Unpublished case','text','Symptoms and unapproved resolution',TRUE,'case-unpublished',11,%s::jsonb)",(source_id,A,AGENT,provenance))
    db.execute("INSERT INTO saas_ai_knowledge_chunks(tenant_id,source_id,chunk_no,content) VALUES(%s::uuid,%s::uuid,0,'Symptoms and unapproved resolution')",(A,source_id));db.commit()
    assert not runs.knowledge_context(A,'Symptoms',agent_id=AGENT)

def test_http_origin_identity_scope_and_no_store(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import tenant_knowledge_candidates_api as api
    async def scenario():
        operation=Mock(return_value={'request':{'status':'completed'},'candidates':[]})
        monkeypatch.setattr(api,'analyze',operation)
        payload={'expected_tenant_id':A,'expected_user_id':11,'request_id':str(uuid.uuid4()),'channel_id':CA,'conversation_id':'client'}
        async def body():return payload
        request=SimpleNamespace(method='POST',content_type='application/json',headers={'Origin':'https://test.local'},json=body,match_info={})
        handle=api.handler(lambda _:PROFILE,'https://test.local',Mock())
        result=await handle(request)
        assert result.status==200 and result.headers['Cache-Control']=='no-store'
        assert operation.call_args.args[1].get('expected_tenant_id') is None
        request.headers={'Origin':'https://evil.local'}
        assert (await handle(request)).status==403
        request.headers={'Origin':'https://test.local'};payload['expected_tenant_id']=B
        assert (await handle(request)).status==409
        assert operation.call_count==1
        request.method='GET';request.query={'expected_tenant_id':A,'expected_user_id':'11'}
        read=Mock(return_value={'candidates':[]});monkeypatch.setattr(api,'listing',read)
        assert (await handle(request)).status==200
        assert read.call_count==1
    asyncio.run(scenario())

def test_model_cannot_certify_its_own_cause(ready):
    db,generation,data=ready
    value=model_card();value['cause']['status']='confirmed'
    generation.return_value=(json.dumps({'cards':[value]}),{'total_tokens':5})
    result=candidate(ready)
    assert result['cause']['status']=='suspected'
    edited=store.mutate(PROFILE,result['id'],{'expected_version':1,'cause':{'status':'confirmed','text':'Сотрудник проверил причину по исходным сообщениям'}})['candidate']
    assert edited['cause']['status']=='suspected'


def test_confirmed_cause_requires_exact_human_source_evidence_and_rechecks_source(ready):
    ready=confirmed_case(ready)
    db,generation,data=ready;value=candidate(ready)
    evidence={'message_id':value['source_message_ids'][1],'quote':'Проверьте соединение и повторите синхронизацию'}
    edited=store.mutate(PROFILE,value['id'],{'expected_version':1,
        'cause':{'status':'confirmed','text':'Сотрудник установил причину по переписке'},'cause_evidence':[evidence]})['candidate']
    assert edited['cause']['status']=='confirmed' and edited['cause_evidence']==[evidence] and edited['can_approve']
    with pytest.raises(TenantLinearError):
        store.mutate(PROFILE,value['id'],{'expected_version':2,'cause_evidence':[{'message_id':str(uuid.uuid4()),'quote':'чужой источник'}]})
    with pytest.raises(TenantLinearError):
        store.mutate(PROFILE,value['id'],{'expected_version':2,'cause_evidence':[{'message_id':value['source_message_ids'][1],'quote':'чужая цитата'}]})
    db.execute("UPDATE saas_crm_messages SET body='Изменённый источник' WHERE external_id='response-0'");db.commit()
    with pytest.raises(TenantLinearError,match='Исходное сообщение изменилось'):
        store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[AGENT]},'approve')

def test_group_ai_authored_flag_never_human_confirmation(ready):
    db,generation,data=ready
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE external_id='response-1'",(json.dumps({'ai_authored':True}),));db.commit()
    assert candidate(ready)['outcome']['status']=='unconfirmed'

def test_expired_request_reports_unknown_without_replay(ready):
    db,generation,data=ready
    item=service.prepare(PROFILE,data)
    db.execute("UPDATE saas_knowledge_extractions SET deadline=now()-interval '1 second'");db.commit()
    response=store.request_detail(PROFILE,item['id'])
    assert response['request']['status']=='unknown' and response['request']['error_code']=='provider_timeout'
    assert service.analyze(PROFILE,data)['request']['id']==item['id']
    generation.assert_not_called()

def test_previewed_message_bounds_and_same_request_other_fragment(ready):
    db,generation,data=ready
    result=service.analyze(PROFILE,data)
    assert result['request']['message_count']==3
    assert len(result['request']['source_message_ids'])==3
    assert result['request']['limits']['max_messages']==80
    with pytest.raises(TenantLinearError):service.analyze(PROFILE,{**data,'message_ids':[result['request']['source_message_ids'][0]]})
    generation.assert_called_once()


def test_same_request_replay_keeps_original_window_after_new_messages(ready):
    db,generation,data=ready
    first=service.analyze(PROFILE,data)
    db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,41,'new-message',%s,'incoming','Новый вопрос')",(A,str(uuid.uuid4()),'connect:'+CA));db.commit()
    replay=service.analyze(PROFILE,data)
    assert replay['request']['id']==first['request']['id']
    assert replay['request']['source_message_ids']==first['request']['source_message_ids']
    generation.assert_called_once()


@pytest.mark.parametrize('steps',[[],['Проверить соединение']])
def test_unresolved_problem_retained_for_clarification_without_publication(ready,steps):
    db,generation,data=ready
    value=model_card(confirmed=False)
    value['resolution_steps']=steps
    value['outcome']={'status':'unknown','text':'Результат неизвестен','message_ids':[]}
    generation.return_value=(json.dumps({'cards':[value]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    first=service.analyze(PROFILE,data)
    result=first['candidates'][0]
    assert first['request']['status']=='completed'
    assert result['status']=='needs_clarification'
    assert result['resolution_steps']==steps and not result['can_approve']
    assert service.analyze(PROFILE,data)['candidates'][0]['id']==result['id']
    generation.assert_called_once()
    with pytest.raises(TenantLinearError,match='confirmation_required'):
        store.mutate(PROFILE,result['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources')


def test_confirmed_card_without_solution_rejected():
    value=model_card();value['resolution_steps']=[]
    ids=[str(uuid.UUID(int=i)) for i in (1,2,3)]
    value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    with pytest.raises(TenantLinearError):policy.card(value,ids)


def test_empty_model_result_completes_without_persistence(ready):
    db,generation,data=ready
    generation.return_value=('{'+'"cards":[]}',{'input_tokens':399,'output_tokens':5,'total_tokens':404})
    result=service.analyze(PROFILE,data)
    assert result['request']['status']=='completed' and result['candidates']==[]
    assert service.analyze(PROFILE,data)['candidates']==[]
    generation.assert_called_once()

def test_knowledge_extraction_defaults_to_az_and_preserves_explicit_languages():
    assert policy.language(None,{})=='az'
    assert policy.language(None,{'language':'en','language_code':'ru'})=='az'
    assert policy.language('en',{})=='az'
    assert policy.language('ru',{'language':'az'})=='az'
    assert policy.language('az',{})=='az'


def test_ai_generation_language_is_always_azerbaijani_even_for_ru_en_interface():
    assert [policy.language(value,{}) for value in ('ru','az','en',None)]==['az','az','az','az']
    assert 'Azerbaijani (AZ)' in service.PROMPT


def test_work_status_is_separate_from_confirmation_and_gates_publication():
    value=model_card(confirmed=True);ids=[str(uuid.UUID(int=i)) for i in (1,2,3)]
    value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    value['work_status']='unresolved'
    normalized=policy.card(value,ids)
    assert normalized['outcome']['status']=='confirmed'
    assert policy.approval_reason(normalized)=='work_not_resolved'
    value['work_status']='unknown'
    assert policy.approval_reason(policy.card(value,ids))=='work_status_required'
    value['work_status']='resolved'
    assert policy.approval_reason(policy.card(value,ids)) is None


def test_card_content_language_is_persistable_and_validated():
    value=model_card();ids=[str(uuid.uuid4()) for _ in value['source_message_ids']];value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    assert policy.card(value,ids)['content_language']=='az'
    assert policy.card({**value,'content_language':'en'},ids)['content_language']=='en'
    with pytest.raises(TenantLinearError):policy.card({**value,'content_language':'fr'},ids)


def test_legacy_card_language_uses_extraction_then_saved_az_is_returned(ready):
    db,_,data=ready;value=candidate(ready)
    db.execute("UPDATE saas_knowledge_candidates SET content=content-'content_language' WHERE id=%s::uuid",(value['id'],))
    db.execute("UPDATE saas_knowledge_extractions SET route=jsonb_set(route,'{language}','\"en\"'::jsonb) WHERE id=%s::uuid",(value['extraction_id'],));db.commit()
    assert store.detail(PROFILE,value['id'])['candidate']['content_language']=='en'
    assert store.listing(PROFILE,{'limit':25})['candidates'][0]['content_language']=='en'
    saved=store.mutate(PROFILE,value['id'],{'expected_version':1,'content_language':'az'})['candidate']
    assert saved['content_language']=='az'
    assert store.listing(PROFILE,{'limit':25})['candidates'][0]['content_language']=='az'


def test_model_cannot_override_azerbaijani_content_language_marker():
    ids=[str(uuid.UUID(int=i)) for i in (31,32,33)]
    value=model_card(refs=('m1','m2','m3'),confirmed=False)
    value['content_language']='en'
    item={'messages':[{'ref':f'm{i+1}','role':'customer','text':'source'} for i in range(3)],'refs':[{'id':message_id} for message_id in ids]}
    parsed=service.parse(json.dumps({'cards':[value]},ensure_ascii=False),item)[0]
    assert parsed['content_language']=='az'
    assert parsed['source_message_ids']==ids
    assert parsed['start_message_id']==ids[0] and parsed['end_message_id']==ids[-1]
    assert parsed['case_relation']=='new_request'


def test_model_message_boundaries_are_mapped_and_must_be_in_selected_source_range():
    ids=[str(uuid.UUID(int=i)) for i in (41,42,43)]
    value=model_card(refs=('m1','m2','m3'),confirmed=False)
    value.update(start_message_ref='m2',end_message_ref='m3',case_relation='recurrence')
    item={'messages':[{'ref':f'm{i+1}','role':'customer','text':'source'} for i in range(3)],'refs':[{'id':message_id} for message_id in ids]}
    parsed=service.parse(json.dumps({'cards':[value]},ensure_ascii=False),item)[0]
    assert parsed['start_message_id']==ids[1] and parsed['end_message_id']==ids[2]
    assert parsed['case_relation']=='recurrence'
    value['start_message_ref']='m3';value['end_message_ref']='m1'
    with pytest.raises(TenantLinearError):service.parse(json.dumps({'cards':[value]},ensure_ascii=False),item)


def test_remote_case_without_employee_actions_cannot_publish():
    value=model_card(confirmed=True)
    value['resolution_method']='remote';value['operator_actions']=[]
    ids=[str(uuid.UUID(int=i)) for i in (1,2,3)]
    value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    normalized=policy.card(value,ids)
    assert policy.approval_reason(normalized)=='operator_actions_required'
    assert normalized['resolution_steps']


def test_informational_case_does_not_require_technical_resolution_steps():
    value=model_card(confirmed=True)
    value['case_kind']='informational';value['resolution_steps']=[]
    ids=[str(uuid.UUID(int=i)) for i in (1,2,3)]
    value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    normalized=policy.card(value,ids)
    assert policy.approval_reason(normalized) is None


def test_azerbaijani_action_summary_keeps_exact_original_quote_as_evidence():
    message_id=str(uuid.UUID(int=77))
    item={'messages':[{'ref':'m1','role':'operator','text':'Routeri yenidən başladım'}],
          'refs':[{'id':message_id}]}
    value=model_card(refs=('m1',),confirmed=False)
    value.update(resolution_method='remote',operator_actions=['Əməkdaş routeri yenidən başlatdı'],
        operator_action_evidence=[{'action_index':0,'message_ref':'m1','quote':'Routeri yenidən başladım'}])
    parsed=service.parse(json.dumps({'cards':[value]},ensure_ascii=False),item)[0]
    assert parsed['operator_actions']==['Əməkdaş routeri yenidən başlatdı']
    assert parsed['operator_action_evidence']==[{'action_index':0,'message_id':message_id,'quote':'Routeri yenidən başladım'}]
    value['operator_action_evidence'][0]['quote']='Invented quote'
    parsed=service.parse(json.dumps({'cards':[value]},ensure_ascii=False),item)[0]
    assert parsed['operator_actions']==[] and parsed['operator_action_evidence']==[]


def test_listing_hides_rejected_by_default_and_supports_explicit_filters(ready):
    value=candidate(ready)
    store.mutate(PROFILE,value['id'],{'expected_version':1},'reject')
    assert store.listing(PROFILE,{'limit':25})['candidates']==[]
    rejected=store.listing(PROFILE,{'limit':25,'status':'rejected'})['candidates']
    included=store.listing(PROFILE,{'limit':25,'status':'all'})['candidates']
    assert [item['id'] for item in rejected]==[value['id']]
    assert [item['id'] for item in included]==[value['id']]


def test_owner_marks_separately_published_knowledge_without_confirming_case_outcome(ready,monkeypatch):
    db,generation,_=ready;value=candidate(ready)
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]['content']
    content['cause']={'status':'suspected','text':'Owner-provided suspected cause'}
    content['outcome']={'status':'unknown','text':'','message_ids':[]}
    db.execute("UPDATE saas_knowledge_candidates SET content=%s::jsonb,status='needs_clarification' WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps(content),A,value['id']))
    db.commit()
    monkeypatch.setattr(agents,'_connect',lambda:db)
    source=agents.save_knowledge(PROFILE,{'title':'Existing owner knowledge','kind':'text','content':'Separately reviewed guidance','active':True})
    before=json.loads(json.dumps(content))

    marked=store.mutate(PROFILE,value['id'],{'expected_version':1,'source_id':str(source['id'])},'mark_knowledge_published')['candidate']
    assert marked['status']=='knowledge_published'
    assert marked['outcome']['status']=='unknown' and marked['result_confirmed'] is False
    assert marked['cause']['status']=='suspected'
    assert marked['knowledge_publication']['source_id']==str(source['id'])
    assert marked['knowledge_publication']['title']=='Existing owner knowledge'
    assert marked['knowledge_publication']['current'] is True
    assert marked['knowledge_publication']['source_hash']==source['content_hash']
    assert db.execute('SELECT content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]['content']==before
    assert db.execute('SELECT active FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,source['id']))[0]['active'] is True
    assert db.execute('SELECT content,content_hash FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,source['id']))[0]['content']==source['content']
    assert generation.call_count==1
    assert store.listing(PROFILE,{'limit':25})['candidates']==[]
    assert store.listing(PROFILE,{'limit':25,'status':'knowledge_published'})['candidates'][0]['id']==value['id']
    assert store.listing(PROFILE,{'limit':25,'status':'all'})['candidates'][0]['id']==value['id']
    assert db.execute("SELECT action,payload FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND entity_id=%s ORDER BY created_at DESC LIMIT 1",(A,value['id']))[0]['action']=='knowledge_candidate_knowledge_published'
    agents.save_knowledge(PROFILE,{'title':'Changed owner source','kind':'text','content':'Revised guidance','active':True},source_id=source['id'])
    assert store.detail(PROFILE,value['id'])['candidate']['knowledge_publication']['current'] is False


def test_owner_publishes_case_guidance_with_unknown_outcome_atomically_and_idempotently(ready,monkeypatch):
    db,generation,_=ready;value=candidate(ready)
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]['content']
    content.update({'cause':{'status':'suspected','text':'Possible connection issue'},
      'outcome':{'status':'unknown','text':'','message_ids':[]},'resolution_steps':[],
      'owner_additions':'Owner reviewed the printer symptoms and recommends checking label alignment.'})
    db.execute("UPDATE saas_knowledge_candidates SET content=%s::jsonb,status='needs_clarification' WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps(content),A,value['id']));db.commit()
    monkeypatch.setattr(agents,'_connect',lambda:db)
    request={'expected_version':1,'agent_ids':[EXPERT],'owner_additions':content['owner_additions']}
    published=store.mutate(PROFILE,value['id'],request,'publish_guidance')['candidate']
    assert published['status']=='knowledge_published'
    assert published['outcome']['status']=='unknown' and published['result_confirmed'] is False
    assert published['cause']['status']=='suspected'
    assert published['owner_additions']==content['owner_additions']
    assert len(published['publications'])==1
    source=db.execute('SELECT * FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,published['publications'][0]['source_id']))[0]
    assert 'РУКОВОДСТВО ВЛАДЕЛЬЦА' in source['content']
    assert 'Статус результата в исходной переписке: unknown' in source['content']
    assert 'Дополнение владельца (знание владельца; не подтверждает исход клиента)' in source['content']
    provenance=source['provenance'];assert provenance['review_status']=='owner_reviewed_guidance'
    assert provenance['customer_outcome_status']=='unknown' and provenance['cause_status']=='suspected'
    again=store.mutate(PROFILE,value['id'],request,'publish_guidance')['candidate']
    assert again['publications']==published['publications']
    assert len(db.execute('SELECT * FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND provenance->>\'candidate_id\'=%s',(A,value['id'])))==1
    assert runs.knowledge_context(A,'printer symptoms',agent_id=EXPERT)
    assert not runs.knowledge_context(B,'printer symptoms',agent_id=EXPERT)
    assert generation.call_count==1
    assert db.execute("SELECT action FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND entity_id=%s ORDER BY created_at DESC LIMIT 1",(A,value['id']))[0]['action']=='knowledge_candidate_guidance_published'


def test_guidance_publication_is_owner_only_versioned_and_never_bypasses_confirmed_approval(ready):
    db,_,_=ready;value=candidate(ready)
    request={'expected_version':1,'agent_ids':[EXPERT],'owner_additions':'Review the customer-provided information carefully.'}
    with pytest.raises(TenantLinearError,match='Карточка изменилась'):
        store.mutate(PROFILE,value['id'],{**request,'expected_version':0},'publish_guidance')
    db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    admin={**PROFILE,'role':'admin'}
    with pytest.raises(TenantLinearError):
        store.mutate(admin,value['id'],request,'publish_guidance')
    db.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    # Guidance may preserve an unknown outcome; ordinary confirmed-case approval still requires a verified result.
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]['content']
    content['outcome']={'status':'unknown','text':'','message_ids':[]}
    db.execute("UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps(content),A,value['id']));db.commit()
    with pytest.raises(TenantLinearError):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[EXPERT]},'approve')


def test_guidance_publication_rejects_duplicate_agent_ids_without_mutation(ready):
    db,_,_=ready;value=candidate(ready)
    request={'expected_version':1,'agent_ids':[EXPERT,EXPERT],
      'owner_additions':'Review the customer-provided information carefully.'}
    with pytest.raises(TenantLinearError,match='без повторов'):
        store.mutate(PROFILE,value['id'],request,'publish_guidance')
    row=db.execute('SELECT status,version FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]
    assert (row['status'],row['version'])==(value['status'],value['version'])
    assert not db.execute("SELECT id FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND provenance->>'candidate_id'=%s",(A,value['id']))
    assert not db.execute("SELECT action FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND entity_id=%s AND action='knowledge_candidate_guidance_published'",(A,value['id']))


def test_owner_permanent_case_delete_requires_exact_target_and_preserves_usage(ready):
    db,_,_=ready;value=candidate(ready)
    crm_before=db.execute('SELECT id,external_id,body FROM saas_crm_messages WHERE tenant_id=%s::uuid ORDER BY id',(A,))
    import tenant_knowledge_media_cache as media_cache
    with db.cursor() as cur:media_cache.schema(cur)
    db.execute("INSERT INTO saas_knowledge_media_cache(tenant_id,fingerprint,state,token,metadata) VALUES(%s::uuid,'held-synthetic-media','unknown',%s::uuid,%s::jsonb)",(A,str(uuid.uuid4()),json.dumps({'kind':'vision','sha256':'synthetic-held-hash'})))
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,state) VALUES(%s::uuid,%s::uuid,'synthetic-unknown-hold','fixture-model','vision',250,'unknown')",(A,str(uuid.uuid4())))
    run=db.execute('SELECT source_refs,media_results FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]
    db.execute('UPDATE saas_knowledge_extractions SET media_results=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps([{'id':'synthetic','status':'partial','transcript':'case payload'}]),A,value['extraction_id']))
    db.execute('UPDATE saas_ai_agent_runs SET result=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps({'cards':[{'title':'private synthetic proposal'}]}),A,value['extraction_id']))
    db.commit()
    with pytest.raises(TenantLinearError):
        store.mutate({'tenant_id':B,'telegram_id':22},value['id'],{'expected_version':1,'confirm_candidate_id':value['id'],'linked_knowledge_action':'none'},'delete')
    with pytest.raises(TenantLinearError,match='именно этой карточки'):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'confirm_candidate_id':str(uuid.uuid4()),'linked_knowledge_action':'none'},'delete')
    assert db.execute('SELECT id FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))
    usage_before=db.execute('SELECT reserved_tokens,input_tokens,output_tokens FROM saas_ai_agent_token_usage WHERE tenant_id=%s::uuid AND run_id=%s::uuid',(A,value['extraction_id']))
    result=store.mutate(PROFILE,value['id'],{'expected_version':1,'confirm_candidate_id':value['id'],'linked_knowledge_action':'none'},'delete')
    assert result=={'deleted':True,'candidate_id':value['id'],'linked_knowledge_action':'none'}
    assert not db.execute('SELECT id FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))
    cleaned=db.execute('SELECT source_refs,media_results,route FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]
    assert cleaned['source_refs']==[] and cleaned['media_results']==[] and cleaned['route']=={}
    assert db.execute('SELECT result FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['extraction_id']))[0]['result'] is None
    assert db.execute('SELECT reserved_tokens,input_tokens,output_tokens FROM saas_ai_agent_token_usage WHERE tenant_id=%s::uuid AND run_id=%s::uuid',(A,value['extraction_id']))==usage_before
    assert db.execute('SELECT fingerprint,state FROM saas_knowledge_media_cache WHERE tenant_id=%s::uuid AND fingerprint=%s',(A,'held-synthetic-media'))==[{'fingerprint':'held-synthetic-media','state':'unknown'}]
    assert db.execute("SELECT call_key,state,reserved_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key='synthetic-unknown-hold'",(A,))==[{'call_key':'synthetic-unknown-hold','state':'unknown','reserved_microusd':250}]
    assert db.execute('SELECT id,external_id,body FROM saas_crm_messages WHERE tenant_id=%s::uuid ORDER BY id',(A,))==crm_before
    assert db.execute("SELECT action FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND entity_id=%s ORDER BY created_at DESC LIMIT 1",(A,value['id']))[0]['action']=='knowledge_candidate_deleted'


@pytest.mark.parametrize('choice,active',[('retain',True),('deactivate',False)])
def test_permanent_delete_detaches_linked_knowledge_by_explicit_choice(ready,monkeypatch,choice,active):
    db,_,_=ready;value=candidate(ready);monkeypatch.setattr(agents,'_connect',lambda:db)
    source=agents.save_knowledge(PROFILE,{'title':'Independent owner guidance','kind':'text','content':'Synthetic instructions','active':True})
    db.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',
      (json.dumps({'candidate_id':value['id'],'extraction_id':value['extraction_id'],'source_message_ids':value['source_message_ids'],'review_status':'owner_reviewed_guidance'}),A,source['id']))
    db.execute('INSERT INTO saas_knowledge_candidate_sources(tenant_id,candidate_id,agent_id,source_id) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid)',(A,value['id'],AGENT,source['id']))
    db.commit()
    with pytest.raises(TenantLinearError,match='Выберите, сохранить ли связанную'):
        store.mutate(PROFILE,value['id'],{'expected_version':1,'confirm_candidate_id':value['id'],'linked_knowledge_action':'none'},'delete')
    store.mutate(PROFILE,value['id'],{'expected_version':1,'confirm_candidate_id':value['id'],'linked_knowledge_action':choice},'delete')
    kept=db.execute('SELECT active,provenance FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,source['id']))[0]
    assert kept['active'] is active and kept['provenance']['review_status']=='owner_retained_independent'
    assert not ({'candidate_id','extraction_id','source_message_ids'} & set(kept['provenance']))
    assert db.execute('SELECT content FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,source['id']))[0]['content']=='Synthetic instructions'


@pytest.mark.parametrize('role,expected_version,source_state',[
    ('admin',1,'active'),('owner',0,'active'),('owner',1,'inactive'),('owner',1,'foreign'),('owner',1,'case_derived')])
def test_knowledge_published_transition_fails_closed(ready,monkeypatch,role,expected_version,source_state):
    db,_,_=ready;value=candidate(ready);monkeypatch.setattr(agents,'_connect',lambda:db)
    source=agents.save_knowledge(PROFILE,{'title':'Source','kind':'text','content':'Published guidance','active':source_state!='inactive'})
    if source_state=='case_derived':
        db.execute("UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps({'candidate_id':value['id']}),A,source['id']));db.commit()
    if source_state=='foreign':
        foreign=str(uuid.uuid4())
        db.execute("INSERT INTO saas_ai_knowledge_sources(id,tenant_id,title,kind,content,active,content_hash,created_by) VALUES(%s,%s::uuid,'Foreign','text','foreign',TRUE,'foreign-hash',22)",(foreign,B));db.commit();source_id=foreign
    else: source_id=str(source['id'])
    if role!='owner':
        db.execute('UPDATE saas_tenant_members SET role=%s WHERE tenant_id=%s::uuid AND telegram_id=11',(role,A));db.commit()
    before=db.execute('SELECT version,status,content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]
    with pytest.raises(TenantLinearError):
        store.mutate(PROFILE,value['id'],{'expected_version':expected_version,'source_id':source_id},'mark_knowledge_published')
    after=db.execute('SELECT version,status,content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,value['id']))[0]
    assert after==before
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_candidate_knowledge_reviews WHERE tenant_id=%s::uuid AND candidate_id=%s::uuid',(A,value['id']))[0]['n']==0


def test_owner_can_archive_and_restore_case_without_losing_knowledge(ready):
    ready=confirmed_case(ready)
    db,_,_=ready
    value=candidate(ready)
    approved=store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')['candidate']
    assert db.execute("SELECT active FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid",(A,))[0]['active']
    archived=store.mutate(PROFILE,value['id'],{'expected_version':approved['version']},'archive')['candidate']
    assert archived['status']=='archived'
    assert store.listing(PROFILE,{'limit':25})['candidates']==[]
    assert store.listing(PROFILE,{'limit':25,'status':'archived'})['candidates'][0]['id']==value['id']
    assert not db.execute("SELECT active FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid",(A,))[0]['active']
    restored=store.mutate(PROFILE,value['id'],{'expected_version':archived['version']},'restore')['candidate']
    assert restored['status']=='approved'
    assert db.execute("SELECT active FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid",(A,))[0]['active']


def test_case_archive_is_owner_admin_only_and_uses_version(ready):
    db,_,_=ready;value=candidate(ready)
    db.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    with pytest.raises(TenantLinearError):
        store.mutate(PROFILE,value['id'],{'expected_version':1},'archive')
    db.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    with pytest.raises(TenantLinearError,match='изменилась'):
        store.mutate(PROFILE,value['id'],{'expected_version':0},'archive')
