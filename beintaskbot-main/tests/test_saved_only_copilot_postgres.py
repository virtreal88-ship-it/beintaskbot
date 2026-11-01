"""Actual advisory/unique contention and mandatory monetary reservation; no provider."""
import json
import multiprocessing
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from cryptography.fernet import Fernet
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A,CA,PROFILE
import tenant_saved_only_copilot as gate
import tenant_chat_ai_provider as provider
import tenant_ai_spend_budget as budget

DATA={'saved_only':True,'lead_id':41,'channel_id':CA,'conversation_id':'synthetic-saved-case'}


def child(dsn,key,payload,barrier,release,results,unknown):
    import os
    os.environ['AI_TENANT_SPEND_LIMITS_MICROUSD']=json.dumps({A:1000000})
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    gate._connect=factory;budget._connect=factory;gate._fernet=lambda:Fernet(key)
    wire=provider.request_payload('gpt-4.1','reply',[],'Synthetic draft only',max_output_tokens=900,service_tier='default')
    bound=gate.quote_payload(wire);stable=gate.request_id(A,DATA);first=True
    def read(profile,data):
        nonlocal first
        with factory() as conn:old=conn.execute('SELECT requested_by,input_hash,status,result FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,stable)).fetchone()
        if first:first=False;barrier.wait(20)
        return {'tenant':A,'actor':11,'command':{**DATA,'request_id':stable,'_source_versions':[]},'config':{'daily_limit':100,'copilot_daily_limit':100},
          'connection':{'updated_at':'synthetic-v1'},'model':'gpt-4.1','key':'synthetic-no-live-key','fingerprint':'fixed-snapshot',
          'wire':wire,**bound,'existing':old,'context':{'lines':[],'request':'Synthetic draft only','instructions':None,'extra_context':None,'context_usage':{}},'audit':{}}
    gate._read=read
    def paid(*args,**kwargs):
        kwargs['before_dispatch'](wire)
        with factory() as conn:conn.execute('INSERT INTO test_saved_only_calls(request_id) VALUES(%s)',(stable,))
        if not release.wait(25):raise TimeoutError('Synthetic coordinator timeout')
        if unknown:raise TimeoutError('Synthetic unknown provider outcome')
        return 'One saved draft',{'input_tokens':10,'output_tokens':2,'total_tokens':12}
    provider.generate=paid
    try:results.put({'ok':True,'text':gate.execute(PROFILE,payload)['text']})
    except BaseException as error:results.put({'ok':False,'error':type(error).__name__})


@pytest.mark.parametrize('unknown',[False,True])
def test_two_processes_same_consent_make_one_dispatch_and_one_financial_hold(database,monkeypatch,unknown):
    db=database
    db.execute('TRUNCATE saas_tenants CASCADE');db.commit()
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Synthetic','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Synthetic owner')",(A,))
    budget.ensure_schema(db)
    db.execute('TRUNCATE saas_ai_spend_reservations');db.commit()
    db.execute('CREATE TABLE IF NOT EXISTS test_saved_only_calls(request_id TEXT)');db.execute('TRUNCATE test_saved_only_calls');db.commit()
    key=Fernet.generate_key();gate_key=Fernet(key)
    wire=provider.request_payload('gpt-4.1','reply',[],'Synthetic draft only',max_output_tokens=900,service_tier='default');bound=gate.quote_payload(wire)
    stable=gate.request_id(A,DATA)
    state={'tenant':A,'actor':11,'command':{'request_id':stable},'fingerprint':'fixed-snapshot',**bound}
    monkeypatch.setattr(gate,'_fernet',lambda:gate_key)
    token=gate._token(state,9999999999);payload={**DATA,'request_id':stable,'cost_token':token}
    context=multiprocessing.get_context('fork');barrier=context.Barrier(2);release=context.Event();results=context.Queue()
    workers=[context.Process(target=child,args=(db.dsn,key,payload,barrier,release,results,unknown)) for _ in range(2)]
    try:
        for worker in workers:worker.start()
        first=results.get(timeout=40);release.set();second=results.get(timeout=40)
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert sum(item['ok'] for item in [first,second])==(0 if unknown else 1)
        assert db.execute('SELECT count(*) AS n FROM test_saved_only_calls')[0]['n']==1
        assert db.execute('SELECT count(*) AS n FROM saas_chat_ai_runs')[0]['n']==1
        rows=db.execute('SELECT state,reserved_microusd FROM saas_ai_spend_reservations')
        assert len(rows)==1 and rows[0]['state']==('unknown' if unknown else 'settled') and rows[0]['reserved_microusd']==bound['reserve']
    finally:
        release.set()
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)
