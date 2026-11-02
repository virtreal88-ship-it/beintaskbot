"""Real Manual consultation drafts from frozen saved evidence, with no media I/O."""
import asyncio
import copy
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

import pytest

from test_tenant_support_inbox import database, db, A, CA, PROFILE
from test_pr465_workflow_integration import isolated, _enable_private_copilot, DEVELOPER
import tenant_chat_ai_provider as provider
import tenant_chat_attachment_store as attachments
import tenant_connect_media as connect_media
import tenant_connect_transport as transport
import tenant_dialog_consultations as flow
import tenant_dialog_consultations_worker as worker
import tenant_episode_media as episode
import tenant_knowledge_media as knowledge_media
import tenant_media_evidence as evidence
import tenant_support_inbox_store as cases
from tenant_linear_policy import TenantLinearError


LOGGER = logging.getLogger(__name__)
SPEECH = 'The customer reported fifty, while the receipt showed forty-nine.'
PHOTO = 'The screen displays receipt total 49.00 and version 2.1.'
DRAFT = 'Your report is accepted. The developer plans to review the recorded amount tomorrow.'


def _persist_saved(db, row):
    ref = episode.reference(row, CA, 41)
    result = {'transcript': SPEECH} if row['message_type'] == 'voice' else {'observations': [{'text': PHOTO}]}
    with db.cursor() as cur:
        evidence.persist(cur, A, [ref], [{'id': ref['id'], 'status': 'processed',
            'code': 'transcribed' if row['message_type'] == 'voice' else 'vision_observation',
            **result}], processor='conversation_episode')
    db.commit()


@pytest.fixture
def frozen_case(isolated, monkeypatch):
    local, _job, cipher = isolated
    for module in (evidence, attachments):
        monkeypatch.setattr(module, '_connect', lambda: local)
    evidence.create_schema(local)
    local.execute('DELETE FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid', (A,))
    selected = []
    media_rows = {}
    # The original voice precedes a staff response and a ten-minute boundary.
    # A frozen consultation must cover every explicitly selected source.
    for index, (kind, body, minutes, direction) in enumerate([
            ('voice', 'Original voice caption', 35, 'incoming'),
            ('text', 'Employee asked the customer to show the screen', 30, 'outgoing'),
            ('image', 'Original screen caption', 20, 'incoming'),
            ('text', 'Please review the recorded amount and screen together', 19, 'incoming')], 1):
        message_id = str(uuid.UUID(int=465050 + index))
        raw = {'mediaId': 'frozen-media-' + str(index), 'fileLength': 100} if kind != 'text' else {}
        local.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
          direction,channel,body,message_type,raw,happened_at)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,%s,%s::jsonb,now()-(%s * interval '1 minute'))""",
          (message_id, A, 'connect:' + message_id, direction, 'connect:' + CA,
           body, kind, json.dumps(raw), minutes))
        selected.append(message_id)
        if kind in {'voice', 'image'}:
            media_rows[kind] = local.execute("""SELECT id::text,external_id,body,message_type,
              direction,raw,channel,COALESCE(happened_at,created_at) AS happened_at
              FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid""", (A, message_id))[0]
    local.commit()
    for row in media_rows.values():
        _persist_saved(local, row)

    created = cases.create(PROFILE, {'request_id': str(uuid.uuid4()), 'lead_id': 41,
        'message_ids': selected, 'assigned_developer_channel_id': CA,
        'assigned_developer_conversation_id': DEVELOPER, 'developer_confirmed': True})
    case_id = created['ticket']['id']
    _enable_private_copilot(local, cipher)

    # Only the external Connect queue endpoint is substituted. The real
    # dispatch, receipt correlation, saved reply and worker paths remain active.
    external = Mock(return_value={'id': 'frozen-consultation-queue', 'status': 'pending'})
    monkeypatch.setattr(transport, '_request', external)
    request = {'request_id': str(uuid.uuid4()), 'case_id': case_id, 'channel_id': CA,
        'conversation_id': 'client', 'destination_channel_id': CA,
        'destination_conversation_id': DEVELOPER, 'message_ids': selected,
        'media_ids': [], 'summary': 'Review the original photo and voice together', 'confirm': True}
    consult = flow.prepare(PROFILE, request, 'https://crm.test')
    assert flow.dispatch(PROFILE, consult['id'], request['request_id'])['state'] == 'pending'
    with local.cursor() as cur:
        assert flow.observe_outbound_receipt(cur, A, CA, DEVELOPER, {
            '_crm_signed_outbound_receipt': True, 'fromMe': True, 'id': 'frozen-consultation-queue',
            'externalMessageId': 'FROZEN_CONSULTATION_PROVIDER',
            'originClientMessageId': request['request_id']})
    local.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
      direction,channel,body,message_type,raw,author_name)
      VALUES(%s::uuid,%s::uuid,88,'connect:frozen-developer-reply','incoming',%s,
        'Accepted; I will review the reported amount tomorrow.','text',%s::jsonb,'Synthetic developer')""",
      (uuid.uuid4(), A, 'connect:' + CA, json.dumps({'id': 'frozen-developer-reply',
        'quotedExternalMessageId': 'FROZEN_CONSULTATION_PROVIDER'})))
    local.commit()
    assert worker.reconcile() == 1
    reply_id = flow.inbox(PROFILE, case_id=case_id)['replies'][0]['id']
    public_before = copy.deepcopy(local.execute("""SELECT selected_message_ids,selected_media_ids,
      source_versions,public_identity,public_messages FROM saas_public_case_shares""")[0])
    assert public_before['selected_media_ids'] == [] and public_before['public_identity'] == {}
    assert len(public_before['source_versions']) == len(selected)

    # This newer unrelated request must not replace or extend the explicit
    # original snapshot during the developer-reply draft.
    local.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,
      channel,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,41,'connect:unrelated-after-snapshot',
      'incoming',%s,'UNRELATED_LATER_INCIDENT','text',clock_timestamp())""", (uuid.uuid4(), A, 'connect:' + CA))
    local.commit()

    media_download = AsyncMock(side_effect=AssertionError('Frozen saved evidence must not download media'))
    monkeypatch.setattr(connect_media, 'download_media', media_download)
    monkeypatch.setattr(knowledge_media, 'download_media', media_download)
    provider_media = Mock(side_effect=AssertionError('Frozen saved evidence must not invoke ASR/video'))
    monkeypatch.setattr(provider, 'transcribe', provider_media)
    monkeypatch.setattr(provider, 'transcribe_video', provider_media)
    legacy_download = Mock(side_effect=AssertionError('Frozen saved evidence must not download Connect bytes'))
    monkeypatch.setattr(transport, 'download_connect_message_media', legacy_download)

    def text_response(url, **kwargs):
        assert url == 'https://api.openai.com/v1/responses'
        wire = kwargs['json']
        assert wire['model'] == 'synthetic-agent-model'
        assert isinstance(wire['input'], str), 'Saved photo evidence is text; no image bytes are sent'
        return SimpleNamespace(status_code=200, json=lambda: {'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': DRAFT}]}],
            'usage': {'input_tokens': 20, 'output_tokens': 5, 'total_tokens': 25}})

    # Keep actual provider.generate and its before_dispatch callback; replace
    # only HTTP so the real frozen-source and rendered-proof checks execute.
    text_http = Mock(side_effect=text_response)
    monkeypatch.setattr(provider.requests, 'post', text_http)
    return {'db': local, 'case_id': case_id, 'reply_id': reply_id, 'selected': selected,
            'media_rows': media_rows, 'external': external, 'text_http': text_http,
            'media_download': media_download, 'provider_media': provider_media,
            'legacy_download': legacy_download, 'public_before': public_before}


def _no_media_or_customer_send(fixture):
    fixture['media_download'].assert_not_called()
    fixture['provider_media'].assert_not_called()
    fixture['legacy_download'].assert_not_called()
    fixture['external'].assert_called_once()
    assert fixture['external'].call_args.args[3]['conversationId'] == DEVELOPER
    assert not fixture['db'].execute('SELECT request_id FROM saas_chat_send_receipts')


def test_manual_snapshot_complete_photo_and_voice_generate_one_private_draft(frozen_case):
    f = frozen_case
    asyncio.run(worker.deliver(LOGGER))
    asyncio.run(worker.deliver(LOGGER))
    f['text_http'].assert_called_once()
    supplied = json.loads(f['text_http'].call_args.kwargs['json']['input'])
    assert SPEECH in str(supplied['conversation']) and PHOTO in str(supplied['conversation'])
    assert 'UNRELATED_LATER_INCIDENT' not in str(supplied['conversation'])
    assert supplied['extra_context']['untrusted_developer_consultation_evidence']['id'] == f['reply_id']
    reply = flow.inbox(PROFILE, case_id=f['case_id'])['replies'][0]
    assert reply['draft_state'] == 'completed' and reply['draft'] == DRAFT
    assert flow.draft(PROFILE, f['reply_id'], {'request_id': str(uuid.uuid4())}) == {
        'text': DRAFT, 'customer_message_sent': False}
    f['text_http'].assert_called_once()
    result = f['db'].execute('SELECT status,result FROM saas_chat_ai_runs')[0]
    assert result['status'] == 'completed' and result['result']['media_coverage']['complete'] is True
    assert result['result']['media_coverage']['snapshot']['reply_id'] == f['reply_id']
    assert {ref['external_id'] for ref in result['result']['source_versions']} == {
        'connect:' + message_id for message_id in f['selected']}
    assert f['db'].execute("""SELECT selected_message_ids,selected_media_ids,source_versions,
      public_identity,public_messages FROM saas_public_case_shares""")[0] == f['public_before']
    assert SPEECH not in str(f['public_before']['public_messages']) and PHOTO not in str(f['public_before']['public_messages'])
    assert f['db'].execute('SELECT status FROM saas_support_tickets')[0]['status'] != 'resolved'
    _no_media_or_customer_send(f)


@pytest.mark.parametrize('failure', ['missing_voice', 'partial_voice', 'missing_image', 'partial_image',
                                    'stale_image_receipt', 'changed_caption', 'replaced_image', 'legacy_no_versions'])
def test_manual_snapshot_unverified_media_blocks_before_text_provider(frozen_case, failure):
    f = frozen_case
    local = f['db']
    kind = 'voice' if failure.endswith('voice') else 'image'
    row = f['media_rows'][kind]
    if failure.startswith('missing_'):
        local.execute('DELETE FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s', (A, row['external_id']))
    elif failure.startswith('partial_'):
        local.execute("UPDATE saas_media_evidence_receipts SET status='partial',coverage=jsonb_set(coverage,'{complete}','false'::jsonb) WHERE tenant_id=%s::uuid AND external_id=%s", (A, row['external_id']))
    elif failure == 'stale_image_receipt':
        local.execute('UPDATE saas_media_evidence_receipts SET source_hash=%s WHERE tenant_id=%s::uuid AND external_id=%s', ('0' * 64, A, row['external_id']))
    elif failure == 'changed_caption':
        local.execute("UPDATE saas_crm_messages SET body='Changed after the consultation snapshot' WHERE tenant_id=%s::uuid AND id=%s::uuid", (A, row['id']))
    elif failure == 'replaced_image':
        local.execute("UPDATE saas_crm_messages SET raw=jsonb_set(raw,'{mediaId}','\"replacement-after-snapshot\"'::jsonb) WHERE tenant_id=%s::uuid AND id=%s::uuid", (A, row['id']))
        local.commit()
        replacement = local.execute("""SELECT id::text,external_id,body,message_type,direction,raw,
          channel,COALESCE(happened_at,created_at) AS happened_at FROM saas_crm_messages WHERE id=%s::uuid""", (row['id'],))[0]
        # Even a new complete receipt cannot rewrite the older frozen source.
        _persist_saved(local, replacement)
    elif failure == 'legacy_no_versions':
        local.execute("UPDATE saas_public_case_shares SET source_versions='{}'::jsonb")
    local.commit()
    asyncio.run(worker.deliver(LOGGER))
    asyncio.run(worker.deliver(LOGGER))
    f['text_http'].assert_not_called()
    reply = local.execute('SELECT draft_state,draft FROM saas_dialog_consultation_replies')[0]
    assert reply['draft_state'] != 'completed' and reply['draft'] == ''
    assert not local.execute("SELECT request_id FROM saas_chat_ai_runs WHERE status='completed'")
    _no_media_or_customer_send(f)


@pytest.mark.parametrize('change', ['source', 'receipt_withdrawn', 'receipt_changed'])
def test_manual_snapshot_changed_during_provider_generation_discards_the_draft(frozen_case, change):
    f = frozen_case
    local = f['db']
    image = f['media_rows']['image']
    response = f['text_http'].side_effect

    def changed_during_generation(url, **kwargs):
        # provider.generate has already invoked the real before_dispatch proof
        # guard. The returned text cannot survive a later source withdrawal.
        if change == 'source':
            local.execute("UPDATE saas_crm_messages SET raw=jsonb_set(raw,'{mediaId}','\"changed-during-generation\"'::jsonb) WHERE id=%s::uuid", (image['id'],))
        elif change == 'receipt_withdrawn':
            local.execute('DELETE FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND external_id=%s', (A, image['external_id']))
        else:
            local.execute("UPDATE saas_media_evidence_receipts SET result=jsonb_set(result,'{observations}','[{\"text\":\"Corrected observation during generation\"}]'::jsonb) WHERE tenant_id=%s::uuid AND external_id=%s", (A, image['external_id']))
        local.commit()
        return response(url, **kwargs)

    f['text_http'].side_effect = changed_during_generation
    asyncio.run(worker.deliver(LOGGER))
    asyncio.run(worker.deliver(LOGGER))
    f['text_http'].assert_called_once()
    reply = local.execute('SELECT draft_state,draft FROM saas_dialog_consultation_replies')[0]
    assert reply['draft_state'] != 'completed' and reply['draft'] == ''
    assert local.execute('SELECT status FROM saas_chat_ai_runs')[0]['status'] == 'unknown'
    _no_media_or_customer_send(f)


@pytest.mark.parametrize('change', ['legacy_no_proof', 'receipt_changed'])
def test_completed_manual_media_draft_replay_requires_original_saved_proof(frozen_case, change):
    f = frozen_case
    local = f['db']
    asyncio.run(worker.deliver(LOGGER))
    assert local.execute('SELECT draft_state FROM saas_dialog_consultation_replies')[0]['draft_state'] == 'completed'
    if change == 'legacy_no_proof':
        local.execute("UPDATE saas_chat_ai_runs SET result=result-'media_coverage'")
    else:
        local.execute("UPDATE saas_media_evidence_receipts SET status='partial',coverage=jsonb_set(coverage,'{complete}','false'::jsonb) WHERE tenant_id=%s::uuid AND media_kind='voice'", (A,))
    local.commit()
    with pytest.raises(TenantLinearError):
        flow.draft(PROFILE, f['reply_id'], {'request_id': str(uuid.uuid4())})
    f['text_http'].assert_called_once()
    _no_media_or_customer_send(f)
