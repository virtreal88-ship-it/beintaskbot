"""Independent claims against disposable localhost PostgreSQL, never a provider."""
import json
import multiprocessing
import uuid

import psycopg
from psycopg.rows import dict_row

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A, CA, AGENT
import tenant_knowledge_campaigns as campaigns
import tenant_knowledge_candidates_store as store


def _claim_child(dsn, campaign_id, barrier, results):
    campaigns._connect=lambda:psycopg.connect(dsn,row_factory=dict_row)
    try:
        barrier.wait(20)
        result=campaigns.claim(A,campaign_id)
        results.put(('ok',str(result['id']) if result else None))
    except Exception as exc:
        results.put(('error',type(exc).__name__,str(exc)))


def test_parallel_exact_claims_preserve_old_unknown(database):
    db=database
    store.schema(db);campaigns.create_schema(db)
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Test','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Owner')",(A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Test')",(A,CA))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,'client',41,'Client')",(A,CA))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Test',11)",(A,AGENT))
    campaign_id=str(uuid.uuid4());pending=str(uuid.uuid4());old=str(uuid.uuid4())
    policy={'allow_external_media':False,'allow_paid_asr':False,'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':0}
    db.execute("""INSERT INTO saas_knowledge_campaigns
      (tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress)
      VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'running',11,now(),2,32000,%s::jsonb,%s::jsonb)""",
      (A,campaign_id,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps({'mode':'single_message'})))
    route={'provider':'connect','channel_id':CA,'conversation_id':'client','history_mode':'channel',
      'message_ids':[str(uuid.uuid4())],'source_version':'a'*64,
      'period_start':'2026-10-01T00:00:00Z','period_end':'2026-10-01T00:00:01Z','approved_channel_ids':[CA]}
    old_route={**route,'message_ids':[str(uuid.uuid4())],'source_version':'b'*64}
    for fragment,source,selected,status in ((pending,'new',route,'pending'),(old,'old',old_route,'unknown')):
        db.execute("""INSERT INTO saas_knowledge_campaign_fragments
          (tenant_id,campaign_id,id,source_key,route,status)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb,%s)""",
          (A,campaign_id,fragment,source,json.dumps(selected),status))
    db.commit()
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);results=ctx.Queue()
    workers=[ctx.Process(target=_claim_child,args=(db.dsn,campaign_id,barrier,results)) for _ in range(2)]
    for worker in workers:worker.start()
    try:
        values=[results.get(timeout=40) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert sorted(values,key=str)==sorted([('ok',pending),('ok',None)],key=str),values
        assert db.execute("SELECT status FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid",(old,))[0]['status']=='unknown'
        assert db.execute("SELECT status FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid",(pending,))[0]['status']=='running'
        assert db.execute("SELECT reserved_calls FROM saas_knowledge_campaigns WHERE id=%s::uuid",(campaign_id,))[0]['reserved_calls']==1
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)


def test_parallel_recovery_claim_skips_quarantined_head(database):
    db=database
    store.schema(db);campaigns.create_schema(db)
    db.execute('TRUNCATE saas_tenants CASCADE')
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Test','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Owner')",(A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Test')",(A,CA))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,'client',41,'Client')",(A,CA))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Test',11)",(A,AGENT))
    key=str(uuid.uuid4());blocked=str(uuid.UUID(int=1));safe=str(uuid.UUID(int=2));unknown=str(uuid.UUID(int=3))
    blocked_source=str(uuid.uuid4());safe_source=str(uuid.uuid4())
    policy={'allow_external_media':False,'allow_paid_asr':False,'max_main_calls':2,'max_asr_calls':0,'max_vision_calls':0}
    progress={'mode':'case_media_batch','failed_media_recovery':{'parents':{}}}
    db.execute("""INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,approved_by,approved_at,max_calls,max_tokens,media_policy,progress)
      VALUES(%s::uuid,%s::uuid,11,%s::jsonb,%s::uuid,'running',11,now(),2,32000,%s::jsonb,%s::jsonb)""",
      (A,key,json.dumps([CA]),AGENT,json.dumps(policy),json.dumps(progress)))
    base={'provider':'connect','channel_id':CA,'conversation_id':'client','history_mode':'channel',
      'source_version':'a'*64,'period_start':'2026-10-01T00:00:00Z','period_end':'2026-10-01T00:00:01Z'}
    for frag,source,route,status in [(blocked,'blocked',{**base,'message_ids':[blocked_source]},'pending'),
      (safe,'safe',{**base,'message_ids':[safe_source]},'pending'),
      (unknown,'unknown',{'channel_id':CA,'message_ids':[blocked_source]},'unknown')]:
        db.execute("""INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route,status,created_at)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb,%s,'2026-01-01T00:00:00Z')""",
          (A,key,frag,source,json.dumps(route),status))
    db.commit()
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);results=ctx.Queue()
    workers=[ctx.Process(target=_claim_child,args=(db.dsn,key,barrier,results)) for _ in range(2)]
    for worker in workers:worker.start()
    try:
        values=[results.get(timeout=40) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert sorted(values,key=str)==sorted([('ok',safe),('ok',None)],key=str),values
        states={str(row['id']):row['status'] for row in db.execute('SELECT id,status FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid',(key,))}
        assert states=={blocked:'pending',safe:'running',unknown:'unknown'}
        assert db.execute('SELECT reserved_calls FROM saas_knowledge_campaigns WHERE id=%s::uuid',(key,))[0]['reserved_calls']==1
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)
