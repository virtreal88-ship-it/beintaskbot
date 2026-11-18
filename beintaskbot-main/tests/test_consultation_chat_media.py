"""Case media binding tests: isolated PostgreSQL/WASM and synthetic provider only."""
import uuid
import pytest
from test_consultation_chat import database,db,ready,case_ready,start,PROFILE,A,B,CA,TARGET
from tenant_linear_policy import TenantLinearError
from tenant_chat_upload_policy import prepare
import tenant_chat_send_store as store
import tenant_consultation_chat_media as binding
import tenant_consultation_chat as chat


@pytest.fixture
def media_case(case_ready,monkeypatch):
    local,data,sender,key=start(case_ready)
    monkeypatch.setenv('CASE_CHAT_MEDIA_ENABLED','true')
    monkeypatch.setattr(store,'_connect',lambda:local)
    def authority(cur,session,lead,**kwargs):
        return {**session,'active':True,'tenant_status':'active'},['connect',CA,TARGET],{'provider':'connect','route':{'channel_id':CA,'conversation_id':TARGET,'api_key':'synthetic'}}
    monkeypatch.setattr(store,'authority',authority)
    command=prepare({'request_id':str(uuid.uuid4()),'lead_id':99,'text':'Look here','consultation_id':key},'screen.pdf','application/pdf',b'%PDF-1.7 synthetic')
    return local,data,key,command


def test_media_binding_is_reserved_atomically_and_unknown_blocks_case(media_case):
    local,data,key,command=media_case
    item=store.reserve(PROFILE,command)
    row=local.execute('SELECT * FROM saas_consultation_group_deliveries WHERE request_id=%s::uuid',(command['request_id'],))[0]
    assert row['state']=='ready' and row['media_metadata']['name']=='screen.pdf'
    assert str(row['consultation_id'])==key and row['conversation_id']==TARGET
    saved=local.execute('SELECT route FROM saas_chat_send_receipts WHERE request_id=%s::uuid',(command['request_id'],))[0]['route']
    assert saved['consultation_id']==key
    store.finish(item,'unknown')
    assert local.execute('SELECT state FROM saas_consultation_group_deliveries WHERE request_id=%s::uuid',(command['request_id'],))[0]['state']=='unknown'
    with pytest.raises(TenantLinearError):chat.send(PROFILE,key,{'request_id':str(uuid.uuid4()),'text':'Do not overtake'})
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,command)


def test_fixed_recipient_flag_closed_case_and_cross_tenant(media_case,monkeypatch):
    local,data,key,command=media_case
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,{**command,'lead_id':41})
    with pytest.raises(TenantLinearError):store.reserve({'tenant_id':B,'telegram_id':22},command)
    monkeypatch.setenv('CASE_CHAT_MEDIA_ENABLED','false')
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,command)
    monkeypatch.setenv('CASE_CHAT_MEDIA_ENABLED','true')
    local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE id=%s::uuid",(data['case_id'],));local.commit()
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,command)
    assert not local.execute('SELECT * FROM saas_chat_send_receipts')


def test_final_guard_blocks_after_case_closed_no_provider_call(media_case):
    local,data,key,command=media_case
    item=store.reserve(PROFILE,command)
    _,credentials=store.mark_sending(item,{'origin':'connect','media_protocol':True,'channel_id':CA,'conversation_id':TARGET})
    local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE id=%s::uuid",(data['case_id'],));local.commit()
    calls=[]
    with pytest.raises(TenantLinearError):store.dispatch_guarded(item,credentials,lambda *_:calls.append(True))
    assert not calls


def test_receipt_reconciliation_keeps_exact_case_after_close(media_case):
    local,data,key,command=media_case
    item=store.reserve(PROFILE,command)
    store.mark_sending(item,{'origin':'connect','media_protocol':True,'channel_id':CA,'conversation_id':TARGET})
    local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE id=%s::uuid",(data['case_id'],));local.commit()
    route=local.execute('SELECT route FROM saas_chat_send_receipts WHERE request_id=%s::uuid',(command['request_id'],))[0]['route']
    receipt={'message_id':str(uuid.uuid4()),'provider_message_id':'FILE_PROVIDER_1','delivery_state':'sent'}
    with local.cursor() as cur:binding.reconcile(cur,A,11,command['request_id'],route,receipt)
    local.commit()
    row=local.execute('SELECT * FROM saas_consultation_group_deliveries WHERE request_id=%s::uuid',(command['request_id'],))[0]
    assert row['state']=='sent' and row['provider_message_id']=='FILE_PROVIDER_1'
    assert row['media_metadata']['delivery_state']=='sent'
    with pytest.raises(TenantLinearError):
        with local.cursor() as cur:binding.reconcile(cur,A,11,command['request_id'],route,{**receipt,'provider_message_id':'DIFFERENT'})


def test_read_receipt_is_bound_to_case_and_can_send_staff_hint(media_case):
    local,data,key,command=media_case
    item=store.reserve(PROFILE,command)
    binding.validate_read(PROFILE,99,key,command['request_id'])
    with pytest.raises(TenantLinearError):binding.validate_read(PROFILE,99,str(uuid.uuid4()),command['request_id'])
    # The hint follows the existing support ACL, not a hardcoded owner role.
    local.execute("UPDATE saas_tenant_members SET permissions='[\"dialogs\",\"support\"]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=33",(A,));local.commit()
    profile={'tenant_id':A,'telegram_id':33}
    row={'case_id':data['case_id'],'source_channel':CA,'source_conversation':'client'}
    with local.cursor() as cur:assert chat.can_send(cur,profile,row)
    local.execute("UPDATE saas_tenant_members SET permissions='[\"dialogs\"]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=33",(A,));local.commit()
    with local.cursor() as cur:assert not chat.can_send(cur,profile,row)


def test_media_cannot_be_dispatched_as_text(media_case):
    local,data,key,command=media_case
    store.reserve(PROFILE,command)
    from tenant_dialog_consultations import dispatch
    with pytest.raises(TenantLinearError):dispatch(PROFILE,key,command['request_id'])


def test_bound_dispatch_preserves_real_transport_callback(media_case):
    local,data,key,command=media_case
    item=store.reserve(PROFILE,command)
    _,credentials=store.mark_sending(item,{'origin':'connect','media_protocol':True,'channel_id':CA,'conversation_id':TARGET})
    calls=[]
    def submit(route,nonce):
        calls.append((route['channel_id'],route['conversation_id'],nonce))
        return {'provider_accepted':False,'state':'unknown'}
    result=store.dispatch_guarded(item,credentials,submit,command['request_id'])
    assert result['state']=='unknown'
    assert calls==[(CA,TARGET,command['request_id'])]
    store.finish(item,'unknown')
    with pytest.raises(TenantLinearError):store.reserve(PROFILE,command)
    assert len(calls)==1


def test_voice_prepare_preserves_binding_without_bypassing_audio_validation(monkeypatch):
    from tenant_customer_voice_service import prepare as prepare_voice
    key=str(uuid.uuid4())
    monkeypatch.setattr('tenant_customer_voice_service.validate_recording',lambda content,mime:(content,'ogg','audio/ogg'))
    command,_=prepare_voice({'request_id':str(uuid.uuid4()),'lead_id':99,'expected_tenant_id':A,'expected_user_id':11,'consultation_id':key},'audio/ogg',b'synthetic')
    assert command['consultation_id']==key and command['file']['type']=='audio'


@pytest.mark.asyncio
async def test_media_service_acceptance_projects_case_attachment_and_quote(media_case,monkeypatch):
    import logging
    import tenant_customer_media_service as media
    from test_dialog_consultations import reply,flow
    local,data,key,command=media_case
    monkeypatch.setattr(media,'_connect',lambda:local)
    monkeypatch.setattr(media,'authority',store.authority)
    receipt={'provider_accepted':True,'state':'accepted','message_id':str(uuid.uuid4()),'provider_message_id':'CASE_MEDIA_PROVIDER',
             'delivery_state':'sent','media_type':'document','operation_id':str(uuid.uuid4()),'delivered':False,'retry_safe':False}
    calls=[]
    monkeypatch.setattr(media,'_submit',lambda *args:(calls.append(args) or receipt))
    result=await media.run(PROFILE,command,b'%PDF-1.7 synthetic',logging.getLogger('synthetic'))
    assert result['state']=='accepted' and len(calls)==1
    row=chat.read(PROFILE,key)['deliveries'][-1]
    assert row['state']=='sent' and row['media_metadata']['type']=='file'
    assert row['attachment_url'].startswith('/api/platform/crm/chat/attachment?')
    assert 'expected_user_id=11' in row['attachment_url']
    assert reply(local,{'id':key},quoted='CASE_MEDIA_PROVIDER')
    assert len(flow.inbox(PROFILE,case_id=data['case_id'])['replies'])==1


@pytest.mark.asyncio
async def test_direct_voice_service_uses_same_case_binding(media_case,monkeypatch):
    import logging
    import tenant_customer_voice_service as voice
    from test_dialog_consultations import flow
    local,data,key,_command=media_case;direct='994500000001@s.whatsapp.net'
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,100,'Developer direct',now())",(A,CA,direct))
    local.execute('UPDATE saas_consultation_group_deliveries SET conversation_id=%s WHERE consultation_id=%s::uuid',(direct,key));local.commit()
    flow.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':direct,'enabled':True})
    monkeypatch.setattr(voice,'validate_recording',lambda content,mime:(content,'ogg','audio/ogg'))
    command,content=voice.prepare({'request_id':str(uuid.uuid4()),'lead_id':100,'expected_tenant_id':A,'expected_user_id':11,'consultation_id':key},'audio/ogg',b'synthetic')
    authority=lambda cur,session,lead,**kw:({**session,'active':True,'tenant_status':'active'},['connect',CA,direct],{'provider':'connect','route':{'channel_id':CA,'conversation_id':direct,'api_key':'synthetic'}})
    monkeypatch.setattr(store,'authority',authority)
    monkeypatch.setattr(voice,'authority',authority)
    monkeypatch.setattr(voice,'_connect',lambda:local)
    receipt={'provider_accepted':True,'state':'accepted','message_id':str(uuid.uuid4()),'provider_message_id':'CASE_VOICE_PROVIDER',
             'delivery_state':'sent','operation_id':str(uuid.uuid4()),'delivered':False,'retry_safe':False}
    calls=[]
    monkeypatch.setattr(voice,'_submit',lambda *args:(calls.append(args) or receipt))
    result=await voice.run(PROFILE,command,content,logging.getLogger('synthetic'))
    assert result['state']=='accepted' and len(calls)==1
    row=chat.read(PROFILE,key)['deliveries'][-1]
    assert row['state']=='sent' and row['media_metadata']['type']=='audio'
    assert row['provider_message_id']=='CASE_VOICE_PROVIDER'
