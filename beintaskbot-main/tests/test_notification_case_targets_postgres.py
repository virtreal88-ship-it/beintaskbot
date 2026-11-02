"""NOT01 through signed ingress, durable case events and real personal authority."""
import asyncio
import hashlib
import hmac
import importlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
import psycopg
from psycopg.rows import dict_row
import pytest
import requests

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, B, CA, CB, AGENT, PROFILE, seed_job
import tenant_ai_agent_actions as actions
import tenant_ai_agent_run_store as runs
import tenant_connect_transport as transport
import tenant_internal_consultations as internal
import tenant_member_store as members
import tenant_notification_center as center
import tenant_notification_center_api as notification_api
import tenant_support_inbox_api as support_api
import tenant_support_inbox_store as cases
import tenant_support_inbox_summary as summaries
from tenant_linear_policy import TenantLinearError


LOGGER = logging.getLogger(__name__)
ORIGIN = 'https://synthetic-case-notifications.example'
SECRET = 'synthetic-notification-case-webhook'


@pytest.fixture
def notifications(db, monkeypatch):
    factory = lambda: psycopg.connect(db.dsn, row_factory=dict_row)
    for name in (
        'tenant_platform', 'tenant_platform_core', 'tenant_member_store',
        'tenant_ai_agent_actions', 'tenant_ai_agent_connect', 'tenant_ai_agent_run_store',
        'tenant_notification_center', 'tenant_support_inbox_store', 'tenant_support_inbox_summary',
        'tenant_internal_consultations', 'tenant_connect_transport', 'tenant_ai_group_coordination',
        'tenant_episode_media', 'tenant_media_evidence', 'tenant_dialog_consultations',
        'tenant_public_case', 'tenant_knowledge_live_queue', 'tenant_telegram_binding',
    ):
        module = importlib.import_module(name)
        if hasattr(module, '_connect'):
            monkeypatch.setattr(module, '_connect', factory)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://invalid.invalid/disabled')
    key = Fernet.generate_key()
    monkeypatch.setenv('TENANT_ENCRYPTION_KEY', key.decode())
    for name in ('AI_AGENTS_ENABLED', 'AI_AGENTS_AUTO_SEND_ENABLED'):
        monkeypatch.setenv(name, 'true')  # Disposable claim admission; no worker/model runs.
    for name in ('AI_AGENTS_PLATFORM_ENABLED', 'AI_SIMULATION_ENABLED', 'AI_AGENTS_SCOPED_LIVE_ENABLED',
                 'AI_AGENTS_PILOT_SCOPE_ENABLED', 'AI_AGENTS_CRM_ENABLED', 'AI_NATIVE_CRM_ACTIONS_ENABLED'):
        monkeypatch.setenv(name, 'false')
    for name in ('AI_AGENTS_PILOT_TENANT_ID', 'AI_AGENTS_PILOT_CHANNEL_ID', 'AI_AGENTS_PILOT_CONVERSATION_ID',
                 'AI_AGENTS_PILOT_GROUP_CONVERSATION_ID', 'AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT', 'AI_AGENTS_PLATFORM_KEY_AGENTS'):
        monkeypatch.delenv(name, raising=False)
    forbidden = Mock(side_effect=AssertionError('Unexpected external provider/transport boundary'))
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    monkeypatch.setattr(summaries, 'generate', forbidden)
    for name in ('_request', 'send_ai_reply', 'send_consultation_with_receipt', 'send_group_with_receipt'):
        monkeypatch.setattr(transport, name, forbidden)
    async_forbidden = AsyncMock(side_effect=AssertionError('Unexpected async HTTP provider boundary'))
    if importlib.util.find_spec('httpx'):
        import httpx
        monkeypatch.setattr(httpx.Client, 'request', forbidden)
        monkeypatch.setattr(httpx.AsyncClient, 'request', async_forbidden)
    seed_job(db)
    db.execute("UPDATE saas_ai_agent_jobs SET state='skipped' WHERE tenant_id=%s::uuid", (A,))
    db.execute("UPDATE saas_ai_agents SET mode='automatic',delay_seconds=0,scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
               (json.dumps([{'provider': 'connect', 'channel_id': CA}]), A, AGENT))
    db.execute('UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid', (A,))
    for actor, role in ((33, 'admin'), (34, 'operator')):
        db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions) VALUES(%s::uuid,%s,%s,%s,'[\"support\"]'::jsonb)",
                   (A, actor, role, 'Synthetic recipient ' + str(actor)))
    encrypted = Fernet(key).encrypt(json.dumps({'api_key': 'synthetic-not-live', 'webhook_secret': SECRET}).encode())
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)", (A, encrypted))
    db.execute('DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid', (A,))
    db.commit()
    yield {'db': db, 'factory': factory, 'forbidden': forbidden}
    forbidden.assert_not_called()
    async_forbidden.assert_not_called()


def _handoff(f, *, conversation='client', reason='unsupported_evidence'):
    if conversation != 'client':
        for table in ('saas_connect_conversations', 'saas_ai_conversation_state', 'saas_ai_agent_jobs'):
            f['db'].execute('UPDATE '+table+" SET conversation_id=%s WHERE tenant_id=%s::uuid AND conversation_id='client'", (conversation, A))
        f['db'].execute('UPDATE saas_ai_agent_jobs SET chat_id=%s WHERE tenant_id=%s::uuid', (conversation, A))
        f['db'].commit()
    mid = str(uuid.uuid4())
    event = {'id': str(uuid.uuid4()), 'type': 'message.inbound.received', 'data': {
        'id': mid, 'channelId': CA, 'conversationId': conversation, 'type': 'text',
        'text': 'Synthetic customer cannot print the saved invoice.', 'fromMe': False}}
    raw = json.dumps(event).encode()
    signature = 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert transport.ingest(raw, signature, event)
    job = runs.claim_next()
    assert job and job['payload']['message_id'] == mid
    revision = f['db'].execute('SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider=\'connect\' AND channel_id=%s AND conversation_id=%s',
                              (A, CA, conversation))[0]['revision']
    f['db'].commit()
    assert actions.handoff(job, reason, source_message_id=mid,
        expected_conversation_revision=revision, case_message_ids=[mid])
    case_id = str(f['db'].execute('SELECT ticket_id FROM saas_support_events WHERE tenant_id=%s::uuid AND event_key=%s',
                                (A, 'handoff:' + job['event_fingerprint']))[0]['ticket_id'])
    notice = f['db'].execute('SELECT id::text,event_key,target_url FROM saas_tenant_notifications WHERE tenant_id=%s::uuid AND telegram_id=11', (A,))[0]
    f['db'].commit()
    return {**f, 'job': job, 'mid': mid, 'event': event, 'raw': raw, 'signature': signature,
            'case_id': case_id, 'notification_id': notice['id'], 'event_key': notice['event_key'], 'reason': reason}


def _app(origin=ORIGIN):
    def authenticated(request):
        return members.member(request.headers.get('X-Synthetic-Tenant', A), int(request.headers.get('X-Synthetic-Actor', '11')))
    app = web.Application()
    app.router.add_get('/notifications', notification_api.handler(authenticated, origin, LOGGER))
    app.router.add_post('/notifications/{notification_id}/read', notification_api.handler(authenticated, origin, LOGGER, 'read'))
    app.router.add_get('/support/{ticket_id}', support_api.handler(authenticated, origin, LOGGER, 'ticket'))
    return app


async def _context(http, f, *, case_id=None, notification_id=None, actor=11, tenant=A, query=None):
    params = query if query is not None else {'notification_id': notification_id or f['notification_id'],
        'expected_tenant_id': tenant, 'expected_user_id': str(actor)}
    response = await http.get('/support/' + (case_id or f['case_id']), params=params,
        headers={'X-Synthetic-Tenant': tenant, 'X-Synthetic-Actor': str(actor)})
    return response.status, await response.json()


async def _read(http, f, *, actor=11, tenant=A, extra=None):
    data = {'expected_tenant_id': tenant, 'expected_user_id': actor,
            'opened_case_id': f['case_id'], 'opened_event_key': f['event_key']}
    if extra is not None:
        data = extra
    response = await http.post('/notifications/'+f['notification_id']+'/read', json=data,
        headers={'Origin': ORIGIN, 'X-Synthetic-Tenant': tenant, 'X-Synthetic-Actor': str(actor)})
    return response.status, await response.json()


def _state(f):
    return {
        'tickets': f['db'].execute('SELECT tenant_id,id,status,reason,summary_state,assigned_to,resolved_at FROM saas_support_tickets ORDER BY tenant_id,id'),
        'ai': f['db'].execute('SELECT tenant_id,provider,channel_id,conversation_id,state,reason,revision FROM saas_ai_conversation_state ORDER BY tenant_id,channel_id,conversation_id'),
        'consultations': f['db'].execute('SELECT id,status,delivery_state FROM saas_internal_consultations ORDER BY id'),
        'runs': f['db'].execute('SELECT id,state FROM saas_ai_agent_runs ORDER BY id'),
        'handled': f['db'].execute('SELECT id,handled_at FROM saas_dialog_consultation_replies ORDER BY id'),
    }


def test_signed_handoff_notification_opens_exact_case_context_then_personal_read_without_status_changes(notifications):
    f = _handoff(notifications)
    before = _state(f)
    item = center.listing(PROFILE)['notifications'][0]
    assert item['target_state'] == 'case' and item['case_id'] == f['case_id']
    assert item['event_kind'] == 'ai_handoff' and item['reason_code'] == 'unsupported_evidence'
    target = parse_qs(urlsplit(item['target_url']).query)
    assert target == {'view': ['support'], 'support_ticket_id': [f['case_id']], 'notification_id': [f['notification_id']]}
    assert center.listing(PROFILE)['unread_count'] == 1
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, body = await _context(http, f)
            assert status == 200, body
            context = body['result']['notification_context']
            assert context['notification_id'] == f['notification_id'] and context['case_id'] == f['case_id']
            assert context['event_key'] == f['event_key'] and context['event_kind'] == 'ai_handoff'
            assert context['reason_code'] == context['body'] == 'unsupported_evidence'
            assert 'consultation_id' not in context
            assert f['db'].execute('SELECT read_at FROM saas_tenant_notifications WHERE id=%s::uuid', (f['notification_id'],))[0]['read_at'] is None
            status, read = await _read(http, f)
            assert status == 200 and read['result']['unread_count'] == 0
            stamp = read['result']['read_at']
            status, repeated = await _read(http, f)
            assert status == 200 and repeated['result']['read_at'] == stamp
    asyncio.run(scenario())
    assert _state(f) == before
    assert center.listing({'tenant_id': A, 'telegram_id': 33})['unread_count'] == 1


def test_replayed_signed_source_and_handoff_preserve_one_case_event_and_personal_notice(notifications):
    f = _handoff(notifications)
    before = _state(f)
    transport.ingest(f['raw'], f['signature'], f['event'])
    assert not actions.handoff(f['job'], f['reason'], source_message_id=f['mid'], case_message_ids=[f['mid']])
    assert f['db'].execute('SELECT count(*) AS n FROM saas_support_events WHERE tenant_id=%s::uuid', (A,))[0]['n'] == 1
    assert f['db'].execute('SELECT count(*) AS n FROM saas_tenant_notifications WHERE tenant_id=%s::uuid AND telegram_id=11', (A,))[0]['n'] == 1
    assert center.listing(PROFILE)['unread_count'] == 1
    assert _state(f) == before


def _new_case(f, *, conversation='other-case', lead=43, event_key=None):
    f['db'].execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,%s)',
                    (A, CA, conversation, lead))
    with f['db'].cursor() as cur:
        case = cases.create_in_transaction(cur, A, CA, conversation, 'Synthetic separate case',
            event_key or 'manual:' + str(uuid.uuid4()), source='manual', actor=11)
    f['db'].commit()
    return case['id']


def _notice(f, event, target, *, actor=11, tenant=A, body='Synthetic saved event'):
    ident = str(uuid.uuid4())
    f['db'].execute("INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url) VALUES(%s::uuid,%s::uuid,%s,%s,'Synthetic notification',%s,%s)",
                    (ident, tenant, actor, event, body, target))
    f['db'].commit()
    return ident


def test_internal_consultation_emitter_keeps_exact_original_event_reason_and_recipient(notifications):
    f = _handoff(notifications)
    reason = 'ai_developer_consultation:missing_knowledge'
    evidence = {'query': 'Synthetic customer cannot print the saved invoice.',
                'case_message_ids': [f['mid']], 'source_external_id': 'connect:' + f['mid']}
    first = internal.create_from_ai(f['job'], recipient_id=33, reason=reason, evidence=evidence)
    second = internal.create_from_ai(f['job'], recipient_id=33, reason=reason, evidence=evidence)
    assert first['created'] and not second['created'] and first['id'] == second['id']
    item = next(n for n in center.listing({'tenant_id': A, 'telegram_id': 33})['notifications']
                if n.get('event_kind') == 'ai_consultation')
    assert item['target_state'] == 'case' and item['consultation_id'] == first['id']
    assert item['case_id'] == f['case_id'] and item['reason_code'] == reason
    f['db'].execute("UPDATE saas_support_tickets SET reason='A newer mutable case reason' WHERE tenant_id=%s::uuid AND id=%s::uuid", (A, f['case_id']))
    f['db'].commit()
    before = _state(f)
    actual = {**f, 'notification_id': item['id'], 'event_key': item['event_key']}
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, body = await _context(http, actual, actor=33)
            assert status == 200, body
            context = body['result']['notification_context']
            assert context['reason_code'] == reason and context['consultation_id'] == first['id']
            assert context['event_key'] == item['event_key'] and context['body'] == item['body']
            status, denied = await _context(http, actual)
            assert status == 404 and denied['success'] is False
            status, read = await _read(http, actual, actor=33)
            assert status == 200 and read['result']['unread_count'] == 1
    asyncio.run(scenario())
    assert _state(f) == before
    assert center.listing(PROFILE)['unread_count'] == 1


def test_legacy_colon_route_repairs_from_exact_event_before_module_filter_without_rewriting_history(notifications):
    f = _handoff(notifications, conversation='synthetic:source:client')
    notice = f['db'].execute('SELECT id::text,event_key FROM saas_tenant_notifications WHERE tenant_id=%s::uuid AND telegram_id=33', (A,))[0]
    old_url = '/app?view=deals&deal_id=41'
    f['db'].execute('UPDATE saas_tenant_notifications SET target_url=%s WHERE id=%s::uuid', (old_url, notice['id']))
    f['db'].execute("UPDATE saas_support_tickets SET reason='Later case text must not replace the original reason' WHERE tenant_id=%s::uuid", (A,))
    f['db'].commit()
    profile = {'tenant_id': A, 'telegram_id': 33}  # Support right, no Deals or Dialogs grant.
    result = center.listing(profile)
    assert result['unread_count'] == 1
    item = result['notifications'][0]
    assert item['id'] == notice['id'] and item['case_id'] == f['case_id']
    assert item['reason_code'] == 'unsupported_evidence'
    assert item['target_state'] == 'case' and 'view=support' in item['target_url']
    assert f['db'].execute('SELECT target_url FROM saas_tenant_notifications WHERE id=%s::uuid', (notice['id'],))[0]['target_url'] == old_url


@pytest.mark.parametrize('mutation,status', [('wrong_case', 409), ('other_actor', 404), ('other_tenant', 404),
    ('wrong_expected', 409), ('invalid_id', 400), ('duplicate_id', 400), ('duplicate_expected', 400)])
def test_case_context_http_rejects_cross_binding_identity_and_malformed_query(notifications, mutation, status):
    f = _handoff(notifications)
    other = _new_case(f)
    before = _state(f)
    options = {}
    if mutation == 'wrong_case': options['case_id'] = other
    elif mutation == 'other_actor': options['actor'] = 34
    elif mutation == 'other_tenant': options.update(actor=22, tenant=B)
    else:
        query = [('notification_id', f['notification_id']), ('expected_tenant_id', A), ('expected_user_id', '11')]
        if mutation == 'wrong_expected': query[1] = ('expected_tenant_id', B)
        elif mutation == 'invalid_id': query[0] = ('notification_id', 'not-a-uuid')
        elif mutation == 'duplicate_id': query.append(('notification_id', f['notification_id']))
        elif mutation == 'duplicate_expected': query.append(('expected_user_id', '11'))
        options['query'] = query
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            actual, body = await _context(http, f, **options)
            assert actual == status and body['success'] is False, body
    asyncio.run(scenario())
    assert _state(f) == before
    assert f['db'].execute('SELECT read_at FROM saas_tenant_notifications WHERE id=%s::uuid', (f['notification_id'],))[0]['read_at'] is None


@pytest.mark.parametrize('bad', ['case_only', 'event_only', 'invalid_case', 'wrong_case', 'wrong_event'])
def test_paired_read_rejects_incomplete_or_stale_render_receipt_without_acknowledging(notifications, bad):
    f = _handoff(notifications)
    data = {'expected_tenant_id': A, 'expected_user_id': 11,
            'opened_case_id': f['case_id'], 'opened_event_key': f['event_key']}
    if bad == 'case_only': data.pop('opened_event_key')
    elif bad == 'event_only': data.pop('opened_case_id')
    elif bad == 'invalid_case': data['opened_case_id'] = 'invalid'
    elif bad == 'wrong_case': data['opened_case_id'] = str(uuid.uuid4())
    else: data['opened_event_key'] += ':changed'
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, body = await _read(http, f, extra=data)
            assert status == (409 if bad.startswith('wrong') else 400), body
    asyncio.run(scenario())
    assert center.listing(PROFILE)['unread_count'] == 1
    assert _state(f) == before


def test_lost_event_proof_after_open_does_not_acknowledge_and_never_guesses_latest_case(notifications):
    f = _handoff(notifications)
    _new_case(f)
    f['db'].execute('DELETE FROM saas_support_events WHERE tenant_id=%s::uuid AND event_key=%s', (A, 'handoff:' + f['job']['event_fingerprint']))
    f['db'].commit()
    item = center.listing(PROFILE)['notifications'][0]
    assert item['target_state'] == 'unavailable' and item['target_url'] == ''
    assert 'case_id' not in item
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, _ = await _context(http, f)
            assert status == 409
            status, _ = await _read(http, f)
            assert status == 409
    asyncio.run(scenario())
    assert center.listing(PROFILE)['unread_count'] == 1


@pytest.mark.parametrize('revoked', ['support', 'membership'])
def test_current_recipient_rights_are_rechecked_after_notification_was_listed(notifications, revoked):
    f = _handoff(notifications)
    profile = {'tenant_id': A, 'telegram_id': 33}
    item = center.listing(profile)['notifications'][0]
    current = {**f, 'notification_id': item['id'], 'event_key': item['event_key']}
    if revoked == 'support':
        f['db'].execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=33", (A,))
    else:
        f['db'].execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=33', (A,))
    f['db'].commit()
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, _ = await _context(http, current, actor=33)
            assert status == 403
            status, _ = await _read(http, current, actor=33)
            assert status == (404 if revoked == 'support' else 403)
    asyncio.run(scenario())
    assert f['db'].execute('SELECT read_at FROM saas_tenant_notifications WHERE id=%s::uuid', (item['id'],))[0]['read_at'] is None


def test_inactive_channel_history_remains_readable_and_never_reactivates_ai_or_sends(notifications):
    f = _handoff(notifications)
    f['db'].execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid', (A,))
    f['db'].commit()
    before = _state(f)
    assert center.listing(PROFILE)['notifications'][0]['target_state'] == 'case'
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, body = await _context(http, f)
            assert status == 200 and body['result']['ticket']['channel_active'] is False
            status, _ = await _read(http, f)
            assert status == 200
    asyncio.run(scenario())
    assert _state(f) == before
    assert f['db'].execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s::uuid', (A,))[0]['active'] is False


def test_tenant_qualified_same_case_uuid_and_event_key_do_not_supply_foreign_binding(notifications):
    f = _handoff(notifications)
    f['db'].execute("INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,reason,status) VALUES(%s::uuid,%s::uuid,%s::uuid,'client','ai_handoff','B private case','waiting')", (B, f['case_id'], CB))
    f['db'].execute('INSERT INTO saas_support_events(tenant_id,event_key,ticket_id) VALUES(%s::uuid,%s,%s::uuid)',
                    (B, 'handoff:' + f['job']['event_fingerprint'], f['case_id']))
    b_notice = _notice(f, f['event_key'], '/app?view=support', tenant=B, actor=22, body='B private event')
    assert center.listing(PROFILE)['notifications'][0]['body'] == 'unsupported_evidence'
    assert center.listing({'tenant_id': B, 'telegram_id': 22})['notifications'][0]['body'] == 'B private event'
    f['db'].execute('DELETE FROM saas_support_events WHERE tenant_id=%s::uuid', (A,))
    f['db'].commit()
    ours = center.listing(PROFILE)['notifications'][0]
    assert ours['target_state'] == 'unavailable' and 'case_id' not in ours
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, _ = await _context(http, f)
            assert status == 409
            status, _ = await _context(http, f, notification_id=b_notice)
            assert status == 404
    asyncio.run(scenario())


@pytest.mark.parametrize('target', ['legacy', 'exact_left', 'unrelated_case'])
def test_ambiguous_legacy_colon_concatenation_requires_unique_source_proof(notifications, target):
    f = notifications
    left = _new_case(f, conversation='chat:a', lead=43, event_key='handoff:b')
    right = _new_case(f, conversation='chat', lead=44, event_key='handoff:a:b')
    unrelated = _new_case(f, conversation='unrelated', lead=45)
    event = 'ai_handoff:chat:a:b'
    url = '/app?view=deals' if target == 'legacy' else '/app?view=support&support_ticket_id=' + (left if target == 'exact_left' else unrelated)
    ident = _notice(f, event, url)
    actual = {**f, 'notification_id': ident, 'event_key': event, 'case_id': left}
    listing = center.listing(PROFILE)
    assert listing['unread_count'] == 1 and len(listing['notifications']) == 1
    item = listing['notifications'][0]
    assert item['id'] == ident
    if target == 'exact_left':
        assert item['target_state'] == 'case' and item['case_id'] == left
    else:
        assert item['target_state'] == 'unavailable' and item['target_url'] == '' and 'case_id' not in item
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, _ = await _read(http, {**actual, 'case_id': right})
            assert status == 409
            status, _ = await _context(http, actual)
            assert status == (200 if target == 'exact_left' else 409)
            status, _ = await _read(http, actual)
            assert status == (200 if target == 'exact_left' else 409)
    asyncio.run(scenario())
    if target != 'exact_left':
        assert center.listing(PROFILE)['unread_count'] == 1
        assert center.mark_read(PROFILE, ident)['unread_count'] == 0  # Existing explicit manual read.


@pytest.mark.parametrize('deleted', ['case', 'route', 'channel'])
def test_deleted_case_or_source_never_yields_notification_context_or_read_receipt(notifications, deleted):
    f = _handoff(notifications)
    # Preserve real FK semantics: a source route with a referenced case cannot
    # be deleted first. Never disable constraints to invent an orphan history.
    f['db'].execute('DELETE FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, f['case_id']))
    if deleted in {'route', 'channel'}:
        f['db'].execute("DELETE FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'", (A, CA))
    if deleted == 'channel':
        f['db'].execute('DELETE FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid', (A, CA))
    f['db'].commit()
    before = _state(f)
    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            status, body = await _context(http, f)
            assert status == 404 and body['success'] is False
            status, _ = await _read(http, f)
            assert status == 404
    asyncio.run(scenario())
    assert _state(f) == before
    assert f['db'].execute('SELECT read_at FROM saas_tenant_notifications WHERE id=%s::uuid', (f['notification_id'],))[0]['read_at'] is None


@pytest.mark.parametrize('target,visible', [('/app?view=support', True),
    ('/app?view=support&support_ticket_id=%FF', False),
    ('https://outside.invalid/app?view=support', False), ('/app?view=support#fragment', False)])
def test_unproven_or_malformed_ai_targets_preserve_existing_privacy_and_never_guess_a_case(notifications, target, visible):
    f = notifications
    ident = _notice(f, 'ai_handoff:unknown:unproven', target, body='unrecognized_backend_code_xyz')
    listing = center.listing(PROFILE)
    assert listing['unread_count'] == int(visible)
    assert len(listing['notifications']) == int(visible)
    if visible:
        item = listing['notifications'][0]
        assert item['target_state'] == 'unavailable' and item['target_url'] == ''
        assert 'case_id' not in item and 'consultation_id' not in item
    with pytest.raises(TenantLinearError) as rejected:
        center.mark_read(PROFILE, ident, opened_case_id=str(uuid.uuid4()), opened_event_key='ai_handoff:unknown:unproven')
    assert getattr(rejected.value, 'status', None) == (409 if visible else 404)
    assert f['db'].execute('SELECT read_at FROM saas_tenant_notifications WHERE id=%s::uuid', (ident,))[0]['read_at'] is None


def test_genuine_kommo_handoff_retains_provider_proven_deal_navigation_and_existing_module_guard(notifications):
    f = notifications
    fingerprint = 'synthetic:kommo:source'
    chat = 'synthetic:kommo:chat'
    f['db'].execute("INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,name) VALUES(%s::uuid,901,'Synthetic Kommo deal')", (A,))
    f['db'].execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,payload,state) VALUES(%s::uuid,%s,'kommo',%s::jsonb,'completed')",
                    (A, fingerprint, json.dumps({'chat_id': chat, 'lead_id': 901})))
    f['db'].execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s::uuid,'kommo','',%s,%s::uuid,'active')", (A, chat, AGENT))
    f['db'].commit()
    assert actions.handoff({'tenant_id': A, 'provider': 'kommo', 'channel_id': '', 'chat_id': chat,
        'conversation_id': chat, 'lead_id': 901, 'agent_id': AGENT, 'event_fingerprint': fingerprint}, 'Synthetic Kommo escalation')
    listing = center.listing(PROFILE)
    assert listing['unread_count'] == 1
    item = listing['notifications'][0]
    assert item['target_state'] == 'legacy' and item['target_url'] == '/app?view=deals&deal_id=901'
    assert 'case_id' not in item
    assert center.listing({'tenant_id': A, 'telegram_id': 33})['unread_count'] == 0
    assert f['db'].execute('SELECT count(*) AS n FROM saas_support_tickets WHERE tenant_id=%s::uuid', (A,))[0]['n'] == 0
    assert center.mark_read(PROFILE, item['id'])['unread_count'] == 0


@pytest.mark.parametrize('changed', ['event_key', 'target_url'])
def test_waiting_paired_read_does_not_acknowledge_a_notification_rebound_under_row_lock(notifications, changed):
    f = _handoff(notifications)
    other = _new_case(f)
    outcome = {}
    def read():
        try:
            outcome['result'] = center.mark_read(PROFILE, f['notification_id'],
                opened_case_id=f['case_id'], opened_event_key=f['event_key'])
        except Exception as error:
            outcome['error'] = error
    with f['factory']() as holder:
        holder.execute('SELECT 1 FROM saas_tenant_notifications WHERE id=%s::uuid FOR UPDATE', (f['notification_id'],))
        thread = threading.Thread(target=read)
        thread.start()
        blocked = False
        try:
            deadline = time.monotonic() + 5
            with f['factory']() as observer:
                while time.monotonic() < deadline:
                    blocked = bool(observer.execute('SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))', (holder.info.backend_pid,)).fetchall())
                    if blocked:
                        break
                    observer.commit()
                    time.sleep(.01)
            assert blocked, 'Personal read never reached the actual notification UPDATE lock'
            if changed == 'event_key':
                holder.execute('UPDATE saas_tenant_notifications SET event_key=%s WHERE id=%s::uuid', ('ai_handoff:changed:unproven', f['notification_id']))
            else:
                holder.execute('UPDATE saas_tenant_notifications SET target_url=%s WHERE id=%s::uuid', ('/app?view=support&support_ticket_id=' + other, f['notification_id']))
        finally:
            holder.commit()
            thread.join(10)
        assert not thread.is_alive()
    assert 'result' not in outcome
    assert isinstance(outcome.get('error'), TenantLinearError) and outcome['error'].status == 409
    assert f['db'].execute('SELECT read_at FROM saas_tenant_notifications WHERE id=%s::uuid', (f['notification_id'],))[0]['read_at'] is None


@pytest.mark.parametrize('mode', ['ready', 'revoked_during_detail'])
def test_actual_app_chromium_uses_real_case_http_and_acknowledges_only_a_rendered_current_event(notifications, mode):
    """Required bridge: no browser API interception, real PostgreSQL authority."""
    from tenant_policy import TenantPolicy
    from tenant_ai_agents_api import _json_dumps
    import tenant_dialog_consultations_api as consultation_api
    import tenant_public_case_api as public_api

    f = _handoff(notifications)
    item = center.listing({'tenant_id': A, 'telegram_id': 33})['notifications'][0]
    ident = item['id']
    unavailable = None
    if mode == 'ready':
        unavailable = _notice(f, 'ai_handoff:unproven:runtime', '/app?view=support', actor=33)
    before = _state(f)
    root = Path(__file__).resolve().parents[1]
    assert (root / '.support-test-deps/node_modules/playwright').exists(), 'Required Chromium bridge dependencies are missing'
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(16)
    origin = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    state = {'posts': [], 'blocked': [], 'pending': False, 'revoked': False}

    def principal(_request, *, owner_only=False):
        member = members.member(A, 33)
        return member if member and (not owner_only or member['role'] == 'owner') else None

    async def start():
        state['release'] = asyncio.Event()
        @web.middleware
        async def only_personal_read(request, handler):
            if request.method not in {'GET', 'HEAD'}:
                payload = await request.json() if request.content_type == 'application/json' else None
                state['posts'].append({'path': request.path, 'body': payload})
                if request.method != 'POST' or request.path != '/api/platform/notifications/' + ident + '/read':
                    state['blocked'].append(request.path)
                    return web.Response(status=405)
            return await handler(request)
        app = web.Application(middlewares=[only_personal_read])
        async def index(_request):
            return web.FileResponse(root / 'docs/tenant-app.html')
        async def asset(request):
            name = request.match_info['name']
            file = root / 'docs' / name
            if Path(name).name != name or not file.is_file():
                raise web.HTTPNotFound()
            return web.FileResponse(file)
        async def profile(request):
            member = principal(request)
            assert member
            return web.json_response({'success': True, 'member': member,
                'capabilities': TenantPolicy(member).public_capabilities(), 'integrations': [], 'members': []}, dumps=_json_dumps)
        detail = support_api.handler(principal, origin, LOGGER, 'ticket')
        async def gated_detail(request):
            if mode == 'revoked_during_detail' and request.query.get('notification_id') == ident:
                state['pending'] = True
                await state['release'].wait()
            return await detail(request)
        async def detail_state(_request):
            return web.json_response({'pending': state['pending']})
        async def revoke(_request):
            if mode != 'revoked_during_detail' or not state['pending']:
                return web.Response(status=409)
            with f['factory']() as conn:
                conn.execute("UPDATE saas_tenant_members SET permissions='[]'::jsonb WHERE tenant_id=%s::uuid AND telegram_id=33", (A,))
            state['revoked'] = True
            state['release'].set()
            return web.json_response({'success': True})
        async def unrelated_empty_read(request):
            # App startup may read unrelated modules. Case/notification
            # authority never falls through to this fixture-only empty state.
            if any(prefix in request.path for prefix in ('notifications', 'support-tickets', 'internal-consultations', 'dialog-consultations', 'support-cases')):
                raise web.HTTPNotFound()
            return web.json_response({'success': True, 'result': {}, 'tasks': [], 'deals': [],
                'contacts': [], 'companies': [], 'pipelines': [], 'members': [], 'integrations': []})
        app.router.add_get('/app', index)
        app.router.add_get('/assets/{name}', asset)
        app.router.add_get('/api/platform/me', profile)
        app.router.add_get('/api/platform/notifications', notification_api.handler(principal, origin, LOGGER))
        app.router.add_post('/api/platform/notifications/{notification_id}/read', notification_api.handler(principal, origin, LOGGER, 'read'))
        app.router.add_get('/api/platform/ai/support-tickets', support_api.handler(principal, origin, LOGGER, 'tickets'))
        app.router.add_get('/api/platform/ai/support-tickets/{ticket_id}', gated_detail)
        app.router.add_get('/api/platform/ai/internal-consultations', support_api.handler(principal, origin, LOGGER, 'consultation'))
        for suffix, resource in (('', 'inbox'), ('/replies', 'inbox'), ('/destinations', 'destinations')):
            app.router.add_get('/api/platform/ai/dialog-consultations' + suffix, consultation_api.handler(principal, origin, LOGGER, resource))
        app.router.add_get('/api/platform/support-cases/{ticket_id}/public-shares', public_api.owner_handler(principal, origin))
        app.router.add_get('/test/notification-detail-state', detail_state)
        app.router.add_get('/test/revoke-support', revoke)
        app.router.add_get('/api/{tail:.*}', unrelated_empty_read)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.SockSite(runner, sock).start()
        state['runner'] = runner
        ready.set()
    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(10)
    try:
        env = {**os.environ, 'NOTIFICATION_CASE_RUNTIME_ORIGIN': origin,
            'NOTIFICATION_CASE_RUNTIME_CASE_ID': f['case_id'],
            'NOTIFICATION_CASE_RUNTIME_NOTIFICATION_ID': ident,
            'NOTIFICATION_CASE_RUNTIME_EVENT_KEY': item['event_key']}
        env.pop('NOTIFICATION_CASE_RUNTIME_REVOKE_ON_DETAIL', None)
        env.pop('NOTIFICATION_CASE_RUNTIME_UNAVAILABLE_NOTIFICATION_ID', None)
        if mode == 'revoked_during_detail':
            env['NOTIFICATION_CASE_RUNTIME_REVOKE_ON_DETAIL'] = '1'
        elif unavailable:
            env['NOTIFICATION_CASE_RUNTIME_UNAVAILABLE_NOTIFICATION_ID'] = unavailable
        completed = subprocess.run(['node', str(root / 'tests/test_notification_case_targets_browser.cjs')],
            cwd=root, env=env, capture_output=True, text=True, timeout=55)
        assert completed.returncode == 0, completed.stdout + '\n' + completed.stderr
        assert not state['blocked'], state['blocked']
        stored = f['db'].execute('SELECT read_at FROM saas_tenant_notifications WHERE id=%s::uuid', (ident,))[0]
        if mode == 'ready':
            assert len(state['posts']) == 1
            assert state['posts'][0] == {'path': '/api/platform/notifications/' + ident + '/read', 'body': {
                'expected_tenant_id': A, 'expected_user_id': 33,
                'opened_case_id': f['case_id'], 'opened_event_key': item['event_key']}}
            assert stored['read_at'] is not None
            assert center.listing({'tenant_id': A, 'telegram_id': 33})['unread_count'] == 1
        else:
            assert state['revoked'] is True and state['posts'] == []
            assert stored['read_at'] is None
        assert _state(f) == before
    finally:
        loop.call_soon_threadsafe(state['release'].set)
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()
        assert not thread.is_alive()


def test_notification_proof_scans_are_bounded_once_per_tenant_for_large_mixed_history(notifications):
    """Actual plan invariant, without a shared-runner wall-clock threshold."""
    f = notifications
    case_id = _new_case(f, conversation='performance-source', lead=43)
    f['db'].execute("""INSERT INTO saas_support_events(tenant_id,event_key,ticket_id)
      SELECT %s::uuid,'handoff:perf-connect:'||i::text,%s::uuid FROM generate_series(1,1000) AS g(i)""", (A, case_id))
    f['db'].execute("INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,name) VALUES(%s::uuid,901,'Synthetic performance Kommo deal')", (A,))
    f['db'].execute("""INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,payload,state)
      SELECT %s::uuid,'perf-kommo:'||i::text,'kommo',
      jsonb_build_object('chat_id','performance-kommo','lead_id',901),'completed'
      FROM generate_series(1,1000) AS g(i)""", (A,))
    f['db'].execute("""INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url)
      SELECT gen_random_uuid(),%s::uuid,11,'ai_handoff:performance-source:perf-connect:'||i::text,
      'Synthetic history','unsupported_evidence','/app?view=deals&deal_id=43'
      FROM generate_series(1,1000) AS g(i)""", (A,))
    f['db'].execute("""INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url)
      SELECT gen_random_uuid(),%s::uuid,11,'ai_handoff:performance-kommo:perf-kommo:'||i::text,
      'Synthetic history','Kommo event','/app?view=deals&deal_id=901'
      FROM generate_series(1,1000) AS g(i)""", (A,))
    f['db'].commit()
    f['db'].execute('ANALYZE saas_tenant_notifications,saas_support_events,saas_tenant_webhook_events')
    f['db'].commit()
    listed = center.listing(PROFILE, limit=20)
    assert listed['unread_count'] == 2000 and len(listed['notifications']) == 20
    assert listed['next_before'] is not None
    with f['factory']() as conn, conn.cursor() as cur:
        tenant, actor, policy = center._access(cur, PROFILE)
        sql, params = center._eligible(cur, tenant, actor, policy)
        projection = sql + 'SELECT target_state,count(*) AS n FROM eligible GROUP BY target_state'
        cur.execute(projection, tuple(params))
        assert {row['target_state']: row['n'] for row in cur.fetchall()} == {'case': 1000, 'legacy': 1000}
        cur.execute('EXPLAIN (ANALYZE,FORMAT JSON) ' + projection, tuple(params))
        plan = cur.fetchone()['QUERY PLAN'][0]['Plan']
    nodes = []
    def visit(node):
        nodes.append(node)
        for child in node.get('Plans', []):
            visit(child)
    visit(plan)
    for relation in ('saas_support_events', 'saas_tenant_webhook_events'):
        scans = [node for node in nodes if node.get('Relation Name') == relation]
        assert scans, relation + ' must be exercised by the actual mixed-history projection'
        assert sum(node['Actual Loops'] for node in scans) == 1, [
            (node['Node Type'], node['Actual Loops'], node['Actual Rows']) for node in scans]
    # Reading each base table once is insufficient if its materialized proof
    # rows are then scanned for every notification. Bound the complete plan's
    # row work generously enough for normal joins/index probes, independently
    # of machine speed, while rejecting a quadratic proof-map consumer.
    work = [(sum(node.get(key, 0) for key in (
        'Actual Rows', 'Rows Removed by Filter', 'Rows Removed by Join Filter'))
        * node.get('Actual Loops', 0), node['Node Type'], node.get('CTE Name'))
        for node in nodes]
    assert sum(item[0] for item in work) < 50 * (2000 + 2000), sorted(work, reverse=True)[:12]
