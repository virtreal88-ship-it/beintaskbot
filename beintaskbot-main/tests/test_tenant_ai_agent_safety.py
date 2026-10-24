import unittest

from tenant_ai_agent_safety import sanitize_draft, sanitize_virtual_reply
from tenant_ai_agents_policy import safety_policy
from tenant_linear_policy import TenantLinearError


class AgentSafetyTests(unittest.TestCase):
    def test_critical_controls_are_always_enabled(self):
        policy = safety_policy({})
        self.assertTrue(all(policy.values()))
        with self.assertRaises(TenantLinearError):
            safety_policy({'protect_internal': False})

    def test_secret_like_and_adult_output_never_reaches_draft(self):
        self.assertNotIn('sk-', sanitize_draft('token sk-abcdefghijklmnop', 'en'))
        self.assertNotIn('xxx', sanitize_draft('xxx offer', 'en').lower())

    def test_normal_customer_reply_is_preserved(self):
        self.assertEqual(sanitize_draft('Salam, menecer qiyməti dəqiqləşdirəcək.', 'az'),
                         'Salam, menecer qiyməti dəqiqləşdirəcək.')

    def test_virtual_reply_cannot_claim_an_unrecorded_handoff_or_ticket(self):
        result=sanitize_virtual_reply('Remote-support code is recorded and the case will be forwarded to programmers.', 'en')
        self.assertIn('does not confirm',result)
        self.assertNotIn('recorded and the case will be forwarded',result)
        self.assertEqual(sanitize_virtual_reply('Please share a screenshot of the error.', 'en'),
                         'Please share a screenshot of the error.')
