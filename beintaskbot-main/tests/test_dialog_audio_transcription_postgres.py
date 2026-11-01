"""Real advisory/unique contention; synthetic authority/provider, actual SQL/budgets."""
import copy
import hashlib
import json
import multiprocessing
import time
import uuid
import psycopg
from psycopg.rows import dict_row
from cryptography.fernet import Fernet
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A,CA,CB,AGENT,PROFILE
from test_dialog_audio_transcription import wav
import tenant_dialog_audio_transcription as flow
import tenant_dialog_audio_store as store
import tenant_ai_asr_model as asr

ROW=str(uuid.UUID(int=9501));OTHER=str(uuid.UUID(int=9502))
EXT='connect:'+str(uuid.UUID(int=9601));EXT2='connect:'+str(uuid.UUID(int=9602))
DATA={'channel_id':CA,'conversation_id':'synthetic-chat','message_id':EXT,'message_row_id':ROW}
CONFIG={'enabled':True,'consent':True,'copilot_enabled':True,'audio_enabled':True,'transcription_model':'whisper-1','daily_limit':50,'copilot_daily_limit':50}


def authority(data,*,aliases=False,reverse=False):
    other=data['message_row_id']==OTHER
    row={'id':OTHER if other else ROW,'external_id':EXT2 if other else EXT,'channel':'connect:'+(CB if other else CA),
         'kommo_lead_id':42 if other else 41,'body':'','raw':{'mediaId':'synthetic-2' if other else 'synthetic'},'message_type':'audio','direction':'incoming'}
    ref=flow.reference(row,row['channel'].removeprefix('connect:'),row['kommo_lead_id'])
    copies=[{'channel_id':CA,'external_id':EXT,'lead_id':41},{'channel_id':CB,'external_id':EXT2,'lead_id':42}] if aliases else [{'channel_id':CA,'external_id':EXT,'lead_id':41}]
    if reverse:copies.reverse()
    return {'tenant':A,'actor':11,'row':row,'selection':row,'ref':ref,'source_version':flow.source_hash(ref),'selection_version':flow.source_hash(ref),
      'source_channel':row['channel'].removeprefix('connect:'),'source_conversation':'synthetic-chat','source_lead':row['kommo_lead_id'],
      'view_channel':data['channel_id'],'view_conversation':data['conversation_id'],'view_lead':row['kommo_lead_id'],
      'config':CONFIG,'config_version':'fixed-v1','context':{'agent_id':AGENT,'model':'gpt-4.1','language':'auto'},'agent_binding':'fixed-binding',
      'model':'gpt-4o-transcribe','scoped':True,'quote':asr.scoped_strong_quote(A),'ai_error':None,'aliases':copies}


def setup(db,monkeypatch):
    import tenant_knowledge_media_cache as cache
    db.execute('TRUNCATE saas_tenants CASCADE')
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Synthetic','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Synthetic owner')",(A,))
    key=Fernet.generate_key();cipher=Fernet(key)
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata) VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)",(A,cipher.encrypt(json.dumps({'tenant_id':A,'provider':'chat_ai','api_key':'synthetic'}).encode()),json.dumps(CONFIG)))
    for row,external,channel,lead in ((ROW,EXT,CA,41),(OTHER,EXT2,CB,42)):
        db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,message_type,body,raw) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','audio','','{}'::jsonb)",(A,row,lead,external,'connect:'+channel))
    db.execute('CREATE TABLE IF NOT EXISTS test_audio_calls(origin TEXT)');db.execute('TRUNCATE test_audio_calls');db.commit()
    monkeypatch.setattr(asr,'SHARED_ASR_TENANT_DIGEST',hashlib.sha256(('shared-dialog-asr-rollout:v1:'+A).encode()).hexdigest())
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1000000}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD','{}')
    store.schema(db);cache.schema(db.cursor())
    db.execute('DELETE FROM saas_ai_spend_reservations');db.execute('DELETE FROM saas_knowledge_media_cache');db.commit()
    # Own tables have tenant cascades; reusable legacy artifacts are isolated too.
    return key


def child(dsn,key,data,request,barrier,release,results,kind='api',aliases=False,reverse=False):
    import tenant_ai_spend_budget as budget
    import tenant_knowledge_media_cache as cache
    import tenant_chat_ai_provider as provider
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    flow._connect=factory;budget._connect=factory;cache._connect=factory
    flow._fernet=lambda:Fernet(key)
    flow._authority=lambda cur,profile,source,**kw:authority(source,aliases=aliases,reverse=reverse)
    def download(*args):
        barrier.wait(20)
        return wav()+(b'\0' if reverse and aliases else b''),'audio/wav'
    flow._download=download
    def paid(*args,**kw):
        if kw.get('before_dispatch'):kw['before_dispatch']()
        with factory() as conn:conn.execute('INSERT INTO test_audio_calls VALUES(%s)',(kind,))
        if not release.wait(25):raise TimeoutError('test coordinator timeout')
        return 'synthetic speech',{'total_tokens':0}
    provider.transcribe=paid
    try:
        if kind=='cache':
            cache.store.authorize=lambda *a:(A,11,None)
            with factory() as conn:
                budget.ensure_schema(conn)
                with conn.cursor() as cur:budget.reserve_media(cur,A,'test-cache-asr','knowledge_asr','whisper-1','asr',1)
                conn.commit()
            barrier.wait(20)
            try:
                value=cache.transcribe_once(PROFILE,{'tenant':A,'key':'synthetic','route':{},'refs':[]},'whisper-1',(wav(),'wav','audio/wav'),paid)
                budget.settle_media(A,'test-cache-asr',operation='asr',attempted_units=1,reserved_units=1)
                results.put({'ok':True,'kind':kind})
            except Exception:
                budget.settle_media(A,'test-cache-asr',operation='asr',attempted_units=0,reserved_units=1)
                raise
        else:
            value=flow.execute(PROFILE,request)
            results.put({'ok':True,'kind':kind,'attempt':value['requested_attempt']['id']})
    except BaseException as error:results.put({'error':type(error).__name__,'code':getattr(error,'code',''),'kind':kind})


def run(db,key,requests,*,kinds=None,aliases=False):
    ctx=multiprocessing.get_context('fork');barrier=ctx.Barrier(2);release=ctx.Event();results=ctx.Queue()
    workers=[ctx.Process(target=child,args=(db.dsn,key,data,request,barrier,release,results,(kinds or ['api','api'])[index],aliases,bool(index and aliases))) for index,(data,request) in enumerate(requests)]
    try:
        for worker in workers:worker.start()
        first=results.get(timeout=40)  # One caller must join/reject while the real provider boundary is held.
        release.set();second=results.get(timeout=40)
        for worker in workers:worker.join(10);assert worker.exitcode==0
        return [first,second]
    finally:
        release.set()
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)


def request_for(key,data,*,aliases=False):
    auth=authority(data,aliases=aliases)
    cost=Fernet(key).encrypt(json.dumps({'purpose':'audio_cost_v1',**flow._scope(auth),'generation':0,'actions':['transcribe']},sort_keys=True,default=str).encode()).decode()
    return {**data,'source_version':auth['source_version'],'action':'transcribe','cost_token':cost,'idempotency_key':str(uuid.uuid4())}

@pytest.mark.parametrize('same_key',[True,False])
def test_parallel_posts_exactly_one_asr_and_one_fixed_reservation(database,monkeypatch,same_key):
    key=setup(database,monkeypatch);one=request_for(key,DATA);two=dict(one) if same_key else {**one,'idempotency_key':str(uuid.uuid4())}
    values=run(database,key,[(DATA,one),(DATA,two)])
    assert sum(v.get('ok',False) for v in values)==(2 if same_key else 1),values
    assert database.execute('SELECT count(*) AS n FROM test_audio_calls')[0]['n']==1
    assert database.execute("SELECT count(*) AS n FROM saas_dialog_audio_attempts WHERE origin='manual'")[0]['n']==1
    assert database.execute('SELECT reserved_calls FROM saas_chat_ai_runs')==[{'reserved_calls':1}]
    assert database.execute('SELECT state,reserved_microusd,actual_microusd FROM saas_ai_spend_reservations')==[{'state':'settled','reserved_microusd':60000,'actual_microusd':60000}]


def test_reversed_alias_contenders_use_one_sorted_lock_order(database,monkeypatch):
    key=setup(database,monkeypatch)
    other={**DATA,'channel_id':CB,'message_id':EXT2,'message_row_id':OTHER}
    values=run(database,key,[(DATA,request_for(key,DATA,aliases=True)),(other,request_for(key,other,aliases=True))],aliases=True)
    assert sum(v.get('ok',False) for v in values)==1,values
    assert database.execute('SELECT count(*) AS n FROM test_audio_calls')[0]['n']==1


def test_api_and_artifact_claim_share_actual_upload_byte_guard(database,monkeypatch):
    key=setup(database,monkeypatch);request=request_for(key,DATA)
    values=run(database,key,[(DATA,request),(DATA,{})],kinds=['api','cache'])
    assert sum(v.get('ok',False) for v in values)==1,values
    assert database.execute('SELECT count(*) AS n FROM test_audio_calls')[0]['n']==1


def admission_with_actual_agent_share(dsn,key,request,entered,shared,results):
    import tenant_operator_case_submission as cases
    from tenant_dialog_ai_context import resolve
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row,application_name='dialog-audio-admission-lock-race')
    flow._connect=factory;flow._fernet=lambda:Fernet(key)
    cases.assignment=lambda *args:{'agent_id':AGENT,'assignment_revision':0}
    def actual_share(cur,profile,source,**kw):
        resolve(cur,A,{'channel_id':CA,'conversation_id':'synthetic-chat'})
        shared.set()
        return authority(source)
    flow._authority=actual_share
    entered.set()
    try:
        auth,attempt,dispatch=flow._admit(PROFILE,request,(wav(),'wav','audio/wav'),hashlib.sha256(wav()).hexdigest())
        if dispatch:
            with factory() as conn:conn.execute("INSERT INTO test_audio_calls VALUES('admitted-once')")
        results.put({'ok':True,'dispatch':dispatch})
    except BaseException as error:results.put({'error':type(error).__name__,'detail':str(error)[:100]})


def test_admission_tenant_lock_precedes_actual_assigned_agent_share(database,monkeypatch):
    key=setup(database,monkeypatch)
    database.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,model,created_by) VALUES(%s::uuid,%s::uuid,'Synthetic assigned','gpt-4.1',11)",(A,AGENT));database.commit()
    request=request_for(key,DATA)
    context=multiprocessing.get_context('fork');entered=context.Event();shared=context.Event();results=context.Queue()
    worker=context.Process(target=admission_with_actual_agent_share,args=(database.dsn,key,request,entered,shared,results))
    with psycopg.connect(database.dsn,row_factory=dict_row) as automatic:
        automatic.execute('SET LOCAL lock_timeout=\'3s\'')
        automatic.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-chat-ai:'+A,))
        worker.start()
        try:
            assert entered.wait(10)
            # Wait for actual lock contention, not a scheduler-dependent delay.
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                database.execute('SELECT pg_stat_clear_snapshot()')
                waiting=database.execute("SELECT 1 FROM pg_stat_activity WHERE application_name='dialog-audio-admission-lock-race' AND wait_event_type='Lock'")
                if waiting:break
                time.sleep(.01)
            assert waiting and not shared.is_set(), 'Agent share must not precede the tenant lock'
            automatic.execute('SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(A,AGENT))
            automatic.commit()
            value=results.get(timeout=15)
            assert value=={'ok':True,'dispatch':True},value
            worker.join(5);assert worker.exitcode==0
        finally:
            automatic.rollback()
            if worker.is_alive():worker.terminate();worker.join(5)
    assert database.execute('SELECT count(*) AS n FROM test_audio_calls')[0]['n']==1
    assert database.execute("SELECT count(*) AS n FROM saas_dialog_audio_attempts WHERE origin='manual'")[0]['n']==1
