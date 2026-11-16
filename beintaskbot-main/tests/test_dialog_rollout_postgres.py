"""Real multi-process rollout fences; disposable localhost DB ONLY, no providers."""
import multiprocessing
import os
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from tenant_dialog_rollout import migrate, guard, transition
from tenant_linear_policy import TenantLinearError
A='00000000-0000-0000-0000-000000000001'
B='00000000-0000-0000-0000-000000000002'

@pytest.fixture
def rollout_dsn():
    base=os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
    if not base:pytest.skip('Disposable localhost PostgreSQL DSN required')
    if conninfo_to_dict(base).get('host') not in {'127.0.0.1','localhost','::1'}:
        raise RuntimeError('Local disposable test server only')
    name='test_rollout_'+uuid.uuid4().hex
    with psycopg.connect(base,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
    dsn=make_conninfo(base,dbname=name)
    try:
        with psycopg.connect(dsn,row_factory=dict_row) as conn:
            conn.execute('CREATE TABLE saas_tenants(id UUID PRIMARY KEY)')
            conn.execute('INSERT INTO saas_tenants VALUES(%s),(%s)',(A,B))
            with conn.cursor() as cur:migrate(cur)
        yield dsn
    finally:
        with psycopg.connect(base,autocommit=True) as admin:
            admin.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(psycopg.sql.Identifier(name)))

def held_fence(dsn,ready,release,out,exclusive=False):
    try:
        with psycopg.connect(dsn,row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                if exclusive:transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
                else:guard(cur,A)
                ready.set()
                if not release.wait(15):raise RuntimeError('test parent did not release fence')
        out.put('ok')
    except BaseException as e:out.put((type(e).__name__,str(e)));ready.set()

@pytest.fixture
def held_process(rollout_dsn):
    processes=[]
    def start(exclusive=False):
        ctx=multiprocessing.get_context('spawn');ready=ctx.Event();release=ctx.Event();out=ctx.Queue()
        p=ctx.Process(target=held_fence,args=(rollout_dsn,ready,release,out,exclusive));p.start()
        processes.append((p,release,out));assert ready.wait(15)
        return release
    yield start
    for p,release,out in processes:
        release.set();p.join(15)
        if p.is_alive():p.terminate();p.join(5);raise AssertionError('fence child hung')
        assert p.exitcode==0
        assert out.get(timeout=3)=='ok'

def test_inflight_legacy_send_fences_only_its_tenant(rollout_dsn,held_process):
    release=held_process()
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            with pytest.raises(TenantLinearError) as error:
                transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
            assert error.value.status==409
            assert not guard(cur,A).enforced
            # Another tenant's activation is independent of A's network work.
            assert transition(cur,B,mode='active',admission_enabled=True,expected_generation=0).enforced
    release.set()

def test_activation_in_progress_blocks_new_dispatch_without_waiting(rollout_dsn,held_process):
    release=held_process(exclusive=True)
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            with pytest.raises(TenantLinearError) as error:guard(cur,A)
            assert error.value.status==409
            assert not guard(cur,B).enforced
    release.set()

def test_latch_survives_env_removal_and_restart_connection(rollout_dsn,monkeypatch):
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    monkeypatch.delenv('TENANT_DIALOG_LIFECYCLE_ENABLED',raising=False)
    monkeypatch.setenv('TENANT_DIALOG_ACCEPT_ENABLED','false')
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            state=guard(cur,A);assert state.enforced and state.admission_enabled
    # Fresh exclusive transaction, never shared->exclusive lock upgrade.
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            stopped=transition(cur,A,mode='active',admission_enabled=False,expected_generation=1)
            assert stopped.enforced and not stopped.admission_enabled
            with pytest.raises(TenantLinearError):transition(cur,A,mode='legacy',admission_enabled=False,expected_generation=2)
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:assert guard(cur,A).enforced

def test_shadow_keeps_legacy_behavior_and_first_activation_is_atomic(rollout_dsn):
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:transition(cur,A,mode='shadow',admission_enabled=False,expected_generation=0)
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            state=guard(cur,A);assert state.mode=='shadow' and not state.enforced
    with psycopg.connect(rollout_dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            with pytest.raises(TenantLinearError):transition(cur,A,mode='active',admission_enabled=False,expected_generation=1)
            result=transition(cur,A,mode='active',admission_enabled=True,expected_generation=1)
            assert result.enforced and result.admission_enabled and result.generation==2
