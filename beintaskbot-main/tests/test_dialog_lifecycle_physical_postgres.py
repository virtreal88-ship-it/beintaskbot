"""Native alias/table ordering race; synthetic webhook write, never provider I/O."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import uuid
import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from test_tenant_support_inbox import A, CA, PROFILE, db
from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_physical_identity import seed
import tenant_connect_direct_reconcile as reconcile


def test_webhook_alias_holder_can_write_before_physical_table_lock(db,monkeypatch):
    """Regression: table-first merge would block webhook while waiting its alias."""
    assert conninfo_to_dict(db.dsn)['host'] in {'localhost','127.0.0.1','::1'}
    ids,payload=seed(db,monkeypatch)
    from tenant_connect_direct_identity import _aliases
    first_alias=sorted(set().union(*(_aliases(i) for i in ids)))[0]
    key='connect-direct:'+json.dumps([str(A),str(CA),first_alias])
    ready=threading.Event(); state={}

    def physical():
        with psycopg.connect(db.dsn,row_factory=dict_row) as conn:
            state['pid']=conn.info.backend_pid
            ready.set()
            with conn.cursor() as cur:
                reconcile._owner(cur,PROFILE)
                result=reconcile._lock_pair(cur,A,payload['pair_id'],CA)
            return result

    with psycopg.connect(db.dsn,row_factory=dict_row) as webhook:
        webhook.execute("SET LOCAL lock_timeout='2s'")
        webhook.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(key,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(physical)
            try:
                assert ready.wait(5)
                # Observe PostgreSQL itself, not a timing guess, before writing.
                deadline=time.monotonic()+4
                with psycopg.connect(db.dsn,autocommit=True,row_factory=dict_row) as observer:
                    while True:
                        row=observer.execute('SELECT wait_event_type,wait_event FROM pg_stat_activity WHERE pid=%s',(state['pid'],)).fetchone()
                        if row and row['wait_event_type']=='Lock' and row['wait_event']=='advisory':break
                        assert not future.done(), 'Physical worker did not wait for the held alias'
                        assert time.monotonic()<deadline, 'Physical worker never reached alias wait'
                        time.sleep(.01)
                    locks=observer.execute("SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='relation' AND mode='ShareRowExclusiveLock' AND granted",(state['pid'],)).fetchall()
                    assert locks==[], 'Dependency tables locked before waiting for webhook alias'
                webhook.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,5101,%s,'connect:'||%s::text,'incoming','Synthetic webhook while alias held')",(str(uuid.uuid4()),A,'race:'+str(uuid.uuid4()),CA))
                webhook.commit()
                assert future.result(timeout=8)==(CA,ids)
            finally:
                webhook.rollback()  # release alias even when assertion fails
    assert db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND body='Synthetic webhook while alias held'",(A,))[0]['n']==1
