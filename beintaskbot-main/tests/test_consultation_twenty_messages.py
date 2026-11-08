"""Twenty-message synthetic fragment: PGlite only; provider and file IO mocked."""
import uuid
from unittest.mock import Mock

import pytest
from test_dialog_consultations import database, db, ready, A, CA, PROFILE, TARGET, flow, public, transport
from tenant_linear_policy import TenantLinearError


def seed_twenty(ready):
    local, data, sender = ready
    local.execute('DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid', (A,))
    ids = [str(uuid.UUID(int=2000 + i)) for i in range(22)]
    kinds = {4: 'image', 9: 'document', 15: 'audio'}
    for i, key in enumerate(ids):
        local.execute('''INSERT INTO saas_crm_messages
          (id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,happened_at,raw)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,%s,%s::timestamptz,'{}'::jsonb)''',
          (key, A, 'connect:SYNTHETIC_'+str(i), 'incoming' if i % 2 else 'outgoing',
           'connect:'+CA, 'Selected message '+str(i), kinds.get(i, 'text'),
           '2026-11-01T12:'+str(i).zfill(2)+':00Z'))
    local.commit()
    data.update(message_ids=ids[1:21], media_ids=[ids[i] for i in kinds])
    shown = flow.preview(PROFILE, {'channel_id': CA, 'conversation_id': 'client'})
    data.update(public_header_version=1, public_header_hash=shown['public_header_hash'],
                include_customer_identity=False,
                public_header_source_versions={row['id']: row['source_version']
                    for row in shown['messages'] if row['id'] in data['message_ids']})
    return ids, kinds


def test_twenty_message_snapshot_and_files_are_exact_but_group_gets_summary_link(ready, monkeypatch):
    local, data, sender = ready
    ids, kinds = seed_twenty(ready)
    loader = Mock(return_value={'base64': 'eA==', 'mimeType': 'application/octet-stream'})
    monkeypatch.setattr(transport, 'download_connect_message_media', loader)
    result = flow.prepare(PROFILE, data, 'https://crm.test')
    body = local.execute('SELECT body FROM saas_consultation_group_deliveries')[0]['body']
    local.commit()
    token = body.rsplit('/', 1)[1]  # Synthetic local token, never fetched over HTTP.
    snapshot = public.read(token)
    assert [item['id'] for item in snapshot['public_messages']] == ids[1:21]
    assert [item['text'] for item in snapshot['public_messages']] == ['Selected message '+str(i) for i in range(1, 21)]
    assert [item['type'] for item in snapshot['public_messages']] == [kinds.get(i, 'text') for i in range(1, 21)]
    assert snapshot['selected_media_ids'] == data['media_ids']
    assert not loader.called and not sender.called
    assert body == data['summary']+'\n\nФрагмент переписки (ссылка действует 7 дней):\nhttps://crm.test/public/case/'+token
    assert 'Selected message' not in body
    assert flow.dispatch(PROFILE, result['id'], data['request_id'])['state'] == 'sent'
    assert sender.call_count == 1 and sender.call_args.args[1] == body
    assert sender.call_args.args[0]['conversation_id'] == TARGET
    for key in data['media_ids']:
        assert public.media(token, key)['base64'] == 'eA=='
    assert loader.call_count == 3
    assert [call.args[1] for call in loader.call_args_list] == ['SYNTHETIC_'+str(i) for i in kinds]
    with pytest.raises(TenantLinearError):
        public.media(token, ids[0])
    local.execute("UPDATE saas_crm_messages SET body='Changed source' WHERE tenant_id=%s::uuid AND kommo_lead_id=41", (A,))
    local.commit()
    assert [item['text'] for item in public.read(token)['public_messages']] == ['Selected message '+str(i) for i in range(1, 21)]
    assert flow.dispatch(PROFILE, result['id'], data['request_id'])['state'] == 'sent'
    assert sender.call_count == 1


@pytest.mark.parametrize('outcome', ['pending', 'unknown'])
def test_twenty_messages_do_not_convert_unconfirmed_receipt_to_sent(ready, outcome):
    local, data, sender = ready
    seed_twenty(ready)
    if outcome == 'pending':
        sender.return_value = {'state': 'pending', 'message_id': 'synthetic-queued'}
    else:
        sender.side_effect = TimeoutError('Synthetic provider timeout')
    result = flow.prepare(PROFILE, data, 'https://crm.test')
    assert flow.dispatch(PROFILE, result['id'], data['request_id'])['state'] == outcome
    assert local.execute('SELECT state FROM saas_consultation_group_deliveries')[0]['state'] == outcome
    local.commit()
    if outcome == 'pending':
        assert flow.dispatch(PROFILE, result['id'], data['request_id'])['state'] == 'pending'
    else:
        with pytest.raises(TenantLinearError):
            flow.dispatch(PROFILE, result['id'], data['request_id'])
    assert sender.call_count == 1
