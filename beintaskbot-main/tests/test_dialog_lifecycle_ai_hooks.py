"""Lifecycle takeover guards: no provider calls, tenant/source exact scope."""
from unittest.mock import MagicMock, patch
import pytest
from tenant_dialog_rollout import RolloutState

@pytest.fixture(autouse=True)
def tenant_rollout(monkeypatch):
    # These unit tests isolate route/AI behavior. Real advisory locking and
    # persisted tenant states are exercised in native rollout tests.
    state = {"value": RolloutState("active", True, True, 1)}
    monkeypatch.setattr("tenant_dialog_rollout.guard", lambda cur, tenant, **kw: state["value"])
    return state


import tenant_ai_agent_connect as ai
import tenant_ai_group_coordination as groups
import tenant_connect_transport as transport

TENANT='00000000-0000-0000-0000-000000000001'
CHANNEL='00000000-0000-0000-0000-000000000002'
OTHER='00000000-0000-0000-0000-000000000003'
CONVERSATION='12000000001@g.us'


def connection():
    conn=MagicMock()
    conn.__enter__.return_value=conn
    cur=conn.cursor.return_value.__enter__.return_value
    return conn,cur


def test_accepted_scope_blocks_worker_admission_before_processing():
    conn,cur=connection()
    with patch('tenant_dialog_lifecycle.ai_blocked', return_value=True) as blocked, patch.object(ai,'runtime_flag',return_value=True):
        assert ai.allowed({'provider':'connect','tenant_id':TENANT,'channel_id':CHANNEL,
                           'conversation_id':CONVERSATION},connection=conn) is False
    blocked.assert_called_once_with(cur,TENANT,'connect',CHANNEL,CONVERSATION)
    cur.execute.assert_not_called()


def test_accepted_scope_prevents_job_enqueue_and_case_side_effects():
    conn,cur=connection()
    with patch('tenant_dialog_lifecycle.ai_blocked',return_value=True) as blocked, patch.object(ai,'lock_dialog'):
        assert ai.enqueue(TENANT,channel_id=CHANNEL,conversation_id=CONVERSATION,lead_id=1,
                          message_id='message',event_id='event',text='hello',connection=conn)==0
    blocked.assert_called_once_with(cur,TENANT,'connect',CHANNEL,CONVERSATION)
    cur.execute.assert_not_called()


def test_group_guard_blocks_before_dedupe_or_delivery_reservation():
    cur=MagicMock()
    with patch('tenant_dialog_lifecycle.ai_blocked',return_value=True) as blocked:
        assert groups.job_allowed(cur,{'provider':'connect','tenant_id':TENANT,
                                      'channel_id':CHANNEL,'conversation_id':CONVERSATION}) is False
    blocked.assert_called_once_with(cur,TENANT,'connect',CHANNEL,CONVERSATION)
    cur.execute.assert_not_called()


def test_final_locked_direct_send_rechecks_acceptance_without_provider_call():
    conn,cur=connection()
    direct='994700000000@s.whatsapp.net'
    with patch.object(transport,'_connect',return_value=conn), patch.object(transport,'_schema'), \
         patch.object(transport,'lock_dialog') as lock, patch('tenant_dialog_lifecycle.ai_blocked',return_value=True) as blocked, \
         patch.object(transport,'_request') as network:
        assert transport.send_ai_reply_if_active(TENANT,1,CHANNEL,direct,'draft','run',1) is None
    lock.assert_called_once_with(cur,TENANT,'connect',CHANNEL,direct)
    blocked.assert_called_once_with(cur,TENANT,'connect',CHANNEL,direct)
    network.assert_not_called()
    cur.execute.assert_not_called()


def test_operator_receipt_stays_channel_local_for_managed_episode():
    conn,cur=connection()
    cur.fetchone.return_value={'present':True}
    with patch.object(ai,'ensure_schema'), patch.object(ai,'lock_dialog',return_value=CHANNEL), \
         patch.object(ai,'unique_unassigned_direct_agent',return_value=None), \
         patch('tenant_dialog_lifecycle.ai_blocked',return_value=True) as blocked, \
         patch.object(groups,'stop') as global_stop:
        ai.mark_operator_reply(TENANT,1,CHANNEL,CONVERSATION,connection=conn,actor=7)
    global_stop.assert_not_called()
    assert not any('INSERT INTO saas_ai_conversation_state' in call.args[0] for call in cur.execute.call_args_list)
    blocked.assert_called_once_with(cur,TENANT,'connect',CHANNEL,CONVERSATION)
    job_writes=[call for call in cur.execute.call_args_list if 'UPDATE saas_ai_agent_jobs' in call.args[0]]
    assert len(job_writes)==1
    assert 'channel_id=%s' in job_writes[0].args[0]
    assert job_writes[0].args[1]==(TENANT,CONVERSATION,CHANNEL)


def test_legacy_operator_receipt_preserves_existing_group_stop(tenant_rollout):
    tenant_rollout["value"] = RolloutState()
    conn,cur=connection()
    cur.fetchone.return_value={'present':True}
    with patch.object(ai,'ensure_schema'), patch.object(ai,'lock_dialog',return_value=CHANNEL), \
         patch.object(ai,'unique_unassigned_direct_agent',return_value=None), \
         patch('tenant_dialog_lifecycle.ai_blocked',return_value=False), patch.object(groups,'stop') as global_stop:
        ai.mark_operator_reply(TENANT,1,CHANNEL,CONVERSATION,connection=conn,actor=7)
    global_stop.assert_called_once_with(cur,TENANT,CONVERSATION,actor=7)


def test_final_send_has_no_legacy_bypass_when_lifecycle_enabled():
    import pytest
    import tenant_chat_send_store as sender
    from tenant_linear_policy import TenantLinearError
    conn,_=connection()
    callback=MagicMock()
    item={'key':(TENANT,7,'request'),'command':{'lead_id':1},
          'connection':['connect',CHANNEL,CONVERSATION]}
    with patch.object(sender,'_connect',return_value=conn), \
         patch('tenant_scoped_access.scoped_access_enabled',return_value=False), \
         patch('tenant_dialog_lifecycle.enabled',return_value=True), \
         patch.object(sender,'authority',side_effect=TenantLinearError('Taken',404)):
        with pytest.raises(TenantLinearError):sender.dispatch_guarded(item,{},callback,'text')
    callback.assert_not_called()


def test_notification_final_guard_cannot_bypass_lifecycle_in_legacy_mode():
    import tenant_crm_notice_outbox as outbox
    conn,cur=connection();cur.fetchone.return_value={'status':'sending'}
    callback=MagicMock()
    item={'tenant_id':TENANT,'recipient_id':7,'event':'incoming_message','kind':'message',
          'source_key':'message','event_id':'event','channel':'push','endpoint_hash':'hash',
          'payload':{},'resource':{}}
    with patch.object(outbox,'_connect',return_value=conn),patch.object(outbox,'resource',return_value=None), \
         patch('tenant_scoped_access.scoped_access_enabled',return_value=False), \
         patch('tenant_dialog_lifecycle.enabled',return_value=True), \
         patch('tenant_scoped_access_store.read_access_profile',return_value=None):
        assert outbox.dispatch_guarded(item,callback) is None
    callback.assert_not_called()
