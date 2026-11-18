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
import tenant_browser_calls as calls
import tenant_call_sources as sources
import tenant_platform
import tenant_connect_transport as transport
import tenant_whatsapp_native_transition as transition
from tenant_linear_policy import TenantLinearError


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
