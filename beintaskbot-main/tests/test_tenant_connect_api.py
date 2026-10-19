import ast
import asyncio
import json
import unittest
import uuid
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from tenant_linear_policy import TenantLinearError
import tenant_connect_api
from tenant_connect_transport import ConnectWebhookError


ROOT = Path(__file__).resolve().parents[1]


class Api(unittest.TestCase):
    def test_webhook_auth_failure_is_classified_without_logging_payload(self):
        class Request:
            headers = {'x-connect-signature-256': 'sha256=private'}
            async def read(self): return b'{"private":"payload"}'

        logger = mock.MagicMock()
        with mock.patch.object(tenant_connect_api, 'ingest',
                                        side_effect=ConnectWebhookError('invalid_signature', 'rejected', 401)):
            response = asyncio.run(tenant_connect_api.webhook_handler(logger)(Request()))
        self.assertEqual(response.status, 401)
        self.assertEqual(json.loads(response.text)['error'], 'rejected')
        logger.warning.assert_called_once_with('Connect webhook rejected code=%s status=%s', 'invalid_signature', 401)
        self.assertNotIn('private', str(logger.mock_calls))

    def test_webhook_missing_signature_is_rejected_before_ingest(self):
        class Request:
            headers = {}
            async def read(self): return b'{}'

        logger = mock.MagicMock()
        with mock.patch.object(tenant_connect_api, 'ingest') as ingest:
            response = asyncio.run(tenant_connect_api.webhook_handler(logger)(Request()))
        self.assertEqual(response.status, 401)
        self.assertEqual(logger.warning.call_args.args[1], 'missing_signature_header')
        ingest.assert_not_called()

    def test_shared_origin_guard_error_keeps_its_status_and_message(self):
        result_factory = lambda body, **kwargs: {'body': body, **kwargs}
        namespace = {
            'web': SimpleNamespace(Response=object, json_response=result_factory),
            'TenantLinearError': TenantLinearError,
        }
        tree = ast.parse((ROOT / 'tenant_connect_api.py').read_text(encoding='utf-8-sig'))
        tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
        exec(compile(tree, 'tenant_connect_api.py', 'exec'), namespace)

        response = namespace['_policy_error_response'](
            namespace['web'], TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403),
        )

        self.assertEqual(response['status'], 403)
        self.assertEqual(response['body']['error'], 'Sorğunun mənbəyi düzgün deyil.')
        self.assertEqual(response['headers']['Cache-Control'], 'no-store')

    def test_channel_rows_are_json_safe(self):
        tree = ast.parse((ROOT / 'tenant_connect_transport.py').read_text(encoding='utf-8-sig'))
        node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == '_public_channel')
        namespace = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'tenant_connect_transport.py', 'exec'), namespace)
        channel_id = uuid.uuid4()

        result = namespace['_public_channel']({
            'connect_channel_id': channel_id, 'name': 'WhatsApp', 'status': 'connected', 'active': True,
        })

        self.assertEqual(result['connect_channel_id'], str(channel_id))
        self.assertEqual(json.loads(json.dumps(result))['name'], 'WhatsApp')


if __name__ == '__main__':
    unittest.main()
