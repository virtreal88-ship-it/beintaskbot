"""Concurrent extension requests against disposable PostgreSQL; no providers."""
import json
import multiprocessing
import uuid
from types import SimpleNamespace
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A, CA, AGENT, PROFILE
import tenant_knowledge_candidates_store as store
import tenant_knowledge_campaigns as campaigns


def _apply(dsn, campaign, request_id, source, barrier, results):
    import tenant_platform
    import tenant_case_media_extension as extension
    import tenant_knowledge_campaigns as worker_campaigns
    tenant_platform._connect=lambda:psycopg.connect(dsn,row_factory=dict_row)
    extension.cases.authorize=lambda *args:(A,11,SimpleNamespace(profile={'role':'owner'}))
    extension.preview=lambda *args,**kw:{'snapshot_hash':'a'*64,'budget':{'available_microusd':9000000},'operation_capacity':{'next_fragment_fits':True},'_snapshot':[source]}
    def enqueue(cur,profile,campaign,route):
        fragment=str(uuid.uuid4())
        cur.execute('INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb)',(A,campaign['id'],fragment,fragment,json.dumps(route)))
        return fragment
    worker_campaigns.enqueue=enqueue
    try:
        barrier.wait(15)
        value=extension.apply(PROFILE,campaign,1,[], 'a'*64,request_id)
        results.put(('ok',value['replayed']))
    except Exception as error:results.put(('error',type(error).__name__,str(error)))


@pytest.mark.parametrize('same_request',[True,False])
def test_concurrent_extension_is_single_commit_and_keeps_holds(database,same_request):
    db=database;store.schema(db);campaigns.create_schema(db)
    import tenant_ai_spend_budget as spending
    spending.ensure_schema(db)
    db.execute('TRUNCATE saas_ai_spend_reservations');db.commit()
    db.execute('TRUNCATE saas_tenants CASCADE');db.commit()
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Test','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Owner')",(A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Test')",(A,CA))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Test',11)",(A,AGENT))
    key=str(uuid.uuid4());msg=str(uuid.uuid4());req=str(uuid.uuid4())
    policy={'allow_external_media':True,'allow_paid_asr':False,'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':4}
    progress={'mode':'case_media_batch','case_plans':[]}
    db.execute("""INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress,max_cost_microusd) VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'paused',11,now(),10,100000,%s::jsonb,%s::jsonb,10000000)""",(A,key,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps(progress)))
    db.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'active',11,now())",(A,key,key))
    db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,grant_id,state) VALUES(%s::uuid,%s::uuid,'hold','test','vision',170400,%s::uuid,'unknown')",(A,str(uuid.uuid4()),key));db.commit()
    source={'case_id':str(uuid.uuid4()),'message_id':msg,'case_version':1,'channel_id':CA,'conversation_id':'client','source_version':'b'*64,'source_hash':'c'*64,'kind':'image','occurred_at':'2026-10-01T00:00:00+00:00','state':'known_not_attempted','reconciliation':{}}
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);results=ctx.Queue()
    requests=[req,req if same_request else str(uuid.uuid4())]
    workers=[ctx.Process(target=_apply,args=(db.dsn,key,request,source,barrier,results)) for request in requests]
    for worker in workers:worker.start()
    try:
        values=[results.get(timeout=30) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        if same_request:assert sorted(values)==[('ok',False),('ok',True)],values
        else:assert ('ok',False) in values and any(v[0:2]==('error','TenantLinearError') for v in values),values
        assert db.execute('SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,))[0]['n']==1
        assert db.execute('SELECT status,version FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]=={'status':'paused','version':2}
        assert db.execute("SELECT state,reserved_microusd FROM saas_ai_spend_reservations WHERE call_key='hold'")[0]=={'state':'unknown','reserved_microusd':170400}
        assert db.execute('SELECT cap_microusd FROM saas_ai_media_grants WHERE id=%s::uuid',(key,))[0]['cap_microusd']==10000000
        from tenant_linear_policy import TenantLinearError
        db.execute("UPDATE saas_knowledge_campaigns SET status='running' WHERE id=%s::uuid",(key,))
        db.execute("INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,model,kind,reserved_microusd,actual_microusd,state) VALUES(%s::uuid,%s::uuid,'prior-settled','test','vision',385362,385362,'settled')",(A,str(uuid.uuid4())))
        db.commit()
        with db.cursor() as cur:spending._grant_admit(cur,A,key,9444238)
        db.rollback()
        with db.cursor() as cur,pytest.raises(TenantLinearError):spending._grant_admit(cur,A,key,9444239)
        db.rollback()
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)
