import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from aiohttp import web
from tenant_linear_policy import TenantLinearError


ROOT = Path(__file__).resolve().parents[1]


class InlineAsyncio:
    @staticmethod
    async def to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)


def load_handler(**overrides):
    source = (ROOT / 'tenant_ai_agents_api.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    node = next(item for item in tree.body
                if isinstance(item, ast.FunctionDef) and item.name == 'handler')
    namespace = {
        # This handler's thread offload is an implementation detail; running
        # the callable inline keeps the dispatch test deterministic on Windows.
        'asyncio': InlineAsyncio,
        'web': web,
        'TenantLinearError': TenantLinearError,
        'list_connect_diagnostics': Mock(return_value={
            'system': {'tenant_id': 'tenant-from-verified-session'},
            'server_flags': {'ai_agents_enabled': True, 'auto_send_enabled': True},
            'runtime': {'service_id': 'production-worker', 'deployment_id': 'deployment-a'},
            'channels': [], 'agents': [], 'events': [], 'latest_event_found': False,
        }),
        'list_knowledge': Mock(return_value=[]),
        **overrides,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'tenant_ai_agents_api.py'), 'exec'), namespace)
    return namespace['handler'], namespace


def run_without_event_loop(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError('test coroutine unexpectedly yielded to an event loop')


class TenantAiAgentApiDiagnosticsTests(unittest.TestCase):
    def test_get_diagnostics_dispatches_to_live_diagnostic_reader_with_verified_session(self):
        handler, namespace = load_handler()
        profile = {'tenant_id': 'tenant-from-verified-session', 'telegram_id': 42}
        member = Mock(return_value=profile)
        request = SimpleNamespace(method='GET', query={'limit': '10', 'tenant_id': 'client-controlled-tenant'})

        response = run_without_event_loop(handler(member, 'https://crm.pro.az', Mock(), 'diagnostics')(request))

        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertTrue(payload['success'])
        self.assertEqual(payload['result']['system']['tenant_id'], profile['tenant_id'])
        self.assertTrue(payload['result']['server_flags']['ai_agents_enabled'])
        namespace['list_connect_diagnostics'].assert_called_once_with(profile, '10')
        namespace['list_knowledge'].assert_not_called()
        member.assert_called_once_with(request)

    def test_missing_verified_session_is_an_authorization_error_not_empty_state(self):
        handler, namespace = load_handler()
        member = Mock(return_value=None)
        request = SimpleNamespace(method='GET', query={'limit': '10'})

        response = run_without_event_loop(handler(member, 'https://crm.pro.az', Mock(), 'diagnostics')(request))

        self.assertEqual(response.status, 403)
        payload = json.loads(response.text)
        self.assertFalse(payload['success'])
        self.assertNotIn('result', payload)
        namespace['list_connect_diagnostics'].assert_not_called()
        namespace['list_knowledge'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
