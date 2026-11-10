"""Workflow scopes coexist with employee settings on isolated PostgreSQL only."""
import json
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
import tenant_workflow_store as workflow
import tenant_member_store as members
from test_employee_card_consistency_postgres import cards
from test_company_employees_navigation_postgres import A, B, database, db, staff


def seed(f):
    value={'61':{'pipeline_ids':['10'],'ai_enabled':False,'provider_scopes':{'linear':['team-old']}},
           '66':{'pipeline_ids':['retired'],'notifications':{'linear_done':{'push':True}}}}
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s AND telegram_id=61", (A,))
        conn.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s,'10','One'),(%s,'20','Two')", (A,A))
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,json.dumps(value)))
    return value


def policy(f):
    with f['factory']() as conn:
        return conn.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'",(A,)).fetchone()['value']


def change():
    return {'telegram_id':61,'expected_pipeline_ids':['10'],'pipeline_ids':['20']}


def test_sparse_scope_and_employee_work_settings_concurrent(cards):
    before=seed(cards)
    row=next(row for row in members.list_members(tenant_id=A,owner_id=11) if row['telegram_id']==61)
    barrier=threading.Barrier(2)
    def scope():
        barrier.wait()
        return workflow.save_tenant_workflow_config(tenant_id=A,owner_id=11,member_pipeline_changes=[change()])
    def settings():
        barrier.wait()
        return members.upsert_member(tenant_id=A,owner_id=11,telegram_id=61,expected_version=row['card_version'],
            changes={'work_settings':{'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':True}}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(scope),pool.submit(settings)]
        for future in futures:future.result(timeout=10)
    after=policy(cards)
    assert after['61']=={**before['61'],'pipeline_ids':['20'],'ai_enabled':True}
    assert after['66']==before['66']


def test_stale_scope_conflict_preserves_policy(cards):
    seed(cards)
    workflow.save_tenant_workflow_config(tenant_id=A,owner_id=11,member_pipeline_changes=[change()])
    before=policy(cards)
    with pytest.raises(workflow.TenantPlatformError,match='dəyişib'):
        workflow.save_tenant_workflow_config(tenant_id=A,owner_id=11,member_pipeline_changes=[change()])
    assert policy(cards)==before


def test_foreign_pipeline_is_not_granted(cards):
    before=seed(cards)
    with cards['factory']() as conn:
        conn.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s,'foreign','Other company')",(B,))
    with pytest.raises(workflow.TenantPlatformError):
        workflow.save_tenant_workflow_config(tenant_id=A,owner_id=11,
            member_pipeline_changes=[{**change(),'pipeline_ids':['foreign']}])
    assert policy(cards)==before
