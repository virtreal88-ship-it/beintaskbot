import json
import pytest

from tenant_ai_agent_no_answer import assess, selected_action, validate_policy
from tenant_linear_policy import TenantLinearError
from tenant_ai_agent_worker import _expert_evidence, _apply_no_answer_policy, _customer_unsolved


def test_new_policy_defaults_are_safe_and_existing_policy_can_remain_disabled():
    assert validate_policy(None) == {
        'enabled': False, 'action': 'stop', 'fallback_action': 'stop',
        'max_clarifications': 1, 'stop_reply': '', 'developer_id': None,
    }
    assert validate_policy({'enabled': False})['enabled'] is False


@pytest.mark.parametrize('value', [
    {'enabled': True, 'action': 'clarify', 'max_clarifications': 0},
    {'enabled': True, 'action': 'clarify', 'max_clarifications': 4},
    {'enabled': True, 'action': 'invented'},
    {'enabled': True, 'action': []},
    {'enabled': True, 'fallback_action': {}},
    {'enabled': True, 'fallback_action': 'create_task'},
    {'stop_reply': 'x' * 501},
    {'enabled': 1},
    {'enabled': True, 'action': 'consult_developer'},
    {'enabled': True, 'action': 'consult_developer', 'developer_id': '11'},
])
def test_policy_rejects_unsafe_or_out_of_range_configuration(value):
    with pytest.raises(TenantLinearError):
        validate_policy(value)


def test_empty_retrieval_is_deterministically_insufficient_even_if_model_claims_supported():
    raw = json.dumps({'status': 'supported', 'reply': 'A verified reply',
        'clarification_question': '', 'citations': []})
    assert assess(raw, [])['status'] == 'insufficient'


def test_supported_requires_an_exact_quote_from_a_retrieved_source():
    knowledge = [{'source_id': 'source-a', 'content': 'Returns are accepted within fourteen days with a receipt.'}]
    valid = json.dumps({'status': 'supported', 'reply': 'You can return it within fourteen days.',
        'clarification_question': '', 'citations': [{'source_id': 'source-a', 'quote': 'accepted within fourteen days with a receipt'}]})
    forged = json.dumps({'status': 'supported', 'reply': 'Returns are always accepted.',
        'clarification_question': '', 'citations': [{'source_id': 'source-a', 'quote': 'always accepted'}]})
    cross_tenant = json.dumps({'status': 'supported', 'reply': 'You can return it.',
        'clarification_question': '', 'citations': [{'source_id': 'other-tenant-source', 'quote': 'accepted within fourteen days'}]})
    assert assess(valid, knowledge)['status'] == 'supported'
    assert assess(forged, knowledge)['status'] == 'insufficient'
    assert assess(cross_tenant, knowledge)['status'] == 'insufficient'


def test_conflict_requires_two_distinct_retrieved_sources_with_exact_quotes():
    knowledge = [
        {'source_id': 'a', 'content': 'Delivery takes two business days in Baku.'},
        {'source_id': 'b', 'content': 'Delivery takes five business days in Baku.'},
    ]
    raw = json.dumps({'status': 'conflicting', 'reply': '', 'clarification_question': '', 'citations': [
        {'source_id': 'a', 'quote': 'takes two business days in Baku'},
        {'source_id': 'b', 'quote': 'takes five business days in Baku'},
    ]})
    one_source = json.dumps({'status': 'conflicting', 'reply': '', 'clarification_question': '', 'citations': [
        {'source_id': 'a', 'quote': 'takes two business days in Baku'},
    ]})
    same_quote = json.dumps({'status': 'conflicting', 'reply': '', 'clarification_question': '', 'citations': [
        {'source_id': 'a', 'quote': 'Delivery takes two business days'},
        {'source_id': 'b', 'quote': 'Delivery takes two business days'},
    ]})
    assert assess(raw, knowledge)['status'] == 'conflicting'
    assert assess(one_source, knowledge)['status'] == 'insufficient'
    assert assess(same_quote, knowledge)['status'] == 'insufficient'


@pytest.mark.parametrize('action', ['stop', 'handoff', 'create_task', 'clarify'])
def test_configured_no_answer_actions_are_selected_only_for_evidence_signals(action):
    policy={'enabled':True,'action':action,'fallback_action':'handoff','max_clarifications':2}
    assert selected_action(policy,'missing_knowledge')['action']==action
    assert selected_action(policy,'not_a_signal')['action']=='continue'


def test_developer_consultation_requires_explicit_designated_employee():
    policy={'enabled':True,'action':'consult_developer','developer_id':11,'fallback_action':'handoff'}
    assert selected_action(policy,'missing_knowledge')['action']=='consult_developer'


def test_clarification_limit_uses_configured_fallback_and_expert_requires_evidence():
    clarify={'enabled':True,'action':'clarify','fallback_action':'handoff','max_clarifications':1}
    assert selected_action(clarify,'unsupported_evidence',clarification_count=1)=={
        'action':'handoff','reason':'clarification_limit_reached'}
    expert={'enabled':True,'action':'call_expert','fallback_action':'stop'}
    assert selected_action(expert,'missing_knowledge',expert_result={'status':'timeout'})['action']=='stop'
    assert selected_action(expert,'missing_knowledge',expert_result={'status':'completed','result':{'requires_human':False},
        'grounded_sources':[{'source_id':'expert-source','quote':'Observed status code from an approved source'}]})=={
        'action':'continue','reason':'expert_evidence_available'}
    assert selected_action(expert,'missing_knowledge',expert_result={'status':'completed','result':{'requires_human':False},
        'specialists':[{'status':'completed','grounded_sources':[{'source_id':'expert-source',
            'quote':'Observed status code from an approved source'}]}]})=={
        'action':'continue','reason':'expert_evidence_available'}


def test_expert_prose_is_reused_only_with_server_verified_source_quotes():
    result={'status':'completed','result':{'requires_human':False,
        'evidence':['A model observation without source validation']},
        'grounded_sources':[{'source_id':'expert-policy','title':'Returns policy',
          'quote':'Returns accepted within fourteen days with a receipt.'}]}
    assert _expert_evidence(result)==[{'source_id':'expert-policy','title':'Returns policy',
        'content':'Returns accepted within fourteen days with a receipt.'}]
    result['grounded_sources']=[]
    assert _expert_evidence(result) is None
    result['specialists']=[{'status':'completed','grounded_sources':[
        {'source_id':'expert-policy','title':'Returns policy','quote':'Returns accepted within fourteen days with a receipt.'}]}]
    assert _expert_evidence(result)==[{'source_id':'expert-policy','title':'Returns policy',
        'content':'Returns accepted within fourteen days with a receipt.'}]


@pytest.mark.parametrize('text', [
    'Bu hələ də işləmir, problem həll olunmadı.',
    'Не помогло, проблема не решена.',
    "It still does not work; it didn't work.",
])
def test_customer_unsolved_signal_requires_prior_agent_reply(text):
    assert _customer_unsolved([
        {'speaker':'CLIENT','text':'Original question'},
        {'speaker':'AGENT','text':'Try these steps'},
        {'speaker':'CLIENT','text':text},
    ]) is True
    assert _customer_unsolved([{'speaker':'CLIENT','text':text}]) is False


@pytest.mark.asyncio
async def test_no_answer_stop_uses_durable_pause_and_does_not_send(monkeypatch):
    import tenant_ai_agent_no_answer as policy_module
    import tenant_ai_agent_worker as worker
    calls=[]
    async def fake_to_thread(fn,*args,**kwargs):
        return fn(*args,**kwargs)
    monkeypatch.setattr(worker.asyncio,'to_thread',fake_to_thread)
    monkeypatch.setattr(policy_module,'stop_without_answer',lambda *args:calls.append(args) or True)
    class Logger:
        def info(self,*args,**kwargs): pass
    result=await _apply_no_answer_policy(
        {'tenant_id':'t','agent_id':'a','provider':'connect','conversation_id':'c'},
        {'mode':'automatic','response_policy':{}},
        {'enabled':True,'action':'stop','fallback_action':'stop','max_clarifications':1,'stop_reply':''},
        'missing_knowledge',{'external_id':'m'},'Where is my order?',[],[],None,None,Logger())
    assert result['handled'] is True
    assert len(calls)==1 and calls[0][2].startswith('ai_no_answer:')


@pytest.mark.asyncio
async def test_no_answer_employee_task_falls_back_without_permission(monkeypatch):
    import tenant_ai_agent_no_answer as policy_module
    import tenant_ai_agent_worker as worker
    calls=[]
    async def fake_to_thread(fn,*args,**kwargs):
        return fn(*args,**kwargs)
    monkeypatch.setattr(worker.asyncio,'to_thread',fake_to_thread)
    monkeypatch.setattr(policy_module,'stop_without_answer',lambda *args:calls.append(args) or True)
    monkeypatch.setattr(worker,'runtime_flag',lambda _flag:True)
    monkeypatch.setattr(worker,'handoff',lambda *args:pytest.fail('must not create a ticket when CRM task permission is absent'))
    class Logger:
        def info(self,*args,**kwargs): pass
    result=await _apply_no_answer_policy(
        {'tenant_id':'t','agent_id':'a','provider':'kommo','conversation_id':'c'},
        {'mode':'automatic','auto_send_enabled':True,'crm_actions_enabled':False},
        {'enabled':True,'action':'create_task','fallback_action':'stop','max_clarifications':1,'stop_reply':''},
        'unsupported_evidence',{'external_id':'m'},'Can you help?',[],[],lambda *a:None,None,Logger())
    assert result['handled'] is True
    assert len(calls)==1 and 'employee_request_not_permitted' in calls[0][2]


@pytest.mark.asyncio
async def test_manual_developer_consultation_handoffs_then_uses_internal_only_receipt(monkeypatch):
    import tenant_ai_agent_worker as worker
    import tenant_internal_consultations as consultations
    calls=[]
    async def fake_to_thread(fn,*args,**kwargs): return fn(*args,**kwargs)
    monkeypatch.setattr(worker.asyncio,'to_thread',fake_to_thread)
    async def current_handoff(job,reservation,source,reason):
        calls.append(('handoff',reason));return True
    monkeypatch.setattr(worker,'_handoff_current_connect',current_handoff)
    monkeypatch.setattr(consultations,'create_from_ai',lambda job,**kwargs:calls.append(('consultation',kwargs)) or {'created':True,'status':'waiting_specialist','customer_message_sent':False})
    class Logger:
        def info(self,*args,**kwargs): pass
    job={'tenant_id':'00000000-0000-0000-0000-000000000101','agent_id':'00000000-0000-0000-0000-000000000301','provider':'connect','channel_id':'00000000-0000-0000-0000-000000000201','conversation_id':'client','event_fingerprint':'webhook-1'}
    result=await worker._apply_no_answer_policy(job,{'run_id':None},{'enabled':True,'action':'consult_developer','developer_id':11,'fallback_action':'handoff','max_clarifications':1,'stop_reply':''},'missing_knowledge',{'external_id':'m1'},'Printer is not working',[],[],None,None,Logger())
    assert result['handled'] and result['consultation']['customer_message_sent'] is False
    assert [item[0] for item in calls]==['handoff','consultation']
    assert calls[1][1]['recipient_id']==11 and calls[1][1]['evidence']['source_external_id']=='m1'


@pytest.mark.asyncio
async def test_stale_generated_consultation_never_creates_case_or_group_delivery(monkeypatch):
    import tenant_ai_agent_worker as worker
    import tenant_internal_consultations as consultations
    import tenant_ai_agent_run_store as run_store
    async def fake_to_thread(fn,*args,**kwargs): return fn(*args,**kwargs)
    monkeypatch.setattr(worker.asyncio,'to_thread',fake_to_thread)
    async def stale_handoff(*_a,**_k): return False
    monkeypatch.setattr(worker,'_handoff_current_connect',stale_handoff)
    monkeypatch.setattr(run_store,'finish_superseded',lambda *_a,**_k: True)
    monkeypatch.setattr(consultations,'create_from_ai',lambda *_a,**_k:
        pytest.fail('Stale generated consultation must not create a case'))
    class Logger:
        def info(self,*args,**kwargs): pass
    job={'tenant_id':'00000000-0000-0000-0000-000000000101','agent_id':'00000000-0000-0000-0000-000000000301',
        'provider':'connect','channel_id':'00000000-0000-0000-0000-000000000201','conversation_id':'client',
        'event_fingerprint':'webhook-stale'}
    result=await worker._apply_no_answer_policy(job,{'run_id':'run','conversation_revision':3},
        {'enabled':True,'action':'consult_developer','developer_id':11,'fallback_action':'stop',
         'max_clarifications':1,'stop_reply':''},'unsupported_evidence',{'external_id':'source-1'},
        'reported issue',[],[],None,None,Logger())
    assert result=={'handled':True,'stale':True}


@pytest.mark.asyncio
@pytest.mark.parametrize('group_state,expected_ack',[('sent','sent'),('unknown',None)])
async def test_initial_consultation_ack_requires_group_receipt(monkeypatch,group_state,expected_ack):
    import tenant_ai_agent_worker as worker
    import tenant_internal_consultations as consultations
    import tenant_ai_pilot as pilot
    import tenant_consultation_group as group_flow
    calls=[]
    async def fake_to_thread(fn,*args,**kwargs): return fn(*args,**kwargs)
    monkeypatch.setattr(worker.asyncio,'to_thread',fake_to_thread)
    async def current_handoff(*_args):calls.append('handoff');return True
    monkeypatch.setattr(worker,'_handoff_current_connect',current_handoff)
    monkeypatch.setattr(pilot,'manual_live_job',lambda _job:True)
    monkeypatch.setattr(consultations,'create_from_ai',lambda *_args,**_kwargs:
        calls.append('case') or {'id':'consultation','status':'waiting_specialist','customer_message_sent':False})
    monkeypatch.setattr(group_flow,'deliver_pilot_consultation_to_group',lambda *_args,**_kwargs:
        calls.append('group') or {'state':group_state})
    monkeypatch.setattr(group_flow,'acknowledge_pilot_consultation_to_customer',lambda *_args,**_kwargs:
        calls.append('ack') or {'state':'sent'})
    class Logger:
        def info(self,*args,**kwargs):pass
    job={'tenant_id':'00000000-0000-0000-0000-000000000101','agent_id':'00000000-0000-0000-0000-000000000301',
        'provider':'connect','channel_id':'00000000-0000-0000-0000-000000000201','conversation_id':'client',
        'event_fingerprint':'webhook-ack'}
    policy={'enabled':True,'action':'consult_developer','developer_id':11,'fallback_action':'stop',
        'max_clarifications':1,'stop_reply':''}
    result=await worker._apply_no_answer_policy(job,{'run_id':'run','conversation_revision':1},policy,
        'unsupported_evidence',{'external_id':'source-1'},'customer report',[],[],None,None,Logger())
    receipt=result['consultation']
    assert receipt['pilot_group_delivery']['state']==group_state
    assert ('pilot_customer_ack' in receipt)==(expected_ack is not None)
    if expected_ack:
        assert receipt['pilot_customer_ack']['state']==expected_ack
        assert calls==['handoff','case','group','ack']
    else:
        assert calls==['handoff','case','group']

