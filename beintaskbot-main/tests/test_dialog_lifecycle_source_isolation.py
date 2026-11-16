"""Synthetic same-client/source isolation. No provider calls or real messages."""
import uuid
import pytest
from test_tenant_support_inbox import database, db, A, CA, PROFILE
from test_dialog_lifecycle_pglite import episodes, payload
import tenant_dialog_lifecycle as lifecycle
import tenant_operator_dialogs as dialogs
import tenant_connect_dialog_equivalence as equivalence
from tenant_dialog_rollout import guard
from tenant_linear_policy import TenantLinearError

SECOND='cccccccc-cccc-4ccc-8ccc-cccccccccccc'
EMPLOYEE={'tenant_id':A,'telegram_id':33}


@pytest.fixture
def sources(episodes,monkeypatch):
    monkeypatch.setattr(dialogs,'_connect',lambda:episodes)
    episodes.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'2135102 synthetic')",(A,SECOND))
    episodes.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'client',45)",(A,SECOND))
    episodes.execute('''INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
      VALUES(%s::uuid,%s::uuid,45,'source-two-synthetic',%s,'incoming','Only source two')''',
      (str(uuid.uuid4()),A,'connect:'+SECOND))
    episodes.commit()
    return episodes


def rows(db,sql,args=()):
    result=db.execute(sql,args);db.commit();return result


def test_transfer_and_close_same_client_source_one_preserves_source_two(sources):
    lifecycle.mutate(EMPLOYEE,payload())
    lifecycle.mutate(EMPLOYEE,{**payload(),'channel_id':SECOND,'lead_id':45})
    workflow_sql='SELECT * FROM saas_dialog_workflows WHERE tenant_id=%s::uuid AND channel_id=%s::uuid'
    episode_sql='SELECT * FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND channel_id=%s::uuid'
    second_before=rows(sources,workflow_sql,(A,SECOND))
    episode_before=rows(sources,episode_sql,(A,SECOND))
    raw_before=rows(sources,'SELECT id,channel,kommo_lead_id,body FROM saas_crm_messages WHERE tenant_id=%s::uuid ORDER BY id',(A,))
    initial_history=dialogs.history(EMPLOYEE,'connect',45,SECOND,'client')
    assert [m['body'] for m in initial_history['messages']]==['Only source two']
    lifecycle.mutate(EMPLOYEE,payload('transfer',1,target_employee_id=11))
    with pytest.raises(TenantLinearError):dialogs.history(EMPLOYEE,'connect',41,CA,'client')
    assert [m['body'] for m in dialogs.history(PROFILE,'connect',41,CA,'client')['messages']]==['Internet is down']
    lifecycle.mutate(PROFILE,payload('close',2))
    assert rows(sources,workflow_sql,(A,SECOND))==second_before
    assert rows(sources,episode_sql,(A,SECOND))==episode_before
    second_history=dialogs.history(EMPLOYEE,'connect',45,SECOND,'client')
    assert [m['body'] for m in second_history['messages']]==['Only source two']
    assert second_history['dialog']['lifecycle']['state']=='accepted'
    # No real connector credential is installed in this fixture. Preserve its
    # independent transport availability, and separately prove lifecycle update.
    assert second_history['dialog']['can_send']==initial_history['dialog']['can_send']
    with sources.cursor() as cur:
        lifecycle.require_access(cur,A,33,'connect',SECOND,'client','update')
    sources.commit()
    with pytest.raises(TenantLinearError):dialogs.history(PROFILE,'connect',45,SECOND,'client')
    assert rows(sources,'SELECT id,channel,kommo_lead_id,body FROM saas_crm_messages WHERE tenant_id=%s::uuid ORDER BY id',(A,))==raw_before


def test_claimed_pair_blocks_only_its_source_other_source_can_link(sources):
    with sources.cursor() as cur:equivalence.schema(cur)
    for channel,lead in ((CA,46),(SECOND,47)):
        sources.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'alias@lid',%s)",(A,channel,lead))
    sources.commit()
    lifecycle.mutate(EMPLOYEE,payload())
    before=rows(sources,'SELECT * FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,))
    with sources.cursor() as cur:
        guard(cur,A)
        with pytest.raises(TenantLinearError):equivalence.lock(cur,A,CA,['client','alias@lid'])
    sources.rollback()
    with sources.cursor() as cur:
        guard(cur,A)
        assert equivalence.lock(cur,A,SECOND,['client','alias@lid'])==['alias@lid','client']
        snapshot=equivalence.snapshot(cur,A,SECOND,['client','alias@lid'],[45,47])
        equivalence.activate(cur,A,SECOND,'synthetic-proof',['client','alias@lid'],[45,47],snapshot)
    sources.commit()
    assert rows(sources,'SELECT channel_id::text,source_id,canonical_id FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid AND active',(A,))==[
        {'channel_id':SECOND,'source_id':'alias@lid','canonical_id':'client'}]
    assert rows(sources,'SELECT * FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,))==before
    with sources.cursor() as cur:
        assert equivalence.canonical(cur,A,CA,'alias@lid')=='alias@lid'
        assert equivalence.canonical(cur,A,SECOND,'alias@lid')=='client'
    sources.commit()
