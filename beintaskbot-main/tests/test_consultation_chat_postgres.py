"""Case-chat concurrency on disposable native PostgreSQL; only a DB send counter."""
import multiprocessing
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from test_dialog_consultations_postgres import database,prepared,PROFILE,A,CA,TARGET


def child(dsn,key,request,profile,barrier,results):
    import tenant_dialog_consultations as flow
    import tenant_consultation_chat as chat
    import tenant_connect_transport as transport
    flow._connect=lambda:psycopg.connect(dsn,row_factory=dict_row)
    transport.connect_route=lambda *_a,**_k:{'api_key':'synthetic','channel_id':CA,'conversation_id':TARGET}
    def submit(_route,_body,nonce):
        with psycopg.connect(dsn) as conn:conn.execute("INSERT INTO test_external_sends VALUES('case-chat',%s)",(nonce,))
        return {'state':'pending','message_id':'queue-'+nonce,'external_message_id':None}
    transport.send_consultation_with_receipt=submit
    try:
        barrier.wait(20)
        results.put(('ok',chat.send(profile,key,{'request_id':request,'text':'Synthetic clarification'})))
    except Exception as error:results.put(('error',type(error).__name__,str(error)))


def race(db,operations):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(len(operations));queue=ctx.Queue()
    workers=[ctx.Process(target=child,args=(db.dsn,*operation,barrier,queue)) for operation in operations]
    for worker in workers:worker.start()
    try:
        values=[queue.get(timeout=45) for _ in workers]
        for worker in workers:worker.join(10);assert worker.exitcode==0
        return values
    finally:
        for worker in workers:
            if worker.is_alive():worker.terminate();worker.join(5)


@pytest.fixture
def case_chat(prepared):
    db,key,request=prepared
    import tenant_support_inbox_store as tickets
    with db.cursor() as cur:case=tickets.create_in_transaction(cur,A,CA,'client','Synthetic','case-chat',actor=11)['id']
    db.execute('UPDATE saas_internal_consultations SET case_id=%s::uuid WHERE id=%s::uuid',(case,key))
    db.execute("UPDATE saas_consultation_group_deliveries SET case_id=%s::uuid,state='sent',connect_message_id='original-connect',provider_message_id='ORIGINAL_PROVIDER' WHERE request_id=%s::uuid",(case,request))
    db.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,reason) VALUES(%s::uuid,%s,'human','test') ON CONFLICT DO NOTHING",(A,TARGET));db.commit()
    return db,key,request,case


def test_same_request_concurrent_has_one_submission(case_chat):
    db,key,_original,_case=case_chat;request=str(uuid.uuid4())
    values=race(db,[(key,request,PROFILE),(key,request,PROFILE)])
    assert any(v[0]=='ok' and v[1]['state']=='pending' for v in values),values
    assert all(v[0]=='ok' or v[1]=='TenantLinearError' for v in values),values
    assert len(db.execute('SELECT * FROM test_external_sends'))==1


def test_sibling_consultation_cannot_overtake_pending(case_chat):
    db,key,original,case=case_chat;sibling=str(uuid.uuid4());sibling_original=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,evidence,status,created_by)
      SELECT tenant_id,%s::uuid,case_id,channel_id,conversation_id,'historical-sibling','Historical',evidence,status,created_by
      FROM saas_internal_consultations WHERE id=%s::uuid''',(sibling,key))
    db.execute('''INSERT INTO saas_consultation_group_deliveries
      (tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by,connect_message_id,provider_message_id)
      SELECT tenant_id,%s::uuid,%s::uuid,case_id,channel_id,conversation_id,body,body_hash,'sent',created_by,'sibling-original','SIBLING_PROVIDER'
      FROM saas_consultation_group_deliveries WHERE request_id=%s::uuid''',(sibling,sibling_original,original));db.commit()
    values=race(db,[(key,str(uuid.uuid4()),PROFILE),(sibling,str(uuid.uuid4()),PROFILE)])
    assert sum(v[0]=='ok' and v[1]['state']=='pending' for v in values)==1,values
    assert sum(v[0]=='error' and v[1]=='TenantLinearError' for v in values)==1,values
    assert len(db.execute('SELECT * FROM test_external_sends'))==1


def test_concurrent_other_tenant_cannot_send_or_read(case_chat):
    db,key,_original,_case=case_chat;other=str(uuid.uuid4());request=str(uuid.uuid4())
    db.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s::uuid,%s,'Other','active',22)",(other,other))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,22,'owner','Other owner')",(other,));db.commit()
    values=race(db,[(key,request,PROFILE),(key,request,{'tenant_id':other,'telegram_id':22})])
    assert sum(v[0]=='ok' and v[1]['state']=='pending' for v in values)==1,values
    assert sum(v[0]=='error' and v[1]=='TenantLinearError' for v in values)==1,values
    assert len(db.execute('SELECT * FROM test_external_sends'))==1
