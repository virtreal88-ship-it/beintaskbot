"""Native signed CASE replies and real worker gates; external boundaries only.

The existing Automatic/Manual pilot send fence intentionally remains intact.
An internal group draft is not evidence that a group message was delivered.
"""
import asyncio
import hashlib
import hmac
import importlib
import json
import logging
import uuid
from unittest.mock import Mock

import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, CA, AGENT, PROFILE
from test_summary_language_integration import language_db, _policy, _case, AZ, RU, SUMMARY

import tenant_ai_agent_actions as actions
import tenant_ai_agent_connect as connect
import tenant_ai_agent_run_store as runs
import tenant_ai_agent_worker as worker
import tenant_ai_pilot as pilot
import tenant_chat_ai_provider as provider
import tenant_connect_transport as transport
import tenant_consultation_group as group_flow
import tenant_internal_consultations as internal


CUSTOMER = '15550007777@s.whatsapp.net'
GROUP = '120363400000999@g.us'
SECRET = 'synthetic-language-worker-signature'
LOGGER = logging.getLogger(__name__)
REPLIES = {
    'group_answer': 'The customer cannot open the sales screen. The developer plans to investigate tomorrow.',
    'customer_question': 'Zəhmət olmasa proqramın versiyasını bildirin.',
    'customer_update': 'Müraciət qəbul edilib. Sabah yoxlanılması planlaşdırılır; zəhmət olmasa proqram versiyasını bildirin.',
}


@pytest.fixture
def group_worker(language_db, monkeypatch):
    f = language_db
    # Bind every database entry point to independent connections in the same
    # disposable native database. Do not replace authority/admission functions.
    for name in (
        'tenant_platform', 'tenant_ai_agents_store', 'tenant_ai_agent_actions',
        'tenant_ai_agent_connect', 'tenant_ai_agent_admission', 'tenant_consultation_group',
        'tenant_internal_consultations', 'tenant_public_case', 'tenant_connect_transport',
        'tenant_connect_voice', 'tenant_connect_images', 'tenant_connect_documents',
        'tenant_connect_videos', 'tenant_dialog_audio_store', 'tenant_knowledge_live_queue',
        'tenant_ai_agent_worker', 'tenant_ai_legacy_permit',
    ):
        module = importlib.import_module(name)
        if hasattr(module, '_connect'):
            monkeypatch.setattr(module, '_connect', f['factory'])
    for name in ('AI_AGENTS_AUTO_SEND_ENABLED', 'AI_AGENTS_PILOT_SCOPE_ENABLED',
                 'AI_AGENTS_CRM_ENABLED', 'AI_NATIVE_CRM_ACTIONS_ENABLED', 'AI_SIMULATION_ENABLED'):
        monkeypatch.setenv(name, 'false')
    monkeypatch.setenv('AI_AGENTS_ENABLED', 'true')
    monkeypatch.setenv('AI_AGENTS_SCOPED_LIVE_ENABLED', 'true')
    monkeypatch.setenv('STAGING_MODE', 'false')
    for name, value in (
        ('AI_AGENTS_PILOT_TENANT_ID', A), ('AI_AGENTS_PILOT_CHANNEL_ID', CA),
        ('AI_AGENTS_PILOT_CONVERSATION_ID', CUSTOMER), ('AI_AGENTS_PILOT_GROUP_CONVERSATION_ID', GROUP),
    ):
        monkeypatch.setenv(name, value)
    # Synthetic deployment configuration, retaining the real exact-target checks.
    for name, value in (
        ('SCOPED_LIVE_TENANT', A), ('SCOPED_LIVE_CHANNEL', CA),
        ('SCOPED_LIVE_CUSTOMER', CUSTOMER), ('SCOPED_LIVE_GROUP', GROUP), ('SCOPED_LIVE_AGENT', AGENT),
    ):
        monkeypatch.setattr(pilot, name, value)
    local = f['db']
    local.execute("UPDATE saas_ai_agent_jobs SET state='skipped' WHERE tenant_id=%s::uuid", (A,))
    local.execute("UPDATE saas_ai_agents SET mode='automatic',language='az',delay_seconds=0 WHERE tenant_id=%s::uuid AND id=%s::uuid", (A, AGENT))
    local.execute('UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid', (A,))
    local.execute("UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s::uuid AND lead_id=41", (CUSTOMER, A))
    local.execute("UPDATE saas_ai_conversation_state SET conversation_id=%s WHERE tenant_id=%s::uuid AND conversation_id='client'", (CUSTOMER, A))
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,77,'Synthetic developer group','group')", (A, CA, GROUP))
    local.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,reason) VALUES(%s::uuid,%s,'paused','synthetic-explicit-owner-stop')", (A, GROUP))
    local.execute("INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by) VALUES(%s::uuid,%s::uuid,'15550001111@s.whatsapp.net',33,11)", (A, CA))
    secret = f['cipher'].encrypt(json.dumps({'api_key': 'synthetic-no-live-connect', 'webhook_secret': SECRET}).encode())
    local.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets) VALUES(%s::uuid,'connect','connected',%s)", (A, secret))
    local.commit()
    _policy(mode='fixed', fixed='en')
    page, ids = _case(f, [AZ], expected='en')
    f['case_id'] = page['ticket']['id']
    f['source_id'] = ids[-1]
    # Model an already-persisted attestation from the previous application
    # version. The retiring UI helpers are not part of this runtime contract.
    # Compute the fingerprint from the real current server-owned scope; all
    # claim, expiry, authority and send checks below remain actual production SQL.
    with local.cursor() as cur:
        row, fingerprint = pilot._snapshot(cur, A, {
            'tenant_id': A, 'channel_id': CA, 'conversation_id': CUSTOMER})
        assert row['agent_mode'] == 'automatic' and row['exact_agent_scope'] is True
        assert len(fingerprint) == 64
        cur.execute("""INSERT INTO saas_ai_pilot_confirmations
          (tenant_id,channel_id,conversation_id,scope_fingerprint,kwid_confirmed_by,
           kwid_confirmed_at,kwid_expires_at,owner_approved_by,owner_approved_at,owner_expires_at)
          VALUES(%s::uuid,%s::uuid,%s,%s,11,clock_timestamp()-interval '1 minute',
           clock_timestamp()+interval '14 minutes',11,clock_timestamp()-interval '1 minute',
           clock_timestamp()+interval '13 minutes')""", (A, CA, CUSTOMER, fingerprint))
    local.commit()
    assert pilot.scoped_live_enabled() and pilot.manual_live_job({
        'provider': 'connect', 'tenant_id': A, 'channel_id': CA, 'conversation_id': CUSTOMER})
    handoff = {'tenant_id': A, 'provider': 'connect', 'agent_id': AGENT, 'channel_id': CA,
        'conversation_id': CUSTOMER, 'chat_id': CUSTOMER, 'lead_id': 41,
        'event_fingerprint': 'native-language-handoff-' + uuid.uuid4().hex}
    local.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,state) VALUES(%s::uuid,%s,'connect','completed')", (A, handoff['event_fingerprint']))
    local.commit()
    assert actions.handoff(handoff, 'ai_developer_consultation:language-fixture', case_message_ids=ids)
    consultation = internal.create_from_ai(handoff, recipient_id=33, reason='missing verified fix',
        evidence={'query': AZ, 'case_message_ids': ids, 'source_external_id': 'connect:' + ids[-1]})
    assert consultation['created']
    f['consultation_id'] = consultation['id']
    # Establish an actual owner-prepared, dispatched and receipted request.
    # This provides the correlation authority for the later signed group reply.
    initial_send = Mock(return_value={'message_id': 'synthetic-initial-group-message',
        'external_message_id': 'SYNTHETIC_CASE_PROVIDER_RECEIPT'})
    monkeypatch.setattr(transport, 'send_consultation_with_receipt', initial_send)
    request_id = str(uuid.uuid4())
    group_flow.prepare(PROFILE, consultation['id'], {'request_id': request_id, 'channel_id': CA,
        'conversation_id': GROUP, 'summary': SUMMARY['en'], 'confirm_group': True, 'attachment_ids': []})
    assert group_flow.dispatch(PROFILE, consultation['id'], request_id)['state'] == 'sent'
    initial_send.assert_called_once()
    f['initial_send'] = initial_send
    f['customer_send'] = Mock(return_value='synthetic-customer-language-reply')
    f['automatic_group_send'] = Mock(side_effect=AssertionError('Existing Automatic/Manual group fence must remain closed'))
    monkeypatch.setattr(transport, 'send_ai_reply', f['customer_send'])
    monkeypatch.setattr(transport, 'send_group_with_receipt', f['automatic_group_send'])
    f['generate'] = Mock()
    monkeypatch.setattr(worker, 'generate', f['generate'])
    event = {'id': 'native-signed-group-' + uuid.uuid4().hex, 'type': 'message.inbound.received', 'data': {
        'id': 'native-developer-message-' + uuid.uuid4().hex, 'channelId': CA,
        'conversationId': GROUP, 'type': 'text', 'fromMe': False,
        'participantJid': '15550001111@s.whatsapp.net', 'participantName': 'Synthetic employee',
        'text': 'CASE:' + consultation['id'] + ' ' + RU,
        'quotedExternalMessageId': 'SYNTHETIC_CASE_PROVIDER_RECEIPT'}}
    raw = json.dumps(event).encode()
    signature = 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert transport.ingest(raw, signature, event)
    followups = local.execute('SELECT * FROM saas_ai_pilot_followups WHERE tenant_id=%s::uuid', (A,))
    assert len(followups) == 1 and followups[0]['state'] == 'queued'
    assert RU in followups[0]['group_reply']
    f['followup_id'] = str(followups[0]['id'])
    local.commit()
    return f


@pytest.mark.parametrize('action', ['group_answer', 'customer_question', 'customer_update'])
def test_actual_worker_keeps_internal_summary_language_separate_from_customer_reply_language(group_worker, action):
    f = group_worker
    job = runs.claim_next()
    assert job and job['payload']['pilot_developer_followup'] is True
    assert job['_pilot_scope_fenced'] is True
    assert pilot.processing_allowed(job) and connect.allowed(job)

    def generated(_key, model, mode, rows, draft, **kwargs):
        assert mode == 'reply'
        assert kwargs['extra_context']['language'] == 'az'
        selection = kwargs['extra_context']['consultation_summary_language']
        assert selection['language'] == 'en' and selection['method'] == 'company_fixed'
        assert selection['policy_version'] == 1
        assert RU in kwargs['extra_context']['pilot_developer_context']['specialist_reply']
        assert 'action=group_answer ONLY' in kwargs['instructions'] and 'English' in kwargs['instructions']
        assert 'customer_question or customer_update preserve the assigned agent reply language' in kwargs['instructions']
        # Exercise the real wire-level coverage/authority barrier before our
        # external-provider stub returns the bounded routing decision.
        wire = provider.request_payload(model, mode, rows, draft, instructions=kwargs['instructions'],
            extra_context=kwargs['extra_context'], images=kwargs.get('images'))
        kwargs['before_dispatch'](wire)
        return json.dumps({'action': action, 'message': REPLIES[action],
            'evidence_quote': AZ if action == 'group_answer' else ''}, ensure_ascii=False), {'total_tokens': 25}

    f['generate'].side_effect = generated
    asyncio.run(worker.process_connect_one(job, LOGGER))
    f['generate'].assert_called_once()
    f['automatic_group_send'].assert_not_called()
    rows = f['db'].execute('SELECT state,error_code FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint=%s', (A, job['event_fingerprint']))
    followup = f['db'].execute('SELECT reply_action,awaiting_customer,customer_outbound_message_id FROM saas_ai_pilot_followups WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, f['followup_id']))[0]
    proposed = f['db'].execute("SELECT entity_id,payload FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='ai_consultation_summary_proposed'", (A,))
    assert followup['reply_action'] == action
    if action == 'group_answer':
        assert not pilot.pilot_group_handoff_allowed(job)
        f['customer_send'].assert_not_called()
        assert rows[0] == {'state': 'unknown', 'error_code': 'pilot_group_send_unconfirmed'}
        delivery = f['db'].execute("SELECT state,body FROM saas_consultation_group_deliveries WHERE tenant_id=%s::uuid AND created_by=0", (A,))[0]
        assert delivery['state'] == 'blocked' and REPLIES[action] in delivery['body']
        assert 'New case information' in delivery['body']
        assert len(proposed) == 1 and proposed[0]['entity_id'] == f['consultation_id']
        assert proposed[0]['payload'] == {
            'case_id': f['case_id'], 'followup_id': f['followup_id'],
            'event_fingerprint': job['event_fingerprint'], 'state': 'draft',
            'summary_language': f['generate'].call_args.kwargs['extra_context']['consultation_summary_language'],
            'summary_text': REPLIES[action]}
        assert not f['db'].execute("SELECT id FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='ai_consultation_summary_update'", (A,))
    else:
        assert proposed == []
        assert rows[0] == {'state': 'sent', 'error_code': ''}
        f['customer_send'].assert_called_once()
        assert f['customer_send'].call_args.args[0]['conversation_id'] == CUSTOMER
        assert f['customer_send'].call_args.args[1] == REPLIES[action]
        assert followup['awaiting_customer'] is True
        assert followup['customer_outbound_message_id'] == 'synthetic-customer-language-reply'
    f['provider'].assert_called_once()  # The pre-existing support summary was reused.
    assert runs.claim_next() is None
    f['generate'].assert_called_once()


@pytest.mark.parametrize('confirmation', ['missing', 'expired'])
def test_actual_group_followup_claim_requires_current_separate_owner_and_kwid_attestations(group_worker, confirmation):
    f = group_worker
    if confirmation == 'missing':
        f['db'].execute('DELETE FROM saas_ai_pilot_confirmations WHERE tenant_id=%s::uuid', (A,))
    else:
        f['db'].execute("""UPDATE saas_ai_pilot_confirmations SET
          kwid_confirmed_at=now()-interval '20 minutes',kwid_expires_at=now()-interval '5 minutes',
          owner_approved_at=now()-interval '20 minutes',owner_expires_at=now()-interval '5 minutes'
          WHERE tenant_id=%s::uuid""", (A,))
    f['db'].commit()
    assert runs.claim_next() is None
    result = f['db'].execute('SELECT state,error_code FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND conversation_id=%s', (A, CUSTOMER))[0]
    assert result == {'state': 'skipped', 'error_code': 'pilot_scope_blocked'}
    f['generate'].assert_not_called()
    f['customer_send'].assert_not_called()
    f['automatic_group_send'].assert_not_called()
