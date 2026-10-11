"""Pure transport checks, without external calls or credentials."""
from types import SimpleNamespace
import unittest
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post


class OriginGuardTests(unittest.TestCase):
    def request(self, *, method='POST', content_type='application/json', origin='https://crm.pro.az'):
        return SimpleNamespace(method=method, content_type=content_type,
                               headers={} if origin is None else {'Origin': origin})

    def test_matching_origin_and_existing_trailing_slash_behavior(self):
        for origin in ('https://crm.pro.az', 'https://crm.pro.az/'):
            for canonical in ('https://crm.pro.az', 'https://crm.pro.az/'):
                self.assertIsNone(require_json_post(self.request(origin=origin), canonical))

    def test_wrong_methods_and_content_types(self):
        for values in ({'method': 'GET'}, {'method': 'PUT'}, {'method': 'post'},
                       {'content_type': 'text/plain'}, {'content_type': 'multipart/form-data'}):
            with self.subTest(values=values):
                with self.assertRaises(TenantLinearError) as caught:
                    require_json_post(self.request(**values), 'https://crm.pro.az')
                self.assertEqual(caught.exception.status, 403)

    def test_missing_null_foreign_and_lookalike_origins(self):
        for origin in (None, '', 'null', 'http://crm.pro.az', 'https://crm.pro.az.evil',
                       'https://evil.crm.pro.az', 'https://crm.pro.az@evil.invalid',
                       'https://crm.pro.az:444', 'https://crm.pro.az/path', ' https://crm.pro.az'):
            with self.subTest(origin=origin):
                with self.assertRaises(TenantLinearError) as caught:
                    require_json_post(self.request(origin=origin), 'https://crm.pro.az')
                self.assertEqual(caught.exception.status, 403)
                self.assertEqual(str(caught.exception), 'Sorğunun mənbəyi düzgün deyil.')

    def test_import_error_text_can_be_preserved(self):
        with self.assertRaises(TenantLinearError) as caught:
            require_json_post(self.request(origin=None), 'https://crm.pro.az', message='İcazə yoxdur.')
        self.assertEqual(str(caught.exception), 'İcazə yoxdur.')
