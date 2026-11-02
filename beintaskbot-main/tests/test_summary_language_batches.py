"""LANG02 through actual HTTP batch admission, finite worker and native storage."""
import asyncio
from datetime import timedelta
import json
import logging
import uuid
from unittest.mock import Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, CA, AGENT, PROFILE
from test_summary_language_integration import (
    language_db, _policy, _messages, _candidate_response, AZ, RU, EN, SUMMARY,
)
import tenant_knowledge_batches as batches
import tenant_knowledge_candidates_api as api
import tenant_knowledge_candidates_service as candidates
import tenant_knowledge_candidates_store as store


ORIGIN = 'https://synthetic-summary-batches.example'
LOGGER = logging.getLogger(__name__)


@pytest.fixture
def batch_language(language_db, monkeypatch):
    monkeypatch.setattr(batches, '_connect', language_db['factory'])
    return language_db


def _command(f, client, **extra):
    _messages(f, [client, {'body': RU, 'direction': 'outgoing'}])
    bounds = f['db'].execute('SELECT min(happened_at) AS first,max(happened_at) AS last FROM saas_crm_messages WHERE tenant_id=%s::uuid', (A,))[0]
    return {'request_id': str(uuid.uuid4()), 'provider': 'connect', 'channel_id': CA,
        'conversation_id': 'client', 'analysis_agent_id': AGENT,
        'period_start': (bounds['first'] - timedelta(seconds=1)).isoformat(),
        'period_end': (bounds['last'] + timedelta(seconds=1)).isoformat(),
        'expected_tenant_id': A, 'expected_user_id': 11, **extra}


def _app():
    app = web.Application()
    profile = {**PROFILE, 'locale': 'ru', 'language': 'ru'}
    app.router.add_post('/batches', api.handler(lambda _request: profile, ORIGIN, LOGGER, 'batches'))
    app.router.add_get('/batches/{batch_id}', api.handler(lambda _request: profile, ORIGIN, LOGGER, 'batch'))
    return app


async def _post(client, data):
    response = await client.post('/batches', json=data, headers={'Origin': ORIGIN})
    assert response.headers['Cache-Control'] == 'no-store'
    return response, await response.json()


@pytest.mark.parametrize('fixed,client,code,marker,expected', [
    ('ru', AZ, 'en', True, 'en'), (None, RU, 'tr', True, 'tr'),
    (None, RU, 'az', None, 'ru'), (None, EN, 'az', False, 'en'),
    ('tr', AZ, 'az', None, 'tr'),
])
def test_actual_batch_api_persists_explicit_intent_and_carries_it_through_processing_to_provider(
        batch_language, monkeypatch, fixed, client, code, marker, expected):
    f = batch_language
    _policy(mode='fixed' if fixed else 'automatic', fixed=fixed)
    command = _command(f, client, language=code,
        **({'language_explicit': marker} if marker is not None else {}))

    def generated(_key, _model, mode, rows, _draft, **kwargs):
        assert mode == 'knowledge_extraction'
        assert all(set(row) == {'ref', 'role', 'text'} for row in rows)
        assert kwargs['extra_context']['output_language'] == expected
        assert kwargs['extra_context']['summary_language']['method'] == (
            'explicit' if marker else 'company_fixed' if fixed else 'customer_detected')
        return json.dumps({'cards': [_candidate_response(expected, [row['ref'] for row in rows])]},
                          ensure_ascii=False), {'total_tokens': 25}

    generation = Mock(side_effect=generated)
    monkeypatch.setattr(candidates, 'generate', generation)

    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            response, body = await _post(http, command)
            assert response.status == 202, body
            batch_id = body['result']['batch']['id']
            # Await the actual task spawned by the real POST handler.
            await asyncio.gather(*list(api._batch_tasks))
            response = await http.get('/batches/' + batch_id, params={
                'expected_tenant_id': A, 'expected_user_id': '11'})
            assert response.status == 200
            result = (await response.json())['result']['batch']
            assert result['status'] == 'completed', result
            assert result['params']['language'] == (code if marker else 'auto')
            assert result['params'].get('language_explicit') is (True if marker else None)
            assert len(result['items']) == 1 and result['items'][0]['status'] == 'completed'
            item = result['items'][0]
            assert item['route']['language'] == (code if marker else 'auto')
            assert item['route'].get('language_explicit') is (True if marker else None)
            assert item['extraction_id'] == str(uuid.uuid5(uuid.UUID(batch_id), store.digest(item['route'])))
            generation.assert_called_once()
            before = f['db'].execute('SELECT params,routes,request_key FROM saas_knowledge_batches WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, batch_id))[0]
            for replay in (command, {**command, 'request_id': str(uuid.uuid4())}):
                replay_response, replayed = await _post(http, replay)
                assert replay_response.status == 202 and replayed['result']['batch']['id'] == batch_id
                assert replayed['result']['batch']['items'] == result['items']
                assert not api._batch_tasks
            changed = {**command, 'language': 'tr' if code != 'tr' else 'en', 'language_explicit': True}
            rejected, rejected_body = await _post(http, changed)
            assert rejected.status == 409 and rejected_body['success'] is False
            assert f['db'].execute('SELECT params,routes,request_key FROM saas_knowledge_batches WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, batch_id))[0] == before
            cards = store.listing(PROFILE, {'batch_id': batch_id})['candidates']
            assert len(cards) == 1 and cards[0]['content_language'] == expected
            assert cards[0]['title'] == SUMMARY[expected]
            saved = store.detail(PROFILE, cards[0]['id'])['candidate']
            assert saved['language_selection']['language'] == expected
            assert saved['language_selection']['policy_version'] == 1
            generation.assert_called_once()
    asyncio.run(scenario())


@pytest.mark.parametrize('marker', ['true', 1, None, [], {}])
def test_batch_api_rejects_non_boolean_explicit_marker_before_reserving_any_paid_work(batch_language, monkeypatch, marker):
    f = batch_language
    _policy()
    command = _command(f, RU, language='en', language_explicit=marker)
    generation = Mock(side_effect=AssertionError('Invalid marker must never dispatch a provider call'))
    monkeypatch.setattr(candidates, 'generate', generation)

    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            response, body = await _post(http, command)
            assert response.status == 400 and body['success'] is False
            assert not api._batch_tasks
    asyncio.run(scenario())
    generation.assert_not_called()
    assert not f['db'].execute('SELECT id FROM saas_knowledge_batches WHERE tenant_id=%s::uuid', (A,))
    assert not f['db'].execute('SELECT id FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid', (A,))


@pytest.mark.parametrize('child_state', ['completed', 'unknown'])
def test_pre_feature_implicit_az_batch_keeps_old_key_routes_and_child_uuid_after_policy_change(
        batch_language, monkeypatch, child_state):
    f = batch_language
    command = _command(f, AZ)
    # The prior version persisted implicit AZ rather than an explicit marker.
    # Reproduce that saved task metadata before running its real finite worker.
    # Membership, route authorization, budgets and AI receipts stay actual.
    receipt = batches.start(PROFILE, {key: value for key, value in command.items()
        if key not in {'expected_tenant_id', 'expected_user_id'}})
    batch_id = receipt['id']
    saved = f['db'].execute('SELECT params,routes FROM saas_knowledge_batches WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, batch_id))[0]
    old_params = {**saved['params'], 'language': 'az'}
    old_routes = [{**route, 'language': 'az'} for route in saved['routes']]
    assert 'language_explicit' not in old_params
    assert len(old_routes) == 1 and 'language_explicit' not in old_routes[0]
    old_key = store.digest(old_params)
    old_child_id = str(uuid.uuid5(uuid.UUID(batch_id), store.digest(old_routes[0])))
    f['db'].execute("""UPDATE saas_knowledge_batches SET params=%s::jsonb,routes=%s::jsonb,request_key=%s
      WHERE tenant_id=%s::uuid AND id=%s::uuid""",
      (json.dumps(old_params), json.dumps(old_routes), old_key, A, batch_id))
    f['db'].commit()

    def generated(_key, _model, mode, rows, _draft, **kwargs):
        assert mode == 'knowledge_extraction'
        assert kwargs['extra_context']['output_language'] == 'az'
        if child_state == 'unknown':
            raise TimeoutError('Synthetic pre-feature paid outcome unknown')
        return json.dumps({'cards': [_candidate_response('az', [row['ref'] for row in rows])]},
                          ensure_ascii=False), {'total_tokens': 25}

    generation = Mock(side_effect=generated)
    monkeypatch.setattr(candidates, 'generate', generation)
    batches.process(PROFILE, batch_id)
    original = batches.get(PROFILE, batch_id)['batch']
    assert len(original['items']) == 1
    assert original['items'][0]['extraction_id'] == old_child_id
    assert original['items'][0]['status'] == child_state
    assert original['items'][0]['route'] == old_routes[0]
    generation.assert_called_once()
    _policy(mode='fixed', fixed='ru')

    async def scenario():
        async with TestClient(TestServer(_app())) as http:
            for replay in (command, {**command, 'request_id': str(uuid.uuid4())}):
                response, body = await _post(http, replay)
                assert response.status == 202, body
                restored = body['result']['batch']
                assert restored['id'] == batch_id and restored['params'] == old_params
                assert restored['items'] == original['items']
                assert not api._batch_tasks
                unchanged = f['db'].execute('SELECT params,routes,request_key FROM saas_knowledge_batches WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, batch_id))[0]
                assert unchanged == {'params': old_params, 'routes': old_routes, 'request_key': old_key}
                generation.assert_called_once()
            response, body = await _post(http, {**command, 'language': 'ru', 'language_explicit': True})
            assert response.status == 409 and body['success'] is False
            assert not api._batch_tasks
    asyncio.run(scenario())
    assert f['db'].execute('SELECT count(*) AS n FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid', (A,))[0]['n'] == 1
    generation.assert_called_once()
