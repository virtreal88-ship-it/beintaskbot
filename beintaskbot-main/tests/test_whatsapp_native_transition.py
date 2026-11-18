"""Exact-source maintenance with synthetic PostgreSQL data, no live sends."""
from unittest.mock import Mock
import pytest
from test_call_sources import managed
from test_browser_calls import gateway,command
from test_call_recordings import pilot
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE
import tenant_whatsapp_native_transition as transition
import tenant_whatsapp_native_rollout as rollout
import tenant_connect_transport as transport
import tenant_whatsapp_native_transport as native
import tenant_browser_calls as calls
from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError


@pytest.fixture
def transition_source(managed,monkeypatch):
    db,_,cfg=managed
    for module in (transition,rollout,transport):monkeypatch.setattr(module,'_connect',lambda:db)
    monkeypatch.setattr(transport,'_stored_connect_credentials',lambda _: {'api_key':'synthetic'})
    legacy=Mock(return_value={'id':CA,'status':'connected','phone':cfg['account_jid'].split('@')[0]})
    monkeypatch.setattr(transport,'_request',legacy)
    remote=Mock(return_value={'connected':True,'account_jid':cfg['account_jid'],
        'diagnostics':{'active_registry_calls':0,'provider_start_requested':0,'audio_attached':0}})
    monkeypatch.setattr(transition,'remote',remote)
    for flag in ('INBOX','PROJECTION','SEND'):monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_'+flag,'1')
    with db.cursor() as cur:rollout.migrate(cur)
    db.commit()
    yield db,cfg,legacy,remote
    db.rollback()
    db.execute('DELETE FROM saas_whatsapp_native_routes')
    db.execute('DELETE FROM saas_whatsapp_native_status')
    db.execute('DELETE FROM saas_whatsapp_native_inbox')
    db.execute('DELETE FROM saas_whatsapp_capture_generations');db.commit()


def prepare(cfg):return transition.prepare(PROFILE,CA,cfg['account_jid'],'live-g1')


def test_prepare_preserves_source_history_and_fences_stale_call_admission(transition_source):
    db,cfg,legacy,remote=transition_source
    channels=db.execute('SELECT * FROM saas_connect_channels ORDER BY tenant_id,connect_channel_id')
    history=db.execute('SELECT id,external_id FROM saas_crm_messages ORDER BY id');db.commit()
    manifest=prepare(cfg)
    assert set(manifest)=={'gateway_id','tenant_id','channel_id','account_jid','public_key','generation'}
    assert prepare(cfg)==manifest
    assert db.execute('SELECT * FROM saas_connect_channels ORDER BY tenant_id,connect_channel_id')==channels
    assert db.execute('SELECT id,external_id FROM saas_crm_messages ORDER BY id')==history;db.commit()
    # confirmed_account is mocked by managed fixture: this intentionally models
    # a call that passed its outer check before prepare committed the fence.
    with pytest.raises(TenantLinearError,match='переключается'):
        calls.action(PROFILE,command(media='websocket'))
    with db.cursor() as cur:
        with pytest.raises(TenantPlatformError,match='native_source_unavailable'):
            native.bind(cur,{'tenant_id':A,'channel_id':CA,'api_key':'legacy'})
    db.rollback()
    with pytest.raises(TenantLinearError):transition.prepare({'tenant_id':B,'telegram_id':11},CA,cfg['account_jid'],'live-g1')


@pytest.mark.parametrize('reason',['active_call','unknown_call','pending_command','wrong_account','runtime_busy','missing_diagnostics'])
def test_prepare_refuses_unconfirmed_closure(transition_source,reason):
    db,cfg,legacy,remote=transition_source
    if reason in {'active_call','unknown_call','pending_command'}:
        started=calls.action(PROFILE,command(media='websocket'))
        if reason=='unknown_call':db.execute("UPDATE saas_browser_calls SET state='unknown' WHERE id=%s",(started['id'],))
        if reason=='pending_command':db.execute("UPDATE saas_browser_calls SET state='ended' WHERE id=%s",(started['id'],))
        db.commit()
    elif reason=='wrong_account':remote.return_value['account_jid']='999999999@s.whatsapp.net'
    elif reason=='runtime_busy':remote.return_value['diagnostics']['provider_start_requested']=1
    else:remote.return_value.pop('diagnostics')
    with pytest.raises(TenantLinearError):prepare(cfg)
    assert not db.execute('SELECT 1 FROM saas_whatsapp_native_routes');db.commit()


def test_capture_and_activation_require_runtime_generation_durable_event_and_legacy_fence(transition_source):
    db,cfg,legacy,remote=transition_source
    prepare(cfg)
    with pytest.raises(TenantLinearError,match='generation_unconfirmed'):
        transition.enable_capture(PROFILE,CA,'live-g1')
    remote.return_value['messaging_generation']='live-g1'
    transition.enable_capture(PROFILE,CA,'live-g1')
    assert db.execute('SELECT enabled,projection_enabled FROM saas_whatsapp_capture_generations')[0]=={'enabled':True,'projection_enabled':False};db.commit()
    with pytest.raises(TenantLinearError,match='capture_unconfirmed'):transition.activate(PROFILE,CA,'live-g1')
    # Synthetic evidence cursor; production must receive an authenticated event.
    db.execute('UPDATE saas_whatsapp_capture_generations SET stored_sequence=1');db.commit()
    with pytest.raises(TenantLinearError,match='legacy_not_disabled'):transition.activate(PROFILE,CA,'live-g1')
    legacy.return_value['status']='disabled'
    assert transition.activate(PROFILE,CA,'live-g1')['state']=='active'
    assert transition.activate(PROFILE,CA,'live-g1')['state']=='active'
    with db.cursor() as cur:transition.admission(cur,A,CA)
    assert db.execute('SELECT enabled,projection_enabled FROM saas_whatsapp_capture_generations')[0]=={'enabled':True,'projection_enabled':True};db.commit()


def test_rollback_keeps_history_marker_and_rejects_stale_native_request(transition_source):
    db,cfg,legacy,remote=transition_source
    prepare(cfg)
    remote.return_value.update(messaging_generation='live-g1',messaging_diagnostics={'unknown_sends':0,'pending_events':0})
    transition.enable_capture(PROFILE,CA,'live-g1')
    db.execute('UPDATE saas_whatsapp_capture_generations SET stored_sequence=1');db.commit()
    legacy.return_value['status']='disabled'
    transition.activate(PROFILE,CA,'live-g1')
    with db.cursor() as cur:
        old_route=native.bind(cur,{'tenant_id':A,'channel_id':CA,'api_key':'legacy'})
    db.commit()
    transition.fence_for_rollback(PROFILE,CA,'live-g1')
    for key in ('unknown_sends','pending_events'):
        remote.return_value['messaging_diagnostics'][key]=1
        with pytest.raises(TenantLinearError):transition.restore_legacy(PROFILE,CA,'live-g1')
        remote.return_value['messaging_diagnostics'][key]=0
        assert db.execute('SELECT enabled,legacy_fallback FROM saas_whatsapp_native_routes')[0]=={'enabled':False,'legacy_fallback':False};db.commit()
    with pytest.raises(TenantLinearError,match='legacy_unconfirmed'):transition.restore_legacy(PROFILE,CA,'live-g1')
    legacy.return_value['status']='connected'
    assert transition.restore_legacy(PROFILE,CA,'live-g1')['state']=='legacy_restored'
    assert transition.restore_legacy(PROFILE,CA,'live-g1')['state']=='legacy_restored'
    with db.cursor() as cur:
        assert native.bind(cur,{'tenant_id':A,'channel_id':CA,'api_key':'legacy'})['api_key']=='legacy'
        with pytest.raises(TenantPlatformError,match='native_source_changed'):native.bind(cur,old_route)
        transition.admission(cur,A,CA)
    assert len(db.execute('SELECT 1 FROM saas_whatsapp_native_routes'))==1
    assert db.execute('SELECT projection_enabled FROM saas_whatsapp_capture_generations')[0]['projection_enabled'];db.commit()
