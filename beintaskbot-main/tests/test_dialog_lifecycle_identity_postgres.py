"""Native transaction interleavings, disposable localhost fixture only."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, CA, PROFILE
from test_dialog_lifecycle_pglite import episodes, payload
import tenant_connect_dialog_equivalence as eq
import tenant_dialog_lifecycle as lifecycle
from tenant_linear_policy import TenantLinearError


def setup_pair(episodes, monkeypatch):
    import tenant_platform
    monkeypatch.setattr(tenant_platform,'_connect',lambda:psycopg.connect(episodes.dsn,row_factory=dict_row))
    with episodes.cursor() as cur:eq.schema(cur)
    episodes.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'other',45)",(A,CA))
    episodes.commit()


def insert_link(cur):
    # Test transaction performs only the structural mutation under the real
    # guard, avoiding unrelated provider-proof setup. No production API bypass.
    eq.lock(cur,A,CA,['client','other'])
    cur.execute('''INSERT INTO saas_connect_dialog_links
      (tenant_id,channel_id,source_id,canonical_id,source_lead,canonical_lead,pair_id,snapshot)
      VALUES(%s::uuid,%s::uuid,'client','other',41,45,'identity-test','{}'::jsonb)''',(A,CA))


def test_native_claim_wins_identity_change_is_rejected(episodes,monkeypatch):
    setup_pair(episodes,monkeypatch)
    import tenant_ai_dialog_scope as scope
    original=scope.lock_dialog;held=threading.Event();release=threading.Event()
    def gated(cur,*args):
        original(cur,*args)
        if threading.current_thread().name.startswith('claim') and not held.is_set():
            held.set();assert release.wait(10)
    monkeypatch.setattr(scope,'lock_dialog',gated)
    def merge():
        from tenant_dialog_rollout import guard
        with psycopg.connect(episodes.dsn,row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                guard(cur,A)
                with pytest.raises(TenantLinearError):insert_link(cur)
    with ThreadPoolExecutor(1,thread_name_prefix='claim') as claims, ThreadPoolExecutor(1) as merges:
        claim=claims.submit(lifecycle.mutate,PROFILE,payload())
        try:
            assert held.wait(10)
            merging=merges.submit(merge)
        finally:release.set()
        claim.result(timeout=15);merging.result(timeout=15)
    assert not episodes.execute('SELECT * FROM saas_connect_dialog_links')
    episodes.commit()


def test_native_merge_wins_stale_claim_does_not_create_orphan(episodes,monkeypatch):
    setup_pair(episodes,monkeypatch)
    import tenant_operator_access as access
    original=access._resolve_connect_route;resolved=threading.Event();release=threading.Event()
    def gated(*args,**kwargs):
        result=original(*args,**kwargs)
        if threading.current_thread().name.startswith('claim') and not resolved.is_set():
            resolved.set();assert release.wait(10)
        return result
    monkeypatch.setattr(access,'_resolve_connect_route',gated)
    with ThreadPoolExecutor(1,thread_name_prefix='claim') as pool:
        claim=pool.submit(lifecycle.mutate,PROFILE,payload())
        try:
            assert resolved.wait(10)
            from tenant_dialog_rollout import guard
            with psycopg.connect(episodes.dsn,row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    guard(cur,A);insert_link(cur)
        finally:release.set()
        with pytest.raises(TenantLinearError):claim.result(timeout=15)
    assert not episodes.execute('SELECT * FROM saas_dialog_workflows')
    assert not episodes.execute('SELECT * FROM saas_dialog_episodes')
    episodes.commit()


def test_native_rollback_cannot_split_claimed_component(episodes,monkeypatch):
    setup_pair(episodes,monkeypatch)
    with episodes.cursor() as cur:
        from tenant_dialog_rollout import guard
        guard(cur,A);insert_link(cur)
    episodes.commit()
    lifecycle.mutate(PROFILE,{**payload(),'conversation_id':'other','lead_id':45})
    with psycopg.connect(episodes.dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            guard(cur,A)
            with pytest.raises(TenantLinearError):eq.rollback(cur,A,CA,'identity-test')
    assert episodes.execute('SELECT active FROM saas_connect_dialog_links')==[{'active':True}]
    episodes.commit()
