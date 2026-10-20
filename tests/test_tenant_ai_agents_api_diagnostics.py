import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
    def test_multipart_knowledge_upload_keeps_the_bound_agent_and_retry_key(self):
        profile = {'tenant_id': '00000000-0000-0000-0000-000000000101', 'telegram_id': 42}
        captured = Mock(return_value={'id': 'source-a'})

        class Part:
            def __init__(self, name, value, filename=''):
                self.name, self.filename, self.chunks = name, filename, [value, b'']

            async def read_chunk(self, _size):
                return self.chunks.pop(0)

        class Reader:
            def __init__(self, parts):
                self.parts = iter(parts)

            async def next(self):
                return next(self.parts, None)

        class Request:
            method = 'POST'
            content_type = 'multipart/form-data; boundary=test'
            content_length = 1024
            headers = {'Origin': 'https://crm.pro.az'}
            query = {}
            match_info = {}

            async def multipart(self):
                return Reader([
                    Part('file', b'Router instructions', 'BeinSystems.txt'),
                    Part('title', b'BeinSystems'),
                    Part('agent_id', b'00000000-0000-0000-0000-000000000103'),
                    Part('setup_key', b'night-wizard-retry-0001-kb-file'),
                    Part('expected_tenant_id', profile['tenant_id'].encode()),
                    Part('expected_user_id', str(profile['telegram_id']).encode()),
                ])

        handler, _ = load_handler(
            _origin=lambda *_args: None,
            _expected=lambda *_args: None,
            extract=Mock(return_value=('BeinSystems.txt', 'txt', 'Router instructions')),
            save_knowledge=captured,
        )
        response = run_without_event_loop(handler(Mock(return_value=profile), 'https://crm.pro.az', Mock(), 'knowledge_upload')(Request()))
        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertTrue(payload['success'])
        submitted = captured.call_args.args[1]
        self.assertEqual(submitted['agent_id'], '00000000-0000-0000-0000-000000000103')
        self.assertEqual(submitted['setup_key'], 'night-wizard-retry-0001-kb-file')

    def test_invalid_wizard_request_returns_a_field_key_instead_of_a_generic_azerbaijani_error(self):
        profile = {'tenant_id': 'tenant-from-verified-session', 'telegram_id': 42}

        class Request:
            method = 'POST'
            content_type = 'application/json'
            content_length = 128
            headers = {'Origin': 'https://crm.pro.az'}
            query = {}
            match_info = {}

            async def json(self):
                return {'expected_tenant_id': profile['tenant_id'], 'expected_user_id': profile['telegram_id']}

        handler, namespace = load_handler(
            _origin=lambda *_args: None,
            _json_body=lambda _request: True,
            _expected=lambda *_args: None,
            save_agent=Mock(side_effect=ValueError('database value failed')),
        )
        response = run_without_event_loop(handler(Mock(return_value=profile), 'https://crm.pro.az', Mock(), 'agents')(Request()))
        payload = json.loads(response.text)
        self.assertEqual(response.status, 400)
        self.assertEqual(payload['field'], 'configuration')
        self.assertEqual(payload['code'], 'invalid_configuration')
        self.assertNotEqual(payload['error'], 'Sorğu düzgün deyil.')
        namespace['save_agent'].assert_called_once()

    def test_model_validation_identifies_the_model_field(self):
        profile = {'tenant_id': 'tenant-from-verified-session', 'telegram_id': 42}

        class Request:
            method = 'POST'
            content_type = 'application/json'
            content_length = 128
            headers = {'Origin': 'https://crm.pro.az'}
            query = {}
            match_info = {}

            async def json(self):
                return {'expected_tenant_id': profile['tenant_id'], 'expected_user_id': profile['telegram_id']}

        handler, _ = load_handler(
            _origin=lambda *_args: None,
            _json_body=lambda _request: True,
            _expected=lambda *_args: None,
            save_agent=Mock(side_effect=TenantLinearError('Model yalnız aktiv mövcud Chat AI provayderindən seçilə bilər.', 409)),
        )
        response = run_without_event_loop(handler(Mock(return_value=profile), 'https://crm.pro.az', Mock(), 'agents')(Request()))
        payload = json.loads(response.text)
        self.assertEqual(response.status, 409)
        self.assertEqual(payload['field'], 'model')
        self.assertEqual(payload['code'], 'invalid_model')

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


    def test_pilot_preflight_is_get_only_and_receives_verified_tenant_profile(self):
        handler, _ = load_handler()
        profile = {'tenant_id': 'tenant-from-verified-session', 'telegram_id': 42}
        member = Mock(return_value=profile)
        with patch('tenant_ai_pilot.preflight', return_value={'ready':False,'blockers':['competing_handler_not_confirmed_disabled']}) as preflight:
            request = SimpleNamespace(method='GET', query={})
            response = run_without_event_loop(handler(member, 'https://crm.pro.az', Mock(), 'pilot_preflight')(request))
        self.assertEqual(response.status, 200)
        self.assertFalse(json.loads(response.text)['result']['ready'])
        preflight.assert_called_once_with(profile)
        member.assert_called_once_with(request)

    def test_cached_options_never_contacts_kommo(self):
        catalog = Mock(return_value={'connect_channels': [{'id': 'tenant-channel'}]})
        handler, _ = load_handler(configuration_options=catalog)
        profile = {'tenant_id': 'tenant-from-verified-session', 'telegram_id': 42}
        remote = Mock(side_effect=AssertionError('Connect must not require Kommo'))
        response = run_without_event_loop(handler(Mock(return_value=profile), 'https://crm.pro.az',
            Mock(), 'options', request_provider=remote)(SimpleNamespace(method='GET', query={'cached': '1'})))
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)['result']['connect_channels'][0]['id'], 'tenant-channel')
        catalog.assert_called_once_with(profile)
        remote.assert_not_called()

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
