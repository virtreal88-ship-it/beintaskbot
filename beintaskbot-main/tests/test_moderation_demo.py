import hashlib
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import moderation_demo as demo
import moderation_demo_api as api


TEST_PASSWORD = "review-example-only-123"
SALT = "00" * 16
HASH = "pbkdf2_sha256$200000$" + SALT + "$" + hashlib.pbkdf2_hmac(
    "sha256", TEST_PASSWORD.encode(), bytes.fromhex(SALT), 200000).hex()
SETTINGS = {"MODERATION_DEMO_ENABLED": "true", "MODERATION_DEMO_TENANT_ID": "5a1dba60-ea6c-4afd-ab66-79d32795bb87",
            "MODERATION_DEMO_USERNAME": "review-test", "MODERATION_DEMO_PASSWORD_HASH": HASH,
            "MODERATION_DEMO_EXPIRES_AT": "2099-01-01T00:00:00Z"}


def complete_immediate(coroutine):
    """Fixtures do not suspend; no sockets/event loop needed for these cases."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError("Fixture unexpectedly suspended")


class ModerationDemoTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(demo.config())
            self.assertFalse(demo.identity_allowed(SETTINGS["MODERATION_DEMO_TENANT_ID"], -42))
            self.assertTrue(demo.identity_allowed("production-tenant", 42))

    def test_isolated_identity_and_revocation(self):
        with patch.dict(os.environ, SETTINGS, clear=True):
            current = demo.config()
            self.assertLess(current.user_id, 0)
            self.assertTrue(demo.identity_allowed(current.tenant_id, current.user_id))
            self.assertFalse(demo.identity_allowed("another-company", current.user_id))
            self.assertFalse(demo.identity_allowed(current.tenant_id, current.user_id - 1))
            with patch.dict(os.environ, {"MODERATION_DEMO_ENABLED": "false"}):
                self.assertFalse(demo.identity_allowed(current.tenant_id, current.user_id))

    def test_invalid_and_expired_configuration(self):
        for key, value in (("MODERATION_DEMO_TENANT_ID", "invalid"),
                           ("MODERATION_DEMO_EXPIRES_AT", "2000-01-01T00:00:00Z"),
                           ("MODERATION_DEMO_EXPIRES_AT", "2099-01-01T00:00:00"),
                           ("MODERATION_DEMO_USERNAME", "")):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {**SETTINGS, key: value}, clear=True):
                self.assertIsNone(demo.config())

    def test_password_validation_fails_closed(self):
        self.assertTrue(demo.password_matches(TEST_PASSWORD, HASH))
        self.assertFalse(demo.password_matches("wrong-password-123", HASH))
        for bad in ("", "plain-password", HASH.replace("200000", "1"), HASH.replace("pbkdf2_sha256", "md5")):
            self.assertFalse(demo.password_matches(TEST_PASSWORD, bad))


class LoginTests(unittest.TestCase):
    def setUp(self):
        api._attempts.clear()
        self.current = demo.DemoConfig(SETTINGS["MODERATION_DEMO_TENANT_ID"], "review-test", HASH, int(time.time()) + 600)
        self.session = Mock(return_value="signed-test-cookie")
        self.login = api.handler(self.session, "demo-cookie", "https://crm.pro.az", Mock())

    def request(self, data, origin="https://crm.pro.az", size=200):
        async def body():
            return data
        return SimpleNamespace(headers={"Origin": origin}, content_type="application/json", content_length=size, json=body)

    def call(self, request, current=True):
        async def threaded(function, *args):
            return function(*args)
        with patch.object(api, "config", return_value=self.current if current else None), \
             patch.object(api.asyncio, "to_thread", threaded), patch.object(api, "provision") as provision:
            response = complete_immediate(self.login(request))
            return response, provision

    def test_disabled_endpoint_does_not_grant_session(self):
        from aiohttp.web import HTTPNotFound
        with self.assertRaises(HTTPNotFound):
            self.call(self.request({}), current=False)
        self.session.assert_not_called()

    def test_bad_origin_body_and_credentials_never_provision(self):
        for request, status in ((self.request({}, origin="https://evil.test"), 403),
                                (self.request({}, size=5000), 400),
                                (self.request([]), 400),
                                (self.request({"username": "wrong", "password": TEST_PASSWORD}), 401)):
            response, provision = self.call(request)
            self.assertEqual(response.status, status)
            provision.assert_not_called()
            self.session.assert_not_called()

    def test_valid_login_only_uses_server_configured_company(self):
        response, provision = self.call(self.request({"username": "review-test", "password": TEST_PASSWORD,
                                                     "tenant_id": "production", "telegram_id": 123}))
        self.assertEqual(response.status, 200)
        provision.assert_called_once_with(self.current)
        self.session.assert_called_once_with(self.current.tenant_id, self.current.user_id)
        cookie = response.cookies["demo-cookie"]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertLessEqual(int(cookie["max-age"]), 600)
        self.assertEqual(json.loads(response.text)["redirect"], "/setup")

    def test_attempts_are_bounded(self):
        for _ in range(5):
            self.call(self.request({}))
        response, provision = self.call(self.request({"username": "review-test", "password": TEST_PASSWORD}))
        self.assertEqual(response.status, 429)
        provision.assert_not_called()


if __name__ == "__main__":
    unittest.main()

class ReviewerCRMIdentityTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, SETTINGS, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        current = demo.config()
        self.profile = {'tenant_id': current.tenant_id, 'telegram_id': current.user_id,
            'company_owner_id': current.user_id, 'role': 'owner', 'active': True,
            'tenant_status': 'active', 'onboarding': {'moderation_demo': True},
            'permissions': ['tasks', 'deals', 'customers'], 'workflow': {}}

    def request(self, profile=None, **changes):
        profile = profile or self.profile
        return SimpleNamespace(query={'expected_tenant_id': profile['tenant_id'],
            'expected_user_id': str(profile['telegram_id']), **changes})

    def test_verified_reviewer_context_and_task_actor_not_resource_ids(self):
        from tenant_crm_request_context import require_expected_context
        import tenant_task_flow as flow
        from tenant_policy import TenantPolicy
        self.assertTrue(demo.reviewer_profile_allowed(self.profile))
        self.assertTrue(TenantPolicy(self.profile).public_capabilities()['moderation_reviewer'])
        require_expected_context(self.request(), self.profile)
        self.assertEqual(flow.query(self.request(source='kommo'), self.profile, required=True)['source'], 'kommo')
        self.assertEqual(flow._actor(self.profile['telegram_id'], self.profile), str(self.profile['telegram_id']))
        with self.assertRaises(flow.TaskFlowError):
            flow._positive(self.profile['telegram_id'])
        sources = flow._sources(TenantPolicy(self.profile))
        self.assertTrue(all(source['view'] for source in sources))
        self.assertTrue(next(source for source in sources if source['source']=='kommo')['create'])

    def test_malformed_or_other_actor_never_uses_demo_exception(self):
        from tenant_crm_request_context import require_expected_context, CRMContextError
        import tenant_task_flow as flow
        for actor in ['0', '-1', str(self.profile['telegram_id'] - 1),
                      ' '+str(self.profile['telegram_id']), '--1', '-01']:
            with self.subTest(actor=actor):
                with self.assertRaises(CRMContextError):
                    require_expected_context(self.request(expected_user_id=actor), self.profile)
                with self.assertRaises(flow.TaskFlowError):
                    flow.query(self.request(expected_user_id=actor), self.profile, required=True)

    def test_demo_profile_missing_or_revoked_is_not_authority(self):
        from tenant_crm_request_context import require_expected_context, CRMContextError
        import tenant_task_flow as flow
        changes = [{'active': False}, {'role': 'admin'}, {'onboarding': {}},
                   {'onboarding': {'moderation_demo': False}}, {'onboarding': 'bad'},
                   {'tenant_status': 'suspended'}, {'tenant_status': 'archived'},
                   {'tenant_id': '00000000-0000-0000-0000-000000000001'},
                   {'company_owner_id': 42}, {'telegram_id': self.profile['telegram_id']-1}]
        for change in changes:
            profile = {**self.profile, **change}
            with self.subTest(change=change):
                self.assertFalse(demo.reviewer_profile_allowed(profile))
                with self.assertRaises(CRMContextError):
                    require_expected_context(self.request(profile), profile)
                with self.assertRaises(flow.TaskFlowError):
                    flow.query(self.request(profile), profile, required=True)

    def test_expiry_disable_and_config_tenant_change_close_exception(self):
        from tenant_crm_request_context import require_expected_context, CRMContextError
        import tenant_task_flow as flow
        for changes in [{'MODERATION_DEMO_ENABLED': 'false'},
                        {'MODERATION_DEMO_EXPIRES_AT': '2000-01-01T00:00:00Z'},
                        {'MODERATION_DEMO_TENANT_ID': '00000000-0000-0000-0000-000000000001'}]:
            with self.subTest(changes=changes), patch.dict(os.environ, changes):
                self.assertFalse(demo.reviewer_profile_allowed(self.profile))
                with self.assertRaises(CRMContextError):
                    require_expected_context(self.request(), self.profile)
                with self.assertRaises(flow.TaskFlowError):
                    flow.query(self.request(), self.profile, required=True)

    def test_cross_tenant_intent_still_fails_and_positive_users_are_unchanged(self):
        from tenant_crm_request_context import require_expected_context, CRMContextError
        import tenant_task_flow as flow
        request = self.request(expected_tenant_id='00000000-0000-0000-0000-000000000001')
        with self.assertRaises(CRMContextError) as crm:
            require_expected_context(request, self.profile)
        self.assertEqual(crm.exception.status, 409)
        with self.assertRaises(flow.TaskFlowError) as tasks:
            flow.query(request, self.profile, required=True)
        self.assertEqual(tasks.exception.status, 409)
        normal = {**self.profile, 'telegram_id': 42, 'company_owner_id':42, 'onboarding':{}}
        with patch.dict(os.environ, {'MODERATION_DEMO_ENABLED':'false'}):
            self.assertFalse(demo.reviewer_profile_allowed(normal))
            require_expected_context(self.request(normal), normal)
            self.assertEqual(flow.query(self.request(normal), normal, required=True)['expected_user_id'], '42')

    def test_task_transaction_revalidates_reviewer_after_membership_read(self):
        import tenant_task_flow as flow
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        connection.cursor.return_value = cursor
        operation = Mock(return_value='ok')
        with patch.object(flow, '_connect', return_value=connection), \
             patch.object(flow, '_require_schema'), \
             patch.object(flow, '_profiles', return_value=(self.profile, {})):
            self.assertEqual(flow._attempt(self.profile, operation), 'ok')
        operation.reset_mock()
        for patch_profile in [{'role':'admin'}, {'active':False}, {'onboarding':{}}]:
            with patch.object(flow, '_connect', return_value=connection), \
                 patch.object(flow, '_require_schema'), \
                 patch.object(flow, '_profiles', return_value=({**self.profile, **patch_profile}, {})):
                with self.assertRaises(flow.TaskFlowError):
                    flow._attempt(self.profile, operation)
            operation.assert_not_called()

    def test_sync_history_requires_current_verified_reviewer_before_rows(self):
        import tenant_crm_sync_history as history
        from tenant_crm_native_access import CRMAccessError
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        cursor = Mock()
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchall.return_value = []
        connection.cursor.return_value = cursor
        tenant, actor = self.profile['tenant_id'], self.profile['telegram_id']
        ident = '00000000-0000-0000-0000-000000000009'
        with patch.object(history, '_connect', return_value=connection), \
             patch.object(history, 'current_access', return_value=self.profile) as access, \
             patch.object(history, 'locked_resource', return_value={'id':ident}) as resource:
            self.assertEqual(history.read_history(tenant,'deal',ident,actor_id=actor)['events'], [])
            access.assert_called_once_with(cursor,tenant,actor,'deals','view')
            resource.assert_called_once()
        for current in [{**self.profile,'role':'admin'}, {**self.profile,'onboarding':{}},
                        {**self.profile,'tenant_id':'00000000-0000-0000-0000-000000000001'}, None]:
            cursor.execute.reset_mock()
            with patch.object(history, '_connect', return_value=connection), \
                 patch.object(history, 'current_access', return_value=current), \
                 patch.object(history, 'locked_resource') as resource:
                with self.assertRaises(CRMAccessError):
                    history.read_history(tenant,'deal',ident,actor_id=actor)
                resource.assert_not_called()
                cursor.execute.assert_not_called()

        with patch.dict(os.environ, {'MODERATION_DEMO_EXPIRES_AT':'2000-01-01T00:00:00Z'}), \
             patch.object(history, '_connect', return_value=connection), \
             patch.object(history, 'current_access', return_value=self.profile), \
             patch.object(history, 'locked_resource') as resource:
            with self.assertRaises(CRMAccessError):
                history.read_history(tenant,'deal',ident,actor_id=actor)
            resource.assert_not_called()
