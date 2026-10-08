"""Linear tenant configuration contracts, no provider/database writes."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
from cryptography.fernet import Fernet
import test_tenant_hot_orders as loaders
from tenant_linear_policy import owner, settings, TenantLinearError
import tenant_linear_provider as provider

ID = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
PROFILE = {'tenant_id': ID, 'telegram_id': 20, 'role': 'owner', 'active': True}


class PolicyTests(unittest.TestCase):
    def test_defaults_are_manual_and_main_only(self):
        news = settings({})['news']
        self.assertTrue(news['manual_approval']); self.assertTrue(news['main_issues_only'])
        self.assertEqual(news['retention_days'], 90)
        self.assertEqual(settings(settings({})), settings({}))

    def test_cannot_enable_auto_publication(self):
        for value in ({'news': {'auto_publish': True}}, {'news': {'manual_approval': False}},
                      {'members': {'20': {'can_edit': 'yes'}}}, {'members': {'020': {}}},
                      {'team_id': 'foreign'}, {'news': {'channel': 'https://evil.invalid'}}):
            with self.assertRaises(TenantLinearError): settings(value)

    def test_membership_not_global_identity(self):
        self.assertEqual(settings({'members': {'20': {'account': ' rufet ', 'operator': 'rufet@example'}}})['members']['20']['account'], 'rufet')
        for change in ({'role': 'admin'}, {'role': 'worker'}, {'active': False}, {'tenant_status': 'disabled'}):
            with self.assertRaises(TenantLinearError): owner({**PROFILE, **change})


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.connect = MagicMock(); self.conn = self.connect.return_value.__enter__.return_value
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cipher = Fernet(Fernet.generate_key()); self.verify = Mock()
        self.service = loaders.load('tenant_linear', {'_connect': self.connect, '_ensure_schema': Mock(),
            '_fernet': Mock(return_value=self.cipher), 'verify': self.verify, 'catalog': Mock(), 'team_catalog': Mock()},
            {'tenant_platform', 'tenant_linear_provider'})

    def test_connect_checks_owner_encrypts_and_never_returns_key(self):
        self.cur.fetchone.side_effect = [{'role': 'owner', 'active': True}, None,
            {'status': 'connected', 'metadata': {}, 'updated_at': 'time'}]
        result = self.service.command(PROFILE, {'action': 'connect', 'api_key': 'private-key'})
        self.verify.assert_called_once_with('private-key')
        insert = self.cur.execute.call_args_list[-1]
        token = insert.args[1][1]
        self.assertNotIn(b'private-key', token)
        self.assertEqual(json.loads(self.cipher.decrypt(token))['tenant_id'], ID)
        self.assertNotIn('private-key', str(result)); self.assertFalse(result['runtime_enabled'])
        self.conn.commit.assert_called_once()

    def test_encryption_failure_stops_before_provider(self):
        self.service.command.__globals__['_fernet'].side_effect = RuntimeError('configuration')
        with self.assertRaises(RuntimeError): self.service.command(PROFILE, {'action': 'connect', 'api_key': 'key'})
        self.verify.assert_not_called(); self.connect.assert_not_called()

    def test_revoked_owner_rejected_in_transaction(self):
        self.cur.fetchone.return_value = {'role': 'worker', 'active': True}
        with self.assertRaises(TenantLinearError): self.service.command(PROFILE, {'action': 'disconnect'})
        self.conn.commit.assert_not_called()

    def test_concurrent_settings_conflict(self):
        self.cur.fetchone.side_effect = [{'role': 'owner', 'active': True}, {'updated_at': 'new'}]
        with self.assertRaises(TenantLinearError) as caught:
            self.service.command(PROFILE, {'action': 'disconnect', 'expected_updated_at': 'old'})
        self.assertEqual(caught.exception.status, 409); self.conn.commit.assert_not_called()
        self.assertIn('pg_advisory_xact_lock', str(self.cur.execute.call_args_list))

    def test_foreign_member_cannot_be_configured(self):
        self.cur.fetchone.side_effect = [{'role': 'owner', 'active': True}, {'updated_at': 't'}]
        self.cur.fetchall.return_value = [{'telegram_id': 20}]
        with self.assertRaises(TenantLinearError):
            self.service.command(PROFILE, {'action': 'settings', 'expected_updated_at': 't', 'settings': {'members': {'999': {}}}})
        self.conn.commit.assert_not_called()

    def test_credential_bound_to_company(self):
        encrypted = self.cipher.encrypt(json.dumps({'tenant_id': 'foreign', 'provider': 'linear', 'api_key': 'secret'}).encode())
        self.cur.fetchone.side_effect = [{'role': 'owner', 'active': True}, {'status': 'connected', 'secrets': encrypted}]
        with self.assertRaises(TenantLinearError) as caught: self.service.credential(PROFILE)
        self.assertEqual(caught.exception.status, 503)

    def test_no_global_token_fallback(self):
        self.cur.fetchone.side_effect = [{'role': 'owner', 'active': True}, None]
        with self.assertRaises(TenantLinearError): self.service.credential(PROFILE)

    def test_foreign_status_or_project_fails_before_write(self):
        self.service.command.__globals__['choices'] = Mock(return_value={'team': {
            'states': {'nodes': []}, 'projects': {'nodes': []}}})
        with self.assertRaises(TenantLinearError):
            self.service.command(PROFILE, {'action': 'settings', 'settings': {'team_id': ID, 'done_state_ids': [ID]}})
        self.connect.assert_not_called()

    def test_disconnect_destroys_secret_without_provider_write(self):
        self.cur.fetchone.side_effect = [{'role': 'owner', 'active': True}, {'updated_at': 't'},
            {'status': 'not_connected', 'metadata': {'settings': {'team_id': ID}}, 'updated_at': 'new'}]
        result = self.service.command(PROFILE, {'action': 'disconnect', 'expected_updated_at': 't'})
        self.assertIn('secrets=NULL', self.cur.execute.call_args.args[0])
        self.assertEqual(result['settings']['team_id'], ID); self.verify.assert_not_called()

    def test_missing_connection_cannot_claim_saved_settings(self):
        self.cur.fetchone.side_effect = [{'role': 'owner', 'active': True}, None]
        with self.assertRaises(TenantLinearError) as caught:
            self.service.command(PROFILE, {'action': 'settings', 'settings': {}})
        self.assertEqual(caught.exception.status, 409); self.conn.commit.assert_not_called()


class ProviderTests(unittest.TestCase):
    @patch.object(provider.requests, 'post')
    def test_graphql_partial_errors_fail_closed(self, post):
        post.return_value.json.return_value = {'data': {'viewer': {'id': ID}}, 'errors': [{'message': 'secret'}]}
        with self.assertRaises(TenantLinearError) as caught: provider.verify('valid-api-key')
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(post.call_args.kwargs['timeout'], (5, 20)); self.assertFalse(post.call_args.kwargs['allow_redirects'])

    @patch.object(provider.requests, 'post')
    def test_read_only_verification(self, post):
        post.return_value.json.return_value = {'data': {'viewer': {'id': ID}}}
        provider.verify('valid-api-key')
        self.assertNotIn('mutation', post.call_args.kwargs['json']['query'])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.command = Mock(return_value={}); self.read = Mock(return_value={}); self.choices = Mock(return_value={})
        self.module = loaders.load('tenant_linear_api', {'read': self.read, 'command': self.command, 'choices': self.choices,
            'web': SimpleNamespace(json_response=lambda body, **kw: {'body': body, **kw})}, {'tenant_linear', 'aiohttp'})
        self.logger = Mock(); self.handle = self.module.linear_handler(lambda r: PROFILE, 'https://crm.pro.az', self.logger)

    async def request(self, data=None, origin='https://crm.pro.az', method='POST', query=None):
        async def read(): return data
        return await self.handle(SimpleNamespace(method=method, content_type='application/json', headers={'Origin': origin},
            json=read, rel_url=SimpleNamespace(query=query or {})))

    async def test_wrong_origin_and_company_rejected(self):
        self.assertEqual((await self.request({}, origin='https://evil.invalid'))['status'], 403)
        self.assertEqual((await self.request({'expected_tenant_id': 'foreign', 'expected_user_id': 20}))['status'], 409)
        self.command.assert_not_called()

    async def test_only_owner_and_no_secret_errors(self):
        self.handle = self.module.linear_handler(lambda r: {**PROFILE, 'role': 'admin'}, 'https://crm.pro.az', self.logger)
        self.assertEqual((await self.request(method='GET'))['status'], 403); self.read.assert_not_called()

    async def test_error_response_and_logs_are_generic(self):
        self.read.side_effect = RuntimeError('PRIVATE KEY')
        result = await self.request(method='GET')
        self.assertEqual(result['status'], 503); self.assertNotIn('PRIVATE', str(result))
        self.logger.error.assert_called_once_with('Tenant Linear configuration failed')

    async def test_signed_profile_used(self):
        data = {'expected_tenant_id': ID, 'expected_user_id': 20, 'action': 'disconnect'}
        result = await self.request(data)
        self.command.assert_called_once_with(PROFILE, data); self.assertEqual(result['headers']['Cache-Control'], 'no-store')
