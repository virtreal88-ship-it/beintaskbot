"""One reviewed case interval, existing campaign consent and provider fences."""
import json
import uuid

import pytest

from test_knowledge_candidates import ready, candidate
from test_tenant_support_inbox import database, db, A, B, CA, AGENT, PROFILE
from tenant_linear_policy import TenantLinearError
import tenant_case_media_admission as admission
import tenant_knowledge_campaigns as campaigns
import tenant_knowledge_campaign_worker as worker


def _image(db, day):
    key=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','Synthetic attachment','image',%s::jsonb,%s::timestamptz)''',
      (A,key,'media-'+key,'connect:'+CA,json.dumps({'mediaId':key}),f'2024-01-{day:02d}T00:00:00Z'))
    return key


def _case(ready, days=(3,4)):
    db, _, _=ready
    card=candidate(ready)
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content']
    start,end=content['start_message_id'],content['end_message_id']
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-01-01T00:00:00Z' WHERE id=%s::uuid",(start,))
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-02-01T00:00:00Z' WHERE id=%s::uuid",(end,))
    ids=[_image(db,day) for day in days]
    db.commit()
    return db,card,ids


def test_two_appeals_same_dialog_and_fourteen_bounded_attachments(ready):
    db,card,inside=_case(ready,tuple(range(2,16)))
    before=_image(db,1)
    after=_image(db,20)
    # Make the latter genuinely outside the first case and inside a second
    # separately reviewed interval of this long lived conversation.
    db.execute("UPDATE saas_crm_messages SET happened_at='2023-12-31T00:00:00Z' WHERE id=%s::uuid",(before,))
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-03-01T00:00:00Z' WHERE id=%s::uuid",(after,))
    db.commit()
    with db.cursor() as cur:
        first=admission.plan(cur,PROFILE,card['id'])
    assert first['status']=='pending_consent'
    assert {x['id'] for x in first['media']}==set(inside)
    assert before not in {x['id'] for x in first['media']}
    assert after not in {x['id'] for x in first['media']}
    assert len(list(admission.routes(first,AGENT)))==14
    second_start,second_end=_image(db,21),_image(db,22)
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-03-02T00:00:00Z' WHERE id=%s::uuid",(second_start,))
    db.execute("UPDATE saas_crm_messages SET happened_at='2024-03-03T00:00:00Z' WHERE id=%s::uuid",(second_end,))
    row=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]
    content=row['content'];content.update(start_message_id=after,end_message_id=second_end,
        source_message_ids=[after,second_start,second_end])
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb,version=version+1 WHERE id=%s::uuid',(json.dumps(content),card['id']))
    db.commit()
    with db.cursor() as cur:second=admission.plan(cur,PROFILE,card['id'])
    assert second['status']=='pending_consent'
    assert {x['id'] for x in second['media']}=={after,second_start,second_end}
    assert not set(inside)&{x['id'] for x in second['media']}


def test_case_campaign_owner_consent_batches_and_replay_fences(ready,monkeypatch):
    db,card,ids=_case(ready)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    monkeypatch.setattr(worker,'_connect',lambda:db)
    key=str(uuid.uuid4())
    data={'request_id':key,'case_id':card['id'],'channel_ids':[CA],
      'analysis_agent_id':AGENT,'max_calls':6,'max_tokens':48000,
      'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':4}
    assert campaigns.prepare(PROFILE,data)==key
    assert campaigns.prepare(PROFILE,data)==key
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments')[0]['n']==0
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_sources')[0]['n']==0
    assert campaigns.claim(A,key) is None
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    plan=row['progress']
    confirmation={'expected_version':row['version'],'channel_ids':[CA],
      'max_calls':6,'max_tokens':48000,'confirm_external_processing':True,
      'media_policy':row['media_policy'],'case_id':card['id'],'case_plan_hash':plan['plan_hash']}
    with pytest.raises(TenantLinearError):
        campaigns.approve(PROFILE,key,{**confirmation,'case_plan_hash':'0'*64})
    assert campaigns.claim(A,key) is None
    campaigns.approve(PROFILE,key,confirmation)
    fragments=db.execute('SELECT * FROM saas_knowledge_campaign_fragments ORDER BY id')
    assert len(fragments)==2
    assert {f['route']['message_ids'][0] for f in fragments}==set(ids)
    assert all(len(f['route']['message_ids'])==1 for f in fragments)
    with db.cursor() as cur:
        same=campaigns.enqueue(cur,PROFILE,
          db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0],fragments[0]['route'])
    assert same==str(fragments[0]['id'])
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments')[0]['n']==2
    claimed=campaigns.claim(A,key)
    assert claimed and campaigns.claim(A,key) is None
    with db.cursor() as cur:assert worker.check_fence(cur,A,claimed['id']) is None
    db.execute("UPDATE saas_knowledge_campaigns SET status='stopped' WHERE id=%s::uuid",(key,));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError):worker.check_fence(cur,A,claimed['id'])


def test_missing_bounds_tenant_and_source_change_fail_closed(ready,monkeypatch):
    db,card,ids=_case(ready,(3,))
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError):admission.plan(cur,{'tenant_id':B,'telegram_id':22},card['id'])
    content=db.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(card['id'],))[0]['content']
    content['end_message_id']=None
    db.execute('UPDATE saas_knowledge_candidates SET content=%s::jsonb WHERE id=%s::uuid',(json.dumps(content),card['id']))
    db.commit()
    with db.cursor() as cur:assert admission.plan(cur,PROFILE,card['id'])['status']=='pending_boundaries'


def test_cached_visual_receipt_is_not_queued_again(ready,monkeypatch):
    import tenant_media_evidence as evidence
    import tenant_knowledge_candidates_store as store
    db,card,ids=_case(ready)
    evidence.create_schema(db)
    row=db.execute('SELECT external_id,body,raw FROM saas_crm_messages WHERE id=%s::uuid',(ids[0],))[0]
    ref={'id':ids[0],'channel_id':CA,'external_id':row['external_id'],
      'media_kind':'image','media_version':evidence.media_version(row['raw']),
      'body_hash':store.digest(row['body'])}
    with db.cursor() as cur:
        evidence.persist(cur,A,[ref],[{'id':ids[0],'status':'processed',
          'code':'vision_observation','observation':'Synthetic saved image observation'}])
    db.commit()
    with db.cursor() as cur:planned=admission.plan(cur,PROFILE,card['id'])
    assert planned['media'][0]['complete']
    assert [r['message_ids'] for r in admission.routes(planned,AGENT)]==[[ids[1]]]


def test_changed_media_and_unknown_hold_never_dispatch_again(ready,monkeypatch):
    db,card,ids=_case(ready,(3,))
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    key=str(uuid.uuid4())
    data={'request_id':key,'case_id':card['id'],'channel_ids':[CA],
      'analysis_agent_id':AGENT,'max_calls':3,'max_tokens':24000,
      'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':1,'max_asr_calls':0,'max_vision_calls':2}
    campaigns.prepare(PROFILE,data)
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    campaigns.approve(PROFILE,key,{'expected_version':row['version'],'channel_ids':[CA],
      'max_calls':3,'max_tokens':24000,'confirm_external_processing':True,
      'media_policy':row['media_policy'],'case_id':card['id'],
      'case_plan_hash':row['progress']['plan_hash']})
    claimed=campaigns.claim(A,key)
    db.execute('UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid',
      (json.dumps({'mediaId':'replacement'}),ids[0]));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError,match='source changed'):
        worker.check_fence(cur,A,claimed['id'])
    db.execute("UPDATE saas_knowledge_campaign_fragments SET lease_until=now()-interval '1 second' WHERE id=%s::uuid",(claimed['id'],))
    db.commit()
    assert campaigns.claim(A,key) is None
    fragment=db.execute('SELECT status FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid',(claimed['id'],))[0]
    assert fragment['status']=='unknown'
    assert db.execute('SELECT reserved_calls FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]['reserved_calls']==3


def test_insufficient_case_budget_never_queues_first_attachment(ready,monkeypatch):
    db,card,_=_case(ready)
    monkeypatch.setattr(campaigns,'_connect',lambda:db)
    key=str(uuid.uuid4())
    campaigns.prepare(PROFILE,{'request_id':key,'case_id':card['id'],'channel_ids':[CA],
      'analysis_agent_id':AGENT,'max_calls':3,'max_tokens':24000,
      'allow_external_media':True,'allow_paid_asr':False,
      'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':2})
    row=db.execute('SELECT * FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]
    with pytest.raises(TenantLinearError,match='Budget does not cover all case attachments'):
        campaigns.approve(PROFILE,key,{'expected_version':row['version'],'channel_ids':[CA],
          'max_calls':3,'max_tokens':24000,'confirm_external_processing':True,
          'media_policy':row['media_policy'],'case_id':card['id'],
          'case_plan_hash':row['progress']['plan_hash']})
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments')[0]['n']==0
    assert db.execute('SELECT status FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]['status']=='awaiting_budget'
