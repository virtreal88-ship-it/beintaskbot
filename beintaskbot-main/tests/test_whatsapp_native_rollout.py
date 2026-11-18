"""Explicit source transition on synthetic SQL; no live provider requests."""
from unittest.mock import Mock
from cryptography.fernet import Fernet
import pytest
from test_call_sources import managed
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE
from tenant_linear_policy import TenantLinearError
import tenant_whatsapp_native_rollout as rollout
import tenant_call_sources as sources
import tenant_connect_transport as transport


@pytest.fixture
def staged_source(managed,monkeypatch):
    db,_,cfg=managed
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr(rollout,'_connect',lambda:db)
    monkeypatch.setattr(rollout,'_fernet',lambda:cipher)
    monkeypatch.setattr(sources,'_fernet',lambda:cipher)
    monkeypatch.setattr(transport,'_stored_connect_credentials',lambda _: {'api_key':'synthetic'})
    legacy=Mock(return_value={'id':CA,'status':'disabled','phone':cfg['account_jid'].split('@')[0]})
    monkeypatch.setattr(transport,'_request',legacy)
    remote=Mock(return_value={'public_key':'a'*64})
    monkeypatch.setattr(rollout,'remote',remote)
    with db.cursor() as cur:rollout.migrate(cur)
    db.execute('DELETE FROM saas_browser_call_sources')
    db.execute("UPDATE saas_connect_channels SET active=FALSE,status='disconnected' WHERE tenant_id=%s AND connect_channel_id=%s",(A,CA));db.commit()
    yield db,cfg['account_jid'],legacy,remote
    db.rollback()
    db.execute('DELETE FROM saas_whatsapp_native_routes')
    db.execute('DELETE FROM saas_whatsapp_native_status')
    db.execute('DELETE FROM saas_whatsapp_native_inbox')
    db.execute('DELETE FROM saas_whatsapp_capture_generations');db.commit()


def test_stage_is_durable_without_activating_history_or_sends(staged_source):
    db,account,legacy,remote=staged_source
    history=db.execute('SELECT id,external_id FROM saas_crm_messages ORDER BY id');db.commit()
    first=rollout.stage(PROFILE,CA,account,'synthetic-g1')
    row=db.execute('SELECT gateway_id,encrypted_token FROM saas_browser_call_sources')[0];db.commit()
    assert rollout.stage(PROFILE,CA,account,'synthetic-g1')==first
    assert db.execute('SELECT gateway_id,encrypted_token FROM saas_browser_call_sources')[0]==row
    assert db.execute('SELECT enabled FROM saas_whatsapp_native_routes')[0]['enabled'] is False
    assert db.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s',(A,CA))[0]['active'] is False
    assert db.execute('SELECT id,external_id FROM saas_crm_messages ORDER BY id')==history
    with db.cursor() as cur:assert sources._owner(cur,PROFILE,CA)==(A,11)
    db.commit()
    assert remote.call_args.args[1]['messaging_generation']=='synthetic-g1'


@pytest.mark.parametrize('change',[{'status':'connected'},{'phone':'999999999'},{'phoneConflict':True}])
def test_stage_refuses_unproven_old_owner(staged_source,change):
    db,account,legacy,remote=staged_source
    legacy.return_value={**legacy.return_value,**change}
    with pytest.raises(TenantLinearError,match='native_legacy_not_disabled'):
        rollout.stage(PROFILE,CA,account,'synthetic-g1')
    assert not db.execute('SELECT 1 FROM saas_whatsapp_native_routes');db.commit()
    remote.assert_not_called()


def test_activate_requires_exact_paired_number_and_retains_other_tenant(staged_source,monkeypatch):
    db,account,legacy,remote=staged_source
    rollout.stage(PROFILE,CA,account,'synthetic-g1')
    for flag in ('INBOX','PROJECTION','SEND'):monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_'+flag,'1')
    other=db.execute('SELECT * FROM saas_connect_channels WHERE tenant_id=%s',(B,));db.commit()
    remote.return_value={'connected':True,'account_jid':'999999999@s.whatsapp.net'}
    with pytest.raises(TenantLinearError,match='native_pairing_unconfirmed'):
        rollout.activate(PROFILE,CA,'synthetic-g1')
    assert not db.execute('SELECT enabled FROM saas_whatsapp_native_routes')[0]['enabled'];db.commit()
    remote.return_value={'connected':True,'account_jid':account}
    assert rollout.activate(PROFILE,CA,'synthetic-g1')['state']=='active'
    assert db.execute('SELECT enabled,projection_enabled FROM saas_whatsapp_capture_generations')[0]=={'enabled':True,'projection_enabled':True}
    assert db.execute('SELECT * FROM saas_connect_channels WHERE tenant_id=%s',(B,))==other;db.commit()
    with pytest.raises(TenantLinearError):rollout.stage({'tenant_id':B,'telegram_id':11},CA,account,'synthetic-g1')
