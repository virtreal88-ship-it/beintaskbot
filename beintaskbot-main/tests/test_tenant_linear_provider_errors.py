"""Safe provider diagnostics. Synthetic responses only, no real Linear writes."""
import unittest
from unittest.mock import Mock, patch
import requests
import tenant_linear_provider as provider
from tenant_linear_policy import TenantLinearError


class ProviderErrorTests(unittest.TestCase):
    def request(self, status=200, payload=None, document='mutation { issueUpdate(id:$id,input:$input) { success } }'):
        response = Mock(status_code=status)
        response.json.return_value = payload
        if status >= 400:
            response.raise_for_status.side_effect = requests.HTTPError('private response secret')
        with patch.object(provider.requests, 'post', return_value=response) as post:
            with self.assertLogs(provider._LOG, level='WARNING') as logs:
                with self.assertRaises(TenantLinearError) as caught:
                    provider.query('secret-key', document, {'id':'private-issue', 'input':{'title':'private-customer'}})
        self.assertEqual(post.call_count, 1)  # never automatically retry a write
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'secret-key')
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(caught.exception.status, 502)
        output = str(caught.exception) + str(logs.output)
        for secret in ('secret-key', 'private-issue', 'private-customer', 'private response', 'sensitive-message'):
            self.assertNotIn(secret, output)
        return str(caught.exception), ' '.join(logs.output)

    def test_denied_write_is_not_invalid_key(self):
        message, log = self.request(403, {'errors':[{'message':'sensitive-message'}]})
        self.assertIn('yazma icazəsini', message)
        self.assertIn('operation=issueUpdate kind=permission http_status=403', log)

    def test_graphql_permission_even_on_http_200(self):
        message, log = self.request(payload={'data':{}, 'errors':[{'extensions':{'code':'FORBIDDEN'}, 'message':'sensitive-message'}]})
        self.assertIn('yazma icazəsini', message)
        self.assertIn('codes=FORBIDDEN', log)

    def test_read_denial_does_not_suggest_write_permission(self):
        message, log = self.request(403, {}, 'query { viewer { id } }')
        self.assertNotIn('yazma', message)
        self.assertIn('operation=viewer', log)

    def test_auth_and_rate_limit_are_distinct(self):
        for status, kind, text in ((401,'authentication','autentifikasiyası'),(429,'rate_limit','limiti')):
            message, log = self.request(status, {})
            self.assertIn(text, message); self.assertIn('kind='+kind, log)

    def test_unknown_provider_codes_cannot_leak(self):
        message, log = self.request(payload={'data':{},'errors':[{'extensions':{'code':'sensitive-message'},'message':'sensitive-message'}]})
        self.assertIn('kind=graphql', log); self.assertIn('codes=none', log)
        self.assertNotIn('Açarı', message)

    def test_validation_failure_not_misreported_as_auth(self):
        message, log = self.request(400, {'errors':[{'extensions':{'code':'GRAPHQL_VALIDATION_FAILED'}}]})
        self.assertIn('codes=GRAPHQL_VALIDATION_FAILED', log)
        self.assertNotIn('açar', message.lower())

    def test_server_redirect_invalid_and_malformed_errors_fail_closed(self):
        for status, payload in ((500,{}),(302,{}),(200,None),(200,{'data':{}} | {'errors':'sensitive-message'}),
                                (200,{'data':{},'errors':[{'extensions':{'code':['sensitive-message']}}]})):
            self.request(status, payload)

    def test_network_failure_no_raw_exception_no_retry(self):
        with patch.object(provider.requests,'post',side_effect=requests.Timeout('secret-key')) as post:
            with self.assertLogs(provider._LOG, level='WARNING') as logs:
                with self.assertRaises(TenantLinearError) as caught:
                    provider.query('secret-key','query { issue(id:$id) { id } }')
        self.assertEqual(post.call_count,1)
        self.assertIn('kind=network', str(logs.output))
        self.assertNotIn('secret-key',str(logs.output)+str(caught.exception))

    def test_success_unchanged(self):
        response=Mock(status_code=200)
        response.json.return_value={'data':{'issueUpdate':{'success':True}}}
        with patch.object(provider.requests,'post',return_value=response):
            self.assertEqual(provider.query('tenant-key','mutation { issueUpdate { success } }'),response.json.return_value['data'])

if __name__ == '__main__': unittest.main()
