"""HTTP identity/origin boundaries and sandbox route capabilities."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,AsyncMock,patch
import tenant_ai_testing_api as api
from tenant_web_routes import TenantWebContext,ai_agent_routes

class TestingAPI(unittest.IsolatedAsyncioTestCase):
    def request(self,method='POST',data=None,origin='https://crm.test',query=None):
        return SimpleNamespace(method=method,headers={'Origin':origin},content_type='application/json',match_info={'session_id':'private-id'},query=query or {},json=AsyncMock(return_value=data))
    async def test_missing_identity_and_cross_origin_never_execute(self):
        for profile,origin in [(None,'https://crm.test'),({'tenant_id':'own','telegram_id':11},'https://evil.test')]:
            with patch.object(api,'turn',new=AsyncMock()) as turn:
                response=await api.handler(lambda _:profile,'https://crm.test',Mock(),'turn')(self.request(origin=origin))
                self.assertEqual(response.status,403);turn.assert_not_called();self.assertEqual(response.headers['Cache-Control'],'no-store')
    async def test_expected_tenant_and_actor_required_before_execution(self):
        profile={'tenant_id':'own','telegram_id':11}
        for data in ({},{'expected_tenant_id':'foreign','expected_user_id':11},{'expected_tenant_id':'own','expected_user_id':22}):
            with patch.object(api,'control') as control:
                response=await api.handler(lambda _:profile,'https://crm.test',Mock(),'control')(self.request(data=data))
                self.assertEqual(response.status,409);control.assert_not_called()
        with patch.object(api,'read') as read:
            response=await api.handler(lambda _:profile,'https://crm.test',Mock(),'session')(self.request('GET',query={'expected_tenant_id':'foreign'}))
            self.assertEqual(response.status,409);read.assert_not_called()
    async def test_virtual_turn_receives_no_transport_or_browser_identity_override(self):
        profile={'tenant_id':'own','telegram_id':11};data={'expected_tenant_id':'own','expected_user_id':11,'text':'hello','request_id':'request'}
        with patch.object(api,'turn',new=AsyncMock(return_value={'simulation':True})) as turn:
            response=await api.handler(lambda _:profile,'https://crm.test',Mock(),'turn')(self.request(data=data))
            turn.assert_awaited_once_with(profile,'private-id',{'text':'hello','request_id':'request'})
            self.assertTrue(json.loads(response.body)['result']['simulation'])
    def test_virtual_testing_routes_do_not_receive_crm_capability(self):
        with patch('tenant_web_routes.testing_handler',return_value=Mock()) as factory:
            context=TenantWebContext(member=Mock(),origin='https://crm.test',logger=Mock(),crm_request=Mock(),wait_slot=Mock(),bot=Mock())
            ai_agent_routes(context)
            # The obsolete Kommo catalog route was removed when AI Testing
            # became transport-independent. Virtual sessions must not gain a
            # CRM client capability through any remaining route.
            self.assertEqual(len(factory.call_args_list),4)
            for call in factory.call_args_list:
                self.assertEqual(len(call.args),4)
