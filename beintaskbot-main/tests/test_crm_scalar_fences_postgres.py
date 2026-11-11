"""Optional real PostgreSQL scalar dispatch fences; never a provider call."""
import threading
from concurrent.futures import ThreadPoolExecutor
import psycopg
from psycopg.rows import dict_row
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db
from test_tenant_crm_relation_delivery_postgres import graph
from test_crm_scoped_scalar_postgres import scalar,edit,Remote
from tenant_crm_scalar_delivery import dispatch


@pytest.mark.parametrize('mutation',[
    "UPDATE saas_tenant_members SET active=false WHERE telegram_id=81",
    "UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'",
    "UPDATE saas_crm_provider_settings SET sync_direction='off'",
    "UPDATE saas_crm_native_deals SET title='Another actor',updated_at=clock_timestamp()",
])
def test_scalar_dispatch_holds_grant_connection_policy_and_revision_fences(scalar,mutation):
    row=edit(scalar);factory=lambda:psycopg.connect(scalar['db'].dsn,row_factory=dict_row)
    entered,release,started,finished=[threading.Event() for _ in range(4)]
    class BlockingRemote(Remote):
        def __call__(self,binding,method,path,**kwargs):
            if method=='PATCH':
                entered.set();assert release.wait(10)
            return super().__call__(binding,method,path,**kwargs)
    remote=BlockingRemote(scalar['binding'])
    def change():
        with factory() as conn:
            conn.execute("SET lock_timeout='8s'");started.set();conn.execute(mutation)
        finished.set()
    with ThreadPoolExecutor(2) as pool:
        delivery=pool.submit(dispatch,factory,row['sync']['event_id'],remote)
        try:
            assert entered.wait(10)
            writer=pool.submit(change)
            assert started.wait(3) and not finished.wait(.2)
        finally:release.set()
        assert delivery.result(10)=='synced';writer.result(10)
    assert finished.is_set() and len(remote.writes)==1
