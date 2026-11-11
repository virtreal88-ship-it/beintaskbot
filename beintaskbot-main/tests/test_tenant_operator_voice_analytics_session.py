import time
import uuid
import unittest
from unittest.mock import AsyncMock

from tenant_operator_voice_pilot import PilotAdmission
from tenant_operator_voice_session import Session, Refused


class Authority:
    async def check(self,*_args):return True


class Lease:
    async def claim(self,*_args):return 1
    async def assert_current(self,*_args):return True
    async def release(self,*_args):return True


class Context:
    async def revalidate(self,_scope):return True


class Client:
    async def send(self,_event):return None


class Provider:
    mock_only=True
    def __init__(self):self.events=[];self.outputs=[]
    async def bind_pilot_claim(self,*_args):return None
    async def send(self,event):self.events.append(event);return True
    async def send_function_call_output(self,call_id,output):
        self.outputs.append((call_id,output));return True
    async def close(self):return None


class Tools:
    names=frozenset({'search_tasks'})
    tools=[{'type':'function','name':'search_tasks','description':'read tasks','parameters':{'type':'object'}}]
    def __init__(self):self.calls=[]
    async def __call__(self,name,arguments):
        self.calls.append((name,arguments));return '{"read_only":true,"result":{"tasks":[]}}'


class VoiceAnalyticsToolSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope=('tenant',7,'channel','dialog');self.session_id=str(uuid.uuid4())
        self.authority=Authority();self.lease=Lease();self.provider=Provider();self.tools=Tools()
        self.policy=PilotAdmission(self.authority,self.lease,self.scope,self.session_id)
        await self.policy.admit(self.scope,self.session_id,1)
        self.session=Session(self.scope,authority=self.authority,ledger=None,lease=self.lease,
            context=Context(),provider=self.provider,client=Client(),session_id=self.session_id,
            pilot_policy=self.policy,duplex=True,analytics_tools=self.tools,max_seconds=60)
        self.session.closed=False;self.session.started=time.monotonic();self.session.fence=1
        self.session._transactional_audio_binding=lambda:False
        self.session._remember_context_item('context_1',usable=True,role='user')
        self.session._remember_context_item('user_1',usable=True,role='user')
        self.session.current_reservation={'request_id':str(uuid.uuid4())}
        self.session.current_input_item_id='user_1';self.session.current_response_kind='user'
        self.session.response_inflight=True;self.session.provider_response_id='response_1'

    async def test_function_call_executes_read_tool_and_dispatches_one_spoken_followup(self):
        call_item={'id':'function_1','type':'function_call','call_id':'call_1','name':'search_tasks',
                   'arguments':'{"query":"invoice"}'}
        await self.session.receive_provider({'type':'response.output_item.added','response_id':'response_1',
                                             'output_index':0,'item':{**call_item,'arguments':''}})
        await self.session.receive_provider({'type':'response.function_call_arguments.done','response_id':'response_1',
            'item_id':'function_1','call_id':'call_1','arguments':'{"query":"invoice"}'})
        await self.session.receive_provider({'type':'response.output_item.done','response_id':'response_1',
                                             'output_index':0,'item':call_item})
        await self.session.receive_provider({'type':'response.done','response':{'id':'response_1','status':'completed',
            'output':[call_item],'usage':{}}})
        self.assertEqual(self.tools.calls,[('search_tasks',{'query':'invoice'})])
        self.assertEqual(self.provider.outputs,[('call_1','{"read_only":true,"result":{"tasks":[]}}')])
        self.assertEqual(self.provider.events,[])
        output=self.provider.outputs[0][1]
        await self.session.receive_provider({'type':'conversation.item.created','item':{
            'id':'function_output_1','type':'function_call_output','call_id':'call_1','output':output}})
        followup=[event for event in self.provider.events if event.get('type')=='response.create']
        self.assertEqual(len(followup),1)
        self.assertEqual(followup[0]['response']['tool_choice'],'none')
        self.assertEqual([ref['id'] for ref in followup[0]['response']['input']],
                         ['context_1','user_1','function_1','function_output_1'])
        self.assertEqual(self.session.current_response_kind,'tool_followup')

    async def test_new_speech_observed_before_tool_result_discards_stale_call(self):
        self.session._observed_speech_epoch=1
        call_item={'id':'function_1','type':'function_call','call_id':'call_1','name':'search_tasks',
                   'arguments':'{"query":"invoice"}'}
        await self.session.receive_provider({'type':'response.done','response':{'id':'response_1','status':'completed',
            'output':[call_item],'usage':{}}})
        self.assertEqual(self.tools.calls,[])
        self.assertEqual(self.provider.outputs,[])
        self.assertIsNone(self.session._pending_analytics_call)

    async def test_undeclared_tool_and_duplicate_argument_keys_are_rejected(self):
        with self.assertRaises(Refused):
            self.session._parse_analytics_arguments('{"query":"a","query":"b"}')
        self.session._parse_analytics_arguments=AsyncMock(side_effect=AssertionError('not used'))
        with self.assertRaises(Refused):
            await self.session._receive_provider({'type':'response.output_item.added','response_id':'response_1',
                'item':{'id':'function_1','type':'function_call','call_id':'call_1','name':'send_message','arguments':'{}'}})


if __name__=='__main__':unittest.main()
