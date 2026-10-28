"""Read-only synthetic reproduction of the Knowledge Center's candidate request."""
import asyncio
import json
from unittest.mock import Mock, patch
from aiohttp.test_utils import make_mocked_request
import tenant_knowledge_candidates_api as api

PROFILE = {'tenant_id': '11111111-1111-4111-8111-111111111111', 'telegram_id': 11, 'role': 'owner'}
QUERY = '?expected_tenant_id=' + PROFILE['tenant_id'] + '&expected_user_id=11&limit=50&status=all'


def request():
    return make_mocked_request('GET', '/api/platform/ai/knowledge-candidates' + QUERY)


def test_knowledge_center_candidate_query_reaches_listing_without_validation_error():
    handler = api.handler(lambda _: PROFILE, 'https://fixture.test', Mock())
    with patch.object(api, 'listing', return_value={'candidates': [], 'next_before': None}) as listing:
        response = asyncio.run(handler(request()))
    assert response.status == 200
    assert json.loads(response.text)['success'] is True
    assert listing.call_args.args == (PROFILE, {
        'expected_tenant_id': PROFILE['tenant_id'], 'expected_user_id': '11', 'limit': '50', 'status': 'all',
    })


def test_generic_parameter_error_does_not_prove_a_bad_browser_query():
    handler = api.handler(lambda _: PROFILE, 'https://fixture.test', Mock())
    # Reproduce the existing exception mapping only. This is not a claim about
    # which exception or stored record caused the observed production banner.
    with patch.object(api, 'listing', side_effect=ValueError('synthetic stored value')):
        response = asyncio.run(handler(request()))
    assert response.status == 400
    assert json.loads(response.text) == {'success': False, 'error': 'Некорректные параметры.'}


def test_exact_query_passes_store_status_and_limit_validation():
    from unittest.mock import MagicMock
    import tenant_knowledge_candidates_store as store
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    handler = api.handler(lambda _: PROFILE, 'https://fixture.test', Mock())
    # Only the database/auth fixture is replaced; handler and listing validation
    # execute together with the browser's exact query, without any database writes.
    with patch.object(store, '_connect', return_value=connection), \
         patch.object(store, 'schema'), \
         patch.object(store, 'authorize', return_value=(PROFILE['tenant_id'], None, None)):
        response = asyncio.run(handler(request()))
    assert response.status == 200
    assert json.loads(response.text)['result'] == {'candidates': [], 'limit': 50, 'next_before': None}
    sql, args = cursor.execute.call_args.args
    assert 'c.tenant_id=%s::uuid' in sql
    assert 'c.status=' not in sql
    assert args == (PROFILE['tenant_id'], 51)
