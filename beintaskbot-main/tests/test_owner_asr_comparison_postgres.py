"""Real parallel admission on the existing disposable localhost PostgreSQL fixture."""
import json
import multiprocessing
import time
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A,CA,AGENT,PROFILE
import tenant_owner_asr_comparison as flow

SOURCE={'channel_id':CA,'conversation_id':'synthetic-approved-chat','message_id':'synthetic-approved-audio'}
META={'enabled':True,'consent':True,'audio_enabled':True,'daily_limit':20,'copilot_daily_limit':20}


def child(dsn,barrier,results):
    import tenant_ai_spend_budget as budget
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    flow._connect=factory;budget._connect=factory
    flow.APPROVED_SCOPE=flow.scope_digest(A,SOURCE)
    flow._authority=lambda *_:(A,11,{'lead_id':41},{'agent_id':AGENT},META,'fixed-version')
    flow._company_key=lambda *_:(A,META,'synthetic-key')
    def download(*_):
        barrier.wait(20)  # Both callers passed preview; contend at real admission.
        return b'identical-synthetic-audio','audio/ogg'
    flow._download=download
    flow._audio=lambda content,mime:(content,'ogg',mime,8.95)
    def provider(key,model,*_):
        with factory() as conn:conn.execute("INSERT INTO test_external_sends(channel,run_id) VALUES('owner-asr',%s)",(model,))
        time.sleep(.05)
        return {'transcript':'synthetic '+model,'usage':{},'diagnostics':{}}
    flow._transcribe=provider
    try:
        result=flow.execute(PROFILE,{**SOURCE,'version':'fixed-version','confirmed':True,'max_calls':2,'max_cost_microusd':60000})
        results.put({'ok':result['admitted'],'can_execute':result['can_execute']})
    except BaseException as error:results.put({'error':type(error).__name__})


def test_parallel_owner_posts_admit_two_calls_once_and_preserve_fixed_reservations(database,monkeypatch):
    db=database;flow.schema(db)
    db.execute('TRUNCATE saas_tenants CASCADE');db.execute('TRUNCATE test_external_sends')
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Synthetic company','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Synthetic owner')",(A,));db.commit()
    monkeypatch.setenv('AI_TENANT_SPEND_LIMITS_MICROUSD',json.dumps({A:1000000}))
    monkeypatch.setenv('AI_MEDIA_OPERATION_PRICES_MICROUSD',json.dumps({'gpt-4o-mini-transcribe':{'asr':30000}}))
    context=multiprocessing.get_context('fork');barrier=context.Barrier(2);results=context.Queue()
    workers=[context.Process(target=child,args=(db.dsn,barrier,results)) for _ in range(2)]
    try:
        for worker in workers:worker.start()
        values=[results.get(timeout=40) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert values==[{'ok':True,'can_execute':False}]*2
        assert db.execute("SELECT count(*) AS n FROM test_external_sends WHERE channel='owner-asr'")[0]['n']==2
        assert db.execute('SELECT count(*) AS n FROM saas_owner_asr_comparisons')[0]['n']==1
        assert db.execute('SELECT count(*) AS n FROM saas_owner_asr_variants')[0]['n']==2
        rows=db.execute('SELECT state,reserved_microusd FROM saas_ai_spend_reservations')
        assert len(rows)==2 and all(row['state']=='settled' for row in rows)
        assert sum(row['reserved_microusd'] for row in rows)==60000
        assert db.execute('SELECT reserved_calls FROM saas_chat_ai_runs')==[{'reserved_calls':2}]
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)
