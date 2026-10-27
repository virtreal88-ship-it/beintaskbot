"""Real settings -> consent -> worker regression with synthetic media only."""
import json
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import db,A,B,AGENT,PROFILE
from test_single_photo_retry import prior,complete
from test_photo_model import recovery,calls
import tenant_chat_ai_settings as settings
import tenant_ai_photo_model as photo
import tenant_ai_single_message_extraction as consent
import tenant_ai_knowledge_extraction_preflight as preflight
import tenant_ai_administrator_read as admin
import tenant_knowledge_campaigns as campaigns
import tenant_knowledge_candidates_service as service
from tenant_linear_policy import TenantLinearError

pytest_plugins=('test_tenant_support_inbox',)


@pytest.fixture
def company(recovery,monkeypatch):
    database,cfg,args,_,_=recovery
    # Production-shaped legacy settings: no hidden models allowlist.
    metadata={'enabled':True,'consent':True,'audio_enabled':False,'model':'gpt-4o-mini',
      'transcription_model':'whisper-1','daily_limit':50,'media_hosts':[],
      'copilot_model':'gpt-4.1-2025-04-14','copilot_enabled':True,'copilot_daily_limit':50}
    database.execute("UPDATE saas_tenant_integrations SET metadata=%s::jsonb WHERE tenant_id=%s::uuid AND provider='chat_ai'",(json.dumps(metadata),A))
    database.execute("UPDATE saas_ai_agents SET model='' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))
    database.commit()
    monkeypatch.setattr(settings,'_connect',lambda:database)
    monkeypatch.setattr(settings,'_fernet',Mock(side_effect=AssertionError('Do not touch the existing key')))
    return recovery


def configure(database,model='gpt-4.1',profile=PROFILE):
    current=settings.read(profile)
    return settings.save(profile,{**current,'photo_model':model,'api_key':'',
      'expected_updated_at':current['updated_at']})


def test_real_settings_readback_exact_consent_worker_and_shared_budget(company,monkeypatch):
    database,cfg,args,_,_=company
    old=database.execute("SELECT secrets,metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(A,))[0]
    agents=database.execute('SELECT * FROM saas_ai_agents ORDER BY id')
    held=database.execute('SELECT * FROM saas_ai_spend_reservations ORDER BY id')
    before=preflight.preflight(database.cursor(),cfg,{k:args[k] for k in ('message_id','channel_id','lead_id','photo_model')})
    assert before['reason_code']=='photo_model_company_configuration_required'
    assert before['photo_tariff']['ready'] and before['photo_tariff']['max_microusd']==8896
    saved=configure(database)
    assert saved['photo_model']=='gpt-4.1' and saved['photo_model_tariffs']['gpt-4.1']['ready']
    audit=database.execute("SELECT payload FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='chat_ai_photo_model_changed'",(A,))
    assert audit[-1]['payload']=={'previous_photo_model':'','photo_model':'gpt-4.1'}
    assert settings.read(PROFILE)['photo_model']=='gpt-4.1'
    after=database.execute("SELECT secrets,metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(A,))[0]
    assert after['secrets']==old['secrets']
    assert after['metadata']=={**old['metadata'],'photo_model':'gpt-4.1'}
    visible=admin.read(database.cursor(),{**cfg,'granted_scopes':[admin.READ_SCOPE,admin.CONTENT_SCOPE]},
      'company_ai_settings_read',{'include_content':True})['photo_configuration']
    assert visible['model']=='gpt-4.1' and visible['tariffs']['gpt-4.1']['max_microusd']==8896
    assert 'secrets' not in str(visible) and 'api_key' not in str(visible)
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    assert prepared['exact_preview']['maximum_additional_microusd']==8896
    with pytest.raises(TenantLinearError,match='does not match exact consent'):
        preflight.preflight(database.cursor(),cfg,{**{k:args[k] for k in ('message_id','channel_id','lead_id')},
          'consent_id':prepared['consent_id'],'photo_model':'gpt-4.1-2025-04-14'})
    database.rollback()
    approved=complete(database,cfg,prepared,acknowledge_prior_unknown=True)
    claim=campaigns.claim(A,approved['campaign_id']);assert claim
    download,vision,main=calls(monkeypatch)
    response=service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    assert response['request']['status']=='completed'
    assert download.await_count==vision.call_count==1 and main.call_count==0
    assert vision.call_args.args[1]=='gpt-4.1'
    for row in held:assert database.execute('SELECT * FROM saas_ai_spend_reservations WHERE id=%s::uuid',(row['id'],))[0]==row
    assert database.execute('SELECT * FROM saas_ai_agents ORDER BY id')==agents
    new=database.execute('SELECT * FROM saas_ai_spend_reservations WHERE call_key LIKE %s',('extraction:'+str(claim['id'])+'%',))
    assert len(new)==1 and new[0]['reserved_microusd']==8896 and new[0]['actual_microusd']==280


@pytest.mark.parametrize('prices',[{}, {'gpt-4.1':{'input':150000,'output':600000}}])
def test_missing_or_wrong_tariff_visible_before_card_or_setting_write(company,monkeypatch,prices):
    database,cfg,args,_,_=company
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION',json.dumps(prices))
    before=settings.read(PROFILE)
    assert before['photo_model_tariffs']['gpt-4.1']=={'ready':False,'model':'gpt-4.1','reason_code':'photo_model_tariff_required'}
    ready=preflight.preflight(database.cursor(),cfg,{k:args[k] for k in ('message_id','channel_id','lead_id','photo_model')})
    assert ready['photo_tariff']['ready'] is False
    with pytest.raises(TenantLinearError,match='photo_model_tariff_required'):configure(database)
    database.rollback()
    assert settings.read(PROFILE)['photo_model']==''
    assert database.execute("SELECT count(*) AS n FROM saas_ai_single_message_extraction_consents WHERE tenant_id=%s::uuid AND message_id=%s::uuid AND photo_model!=''",(A,args['message_id']))[0]['n']==0


def test_photo_selection_requires_owner_and_legacy_save_preserves_selection(company):
    database,cfg,args,_,_=company
    configure(database)
    current=settings.read(PROFILE);current.pop('photo_model')
    settings.save(PROFILE,{**current,'expected_updated_at':current['updated_at']})
    assert settings.read(PROFILE)['photo_model']=='gpt-4.1'
    database.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));database.commit()
    with pytest.raises(TenantLinearError,match='Company owner'):configure(database,'')
    database.rollback()
    assert settings.read(PROFILE)['photo_model']=='gpt-4.1'
    with pytest.raises(TenantLinearError):settings.read({'tenant_id':B,'telegram_id':11})
    database.rollback()


@pytest.mark.parametrize('phase',['prepared','claimed'])
def test_photo_setting_revoked_before_approval_or_dispatch_blocks(company,monkeypatch,phase):
    database,cfg,args,_,_=company
    configure(database)
    prepared=consent.prepare(database.cursor(),cfg,args);database.commit()
    if phase=='claimed':
        approved=complete(database,cfg,prepared,acknowledge_prior_unknown=True)
        claim=campaigns.claim(A,approved['campaign_id']);assert claim
    configure(database,'')
    download,vision,main=calls(monkeypatch)
    with pytest.raises(TenantLinearError):
        if phase=='prepared':complete(database,cfg,prepared,acknowledge_prior_unknown=True)
        else:service.analyze(PROFILE,{**claim['route'],'request_id':str(claim['id'])})
    database.rollback()
    assert not download.await_count and not vision.call_count and not main.call_count


def test_selected_photo_does_not_allow_text_model_and_preflight_scope_is_exact(company):
    database,cfg,args,_,_=company
    configure(database)
    database.execute("UPDATE saas_ai_agents SET model='gpt-4.1' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT));database.commit()
    model,source,ready,reason=preflight._model(database.cursor(),cfg)
    assert model=='gpt-4.1' and ready is False
    with pytest.raises(TenantLinearError):preflight.preflight(database.cursor(),{**cfg,'AI_LEARNING_MCP_TENANT_ID':B},
      {k:args[k] for k in ('message_id','channel_id','lead_id','photo_model')})
    database.rollback()
    for invalid in ('gpt-6-luna',None,{},['gpt-4.1']):
        with pytest.raises(TenantLinearError):configure(database,invalid)
        database.rollback()


def test_existing_general_allowlist_is_preserved_without_photo_expansion(company):
    database,cfg,args,_,_=company
    database.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,'{models}','[\"legacy-text\"]'::jsonb) WHERE tenant_id=%s::uuid AND provider='chat_ai'",(A,));database.commit()
    configure(database)
    saved=database.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(A,))[0]['metadata']
    assert saved['models']==['legacy-text'] and saved['photo_model']=='gpt-4.1'


def test_empty_photo_setting_keeps_legacy_consent_configuration_fingerprint(company):
    database,cfg,args,_,_=company
    before=preflight.config_hash(database.cursor(),cfg,'company','whisper-1')
    configure(database,'')
    assert preflight.config_hash(database.cursor(),cfg,'company','whisper-1')==before


def test_configured_photo_preflight_reports_budget_and_price_before_card(company,monkeypatch):
    database,cfg,args,_,_=company
    configure(database)
    selected={k:args[k] for k in ('message_id','channel_id','lead_id','photo_model')}
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:8896}))
    state=preflight.preflight(database.cursor(),cfg,selected)
    assert state['photo_tariff']['ready'] and state['photo_budget']=={'ready':False,'reason_code':'photo_model_budget_exhausted'}
    monkeypatch.setenv('AI_MODEL_PRICES_MICROUSD_PER_MILLION','{}')
    state=preflight.preflight(database.cursor(),cfg,selected)
    assert state['reason_code']=='photo_model_tariff_required'
    assert state['photo_tariff']['ready'] is False
    with pytest.raises(TenantLinearError,match='photo_model_tariff_required'):consent.prepare(database.cursor(),cfg,args)
    database.rollback()
