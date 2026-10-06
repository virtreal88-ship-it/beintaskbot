"""News publishing access must not imply Linear or system approval access."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def load_functions():
    source = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text(encoding='utf-8-sig'))
    names = {'employee_has_permission', '_required_api_permission', 'handle_api_linear_news_review'}
    nodes = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    profile = {'active': True, 'permissions': ['waiting', 'approvals_news']}
    namespace = {
        'employee_access_profile': lambda _: profile,
        'asyncio': asyncio,
        'web': SimpleNamespace(Request=dict, Response=dict, json_response=lambda data, status=200: {'data': data, 'status': status}),
        'list_linear_news': Mock(return_value=[{'title': 'New feature'}]),
        'logger': Mock(),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<news-permissions>', 'exec'), namespace)
    return namespace, profile


class NewsPermissionTests(unittest.TestCase):
    def setUp(self):
        self.ns, self.profile = load_functions()

    def test_news_editor_can_read_without_admin_or_linear_access(self):
        request = SimpleNamespace(method='GET', get=lambda key: 7329891614)
        response = asyncio.run(self.ns['handle_api_linear_news_review'](request))
        self.assertEqual(response['status'], 200)
        self.assertTrue(response['data']['success'])
        self.assertFalse(self.ns['employee_has_permission'](7329891614, 'linear'))
        self.assertFalse(self.ns['employee_has_permission'](7329891614, 'approvals_system'))

    def test_news_endpoint_does_not_use_linear_permission(self):
        self.assertEqual(self.ns['_required_api_permission']('/api/linear/news/review'), 'approvals_news')
        self.assertEqual(self.ns['_required_api_permission']('/api/pending_actions/resolve'), 'approvals_system')

    def test_system_permission_cannot_publish_news(self):
        self.profile['permissions'] = ['waiting', 'approvals_system', 'linear']
        request = SimpleNamespace(method='POST', get=lambda key: 7329891614)
        response = asyncio.run(self.ns['handle_api_linear_news_review'](request))
        self.assertEqual(response['status'], 403)

    def test_disabled_employee_cannot_read_news(self):
        self.profile['active'] = False
        request = SimpleNamespace(method='GET', get=lambda key: 7329891614)
        response = asyncio.run(self.ns['handle_api_linear_news_review'](request))
        self.assertEqual(response['status'], 403)

    def test_closed_parent_page_blocks_news(self):
        self.profile['permissions'] = ['approvals_news']
        self.assertFalse(self.ns['employee_has_permission'](7329891614, 'approvals_news'))

    def test_huseyn_grant_is_persisted_once_and_revocation_wins(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text(encoding='utf-8-sig'))
        node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'employee_access_profile')
        records = {'7329891614': {'role': 'employee', 'active': True, 'permissions': ['tasks'], 'linear_create_permission_version': 1}}
        save = Mock(side_effect=lambda updated: records.update(updated))
        ns = {
            'ADMIN_CHAT_ID': 1628569350, 'HUSEYN_CHAT_ID': 7329891614, 'RUFAT_CHAT_ID': 6824377548,
            '_RETIRED_EMPLOYEE_CHAT_IDS': set(), '_load_employee_access_records': lambda: records,
            '_save_employee_access_records': save,
            '_normalize_employee_permissions': lambda values, role: list(values),
            '_normalize_hot_order_skills': lambda values: [],
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<profile>', 'exec'), ns)
        granted = ns['employee_access_profile'](7329891614)
        self.assertIn('approvals_news', granted['permissions'])
        self.assertNotIn('approvals_system', granted['permissions'])
        self.assertEqual(records['7329891614']['approval_permissions_version'], 1)
        records['7329891614']['permissions'] = ['tasks']
        revoked = ns['employee_access_profile'](7329891614)
        self.assertNotIn('approvals_news', revoked['permissions'])
        save.assert_called_once()


if __name__ == '__main__':
    unittest.main()
