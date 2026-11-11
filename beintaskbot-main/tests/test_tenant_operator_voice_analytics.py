import asyncio
import json
import unittest
from unittest.mock import patch

from tenant_linear_policy import TenantLinearError
import tenant_operator_voice_analytics as voice_analytics


class AnalyticsVoiceAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_realtime_definitions_are_only_existing_read_tools(self):
        tools=voice_analytics.realtime_definitions()
        self.assertEqual({tool['name'] for tool in tools},voice_analytics._TOOLS)
        self.assertTrue(all(tool['type']=='function' and tool['strict'] is False for tool in tools))
        self.assertTrue(all(tool['parameters']['additionalProperties'] is False for tool in tools))
        self.assertFalse(any(tool['name'] in {'send_message','update_task','run_sql'} for tool in tools))

    async def test_executes_through_registry_with_authority_before_and_after(self):
        events=[]
        class Authority:
            profile={'tenant_id':'tenant','telegram_id':7}
            session_scope=('tenant',7,'channel','conversation')
            async def check(self,scope,stage):
                self.assert_scope=scope
                events.append(stage)
        adapter=voice_analytics.AnalyticsToolExecutor(Authority())
        with patch.object(voice_analytics,'execute',return_value={'read_only':True,'tasks':[]}) as execute:
            result=await adapter('search_tasks',{'query':'invoice'})
        execute.assert_called_once_with({'tenant_id':'tenant','telegram_id':7},'search_tasks',{'query':'invoice'})
        self.assertEqual(events,['analytics_tool','analytics_tool_result'])
        self.assertEqual(json.loads(result),{'read_only':True,'result':{'read_only':True,'tasks':[]}})

    async def test_unknown_tool_is_rejected_without_registry_call(self):
        class Authority:
            profile={'tenant_id':'tenant','telegram_id':7}
            session_scope=('tenant',7,'channel','conversation')
            async def check(self,*_):raise AssertionError('must not authorize unknown tool')
        adapter=voice_analytics.AnalyticsToolExecutor(Authority())
        with patch.object(voice_analytics,'execute') as execute:
            with self.assertRaises(TenantLinearError):await adapter('send_message',{})
        execute.assert_not_called()

    async def test_tool_results_are_bounded_and_errors_are_sanitized(self):
        class Authority:
            profile={'tenant_id':'tenant','telegram_id':7}
            session_scope=('tenant',7,'channel','conversation')
            async def check(self,*_):pass
        adapter=voice_analytics.AnalyticsToolExecutor(Authority())
        with patch.object(voice_analytics,'execute',return_value={'content':'x'*30000}):
            result=json.loads(await adapter('search_tasks',{'query':'invoice'}))
        self.assertEqual(result,{'read_only':True,'available':False,'reason':'result_too_large'})
        with patch.object(voice_analytics,'execute',side_effect=TenantLinearError('private provider text',503)):
            result=json.loads(await adapter('search_tasks',{'query':'invoice'}))
        self.assertEqual(result['result'],{'read_only':True,'available':False,'reason':'source_unavailable'})
        self.assertNotIn('private',str(result))


if __name__=='__main__':unittest.main()
