"""Real PostgreSQL concurrency; disposable localhost database only."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,CA,AGENT,PROFILE
from test_tenant_ai_expert import expert,EXPERT,service,agents
from test_ai_teams import E2
from tenant_linear_policy import TenantLinearError


def parallel(fn):
    barrier=Barrier(2)
    def run(n):barrier.wait(10);return fn(n)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks=[pool.submit(run,n) for n in range(2)]
        return [t.result(timeout=25) for t in tasks]


def test_simultaneous_activation_has_one_winner(expert,monkeypatch):
    d,job,parent=expert
    scopes=json.dumps([{'provider':'connect','channel_id':CA}])
    d.execute('UPDATE saas_ai_agents SET enabled=FALSE,scopes=%s::jsonb WHERE id=%s::uuid',(scopes,AGENT))
    d.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled,scopes) VALUES(%s::uuid,%s::uuid,'Second',11,FALSE,%s::jsonb)",(A,E2,scopes));d.commit()
    monkeypatch.setattr(agents,'_connect',lambda:psycopg.connect(d.dsn,row_factory=dict_row))
    monkeypatch.setattr(agents,'platform_service_ready',lambda:True)
    monkeypatch.setattr(agents,'platform_model_allowed',lambda _:True)
    def enable(n):
        try:agents.save_agent(PROFILE,{'enabled':True,'expected_version':1},agent_id=[AGENT,E2][n]);return 'enabled'
        except TenantLinearError as e:
            assert 'AI_PRIMARY_SCHEDULE_CONFLICT' in str(e);return 'conflict'
    assert sorted(parallel(enable))==['conflict','enabled']
    assert d.execute("SELECT count(*) n FROM saas_ai_agents WHERE role='primary' AND enabled=TRUE")[0]['n']==1


def test_simultaneous_expert_admission_creates_one_receipt(expert,monkeypatch):
    d,job,parent=expert
    monkeypatch.setattr(service,'_connect',lambda:psycopg.connect(d.dsn,row_factory=dict_row))
    results=parallel(lambda _:service.reserve(job,parent))
    assert sum('task_id' in r for r in results)==1
    assert sum('existing' in r for r in results)==1
    assert d.execute('SELECT count(*) n FROM saas_ai_expert_tasks')[0]['n']==1
