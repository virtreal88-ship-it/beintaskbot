"""Task assignment must not mutate existing deal pipeline/status."""
import ast
from pathlib import Path
import unittest


class AssignmentRoutingTests(unittest.TestCase):
    def test_only_explicit_deal_edit_can_call_transfer_helper(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'))
        calls=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='move_lead_to_icraci':
                calls.append(node)
        self.assertEqual(len(calls),1,'No task creation/confirmation/assignment path may move a deal')
        handler=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='handle_api_action')
        self.assertIn(calls[0],list(ast.walk(handler)))
        source=ast.get_source_segment((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'),handler)
        self.assertIn('elif action == "deal_edit":',source)

    def test_saas_creation_service_has_no_lead_patch(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'tenant_tasks.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='request':
                methods=[arg.value for arg in node.args if isinstance(arg,ast.Constant) and isinstance(arg.value,str)]
                self.assertNotIn('PATCH',methods,'Task creation cannot move existing deals')


if __name__=='__main__':unittest.main()
