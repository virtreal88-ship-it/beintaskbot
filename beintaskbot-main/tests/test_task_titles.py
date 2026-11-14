import asyncio
from unittest.mock import AsyncMock
import pytest
from tenant_task_titles import TaskTypeTitles, task_title_fields


def test_raw_text():
    row = task_title_fields({'text': 'Связаться', 'raw': {'text': '', 'task_type_id': 4229224}})
    assert row['text'] == '' and row['task_type_id'] == '4229224'
    assert row['display_title'] == 'Tapşırıq'
    row = task_title_fields({'raw': {'text': ' Call client ', 'task_type_id': 'bad'}})
    assert row['text'] == ' Call client ' and row['display_title'] == 'Tapşırıq'
    assert row['task_type_id'] is None
    assert task_title_fields({'text': 'Legacy task'})['display_title'] == 'Tapşırıq'


def catalog(name):
    return {'_embedded': {'task_types': [{'id': 4229224, 'name': name}]}}


@pytest.mark.asyncio
async def test_title_shapes_and_single_fetch():
    request = AsyncMock(return_value=catalog('Cavab gözlənilir'))
    service = TaskTypeTitles(request, identity=lambda _: 'connection1')
    rows = [task_title_fields({'raw': {'text': '', 'task_type_id': 4229224}}) for _ in range(3)]
    result = {'source': 'kommo', 'tasks': [rows[0]], 'task': rows[1], 'command': {'task': rows[2]}}
    await service.enrich('tenant1', result)
    assert all(r['display_title'] == 'Cavab gözlənilir' and r['text'] == '' for r in rows)
    request.assert_awaited_once_with('tenant1', 'GET', 'account', params={'with': 'task_types'})
    await service.enrich('tenant1', result)
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_tenant_connection_isolation_and_rename():
    clock, generation = [0], ['a']
    request = AsyncMock(side_effect=[catalog('A'), catalog('B'), catalog('C'), catalog('Renamed')])
    service = TaskTypeTitles(request, identity=lambda _: generation[0], clock=lambda: clock[0])
    assert (await service.names('tenantA'))['4229224'] == 'A'
    assert (await service.names('tenantB'))['4229224'] == 'B'
    generation[0] = 'new'
    assert (await service.names('tenantA'))['4229224'] == 'C'
    clock[0] = 301
    assert (await service.names('tenantA'))['4229224'] == 'Renamed'


@pytest.mark.asyncio
async def test_failure_and_native():
    request = AsyncMock(side_effect=RuntimeError('provider unavailable'))
    service = TaskTypeTitles(request, identity=lambda _: 'a')
    row = {'text': '', 'task_type_id': '4229224'}
    await service.enrich('t', {'source': 'kommo', 'task': row})
    assert row['display_title'] == 'Tapşırıq' and row['task_type_name'] == ''
    await service.enrich('t', {'source': 'kommo', 'task': row})
    await service.enrich('t', {'source': 'native', 'task': {'text': 'Native'}})
    await service.enrich('t', {'source': 'kommo', 'tasks': []})
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_connection_race():
    generation = ['a']
    async def request(*args, **kwargs):
        generation[0] = 'b'
        return catalog('Wrong connection')
    service = TaskTypeTitles(request, identity=lambda _: generation[0])
    assert await service.names('t') == {} and not service.cache


@pytest.mark.asyncio
async def test_bounded_cache_and_concurrent_single_fetch():
    request = AsyncMock(return_value=catalog('Name'))
    service = TaskTypeTitles(request, identity=lambda _: 'a')
    await asyncio.gather(*(service.names('same') for _ in range(10)))
    assert request.await_count == 1
    for i in range(130):
        await service.names(str(i))
    assert len(service.cache) == 128


@pytest.mark.asyncio
async def test_unknown_id_and_type_separate_from_description():
    request = AsyncMock(return_value=catalog('Named type'))
    service = TaskTypeTitles(request, identity=lambda _: 'a')
    rows = [{'task_type_id': '999', 'text': ''}, {'task_type_id': '4229224', 'text': 'Actual instruction'}]
    await service.enrich('t', {'source': 'kommo', 'tasks': rows})
    assert rows[0]['display_title'] == 'Tapşırıq'
    assert rows[1]['display_title'] == 'Named type'
    assert rows[1]['text'] == 'Actual instruction'
    assert rows[1]['task_type_name'] == 'Named type'


@pytest.mark.asyncio
async def test_read_denial_precedes_catalog_request(monkeypatch):
    import tenant_task_api as api
    from test_tenant_task_api import TenantTaskApiTests, Request, body
    fixture = TenantTaskApiTests()
    fixture.setUp()
    def denied(*args, **kwargs):
        raise api.TaskFlowError('task_forbidden', 403)
    monkeypatch.setattr(api, 'flow_query', lambda *a, **k: {'flow': '1', 'source': 'kommo'})
    monkeypatch.setattr(api, 'read_flow', denied)
    response = await fixture.api['tasks'](Request(query={'flow': '1'}))
    assert response.status == 403
    fixture.crm_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_reads_never_fetch_catalog():
    request = AsyncMock()
    service = TaskTypeTitles(request, identity=lambda _: 'a')
    row = {'text': 'Original task', 'task_type_id': '1'}
    await service.enrich('t', {'source': 'kommo', 'command': {'task': row}})
    assert row['display_title'] == 'Tapşırıq'
    assert row['text'] == 'Original task'
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_slow_tenant_does_not_block_other_tenants():
    entered, release = asyncio.Event(), asyncio.Event()
    async def request(tenant, *args, **kwargs):
        if tenant == 'slow':
            entered.set()
            await release.wait()
        return catalog(tenant)
    service = TaskTypeTitles(request, identity=lambda _: 'a')
    slow = asyncio.create_task(service.names('slow'))
    await entered.wait()
    assert (await asyncio.wait_for(service.names('fast'), 1))['4229224'] == 'fast'
    release.set()
    assert (await slow)['4229224'] == 'slow'


@pytest.mark.asyncio
async def test_list_and_detail_resolve_type_even_with_nonempty_description():
    request = AsyncMock(return_value=catalog('Cavab gözlənilir'))
    service = TaskTypeTitles(request, identity=lambda _: 'a')
    row = {'text': 'Связаться', 'task_type_id': '4229224'}
    await service.enrich('t', {'source': 'kommo', 'task': row})
    assert row['display_title'] == 'Cavab gözlənilir'
    assert row['text'] == 'Связаться'
    await service.enrich('t', {'source': 'kommo', 'tasks': [row]})
    assert request.await_count == 1
