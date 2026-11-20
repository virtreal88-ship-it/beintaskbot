"""Shared-group echoes deduplicate only AFTER source/episode access filtering.

Synthetic stored receipts only: no connector calls, sends, AI, or production.
"""
import json
import uuid
import pytest
from test_connect_group_history import database,db,flow,channels,group,history_db,add,A,CA,CB,PROFILE,G
import tenant_operator_dialogs as dialogs
import tenant_dialog_lifecycle as lifecycle
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def projection(history_db,monkeypatch):
    d=history_db
    import tenant_platform
    monkeypatch.setattr(tenant_platform,'_connect',lambda:d)
    monkeypatch.setattr(dialogs,'_connect',lambda:d)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID',A)
    monkeypatch.setenv('TENANT_DIALOG_PRESENCE_ROUTING_ENABLED','true')
    with d.cursor() as cur:lifecycle.migrate(cur)
    for actor in (33,44):
        d.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions) VALUES(%s::uuid,%s,'operator','Synthetic operator','[\"dialogs\",\"support\"]'::jsonb)",(A,actor))
    for actor in (11,33,44):
        d.execute("INSERT INTO saas_dialog_employee_presence(tenant_id,employee_id,status,updated_by) VALUES(%s::uuid,%s,'online',%s)",(A,actor,actor))
    policies={str(actor):{'access_scopes':{'dialogs':{'view':scope,'update':scope}}} for actor,scope in ((33,'self'),(44,'all'))}
    d.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,json.dumps(policies)))
    d.execute("INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by) VALUES(%s::uuid,'connect',%s::uuid,33,11)",(A,CB))
    d.commit()
    # Same verified event on two company sources, not text-similarity matching.
    add(d,1,CA,event='EXACT_SHARED_ECHO',direction='outgoing')
    add(d,2,CB,event='EXACT_SHARED_ECHO',direction='incoming')
    return d


def read(actor,channel):
    return dialogs.history({'tenant_id':A,'telegram_id':actor},'connect',1001 if channel==CA else 1002,channel,G)['messages']


def assert_copy(messages,direction,channels):
    assert len(messages)==1
    message=messages[0]
    assert message['direction']==direction
    assert {source['channel_id'] for source in message['sources']}==set(channels)
    assert message['source_channel_id'] in channels
    return message


def change(actor,action='accept',revision=0,**extra):
    return lifecycle.mutate({'tenant_id':A,'telegram_id':actor},{'provider':'connect','channel_id':CA,'conversation_id':G,'lead_id':1001,'action':action,'expected_revision':revision,'request_id':str(uuid.uuid4()),**extra})


def test_owner_eligible_for_both_sources_sees_one_outgoing_echo(projection):
    message=assert_copy(read(11,CA),'outgoing',[CA,CB])
    assert message['source_channel_id']==CA
    assert {source['direction'] for source in message['sources']}=={'incoming','outgoing'}
    assert projection.execute('SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id IN (1001,1002)',(A,))[0]['n']==2


def test_employee_bound_only_to_b_sees_single_incoming_without_a_metadata(projection):
    message=assert_copy(read(33,CB),'incoming',[CB])
    assert message['source_channel_id']==CB and message['source_lead_id']==1002
    with pytest.raises(TenantLinearError):read(33,CA)


def test_claim_and_transfer_filter_copies_before_group_dedup(projection):
    from tenant_dialog_rollout import transition
    with projection.cursor() as cur:transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    projection.commit()
    assert_copy(read(11,CA),'outgoing',[CA,CB])
    change(44)
    # Owner has no exception: A is hidden, so B's incoming cannot be converted
    # using inaccessible outgoing evidence or leak A through sources[].
    assert_copy(read(11,CB),'incoming',[CB])
    with pytest.raises(TenantLinearError) as denied:read(11,CA)
    assert denied.value.status==404
    assert_copy(read(44,CA),'outgoing',[CA,CB])
    assert_copy(read(33,CB),'incoming',[CB])
    def listed(actor):
        return {dialog['channel_id'] for dialog in
                dialogs.listing({'tenant_id':A,'telegram_id':actor},channel='connect:'+CA)['dialogs']
                if dialog['conversation_id']==G}

    assert CA in listed(44)
    change(44,'transfer',1,target_employee_id=11)
    assert CA not in listed(44)
    from tenant_dialog_presence import set_status
    set_status({'tenant_id':A,'telegram_id':11},{'status':'lunch'})
    assert CA in listed(11)
    assert_copy(read(11,CA),'outgoing',[CA,CB])
    assert_copy(read(44,CB),'incoming',[CB])
    with pytest.raises(TenantLinearError) as former:read(44,CA)
    assert former.value.status==404
    assert projection.execute('SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id IN (1001,1002)',(A,))[0]['n']==2
