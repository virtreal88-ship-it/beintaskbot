"""Concurrent owner completion and worker claims on disposable local PostgreSQL."""
import multiprocessing
import psycopg
from psycopg.rows import dict_row

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A
from test_single_photo_retry import prior
import tenant_ai_single_message_extraction as consent
import tenant_knowledge_campaigns as campaigns


def child(dsn,kind,cfg,payload,barrier,results):
    try:
        # The fixture initialized the schema before spawning web/worker peers.
        # Match normal post-startup processes; race business transactions, not DDL.
        import tenant_platform_schema,tenant_ai_agents_schema,tenant_chat_ai_schema
        tenant_platform_schema._schema_ready=True
        tenant_ai_agents_schema._ready=True
        tenant_chat_ai_schema._ready=True
        campaigns._connect=lambda:psycopg.connect(dsn,row_factory=dict_row)
        barrier.wait(20)
        if kind=='approve':
            with psycopg.connect(dsn,row_factory=dict_row) as conn:
                with conn.cursor() as cur:result=consent.complete(cur,cfg,**payload)
        else:result=campaigns.claim(A,payload['campaign_id'])
        results.put(('ok',str(result['fragment_id'] if kind=='approve' else result['id']) if result else None))
    except Exception as error:results.put(('error',type(error).__name__,str(error)))


def race(dsn,kind,cfg,payload):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);results=ctx.Queue()
    processes=[ctx.Process(target=child,args=(dsn,kind,cfg,payload,barrier,results)) for _ in range(2)]
    for p in processes:p.start()
    try:
        values=[results.get(timeout=40) for _ in processes]
        for p in processes:p.join(10);assert p.exitcode==0
        assert all(x[0]=='ok' for x in values),values
        return [x[1] for x in values]
    finally:
        for p in processes:
            if p.is_alive():p.terminate();p.join(5)


def test_duplicate_owner_decision_and_parallel_claim_produce_one_child(prior):
    db,cfg,args,fp,_=prior
    prepared=consent.prepare(db.cursor(),cfg,args);db.commit()
    meta=prepared['_component_meta']['owner_single_extraction']
    payload={'consent_id':prepared['consent_id'],'capability':meta['capabilities']['approve'],
      'action':'approve','acknowledge_prior_unknown':True}
    outcomes=race(db.dsn,'approve',cfg,payload)
    assert len(set(outcomes))==1
    row=db.execute('SELECT campaign_id,fragment_id FROM saas_ai_single_message_extraction_consents WHERE id=%s::uuid',(prepared['consent_id'],))[0]
    db.commit()
    claims=race(db.dsn,'claim',cfg,{'campaign_id':str(row['campaign_id'])})
    assert sorted(claims,key=str)==sorted([str(row['fragment_id']),None],key=str)
    assert db.execute("SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE route->>'retry_of'=%s",(args['retry_of'],))[0]['n']==1
    assert db.execute('SELECT status FROM saas_knowledge_campaign_fragments WHERE id=%s::uuid',(args['retry_of'],))[0]['status']=='unknown'
    assert db.execute('SELECT state FROM saas_knowledge_media_cache WHERE fingerprint=%s',(fp,))[0]['state']=='unknown'
    assert db.execute("SELECT sum(reserved_microusd) AS n FROM saas_ai_spend_reservations WHERE state='unknown'")[0]['n']==700
    assert db.execute('SELECT count(*) AS n FROM saas_knowledge_extractions')[0]['n']==1
