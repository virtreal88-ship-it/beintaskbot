import json

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
