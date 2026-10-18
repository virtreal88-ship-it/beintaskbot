import json,hashlib
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
import pytest
from tenant_ai_lesson_contract import validate,approved_lesson,internal_attachment
from tenant_linear_policy import TenantLinearError

ROOT=Path(__file__).resolve().parents[1]
A='00000000-0000-0000-0000-000000000001';B='00000000-0000-0000-0000-000000000002'

def lesson():return json.loads((ROOT/'docs/instructor-pilot/warehouse-transfer.lesson.json').read_text(encoding='utf-8'))

def test_eight_distinct_real_capture_hashes_and_bounds():
    v=validate(lesson());hashes=[]
    for slide in v['slides']:
        path=ROOT/'docs/instructor-pilot/visual-az'/slide['image']['asset_id']
        digest=hashlib.sha256(path.read_bytes()).hexdigest();assert digest==slide['image']['sha256'];hashes.append(digest)
    assert len(set(hashes))==8
    assert all(f['only_below_scale']==0.6 for s in v['slides'][:2] for f in s['playback']['focus'])
    assert v['review_status']=='pending_review'
    assert 'mənfi qalığa' in v['slides'][4]['note']

@pytest.mark.parametrize('field,value',[('order',7),('title','x'*2001)])
def test_invalid_slide_metadata(field,value):
    v=lesson();v['slides'][0][field]=value
    with pytest.raises(ValueError):validate(v)

@pytest.mark.parametrize('fault',['path','bounds','focus','duration','status'])
def test_untrusted_or_unbounded_material(fault):
    v=lesson();s=v['slides'][0]
    if fault=='path':s['image']['asset_id']='../other.jpg'
    if fault=='bounds':s['areas'][0]['width']=9000
    if fault=='focus':s['playback']['focus']=[{'area_id':'missing','at_ms':2,'padding':20}]
    if fault=='duration':s['playback']['duration_ms']=999999
    if fault=='status':v['verification']['status']='stock_transfer_success'
    with pytest.raises(ValueError):validate(v)

def test_existing_knowledge_sources_reuse_is_scoped_and_no_browser_fallback():
    cur=Mock();assert approved_lesson(cur,A,B) is None;cur.execute.assert_not_called()
    v=lesson();v['review_status']='approved';v['verification']['ui_version']='reviewed-build'
    cur.fetchone.return_value={'id':B,'content':json.dumps(v),'provenance':{}}
    result=approved_lesson(cur,A,B,ui_version='reviewed-build')
    assert result['source_id']==B
    sql,args=cur.execute.call_args.args
    assert args[:2]==(A,B) and 'k.active=TRUE' in sql and "t.status='active'" in sql
    assert "review_status'='approved'" in sql and 'k.agent_id' in sql
    v['review_status']='pending_review';cur.fetchone.return_value['content']=json.dumps(v)
    assert approved_lesson(cur,A,B,ui_version='reviewed-build') is None

@pytest.mark.parametrize('state',['human','paused','stopped'])
def test_no_attachment_for_inactive_dialog(state):
    with pytest.raises(TenantLinearError):internal_attachment(lesson(),tenant_id=A,expected_tenant_id=A,source_id=B,dialog_state=state,group_revision=2,live_group_revision=2)

def test_internal_contract_no_direct_delivery_and_pending_review_block():
    v=lesson();args=dict(tenant_id=A,expected_tenant_id=A,source_id=B,dialog_state='manual',group_revision=2,live_group_revision=2)
    with pytest.raises(TenantLinearError):internal_attachment(v,**args)
    v['review_status']='approved';result=internal_attachment(v,**args)
    assert result['delivery']=='internal_only' and not result['customer_send_allowed'] and not result['changes_dialog_state']
    with pytest.raises(TenantLinearError):internal_attachment(v,**{**args,'expected_tenant_id':B})
    with pytest.raises(TenantLinearError):internal_attachment(v,**{**args,'live_group_revision':3})


# Real SQL verifies the tenant/agent/provenance predicates, not only query text.
from test_tenant_support_inbox import database,db,seed_job,A as DB_A,B as DB_B,AGENT,PROFILE

def test_real_knowledge_sources_isolation_and_review(db):
    seed_job(db)
    value=lesson();value['review_status']='approved';value['verification']['ui_version']='build-1'
    source='00000000-0000-0000-0000-000000000901'
    provenance={'review_status':'approved','approved_by':11,'lesson':{'operation':value['operation'],'language':'az','ui_version':'build-1'}}
    db.execute("INSERT INTO saas_ai_knowledge_sources(id,tenant_id,title,kind,content,content_hash,created_by,agent_id,provenance) VALUES(%s::uuid,%s::uuid,'Lesson','text',%s,'hash',11,%s::uuid,%s::jsonb)",(source,DB_A,json.dumps(value),AGENT,json.dumps(provenance)));db.commit()
    def find(tenant=DB_A,agent=AGENT):
        with db.cursor() as cur:return approved_lesson(cur,tenant,agent,ui_version='build-1')
    assert find()['source_id']==source
    assert find(DB_B) is None and find(agent=source) is None
    db.execute('UPDATE saas_ai_knowledge_sources SET active=false');db.commit();assert find() is None
    db.execute("UPDATE saas_ai_knowledge_sources SET active=true,provenance=jsonb_set(provenance,'{review_status}','\"pending_review\"')");db.commit();assert find() is None
    db.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb',(json.dumps(provenance),));db.execute("UPDATE saas_tenants SET status='archived' WHERE id=%s::uuid",(DB_A,));db.commit();assert find() is None
