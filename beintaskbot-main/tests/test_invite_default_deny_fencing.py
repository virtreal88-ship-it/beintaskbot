"""Native PostgreSQL invitation/default-deny fencing regressions."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import psycopg
from psycopg.rows import dict_row
import pytest
import tenant_invite_store as invites
import tenant_member_store as members
from tenant_platform_core import TenantPlatformError
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A

@pytest.fixture
def prepared(db, monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    factory=lambda:psycopg.connect(db.dsn,row_factory=dict_row)
    monkeypatch.setattr(invites,'_connect',factory)
    monkeypatch.setattr(members,'_connect',factory)
    monkeypatch.setattr(invites,'_ensure_schema',lambda conn:None)
    invite,request=str(uuid.uuid4()),str(uuid.uuid4())
    db.execute("""INSERT INTO saas_tenant_invites(id,tenant_id,token_hash,display_name,role,permissions,created_by,expires_at)
      VALUES(%s::uuid,%s::uuid,'synthetic-fence','Synthetic','manager','["linear"]',11,now()+interval '1 day')""",(invite,A))
    db.execute("""INSERT INTO saas_tenant_invite_requests(id,invite_id,tenant_id,telegram_id,display_name)
      VALUES(%s::uuid,%s::uuid,%s::uuid,333,'Synthetic')""",(request,invite,A))
    db.commit()
    return db,factory,invite,request

@pytest.mark.parametrize('consumed',[False,True])
def test_reject_stale_approval(prepared,consumed):
    db,_,invite,request=prepared
    sql='accepted_at=now()' if consumed else "expires_at=now()-interval '1 day'"
    db.execute('UPDATE saas_tenant_invites SET '+sql+' WHERE id=%s::uuid',(invite,));db.commit()
    with pytest.raises(TenantPlatformError):
        invites.decide_invite_request(tenant_id=A,owner_id=11,request_id=request,approve=True)
    assert not db.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=333',(A,))

def test_approval_waits_and_reads_reset_grants(prepared):
    db,factory,invite,request=prepared
    with factory() as transition:
        transition.execute('SELECT id FROM saas_tenants WHERE id=%s::uuid FOR UPDATE',(A,))
        started=threading.Event()
        def approve():
            started.set()
            return invites.decide_invite_request(tenant_id=A,owner_id=11,request_id=request,approve=True)
        with ThreadPoolExecutor() as pool:
            future=pool.submit(approve)
            assert started.wait(3)
            time.sleep(.15)
            assert not future.done()
            transition.execute("UPDATE saas_tenant_invites SET permissions='[]'::jsonb WHERE id=%s::uuid",(invite,))
            transition.commit()
            assert future.result(timeout=5)['approved']
    assert db.execute('SELECT permissions FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=333',(A,))[0]['permissions']==[]

def test_acceptance_tenant_before_invite_fence(prepared):
    _,factory,invite,_=prepared
    with factory() as transition:
        transition.execute('SELECT id FROM saas_tenants WHERE id=%s::uuid FOR UPDATE',(A,))
        started=threading.Event()
        def fence():
            with factory() as conn,conn.cursor() as cur:
                started.set()
                return invites._lock_invite_tenant(cur,'synthetic-fence')
        with ThreadPoolExecutor() as pool:
            future=pool.submit(fence)
            assert started.wait(3)
            time.sleep(.15)
            assert not future.done()
            transition.execute("SET LOCAL lock_timeout='1s'")
            transition.execute('SELECT id FROM saas_tenant_invites WHERE id=%s::uuid FOR UPDATE',(invite,))
            transition.commit()
            assert future.result(timeout=5)==A
