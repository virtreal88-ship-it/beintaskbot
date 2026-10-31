"""HTTP serialization and access contract for internal consultation lists."""
import asyncio
from datetime import datetime, timezone
import logging
import uuid

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tenant_support_inbox_api import handler


PROFILE = {'tenant_id': str(uuid.UUID(int=101)), 'telegram_id': 11}


def test_consultation_list_serializes_postgres_uuid_and_datetime_and_keeps_group_rows(monkeypatch):
    consultation_id = uuid.uuid4()
    case_id = uuid.uuid4()
    created_at = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    row = {
        'id': consultation_id,
        'case_id': case_id,
        'reason': 'Need to inspect the attached case photo',
        'evidence': {'source': 'case-bound'},
        'status': 'waiting_specialist',
        'delivery_state': 'pilot_group_pending',
        'created_at': created_at,
        'answered_at': None,
        'response': '',
        'response_kind': '',
        'recipient_id': None,
        'recipient_name': None,
    }
    monkeypatch.setattr('tenant_internal_consultations.listing', lambda *_: {'consultations': [row]})

    async def scenario():
        app = web.Application()
        app.router.add_get('/internal-consultations', handler(
            lambda _request: PROFILE, 'https://crm.test', logging.getLogger(__name__), 'consultation'))
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/internal-consultations?case_id=' + str(case_id))
            assert response.status == 200
            payload = await response.json()
            result = payload['result']['consultations']
            assert result == [{
                **row,
                'id': str(consultation_id),
                'case_id': str(case_id),
                'created_at': created_at.isoformat(),
            }]
            # Group-target consultations intentionally have no personal recipient.
            assert result[0]['recipient_id'] is None
            assert result[0]['recipient_name'] is None
            assert not ({'tenant_id', 'conversation_id', 'request_key', 'secrets'} & set(result[0]))
    asyncio.run(scenario())


def test_consultation_list_empty_result_and_permission_denial(monkeypatch):
    calls = []
    monkeypatch.setattr('tenant_internal_consultations.listing',
                        lambda *_: calls.append('listed') or {'consultations': []})

    async def scenario():
        app = web.Application()
        current = {'profile': PROFILE}
        app.router.add_get('/internal-consultations', handler(
            lambda _request: current['profile'], 'https://crm.test', logging.getLogger(__name__), 'consultation'))
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/internal-consultations?case_id=' + str(uuid.uuid4()))
            assert response.status == 200
            assert (await response.json())['result']['consultations'] == []
            current['profile'] = None
            response = await client.get('/internal-consultations?case_id=' + str(uuid.uuid4()))
            assert response.status == 403
            assert calls == ['listed']
    asyncio.run(scenario())


def test_support_api_serializer_rejects_unapproved_object_types(monkeypatch):
    monkeypatch.setattr('tenant_internal_consultations.listing',
                        lambda *_: {'consultations': [{'unexpected': object()}]})

    async def scenario():
        app = web.Application()
        app.router.add_get('/internal-consultations', handler(
            lambda _request: PROFILE, 'https://crm.test', logging.getLogger(__name__), 'consultation'))
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/internal-consultations?case_id=' + str(uuid.uuid4()))
            assert response.status == 400
    asyncio.run(scenario())
