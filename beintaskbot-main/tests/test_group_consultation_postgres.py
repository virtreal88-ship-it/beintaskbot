"""Real PostgreSQL races; provider sends are a local database counter."""
import multiprocessing
import time
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import A,CA,PROFILE
import tenant_consultation_group as flow
import tenant_support_inbox_store as tickets

GROUP='120363411822115409@g.us'

def child(dsn,kind,consult,data,barrier,results):
    import tenant_consultation_group as local
    import tenant_connect_transport as transport
    local._connect=lambda:psycopg.connect(dsn,row_factory=dict_row)
    transport.connect_route=lambda *_:{'api_key':'synthetic','channel_id':CA,'conversation_id':GROUP}
    def send(_route,_body,request):
        with psycopg.connect(dsn) as conn:
            group_state=conn.execute('SELECT state FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND group_jid=%s',(A,GROUP)).fetchone()[0]
            conn.execute('INSERT INTO test_external_sends VALUES(%s,%s)',(group_state,request))
        time.sleep(.15)
        return {'message_id':'synthetic','external_message_id':'PROVIDER_123'}
    transport.send_group_with_receipt=send
    try:
        barrier.wait(20)
        if kind=='prepare':value=local.prepare(PROFILE,consult,data)
        elif kind=='activate':
            from tenant_ai_dialog_scope import lock_dialog
            with local._connect() as conn:
                with conn.cursor() as cur:
                    lock_dialog(cur,A,'connect',CA,GROUP)
                    cur.execute("UPDATE saas_ai_group_coordination SET state='active',revision=revision+1 WHERE tenant_id=%s::uuid AND group_jid=%s",(A,GROUP))
                conn.commit()
            value={'state':'activated'}
        else:value=local.dispatch(PROFILE,consult,data['request_id'])
        results.put(('ok',value.get('created',value.get('state'))))
    except Exception as error:results.put(('error',type(error).__name__))

@pytest.mark.parametrize('same_request',[True,False])
def test_prepare_and_send_are_singleflight(database,same_request):
    db=database;flow.schema(db)
    db.execute('TRUNCATE saas_tenants CASCADE');db.execute('TRUNCATE test_external_sends');db.commit()
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Company','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Owner')",(A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Test')",(A,CA))
    for conversation,lead in [('client',41),(GROUP,99)]:
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,%s,'Test',now())",(A,CA,conversation,lead))
    db.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,reason) VALUES(%s::uuid,%s,'human','existing')",(A,GROUP))
    with db.cursor() as cur:case=tickets.create_in_transaction(cur,A,CA,'client','Synthetic','race',actor=11)['id']
    consult=str(uuid.uuid4());db.execute("""INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,status,recipient_id)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client','race-consult','Synthetic','waiting_specialist',11)""",(A,consult,case,CA));db.commit()
    payload={'request_id':str(uuid.uuid4()),'channel_id':CA,'conversation_id':GROUP,'summary':'Synthetic','confirm_group':True,'attachment_ids':[]}
    second={**payload,'request_id':payload['request_id'] if same_request else str(uuid.uuid4())}
    ctx=multiprocessing.get_context('spawn')
    def race(kind,values):
        barrier=ctx.Barrier(2);queue=ctx.Queue();workers=[ctx.Process(target=child,args=(db.dsn,kind,consult,value,barrier,queue)) for value in values]
        for worker in workers:worker.start()
        try:
            result=[queue.get(timeout=30) for _ in workers]
            for worker in workers:worker.join(10);assert worker.exitcode==0
            return result
        finally:
            for worker in workers:
                if worker.is_alive():worker.terminate();worker.join(5)
    results=race('prepare',[payload,second])
    assert sum(value==('ok',True) for value in results)==1,results
    assert len(db.execute('SELECT request_id FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid',(A,)))==1
    row=db.execute('SELECT request_id FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid',(A,))[0];db.commit()
    selected={'request_id':str(row['request_id'])}
    race('dispatch',[selected,selected])
    assert len(db.execute('SELECT run_id FROM test_external_sends'))==1
    assert db.execute('SELECT state FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid',(A,))[0]['state']=='sent'
    db.commit()

def test_group_activation_and_delivery_serialize(database,monkeypatch):
    db=database;flow.schema(db)
    db.execute('TRUNCATE saas_tenants CASCADE');db.execute('TRUNCATE test_external_sends');db.commit()
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Company','active',11)",(A,A))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,11,'owner','Owner')",(A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Test')",(A,CA))
    for conversation,lead in [('client',41),(GROUP,99)]:
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,%s,'Test',now())",(A,CA,conversation,lead))
    db.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,reason) VALUES(%s::uuid,%s,'human','existing')",(A,GROUP))
    with db.cursor() as cur:case=tickets.create_in_transaction(cur,A,CA,'client','Synthetic','race',actor=11)['id']
    consult=str(uuid.uuid4());db.execute("""INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,status,recipient_id)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client','race-consult','Synthetic','waiting_specialist',11)""",(A,consult,case,CA));db.commit()
    monkeypatch.setattr(flow,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    payload={'request_id':str(uuid.uuid4()),'channel_id':CA,'conversation_id':GROUP,'summary':'Synthetic','confirm_group':True,'attachment_ids':[]}
    flow.prepare(PROFILE,consult,payload)
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);queue=ctx.Queue()
    workers=[ctx.Process(target=child,args=(db.dsn,kind,consult,payload,barrier,queue)) for kind in ('dispatch','activate')]
    for worker in workers:worker.start()
    try:
        results=[queue.get(timeout=30) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        assert ('ok','activated') in results,results
        sends=db.execute('SELECT channel FROM test_external_sends')
        assert len(sends)<=1 and all(row['channel']=='human' for row in sends)
        delivery=db.execute('SELECT state FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid',(A,))[0]['state']
        assert delivery==('sent' if sends else 'blocked')
        db.commit()
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)
