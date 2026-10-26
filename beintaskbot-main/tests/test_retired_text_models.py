"""Retirement admission is offline: no key, budget hold or provider operation."""
import json
import os
from unittest.mock import Mock, patch
import pytest
import tenant_ai_agent_key_policy as policy
import tenant_chat_ai_provider as provider
import tenant_news_ai_provider as news
import tenant_ai_knowledge_extraction_preflight as preflight
import tenant_ai_simulation_engine as simulation
from tenant_linear_policy import TenantLinearError

RETIRED=('gpt-4o-mini','gpt-4o-mini-2024-07-18','gpt-4o-mini-2026-10-26')

@pytest.mark.parametrize('model',RETIRED)
def test_retired_models_block_provider_and_credential_selection(model):
    with patch.object(provider.requests,'post') as post:
        for call in (lambda:provider.generate('fixture',model,'reply',[],''),
                     lambda:news.classify('fixture',model,'title','text'),
                     lambda:policy.choose_api_credential('tenant','agent','connect','fixture',model)):
            with pytest.raises(TenantLinearError,match='configuration_required'):call()
        post.assert_not_called()

@pytest.mark.parametrize('model',['gpt-4o-mini-transcribe','gpt-4o-mini-transcribe-2025-12-15','gpt-4o','gpt-4.1','custom-model'])
def test_other_explicit_models_untouched(model):
    assert not policy.retired_text_model(model)
    assert policy.require_text_model(model)==model

@pytest.mark.parametrize('model',RETIRED)
def test_platform_stale_explicit_default_does_not_select_another_model(model):
    env={'AI_AGENTS_PLATFORM_ENABLED':'true','OPENAI_API_KEY':'fixture',
         'AI_AGENTS_PLATFORM_DEFAULT_MODEL':model,
         'AI_AGENTS_PLATFORM_MODELS':json.dumps([model,'gpt-4.1'])}
    with patch.dict(os.environ,env,clear=True):
        assert policy.platform_models()==['gpt-4.1']
        assert model not in [x['id'] for x in policy.platform_model_choices()]
        with pytest.raises(TenantLinearError,match='configuration_required'):
            policy.choose_api_credential('tenant','agent','connect','',policy.platform_model())


def test_provider_catalog_filters_retired_ids_but_preserves_transcription_ids():
    response=Mock(status_code=200,json=Mock(return_value={'data':[{'id':x} for x in (*RETIRED,'gpt-4.1','gpt-4o-mini-transcribe')]}))
    with patch.object(provider.requests,'get',return_value=response) as get,patch.object(provider.requests,'post') as post:
        assert provider.list_models('fixture')==['gpt-4.1','gpt-4o-mini-transcribe']
        get.assert_called_once();post.assert_not_called()

@pytest.mark.parametrize('agent_model,company_model,expected',[
    ('explicit-agent','company-model','explicit-agent'),
    ('','company-model','company-model'),('','','platform-model')])
def test_media_preflight_preserves_company_first_order(agent_model,company_model,expected):
    cur=Mock();cur.fetchone.return_value={'enabled':True,'settings_enabled':True,'emergency_stop':False,
      'model':agent_model,'tenant_credential_present':True,
      'metadata':{'model':company_model,'models':['explicit-agent','company-model'], 'enabled':True,'consent':True}}
    with patch.object(preflight,'platform_model',return_value='platform-model'),patch.object(preflight,'choose_api_credential',return_value=('fixture','company')):
        assert preflight._model(cur,{'AI_LEARNING_MCP_TENANT_ID':'tenant','AI_LEARNING_MCP_AGENT_ID':'agent'})[0]==expected

@pytest.mark.parametrize('agent_model',RETIRED)
def test_media_preflight_rejects_before_credential_or_budget(agent_model):
    cur=Mock();cur.fetchone.return_value={'enabled':True,'settings_enabled':True,'model':agent_model,'metadata':{}}
    with patch.object(preflight,'choose_api_credential') as choose:
        assert preflight._model(cur,{'AI_LEARNING_MCP_TENANT_ID':'tenant','AI_LEARNING_MCP_AGENT_ID':'agent'})[2:]==(False,'configuration_required')
        choose.assert_not_called()

@pytest.mark.parametrize('agent_model,platform_ready,expected',[
 ('explicit-agent',True,'explicit-agent'),('',True,'platform-model'),('',False,'company-model')])
def test_simulation_preserves_platform_first_order(agent_model,platform_ready,expected):
    cur=Mock();cur.fetchone.return_value={'metadata':{'enabled':True,'consent':True,'model':'company-model'}}
    with patch.object(policy,'platform_service_ready',return_value=platform_ready),patch.object(policy,'platform_model',return_value='platform-model'),patch.object(policy,'choose_api_credential',return_value=('fixture','company')):
        assert simulation._provider_credentials(cur,'tenant','agent','connect',agent_model)[1]==expected


def test_env_cannot_reenable_retired_vision_model():
    from tenant_connect_images import supports_images
    with patch.dict(os.environ,{'AI_AGENTS_VISION_MODELS':','.join((*RETIRED,'gpt-4.1'))}):
        assert supports_images('gpt-4.1')
        assert all(not supports_images(model) for model in RETIRED)


def test_catalog_order_cannot_silently_replace_stale_default():
    with patch.dict(os.environ,{'AI_AGENTS_PLATFORM_MODELS':'["gpt-4o-mini","gpt-4.1"]'},clear=True):
        assert policy.platform_models()==['gpt-4.1']
        assert policy.platform_model()=='gpt-4o-mini'
        with pytest.raises(TenantLinearError,match='configuration_required'):
            policy.require_text_model(policy.platform_model())


def test_disabling_existing_chat_ai_never_requires_a_replacement_model():
    from tenant_chat_ai_policy import configuration
    from test_tenant_chat_ai import CONFIG
    for model in ('','gpt-4o-mini','gpt-4o-mini-2024-07-18'):
        result=configuration({**CONFIG,'enabled':False,'model':model,'copilot_model':model})
        assert result['enabled'] is False
        with pytest.raises(TenantLinearError):configuration({**CONFIG,'model':model})


def test_explicit_luna_image_input_uses_existing_responses_without_new_calls():
    from tenant_connect_images import supports_images
    with patch.dict(os.environ,{},clear=True):assert supports_images('gpt-6-luna')
    response=Mock(status_code=200,json=Mock(return_value={'status':'completed','output':[
        {'type':'message','content':[{'type':'output_text','text':'fixture observation'}]}]}))
    with patch.object(provider.requests,'post',return_value=response) as post:
        assert provider.generate('fixture','gpt-6-luna','summary',[],'',images=[{'content_type':'image/png','content':b'fixture'}])=='fixture observation'
        assert post.call_args.kwargs['json']['model']=='gpt-6-luna'
        assert post.call_args.args[0]=='https://api.openai.com/v1/responses'
        post.assert_called_once()

@pytest.mark.parametrize('agent_model,platform_ready,expected',[
 ('explicit-agent',True,'explicit-agent'),('',True,'platform-model'),('',False,'company-model')])
def test_primary_worker_preserves_platform_first_order(agent_model,platform_ready,expected):
    from unittest.mock import MagicMock
    import tenant_ai_agent_run_store as runs
    row={'tenant_status':'active','role':'primary','agent_enabled':True,'settings_enabled':True,
      'operating_mode':'active','mode':'automatic','auto_send_enabled':True,'agent_model':agent_model,
      'metadata':{'enabled':True,'consent':True,'model':'company-model'},'secrets':b'fixture',
      'scopes':[],'schedule':{},'crm_actions_enabled':False}
    connect=MagicMock();connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value=row
    job={'tenant_id':'t','agent_id':'a','provider':'connect','conversation_id':'c','channel_id':'00000000-0000-4000-8000-000000000001','lease_token':'l','revision':1}
    with patch.object(runs,'_connect',connect),patch.object(runs,'ensure_schema'),patch.object(runs,'platform_service_ready',return_value=platform_ready),patch.object(runs,'platform_model',return_value='platform-model'):
        assert runs.load_agent_config(job)['agent_model']==expected

@pytest.mark.parametrize('model',['explicit-agent','gpt-4o-mini-2024-07-18'])
def test_instructor_uses_explicit_model_or_fails_before_provider(model):
    from unittest.mock import MagicMock
    import tenant_platform
    import tenant_ai_instructor_worker as instructor
    from tenant_ai_instructor_contract import ContractError
    connect=MagicMock();cur=connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    meta={'enabled':True,'consent':True,'model':'company-model','models':['explicit-agent',model]}
    cur.fetchall.return_value=[{'id':id,'role':role,'enabled':True,'mode':'manual','settings_enabled':True,
        'tenant_status':'active','metadata':meta,'secrets':b'fixture','model':model if role=='primary' else ''}
        for id,role in [('p','primary'),('e','expert')]]
    cipher=Mock();cipher.decrypt.return_value=json.dumps({'tenant_id':'t','provider':'chat_ai','api_key':'fixture'}).encode()
    with patch.object(tenant_platform,'_connect',connect),patch.object(tenant_platform,'_fernet',return_value=cipher):
        if policy.retired_text_model(model):
            with pytest.raises(ContractError,match='configuration_required'):
                instructor._provider_pair({'tenant_id':'t','primary_agent_id':'p','expert_agent_id':'e'})
        else:
            assert instructor._provider_pair({'tenant_id':'t','primary_agent_id':'p','expert_agent_id':'e'})[1:3]==('explicit-agent','company-model')
