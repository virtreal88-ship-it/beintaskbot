"""Integration checks for ownership on ordinary/legacy/group readers, no network."""
import uuid
import pytest
from unittest.mock import patch
from test_tenant_support_inbox import database,db,A,CA,PROFILE
from test_dialog_lifecycle_pglite import episodes,command,payload
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy
import tenant_dialog_lifecycle as lifecycle
import tenant_operator_dialogs as dialogs
import tenant_operator_access as access
import tenant_dialog_access as scopes
import tenant_chat_send_store as sender

OTHER='cccccccc-cccc-4ccc-8ccc-cccccccccccc'
GROUP='1200000000001@g.us'


@pytest.fixture
def readers(episodes,monkeypatch):
    monkeypatch.setattr(dialogs,'_connect',lambda:episodes)
    monkeypatch.setattr(sender,'_connect',lambda:episodes)
    return episodes


def test_claim_hides_list_detail_legacy_read_from_owner_but_not_operator(readers):
    command(actor=33)
    assert dialogs.listing(PROFILE)['dialogs']==[]
    with pytest.raises(TenantLinearError) as denied:
        dialogs.history(PROFILE,'connect',41,CA,'client')
    assert denied.value.status==404
    actor={'tenant_id':A,'telegram_id':33}
    result=dialogs.history(actor,'connect',41,CA,'client')
    assert result['dialog']['lifecycle']['state']=='accepted'
    assert result['messages'][0]['body']=='Internet is down'
    with readers.cursor() as cur:
        tenant,_,policy=access.access(cur,PROFILE)
        with pytest.raises(TenantLinearError):scopes.require_lead(cur,tenant,policy,41)
    readers.commit()


def test_close_removes_queue_and_keeps_readonly_case_detail(readers):
    command()
    command('close',1)
    assert dialogs.listing(PROFILE)['dialogs']==[]
    result=dialogs.history(PROFILE,'connect',41,CA,'client')
    assert result['dialog']['lifecycle']['can_submit_case'] is True
    assert result['dialog']['can_send'] is False
    with readers.cursor() as cur:
        _,_,policy=access.access(cur,PROFILE)
        with pytest.raises(TenantLinearError) as denied:
            scopes.require_lead(cur,A,policy,41,'update')
        assert denied.value.status==409
    readers.commit()


def test_group_history_does_not_union_other_operators_channel(readers):
    readers.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second WhatsApp')",(A,OTHER))
    for channel,lead,text in ((CA,43,'hidden other operator'),(OTHER,44,'visible source')):
        readers.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind,display_name) VALUES(%s::uuid,%s::uuid,%s,%s,'group','Group')",(A,channel,GROUP,lead))
        readers.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s)",
                        (str(uuid.uuid4()),A,lead,'connect:'+str(uuid.uuid4()),'connect:'+channel,text))
    readers.commit()
    lifecycle.mutate({'tenant_id':A,'telegram_id':33},{**payload(), 'conversation_id':GROUP,'lead_id':43})
    with readers.cursor() as cur:
        _,actor,policy=access.access(cur,PROFILE)
        assert scopes.allowed_channels(cur,A,policy,conversation=GROUP)==[OTHER]
    readers.commit()
    result=dialogs.history(PROFILE,'connect',44,OTHER,GROUP)
    assert [m['body'] for m in result['messages']]==['visible source']
    assert all(s['channel_id']==OTHER for m in result['messages'] for s in m.get('sources',[]))


def test_retained_alias_and_pending_notice_do_not_bypass_claim(readers):
    from tenant_connect_dialog_equivalence import schema
    with readers.cursor() as cur:schema(cur)
    readers.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'alias@lid',45)",(A,CA))
    readers.execute("INSERT INTO saas_connect_dialog_links(tenant_id,channel_id,source_id,canonical_id,source_lead,canonical_lead,pair_id,snapshot) VALUES(%s::uuid,%s::uuid,'alias@lid','client',45,41,'verified-pair','{}'::jsonb)",(A,CA))
    readers.commit()
    command(actor=33)
    with readers.cursor() as cur:
        _,_,policy=access.access(cur,PROFILE)
        with pytest.raises(TenantLinearError):scopes.require_lead(cur,A,policy,45)
        from tenant_crm_notice_store import _dialog_notice_visible
        row={'channel':'connect:'+CA,'kommo_lead_id':45,'direction':'incoming',
             'raw':{'channelId':CA,'conversationId':'alias@lid'}}
        assert not _dialog_notice_visible(cur,PROFILE,'incoming_message',row)
        assert _dialog_notice_visible(cur,{'tenant_id':A,'telegram_id':33},'incoming_message',row)
    readers.commit()


def test_receipt_persistence_after_close_requires_exact_dispatch_proof(readers,monkeypatch):
    import json
    from tenant_chat_send_schema import ensure_schema
    ensure_schema(readers)
    request_id=str(uuid.uuid4());external=str(uuid.uuid4())
    result=command()
    readers.execute("INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state,route) VALUES(%s::uuid,11,%s::uuid,41,'hash','sending',%s::jsonb)",
                    (A,request_id,json.dumps({'lifecycle_episode_id':result['lifecycle']['id']})))
    readers.commit()
    route={'channel_id':CA,'conversation_id':'client','api_key':'test-only'}
    monkeypatch.setattr(sender,'connect_route',lambda *args,**kwargs:route)
    item={'key':(A,11,request_id),'command':{'lead_id':41,'request_id':request_id},'connection':['connect',CA,'client']}
    assert sender.dispatch_guarded(item,route,lambda *args:external)==external
    command('close',1)
    with readers.cursor() as cur:
        sender.authority(cur,PROFILE,41,lifecycle_receipt=(request_id,external))
        for rid,eid,actor in ((str(uuid.uuid4()),external,11),(request_id,str(uuid.uuid4()),11),(request_id,external,33)):
            with pytest.raises(TenantLinearError):
                sender.authority(cur,{'tenant_id':A,'telegram_id':actor},41,lifecycle_receipt=(rid,eid))
    readers.commit()


def test_unknown_status_reconciliation_keeps_original_episode_and_no_send_grant(readers,monkeypatch):
    import json
    from tenant_chat_send_schema import ensure_schema
    ensure_schema(readers)
    request_id=str(uuid.uuid4());external=str(uuid.uuid4())
    original=command()['lifecycle']['id']
    readers.execute("INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state,route) VALUES(%s::uuid,11,%s::uuid,41,'hash','unknown',%s::jsonb)",
                    (A,request_id,json.dumps({'lifecycle_episode_id':original})))
    readers.commit()
    command('close',1)
    with readers.cursor() as cur:lifecycle.note_inbound(cur,A,CA,'client',str(uuid.uuid4()),'2099-01-01T00:00:00Z')
    readers.commit();command(revision=3,actor=33)
    route={'channel_id':CA,'conversation_id':'client','api_key':'test-only'}
    monkeypatch.setattr(sender,'connect_route',lambda *args,**kwargs:route)
    with readers.cursor() as cur:
        sender.authority(cur,PROFILE,41,lifecycle_status=request_id)
        with pytest.raises(TenantLinearError):sender.authority(cur,PROFILE,41)
        for actor,rid in ((33,request_id),(11,str(uuid.uuid4()))):
            with pytest.raises(TenantLinearError):sender.authority(cur,{'tenant_id':A,'telegram_id':actor},41,lifecycle_status=rid)
        receipt={'provider_accepted':True,'message_id':external}
        lifecycle.record_reconciled_dispatch(cur,A,11,41,request_id,CA,'client',receipt)
        with pytest.raises(TenantLinearError):lifecycle.record_reconciled_dispatch(cur,A,11,41,request_id,CA,'other',receipt)
        with pytest.raises(TenantLinearError):lifecycle.record_reconciled_dispatch(cur,A,11,41,request_id,CA,'client',{**receipt,'message_id':str(uuid.uuid4())})
    message=str(uuid.uuid4())
    readers.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,41,%s,%s,'outgoing','receipt after close')",(message,A,'connect:'+external,'connect:'+CA))
    with readers.cursor() as cur:lifecycle.record_message(cur,A,CA,'client',message)
    assert str(readers.execute('SELECT episode_id FROM saas_dialog_episode_messages WHERE message_id=%s::uuid',(message,))[0]['episode_id'])==original
    readers.commit()
