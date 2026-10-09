"""Reply model recovery never changes instructions, media or sends messages."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from legacy_chat_reply_provider import generate, log_failure, candidates


class ProviderError(Exception):
    def __init__(self, code, status=404):
        super().__init__('PRIVATE KEY AND CUSTOMER MESSAGE')
        self.status_code=status;self.body={'error':{'code':code}}


def response(text='Salam.'):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.api=Mock();self.logger=Mock()
        self.messages=[{'role':'system','content':'Original instructions'}, {'role':'user','content':[
            {'type':'text','text':'Müştəri (səs): məlumat ver'},
            {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,PRIVATE'}}]}]
    def test_available_model_is_not_changed(self):
        self.api.create.return_value=response()
        self.assertEqual(generate(self.api,'gpt-4.1-2025-04-14',self.messages,self.logger),('Salam.','gpt-4.1-2025-04-14'))
        self.api.create.assert_called_once()
    def test_snapshot_access_error_tries_family_alias(self):
        self.api.create.side_effect=[ProviderError('model_not_found'),response()]
        self.assertEqual(generate(self.api,'gpt-4.1-2025-04-14',self.messages,self.logger),('Salam.','gpt-4.1'))
        for call in self.api.create.call_args_list:
            self.assertIs(call.kwargs['messages'],self.messages)
            self.assertEqual(call.kwargs['temperature'],0.45)
        self.assertNotIn('PRIVATE',str(self.logger.mock_calls))
    def test_mini_only_account_can_still_generate_with_media(self):
        self.api.create.side_effect=[ProviderError('model_not_found')]*3+[response()]
        self.assertEqual(generate(self.api,'gpt-4.1-2025-04-14',self.messages,self.logger)[1],'gpt-4o-mini')
        self.assertEqual(self.api.create.call_count,4)
    def test_no_fallback_for_key_quota_rate_limit_timeout_or_bad_image(self):
        for error in (ProviderError('invalid_api_key',401),ProviderError('insufficient_quota',429),
                      ProviderError('rate_limit_exceeded',429),ProviderError('invalid_image',400),TimeoutError()):
            api=Mock();api.create.side_effect=error
            with self.assertRaises(type(error)):generate(api,'gpt-4.1',self.messages,self.logger)
            api.create.assert_called_once()
    def test_all_models_unavailable_are_bounded(self):
        self.api.create.side_effect=ProviderError('model_not_found')
        with self.assertRaises(ProviderError):generate(self.api,'gpt-4.1',self.messages,self.logger)
        self.assertEqual(self.api.create.call_count,3)
    def test_no_duplicate_candidates_or_unbounded_model_search(self):
        self.assertEqual(candidates('gpt-4.1'),['gpt-4.1','gpt-4o','gpt-4o-mini'])
        self.assertLessEqual(len(candidates('custom')),4)
    def test_log_does_not_expose_exception_body_or_key(self):
        log_failure(self.logger,ProviderError('SECRET_KEY',500))
        self.assertNotIn('SECRET',str(self.logger.mock_calls));self.assertNotIn('PRIVATE',str(self.logger.mock_calls))
    def test_empty_answer_not_retried_as_access_error(self):
        self.api.create.return_value=response('')
        self.assertEqual(generate(self.api,'gpt-4.1',self.messages,self.logger),('','gpt-4.1'))
        self.api.create.assert_called_once()
