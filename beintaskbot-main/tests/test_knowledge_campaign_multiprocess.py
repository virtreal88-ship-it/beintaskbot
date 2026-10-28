"""Independent processes and connections against disposable localhost PostgreSQL."""
import multiprocessing
import uuid
import psycopg
import pytest
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A,CA,CB,AGENT,PROFILE
from tenant_knowledge_candidates_policy import MAX_TOKENS
from tenant_linear_policy import TenantLinearError


def recovery_child(dsn,key,request,parent_id,source_id,case_id,barrier,out):
    import json
    import tenant_knowledge_campaigns as campaigns
    import tenant_case_media_admission as admission
    import tenant_case_media_recovery as recovery
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    campaigns._connect=factory
    route={'case_id':case_id,'message_ids':[source_id],'source_version':'a'*64}
    recovery.approved_parent=lambda *a:{'id':parent_id,'extraction_id':parent_id,'route':route}
    admission.plan=lambda *a,**k:{'status':'pending_consent','plan_hash':'plan',
      'media':[{'id':source_id,'kind':'image','source_version':'a'*64,'source_hash':'hash',
                'complete':False,'current_source_addressable':True}]}
    def enqueue(cur,profile,campaign,route):
        child=str(uuid.uuid5(uuid.UUID(key),parent_id))
        cur.execute('''INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb) ON CONFLICT DO NOTHING''',
          (A,key,child,'child',json.dumps(route)))
        return child
    campaigns.enqueue=enqueue
    try:
        barrier.wait(15)
        out.put(('ok',campaigns.recover_failed_media(PROFILE,key,1,request)))
    except Exception as error:out.put(('error',type(error).__name__,str(error)))


def test_recovery_replay_is_single_flight_under_concurrency(campaign):
    import json
    d,_,_,key=campaign
    parent_id,source_id,case_id,request=[str(uuid.uuid4()) for _ in range(4)]
    progress={'mode':'case_media_batch','case_plans':[{'case_id':case_id,'plan_hash':'plan',
      'media':[{'id':source_id,'kind':'image','source_hash':'hash'}]}]}
    d.execute("UPDATE saas_knowledge_campaigns SET status='stopped',version=1,approved_at=now(),approved_by=11,max_cost_microusd=10000000,progress=%s::jsonb WHERE id=%s::uuid",(json.dumps(progress),key))
    d.execute("INSERT INTO saas_ai_media_grants(tenant_id,id,campaign_id,created_by,cap_microusd,status,approved_by,approved_at) VALUES(%s::uuid,%s::uuid,%s::uuid,11,10000000,'completed',11,now())",(A,key,key))
    d.execute("INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route,status) VALUES(%s::uuid,%s::uuid,%s::uuid,'parent','{}'::jsonb,'partial')",(A,key,parent_id));d.commit()
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);out=ctx.Queue()
    children=[ctx.Process(target=recovery_child,args=(d.dsn,key,request,parent_id,source_id,case_id,barrier,out)) for _ in range(2)]
    for child in children:child.start()
    for child in children:
        child.join(30)
        if child.is_alive():child.terminate();child.join();raise AssertionError('Recovery race timed out')
        assert child.exitcode==0
    results=[out.get(timeout=3) for _ in children]
    assert all(r[0]=='ok' for r in results),results
    assert sorted(r[1]['replayed'] for r in results)==[False,True]
    assert d.execute("SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE campaign_id=%s::uuid AND status='pending'",(key,))[0]['n']==1


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
                if kind in {'vision','owner_retry_vision'}:
                    import tenant_knowledge_candidates_service as service
                    from test_tenant_support_inbox import CA,CB
                    service.check_active=lambda *a:None
                    if kind=='owner_retry_vision':
                        message_id=str(uuid.UUID(int=777+index));external_id='connect:'+str(uuid.UUID(int=778+index))
                        ref={'id':message_id,'external_id':external_id,'channel_id':CA,'media_version':'a'*64}
                        refs=[ref]
                        route={'allow_external_media':True,'allow_vision':True,'owner_retry_unknown':True,
                          'owner_retry_basis':'current_connect_message','media_grant_id':key,'channel_id':CA,
                          'message_ids':[message_id],'source_version':'a'*64}
                        content=b'one current source image'
                    else:
                        refs=[{'id':'synthetic-1','channel_id':CA},{'id':'synthetic-2','channel_id':CB}]
                        ref=refs[index]
                        route={'allow_external_media':True,'allow_vision':True,'approved_channel_ids':[CA,CB]}
                        content=b'fake-image'
                    item={'tenant':A,'key':'mock','model':'fixture','refs':refs,'route':route}
                    result=cache.observe_once(PROFILE,item,ref,content,'image/png',{'message_index':index},invoke)
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


def test_owner_retry_is_single_flight_and_changed_bytes_cannot_reuse_ack(campaign,monkeypatch):
    from unittest.mock import Mock
    import tenant_knowledge_candidates_service as service
    import tenant_knowledge_media_cache as cache
    d,_,_,key=campaign
    with d.cursor() as cur:cache.schema(cur)
    d.execute('CREATE TABLE IF NOT EXISTS test_asr_calls(id BIGSERIAL PRIMARY KEY)');d.execute('TRUNCATE test_asr_calls');d.commit()
    assert sum(r[1] for r in race(d,'owner_retry_vision',key))==1
    assert d.execute('SELECT count(*) AS n FROM test_asr_calls')[0]['n']==1
    monkeypatch.setattr(cache,'_connect',lambda:d)
    monkeypatch.setattr(service,'_connect',lambda:d)
    monkeypatch.setattr(service,'check_active',lambda *a:None)
    message_id=str(uuid.UUID(int=779));ref={'id':message_id,'external_id':'connect:'+str(uuid.UUID(int=780)),'channel_id':CA,'media_version':'a'*64}
    item={'tenant':A,'key':'mock','model':'fixture','refs':[ref],
      'route':{'allow_external_media':True,'allow_vision':True,'owner_retry_unknown':True,
        'owner_retry_basis':'current_connect_message','media_grant_id':key,'channel_id':CA,
        'message_ids':[message_id],'source_version':'a'*64}}
    # The second message carries identical bytes and must reuse the one shared
    # tenant artifact instead of creating another provider call.
    same=Mock(return_value=('must not dispatch',{'total_tokens':1}))
    reused=cache.observe_once(PROFILE,item,ref,b'one current source image','image/png',{},same)
    assert reused[2] is False
    same.assert_not_called()
    changed=Mock(return_value=('must not dispatch',{'total_tokens':1}))
    with pytest.raises(TenantLinearError,match='Источник изменился'):
        cache.observe_once(PROFILE,item,ref,b'different bytes for same message','image/png',{},changed)
    changed.assert_not_called()
    assert d.execute('SELECT count(*) AS n FROM test_asr_calls')[0]['n']==1
