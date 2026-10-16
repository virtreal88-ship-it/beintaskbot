import json
import logging
import os
import unittest
from unittest.mock import Mock, patch

from tenant_ai_openai_diagnostics import _probe_key, run_probe


TENANT = 'e09a5c24-093f-4141-992d-532052e00c90'


class Response:
    def __init__(self, status, body=None):
        self.status_code = status
        self.body = body or {}
    def json(self):
        return self.body


class ProbeTests(unittest.TestCase):
    def test_probe_only_returns_safe_status_and_never_response_text(self):
        get = Mock(return_value=Response(200, {'data': [{'id': 'gpt-test'}]}))
        post = Mock(return_value=Response(403, {'error': {'code': 'model_not_found', 'message': 'private'}}))
        result = _probe_key('secret-value', 'gpt-test', get=get, post=post)
        self.assertEqual(result['model_listed'], True)
        self.assertEqual(result['generation_category'], 'model_access')
        self.assertNotIn('secret-value', repr(result))
        self.assertNotIn('private', repr(result))

    def test_probe_refuses_any_tenant_other_than_explicit_env_allowlist(self):
        with patch.dict(os.environ, {'AI_AGENTS_OPENAI_PROBE_TENANT_ID': 'another-tenant'}):
            with self.assertRaises(ValueError):
                run_probe(TENANT, logging.getLogger('test'), connect_fn=Mock())

    def test_run_probe_uses_exact_tenant_and_logs_no_credentials_or_draft(self):
        secret = {'tenant_id': TENANT, 'provider': 'chat_ai', 'api_key': 'company-secret'}
        row = {'id': 'agent-id', 'name': 'Nizam Ai', 'model': 'gpt-current', 'enabled': True,
               'mode': 'automatic', 'metadata': {'model': 'gpt-current'},
               'secrets': b'encrypted'}
        cur = Mock()
        cur.fetchone.return_value = row
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        conn.cursor.return_value.__enter__ = Mock(return_value=cur)
        conn.cursor.return_value.__exit__ = Mock(return_value=False)
        connect_fn = Mock(return_value=conn)
        cipher = Mock()
        cipher.decrypt.return_value = json.dumps(secret).encode()
        logger = Mock()
        get = Mock(return_value=Response(200, {'data': [{'id': 'gpt-current'}]}))
        post = Mock(return_value=Response(200, {'status': 'completed'}))
        env = {'AI_AGENTS_OPENAI_PROBE_TENANT_ID': TENANT, 'OPENAI_API_KEY': 'platform-secret',
               'OPENAI_HISTORY_REPLY_MODEL': 'gpt-reference'}
        with patch.dict(os.environ, env, clear=False):
            run_probe(TENANT, logger, connect_fn=connect_fn, fernet_fn=Mock(return_value=cipher), get=get, post=post)
        cur.execute.assert_called_once()
        self.assertEqual(cur.execute.call_args.args[1], (TENANT, 'Nizam Ai'))
        rendered = str(logger.info.call_args)
        self.assertNotIn('company-secret', rendered)
        self.assertNotIn('platform-secret', rendered)
        self.assertNotIn('Return only', rendered)
        self.assertIn('gpt-current', rendered)
        self.assertEqual(post.call_count, 3)


if __name__ == '__main__':
    unittest.main()
