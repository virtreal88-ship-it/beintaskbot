"""Disposable native PostgreSQL parity and real multi-process policy races."""
import multiprocessing
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,PROFILE
from test_administrator_publication import setup
from test_ai_training_policy import (
    trusted,policy,op,target,
    test_one_owner_policy_multiple_materials_update_publish_readback_rollback,
    test_policy_fail_closed,
    test_idempotency_and_changed_content_or_evidence_are_rejected,
    test_no_self_policy_or_extra_rights_and_owner_rbac,
    test_foreign_source_and_unverified_source_are_blocked,
    test_policy_revoke_blocks_existing_unconsumed_grant,
    test_historical_case_evidence_and_clarification,
    test_transaction_failure_rolls_back_internal_approvals_and_materials,
    test_http_single_owner_consent_then_multiple_mcp_publications,
    test_real_historical_status_fixtures_cannot_become_verified_outcomes,
    test_transitive_evidence_withdrawal_and_draft_injection_are_blocked,
)
from test_knowledge_candidates import ready
import tenant_ai_administrator_approvals as approval
from tenant_linear_policy import TenantLinearError


def child(dsn,cfg,action,data,barrier,results):
    import tenant_ai_agents_schema as schema
    import tenant_platform_schema as platform
    import tenant_chat_ai_schema as chat
    schema._ready=True;platform._schema_ready=True;chat._ready=True
    try:
        barrier.wait(25)
        with psycopg.connect(dsn,row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                result=(approval.training_policy_revoke(cur,PROFILE,data['policy_id']) if action=='revoke'
                    else approval.training_operation(cur,cfg,action,data))
        results.put(('ok',result))
    except TenantLinearError as exc:results.put(('blocked',exc.status))
    except Exception as exc:results.put(('error',type(exc).__name__,str(exc)))


def race(db,cfg,actions):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(len(actions));results=ctx.Queue()
    children=[ctx.Process(target=child,args=(db.dsn,cfg,action,data,barrier,results)) for action,data in actions]
    for process in children:process.start()
    try:
        values=[results.get(timeout=50) for _ in children]
        for process in children:process.join(timeout=10);assert process.exitcode==0
        assert all(v[0]!='error' for v in values),values
        return values
    finally:
        for process in children:
            if process.is_alive():process.terminate();process.join(timeout=5)


def test_concurrent_requests_quota_and_restart(setup):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:p=policy(cur,max_operations=1)
    db.commit()
    args={'policy_id':p,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':source}
    values=race(db,cfg,[('create',args),('create',args)])
    assert values[0][0]==values[1][0]=='ok' and values[0][1]==values[1][1]
    assert len(db.execute('SELECT * FROM saas_ai_training_operations'))==1;db.commit()
    # New worker, same request: persisted receipt, no second material.
    again=race(db,cfg,[('create',args),('create',{**args,'request_id':str(uuid.uuid4())})])
    assert sorted(v[0] for v in again)==['blocked','ok'] and ('blocked',429) in again
    assert len(db.execute('SELECT * FROM saas_ai_training_operations'))==1
    assert not db.execute('SELECT * FROM test_external_sends');db.commit()


def test_concurrent_publication_is_one_receipt_and_one_source(setup):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:
        p=policy(cur);prepared=op(cur,cfg,p,'create',kind='knowledge',source_id=source)
    db.commit()
    args={'policy_id':p,'request_id':str(uuid.uuid4()),**target(prepared)}
    values=race(db,cfg,[('publish',args),('publish',args)])
    assert values[0][0]==values[1][0]=='ok' and values[0][1]==values[1][1]
    assert len(db.execute("SELECT * FROM saas_ai_training_operations WHERE action='publish'"))==1
    assert db.execute('SELECT count(*) AS n FROM saas_ai_knowledge_sources')[0]['n']==4
    assert not db.execute('SELECT * FROM test_external_sends');db.commit()


def test_revoke_race_is_serial_and_blocks_next_request(setup):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:p=policy(cur)
    db.commit()
    args={'policy_id':p,'request_id':str(uuid.uuid4()),'kind':'knowledge','source_id':source}
    values=race(db,cfg,[('create',args),('revoke',{'policy_id':p})])
    assert any(v[0]=='ok' and v[1].get('revoked') for v in values)
    assert len(db.execute('SELECT * FROM saas_ai_training_operations'))<=1;db.commit()
    after=race(db,cfg,[('create',{**args,'request_id':str(uuid.uuid4())})])
    assert after==[('blocked',403)]


def test_distinct_concurrent_requests_cannot_exceed_policy_quota(setup):
    db,cfg=setup;source=trusted(setup)
    with db.cursor() as cur:p=policy(cur,max_operations=1)
    db.commit()
    args={'policy_id':p,'kind':'knowledge','source_id':source}
    values=race(db,cfg,[('create',{**args,'request_id':str(uuid.uuid4())}),('create',{**args,'request_id':str(uuid.uuid4())})])
    assert sorted(v[0] for v in values)==['blocked','ok'] and ('blocked',429) in values
    assert len(db.execute('SELECT * FROM saas_ai_training_operations'))==1;db.commit()
