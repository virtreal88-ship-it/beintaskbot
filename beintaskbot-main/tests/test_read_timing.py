"""Controlled timing and API fixtures; no database or external API access."""
import asyncio
import json
from unittest.mock import Mock

import pytest
from aiohttp.test_utils import make_mocked_request

import tenant_read_timing as timing
import tenant_operator_api as operator
import tenant_knowledge_candidates_api as cases


@pytest.fixture(autouse=True)
def clear_rate_limit():
    timing._last_logged.clear()
    yield
    timing._last_logged.clear()


def test_numeric_stage_boundaries(monkeypatch):
    ticks = iter([0, 1, 3, 4, 7, 12, 15, 20])
    monkeypatch.setattr(timing.time, 'perf_counter', lambda: next(ticks))

    async def inline(function):
        return function()

    monkeypatch.setattr(timing.asyncio, 'to_thread', inline)
    logger = Mock()
    measured = timing.ReadTiming(1, logger)
    assert measured.authenticate(lambda request: request, 'secret-cookie') == 'secret-cookie'
    assert asyncio.run(measured.run(lambda: 'private-body')) == 'private-body'
    measured.finish()
    assert logger.info.call_args.args[1:] == (1, 1, 2000, 3000, 5000, 3000, 20000)
    assert 'secret' not in repr(logger.info.call_args)
    assert 'private' not in repr(logger.info.call_args)


def test_log_volume_is_bounded_per_resource_and_process(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(timing.time, 'perf_counter', lambda: now[0])
    logger = Mock()
    for _ in range(100):
        for resource in (1, 2, 3):
            timing.ReadTiming(resource, logger).finish()
    assert logger.info.call_count == 3
    now[0] += 59.9
    timing.ReadTiming(1, logger).finish()
    assert logger.info.call_count == 3
    now[0] += .1
    timing.ReadTiming(1, logger).finish()
    assert logger.info.call_count == 4
    with pytest.raises(ValueError):
        timing.ReadTiming('private-path', logger)


def test_exception_propagates_without_logging_sensitive_exception():
    logger = Mock()
    measured = timing.ReadTiming(2, logger)

    def fail():
        raise ValueError('private request payload')

    with pytest.raises(ValueError, match='private request payload'):
        asyncio.run(measured.run(fail))
    measured.finish()
    args = logger.info.call_args.args
    assert args[2] == 0 and args[4] >= 0 and args[5] >= 0
    assert all(isinstance(value, (int, float)) for value in args[1:])
    assert 'private request payload' not in repr(args)


@pytest.mark.parametrize('resource,function', [('dialogs', 'listing'), ('history', 'history'), ('conversation', 'conversation_link')])
def test_handlers_preserve_auth_and_only_log_numbers(monkeypatch, resource, function):
    module = cases if resource == 'conversation' else operator
    logger = Mock()
    profile = {'tenant_id': 'private-tenant', 'telegram_id': 44, 'role': 'owner'}
    authenticate = Mock(side_effect=[profile, None])
    read = Mock(return_value={'sensitive': 'private-message'})
    monkeypatch.setattr(module, function, read)
    handle = module.handler(authenticate, 'https://fixture.test', logger, resource)
    headers = {'Cookie': 'private-cookie'}
    request = make_mocked_request('GET', '/read?lead_id=73&conversation_id=private-dialog', headers=headers)
    if resource == 'conversation':
        request = make_mocked_request('GET', '/read?expected_tenant_id=private-tenant&expected_user_id=44', headers=headers, match_info={'candidate_id': 'private-case'})
    response = asyncio.run(handle(request))
    assert response.status == 200
    assert json.loads(response.text)['result'] == {'sensitive': 'private-message'}
    denied = asyncio.run(handle(request))
    assert denied.status == 403
    assert authenticate.call_count == 2 and read.call_count == 1
    assert logger.info.call_count == 1
    arguments = logger.info.call_args.args
    assert all(isinstance(value, (int, float)) for value in arguments[1:])
    assert 'private' not in repr(arguments)


def test_auth_failure_records_duration_and_does_not_run_read(monkeypatch):
    logger = Mock()
    read = Mock()
    monkeypatch.setattr(operator, 'listing', read)
    handle = operator.handler(lambda _: None, 'https://fixture.test', logger, 'dialogs')
    assert asyncio.run(handle(make_mocked_request('GET', '/read'))).status == 403
    read.assert_not_called()
    args = logger.info.call_args.args
    assert args[2] == 0 and args[3] >= 0 and args[4:7] == (-1, -1, -1)


def test_logger_failure_does_not_change_success(monkeypatch):
    logger = Mock()
    logger.info.side_effect = RuntimeError('log sink unavailable')
    monkeypatch.setattr(operator, 'listing', lambda *args: {'dialogs': []})
    handle = operator.handler(lambda _: {'tenant_id': 'tenant', 'telegram_id': 1}, '', logger, 'dialogs')
    assert asyncio.run(handle(make_mocked_request('GET', '/read'))).status == 200
