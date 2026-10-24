"""Owner Learning review keeps authentic evidence separate from proposed knowledge."""
import json,uuid
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,PROFILE,seed_simulation_run
from test_ai_learning_mcp import _setup
import tenant_ai_administrator_api as api
import tenant_ai_administrator_drafts as drafts


def test_review_provenance_boundaries_and_draft_are_nonpublic(db,monkeypatch):
    _setup(monkeypatch,db); run,source,_=seed_simulation_run(db,events=[{'type':'knowledge_retrieved','source_ids':[]}])
    future=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at,raw)
      SELECT %s::uuid,tenant_id,kommo_lead_id,'future-review',channel,'outgoing','Employee fixed printer','image',COALESCE(happened_at,created_at)+interval '1 second',%s::jsonb FROM saas_crm_messages WHERE id=%s::uuid''',(future,json.dumps({'private_url':'https://secret.test?token=hidden'}),source));db.commit()
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    with db.cursor() as cur:
        review=api.review_context(cur,PROFILE,run)
        assert next(m for m in review['messages'] if m['id']==source)['review_phase']=='trigger'
        later=next(m for m in review['messages'] if m['id']==future)
        assert later['review_phase']=='later' and later['author_type']=='operator' and later['media_url']==''
        assert 'secret.test' not in json.dumps(review) and 'private_url' not in json.dumps(review)
        request={'action':'prepare_review_solution','run_id':run,'explanation':'Owner explains the printer restart.','kind':'knowledge','request_key':str(uuid.uuid4())}
        saved=api.prepare_review_solution(cur,PROFILE,request)
        assert saved['state']=='draft' and saved['body']['evidence_ids']==[source] and saved['target']['review_run_id']==run
        assert api.prepare_review_solution(cur,PROFILE,request)['id']==saved['id']
        with pytest.raises(Exception): api.prepare_review_solution(cur,PROFILE,{**request,'explanation':'Changed'})
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n']==0
    db.commit()


@pytest.mark.parametrize('fault',['tenant','role','missing_run'])
def test_review_rejects_unauthorized_evidence(db,monkeypatch,fault):
    _setup(monkeypatch,db);run,_,_=seed_simulation_run(db)
    profile={'tenant_id':B,'telegram_id':22} if fault=='tenant' else PROFILE
    if fault=='role':db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid",(A,));db.commit()
    if fault=='missing_run':run=str(uuid.uuid4())
    with db.cursor() as cur:
        with pytest.raises(Exception):api.review_context(cur,profile,run)
