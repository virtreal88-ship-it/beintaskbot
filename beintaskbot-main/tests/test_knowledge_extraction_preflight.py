"""Scoped MCP admission checks; no extraction, provider or media calls."""
import json
import uuid
import pytest

pytest_plugins=('test_tenant_support_inbox',)
from test_tenant_support_inbox import db, A, CA, CB, AGENT, PROFILE, seed_job
from tenant_linear_policy import TenantLinearError
import tenant_ai_knowledge_extraction_preflight as preflight


def _cfg():
    return {'AI_LEARNING_MCP_TENANT_ID':A,'AI_LEARNING_MCP_AGENT_ID':AGENT,
            'AI_LEARNING_MCP_CHANNEL_ID':CA,'actor_id':11,'granted_scopes':['ai_learning.read']}


def _source(database):
    message=str(uuid.uuid4())
    database.execute("""INSERT INTO saas_crm_messages
      (tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw)
      VALUES(%s::uuid,%s::uuid,41,'synthetic-video','connect:'||%s,'incoming','redacted','video',%s::jsonb)""",
      (A,message,CA,json.dumps({'mediaId':'synthetic','fileLength':100})))
    database.commit();return message


def _pricing(monkeypatch):
    monkeypatch.setenv('AI_AGENTS_PLATFORM_MODELS',json.dumps(['fixture-model']))
    monkeypatch.setenv('AI_AGENTS_PLATFORM_DEFAULT_MODEL','fixture-model')
    monkeypatch.setenv('AI_AGENTS_TRANSCRIPTION_MODEL','fixture-asr')
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps({'fixture-model':{'input':1,'output':1}}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'fixture-model':{'vision_low':10},'fixture-asr':{'asr':10}}))


def test_preflight_exact_source_is_read_only_and_fails_closed_without_fragment(db,monkeypatch):
    seed_job(db);_pricing(monkeypatch);message=_source(db)
    value=preflight.preflight(db.cursor(),_cfg(),{'message_id':message,'channel_id':CA,'lead_id':41})
    assert value['ready'] is False and value['reason_code']=='approved_campaign_fragment_required'
    assert value['message_id']==message and value['provider_domain']=='api.openai.com'
    assert 'conversation_id' not in value and value['tariffs']=={'text':True,'vision_low':True,'asr':True}
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_extractions')[0]['n']==0


def test_preflight_requires_scoped_channel_and_approved_live_fragment(db,monkeypatch):
    import tenant_knowledge_campaign_schema as schema
    seed_job(db);_pricing(monkeypatch);message=_source(db)
    with pytest.raises(TenantLinearError,match='OAuth channel'):
        preflight.preflight(db.cursor(),_cfg(),{'message_id':message,'channel_id':CB,'lead_id':41})
    schema.create_schema(db);campaign=str(uuid.uuid4());fragment=str(uuid.uuid4())
    db.execute("""INSERT INTO saas_knowledge_campaigns
      (tenant_id,id,created_by,sources,analysis_agent_id,status,approved_at,max_calls,max_tokens)
      VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'running',now(),1,16000)""",
      (A,campaign,json.dumps([CA]),AGENT))
    db.execute("""INSERT INTO saas_knowledge_campaign_fragments
      (tenant_id,campaign_id,id,source_key,route,status,lease_until)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'synthetic',%s::jsonb,'running',now()+interval '5 minutes')""",
      (A,campaign,fragment,json.dumps({'channel_id':CA,'conversation_id':'client'})))
    db.commit()
    value=preflight.preflight(db.cursor(),_cfg(),{'message_id':message,'channel_id':CA,'lead_id':41})
    assert value['ready'] is True and value['model_id']=='fixture-model' and value['asr_model_id']=='fixture-asr'
