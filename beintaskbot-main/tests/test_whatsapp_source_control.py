"""Synthetic source stop/resume; no live engine or provider calls."""
import pytest
from unittest.mock import Mock
from test_whatsapp_integration import setup
from test_whatsapp_native_rollout import staged_source
from test_call_sources import managed
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE
import tenant_whatsapp_integration as app
import tenant_whatsapp_source_control as control
import tenant_call_sources as sources
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def active(setup,monkeypatch):
    db,account,legacy,remote=setup
    phone='994100000777'
    source=app.begin(PROFILE,phone,'Synthetic')['channel_id']
    remote.return_value={'connected':True,'account_jid':phone+'@s.whatsapp.net'}
    app.activate(PROFILE,source)
    row=db.execute('SELECT s.*,r.generation FROM saas_browser_call_sources s JOIN saas_whatsapp_native_routes r USING(tenant_id,channel_id)')[0];db.commit()
    good={'connected':True,'account_jid':row['account_jid'],'messaging_generation':row['generation'],
          'diagnostics':dict(active_registry_calls=0,provider_start_requested=0,audio_attached=0),
          'messaging_diagnostics':dict(unknown_sends=0,pending_events=0)}
    provider=Mock()
    provider.side_effect=lambda path,payload=None: {'stopped':True,'scope':payload['scope']} if path.endswith('/disconnect') else {'public_key':row['public_key']} if payload else good
    monkeypatch.setattr(control,'_connect',lambda:db)
    monkeypatch.setattr(control,'_fernet',sources._fernet)
    monkeypatch.setattr(control,'remote',provider)
    return db,source,row,good,provider

def test_stop_same_source_preserves_history_grants_and_resume(active):
    db,source,row,good,provider=active
    history=db.execute('SELECT id,external_id FROM saas_crm_messages ORDER BY id')
    other=db.execute('SELECT * FROM saas_connect_channels WHERE tenant_id=%s',(B,))
    grants=db.execute('SELECT * FROM saas_dialog_source_members ORDER BY tenant_id,source_id,employee_id');db.commit()
    assert control.disconnect(PROFILE,[source])=={'stopped':[source]}
    state=db.execute('SELECT paused,enabled FROM saas_whatsapp_native_routes')[0];db.commit()
    assert state=={'paused':True,'enabled':False}
    assert app.inventory(PROFILE)['channels'][0]['state']=='stopped'
    provider.reset_mock()
    assert control.disconnect(PROFILE,[source])=={'stopped':[source]}
    provider.assert_not_called()
    resumed=control.resume(PROFILE,source)
    assert resumed['state']=='ready' and resumed['activation_pending']
    assert provider.call_args.args[1]['resume'] is True
    app.activate(PROFILE,source)
    assert app.inventory(PROFILE)['channels'][0]['messages_ready']
    assert db.execute('SELECT id,external_id FROM saas_crm_messages ORDER BY id')==history
    assert db.execute('SELECT * FROM saas_connect_channels WHERE tenant_id=%s',(B,))==other
    assert db.execute('SELECT * FROM saas_dialog_source_members ORDER BY tenant_id,source_id,employee_id')==grants;db.commit()

@pytest.mark.parametrize('section,key',[('diagnostics','active_registry_calls'),('diagnostics','provider_start_requested'),('messaging_diagnostics','unknown_sends'),('messaging_diagnostics','pending_events')])
def test_stop_refuses_active_or_unknown_operations(active,section,key):
    db,source,row,good,provider=active
    good[section][key]=1
    with pytest.raises(TenantLinearError,match='незавершённые'):control.disconnect(PROFILE,[source])
    assert db.execute('SELECT enabled,paused FROM saas_whatsapp_native_routes')[0]=={'enabled':True,'paused':False};db.commit()
    assert not any(c.args[0].endswith('/disconnect') for c in provider.call_args_list)

def test_stop_unknown_outcome_keeps_fence_and_retry_uses_same_identity(active):
    db,source,row,good,provider=active
    original=provider.side_effect
    provider.side_effect=lambda path,payload=None: {} if path.endswith('/disconnect') else original(path,payload)
    with pytest.raises(TenantLinearError,match='Остановка ещё'):control.disconnect(PROFILE,[source])
    assert app.inventory(PROFILE)['channels'][0]['state']=='stopping'
    with pytest.raises(TenantLinearError):control.resume(PROFILE,source)
    provider.side_effect=original
    assert control.disconnect(PROFILE,[source])=={'stopped':[source]}

def test_stop_cross_tenant_and_changed_all_list_denied(active):
    db,source,row,good,provider=active
    with pytest.raises(TenantLinearError):control.disconnect({**PROFILE,'tenant_id':B},[source])
    with pytest.raises(TenantLinearError):control.disconnect(PROFILE,[source,CA],all_sources=True)
    provider.assert_not_called()

