
"""Isolated DB and mocked provider, with synthetic WhatsApp identities."""
import json
import uuid
from unittest.mock import Mock

import pytest

from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError
import tenant_chat_send_store as send_store
import tenant_whatsapp_native_consumer as consumer
import tenant_whatsapp_native_reactions as reactions
import tenant_whatsapp_native_transport as native
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_call_sources import managed
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db
from test_whatsapp_native_inbox import capture
from test_whatsapp_native_transport import configured, accepted

GROUP = '15550000003@g.us'
PARTICIPANT = '15550000004@lid'


@pytest.fixture(params=[False, True], ids=['direct', 'group'])
def target(configured, monkeypatch, request):
    db, route = configured
    chat = GROUP if request.param else route['conversation_id']
    local_id = str(uuid.uuid4())
    route = {**route, 'conversation_id': chat}
    db.execute("""INSERT INTO saas_connect_conversations
      (tenant_id,connect_channel_id,conversation_id,lead_id)
      VALUES(%s,%s,%s,551) ON CONFLICT (tenant_id,connect_channel_id,conversation_id)
      DO UPDATE SET lead_id=551""", (A, CA, chat))
    row = {'id':local_id,'source_conversation_id':chat,'source_channel_id':CA,'direction':'incoming',
           'channel':'connect:'+CA,'message_type':'text','raw':{'externalMessageId':'TARGET_123',
           'conversationId':chat,'fromMe':False,'channelId':CA,'participantJid':PARTICIPANT}}
    db.execute("""INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,direction,channel,message_type,raw)
      VALUES(%s,%s,551,%s,'incoming',%s,'text',%s::jsonb)""",
      (local_id,A,'connect:'+local_id,'connect:'+CA,json.dumps(row['raw'])))
    with db.cursor() as cur:
        reactions.migrate(cur)
        consumer.migrate(cur)
    db.execute('UPDATE saas_whatsapp_capture_generations SET projection_enabled=TRUE')
    db.commit()
    monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_INBOX','1')
    monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_PROJECTION','1')
    monkeypatch.setattr(reactions,'_connect',lambda: db)

    def authority(cur, session, lead):
        assert lead == 551
        if session['tenant_id'] != A:
            raise TenantLinearError('denied',403)
        return session,['connect',CA,chat],{'route':route}
    monkeypatch.setattr(send_store,'authority',authority)
    yield db, row, route
    db.rollback()
    db.execute("DELETE FROM saas_whatsapp_reaction_operations WHERE tenant_id=%s", (A,))
    db.execute("DELETE FROM saas_whatsapp_reactions WHERE tenant_id=%s", (A,))
    db.execute("DELETE FROM saas_crm_messages WHERE id=%s", (local_id,))
    db.execute("DELETE FROM saas_connect_conversations WHERE tenant_id=%s AND lead_id=551", (A,))
    db.commit()


def test_react_replace_remove_and_replay(target, monkeypatch):
    db,row,route = target
    call = accepted(monkeypatch)
    for expected,emoji in enumerate(['👍','😂',''], start=1):
        operation = str(uuid.uuid4())
        session = {**PROFILE, 'telegram_id':11 if expected != 2 else 12}
        assert reactions.send(session,551,row['id'],operation,emoji) == {'state':'accepted','emoji':emoji}
        assert reactions.send(session,551,row['id'],operation,emoji) == {'state':'accepted','emoji':emoji}
        payload = call.call_args.kwargs['json']
        assert payload['chat'] == row['source_conversation_id']
        assert payload['text'] == emoji and payload['kind'] == 'reaction'
        assert payload['operation'] == f"reaction:{session['telegram_id']}:{operation}"
        assert payload['reaction'] == {'id':'TARGET_123','from_me':False,
                                      **({'participant':PARTICIPANT} if payload['chat']==GROUP else {})}
        assert call.call_count == expected
    assert db.execute('SELECT actor_id FROM saas_whatsapp_reaction_operations ORDER BY actor_id') == [
        {'actor_id':11},{'actor_id':11},{'actor_id':12}]


def test_unknown_only_checks_existing_operation(target,monkeypatch):
    _,row,_ = target
    submit = Mock(side_effect=TenantPlatformError('timeout'))
    lookup = Mock(return_value={'providerAccepted':False,'externalMessageId':'REACTION_1'})
    monkeypatch.setattr(native,'submit',submit)
    monkeypatch.setattr(native,'lookup',lookup)
    operation=str(uuid.uuid4())
    assert reactions.send(PROFILE,551,row['id'],operation,'👍')['state']=='unknown'
    assert reactions.send(PROFILE,551,row['id'],operation,'👍')['state']=='unknown'
    with pytest.raises(TenantLinearError,match='reaction_request_changed'):
        reactions.send(PROFILE,551,row['id'],operation,'😂')
    assert submit.call_count==1 and lookup.call_count==1
    assert lookup.call_args.kwargs=={'reaction':True}


def test_message_from_other_scope_cannot_send(target,monkeypatch):
    db,row,_ = target
    call = accepted(monkeypatch)
    operation=str(uuid.uuid4())
    with pytest.raises(TenantLinearError):
        reactions.send({'tenant_id':B,'telegram_id':11},551,row['id'],operation,'👍')
    db.execute("UPDATE saas_crm_messages SET raw=raw||jsonb_build_object('channelId',%s::text) WHERE id=%s",(CB,row['id']))
    db.commit()
    with pytest.raises(TenantLinearError,match='reaction_message_unavailable'):
        reactions.send(PROFILE,551,row['id'],operation,'👍')
    call.assert_not_called()


def test_control_projection_updates_only_reaction_and_ignores_older_delivery(target,monkeypatch):
    db,row,route = target
    scope=route['native']['scope']
    key=reactions._target(row)['key']
    event={'kind':'message','variant':'control','from_me':True,'chat':route['conversation_id'],
           'reaction':key,'text':'👍','provider_id':'REACTION_1','timestamp':1795521600000}
    monkeypatch.setattr(consumer,'_connect',lambda: db)
    monkeypatch.setattr(consumer,'load_evidence',lambda *a,**kw:(scope,event))
    ingest=Mock(side_effect=AssertionError('reaction reached AI/ordinary ingestion'))
    monkeypatch.setattr('tenant_connect_transport._ingest_once',ingest)
    reference=(A,CA,scope['generation'],'a'*64)
    for emoji, offset, ident in [('👍',0,1),('❤️',10,2),('',20,3),('👍',0,1)]:
        event.update(text=emoji,timestamp=1795521600000+offset,provider_id=f'REACTION_{ident}')
        assert consumer.consume(reference)
    with db.cursor() as cur:
        reactions.attach(cur,A,[row])
    assert row['wa_reaction_available'] and row['wa_reaction']==''
    assert row['wa_reaction_event'].endswith('REACTION_3')
    ingest.assert_not_called()
