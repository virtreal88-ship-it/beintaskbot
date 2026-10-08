import ast
from pathlib import Path
import unittest
from unittest.mock import patch
from legacy_chat_reply_prompt import history_reply_instructions, history_reply_model

ROOT = Path(__file__).resolve().parents[1]


class HistoryReply(unittest.TestCase):
    def test_dedicated_model_ignores_global_mini_setting(self):
        with patch.dict('os.environ', {'OPENAI_MODEL': 'gpt-4o-mini'}, clear=True):
            self.assertEqual(history_reply_model(), 'gpt-4.1-2025-04-14')
        with patch.dict('os.environ', {'OPENAI_HISTORY_REPLY_MODEL': '  gpt-4.1  '}, clear=True):
            self.assertEqual(history_reply_model(), 'gpt-4.1')
        with patch.dict('os.environ', {'OPENAI_HISTORY_REPLY_MODEL': '   '}, clear=True):
            self.assertEqual(history_reply_model(), 'gpt-4.1-2025-04-14')

    def test_only_reply_uses_dedicated_model(self):
        tree = ast.parse((ROOT/'bot.py').read_text(encoding='utf-8-sig'))
        handler = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'handle_api_deal_chat_suggest')
        reply = next(node for node in handler.body if isinstance(node, ast.If) and ast.unparse(node.test) == "mode == 'reply'")
        dedicated = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'history_reply_model']
        self.assertEqual(len(dedicated), 2)
        self.assertTrue(all(node in list(ast.walk(reply)) for node in dedicated))
        summary = next(node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name == '_ask_summary')
        self.assertIn('model=LLM_MODEL', ast.unparse(summary))

    def test_complete_company_prompt_and_critical_rules(self):
        prompt = history_reply_instructions()
        original = (ROOT/'prompts/beinsystems-history-reply.az.txt').read_text(encoding='utf-8').strip()
        self.assertTrue(prompt.endswith(original))
        for value in ('+994502072240', 'bein.az', 'CRITICAL QADAĞA', 'kvadratmetri', 'qiymət mövzusunu AÇMA', 'Yalnız Azərbaycan dilində', '18+ fəaliyyət'):
            self.assertIn(value, prompt)

    def test_instruction_only_in_history_reply_branch(self):
        tree = ast.parse((ROOT/'bot.py').read_text(encoding='utf-8-sig'))
        handler = next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='handle_api_deal_chat_suggest')
        reply = next(node for node in handler.body if isinstance(node,ast.If) and ast.unparse(node.test)=="mode == 'reply'")
        calls = [node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='history_reply_instructions']
        self.assertEqual(len(calls),1);self.assertIn(calls[0],list(ast.walk(reply)))
        self.assertNotIn('söhbəti irəli aparmaq',ast.get_source_segment((ROOT/'bot.py').read_text(encoding='utf-8-sig'),reply))

    def test_not_installed_as_other_companies_default(self):
        for name in ('tenant_chat_ai_provider.py','tenant_news_ai_provider.py'):
            text = (ROOT/name).read_text(encoding='utf-8')
            self.assertNotIn('history_reply_instructions',text);self.assertNotIn('+994502072240',text)


if __name__=='__main__':unittest.main()
