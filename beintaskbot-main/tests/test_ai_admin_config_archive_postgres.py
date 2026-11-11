"""Real approved admin apply versus unused archive, with production validation."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, CA, AGENT, PROFILE, seed_simulation
from test_ai_scoped_documents import scoped, grants, ACTOR
from test_ai_scoped_documents_postgres import concurrent, worker_pid, wait_blocked
from test_ai_learning_mcp import _setup
import tenant_ai_learning_mcp as mcp
import tenant_ai_administrator_apply as apply
import tenant_ai_administrator_drafts as drafts
import tenant_ai_administrator_approvals as approvals
from tenant_ai_unused_archive import archive_unused_agent
from tenant_linear_policy import TenantLinearError


def test_approved_agent_config_apply_holds_mutex_before_agent_while_archive_waits(concurrent,scoped,monkeypatch):
    factory,_=concurrent;database,_=scoped
    seed_simulation(database);_setup(monkeypatch,database)
    monkeypatch.setenv('AI_LEARNING_MCP_DRAFTS_ENABLED','true')
    monkeypatch.setenv('AI_AUTONOMOUS_APPLY_ENABLED','true')
    grants(database,{'delete':'self','view':'none','update':'none'})
    database.execute('UPDATE saas_ai_agents SET access_responsible_id=31 WHERE tenant_id=%s AND id=%s',(A,AGENT))
    cfg=mcp._require_cfg();cfg.update(actor_id=11,granted_scopes=[drafts.DRAFT_SCOPE,mcp.APPLY_SCOPE],AI_LEARNING_MCP_AGENT_ID=AGENT)
    with database.cursor() as cur:
        draft=mcp._tools(cur,cfg,'administrator_draft_agent_config_create',{'agent_id':AGENT,'channel_id':CA,'expected_agent_version':1,'patch':{'language':'ru'}})
        receipt=approvals.request(cur,PROFILE,draft['id'],draft['version'])
        approvals.approve(cur,PROFILE,receipt['id'],'approve')
    database.commit()
    validated=threading.Event();resume=threading.Event();apply_pids=[]
    original=apply.validate_config_proposal
    def pause_after_real_validation(cur,config,data):
        result=original(cur,config,data)
        apply_pids.append(cur.connection.info.backend_pid)
        validated.set()
        assert resume.wait(5)
        return result
    monkeypatch.setattr(apply,'validate_config_proposal',pause_after_real_validation)
    def approved_apply():
        with factory() as conn:
            with conn.cursor() as cur:
                result=apply.apply_agent_config(cur,PROFILE,receipt['id'],draft['id'],cfg)
            conn.commit();return result
    started,archive_pids,ready=worker_pid()
    def archive():
        with factory() as conn:
            ready(conn.info.backend_pid)
            try:return archive_unused_agent(ACTOR,AGENT,expected_version=1,connection=conn)
            except TenantLinearError as error:return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        applying=pool.submit(approved_apply)
        try:
            assert validated.wait(5)
            archiving=pool.submit(archive)
            assert started.wait(4)
            wait_blocked(factory,archive_pids[0],apply_pids[0])
            with factory() as observer:
                assert observer.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted) AS blocked",(archive_pids[0],)).fetchone()['blocked']
        finally:
            resume.set()
        assert applying.result(timeout=12)['version']==2
        assert archiving.result(timeout=12)==409
    with factory() as conn:
        row=conn.execute('SELECT language,version,archived_at FROM saas_ai_agents WHERE tenant_id=%s AND id=%s',(A,AGENT)).fetchone()
        assert row=={'language':'ru','version':2,'archived_at':None}
