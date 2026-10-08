"""Exercise the real legacy context builder without importing the bot runtime."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

TREE = ast.parse((Path(__file__).resolve().parents[1] / 'bot.py').read_text(encoding='utf-8-sig'))


def context_builder(transcription=''):
    function = next(node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_ai_history_lines')
    transcribe = Mock(return_value=transcription)
    namespace = {
        'asyncio': asyncio,
        '_history_message_is_incoming': lambda item: item.get('incoming', False),
        '_is_history_audio': lambda item: item.get('message_type') in ('voice', 'call'),
        '_transcribe_summary_audio': transcribe,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<context builder>', 'exec'), namespace)
    return namespace['_ai_history_lines'], transcribe


class ReplyContext(unittest.IsolatedAsyncioTestCase):
    async def test_actual_reply_request_uses_model_prompt_and_voice_context(self):
        handler = next(node for node in TREE.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'handle_api_deal_chat_suggest')
        branch = next(node for node in handler.body if isinstance(node, ast.If) and ast.unparse(node.test) == "mode == 'reply'")
        wrapper = ast.parse('async def reply():\n    pass').body[0]
        wrapper.body = [branch]
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Salam.'))])
        namespace = {
            'asyncio': asyncio, 'mode': 'reply', 'lead': {'name': 'Sale'},
            'history': 'Müştəri (səsli mesajın mətni): Restoran üçün məlumat ver',
            'draft': '', 'contact_name': 'Ali', 'history_rows': [{}, {}],
            'transcribed_count': 1, 'untranscribed_voice_count': 0,
            'history_reply_instructions': lambda: 'Sales instructions',
            'history_reply_model': lambda: 'gpt-4.1-2025-04-14',
            '_select_ai_reply_examples': lambda *args: [], 'llm_client': client,
            'web': SimpleNamespace(json_response=lambda value, **kwargs: value),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), '<reply request>', 'exec'), namespace)
        result = await namespace['reply']()
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request['model'], 'gpt-4.1-2025-04-14')
        self.assertEqual(request['messages'][0]['content'], 'Sales instructions')
        self.assertIn(namespace['history'], request['messages'][1]['content'])
        self.assertEqual(result['context_message_count'], 2)
        self.assertEqual(result['transcribed_voice_count'], 1)

    async def test_unrelated_saved_examples_are_not_sent(self):
        function = next(node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == '_select_ai_reply_examples')
        namespace = {
            '_AI_REPLY_EXAMPLES_FILE': 'unused', '_ai_reply_example_scope': lambda lead: 'pipeline',
            '_ai_reply_tokens': lambda text: set(text.split()),
            'read_json': lambda path: [
                {'context': 'restaurant', 'reply': 'Restaurant example', 'scope': 'pipeline'},
                {'context': 'warehouse', 'reply': 'Wrong topic', 'scope': 'pipeline'},
            ],
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<examples>', 'exec'), namespace)
        selected = namespace['_select_ai_reply_examples']({}, 'restaurant')
        self.assertEqual([row['reply'] for row in selected], ['Restaurant example'])

    async def test_long_customer_message_and_manager_roles(self):
        build, _ = context_builder()
        text = 'a' * 500 + ' restoran üçün istəyirəm'
        lines, _, _ = await build([{'incoming': True, 'text': text}, {'text': 'Cavab'}], reply_context=True)
        self.assertEqual(lines, ['Müştəri: ' + text, 'Satış meneceri: Cavab'])

    async def test_summary_keeps_its_original_text_limit(self):
        build, _ = context_builder()
        lines, _, _ = await build([{'incoming': True, 'text': 'a' * 500}])
        self.assertEqual(lines, ['Müştəri: ' + 'a' * 400])

    async def test_latest_thirty_valid_messages(self):
        build, _ = context_builder()
        rows = [{'text': str(i)} for i in range(35)] + [None]
        lines, _, _ = await build(rows, reply_context=True)
        self.assertEqual(len(lines), 30)
        self.assertEqual(lines[0], 'Satış meneceri: 5')
        self.assertEqual(lines[-1], 'Satış meneceri: 34')

    async def test_existing_voice_transcript_reused(self):
        build, transcribe = context_builder()
        lines, count, failed = await build([{'incoming': True, 'message_type': 'voice', 'transcript': 'Qiymət nə qədərdir?'}], reply_context=True)
        self.assertIn('Müştəri (səsli mesajın mətni): Qiymət nə qədərdir?', lines)
        self.assertEqual((count, failed), (1, 0))
        transcribe.assert_not_called()

    async def test_call_is_transcribed_before_answer(self):
        build, transcribe = context_builder('Restoran üçün proqram lazımdır')
        row = {'incoming': True, 'message_type': 'call'}
        lines, count, failed = await build([row], {'id': 42}, reply_context=True)
        self.assertIn('Müştəri (zəng yazısının mətni): Restoran üçün proqram lazımdır', lines)
        self.assertEqual((count, failed), (1, 0))
        transcribe.assert_called_once_with(row, {'id': 42})

    async def test_missing_audio_and_unanalysed_image_are_explicit(self):
        build, _ = context_builder()
        lines, count, failed = await build([{'incoming': True, 'message_type': 'voice'}, {'incoming': True, 'media_url': 'photo.jpg'}], reply_context=True)
        self.assertIn('məzmunu məlum deyil', lines[0])
        self.assertIn('təhlil edilməyib', lines[1])
        self.assertEqual((count, failed), (0, 1))

    async def test_oversized_text_is_bounded_and_marked(self):
        build, _ = context_builder()
        lines, _, _ = await build([{'text': 'x' * 7000}], reply_context=True)
        self.assertIn('ixtisar edilib', lines[0])
        self.assertLess(len(lines[0]), 6100)


if __name__ == '__main__':
    unittest.main()
