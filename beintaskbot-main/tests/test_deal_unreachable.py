"""Fixed deal routing action, extracted without bot startup or live writes."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class UnreachableDealTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'))
        tree.body=[node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='handle_api_action']
        cls.code=compile(tree,'<deal-api>','exec')

    def setUp(self):
        self.ns={'asyncio':asyncio,'web':SimpleNamespace(Request=dict,Response=dict,json_response=lambda data,status=200:(status,data)),
            'load_users':lambda:{'20':{}},'employee_access_profile':Mock(return_value={'active':True,'permissions':['deals']}),
            'lead_allowed_for_chat':Mock(return_value=True),'SOVDELESMELER_PIPELINE_ID':8329347,
            '_pipeline_stage_cache':{},
            'load_pipeline_stage_maps':Mock(return_value=({'postponed':123},{123:'TƏXİRƏ SALINIB'},[])),
            'update_lead_kommo':Mock(return_value={'id':7}),'invalidate_rufat_overview_cache':Mock(),
            '_clear_terminal_reentry':Mock(),'record_lead_pulse_event':Mock(),'logger':Mock()}
        exec(self.code,self.ns)
        async def data():return {'action':'deal_unreachable','lead_id':7,'pipeline_id':999,'status_id':999}
        self.request=SimpleNamespace(headers={'X-TG-User-ID':'20'},json=data)

    async def test_staff_can_move_only_to_fixed_pipeline_and_named_stage(self):
        status,body=await self.ns['handle_api_action'](self.request)
        self.assertEqual(status,200);self.assertTrue(body['success'])
        self.ns['update_lead_kommo'].assert_called_once_with(7,{'pipeline_id':8329347,'status_id':123})
        self.ns['load_pipeline_stage_maps'].assert_called_once_with(8329347,fallback=False)
        self.ns['invalidate_rufat_overview_cache'].assert_called_once()
        self.ns['record_lead_pulse_event'].assert_called_once_with(7,'deal_edit',pipeline_id=8329347,stage_key='postponed')

    async def test_missing_or_ambiguous_stage_never_mutates_deal(self):
        for names in ({123:'New'}, {123:'Təxirə salınıb',124:'Təxirə salınıb'}, {142:'Təxirə salınıb'}):
            self.ns['load_pipeline_stage_maps'].return_value=({},names,[])
            status,body=await self.ns['handle_api_action'](self.request)
            self.assertEqual(status,400);self.assertFalse(body['success'])
        self.ns['update_lead_kommo'].assert_not_called()

    async def test_hidden_page_inactive_employee_and_foreign_deal_are_denied(self):
        for access in ({'active':False,'permissions':['deals']},{'active':True,'permissions':[]}):
            self.ns['employee_access_profile'].return_value=access
            self.assertEqual((await self.ns['handle_api_action'](self.request))[0],403)
        self.ns['employee_access_profile'].return_value={'active':True,'permissions':['deals']}
        self.ns['lead_allowed_for_chat'].return_value=False
        self.assertEqual((await self.ns['handle_api_action'](self.request))[0],403)
        self.ns['update_lead_kommo'].assert_not_called()

    async def test_provider_failure_does_not_report_success_or_invalidate(self):
        self.ns['update_lead_kommo'].return_value=None
        status,body=await self.ns['handle_api_action'](self.request)
        self.assertEqual(status,502);self.assertFalse(body['success'])
        self.ns['invalidate_rufat_overview_cache'].assert_not_called()


if __name__=='__main__':unittest.main()
