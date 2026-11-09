"""Genuine concurrent PostgreSQL sessions; mocked AI and localhost DB only."""
import json
import multiprocessing
import uuid
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,PROFILE
from test_knowledge_candidates import ready,model_card
from test_operator_learning_retry import failed


def retry_child(dsn,command,barrier,out):
    import tenant_operator_case_submission as submission
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_candidates_store as store
    import tenant_ai_spend_budget as budget
    import tenant_ai_agent_run_store as runs
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    for module in (submission,service,store,budget,runs):module._connect=factory
    service.choose_api_credential=lambda *a:('synthetic-key','platform')
    service.platform_model=lambda:'test-model'
    service.platform_model_allowed=lambda *a:True
    service.platform_token_limit=lambda *a:0
    def generate(*args,**kwargs):
        with factory() as conn:conn.execute("INSERT INTO test_external_sends(channel,run_id) VALUES('mock-ai',%s)",(str(uuid.uuid4()),))
        return json.dumps({'cards':[model_card()]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60}
    service.generate=generate
    try:
        barrier.wait(20)
        result=submission.submit(PROFILE,command)
        out.put(('ok',result['request']['id']))
    except Exception as error:
        import traceback
        out.put(('error',type(error).__name__,traceback.format_exc()))


def test_simultaneous_explicit_retries_create_one_successor(failed):
    conn,generation,data,parent=failed
    conn.commit()
    command={**data,'retry_request_id':parent}
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);out=ctx.Queue()
    children=[ctx.Process(target=retry_child,args=(conn.dsn,command,barrier,out)) for _ in range(2)]
    for child in children:child.start()
    try:
        results=[out.get(timeout=45) for _ in children]
        for child in children:child.join(10);assert child.exitcode==0
        assert all(r[0]=='ok' for r in results),'\n'.join(str(r[2]) for r in results if r[0]!='ok')
        assert results[0][1]==results[1][1] and results[0][1]!=parent
        rows=conn.execute("SELECT id FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND route->>'retry_of'=%s",(A,parent))
        assert len(rows)==1
        assert len(conn.execute("SELECT * FROM test_external_sends WHERE channel='mock-ai'"))==1
        generation.assert_not_called()
    finally:
        for child in children:
            if child.is_alive():child.terminate();child.join(5)
