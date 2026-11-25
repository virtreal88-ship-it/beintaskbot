from datetime import datetime, timedelta, timezone

import pytest
pytest_plugins = ('test_tenant_support_inbox',)

import tenant_operator_dialogs
import tenant_whatsapp_message_edit as actions
from test_dialog_lifecycle_pglite import episodes
from test_tenant_support_inbox import A, B, CA, CB

TENANT = A
CHANNEL = CA
CHAT = '12025550101@s.whatsapp.net'


def own_message(*, age=timedelta(minutes=1), message_type='text', **raw_changes):
    sent = datetime.now(timezone.utc) - age
    raw = {
        'externalMessageId': 'SYNTHETIC_PROVIDER_ID', 'channelId': CHANNEL,
        'conversationId': CHAT, '_crm_provider_conversation_id': CHAT,
        'fromMe': True, '_crm_native_send_receipt': True,
        'verified_employee_id': 11,
    }
    raw.update(raw_changes)
    return {
        'id': '00000000-0000-4000-8000-000000000033', 'tenant_id': TENANT,
        'kommo_lead_id': 81, 'external_id': 'connect:SYNTHETIC_PROVIDER_ID',
        'channel': 'connect:'+CHANNEL, 'direction': 'outgoing', 'message_type': message_type,
        'created_at': sent, 'happened_at': sent, 'body': 'Synthetic text',
        'author_name': 'Operator', 'raw': raw,
        'source_lead_id': 81, 'source_channel_id': CHANNEL, 'source_conversation_id': CHAT,
    }


def test_only_the_verified_operator_message_on_the_exact_channel_is_actionable():
    row = own_message()
    assert actions._original(row, 11, CHANNEL, CHAT, action='edit') == (CHAT, 'SYNTHETIC_PROVIDER_ID')
    assert actions._original(row, 12, CHANNEL, CHAT, action='edit') is None
    assert actions._original(row, 11, '00000000-0000-4000-8000-000000000099', CHAT, action='edit') is None
    assert actions._original({**row, 'raw': {**row['raw'], 'ai_authored': True}}, 11, CHANNEL, CHAT) is None
    assert actions._original({**row, 'raw': {**row['raw'], '_crm_native_send_receipt': False}}, 11, CHANNEL, CHAT) is None
    assert actions._original({**row, 'raw': {**row['raw'], 'message_deleted': True}}, 11, CHANNEL, CHAT, action='delete') is None
    assert actions._original({**row, 'message_type': 'image'}, 11, CHANNEL, CHAT, action='edit') is None
    assert actions._original({**row, 'message_type': 'image'}, 11, CHANNEL, CHAT, action='delete') is not None


def test_edit_15_minutes_and_delete_48_hours_are_separate_capabilities(monkeypatch):
    for flag in ('SEND', 'INBOX', 'PROJECTION'):
        monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_'+flag, '1')
    dialog = {'wecall': True, 'can_send': True, 'channel_id': CHANNEL,
              'conversation_id': CHAT, 'lead_id': 81}
    row = own_message(age=timedelta(minutes=16))
    actions.attach_capabilities([row], dialog, 11)
    assert 'wa_edit_version' not in row
    assert row['wa_delete_version'] == actions._delete_version(row)
    assert datetime.fromisoformat(row['wa_delete_until']) > datetime.now(timezone.utc)

    expired = own_message(age=timedelta(hours=49))
    actions.attach_capabilities([expired], dialog, 11)
    assert 'wa_edit_version' not in expired and 'wa_delete_version' not in expired


def test_customer_delete_tombstone_does_not_leak_saved_transcript_or_media():
    result = tenant_operator_dialogs._message({
        'id': '00000000-0000-4000-8000-000000000044',
        'external_id': 'connect:SYNTHETIC_INCOMING', 'channel': 'connect:'+CHANNEL,
        'direction': 'incoming', 'message_type': 'audio', 'author_name': 'Customer',
        'body': '', 'media_url': '', 'created_at': datetime.now(timezone.utc),
        '_saved_audio_transcript': 'private customer speech',
        'raw': {'externalMessageId': 'SYNTHETIC_INCOMING', 'channelId': CHANNEL,
                'conversationId': CHAT, 'fromMe': False, 'message_deleted': True,
                'message_delete_source': 'whatsapp', 'message_deleted_at': datetime.now(timezone.utc)},
    })
    assert result['message_deleted'] is True
    assert result['body'] == '' and result['media_url'] == ''
    assert result['message_delete_source'] == 'whatsapp'
    assert 'audio_transcript' not in result and not result.get('has_transcription')
    assert not ({'media_version','media_thumbnail','media_filename','media_size_bytes'} & result.keys())

@pytest.mark.parametrize('wrong', ['other-participant@s.whatsapp.net', '15550000003@s.whatsapp.net'])
def test_client_revoke_redacts_only_exact_group_member_row(episodes, monkeypatch, wrong):
    import json
    import uuid
    import tenant_connect_transport
    import tenant_whatsapp_native_rollout

    db = episodes
    group = '120363405555555555@g.us'
    participant = '15550000002@s.whatsapp.net'
    provider_id = 'SYNTHETIC_GROUP_MESSAGE'
    scope = {'tenant': A, 'source': CA,
             'generation': 'revoke-test', 'account': '15550000009@s.whatsapp.net'}
    with db.cursor() as cur:
        tenant_connect_transport._schema(db)
        tenant_whatsapp_native_rollout.migrate(cur)
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,status) VALUES(%s::uuid,%s::uuid,'Synthetic source','connected') ON CONFLICT DO NOTHING", (scope['tenant'], CA))
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,contact_name,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,'Support group','group') ON CONFLICT DO NOTHING", (scope['tenant'], CA, group))
    lead = db.execute('SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s', (scope['tenant'], CA, group))[0]['lead_id']
    message_id = str(uuid.uuid4())
    raw = {'externalMessageId':provider_id,'channelId':CA,'conversationId':group,'fromMe':False,
           'participantJid':participant,'text':'private customer text','mediaKey':'synthetic-secret'}
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,media_url,happened_at,raw) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','private customer text','image','https://media.invalid/private',now(),%s::jsonb)",
               (message_id, scope['tenant'], lead, 'connect:'+provider_id, 'connect:'+CA, json.dumps(raw)))
    db.commit()
    monkeypatch.setattr(actions, '_sent_at', lambda row: None)

    payload = {'kind':'message','variant':'control','chat':group,'timestamp':int(datetime.now(timezone.utc).timestamp()*1000),
               'revoke':{'id':provider_id,'chat':group,'from_me':False,'participant':wrong}}
    with db.cursor() as cur:
        assert not actions.observe_revoke(cur, scope, payload)
    assert db.execute('SELECT body FROM saas_crm_messages WHERE id=%s::uuid',(message_id,))[0]['body']=='private customer text'
    db.commit()

    payload['revoke']['participant'] = participant
    with db.cursor() as cur:
        assert actions.observe_revoke(cur, scope, payload)
    with db.cursor() as cur:
        assert actions.observe_revoke(cur, scope, payload), 'a repeated provider event stays idempotent'
    row = db.execute('SELECT body,media_url,raw FROM saas_crm_messages WHERE id=%s::uuid',(message_id,))[0]
    assert row['body']=='' and row['media_url']==''
    assert row['raw']['message_deleted'] is True and row['raw']['message_delete_source']=='whatsapp'
    assert 'text' not in row['raw'] and 'mediaKey' not in row['raw']
    assert db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s",(scope['tenant'],lead))[0]['n']==1
    db.commit()

def test_new_number_route_reopens_only_on_selected_source(episodes, monkeypatch):
    import uuid
    import tenant_dialog_compose_api as compose
    import tenant_connect_transport
    from test_tenant_support_inbox import PROFILE

    db = episodes
    monkeypatch.setattr(compose, '_connect', lambda: db)
    # No service credentials or provider traffic in this integration test.
    monkeypatch.setattr(tenant_connect_transport, 'connect_route', lambda cur, tenant, lead: {'lead_id': lead})
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected') ON CONFLICT DO NOTHING", (A,))
    db.commit()
    request = {'channel_id': CA, 'request_id': str(uuid.uuid4()), 'phone': '12025550188', 'text': 'synthetic hello'}
    original = compose._prepare(PROFILE, request)
    assert not original['existing']
    repeated = compose._prepare(PROFILE, request)
    assert repeated['existing'] and not repeated['recover']
    assert repeated['route']['lead_id'] == original['route']['lead_id']
    assert any(source['channel_id'] == CA for source in compose._channel_options(PROFILE)['sources'])


@pytest.mark.parametrize('chat', [CHAT, '120363405555555555@g.us'])
def test_customer_text_edit_updates_only_the_original_in_its_source(episodes, monkeypatch, chat):
    import json
    import uuid
    import tenant_connect_transport
    import tenant_whatsapp_native_rollout

    db = episodes
    with db.cursor() as cur:
        tenant_connect_transport._schema(db)
        tenant_whatsapp_native_rollout.migrate(cur)
    is_group = chat.endswith('@g.us')
    alternate = '123456789012@lid'
    provider_id = 'SYNTHETIC_CUSTOMER_TEXT'
    scope = {'tenant': A, 'source': CA}
    ids = []
    for tenant, channel in ((A, CA), (A, str(uuid.uuid4())), (B, CB)):
        db.execute("""INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name)
          VALUES(%s::uuid,%s::uuid,'WhatsApp') ON CONFLICT DO NOTHING""", (tenant, channel))
        lead = db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,
          conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s) RETURNING lead_id""",
            (tenant, channel, chat, 'group' if is_group else 'direct'))[0]['lead_id']
        raw = {'externalMessageId': provider_id, 'channelId': channel, 'conversationId': chat,
               'participantJid': CHAT, 'text': 'original', 'fromMe': False}
        ids.append(str(uuid.uuid4()))
        db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,
          body,message_type,happened_at,raw) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','original','text',now(),%s::jsonb)""",
            (ids[-1], tenant, lead, 'connect:'+ids[-1], 'connect:'+channel, json.dumps(raw)))
    db.commit()
    # PGlite's Python bridge serializes postgres timestamptz as ISO strings.
    monkeypatch.setattr(actions, '_sent_at', lambda row: datetime.fromisoformat(row['happened_at']))
    stamp = int(datetime.now(timezone.utc).timestamp()*1000)
    payload = {'kind': 'message', 'variant': 'control', 'from_me': False, 'sender': alternate,
               'sender_alt': CHAT, 'chat': chat, 'provider_id': 'SYNTHETIC_CONTROL',
               'text': 'corrected', 'timestamp': stamp, 'edit': {'id': provider_id}}
    with db.cursor() as cur:
        assert not actions.observe(cur, scope, {**payload, 'sender': '12025550102@s.whatsapp.net', 'sender_alt': ''})
        assert not actions.observe(cur, scope, {**payload, 'from_me': True})
        assert not actions.observe(cur, scope, {**payload, 'app_dispatch': True})
        assert not actions.observe(cur, scope, {**payload, 'edit': {'id': provider_id, 'operation': 'invalid'}})
        assert actions.observe(cur, scope, payload)
        assert actions.observe(cur, scope, payload)
        assert actions.observe(cur, scope, {**payload, 'text': 'old', 'timestamp': stamp-1})
    rows = db.execute("SELECT id,body,raw,direction FROM saas_crm_messages WHERE raw->>'externalMessageId'=%s", (provider_id,))
    assert len(rows) == 3
    for row in rows:
        original = str(row['id']) == ids[0]
        assert row['body'] == row['raw']['text'] == ('corrected' if original else 'original')
        assert row['direction'] == 'incoming'
        assert ('_crm_text_edit' in row['raw']) == original
    db.execute("UPDATE saas_crm_messages SET body='',raw=raw||'{\"message_deleted\":true}'::jsonb WHERE id=%s::uuid", (ids[0],))
    with db.cursor() as cur:
        assert not actions.observe(cur, scope, {**payload, 'timestamp': stamp+1})
    db.commit()
