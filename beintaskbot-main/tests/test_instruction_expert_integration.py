"""Approved lessons only, existing expert runtime and isolated PostgreSQL."""
import json
import pytest
from test_instruction_library import database,db,ready,save,mutate,uid,doc,A,B,AGENT,PROFILE
from test_tenant_ai_expert import expert,service,EXPERT,GOOD
import tenant_instruction_library as lib


def lookup(db,tenant=A,agent=AGENT,query='inventory.warehouse_transfer'):
    with db.cursor() as cur:return lib.expert_lookup(cur,tenant,agent,query)


def publish(ready):
    row=save(ready)
    row=mutate(mutate(row,'submit'),'approve',review_confirmed=True)
    return mutate(row,'publish',agent_ids=[AGENT])


def test_lookup_review_scope_revocation_and_snapshot(ready):
    db,_=ready
    row=save(ready)
    assert lookup(db)['status']=='not_found'
    row=mutate(mutate(row,'submit'),'approve',review_confirmed=True)
    assert lookup(db)['status']=='not_found' # approval alone does not publish
    row=mutate(row,'publish',agent_ids=[AGENT])
    result=lookup(db)
    assert result['status']=='approved_candidate' and result['customer_send_allowed'] is False
    assert result['evidence'][0]['lesson_id']==row['id'] and result['evidence'][0]['excerpt_only']
    assert lookup(db,B)['status']=='not_found' and lookup(db,agent=uid())['status']=='not_found'
    assert lookup(db,query='unrelated-operation')['browser_fallback']=='integration_unavailable'
    db.commit()
    db.execute("UPDATE saas_ai_knowledge_sources SET content='tampered'");db.commit()
    assert lookup(db)['status']=='source_changed'
    lib.operation(PROFILE,'save',{'expected_version':row['version'],'doc':row['doc']},row['id'])
    assert lookup(db)['status']=='not_found'


def test_call_expert_gets_lesson_without_delivery_and_keeps_live_fence(expert,monkeypatch):
    db,job,parent=expert;monkeypatch.setattr(lib,'_connect',lambda:db);lib.schema(db)
    asset=uid();db.execute('INSERT INTO saas_instruction_assets VALUES(%s::uuid,%s::uuid,%s,100,100,%s)',(A,asset,b'jpeg','a'*64));db.commit()
    publish((db,asset))
    db.execute("UPDATE saas_ai_agents SET expert_policy=jsonb_set(expert_policy,'{tools}','[\"instruction_library\"]'::jsonb) WHERE id=%s::uuid",(AGENT,));db.commit()
    observed=[]
    def model(*args,**kwargs):
        observed.append(kwargs['extra_context']['diagnostics'])
        return json.dumps(GOOD),{'input_tokens':80,'output_tokens':20,'total_tokens':100}
    monkeypatch.setattr(service,'generate',model)
    reservation=service.reserve(job,parent)
    result=service.execute(job,parent,reservation,'inventory.warehouse_transfer',[])
    assert result['status']=='completed'
    assert observed[0][0]['status']=='approved_candidate'
    assert not observed[0][0]['customer_send_allowed']
    assert db.execute('SELECT count(*) n FROM saas_ai_agent_drafts')[0]['n']==0
    db.execute("UPDATE saas_ai_conversation_state SET state='human',revision=revision+1");db.commit()
    stale=service.execute(job,parent,reservation,'inventory.warehouse_transfer',[])
    assert stale['status']=='stale' and len(observed)==1
