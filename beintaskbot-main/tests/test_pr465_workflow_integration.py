"""PR 465 workflows through real stores and gates; external I/O is synthetic.

The shared SQL fixture is disposable PGlite.  No authority, draft, enqueue, or
send-receipt function is replaced.  Only the provider boundary is stubbed.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from unittest.mock import Mock

from cryptography.fernet import Fernet
import pytest

from test_tenant_support_inbox import database, db, A, CA, AGENT, PROFILE, seed_job
import tenant_ai_agent_actions as actions
import tenant_ai_agent_connect as connect_ai
import tenant_ai_agent_no_answer as no_answer
import tenant_ai_agents_store as agents
import tenant_ai_group_coordination as groups
import tenant_chat_send_service as chat_send
import tenant_chat_send_store as chat_send_store
import tenant_connect_transport as transport
import tenant_dialog_consultations as flow
import tenant_dialog_consultations_worker as worker
import tenant_episode_media as episode_media
import tenant_notification_center as notification_center
import tenant_operator_copilot as copilot
import tenant_public_case as public_case
import tenant_support_inbox_store as cases
import tenant_support_inbox_summary as summaries
from tenant_chat_send_schema import ensure_schema as send_schema
from tenant_linear_policy import TenantLinearError


DEVELOPER = '15550002222@s.whatsapp.net'
CLIENT_GROUP = '120363400000009991@g.us'
DEVELOPER_GROUP = '120363400000008881@g.us'
LOGGER = logging.getLogger(__name__)


@pytest.fixture
def isolated(db, monkeypatch):
    job = seed_job(db)
    flow.schema(db)
    send_schema(db)
    groups.create_schema(db)
    for module in (flow, worker, transport, copilot, episode_media,
                   notification_center, public_case, chat_send_store, groups):
        monkeypatch.setattr(module, '_connect', lambda: db)
    cipher = Fernet.generate_key()
    monkeypatch.setenv('TENANT_ENCRYPTION_KEY', cipher.decode())
    encrypted = Fernet(cipher).encrypt(json.dumps({'api_key': 'synthetic-connect-key'}).encode())
    db.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets)
      VALUES(%s::uuid,'connect','connected',%s)""", (A, encrypted))
    db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,
      conversation_id,lead_id,contact_name,last_message_at)
      VALUES(%s::uuid,%s::uuid,%s,88,'Synthetic developer',now())""", (A, CA, DEVELOPER))
    db.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid", (A,))
    db.execute("UPDATE saas_crm_messages SET external_id='connect:original-problem' WHERE tenant_id=%s::uuid", (A,))
    db.commit()
    flow.developer_marker(PROFILE, {'channel_id': CA, 'conversation_id': DEVELOPER, 'enabled': True})
    # A creation may ask for an optional summary.  In this fixture no summary
    # model/consent is configured, so reaching paid I/O would be a regression.
    monkeypatch.setattr(summaries, 'generate', Mock(side_effect=AssertionError('unexpected summary provider call')))
    return db, job, Fernet(cipher)


def _create_manual_case(db):
    source = db.execute("SELECT id::text FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41", (A,))[0]['id']
    payload = {'request_id': str(uuid.uuid4()), 'lead_id': 41,
               'message_ids': [source], 'reason': 'Customer reports a connection problem',
               'assigned_developer_channel_id': CA,
               'assigned_developer_conversation_id': DEVELOPER,
               'developer_confirmed': True}
    with pytest.raises(TenantLinearError):
        cases.create(PROFILE, {key: value for key, value in payload.items() if key != 'developer_confirmed'})
    assert not db.execute('SELECT id FROM saas_support_tickets')
    created = cases.create(PROFILE, payload)
    assert cases.create(PROFILE, payload)['ticket']['id'] == created['ticket']['id']
    ticket = created['ticket']
    assert ticket['assigned_developer_conversation_id'] == DEVELOPER
    assert ticket['assigned_to'] is None
    return ticket['id'], source


def _enable_private_copilot(db, cipher):
    db.execute("""UPDATE saas_ai_agents SET model='synthetic-agent-model',
      instructions='Use verified developer evidence. Tomorrow means planned review, not resolution.',
      language='en',scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid""",
      (json.dumps([{'provider': 'connect', 'channel_id': CA}]), A, AGENT))
    secret = cipher.encrypt(json.dumps({'tenant_id': A, 'provider': 'chat_ai', 'api_key': 'synthetic-ai-key'}).encode())
    db.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,secrets,metadata)
      VALUES(%s::uuid,'chat_ai',%s,%s::jsonb)""", (A, secret, json.dumps({
        'enabled': True, 'consent': True, 'copilot_enabled': True, 'daily_limit': 50,
        'models': ['synthetic-agent-model'], 'model': 'synthetic-agent-model'})))
    db.commit()


def test_create_api_acknowledges_each_request_and_holds_changed_developer_replays(isolated):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from tenant_support_inbox_api import handler

    db, _job, _cipher = isolated
    alternate = '15550004444@s.whatsapp.net'
    db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,
      conversation_id,lead_id,contact_name,last_message_at)
      VALUES(%s::uuid,%s::uuid,%s,89,'Alternate developer',now())""", (A, CA, alternate))
    db.commit()
    flow.developer_marker(PROFILE, {'channel_id': CA, 'conversation_id': alternate, 'enabled': True})
    # Sorting the request UUIDs makes the old earliest-event projection differ
    # deterministically from the second POST's request-specific acknowledgement.
    first_request, second_request = (str(uuid.UUID(int=n)) for n in (465001, 465002))
    original = {'request_id': first_request, 'lead_id': 41,
                'expected_tenant_id': A, 'expected_user_id': 11,
                'assigned_developer_channel_id': CA,
                'assigned_developer_conversation_id': DEVELOPER,
                'developer_confirmed': True}

    async def scenario():
        path = '/api/platform/ai/support-tickets'
        app = web.Application()
        for method, suffix, resource in [('POST', '', 'tickets'), ('GET', '/{ticket_id}', 'ticket'),
                                         ('PATCH', '/{ticket_id}', 'ticket')]:
            app.router.add_route(method, path + suffix, handler(lambda _request: PROFILE,
                                 'https://crm.test', LOGGER, resource))
        async with TestClient(TestServer(app)) as client:
            async def create(payload, expected_status=200):
                response = await client.post(path, json=payload, headers={'Origin': 'https://crm.test'})
                result = await response.json()
                assert response.status == expected_status, result
                return result.get('result', {}).get('ticket')

            first = await create(original)
            case_id = first['id']

            def exact(ticket, request_id, developer=DEVELOPER):
                assert ticket['id'] == case_id
                assert ticket['create_request_id'] == request_id
                assert ticket['channel_id'] == CA and ticket['conversation_id'] == 'client'
                assert ticket['assigned_developer_channel_id'] == CA
                assert ticket['assigned_developer_conversation_id'] == developer

            exact(first, first_request)
            exact(await create(original), first_request)
            exact(await create({**original, 'request_id': second_request}), second_request)
            exact(await create(original), first_request)
            await create({**original, 'assigned_developer_conversation_id': alternate}, 409)
            assert db.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n'] == 1
            assert db.execute('SELECT assigned_developer_conversation_id FROM saas_support_tickets')[0]['assigned_developer_conversation_id'] == DEVELOPER

            changed = await client.patch(path + '/' + case_id, headers={'Origin': 'https://crm.test'}, json={
                'expected_tenant_id': A, 'expected_user_id': 11,
                'assigned_developer_channel_id': CA, 'assigned_developer_conversation_id': alternate})
            assert changed.status == 200, await changed.json()
            # The old pending payload stays immutable after another employee
            # action changes the assignment. It cannot acknowledge the new
            # developer as if the original Create had requested that developer.
            await create(original, 409)
            await create({**original, 'request_id': second_request}, 409)
            current = await client.get(path + '/' + case_id)
            assert current.status == 200
            ticket = (await current.json())['result']['ticket']
            assert ticket['id'] == case_id and ticket['assigned_developer_conversation_id'] == alternate
            assert db.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n'] == 1
            assert {row['event_key'] for row in db.execute('SELECT event_key FROM saas_support_events')} == {
                'manual:' + first_request, 'manual:' + second_request}

    asyncio.run(scenario())


def test_manual_case_receipt_reply_real_private_draft_then_employee_send_once(isolated, monkeypatch):
    db, _job, cipher = isolated
    case_id, source = _create_manual_case(db)
    _enable_private_copilot(db, cipher)
    calls = []

    def external_connect(_key, method, path, payload=None, **_kwargs):
        assert method == 'POST' and path == '/v1/channels/' + CA + '/messages'
        calls.append(dict(payload))
        if payload['conversationId'] == DEVELOPER:
            return {'id': 'queued-developer-message', 'status': 'pending'}
        assert payload['conversationId'] == 'client'
        return {'id': 'employee-approved-client-message'}

    monkeypatch.setattr(transport, '_request', external_connect)
    request = {'request_id': str(uuid.uuid4()), 'case_id': case_id, 'channel_id': CA,
               'conversation_id': 'client', 'destination_channel_id': CA,
               'destination_conversation_id': DEVELOPER, 'message_ids': [source],
               'media_ids': [], 'summary': 'Investigate the reported connection problem', 'confirm': True}
    prepared = flow.prepare(PROFILE, request, 'https://crm.test')
    assert flow.dispatch(PROFILE, prepared['id'], request['request_id'])['state'] == 'pending'
    assert flow.dispatch(PROFILE, prepared['id'], request['request_id'])['state'] == 'pending'
    assert len(calls) == 1
    body = calls[0]['text']
    assert request['summary'] in body and 'https://crm.test/public/case/' in body
    assert 'CONSULT:' not in body and prepared['id'] not in body
    assert db.execute('SELECT public_identity FROM saas_public_case_shares')[0]['public_identity'] == {}

    # A reply arrives before the authoritative outbound delivery receipt.
    text = 'Завтра посмотрю'
    raw = {'id': 'developer-review-tomorrow', 'quotedExternalMessageId': 'PROVIDER_DEVELOPER_REQUEST'}
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
      direction,channel,body,message_type,raw,author_name)
      VALUES(%s::uuid,%s::uuid,88,'connect:developer-review-tomorrow','incoming',%s,%s,'text',%s::jsonb,'Synthetic developer')""",
      (uuid.uuid4(), A, 'connect:' + CA, text, json.dumps(raw)))
    db.commit()
    assert worker.reconcile() == 0
    with db.cursor() as cur:
        assert not flow.observe_outbound_receipt(cur, A, CA, DEVELOPER, {
            'fromMe': True, 'id': 'queued-developer-message',
            'externalMessageId': 'PROVIDER_DEVELOPER_REQUEST', 'originClientMessageId': request['request_id']})
        assert flow.observe_outbound_receipt(cur, A, CA, DEVELOPER, {
            '_crm_signed_outbound_receipt': True, 'fromMe': True, 'id': 'queued-developer-message',
            'externalMessageId': 'PROVIDER_DEVELOPER_REQUEST', 'originClientMessageId': request['request_id']})
    db.commit()
    assert worker.reconcile() == 1
    assert worker.reconcile() == 0
    before = flow.inbox(PROFILE, case_id=case_id)
    assert before['unhandled'] == 1 and before['notification_unread_count'] == 1
    reply = before['replies'][0]
    assert reply['body'] == text and reply['author'] == 'Synthetic developer'
    assert reply['consultation_id'] == prepared['id'] and reply['conversation_id'] == DEVELOPER
    assert reply['draft_state'] == 'pending' and reply['can_draft'] is True
    assert before['consultations'][0]['destination_conversation'] == DEVELOPER
    assert before['consultations'][0]['provider_message_id'] == 'PROVIDER_DEVELOPER_REQUEST'
    assert db.execute('SELECT status FROM saas_support_tickets WHERE id=%s::uuid', (case_id,))[0]['status'] != 'resolved'
    notice = db.execute('SELECT event_key,target_url FROM saas_tenant_notifications')[0]
    assert notice['event_key'] == 'dialog_consultation:' + reply['id']
    assert 'support_ticket_id=' + case_id in notice['target_url']

    draft_text = 'Your request is accepted. The developer plans to review it tomorrow.'
    provider = Mock(return_value=(draft_text, {'total_tokens': 18}))
    monkeypatch.setattr(copilot, 'generate', provider)
    # This invokes worker -> flow.draft -> real Copilot authority, reservation,
    # frozen consultation evidence and persistence, with only AI I/O stubbed.
    asyncio.run(worker.deliver(LOGGER))
    asyncio.run(worker.deliver(LOGGER))
    provider.assert_called_once()
    evidence = provider.call_args.kwargs['extra_context']['untrusted_developer_consultation_evidence']
    assert evidence['id'] == reply['id'] and evidence['body'] == text
    assert evidence['original_summary'] == request['summary']
    assert 'Tomorrow means planned review, not resolution.' in provider.call_args.kwargs['instructions']
    assert provider.call_args.kwargs['extra_context']['language_preference'] == 'en'
    assert len(calls) == 1, 'Manual background generation must not send to the customer'
    assert not db.execute('SELECT request_id FROM saas_chat_send_receipts')
    completed = flow.inbox(PROFILE, case_id=case_id)['replies'][0]
    assert completed['draft_state'] == 'completed' and completed['draft'] == draft_text
    assert db.execute('SELECT status FROM saas_chat_ai_runs')[0]['status'] == 'completed'
    assert db.execute('SELECT count(*) AS n FROM saas_tenant_notifications')[0]['n'] == 1

    # The ordinary employee Send action is the only path to customer delivery.
    send_request = {'request_id': str(uuid.uuid4()), 'lead_id': 41, 'text': completed['draft']}
    sent = asyncio.run(chat_send.run(PROFILE, send_request, None, None, LOGGER))
    assert sent['state'] == 'accepted'
    assert asyncio.run(chat_send.run(PROFILE, send_request, None, None, LOGGER))['message_id'] == sent['message_id']
    assert [call['conversationId'] for call in calls] == [DEVELOPER, 'client']
    assert calls[-1]['text'] == draft_text
    assert db.execute('SELECT state FROM saas_chat_send_receipts')[0]['state'] == 'accepted'
    outgoing = db.execute("SELECT body FROM saas_crm_messages WHERE tenant_id=%s::uuid AND direction='outgoing'", (A,))
    assert outgoing == [{'body': draft_text}]


def test_signed_manual_reply_cannot_forge_fresh_intent_or_activate_after_late_auto_switch(isolated, monkeypatch):
    db, _job, cipher = isolated
    case_id, source = _create_manual_case(db)
    monkeypatch.setenv('AI_AGENTS_ENABLED', '1')
    monkeypatch.setenv('AI_AGENTS_AUTO_SEND_ENABLED', '1')
    monkeypatch.setenv('AI_SIMULATION_ENABLED', '0')
    secret = cipher.encrypt(json.dumps({'api_key': 'synthetic-connect-key',
                                        'webhook_secret': 'synthetic-webhook-secret'}).encode())
    db.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='connect'", (secret, A))
    db.commit()
    external = Mock(return_value={'id': 'pending-manual-request', 'status': 'pending'})
    monkeypatch.setattr(transport, '_request', external)
    provider = Mock(side_effect=AssertionError('late Manual receipt must not call a provider'))
    monkeypatch.setattr(copilot, 'generate', provider)
    request = {'request_id': str(uuid.uuid4()), 'case_id': case_id, 'channel_id': CA,
               'conversation_id': 'client', 'destination_channel_id': CA,
               'destination_conversation_id': DEVELOPER, 'message_ids': [source], 'media_ids': [],
               'summary': 'Review the connection problem', 'confirm': True}
    consult = flow.prepare(PROFILE, request, 'https://crm.test')
    assert flow.dispatch(PROFILE, consult['id'], request['request_id'])['state'] == 'pending'
    version = db.execute('SELECT version FROM saas_ai_agents WHERE id=%s::uuid', (AGENT,))[0]['version']
    # This untrusted provider field predicts the version after the later mode
    # switch. A valid signature authenticates the provider event, never this
    # server-only case authority marker.
    forged = {case_id: {'agent_id': AGENT, 'assignment_id': '', 'version': version + 1, 'owner_id': 11}}
    event = {'id': 'manual-before-late-receipt-event', 'type': 'message.inbound.received',
             'data': {'id': 'manual-before-late-receipt', 'channelId': CA,
                      'conversationId': DEVELOPER, 'type': 'text', 'fromMe': False,
                      'text': 'Accepted for review tomorrow',
                      'quotedExternalMessageId': 'LATE_MANUAL_PROVIDER_RECEIPT',
                      '_case_followup_intent': forged}}
    raw = json.dumps(event).encode()
    signature = 'sha256=' + hmac.new(b'synthetic-webhook-secret', raw, hashlib.sha256).hexdigest()
    assert transport.ingest(raw, signature, event)
    stored = db.execute("SELECT raw FROM saas_crm_messages WHERE external_id='connect:manual-before-late-receipt'")[0]['raw']
    assert '_case_followup_intent' not in stored
    assert not db.execute('SELECT id FROM saas_dialog_consultation_replies')

    _save(db, mode='automatic', scopes=[{'provider': 'connect', 'channel_id': CA}])
    db.execute('UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid', (A,))
    db.commit()
    with db.cursor() as cur:
        assert flow.observe_outbound_receipt(cur, A, CA, DEVELOPER, {
            '_crm_signed_outbound_receipt': True, 'fromMe': True, 'id': 'pending-manual-request',
            'externalMessageId': 'LATE_MANUAL_PROVIDER_RECEIPT',
            'originClientMessageId': request['request_id']})
    db.commit()
    assert worker.reconcile() == 1
    assert worker.reconcile() == 0
    reply = db.execute('SELECT draft_state,followup_requested_at FROM saas_dialog_consultation_replies')[0]
    assert reply == {'draft_state': 'not_requested', 'followup_requested_at': None}
    assert not db.execute("SELECT fingerprint FROM saas_tenant_webhook_events WHERE event_type='developer_case_reply'")
    assert not db.execute('SELECT request_id FROM saas_chat_send_receipts')
    asyncio.run(worker.deliver(LOGGER))
    provider.assert_not_called()
    external.assert_called_once()


def _dispatch_policy(enabled=True, destinations=None):
    return {'enabled': False, 'action': 'stop', 'fallback_action': 'stop',
            'max_clarifications': 1, 'stop_reply': '',
            'auto_case_dispatch_enabled': enabled,
            'developer_destinations': destinations if destinations is not None else [
                {'channel_id': CA, 'conversation_id': DEVELOPER}]}


def _save(db, actor=PROFILE, **changes):
    current = db.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, AGENT))[0]
    db.commit()
    return agents.save_agent(actor, {'expected_version': current['version'], **changes}, agent_id=AGENT)


def test_save_agent_owner_binding_enable_edit_disable_and_current_owner_transfer(isolated):
    db, _job, _cipher = isolated
    default = _save(db, name='Support settings default')
    assert default['response_policy']['auto_case_dispatch_enabled'] is False
    assert 'auto_case_dispatch_owner_id' not in default['response_policy']
    enabled = _save(db, mode='automatic', response_policy=_dispatch_policy())
    assert enabled['response_policy']['auto_case_dispatch_owner_id'] == 11
    assert no_answer.selected_action(enabled['response_policy'], 'missing_knowledge')['action'] == 'continue'
    edited = _save(db, name='Renamed overnight support')
    assert edited['response_policy']['auto_case_dispatch_enabled'] is True
    assert edited['response_policy']['auto_case_dispatch_owner_id'] == 11
    assert no_answer.selected_action(edited['response_policy'], 'missing_knowledge')['action'] == 'continue'

    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,44,'owner','Successor')", (A,))
    db.execute('UPDATE saas_tenants SET owner_telegram_id=44 WHERE id=%s::uuid', (A,))
    db.execute("UPDATE saas_tenant_members SET role='operator',permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=11", (A,))
    db.commit()
    successor = {'tenant_id': A, 'telegram_id': 44}
    transferred = _save(db, actor=successor, name='Successor reviewed settings')
    assert transferred['response_policy']['auto_case_dispatch_owner_id'] == 44
    disabled = _save(db, actor=successor, response_policy=_dispatch_policy(False, []))
    assert disabled['response_policy']['auto_case_dispatch_enabled'] is False
    assert 'auto_case_dispatch_owner_id' not in disabled['response_policy']
    reenabled = _save(db, actor=successor, response_policy=_dispatch_policy())
    assert reenabled['response_policy']['auto_case_dispatch_owner_id'] == 44
    manual = _save(db, actor=successor, mode='manual')
    assert manual['response_policy']['auto_case_dispatch_enabled'] is False
    assert 'auto_case_dispatch_owner_id' not in manual['response_policy']


@pytest.mark.parametrize('forged_owner', [11, 22, None, True, '11'])
def test_save_agent_rejects_browser_owned_dispatch_owner_even_when_it_matches(isolated, forged_owner):
    db, _job, _cipher = isolated
    _save(db, mode='automatic', response_policy=_dispatch_policy())
    before = db.execute('SELECT version,response_policy FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, AGENT))[0]
    db.commit()
    with pytest.raises(TenantLinearError):
        _save(db, response_policy={**_dispatch_policy(), 'auto_case_dispatch_owner_id': forged_owner})
    assert db.execute('SELECT version,response_policy FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, AGENT))[0] == before


def test_each_configured_developer_projects_only_its_exact_receipted_reply(isolated, monkeypatch):
    db, job, _cipher = isolated
    second = '15550003333@s.whatsapp.net'
    db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,
      lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,89,'Second developer',now())""", (A, CA, second))
    db.commit()
    flow.developer_marker(PROFILE, {'channel_id': CA, 'conversation_id': second, 'enabled': True})
    _save(db, mode='automatic', scopes=[{'provider': 'connect', 'channel_id': CA}],
          response_policy=_dispatch_policy(destinations=[
              {'channel_id': CA, 'conversation_id': DEVELOPER},
              {'channel_id': CA, 'conversation_id': second}]))
    db.execute('UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid', (A,))
    db.commit()
    monkeypatch.setenv('AI_AGENTS_ENABLED', '1')
    monkeypatch.setenv('AI_AGENTS_AUTO_SEND_ENABLED', '1')
    provider_calls = []

    def external_connect(_key, method, path, payload=None, **_kwargs):
        assert method == 'POST' and path == '/v1/channels/' + CA + '/messages'
        assert payload['conversationId'] in {DEVELOPER, second}
        provider_calls.append(dict(payload))
        return {'id': 'queued-' + str(len(provider_calls)), 'status': 'pending'}

    monkeypatch.setattr(transport, '_request', external_connect)
    with db.cursor() as cur:
        case = cases.handoff_ticket(cur, {**job, 'event_fingerprint': 'fresh-case-two-recipients',
            'source_message_id': 'original-problem', 'case_message_ids': ['original-problem']}, 'Needs developer review')
    db.commit()
    queued = worker.auto_dispatch_queue()
    assert len(queued) == 2
    for item_id in queued:
        worker.dispatch_auto_case(item_id)
    assert len(provider_calls) == 2
    assert {row['state'] for row in flow.inbox(PROFILE, case_id=case['id'])['auto_dispatches']} == {'pending'}
    for index, payload in enumerate(provider_calls, 1):
        with db.cursor() as cur:
            assert flow.observe_outbound_receipt(cur, A, CA, payload['conversationId'], {
                '_crm_signed_outbound_receipt': True, 'fromMe': True, 'id': 'queued-' + str(index),
                'externalMessageId': 'EXACT_PROVIDER_' + str(index),
                'originClientMessageId': payload['clientMessageId']})
        db.commit()
    worker.reconcile_auto_dispatches()
    provider_id = 'EXACT_PROVIDER_' + str(next(i for i, payload in enumerate(provider_calls, 1)
                                            if payload['conversationId'] == DEVELOPER))
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,
      channel,body,message_type,raw,author_name) VALUES(%s::uuid,%s::uuid,88,'connect:one-recipient-reply',
      'incoming',%s,'Accepted for review tomorrow','text',%s::jsonb,'Synthetic developer')""",
      (uuid.uuid4(), A, 'connect:' + CA, json.dumps({'id': 'one-recipient-reply',
                                                    'quotedExternalMessageId': provider_id})))
    db.commit()
    assert worker.reconcile() == 1
    assert worker.reconcile() == 0
    thread = flow.inbox(PROFILE, case_id=case['id'])
    assert {item['recipient']: item['state'] for item in thread['auto_dispatches']} == {
        'Synthetic developer': 'reply_received', 'Second developer': 'sent'}
    assert thread['notification_unread_count'] == 1 and len(thread['replies']) == 1
    assert thread['replies'][0]['draft_state'] == 'not_requested'
    # Historical correlation has no fresh automatic follow-up admission.
    assert not db.execute("SELECT fingerprint FROM saas_tenant_webhook_events WHERE event_type='developer_case_reply'")
    for item_id in queued:
        worker.dispatch_auto_case(item_id)
    assert len(provider_calls) == 2


def _claimed_client_group(db, job):
    db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,
      conversation_kind,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,'group',999,'Customer team'),
      (%s::uuid,%s::uuid,%s,'group',998,'Developer team')""", (A, CA, CLIENT_GROUP, A, CA, DEVELOPER_GROUP))
    db.execute("UPDATE saas_ai_agents SET mode='automatic',scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
      (json.dumps([{'provider': 'connect', 'channel_id': CA, 'allow_group_messages': True}]), A, AGENT))
    db.execute("""INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,revision,assignments)
      VALUES(%s::uuid,%s,'active',7,%s::jsonb),(%s::uuid,%s,'human',12,'[]'::jsonb)""",
      (A, CLIENT_GROUP, json.dumps([{'agent_id': AGENT, 'channel_id': CA, 'priority': 0}]), A, DEVELOPER_GROUP))
    db.execute("""INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,
      agent_id,state,revision) VALUES(%s::uuid,'connect',%s,%s,%s::uuid,'active',4)""", (A, CLIENT_GROUP, CA, AGENT))
    db.execute("""UPDATE saas_ai_agent_jobs SET conversation_id=%s,chat_id=%s,state='processing',
      lease_token=%s,lease_until=now()+interval '3 minutes',lead_id=999,revision=1
      WHERE tenant_id=%s::uuid AND event_fingerprint='evt'""", (CLIENT_GROUP, CLIENT_GROUP, job['lease_token'], A))
    source = str(uuid.uuid4())
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,
      body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,999,%s,'incoming',%s,'Group issue','text',clock_timestamp())""",
      (source, A, 'connect:' + source, 'connect:' + CA))
    db.execute("""INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,state)
      VALUES(%s::uuid,'group-next-event','connect','queued')""", (A,))
    db.commit()
    return {**job, 'lead_id': 999, 'conversation_id': CLIENT_GROUP, 'chat_id': CLIENT_GROUP}, source


def test_client_group_case_preserves_exact_current_authority_and_unclaimed_event(isolated):
    db, job, _cipher = isolated
    job, source = _claimed_client_group(db, job)
    assert actions.handoff(job, 'Group support case', source_message_id=source,
                           expected_conversation_revision=4, preserve_active=True)
    assert db.execute('SELECT state,revision FROM saas_ai_group_coordination WHERE group_jid=%s', (CLIENT_GROUP,))[0] == {'state': 'active', 'revision': 7}
    assert db.execute('SELECT state,revision FROM saas_ai_group_coordination WHERE group_jid=%s', (DEVELOPER_GROUP,))[0] == {'state': 'human', 'revision': 12}
    assert db.execute("SELECT state,revision FROM saas_ai_conversation_state WHERE conversation_id=%s", (CLIENT_GROUP,))[0] == {'state': 'active', 'revision': 4}
    assert db.execute("SELECT state,error_code FROM saas_ai_agent_jobs WHERE conversation_id=%s", (CLIENT_GROUP,))[0] == {'state': 'skipped', 'error_code': 'support_case_created'}
    assert db.execute("SELECT state FROM saas_tenant_webhook_events WHERE fingerprint='group-next-event'")[0]['state'] == 'queued'
    ticket = db.execute('SELECT conversation_id,history_scope FROM saas_support_tickets')[0]
    assert ticket == {'conversation_id': CLIENT_GROUP, 'history_scope': 'selected'}
    assert db.execute('SELECT message_id::text FROM saas_support_ticket_messages')[0]['message_id'] == source


@pytest.mark.parametrize('revocation', ['employee_reply', 'group_assignment', 'dialog_assignment'])
def test_late_group_employee_or_reassignment_blocks_preserve_active_handoff(isolated, revocation):
    db, job, _cipher = isolated
    job, source = _claimed_client_group(db, job)
    if revocation == 'employee_reply':
        connect_ai.mark_operator_reply(A, 999, CA, CLIENT_GROUP, actor=11)
    elif revocation == 'group_assignment':
        groups.configure(PROFILE, {'conversation_id': CLIENT_GROUP, 'expected_revision': 7, 'assignments': []})
    else:
        db.execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,agent_assignment_id=%s::uuid WHERE conversation_id=%s", (uuid.uuid4(), CLIENT_GROUP))
        db.commit()
    assert not actions.handoff(job, 'Stale generated case', source_message_id=source,
                               expected_conversation_revision=4, preserve_active=True)
    assert not db.execute('SELECT id FROM saas_support_tickets')
    if revocation == 'employee_reply':
        assert db.execute('SELECT state,assigned_to FROM saas_ai_group_coordination WHERE group_jid=%s', (CLIENT_GROUP,))[0] == {'state': 'human', 'assigned_to': 11}
    assert db.execute('SELECT state,revision FROM saas_ai_group_coordination WHERE group_jid=%s', (DEVELOPER_GROUP,))[0] == {'state': 'human', 'revision': 12}
