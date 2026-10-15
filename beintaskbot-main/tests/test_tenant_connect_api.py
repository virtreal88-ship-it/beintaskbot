import ast
import json
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from tenant_linear_policy import TenantLinearError


ROOT = Path(__file__).resolve().parents[1]


class Api(unittest.TestCase):
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
