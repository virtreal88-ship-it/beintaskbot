import uuid
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from tenant_linear_policy import TenantLinearError
import tenant_operator_voice_context as voice


TENANT=str(uuid.uuid4());CHANNEL=str(uuid.uuid4());OTHER=str(uuid.uuid4())
ROUTE={'provider':'connect','lead_id':41,'channel_id':CHANNEL,'conversation_id':'123456789@s.whatsapp.net'}
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
    monkeypatch.setattr(voice,'assert_route',Mock(return_value={'lead_id':41,'conversation_kind':'contact'}))
    monkeypatch.setattr(voice,'conflicted',Mock(return_value=False))
    monkeypatch.setattr(voice,'_assigned_context',Mock(return_value={'agent_id':OTHER,'model':'existing-model'}))
    monkeypatch.setattr(voice,'_agent_binding',Mock(return_value='binding-1'))
    return policy


def test_admission_uses_scoped_membership_route_consent_and_existing_binding(boundaries):
    cur=Cursor();scope=voice.authorize(cur,PROFILE,ROUTE)
    assert (scope.tenant,scope.actor,scope.channel_id,scope.conversation_id,scope.lead_id)==(TENANT,7,CHANNEL,ROUTE['conversation_id'],41)
    assert scope.agent_binding=='binding-1' and scope.config_version=='version-1'
    voice.access.assert_called_once_with(cur,PROFILE,'dialogs')
    voice.assert_route.assert_called_once_with(cur,TENANT,boundaries,'connect',ROUTE['conversation_id'],CHANNEL,41)
    assert all('secrets,' not in sql.lower() for sql,_ in cur.calls)
    with pytest.raises(TenantLinearError):voice.context(scope)


def test_voice_preflight_refuses_group_identity_and_group_route(boundaries):
    group={**ROUTE,'conversation_id':'123456789-123456789@g.us'}
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,group)
    voice.access.assert_not_called()
    # Also reject a group marked by the stored route, even if its identifier
    # is opaque and the provider JID predicate cannot identify it.
    voice.assert_route.return_value={'lead_id':41,'conversation_kind':'GrOuP'}
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,ROUTE)
    for kind in ('contact','direct','personal','CONTACT'):
        voice.assert_route.return_value={'lead_id':41,'conversation_kind':kind}
        assert voice.authorize(Cursor(),PROFILE,ROUTE).conversation_id==ROUTE['conversation_id']


@pytest.mark.parametrize('conversation',['opaque','123456789-123456789@G.US','123456789@g.us',
                                         '123456789@broadcast','123456789@UNKNOWN',''])
def test_voice_preflight_refuses_unsupported_or_untyped_identity(boundaries,conversation):
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,{**ROUTE,'conversation_id':conversation})


@pytest.mark.parametrize('kind',[None,'','unknown','group','untrusted'])
def test_voice_preflight_requires_known_contact_route_kind(boundaries,kind):
    voice.assert_route.return_value={'lead_id':41,'conversation_kind':kind}
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,ROUTE)


@pytest.mark.parametrize('conversation',['123456789@s.whatsapp.net','123456789@c.us','123456789@lid'])
def test_voice_preflight_accepts_supported_direct_identity(boundaries,conversation):
    assert voice.authorize(Cursor(),PROFILE,{**ROUTE,'conversation_id':conversation}).conversation_id==conversation


@pytest.mark.parametrize('disabled',['enabled','consent','audio_enabled','copilot_enabled'])
def test_company_flags_fail_closed(boundaries,disabled):
    config={'enabled':True,'consent':True,'audio_enabled':True,'copilot_enabled':True};config[disabled]=False
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(config),PROFILE,ROUTE)


def test_route_mismatch_permission_conflict_and_emergency_stop(boundaries):
    voice.assert_route.return_value={'lead_id':42}
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,ROUTE)
    voice.assert_route.return_value={'lead_id':41,'conversation_kind':'contact'};boundaries.can_use_chat_ai=lambda:False
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,ROUTE)
    boundaries.can_use_chat_ai=lambda:True;voice.conflicted.return_value=True
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,ROUTE)
    voice.conflicted.return_value=False
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(stopped=True),PROFILE,ROUTE)


@pytest.mark.parametrize('changes',[{'provider':'kommo'},{'channel_id':'wrong'},{'lead_id':True},{'lead_id':41.9},
                                   {'lead_id':'041'},{'conversation_id':'bad '},{'conversation_id':'bad\x00value'},{'extra':'x'}])
def test_browser_cannot_inject_or_change_scope(boundaries,changes):
    with pytest.raises(TenantLinearError):voice.authorize(Cursor(),PROFILE,{**ROUTE,**changes})
