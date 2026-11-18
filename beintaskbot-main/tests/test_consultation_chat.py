"""Synthetic provider-only case chat: no external network or customer sends."""
import uuid
import pytest
from test_case_consultations_unified import database,db,ready,case_ready,PROFILE,A,B,CA,TARGET,flow
from test_dialog_consultations import reply
from tenant_linear_policy import TenantLinearError
import tenant_consultation_chat as chat


def start(case_ready):
    local,data,sender=case_ready
    created=flow.prepare(PROFILE,data,'https://crm.test')
    assert flow.dispatch(PROFILE,created['id'],data['request_id'])['state']=='sent'
    sender.return_value={'message_id':'second-connect','external_message_id':'SECOND_PROVIDER'}
    return local,data,sender,created['id']


def test_followup_exact_body_fixed_destination_idempotent_and_inbox(case_ready):
    local,data,sender,key=start(case_ready)
    request={'request_id':str(uuid.uuid4()),'text':'Please check the printer again'}
    assert chat.send(PROFILE,key,request)['state']=='sent'
    assert chat.send(PROFILE,key,request)['state']=='sent'
    assert sender.call_count==2
    assert sender.call_args.args[0]['conversation_id']==TARGET
    assert sender.call_args.args[1]==request['text']
    thread=flow.inbox(PROFILE,case_id=data['case_id'])['consultations'][0]
    assert len(thread['deliveries'])==2
    assert thread['deliveries'][1]['body']==request['text']
    assert thread['deliveries'][1]['state']=='sent'
    assert thread['provider_message_id']=='PROVIDER_123'
    assert 'lead_id=99' in thread['destination_url']
    assert reply(local,{'id':key},quoted='SECOND_PROVIDER')
    assert flow.inbox(PROFILE,case_id=data['case_id'])['replies'][0]['body']=='Replace cable'
    with pytest.raises(TenantLinearError):chat.send(PROFILE,key,{**request,'text':'Changed request'})
    assert sender.call_count==2


@pytest.mark.parametrize('state',['pending','unknown'])
def test_pending_unknown_retained_never_retried(case_ready,state):
    local,data,sender,key=start(case_ready)
    request={'request_id':str(uuid.uuid4()),'text':'Follow-up'}
    if state=='pending':sender.return_value={'state':'pending','message_id':'queued-id'}
    else:sender.side_effect=TimeoutError('Synthetic timeout')
    assert chat.send(PROFILE,key,request)['state']==state
    assert chat.send(PROFILE,key,request)['state']==state
    with pytest.raises(TenantLinearError):chat.send(PROFILE,key,{'request_id':str(uuid.uuid4()),'text':'Another'})
    assert sender.call_count==2


def test_followup_without_new_share_after_expiry(case_ready):
    local,data,sender,key=start(case_ready)
    local.execute("UPDATE saas_public_case_shares SET expires_at=now()-interval '1 day'");local.commit()
    assert chat.send(PROFILE,key,{'request_id':str(uuid.uuid4()),'text':'Still checking'})['state']=='sent'
    assert local.execute('SELECT count(*) AS n FROM saas_public_case_shares')[0]['n']==1


def test_closed_cross_tenant_and_route_fences(case_ready):
    local,data,sender,key=start(case_ready)
    request={'request_id':str(uuid.uuid4()),'text':'Check'}
    with pytest.raises(TenantLinearError):chat.send({'tenant_id':B,'telegram_id':22},key,request)
    with pytest.raises(TenantLinearError):chat.send(PROFILE,key,{**request,'destination_conversation_id':'other'})
    local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE id=%s::uuid",(data['case_id'],));local.commit()
    with pytest.raises(TenantLinearError):chat.send(PROFILE,key,request)
    assert sender.call_count==1


def test_signed_pending_followup_receipt_recovers_exact_request(case_ready):
    local,data,sender,key=start(case_ready)
    request={'request_id':str(uuid.uuid4()),'text':'Check again'}
    sender.return_value={'state':'pending','message_id':'queued-followup'}
    assert chat.send(PROFILE,key,request)['state']=='pending'
    with local.cursor() as cur:
        assert not flow.observe_outbound_receipt(cur,B,CA,TARGET,{'_crm_signed_outbound_receipt':True,'id':'queued-followup','externalMessageId':'PROVIDER_FOLLOWUP','originClientMessageId':request['request_id']})
        assert flow.observe_outbound_receipt(cur,A,CA,TARGET,{'_crm_signed_outbound_receipt':True,'id':'queued-followup','externalMessageId':'PROVIDER_FOLLOWUP','originClientMessageId':request['request_id']})
    local.commit()
    assert chat.send(PROFILE,key,request)['state']=='sent'
    assert reply(local,{'id':key},quoted='PROVIDER_FOLLOWUP')
    assert sender.call_count==2


def test_missing_marker_blocks_without_new_intent(case_ready):
    local,data,sender,key=start(case_ready)
    local.execute('UPDATE saas_dialog_developer_markers SET enabled=FALSE WHERE tenant_id=%s::uuid',(A,));local.commit()
    with pytest.raises(TenantLinearError):chat.send(PROFILE,key,{'request_id':str(uuid.uuid4()),'text':'Check'})
    assert sender.call_count==1
    assert local.execute('SELECT count(*) AS n FROM saas_consultation_group_deliveries')[0]['n']==1


def test_chat_schema_upgrade_preserves_original_receipts(case_ready):
    local,data,sender,key=start(case_ready)
    local.execute('ALTER TABLE saas_consultation_group_deliveries DROP COLUMN is_chat_followup');local.commit()
    with pytest.raises(TenantLinearError):chat.send(PROFILE,key,{'request_id':str(uuid.uuid4()),'text':'Check'})
    flow.schema(local)
    row=local.execute('SELECT is_chat_followup,state,provider_message_id FROM saas_consultation_group_deliveries')[0]
    assert row=={'is_chat_followup':False,'state':'sent','provider_message_id':'PROVIDER_123'}
    assert sender.call_count==1


def test_bounded_delivery_pagination_is_complete_and_scoped(case_ready):
    local,data,sender,key=start(case_ready)
    local.execute('''INSERT INTO saas_consultation_group_deliveries
      (tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by,is_chat_followup,created_at)
      SELECT tenant_id,consultation_id,md5(i::text)::uuid,case_id,channel_id,conversation_id,
        'old '||i,'hash','sent',created_by,TRUE,created_at-interval '1 minute'*i
      FROM saas_consultation_group_deliveries CROSS JOIN generate_series(1,205) i WHERE request_id=%s::uuid''',(data['request_id'],));local.commit()
    newest=chat.read(PROFILE,key)
    assert len(newest['deliveries'])==200 and newest['has_earlier']
    older=chat.read(PROFILE,key,newest['next_before'])
    assert len(older['deliveries'])==6 and not older['has_earlier']
    assert len({d['request_id'] for d in older['deliveries']+newest['deliveries']})==206
    with pytest.raises(TenantLinearError):chat.read(PROFILE,key,str(uuid.uuid4()))
    with pytest.raises(TenantLinearError):chat.read({'tenant_id':B,'telegram_id':22},key)


def test_unknown_sibling_same_case_recipient_fences_new_send(case_ready):
    local,data,sender,key=start(case_ready)
    sibling=str(uuid.uuid4());request=str(uuid.uuid4())
    local.execute('''INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,evidence,status,created_by)
      SELECT tenant_id,%s::uuid,case_id,channel_id,conversation_id,'old-sibling','Historical',evidence,status,created_by
      FROM saas_internal_consultations WHERE id=%s::uuid''',(sibling,key))
    local.execute('''INSERT INTO saas_consultation_group_deliveries
      (tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by)
      SELECT tenant_id,%s::uuid,%s::uuid,case_id,channel_id,conversation_id,'Uncertain','hash','unknown',created_by
      FROM saas_consultation_group_deliveries WHERE request_id=%s::uuid''',(sibling,request,data['request_id']));local.commit()
    with pytest.raises(TenantLinearError):chat.send(PROFILE,key,{'request_id':str(uuid.uuid4()),'text':'Do not overtake'})
    assert sender.call_count==1


def test_prepared_original_cannot_overtake_unknown_sibling(case_ready):
    local,data,sender,key=start(case_ready)
    request=str(uuid.uuid4())
    # A historical prepared send can coexist with a newer unknown operation.
    local.execute('''INSERT INTO saas_consultation_group_deliveries
      (tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by,is_chat_followup)
      SELECT tenant_id,consultation_id,%s::uuid,case_id,channel_id,conversation_id,'Uncertain','hash','unknown',created_by,TRUE
      FROM saas_consultation_group_deliveries WHERE request_id=%s::uuid''',(request,data['request_id']))
    local.execute("UPDATE saas_consultation_group_deliveries SET state='ready' WHERE request_id=%s::uuid",(data['request_id'],));local.commit()
    with pytest.raises(TenantLinearError,match='предыдущей отправки'):
        flow.dispatch(PROFILE,key,data['request_id'])
    assert sender.call_count==1
