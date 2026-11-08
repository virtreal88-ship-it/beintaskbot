"""Synthetic provider fixtures; automatic authority belongs to ingress generation."""
import uuid
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, CA, AGENT
from test_integrated_connect_release import flow
from test_pr465_followup_worker import case_flow
import tenant_ai_agent_connect as connect
import tenant_dialog_consultations as consultations
import tenant_connect_transport as transport


def seed(f):
    source=f['inbound']('text','Original report')[0]
    assert f['process']()
    f['create_case']([source])
    f['send'].reset_mock();f['generate'].reset_mock()


def mode_cycle(f):
    for mode in ('manual','automatic'):
        f['db'].execute('UPDATE saas_ai_agents SET mode=%s,version=version+1 WHERE id=%s::uuid',(mode,AGENT))
        f['db'].commit()


def payload(f):
    return f['db'].execute("SELECT e.payload FROM saas_ai_agent_jobs j JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint WHERE j.tenant_id=%s::uuid AND j.conversation_id='client'",(A,))[0]['payload']


def test_mode_cycle_before_enqueue_cannot_repay_old_intent(case_flow):
    f=case_flow;seed(f)
    reply,admitted=f['reply'](1,'Review tomorrow.',enqueue=False)
    assert reply and not admitted
    row=f['db'].execute('SELECT source_external_id FROM saas_dialog_consultation_replies WHERE id=%s::uuid',(reply,))[0]
    mode_cycle(f)
    with f['db'].cursor() as cur:
        assert not connect.enqueue_case_developer_followup(cur,A,reply,
            source_external_id=row['source_external_id'],quoted_message_id='PROVIDER_synthetic-developer-1')
    f['send'].assert_not_called();f['generate'].assert_not_called()


@pytest.mark.parametrize('change',['mode_cycle','missing_stamp'])
def test_queued_intent_revalidated_before_generation(case_flow,change):
    f=case_flow;seed(f)
    assert f['reply'](1,'Review tomorrow.')[1]
    original=payload(f)
    if change=='mode_cycle':mode_cycle(f)
    else:
        f['db'].execute("UPDATE saas_crm_messages SET raw=raw-'_case_followup_intent' WHERE tenant_id=%s::uuid",(A,));f['db'].commit()
    assert not consultations.case_developer_followup_is_current(A,original)
    # Human evidence/resolution authority deliberately does not inherit the
    # automatic generation requirement.
    with f['db'].cursor() as cur:assert consultations.case_developer_followup_current(cur,A,original)
    assert f['process']()
    f['generate'].assert_not_called();f['send'].assert_not_called()


def test_mode_cycle_during_generation_blocks_customer_send(case_flow):
    f=case_flow;seed(f);assert f['reply'](1,'Review tomorrow.')[1]
    def generate(*args,**kwargs):
        mode_cycle(f)
        return 'Synthetic response',{'total_tokens':3}
    f['generate'].side_effect=generate
    assert f['process']()
    f['generate'].assert_called_once();f['send'].assert_not_called()


def test_final_boundary_independently_rejects_old_ingress(case_flow):
    f=case_flow;seed(f);assert f['reply'](1,'Review tomorrow.')[1]
    original=payload(f);mode_cycle(f)
    revision=f['db'].execute("SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND conversation_id='client'",(A,))[0]['revision']
    assert transport.send_ai_reply_if_active(A,41,CA,'client','Never send',str(uuid.uuid4()),revision,
        case_followup_binding=original) is None
    f['send'].assert_not_called()


def test_each_coalesced_binding_requires_same_live_ingress_generation(case_flow):
    f=case_flow;seed(f);assert f['reply'](1,'First review tomorrow.')[1]
    stale=payload(f)['developer_reply_bindings'][0]
    mode_cycle(f)
    assert f['reply'](2,'Second review tomorrow.')[1]
    current=payload(f);assert len(current['developer_reply_bindings'])==1
    fresh=current['developer_reply_bindings'][0]
    assert not consultations.case_developer_followup_is_current(A,stale)
    assert consultations.case_developer_followup_is_current(A,fresh)
    combined={**current,'developer_reply_bindings':[stale,fresh]}
    assert not consultations.case_developer_followup_is_current(A,combined)
    # A stale binding nested in a stored/coalesced payload is still blocked.
    assert transport.send_ai_reply_if_active(A,41,CA,'client','Never send',str(uuid.uuid4()),0,
        case_followup_binding=combined) is None
    f['send'].assert_not_called()
    # A genuinely fresh reply receives only its own authority, not that of
    # the expired prior turn. The old reply record remains as human evidence.
    assert f['process']()
    f['generate'].assert_called_once();f['send'].assert_called_once()
    updates=f['generate'].call_args.kwargs['extra_context']['verified_developer_case_updates']
    assert [x['source_id'] for x in updates]==['case-reply:'+fresh['developer_reply_message_id']]
    assert len(f['db'].execute('SELECT id FROM saas_dialog_consultation_replies'))==2


def test_unchanged_ingress_still_generates_and_sends_once(case_flow):
    f=case_flow;seed(f);assert f['reply'](1,'Review tomorrow.')[1]
    assert consultations.case_developer_followup_is_current(A,payload(f))
    assert f['process']()
    f['generate'].assert_called_once();f['send'].assert_called_once()
