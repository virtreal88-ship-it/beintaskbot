import unittest

from session import Session as _Session  # Adds the repository's production module path.
from tenant_operator_realtime_usage import usage_cost_microusd


class RealtimeUsageTests(unittest.TestCase):
    def test_prices_audio_text_and_cached_input_with_rounding_up(self):
        usage={'input_tokens':10,'output_tokens':10,'total_tokens':20,
               'input_token_details':{'text_tokens':5,'audio_tokens':5,'image_tokens':0,'cached_tokens':2,
                                      'cached_tokens_details':{'text_tokens':2,'audio_tokens':0,'image_tokens':0}},
               'output_token_details':{'text_tokens':3,'audio_tokens':7}}
        self.assertEqual(usage_cost_microusd(usage),201)

    def test_unknown_or_inconsistent_usage_is_not_repriced(self):
        self.assertIsNone(usage_cost_microusd(None))
        self.assertIsNone(usage_cost_microusd({'input_tokens':1,'output_tokens':0,'total_tokens':1}))
        usage={'input_tokens':2,'output_tokens':0,'total_tokens':2,
               'input_token_details':{'text_tokens':0,'audio_tokens':1},
               'output_token_details':{'text_tokens':0,'audio_tokens':0}}
        self.assertIsNone(usage_cost_microusd(usage))

    def test_unpriced_image_usage_fails_closed(self):
        usage={'input_tokens':1,'output_tokens':0,'total_tokens':1,
               'input_token_details':{'text_tokens':0,'audio_tokens':0,'image_tokens':1},
               'output_token_details':{'text_tokens':0,'audio_tokens':0}}
        self.assertIsNone(usage_cost_microusd(usage))


if __name__=='__main__':
    unittest.main()
