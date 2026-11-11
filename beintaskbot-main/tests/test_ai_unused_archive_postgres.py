"""Real PostgreSQL admission races; never PGlite/provider execution."""
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A
from test_ai_scoped_documents import scoped, grants, ACTOR
from test_ai_scoped_documents_postgres import concurrent, worker_pid, wait_blocked
from tenant_ai_unused_archive import archive_unused_agent
from tenant_linear_policy import TenantLinearError


def archive(factory,target,ready):
    with factory() as conn:
        ready(conn.info.backend_pid)
        try:return archive_unused_agent(ACTOR,target,expected_version=1,connection=conn)
        except TenantLinearError as error:return error.status


def test_association_admitted_first_blocks_archive(concurrent,scoped):
    factory,ids=concurrent;database,_=scoped;grants(database,{'delete':'self'})
    event,pids,ready=worker_pid()
    with factory() as writer:
        writer.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s',(json.dumps({'allowed_primary_ids':[ids['self']]}),ids['peer']))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(archive,factory,ids['self'],ready)
            assert event.wait(4);wait_blocked(factory,pids[0],writer.info.backend_pid)
            writer.commit()
            assert future.result(timeout=12)==409
    with factory() as conn:assert conn.execute('SELECT archived_at FROM saas_ai_agents WHERE id=%s',(ids['self'],)).fetchone()['archived_at'] is None


def test_archive_fence_first_rejects_waiting_json_writer(concurrent,scoped):
    factory,ids=concurrent;database,_=scoped;grants(database,{'delete':'self'})
    event,pids,ready=worker_pid()
    def associate():
        with factory() as conn:
            ready(conn.info.backend_pid)
            try:
                conn.execute('UPDATE saas_ai_agents SET expert_policy=%s::jsonb WHERE id=%s',(json.dumps({'allowed_primary_ids':[ids['self']]}),ids['peer']))
                conn.commit();return 'unexpected success'
            except Exception as error:return getattr(error,'sqlstate',None)
    with factory() as holder:
        holder.execute('SELECT id FROM saas_ai_agents WHERE id=%s FOR UPDATE',(ids['self'],))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(associate)
            assert event.wait(4);wait_blocked(factory,pids[0],holder.info.backend_pid)
            result=archive_unused_agent(ACTOR,ids['self'],expected_version=1,connection=holder)
            assert result['archived']
            assert future.result(timeout=12)=='23514'
    with factory() as conn:assert not conn.execute('SELECT expert_policy FROM saas_ai_agents WHERE id=%s',(ids['peer'],)).fetchone()['expert_policy']


def test_revoked_membership_seen_before_archive(concurrent,scoped):
    factory,ids=concurrent;database,_=scoped;grants(database,{'delete':'self'})
    event,pids,ready=worker_pid()
    with factory() as revoker:
        revoker.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=31',(A,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(archive,factory,ids['self'],ready)
            assert event.wait(4);wait_blocked(factory,pids[0],revoker.info.backend_pid)
            revoker.commit();assert future.result(timeout=12)==403
