"""Independent processes and connections against disposable localhost PostgreSQL."""
import multiprocessing
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,PROFILE
from test_knowledge_candidates import ready
from test_knowledge_campaigns import campaign,add,approve_fixture


def child(dsn,kind,key,barrier,out):
    import tenant_knowledge_campaigns as campaigns
    import tenant_knowledge_media_cache as cache
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    campaigns._connect=cache._connect=factory
    try:
        barrier.wait(15)
        if kind=='claim':out.put(('ok',bool(campaigns.claim(A,key))))
        else:
            def invoke(*args,**kwargs):
                with factory() as conn:conn.execute('INSERT INTO test_asr_calls DEFAULT VALUES')
                return 'Synthetic transcript',{'total_tokens':10}
            try:
                if kind=='vision':
                    import tenant_knowledge_candidates_service as service
                    from test_tenant_support_inbox import CA
                    service.check_active=lambda *a:None
                    ref={'id':'synthetic','channel_id':CA}
                    item={'tenant':A,'key':'mock','model':'fixture','refs':[ref],
                          'route':{'allow_external_media':True,'allow_vision':True,'approved_channel_ids':[CA]}}
                    result=cache.observe_once(PROFILE,item,ref,b'fake-image','image/png',{},invoke)
                else:
                    result=cache.transcribe_once(PROFILE,{'tenant':A,'key':'mock'},'test',(b'fake','wav','audio/wav'),invoke)
                out.put(('ok',result[2]))
            except Exception as error:
                from tenant_linear_policy import TenantLinearError
                if isinstance(error,TenantLinearError) and error.status==409:out.put(('ok',False))
                else:raise
    except Exception as error:out.put(('error',type(error).__name__,str(error)))


def race(d,kind,key):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);out=ctx.Queue()
    children=[ctx.Process(target=child,args=(d.dsn,kind,key,barrier,out)) for _ in range(2)]
    for p in children:p.start()
    for p in children:p.join(25)
    for p in children:
        if p.is_alive():p.terminate();p.join();raise AssertionError('Concurrent worker timeout')
        assert p.exitcode==0
    results=[out.get(timeout=3) for _ in children]
    assert all(r[0]=='ok' for r in results),results
    return results


def test_two_workers_reserve_one_fragment(campaign):
    d,_,_,key=campaign;add(d,key);add(d,key,1);approve_fixture(d,key)
    assert sum(r[1] for r in race(d,'claim',key))==1
    assert d.execute('SELECT reserved_calls FROM saas_knowledge_campaigns')[0]['reserved_calls']==1


def test_media_cache_race_never_duplicates_asr(campaign):
    import tenant_knowledge_media_cache as cache
    d,_,_,key=campaign
    with d.cursor() as cur:cache.schema(cur)
    d.execute('CREATE TABLE IF NOT EXISTS test_asr_calls(id BIGSERIAL PRIMARY KEY)');d.execute('TRUNCATE test_asr_calls');d.commit()
    assert sum(r[1] for r in race(d,'media',key))==1
    assert d.execute('SELECT count(*) AS n FROM test_asr_calls')[0]['n']==1


def test_media_cache_race_never_duplicates_vision(campaign):
    import tenant_knowledge_media_cache as cache
    d,_,_,key=campaign
    with d.cursor() as cur:cache.schema(cur)
    d.execute('CREATE TABLE IF NOT EXISTS test_asr_calls(id BIGSERIAL PRIMARY KEY)');d.execute('TRUNCATE test_asr_calls');d.commit()
    assert sum(r[1] for r in race(d,'vision',key))==1
    assert d.execute('SELECT count(*) AS n FROM test_asr_calls')[0]['n']==1
    # Fresh processes (a worker restart) reuse the durable receipt.
    assert sum(r[1] for r in race(d,'vision',key))==0
    assert d.execute('SELECT count(*) AS n FROM test_asr_calls')[0]['n']==1
