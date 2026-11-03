import json
import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from tenant_linear_policy import TenantLinearError
import tenant_operator_voice_context as voice
import tenant_operator_copilot as copilot


TENANT=str(uuid.uuid4());CHANNEL=str(uuid.uuid4());OTHER=str(uuid.uuid4())
ROUTE={'provider':'connect','lead_id':41,'channel_id':CHANNEL,'conversation_id':'synthetic-dialog'}
PROFILE={'tenant_id':TENANT,'telegram_id':7}


class Cursor:
    def __init__(self,config=None,stopped=False):
        self.config=config if config is not None else {'enabled':True,'consent':True,'audio_enabled':True,'copilot_enabled':True}
        self.stopped=stopped;self.calls=[]
    def execute(self,sql,args): self.calls.append((sql,args))
    def fetchone(self):
        sql=self.calls[-1][0]
        if 'saas_tenant_integrations' in sql:return {'metadata':self.config,'configured':True,'updated_at':'version-1'}
        if 'saas_ai_agent_settings' in sql:return {'emergency_stop':self.stopped}
        raise AssertionError('unexpected SQL')


@pytest.fixture
def boundaries(monkeypatch):
    policy=SimpleNamespace(can_use_chat_ai=lambda:True)
    monkeypatch.setattr(voice,'access',Mock(return_value=(TENANT,7,policy)))
    monkeypatch.setattr(voice,'assert_route',Mock(return_value={'lead_id':41}))
    monkeypatch.setattr(voice,'conflicted',Mock(return_value=False))
    monkeypatch.setattr(voice,'_assigned_context',Mock(return_value={'agent_id':OTHER,'model':'existing-model'}))
    monkeypatch.setattr(voice,'_agent_binding',Mock(return_value='binding-1'))
    return policy


def test_admission_uses_scoped_membership_route_consent_and_existing_binding(boundaries):
    cur=Cursor();scope=voice.authorize(cur,PROFILE,ROUTE)
    assert (scope.tenant,scope.actor,scope.channel_id,scope.conversation_id,scope.lead_id)==(TENANT,7,CHANNEL,'synthetic-dialog',41)
    assert scope.agent_binding=='binding-1' and scope.config_version=='version-1'
    voice.access.assert_called_once_with(cur,PROFILE,'dialogs')
    voice.assert_route.assert_called_once_with(cur,TENANT,boundaries,'connect','synthetic-dialog',CHANNEL,41)
    assert all('secrets,' not in sql.lower() for sql,_ in cur.calls)
    with pytest.raises(TenantLinearError):voice.context(scope)


@pytest.mark.parametrize('disabled',['enabled','consent','audio_enabled','copilot_enabled'])
def test_company_flags_fail_closed(boundaries,disabled):
    config={'enabled':True,'consent':True,'audio_enabled':True,'copilot_enabled':True};config[disabled]=False
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(config),PROFILE,ROUTE)


def test_route_mismatch_permission_conflict_and_emergency_stop(boundaries):
    voice.assert_route.return_value={'lead_id':42}
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,ROUTE)
    voice.assert_route.return_value={'lead_id':41};boundaries.can_use_chat_ai=lambda:False
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,ROUTE)
    boundaries.can_use_chat_ai=lambda:True;voice.conflicted.return_value=True
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,ROUTE)
    voice.conflicted.return_value=False
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(stopped=True),PROFILE,ROUTE)


@pytest.mark.parametrize('changes',[{'provider':'kommo'},{'channel_id':'wrong'},{'lead_id':True},{'conversation_id':'bad '},{'extra':'x'}])
def test_browser_cannot_inject_or_change_scope(boundaries,changes):
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,{**ROUTE,**changes})


def test_context_only_chat_never_loads_media_calls_provider_or_writes_finish(monkeypatch):
    import tenant_episode_media
    import tenant_ai_agent_run_store
    execution=SimpleNamespace(source_refs=[])
    monkeypatch.setattr(tenant_episode_media,'ManualExecution',Mock(return_value=execution))
    knowledge=Mock(return_value=[]);monkeypatch.setattr(tenant_ai_agent_run_store,'knowledge_context',knowledge)
    media=Mock(side_effect=AssertionError('media not allowed'));finish=Mock(side_effect=AssertionError('write not allowed'))
    provider=Mock(side_effect=AssertionError('provider not allowed'))
    monkeypatch.setattr(copilot,'_prepare_copilot_media',media);monkeypatch.setattr(copilot,'_finish',finish);monkeypatch.setattr(copilot,'generate',provider)
    cmd={'mode':'chat','prompt':'','lead_id':41,'channel_id':CHANNEL,'conversation_id':'synthetic-dialog','_agent_id':OTHER}
    rows=[{'external_id':'synthetic','direction':'incoming','body':'synthetic question','message_type':'text','raw':{}}]
    agent={'model':'existing-model','agent_id':OTHER,'language':'auto'}
    result=copilot._draft_context(TENANT,7,cmd,{}, {'updated_at':'v1'}, {'api_key':'NOT_A_REAL_KEY'},'existing-model',agent,rows,[],context_only=True)
    assert 'Never send messages' in result['instructions'];assert knowledge.call_args.kwargs['_read_only'] is True
    # The base text system prompt is byte-identical to fc75c28; no voice-only
    # addition appears in a context that contains no voice-marked turns.
    assert hashlib.sha256(copilot.INSTRUCTIONS.encode()).hexdigest()=='ff97d5d35675f74caa84e53ec1e5587ed487c75233bcb6b531a1f9292fc04568'
    assert result['instructions']==copilot.INSTRUCTIONS+copilot.assigned_guidance(company='',agent='',style=None)
    assert set(result)=={'instructions','lines','extra_context'} and 'NOT_A_REAL_KEY' not in json.dumps(result)
    media.assert_not_called();provider.assert_not_called();finish.assert_not_called()
    with pytest.raises(TenantLinearError):copilot._draft_context(TENANT,7,{**cmd,'mode':'reply'},{},{},{},'m',agent,rows,[],context_only=True)
