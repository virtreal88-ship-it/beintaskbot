"""Real multiprocess PostgreSQL races: disposable localhost only; mocked sends/AI."""
import multiprocessing
import time
import uuid
from unittest.mock import Mock
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A,CA,AGENT,PROFILE
import tenant_dialog_consultations as flow

TARGET='120363411822115409@g.us'


def child(dsn,kind,consult,request,reply,barrier,results):
    import tenant_dialog_consultations as local
    import tenant_connect_transport as transport
    import tenant_operator_copilot as copilot
    import tenant_ai_agents_schema as schema
    schema._ready=True
    factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
    local._connect=factory
    transport.connect_route=lambda *_:{'api_key':'synthetic','channel_id':CA,'conversation_id':TARGET}
    def send(*_):
        with factory() as db:db.execute("INSERT INTO test_external_sends VALUES('consultation',%s)",(request,))
        time.sleep(.15)
        return {'state':'pending','message_id':'connect-id','external_message_id':None} if kind=='dispatch_queued' else {'message_id':'connect-id','external_message_id':'PROVIDER_123'}
    transport.send_consultation_with_receipt=send
    copilot.run=lambda *a,**kw:{'text':'Private draft'}
    try:
        barrier.wait(20)
        if kind in {'dispatch','dispatch_queued'}:value=local.dispatch(PROFILE,consult,request)
        elif kind=='handled':value=local.action(PROFILE,reply,{'action':'handled'})
        elif kind=='draft':value=local.draft(PROFILE,reply,{'request_id':str(uuid.uuid4())})
        else:
            with factory() as db:
                with db.cursor() as cur:value=local.record_reply(cur,A,CA,TARGET,'new-reply','New developer detail','PROVIDER_123','Developer')
            value={'id':value}
        results.put(('ok',value))
    except Exception as error:results.put(('error',type(error).__name__,str(error)))


def race(db,operations,consult,request,reply=None):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(len(operations));results=ctx.Queue()
    workers=[ctx.Process(target=child,args=(db.dsn,kind,consult,request,reply,barrier,results)) for kind in operations]
    for worker in workers:worker.start()
    try:
        values=[results.get(timeout=40) for worker in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        return values
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)


@pytest.fixture
def prepared(database,monkeypatch):
    db=database;flow.schema(db)
    db.execute('TRUNCATE saas_tenants CASCADE');db.execute('TRUNCATE test_external_sends')
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Company','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Owner')",(A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Test')",(A,CA))
    for conversation,lead in [('client',41),(TARGET,99)]:
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,%s,'Test',now())",(A,CA,conversation,lead))
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,enabled) VALUES(%s::uuid,%s::uuid,'NightSupport',11,TRUE)",(A,AGENT))
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'connect',%s,'client',%s::uuid,'human')",(A,CA,AGENT))
    message=str(uuid.uuid4());db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,41,'source',%s,'incoming','Original problem')",(message,A,'connect:'+CA));db.commit()
    monkeypatch.setattr(flow,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    data={'request_id':str(uuid.uuid4()),'channel_id':CA,'conversation_id':'client','destination_channel_id':CA,'destination_conversation_id':TARGET,'message_ids':[message],'media_ids':[],'summary':'Original problem','confirm':True}
    consult=flow.prepare(PROFILE,data,'https://crm.test')['id']
    return db,consult,data['request_id']


@pytest.mark.parametrize('queued',[False,True])
def test_double_click_dispatch_has_exactly_one_provider_attempt(prepared,queued):
    db,consult,request=prepared
    kind='dispatch_queued' if queued else 'dispatch'
    expected='pending' if queued else 'sent'
    results=race(db,[kind,kind],consult,request)
    assert sum(value[0]=='ok' and value[1]['state']==expected for value in results)>=1,results
    assert all(value[0]=='ok' or value[1]=='TenantLinearError' for value in results),results
    assert len(db.execute('SELECT * FROM test_external_sends'))==1
    assert db.execute('SELECT state FROM saas_consultation_group_deliveries')[0]['state']==expected


def sent(prepared):
    db,consult,request=prepared
    assert race(db,['dispatch'],consult,request)[0][0]=='ok'
    with db.cursor() as cur:reply=flow.record_reply(cur,A,CA,TARGET,'old-reply','Old developer answer','PROVIDER_123','Developer')
    db.commit()
    return db,consult,request,reply


def test_handling_old_reply_does_not_clear_concurrent_new_reply(prepared):
    db,consult,request,reply=sent(prepared)
    results=race(db,['handled','new-reply'],consult,request,reply)
    assert all(value[0]=='ok' for value in results),results
    rows=db.execute('SELECT message_id,handled_at FROM saas_dialog_consultation_replies ORDER BY message_id')
    assert {row['message_id']:row['handled_at'] is None for row in rows}=={'old-reply':False,'new-reply':True}


def test_draft_and_handling_share_consultation_first_lock_order(prepared):
    db,consult,request,reply=sent(prepared)
    results=race(db,['draft','handled'],consult,request,reply)
    assert all(value[0]=='ok' for value in results),results
    row=db.execute('SELECT draft_state,handled_at FROM saas_dialog_consultation_replies')[0]
    assert row['draft_state']=='completed' and row['handled_at'] is not None
