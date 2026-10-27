"""Independent processes and connections against disposable localhost PostgreSQL."""
import multiprocessing
import uuid
import psycopg
import pytest
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A,CA,CB,AGENT,PROFILE
from tenant_knowledge_candidates_policy import MAX_TOKENS


@pytest.fixture
def campaign(database,monkeypatch):
    """Build a campaign in the disposable real PostgreSQL database used by the race tests."""
    import tenant_knowledge_campaigns as campaigns
    import tenant_knowledge_candidates_store as cases
    import tenant_knowledge_media_cache as cache
    d=database
    d.execute('TRUNCATE saas_tenants CASCADE')
    d.execute('DELETE FROM saas_ai_spend_reservations')
    with d.cursor() as cur:
        cur.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Race company','active',11)",(A,A))
        cur.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Owner')",(A,))
        cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Race channel')",(A,CA))
        cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'client',41,'Client',now())",(A,CA))
        cur.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,41,'m1',%s,'incoming','test message')",(A,str(uuid.UUID(int=801)),'connect:'+CA))
        cur.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Race agent',11)",(A,AGENT))
    d.commit()
    monkeypatch.setattr(campaigns,'_connect',lambda:d)
    monkeypatch.setattr(cases,'_connect',lambda:d)
    monkeypatch.setattr(cache,'_connect',lambda:d)
    data={'request_id':str(uuid.uuid4()),'channel_ids':[CA],'analysis_agent_id':AGENT,
          'max_calls':2,'max_tokens':2*(MAX_TOKENS+8000)}
    key=campaigns.prepare(PROFILE,data)
    return d,lambda:None,data,key


def add(d,key,n=0):
    import tenant_knowledge_campaigns as campaigns
    value={'provider':'connect','channel_id':CA,'conversation_id':'client','history_mode':'channel',
           'period_start':'2026-10-01T00:00:00Z','period_end':'2026-10-02T00:00:00Z'}
    if n:value['before']=str(uuid.UUID(int=900+n))
    with d.cursor() as cur:
        cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,key))
        result=campaigns.enqueue(cur,PROFILE,cur.fetchone(),value)
    d.commit();return result


def approve_fixture(d,key):
    d.execute("UPDATE saas_knowledge_campaigns SET status='running',approved_at=now(),approved_by=11 WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,key));d.commit()


def child(dsn,kind,key,barrier,out,index=0):
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
                    from test_tenant_support_inbox import CA,CB
                    service.check_active=lambda *a:None
                    refs=[{'id':'synthetic-1','channel_id':CA},{'id':'synthetic-2','channel_id':CB}]
                    ref=refs[index]
                    item={'tenant':A,'key':'mock','model':'fixture','refs':refs,
                          'route':{'allow_external_media':True,'allow_vision':True,'approved_channel_ids':[CA,CB]}}
                    result=cache.observe_once(PROFILE,item,ref,b'fake-image','image/png',{'message_index':index},invoke)
                else:
                    result=cache.transcribe_once(PROFILE,{'tenant':A,'key':'mock'},'test',(b'fake','wav','audio/wav'),invoke)
                out.put(('ok',result[2]))
            except Exception as error:
                from tenant_linear_policy import TenantLinearError
                if isinstance(error,TenantLinearError) and error.status==409:out.put(('ok',False))
                else:raise
    except Exception as error:out.put(('error',type(error).__name__,str(error)))


def race(d,kind,key,indexes=(0,1)):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);out=ctx.Queue()
    children=[ctx.Process(target=child,args=(d.dsn,kind,key,barrier,out,index)) for index in indexes]
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
