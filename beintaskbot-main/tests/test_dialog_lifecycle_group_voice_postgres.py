"""Real CRM HTTP and PostgreSQL with a synthetic WeCall peer and audio."""
import base64
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import uuid

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
import psycopg
from psycopg.rows import dict_row
import pytest
import requests

import tenant_customer_voice_api as api
import tenant_customer_voice_service as voice
import tenant_whatsapp_native_consumer as consumer
import tenant_whatsapp_native_transport as native
from tenant_connect_group_history import page as group_history
from test_dialog_lifecycle_group_work_postgres import (
    A, CB, GROUP, LIDS, SOURCES, GatewayResponse, action, collaboration, database,
    db, episodes, history, join, profile,
)

ORIGIN = 'http://localhost'
LOGGER = logging.getLogger(__name__)
RECORDING = (Path(__file__).parent / 'fixtures' / 'connect-voice-tone.ogg').read_bytes()


@pytest.fixture
def voice_group(collaboration, monkeypatch, request):
    connection, membership, _ = collaboration
    if getattr(request, 'param', 'shared') == 'ordinary':
        # Exercise an already-accepted ordinary group, not a collaboration participant.
        with monkeypatch.context() as before_rollout:
            before_rollout.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_SEND', '0')
            action(33, 'accept')
        assert connection.execute('SELECT count(*) AS n FROM saas_dialog_group_participants')[0]['n'] == 0
        connection.commit()
    else:
        join(connection)
    for module in (voice, consumer):
        monkeypatch.setattr(module, '_connect', lambda: psycopg.connect(connection.dsn, row_factory=dict_row))
    monkeypatch.setenv('CUSTOMER_VOICE_ENABLED', 'true')
    monkeypatch.setenv('AI_AGENTS_ENABLED', 'false')
    gateway = {'checks': [], 'attempts': [], 'delivered': {}, 'failure': None}
    bindings = {(row['channel_id'], row['account_jid']): row['gateway_id'] for row in connection.execute(
        'SELECT channel_id::text,gateway_id::text,account_jid FROM saas_browser_call_sources WHERE tenant_id=%s', (A,))}
    connection.commit()

    def post(url, *, json, **_):
        scope = json['scope']
        assert scope['tenant'] == A and json['chat'] == GROUP
        assert url.startswith('https://isolated-test.invalid/v1/sources/' + bindings[scope['source'], scope['account']] + '/')
        if url.endswith('/group-membership'):
            gateway['checks'].append(scope['account'])
            if gateway['failure'] == 'proof':
                raise requests.ReadTimeout()
            if gateway['failure'] == 'disabled':
                return GatewayResponse(409, {'error': 'native_group_session_unavailable'})
            return GatewayResponse(200, {'member': True, 'scope': scope, 'chat': GROUP,
                'account_lid': LIDS[scope['account']]})
        key = (scope['account'], json['operation'])
        if url.endswith('/messages/status'):
            return GatewayResponse(200, {'confirmed': True,
                'result': {'state': 'accepted', 'provider_id': gateway['delivered'][key]}})
        assert url.endswith('/messages') and json['verify_group_membership'] is True
        assert json['kind'] == 'voice' and json['mime'] == 'audio/ogg'
        assert base64.b64decode(json['data']).startswith(b'OggS')
        gateway['attempts'].append(key)
        if not membership[scope['account']]:
            return GatewayResponse(422, {'confirmed': False, 'error': 'native_group_membership_unconfirmed',
                'delivery_phase': 'not_sent'})
        provider = 'SYNTHETIC_VOICE_' + str(len(gateway['delivered']) + 1)
        gateway['delivered'][key] = provider
        if gateway['failure'] == 'timeout':
            raise requests.Timeout()
        return GatewayResponse(200, {'confirmed': True, 'result': {'state': 'accepted', 'provider_id': provider}})

    monkeypatch.setattr(native.requests, 'post', post)
    return connection, membership, gateway


def command(actor, **overrides):
    channel, lead, _ = SOURCES[actor]
    return {'request_id': str(uuid.uuid4()), 'lead_id': lead, 'expected_tenant_id': A,
            'expected_user_id': actor, 'expected_channel_id': channel, 'expected_conversation_id': GROUP, **overrides}


def app_client(actor):
    app = web.Application()
    session = lambda _: profile(actor)
    app.router.add_post('/send', api.handler(session, ORIGIN, LOGGER))
    app.router.add_get('/capability', api.capability_handler(session, LOGGER))
    app.router.add_get('/receipt', api.receipt_handler(session, LOGGER))
    return TestClient(TestServer(app))


async def upload(client, data):
    form = FormData()
    form.add_field('command', json.dumps(data))
    form.add_field('file', RECORDING, filename='voice.ogg', content_type='audio/ogg')
    response = await client.post('/send', data=form, headers={'Origin': ORIGIN})
    return response.status, await response.json()


def project_echo(monkeypatch, actor, provider, *, sender, from_me):
    channel, _, account = SOURCES[actor]
    scope = {'tenant': A, 'source': channel, 'generation': 'group-test', 'account': account}
    payload = {'kind': 'message', 'chat': GROUP, 'sender': sender, 'from_me': from_me,
        'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000), 'provider_id': provider,
        'message_type': 'audio', 'media_kind': 'audio', 'media_mime': 'audio/ogg',
        'media_size': len(RECORDING), 'text': '', 'app_dispatch': from_me}
    monkeypatch.setattr(consumer, 'load_evidence', lambda cur, reference, **_: (scope, payload))
    return consumer.consume((A, channel, 'group-test', uuid.uuid4().hex * 2))


@pytest.mark.asyncio
async def test_two_operator_voices_use_exact_sources_and_reconcile_echoes(voice_group, monkeypatch):
    database, _, gateway = voice_group
    replays = []
    for actor, other in ((33, 44), (44, 33)):
        data = command(actor)
        async with app_client(actor) as client:
            capability = await client.get('/capability', params={key: str(data[key]) for key in
                ('lead_id', 'expected_tenant_id', 'expected_user_id')})
            assert (await capability.json())['enabled'] is True
            status, result = await upload(client, data)
            assert status == 200 and result['provider_accepted'] is True
            _, _, account = SOURCES[actor]
            assert result['participant_jid'] == account
            provider = result['provider_message_id']
            project_echo(monkeypatch, actor, provider, sender=account, from_me=True)
            project_echo(monkeypatch, other, provider, sender=LIDS[account], from_me=False)
            replays.append((actor, data, result['message_id']))

    assert len(gateway['attempts']) == len(gateway['delivered']) == 2
    assert database.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE direction='outgoing' AND message_type='audio'")[0]['n'] == 2
    database.commit()
    for actor in SOURCES:
        messages = [message for message in history(actor)['messages'] if message['message_type'] == 'audio']
        assert len(messages) == 2
        assert {(message['author_name'], message['sending_number']) for message in messages} == {
            ('Operator A', '+994500000111'), ('Operator B', '+994500000112')}
        assert {source['channel_id'] for message in messages for source in message['sources']} == {SOURCES[actor][0]}
    channels = [route[0] for route in SOURCES.values()]
    with database.cursor() as cur:
        combined = [message for message in group_history(cur, A, GROUP, channel_ids=channels)
                    if message['message_type'] == 'audio']
    database.commit()
    assert len(combined) == 2 and all(message['direction'] == 'outgoing' for message in combined)
    assert all({source['channel_id'] for source in message['sources']} == set(channels) for message in combined)
    for actor, data, message_id in replays:
        async with app_client(actor) as client:
            replay_status, replay = await upload(client, data)
        assert replay_status == 200 and replay['message_id'] == message_id
    assert len(gateway['attempts']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['removed', 'proof', 'disabled', 'permission', 'source'])
async def test_group_voice_refusals_keep_draft_and_never_switch_source(voice_group, monkeypatch, failure):
    database, membership, gateway = voice_group
    data = command(44)
    if failure == 'removed':
        membership[SOURCES[44][2]] = False
    elif failure == 'permission':
        # The browser upload and CRM reservation have already completed. Revoke
        # before the final guarded dispatch while the voice is being converted.
        original = voice.native_opus

        def revoke_after_recording(content, mime):
            prepared = original(content, mime)
            database.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s AND employee_id=44', (A,))
            database.commit()
            return prepared

        monkeypatch.setattr(voice, 'native_opus', revoke_after_recording)
    elif failure == 'source':
        data['expected_channel_id'] = SOURCES[33][0]
    else:
        gateway['failure'] = failure
    async with app_client(44) as client:
        status, result = await upload(client, data)
    assert status in (403, 409) and result['keep_request'] is False
    assert not gateway['delivered']
    expected_code = {'removed': 'native_group_membership_unconfirmed',
                     'proof': 'native_group_check_unavailable', 'disabled': 'native_group_session_unavailable'}
    if failure in expected_code:
        assert result['error_code'] == expected_code[failure]
        assert result['delivery_phase'] == 'not_sent'
        assert gateway['checks'] == [SOURCES[44][2]]
    if failure == 'removed':
        assert len(gateway['attempts']) == 1 and gateway['attempts'][0][0] == SOURCES[44][2]
    else:
        assert not gateway['attempts']


@pytest.mark.asyncio
async def test_group_voice_lost_ack_is_only_recovered_by_lookup(voice_group):
    database, _, gateway = voice_group
    gateway['failure'] = 'timeout'
    data = command(44)
    async with app_client(44) as client:
        status, result = await upload(client, data)
        assert status == 503 and result['keep_request'] is True and result['delivery_phase'] == 'unknown'
        status, result = await upload(client, data)
        assert status == 409 and result['keep_request'] is True
        params = {key: str(data[key]) for key in ('lead_id', 'request_id', 'expected_tenant_id', 'expected_user_id')}
        status_read = await client.get('/receipt', params=params)
        assert status_read.status == 200
        assert (await status_read.json())['provider_accepted'] is True
    assert len(gateway['attempts']) == len(gateway['delivered']) == 1
    row = database.execute("""SELECT author_name,channel,raw FROM saas_crm_messages
        WHERE direction='outgoing' AND message_type='audio'""")
    assert len(row) == 1 and row[0]['author_name'] == 'Operator B' and row[0]['channel'] == 'connect:' + CB
    assert row[0]['raw']['participantJid'] == SOURCES[44][2]

@pytest.mark.asyncio
@pytest.mark.parametrize('voice_group', ['ordinary'], indirect=True)
async def test_ordinary_group_voice_uses_accepted_route_and_requires_fresh_membership(voice_group):
    database, membership, gateway = voice_group
    data = command(33)
    async with app_client(33) as client:
        capability = await client.get('/capability', params={key: str(data[key]) for key in
            ('lead_id', 'expected_tenant_id', 'expected_user_id')})
        assert (await capability.json())['enabled'] is True
        membership[SOURCES[33][2]] = False
        status, result = await upload(client, data)
        assert status == 409 and result['delivery_phase'] == 'not_sent' and result['keep_request'] is False
        assert not gateway['delivered']
        membership[SOURCES[33][2]] = True
        data = command(33)
        status, result = await upload(client, data)
        assert status == 200 and result['participant_jid'] == SOURCES[33][2]
        assert result['provider_accepted'] is True
        status, replay = await upload(client, data)
        assert status == 200 and replay['message_id'] == result['message_id']
    async with app_client(44) as client:
        status, denied = await upload(client, command(44))
        assert status in (403, 409)
        assert denied['keep_request'] is False
    assert gateway['checks'] == [SOURCES[33][2]] * 2
    assert len(gateway['attempts']) == 2 and len(gateway['delivered']) == 1
    messages = database.execute("SELECT channel, raw FROM saas_crm_messages WHERE message_type='audio'")
    assert len(messages) == 1 and messages[0]['channel'] == 'connect:' + SOURCES[33][0]
    assert messages[0]['raw']['participantJid'] == SOURCES[33][2]
