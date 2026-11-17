"""Independent native PostgreSQL connections; disposable localhost database only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, PROFILE
from test_call_recordings import pilot
from test_browser_calls import gateway, command, snapshot, calls, TenantLinearError


def test_simultaneous_claims_and_cancelled_start(gateway,monkeypatch):
    db,key,cfg=gateway
    monkeypatch.setattr(calls,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    def race(commands):
        gate=Barrier(len(commands))
        def act(cmd):
            gate.wait(10)
            try:return calls.action(PROFILE,cmd)
            except TenantLinearError as error:
                assert error.status==409
                return None
        with ThreadPoolExecutor(max_workers=len(commands)) as pool:return list(pool.map(act,commands))
    cmd=command();values=race([cmd,cmd]);assert values[0]['id']==values[1]['id']
    assert len(db.execute('SELECT id FROM saas_browser_calls'))==1;db.commit()
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    calls.sync(cfg,snapshot(3,results=[{'id':work['id'],'ok':False,'not_started':True,'error_code':'busy'}]))
    cmd=command();race([cmd,{**cmd,'op':'end'}])
    # Either cancellation wins before insertion, or the sole row is ending.
    rows=db.execute('SELECT state FROM saas_browser_calls WHERE client_request=%s::uuid',(cmd['request_id'],));db.commit()
    assert not rows or rows==[{'state':'ending'}]
    try:calls.action(PROFILE,cmd)
    except TenantLinearError:pass
    else:raise AssertionError('Cancelled request restarted')
