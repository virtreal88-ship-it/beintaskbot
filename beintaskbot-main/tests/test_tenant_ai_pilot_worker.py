import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('group_state,expected_finish', [('sent', 'superseded'), ('unknown', 'unknown')])
async def test_initial_consultation_runs_through_worker_and_requires_delivery_receipts(
        monkeypatch, group_state, expected_finish):
    import tenant_ai_agent_worker as worker
    import tenant_ai_agent_connect as connect
    import tenant_ai_agent_run_store as run_store
    import tenant_ai_pilot as pilot
    import tenant_connect_images as images
    import tenant_connect_documents as documents
    import tenant_connect_videos as videos
    import tenant_internal_consultations as consultations
    import tenant_consultation_group as group_flow

    source = {'external_id': 'source-1', 'direction': 'incoming', 'body': 'Alış 1 qutu 50 AZN, ekranda 49,9992 göstərilir.',
        'message_type': 'text', 'happened_at': '2026-10-31T04:43:44+00:00', 'media_url': '', 'has_attachment': False}
    config = {'scopes': ['channel-1'], 'schedule': {}, 'chat_model': 'test-model', 'agent_model': '',
        'response_policy': {}}
    reservation = {'run_id': 'run-1', 'mode': 'automatic', 'auto_send_enabled': True,
        'conversation_revision': 3, 'response_policy': config['response_policy'],
        'agent_language': 'az', 'agent_style': '', 'api_key': 'fixture', 'company_instructions': '',
        'agent_instructions': '', 'scope_instructions': ''}
    events = []

    async def inline_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(worker.asyncio, 'to_thread', inline_to_thread)
    monkeypatch.setattr(worker, 'assert_job_allowed', lambda *_: None)
    monkeypatch.setattr(connect, 'history', lambda *_: [source])
    monkeypatch.setattr(connect, 'scope_matches', lambda scopes, channel: channel in scopes)
    monkeypatch.setattr(connect, 'group_context', lambda *_: [source])
    monkeypatch.setattr(connect, 'finish_sent_after_reply_handoff', lambda *_: None)
    monkeypatch.setattr(run_store, 'load_agent_config', lambda *_a, **_k: (config, None))
    monkeypatch.setattr(worker, 'reserve_run', lambda *_a, **_k: reservation)
    monkeypatch.setattr(worker, 'enabled_triggers', lambda *_: [])
    monkeypatch.setattr(worker, 'matching_triggers', lambda *_a, **_k: [])
    monkeypatch.setattr(worker, 'handoff_reason', lambda *_: '')
    monkeypatch.setattr(worker, '_next_open', lambda *_: None)
    monkeypatch.setattr(worker, 'knowledge_context', lambda *_a, **_k: [])
    monkeypatch.setattr(worker, 'record_token_usage', lambda *_a, **_k: None)
    monkeypatch.setattr(worker, 'generate', lambda *_a, **_k: (json.dumps({
        'action': 'consult_developer', 'message': 'Please investigate this report.',
        'evidence_quote': 'Alış 1 qutu 50 AZN', 'missing_items': [], 'request_media': False}),
        {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}))
    async def empty_images(*_a, **_k): return [], set()
    async def empty_documents(*_a, **_k): return []
    async def empty_videos(*_a, **_k): return {}, {}, {}
    monkeypatch.setattr(images, 'prepare', empty_images)
    monkeypatch.setattr(documents, 'prepare', empty_documents)
    monkeypatch.setattr(videos, 'prepare', empty_videos)
    monkeypatch.setattr(worker, 'history', lambda *_a, **_k: ([{'speaker': 'CLIENT', 'text': source['body']}], 0))
    monkeypatch.setattr(pilot, 'manual_live_job', lambda _job: True)
    monkeypatch.setattr(pilot, 'configured_group_scope', lambda: {'tenant_id':'tenant-1','channel_id':'channel-1','conversation_id':'developers@g.us'})
    async def guarded_handoff(*_a, **_k): events.append('handoff'); return True
    monkeypatch.setattr(worker, '_handoff_current_connect', guarded_handoff)
    create_calls=[]
    monkeypatch.setattr(consultations, 'create_from_ai', lambda *_a, **kwargs:
        create_calls.append(kwargs) or events.append('case') or {'id': 'case-1', 'status': 'waiting_specialist'})
    monkeypatch.setattr(group_flow, 'deliver_pilot_consultation_to_group', lambda *_a, **_k:
        events.append('group') or {'state': group_state})
    monkeypatch.setattr(group_flow, 'acknowledge_pilot_consultation_to_customer', lambda *_a, **_k:
        events.append('customer_ack') or {'state': 'sent'})
    finishes = []
    monkeypatch.setattr(worker, 'finish_superseded', lambda *_a, **_k: finishes.append('superseded'))
    monkeypatch.setattr(worker, 'finish_unknown', lambda *_a, **_k: finishes.append('unknown'))

    job = {'tenant_id': 'tenant-1', 'agent_id': 'agent-1', 'provider': 'connect', 'lead_id': 1,
        'channel_id': 'channel-1', 'conversation_id': 'client-1', 'event_fingerprint': 'hook-1',
        'payload': {'message_id': source['external_id']}}
    class Logger:
        def info(self, *_a, **_k): pass
        def warning(self, *_a, **_k): pass

    await worker.process_connect_one(job, Logger())
    assert events == (['handoff', 'case', 'group', 'customer_ack'] if group_state == 'sent'
        else ['handoff', 'case', 'group'])
    assert create_calls and create_calls[0]['destination']=='pilot_group' and create_calls[0]['recipient_id'] is None
    assert finishes == [expected_finish]


def _install_initial_worker_fakes(monkeypatch, *, generated, send_result='sent-1', history_rows=None,
                                  live_values=None, send_error=None, knowledge=None):
    import tenant_ai_agent_worker as worker
    import tenant_ai_agent_connect as connect
    import tenant_ai_agent_run_store as run_store
    import tenant_ai_pilot as pilot
    import tenant_connect_images as images
    import tenant_connect_documents as documents
    import tenant_connect_videos as videos
    import tenant_connect_transport as transport
    import tenant_ai_agent_native_crm_actions as native

    source = {'external_id':'source-1','direction':'incoming',
        'body':'The app still shows an unexpected value.','message_type':'text',
        'happened_at':'2026-10-31T04:43:44+00:00','media_url':'','has_attachment':False}
    rows = history_rows or [source]
    config = {'scopes':['channel-1'],'schedule':{},'chat_model':'test-model','agent_model':'',
        'response_policy':{}}
    reservation = {'run_id':'run-1','mode':'automatic','auto_send_enabled':True,
        'conversation_revision':3,'response_policy':{},'agent_language':'en','agent_style':'',
        'api_key':'fixture','company_instructions':'','agent_instructions':'','scope_instructions':''}
    sends=[]

    async def inline_to_thread(fn, *args, **kwargs): return fn(*args, **kwargs)
    async def no_expert(*_args, **_kwargs): return None
    async def empty_images(*_args, **_kwargs): return [], set()
    async def empty_documents(*_args, **_kwargs): return []
    async def empty_videos(*_args, **_kwargs): return {}, {}, {}
    def send(*args, **kwargs):
        sends.append((args,kwargs))
        if send_error: raise send_error
        return send_result
    live_iter=iter(live_values or [True,True])

    monkeypatch.setattr(worker.asyncio,'to_thread',inline_to_thread)
    monkeypatch.setattr(worker,'assert_job_allowed',lambda *_:None)
    monkeypatch.setattr(worker,'request_expert',no_expert)
    monkeypatch.setattr(connect,'history',lambda *_:rows)
    monkeypatch.setattr(connect,'scope_matches',lambda scopes,channel:channel in scopes)
    monkeypatch.setattr(connect,'group_context',lambda *_: [source])
    monkeypatch.setattr(run_store,'load_agent_config',lambda *_a,**_k:(config,None))
    monkeypatch.setattr(worker,'reserve_run',lambda *_a,**_k:reservation)
    monkeypatch.setattr(worker,'enabled_triggers',lambda *_:[])
    monkeypatch.setattr(worker,'matching_triggers',lambda *_a,**_k:[])
    monkeypatch.setattr(worker,'handoff_reason',lambda *_:'')
    monkeypatch.setattr(worker,'_next_open',lambda *_:None)
    monkeypatch.setattr(worker,'knowledge_context',lambda *_a,**_k:knowledge or [])
    monkeypatch.setattr(worker,'record_token_usage',lambda *_a,**_k:None)
    monkeypatch.setattr(worker,'generate',lambda *_a,**_k:(generated,{'input_tokens':1,'output_tokens':1,'total_tokens':2}))
    monkeypatch.setattr(worker,'history',lambda *_a,**_k:([{'speaker':'CLIENT','text':source['body']}],0))
    monkeypatch.setattr(worker,'_handoff_current_connect',lambda *_a,**_k:True)
    async def no_native_action(*_a,**_k): return {}
    monkeypatch.setattr(native,'execute',no_native_action)
    monkeypatch.setattr(transport,'send_ai_reply_if_active',send)
    monkeypatch.setattr(pilot,'manual_live_job',lambda _job:next(live_iter,False))
    monkeypatch.setattr(images,'prepare',empty_images)
    monkeypatch.setattr(documents,'prepare',empty_documents)
    monkeypatch.setattr(videos,'prepare',empty_videos)
    monkeypatch.setattr(connect,'finish_sent_after_reply_handoff',lambda *_:None)
    monkeypatch.setattr(worker,'finish_sent',lambda *_a,**_k:None)
    finishes=[]
    monkeypatch.setattr(worker,'finish_superseded',lambda *_a,**_k:finishes.append(('superseded',_a[2] if len(_a)>2 else '')))
    monkeypatch.setattr(worker,'finish_unknown',lambda *_a,**_k:finishes.append(('unknown',_a[2] if len(_a)>2 else '')))
    monkeypatch.setattr(worker,'skip_job',lambda *_a,**_k:finishes.append(('skipped',_a[1] if len(_a)>1 else '')))
    diagnostics=[]
    logger=SimpleNamespace(info=lambda message,*args:diagnostics.append(message % args if args else message),
                           warning=lambda message,*args:diagnostics.append(message % args if args else message),
                           error=lambda message,*args:diagnostics.append(message % args if args else message))
    job={'tenant_id':'tenant-1','agent_id':'agent-1','provider':'connect','lead_id':1,
        'channel_id':'channel-1','conversation_id':'client-1','event_fingerprint':'hook-1',
        'payload':{'message_id':source['external_id']}}
    return worker,job,logger,source,sends,finishes,diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize(('generated','diagnostic'),[
    ('not json','pilot_initial_invalid_json'),
    ('{"action":"clarify"}','pilot_initial_invalid_schema'),
    (json.dumps({'action':'clarify','message':'Müraciətinizi proqramçılara ötürdüm. Hansı versiyadan istifadə edirsiniz?',
        'evidence_quote':'','missing_items':['version'],'request_media':False}),
        'pilot_initial_forbidden_claim'),
    (json.dumps({'action':'reply','message':'Rounding can affect the displayed total.',
        'evidence_quote':'A rounding issue may affect the display.','missing_items':[],'request_media':False}),
        'pilot_initial_unverified_paraphrase'),
    (json.dumps({'action':'reply','message':'This is a rounding defect.',
        'evidence_quote':'This is a rounding defect.','missing_items':[],'request_media':False}),
        'pilot_initial_forbidden_claim'),
])
async def test_initial_parser_rejection_sends_only_server_owned_clarification(
        monkeypatch,generated,diagnostic):
    kb=([{'content':'A rounding issue may affect the display.'}]
        if diagnostic=='pilot_initial_unverified_paraphrase' else None)
    worker,job,logger,_source,sends,finishes,diagnostics=_install_initial_worker_fakes(
        monkeypatch,generated=generated,knowledge=kb)
    await worker.process_connect_one(job,logger)
    assert not diagnostics or any('initial_triage_rejected' in line for line in diagnostics), diagnostics
    assert len(sends)==1, (finishes, diagnostics)
    message=sends[0][0][4]
    assert message=='Please add any details that are not already in the conversation and could help clarify the issue.'
    assert 'programmers' not in message and 'rounding' not in message
    assert any(diagnostic in line for line in diagnostics)
    from tenant_ai_reliability import reason
    reason_code,label=reason(diagnostic)
    assert reason_code==diagnostic and label and 'внутренняя ошибка' not in label.casefold()
    assert finishes==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['newer_inbound','expired_attestation','takeover','duplicate_refused','unknown_send'])
async def test_fallback_obeys_worker_guards_and_never_retries_uncertain_or_refused_send(monkeypatch,case):
    generated='not json'
    source={'external_id':'source-1','direction':'incoming','body':'The app still shows an unexpected value.',
        'message_type':'text','happened_at':'2026-10-31T04:43:44+00:00','media_url':'','has_attachment':False}
    histories=[source, {'external_id':'newer-1','direction':'incoming','body':'new update',
        'message_type':'text','happened_at':'2026-10-31T04:44:00+00:00'}] if case=='newer_inbound' else None
    live_values=[True,False] if case=='expired_attestation' else [True,True]
    send_result=None if case in {'takeover','duplicate_refused'} else 'sent-1'
    send_error=RuntimeError('provider result unknown') if case=='unknown_send' else None
    worker,job,logger,_source,sends,finishes,_diagnostics=_install_initial_worker_fakes(
        monkeypatch,generated=generated,send_result=send_result,history_rows=histories,
        live_values=live_values,send_error=send_error)
    await worker.process_connect_one(job,logger)
    if case in {'newer_inbound','expired_attestation'}:
        assert sends==[]
    else:
        assert len(sends)==1
    if case=='unknown_send':
        assert finishes==[('unknown','connect_send_unknown')]
    elif case=='newer_inbound':
        assert finishes==[('skipped','newer_connect_message')]
    elif case=='expired_attestation':
        assert finishes==[('superseded','automatic_processing_disabled')]
    else:
        assert finishes==[('superseded','connect_human_takeover')]
