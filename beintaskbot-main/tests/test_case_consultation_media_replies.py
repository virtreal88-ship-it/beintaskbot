"""Quoted developer media is correlated without download, transcription or AI."""
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit
from unittest.mock import Mock
import pytest
from test_case_consultations_unified import case_ready,ready,db,database,A,B,CA,CB,PROFILE,TARGET
import tenant_dialog_consultations as flow
import tenant_dialog_consultations_worker as worker
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError


@pytest.mark.parametrize(('kind','canonical'),[('audio','audio'),('video','video'),('image','image'),('document','document'),('voice','audio'),('ptt','audio'),('voice_message','audio'),('photo','image'),('file','document')])
def test_signed_quoted_media_reply_has_protected_original_source_without_processing(case_ready,monkeypatch,kind,canonical):
    local,data,sender=case_ready
    prepared=flow.prepare(PROFILE,data,'https://crm.test')
    flow.dispatch(PROFILE,prepared['id'],data['request_id'])
    monkeypatch.setattr(transport,'_connect',lambda:local)
    monkeypatch.setattr(transport,'_fernet',lambda:SimpleNamespace(decrypt=lambda _:b'{"webhook_secret":"synthetic-secret"}'))
    local.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata) VALUES(%s::uuid,'connect','connected',%s,'{}'::jsonb) ON CONFLICT(tenant_id,provider) DO UPDATE SET secrets=EXCLUDED.secrets",(A,b'synthetic'));local.commit()
    download=Mock();ai=Mock();learning=Mock()
    monkeypatch.setattr(transport,'download_connect_message_media',download)
    monkeypatch.setattr('tenant_ai_agent_connect.enqueue',ai)
    monkeypatch.setattr('tenant_knowledge_live_queue.enqueue',learning)
    message_id=str(uuid.uuid4())
    event={'id':'event-'+kind,'type':'message.inbound.received','occurred_at':'2026-11-01T10:00:00Z','data':{
      'id':message_id,'channelId':CA,'conversationId':TARGET,'isGroup':True,'fromMe':False,'type':kind,
      'quotedExternalMessageId':'PROVIDER_123','participantName':'Developer media author','text':''}}
    raw=json.dumps(event).encode();signature='sha256='+hmac.new(b'synthetic-secret',raw,hashlib.sha256).hexdigest()
    assert transport.ingest(raw,signature,event)
    assert transport.ingest(raw,signature,event)
    thread=flow.inbox(PROFILE,case_id=data['case_id'])
    assert len(thread['replies'])==1
    reply=thread['replies'][0]
    assert reply['message_type']==canonical and reply['draft_state']=='not_requested'
    assert not reply['can_draft']
    assert reply['author']=='Developer media author'
    original=local.execute("SELECT id::text,message_type FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s",(A,'connect:'+message_id))[0]
    assert reply['source_route']=={'provider':'connect','lead_id':99,'channel_id':CA,'conversation_id':TARGET,'source_message_id':original['id']}
    query=parse_qs(urlsplit(reply['attachment_url']).query)
    assert query['message_id']==['connect:'+message_id] and query['conversation_id']==[TARGET]
    assert query['expected_tenant_id']==[A] and query['expected_user_id']==['11']
    assert not download.called and not ai.called and not learning.called
    assert worker.pending()==[]
    model=Mock();monkeypatch.setattr('tenant_operator_copilot.run',model)
    with pytest.raises(TenantLinearError):flow.draft(PROFILE,reply['id'],{'request_id':str(uuid.uuid4())})
    assert not model.called
    assert local.execute('SELECT count(*) AS n FROM saas_tenant_notifications')[0]['n']==1
    assert local.execute('SELECT selected_message_ids,selected_media_ids FROM saas_public_case_shares')[0]=={'selected_message_ids':data['message_ids'],'selected_media_ids':[]}
    # Exercise the real protected authorization/loader and the actual HTTP path;
    # only the byte transport is mocked, and only after the explicit click.
    import asyncio
    import logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from unittest.mock import AsyncMock
    import tenant_chat_attachment_store as attachments
    import tenant_chat_attachment_api as attachment_api
    monkeypatch.setattr(attachments,'_connect',lambda:local)
    attachment_api.REQUESTS.clear()
    item=attachments.load(PROFILE,99,'connect:'+message_id,channel_id=CA,conversation_id=TARGET)
    assert item['row']['message_type']==('voice' if kind=='voice' else canonical)
    mime={'audio':'audio/ogg','video':'video/mp4','image':'image/png','document':'application/pdf'}[canonical]
    byte_transport=AsyncMock(return_value=(b'synthetic original file',mime))
    monkeypatch.setattr('tenant_connect_media.download_media',byte_transport)
    async def open_file():
        app=web.Application()
        app.router.add_get('/api/platform/crm/chat/attachment',attachment_api.handler(lambda _:PROFILE,Mock(),logging.getLogger(__name__)))
        async with TestClient(TestServer(app)) as client:
            response=await client.get(reply['attachment_url'])
            assert response.status==200,await response.text()
            assert await response.read()==b'synthetic original file'
    asyncio.run(open_file())
    assert byte_transport.call_count==1
    assert local.execute('SELECT message_type FROM saas_crm_messages WHERE id=%s::uuid',(original['id'],))[0]['message_type']==kind



def test_unquoted_media_is_not_guessed_and_conflicting_quote_cannot_override_marker(case_ready):
    local,data,_=case_ready
    prepared=flow.prepare(PROFILE,data,'https://crm.test')
    flow.dispatch(PROFILE,prepared['id'],data['request_id'])
    with local.cursor() as cur:
        assert flow.record_reply(cur,A,CA,TARGET,'unquoted','A picture',message_type='image') is None
        assert flow.record_reply(cur,A,CA,TARGET,'conflicting','CASE:'+prepared['id'],'WRONG_PROVIDER',message_type='video') is None
        assert flow.record_reply(cur,A,CB,TARGET,'foreign','CASE:'+prepared['id'],message_type='document') is None
        assert flow.record_reply(cur,A,CA,TARGET,'caption-marker','CASE:'+prepared['id']+' This is the error screen',message_type='image')
    local.commit()
    reply=flow.inbox(PROFILE,case_id=data['case_id'])['replies'][0]
    assert reply['message_type']=='image'
    assert 'attachment_url' not in reply,'no original CRM message means no invented file link'


def test_queued_video_reply_reconciles_after_receipt_without_loading_file(case_ready,monkeypatch):
    local,data,sender=case_ready
    sender.return_value={'state':'pending','message_id':'media-queue','external_message_id':None}
    prepared=flow.prepare(PROFILE,data,'https://crm.test')
    assert flow.dispatch(PROFILE,prepared['id'],data['request_id'])['state']=='pending'
    original=str(uuid.uuid4())
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw,author_name) VALUES(%s::uuid,%s::uuid,99,'connect:late-video','incoming',%s,'','video',%s::jsonb,'Developer')",(original,A,'connect:'+CA,'{"id":"late-video","quotedExternalMessageId":"LATE_MEDIA_PROVIDER"}'));local.commit()
    loader=Mock();monkeypatch.setattr(transport,'download_connect_message_media',loader)
    assert worker.reconcile()==0
    with local.cursor() as cur:
        assert flow.observe_outbound_receipt(cur,A,CA,TARGET,{'_crm_signed_outbound_receipt':True,'fromMe':True,'id':'media-queue','externalMessageId':'LATE_MEDIA_PROVIDER','originClientMessageId':data['request_id']})
    local.commit()
    assert worker.reconcile()==1 and worker.reconcile()==0
    reply=flow.inbox(PROFILE,case_id=data['case_id'])['replies'][0]
    assert reply['message_type']=='video' and reply['source_route']['source_message_id']==original
    assert not loader.called and worker.pending()==[]


def test_source_attachment_link_requires_developer_destination_access(case_ready,monkeypatch):
    local,data,_=case_ready
    prepared=flow.prepare(PROFILE,data,'https://crm.test');flow.dispatch(PROFILE,prepared['id'],data['request_id'])
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type) VALUES(%s::uuid,%s::uuid,99,'connect:restricted-file','incoming',%s,'','audio')",(uuid.uuid4(),A,'connect:'+CA))
    with local.cursor() as cur:
        assert flow.record_reply(cur,A,CA,TARGET,'restricted-file','','PROVIDER_123',message_type='audio')
    local.commit()
    original_route=flow.route
    def limited(cur,tenant,policy,channel,conversation,**kwargs):
        if conversation==TARGET:raise TenantLinearError('Destination unavailable',403)
        return original_route(cur,tenant,policy,channel,conversation,**kwargs)
    monkeypatch.setattr(flow,'route',limited)
    reply=flow.inbox(PROFILE,case_id=data['case_id'])['replies'][0]
    assert reply['message_type']=='audio' and reply['body']
    assert not any(key in reply for key in ('attachment_url','source_url','source_route'))


def test_confirmed_receipt_survives_malformed_legacy_raw_and_uses_one_identity(case_ready):
    local,data,sender=case_ready
    prepared=flow.prepare(PROFILE,data,'https://crm.test')
    mid=str(uuid.uuid4())
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,%s,'incoming',%s,%s,'image','[\"legacy\"]'::jsonb)",(uuid.uuid4(),A,'connect:'+mid,'connect:'+CA,'CONSULT:'+prepared['id']));local.commit()
    assert flow.dispatch(PROFILE,prepared['id'],data['request_id'])['state']=='sent'
    assert sender.call_count==1
    reply=flow.inbox(PROFILE,case_id=data['case_id'])['replies'][0]
    assert reply['message_id']==mid and reply['source_external_id']=='connect:'+mid
    assert worker.reconcile()==0
    with local.cursor() as cur:
        assert flow.record_reply(cur,A,CA,TARGET,mid,'CONSULT:'+prepared['id'],message_type='image') is None
    local.commit()
    assert local.execute('SELECT count(*) AS n FROM saas_dialog_consultation_replies')[0]['n']==1


def test_optional_reply_failure_cannot_roll_back_positive_provider_receipt(case_ready,monkeypatch):
    local,data,sender=case_ready
    prepared=flow.prepare(PROFILE,data,'https://crm.test');mid=str(uuid.uuid4())
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,%s,'incoming',%s,'Replace cable','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+mid,'connect:'+CA,json.dumps({'id':mid,'quotedExternalMessageId':'PROVIDER_123'})));local.commit()
    original=flow.record_reply;monkeypatch.setattr(flow,'record_reply',Mock(side_effect=RuntimeError('synthetic transient correlation failure')))
    assert flow.dispatch(PROFILE,prepared['id'],data['request_id'])['state']=='sent'
    assert local.execute('SELECT state,provider_message_id FROM saas_consultation_group_deliveries')[0]=={'state':'sent','provider_message_id':'PROVIDER_123'}
    assert local.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE external_id='connect:connect-id'")[0]['n']==1
    monkeypatch.setattr(flow,'record_reply',original)
    assert worker.reconcile()==1 and worker.reconcile()==0
    assert sender.call_count==1


def test_existing_prefixed_legacy_reply_alias_does_not_duplicate_on_upgrade(case_ready):
    local,data,_=case_ready
    prepared=flow.prepare(PROFILE,data,'https://crm.test');flow.dispatch(PROFILE,prepared['id'],data['request_id'])
    mid=str(uuid.uuid4())
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,%s,'incoming',%s,%s,'text','{}'::jsonb)",(uuid.uuid4(),A,'connect:'+mid,'connect:'+CA,'CONSULT:'+prepared['id']+' Advice'));local.commit()
    assert worker.reconcile()==1
    local.execute("UPDATE saas_dialog_consultation_replies SET message_id=%s,source_external_id=''",('connect:'+mid,));local.commit()
    assert worker.reconcile()==0
    with local.cursor() as cur:
        assert flow.record_reply(cur,A,CA,TARGET,mid,'Advice','PROVIDER_123') is None
    local.commit()
    assert local.execute('SELECT message_id FROM saas_dialog_consultation_replies')[0]['message_id']=='connect:'+mid
    assert local.execute('SELECT count(*) AS n FROM saas_tenant_notifications')[0]['n']==1


def test_reconciliation_missing_raw_ids_advances_past_first_bounded_page(case_ready):
    local,data,_=case_ready
    prepared=flow.prepare(PROFILE,data,'https://crm.test');flow.dispatch(PROFILE,prepared['id'],data['request_id'])
    for _ in range(102):
        mid=str(uuid.uuid4())
        local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,%s,'incoming',%s,'Advice','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+mid,'connect:'+CA,'{"quotedExternalMessageId":"PROVIDER_123"}'))
    local.commit()
    assert worker.reconcile()==100
    assert worker.reconcile()==2
    assert worker.reconcile()==0
    assert local.execute('SELECT count(*) AS n FROM saas_dialog_consultation_replies')[0]['n']==102
    assert worker.pending()==[]


def test_authoritative_source_external_id_never_falls_back_to_later_different_file(case_ready):
    local,data,_=case_ready
    prepared=flow.prepare(PROFILE,data,'https://crm.test');flow.dispatch(PROFILE,prepared['id'],data['request_id'])
    actual,wrong=str(uuid.uuid4()),str(uuid.uuid4());source_id=str(uuid.uuid4())
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type) VALUES(%s::uuid,%s::uuid,99,%s,'incoming',%s,'','audio')",(source_id,A,'connect:'+actual,'connect:'+CA))
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type) VALUES(%s::uuid,%s::uuid,99,%s,'incoming',%s,'','audio')",(uuid.uuid4(),A,'connect:'+wrong,'connect:'+CA))
    with local.cursor() as cur:
        assert flow.record_reply(cur,A,CA,TARGET,wrong,'Other source','PROVIDER_123',message_type='audio',source_external_id='connect:'+wrong)
        assert flow.record_reply(cur,A,CA,TARGET,wrong,'','PROVIDER_123',message_type='audio',source_external_id='connect:'+actual)
    local.commit()
    reply=next(row for row in flow.inbox(PROFILE,case_id=data['case_id'])['replies'] if row['source_external_id']=='connect:'+actual)
    assert reply['message_id']==actual
    assert reply['source_route']['source_message_id']==source_id
    assert parse_qs(urlsplit(reply['attachment_url']).query)['message_id']==['connect:'+actual]
