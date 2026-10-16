import os
import unittest
from unittest.mock import Mock, patch

from tenant_ai_agent_key_policy import (platform_agent_enabled, platform_key_ready,
    platform_model, platform_models, platform_model_choices, platform_token_limit,
    skip_pre_activation_jobs, choose_api_credential)


TENANT = 'e09a5c24-093f-4141-992d-532052e00c90'
AGENT = 'b1392647-2455-4ec8-a783-602af5a0a4a5'
PAIR = f'{TENANT}:{AGENT}'


class PlatformKeyPolicyTests(unittest.TestCase):
    def test_platform_credential_is_opt_in_for_exact_tenant_agent_and_connect(self):
        env = {'AI_AGENTS_PLATFORM_KEY_AGENTS': PAIR, 'OPENAI_API_KEY': 'opaque-secret',
               'OPENAI_HISTORY_REPLY_MODEL': 'gpt-4.1-2025-04-14',
               'AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT': '2026-10-16T04:00:00Z'}
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(platform_agent_enabled(TENANT, AGENT, 'connect'))
            self.assertFalse(platform_agent_enabled(TENANT, AGENT, 'kommo'))
            self.assertFalse(platform_agent_enabled('other-tenant', AGENT, 'connect'))
            self.assertFalse(platform_agent_enabled(TENANT, 'other-agent', 'connect'))
            self.assertTrue(platform_key_ready(TENANT, AGENT, 'connect'))
            self.assertEqual(platform_model(), 'gpt-4.1-2025-04-14')
            self.assertEqual(choose_api_credential(TENANT, AGENT, 'connect', 'company-secret'),
                             ('opaque-secret', 'platform'))
            self.assertEqual(choose_api_credential(TENANT, AGENT, 'kommo', 'company-secret'),
                             ('company-secret', 'company'))

    def test_incomplete_or_missing_platform_configuration_never_uses_global_key(self):
        cases = [
            {'OPENAI_API_KEY': 'opaque-secret'},
            {'AI_AGENTS_PLATFORM_KEY_AGENTS': PAIR, 'OPENAI_API_KEY': 'opaque-secret'},
            {'AI_AGENTS_PLATFORM_KEY_AGENTS': PAIR, 'OPENAI_HISTORY_REPLY_MODEL': 'gpt-4.1-2025-04-14'},
        ]
        for env in cases:
            with self.subTest(env=sorted(env)):
                with patch.dict(os.environ, env, clear=True):
                    self.assertFalse(platform_key_ready(TENANT, AGENT, 'connect'))

    def test_cutover_skips_only_allowlisted_old_connect_jobs(self):
        cur = Mock()
        env = {'AI_AGENTS_PLATFORM_KEY_AGENTS': PAIR,
               'AI_AGENTS_PLATFORM_KEY_ACTIVATED_AT': '2026-10-16T04:00:00Z'}
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(skip_pre_activation_jobs(cur))
        self.assertEqual(cur.execute.call_count, 2)
        self.assertEqual(cur.execute.call_args_list[0].args[1], ('2026-10-16T04:00:00Z', [PAIR]))
        self.assertEqual(cur.execute.call_args_list[1].args[1], ([PAIR], '2026-10-16T04:00:00Z'))
        self.assertIn("j.provider='connect'", cur.execute.call_args_list[0].args[0])

    def test_platform_catalog_is_centrally_admitted_and_default_is_shared(self):
        env = {
            'AI_AGENTS_PLATFORM_ENABLED': 'true',
            'OPENAI_API_KEY': 'opaque-secret',
            'AI_AGENTS_PLATFORM_DEFAULT_MODEL': 'gpt-4.1-mini',
            'AI_AGENTS_PLATFORM_MODELS': '[{"id":"gpt-4.1-mini","label":"GPT-4.1 mini"},"gpt-5-mini"]',
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(platform_models(), ['gpt-4.1-mini', 'gpt-5-mini'])
            self.assertEqual(platform_model(), 'gpt-4.1-mini')
            self.assertEqual(platform_model_choices()[0]['id'], '')
            self.assertTrue(platform_key_ready('tenant-a', 'agent-a', 'connect'))
            self.assertEqual(choose_api_credential('tenant-a', 'agent-a', 'connect', '', 'gpt-5-mini'),
                             ('opaque-secret', 'platform'))
            # A legacy tenant with a private key and a non-catalogue model is
            # left on its private configuration.
            self.assertEqual(choose_api_credential('tenant-b', 'agent-b', 'kommo', 'company-secret', 'legacy-model'),
                             ('company-secret', 'company'))

    def test_platform_limits_are_server_scoped_per_tenant(self):
        env = {'AI_AGENTS_PLATFORM_DEFAULT_TOKEN_LIMIT': '1000',
               'AI_AGENTS_PLATFORM_TENANT_TOKEN_LIMITS': '{"tenant-a": 250}'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(platform_token_limit('tenant-a'), 250)
            self.assertEqual(platform_token_limit('tenant-b'), 1000)
            self.assertEqual(platform_token_limit('tenant-c'), 1000)


if __name__ == '__main__':
    unittest.main()
