import ast
import unittest
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


if __name__ == '__main__':
    unittest.main()
