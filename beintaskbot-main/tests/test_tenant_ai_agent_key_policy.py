import os
import unittest
from unittest.mock import Mock, patch

from tenant_ai_agent_key_policy import (platform_agent_enabled, platform_key_ready,
    platform_model, skip_pre_activation_jobs)


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


if __name__ == '__main__':
    unittest.main()
