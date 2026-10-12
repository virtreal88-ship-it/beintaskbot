"""Task assignment must not mutate existing deal pipeline/status."""
import ast
from pathlib import Path
import unittest


class AssignmentRoutingTests(unittest.TestCase):
    def test_only_explicit_deal_edit_can_call_transfer_helper(self):
        root=Path(__file__).resolve().parents[1]
        bot_tree=ast.parse((root/'bot.py').read_text(encoding='utf-8-sig'))
        routing_tree=ast.parse((root/'legacy_action_deal_routing.py').read_text(encoding='utf-8-sig'))
        direct_calls=[]
        for tree in (bot_tree,routing_tree):
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='move_lead_to_icraci':
                    direct_calls.append(node)
        self.assertEqual(direct_calls,[],'Business code must use the injected routing port')
        routing_source=(root/'legacy_action_deal_routing.py').read_text(encoding='utf-8-sig')
        self.assertEqual(routing_source.count('context.move_lead_to_assignee('),1,
                         'No task creation/confirmation path may move a deal')
        handler=next(node for node in bot_tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='handle_api_action')
        source=ast.get_source_segment((root/'bot.py').read_text(encoding='utf-8-sig'),handler)
        self.assertIn('handle_deal_edit(',source)

    def test_saas_creation_service_has_no_lead_patch(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'tenant_tasks.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='request':
                methods=[arg.value for arg in node.args if isinstance(arg,ast.Constant) and isinstance(arg.value,str)]
                self.assertNotIn('PATCH',methods,'Task creation cannot move existing deals')


if __name__=='__main__':unittest.main()
