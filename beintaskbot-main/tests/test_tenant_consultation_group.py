"""Isolated SQL tests; external delivery is always synthetic."""
import uuid
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, PROFILE
import tenant_consultation_group as flow
import tenant_support_inbox_store as tickets
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError

GROUP='120363411822115409@g.us'

def setup_case(db, monkeypatch):
    monkeypatch.setattr(flow, '_connect', lambda: db)
    flow.schema(db)
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,99,'Group',now())",(A,CA,GROUP))
    with db.cursor() as cur:
        ticket=tickets.create_in_transaction(cur,A,CA,'client','Synthetic issue','test-group',actor=11)
    case=ticket['id']; consult=str(uuid.uuid4())
    db.execute("""INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,status,recipient_id)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client',%s,'Synthetic issue','waiting_specialist',11)""",(A,consult,case,CA,'consult-'+consult))
    db.commit()
    monkeypatch.setattr(transport,'connect_route',lambda *_: {'api_key':'synthetic','channel_id':CA,'conversation_id':GROUP})
    return consult,case

def request():
    return {'request_id':str(uuid.uuid4()),'channel_id':CA,'conversation_id':GROUP,
      'summary':'A synthetic question','confirm_group':True,'attachment_ids':[]}

def test_one_shot_receipt_and_reply_that_arrives_during_send(db,monkeypatch):
    consult,case=setup_case(db,monkeypatch); payload=request()
    assert flow.prepare(PROFILE,consult,payload)['created']
    assert not flow.prepare(PROFILE,consult,payload)['created']
    calls=[]
    def send(route,body,request_id):
        calls.append((body,request_id))
        with db.cursor() as cur:
            cur.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw)
              VALUES(%s::uuid,%s::uuid,99,'connect:inbound-1','incoming',%s,'Synthetic reply','text',%s::jsonb)""",
              (str(uuid.uuid4()),A,'connect:'+CA,'{"id":"inbound-1","quotedExternalMessageId":"PROVIDER_123"}'))
            assert flow.record_group_reply(cur,A,CA,GROUP,'inbound-1','Synthetic reply','PROVIDER_123') is None
        db.commit()
        return {'message_id':'connect-id','external_message_id':'PROVIDER_123'}
    monkeypatch.setattr(transport,'send_group_with_receipt',send)
    assert flow.dispatch(PROFILE,consult,payload['request_id'])['state']=='sent'
    assert flow.dispatch(PROFILE,consult,payload['request_id'])['state']=='sent'
    assert len(calls)==1 and ('CASE:'+consult+'\n') in calls[0][0]
    rows=db.execute('SELECT consultation_id::text,body FROM saas_consultation_group_replies WHERE tenant_id=%s::uuid',(A,))
    assert rows==[{'consultation_id':consult,'body':'Synthetic reply'}]
    with db.cursor() as cur:
        assert flow.record_group_reply(cur,A,CA,GROUP,'inbound-1','Synthetic reply','PROVIDER_123') is None
        assert flow.record_group_reply(cur,B,CA,GROUP,'inbound-2','Synthetic reply','PROVIDER_123') is None
        assert flow.record_group_reply(cur,A,CA,'120363411822115408@g.us','inbound-3','Synthetic reply','PROVIDER_123') is None
    db.commit()

def test_unknown_delivery_is_never_retried_and_media_is_not_silently_dropped(db,monkeypatch):
    consult,_=setup_case(db,monkeypatch); payload=request()
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,consult,{**payload,'attachment_ids':['synthetic']})
    assert flow.prepare(PROFILE,consult,payload)['created']
    calls=[]
    def unknown(*args):calls.append(args);raise RuntimeError('synthetic network error')
    monkeypatch.setattr(transport,'send_group_with_receipt',unknown)
    with pytest.raises(TenantLinearError):flow.dispatch(PROFILE,consult,payload['request_id'])
    with pytest.raises(TenantLinearError):flow.dispatch(PROFILE,consult,payload['request_id'])
    assert len(calls)==1

def test_preparation_requires_owner_explicit_destination_and_is_tenant_scoped(db,monkeypatch):
    consult,_=setup_case(db,monkeypatch); payload=request()
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,consult,{**payload,'confirm_group':False})
    with pytest.raises(TenantLinearError):flow.prepare({'tenant_id':B,'telegram_id':22},consult,payload)
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,consult,{**payload,'conversation_id':'not-a-group'})
    assert flow.prepare(PROFILE,consult,payload)['created']
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,consult,{**payload,'summary':'Different summary'})
    with pytest.raises(TenantLinearError):flow.dispatch(PROFILE,consult,str(uuid.uuid4()))

def test_two_concurrent_cases_require_exact_quote_or_unambiguous_marker(db,monkeypatch):
    first,_=setup_case(db,monkeypatch);first_request=request()
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'client2',100,'Client',now())",(A,CA))
    with db.cursor() as cur:
        second_case=tickets.create_in_transaction(cur,A,CA,'client2','Other issue','second-group',actor=11)['id']
    second=str(uuid.uuid4())
    db.execute("""INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,status,recipient_id)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client2',%s,'Other issue','waiting_specialist',11)""",(A,second,second_case,CA,'consult-'+second))
    db.commit()
    second_request=request()
    flow.prepare(PROFILE,first,first_request);flow.prepare(PROFILE,second,second_request)
    receipts=iter([{'message_id':'connect-1','external_message_id':'PROVIDER_111'},
                   {'message_id':'connect-2','external_message_id':'PROVIDER_222'}])
    monkeypatch.setattr(transport,'send_group_with_receipt',lambda *_:next(receipts))
    flow.dispatch(PROFILE,first,first_request['request_id']);flow.dispatch(PROFILE,second,second_request['request_id'])
    with db.cursor() as cur:
        assert flow.record_group_reply(cur,A,CA,GROUP,'unmatched','No reference') is None
        assert flow.record_group_reply(cur,A,CA,GROUP,'conflict','CASE:'+second,'PROVIDER_111') is None
        assert flow.record_group_reply(cur,A,CA,GROUP,'ambiguous','CASE:'+first+' CASE:'+second) is None
        assert flow.record_group_reply(cur,A,CA,GROUP,'first-reply','Synthetic answer','PROVIDER_111')==first
        assert flow.record_group_reply(cur,A,CA,GROUP,'second-reply','CASE:'+second+' Synthetic answer')==second
    db.commit()
    rows=db.execute('SELECT consultation_id::text AS id,message_id FROM saas_consultation_group_replies ORDER BY message_id')
    assert rows==[{'id':first,'message_id':'first-reply'},{'id':second,'message_id':'second-reply'}]
