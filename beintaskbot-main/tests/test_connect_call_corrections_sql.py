"""Disposable PGlite/SQL end-to-end correction projection; no external actions."""
import json
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels
from test_connect_missed_calls import rejected, call
from test_connect_call_corrections import invalidated
import tenant_connect_transport as transport
import tenant_ai_agent_connect as ai


@pytest.fixture
def ingest(channels, monkeypatch):
    channels['db'].execute("SELECT setval(pg_get_serial_sequence('saas_connect_conversations','lead_id'),1000,true)")
    monkeypatch.setattr(transport, '_verify_signature', lambda *args: True)
    monkeypatch.setenv('AI_AGENTS_ENABLED','false');monkeypatch.setenv('AI_SIMULATION_ENABLED','false')
    def no_remote(*args, **kwargs):raise AssertionError('No external service calls')
    monkeypatch.setattr(transport,'_request',no_remote)
    for name in ('enqueue','mark_operator_reply','bind_unassigned_direct_agent'):
        monkeypatch.setattr(ai,name,no_remote)
    return lambda event:transport.ingest(json.dumps(event).encode(),'mock',event)


@pytest.mark.parametrize('order',[(rejected,invalidated),(invalidated,rejected)])
def test_correction_both_orders_retries_and_source_isolation(ingest,channels,order):
    for factory in (*order,*order):
        event=factory(channelId=CA)
        assert ingest(event)
        assert ingest(event)
    rows=channels['db'].execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==1 and rows[0]['message_type']=='call_invalidated'
    assert rows[0]['raw']['callRevision']==2 and rows[0]['direction']=='system'
    assert ingest(rejected(channelId=CB))
    rows=channels['db'].execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==2 and {r['message_type'] for r in rows}=={'call_invalidated','call_rejected'}
    assert not channels['db'].execute("SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(B,))


def test_correction_cannot_overwrite_missed(ingest,channels):
    assert ingest(call(channelId=CA))
    with pytest.raises(transport.ConnectWebhookError,match='Call scope mismatch'):
        ingest(invalidated(channelId=CA))
    rows=channels['db'].execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id LIKE 'connect:call:%%'",(A,))
    assert len(rows)==1 and rows[0]['message_type']=='call_missed'


@pytest.mark.parametrize('proof',['provider','link','unknown','other_source'])
def test_correction_after_verified_route_change_uses_persisted_proof(ingest,channels,proof):
    # Synthetic historical row retaining its old raw identity after audited route
    # reconciliation. Only stored source-local proof can authorize the correction.
    lid='80000000000091@lid';jid='994700000091@s.whatsapp.net'
    assert ingest(rejected(channelId=CA))
    local=channels['db']
    row=local.execute("SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND message_type='call_rejected'",(A,))[0]
    local.execute("UPDATE saas_crm_messages SET raw=jsonb_set(raw,'{conversationId}',to_jsonb(%s::text)) WHERE id=%s::uuid",(lid,row['id']))
    if proof in {'provider','other_source'}:
        if proof=='other_source':
            assert ingest(rejected(channelId=CB,externalCallId='other_source_call'))
        local.execute('''INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id)
            VALUES(%s::uuid,%s::uuid,%s,%s)''',(A,CA if proof=='provider' else CB,lid,jid))
    elif proof=='link':
        # A separate retained source route linked by the existing explicit merge
        # mechanism. No new identity is inferred by correction handling itself.
        assert ingest(rejected(channelId=CA,conversationId=lid,externalCallId='other_call'))
        source=local.execute('SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s',(A,CA,lid))[0]
        local.execute('''INSERT INTO saas_connect_dialog_links
          (tenant_id,channel_id,source_id,canonical_id,source_lead,canonical_lead,pair_id,snapshot)
          VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,'synthetic-proof','{}'::jsonb)''',
          (A,CA,lid,jid,source['lead_id'],row['kommo_lead_id']))
    local.commit()
    if proof in {'provider','link'}:
        assert ingest(invalidated(channelId=CA))
        result=local.execute('SELECT message_type,raw FROM saas_crm_messages WHERE id=%s::uuid',(row['id'],))[0]
        assert result['message_type']=='call_invalidated' and result['raw']['conversationId']==lid
    else:
        with pytest.raises(transport.ConnectWebhookError,match='Call scope mismatch'):
            ingest(invalidated(channelId=CA))
        assert local.execute('SELECT message_type FROM saas_crm_messages WHERE id=%s::uuid',(row['id'],))[0]['message_type']=='call_rejected'
