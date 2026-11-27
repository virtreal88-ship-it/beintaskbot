"""Real independent PostgreSQL connections; only disposable localhost DB."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import psycopg
from psycopg.rows import dict_row
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,PROFILE,CA
from test_call_recordings import pilot
from test_browser_calls import gateway,command
from test_call_sources import managed
from test_whatsapp_native_transition import transition_source
from test_whatsapp_native_rollout import staged_source
from test_whatsapp_integration import setup
import tenant_browser_calls as calls
import tenant_call_sources as sources
import tenant_platform
import tenant_connect_transport as transport
import tenant_whatsapp_integration as integration
import tenant_whatsapp_source_control as control
import tenant_whatsapp_native_transition as transition
from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError


def test_maintenance_waits_for_call_admission_then_refuses_live_call(transition_source,monkeypatch):
    db,cfg,legacy,remote=transition_source
    def connect():return psycopg.connect(db.dsn,row_factory=dict_row,options='-c statement_timeout=8000')
    for module in (calls,sources,tenant_platform,transport,transition):monkeypatch.setattr(module,'_connect',connect)
    monkeypatch.setattr(calls,'configurations',lambda *a,**k:[cfg])
    entered=Event();resume=Event();completed=Event()
    original=calls._action
    def parked(*args):
        entered.set();assert resume.wait(5)
        return original(*args)
    monkeypatch.setattr(calls,'_action',parked)
    def prepare():
        try:return transition.prepare(PROFILE,CA,cfg['account_jid'],'race-g1')
        finally:completed.set()
    with ThreadPoolExecutor(max_workers=2) as workers:
        starting=workers.submit(calls.action,PROFILE,command(media='websocket'))
        try:
            assert entered.wait(5)
            preparing=workers.submit(prepare)
            assert not completed.wait(.3)
        finally:resume.set()
        assert starting.result(timeout=10)['state']=='starting'
        with pytest.raises(TenantLinearError,match='call_not_closed'):preparing.result(timeout=10)
    assert not db.execute('SELECT 1 FROM saas_whatsapp_native_routes')
    assert len(db.execute('SELECT id FROM saas_browser_calls'))==1;db.commit()


def test_removal_fences_a_stale_qr_activation(setup,monkeypatch):
    db,_,_,_=setup
    source=integration.begin(PROFILE,'994100000777','Synthetic')['channel_id']
    entered=Event()
    release=Event()
    with db.cursor() as cur:
        cur.execute('SELECT generation FROM saas_whatsapp_native_routes WHERE channel_id=%s',(source,))
        generation=cur.fetchone()['generation']
    db.commit()

    def connect():
        return psycopg.connect(db.dsn,row_factory=dict_row,options='-c statement_timeout=8000')

    def provider(path,payload=None):
        if path.endswith('/disconnect'):
            entered.set()
            assert release.wait(5)
            return {'stopped':True,'scope':payload['scope']}
        return {'messaging_generation':generation,
                'diagnostics':dict(active_registry_calls=0,provider_start_requested=0,audio_attached=0),
                'messaging_diagnostics':dict(unknown_sends=0,pending_events=0)}

    for module in (control,integration,sources,transport):
        monkeypatch.setattr(module,'_connect',connect)
    monkeypatch.setattr(control,'remote',provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        removing=pool.submit(control.remove,PROFILE,source)
        try:
            assert entered.wait(5)
            with pytest.raises(TenantPlatformError):
                integration.activate(PROFILE,source)
        finally:
            release.set()
        assert removing.result(timeout=10)=={'removed':source}
    with pytest.raises(TenantLinearError) as hidden:
        integration.activate(PROFILE,source)
    assert hidden.value.status==404
    assert integration.inventory(PROFILE)['channels']==[]
    assert db.execute('SELECT enabled,paused FROM saas_whatsapp_native_routes WHERE channel_id=%s',(source,))==[
        {'enabled':False,'paused':True}]
    db.commit()
