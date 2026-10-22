"""Real PostgreSQL group fences, no external sends."""
import json
from test_connect_channel_multiprocess import database, race, child
from test_group_coordination import group, config, incoming, G, groups, agents, A, CA, CB, PROFILE
from test_tenant_support_inbox import db
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels


def ready(group):
    d=group['db']
    d.execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))
    d.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid",(A,));d.commit()
    groups.configure(PROFILE,config());agents.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)


def test_group_concurrent_webhooks(group):
    ready(group);d=group['db']
    routes=d.execute('SELECT connect_channel_id::text channel,lead_id FROM saas_connect_conversations WHERE conversation_id=%s',(G,));d.commit()
    operations=[]
    for r in routes:
        payload=dict(tenant=A,channel_id=r['channel'],conversation_id=G,lead_id=r['lead_id'],message_id=r['channel'],event_id=r['channel'],text='test',is_group=True,provider_message_id='SAME_GROUP_EVENT')
        operations.extend([('enqueue',payload)]*2)
    assert sorted(race(d,operations))==[0,0,0,1]
    rows=d.execute('SELECT channel_id FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid',(A,));d.commit()
    assert [r['channel_id'] for r in rows]==[CA]


def test_group_durable_reservation_between_processes(group):
    ready(group);d=group['db'];incoming(group,CA,'RESERVE_PROCESS');group['process']()
    draft=d.execute('SELECT id FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid',(A,))[0]['id'];d.commit()
    payload=dict(tenant=A,channel=CA,conversation=G,run_id=str(draft))
    assert sorted(race(d,[('group_reserve',payload)]*3))==[False,False,True]
    assert race(d,[('group_reserve',payload)])==[False]


def test_group_takeover_invalidates_other_process_reservation(group):
    ready(group);d=group['db'];incoming(group,CA,'TAKEOVER_PROCESS');group['process']()
    draft=d.execute('SELECT id FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid',(A,))[0]['id'];d.commit()
    agents.set_conversation_state(PROFILE,G,'human',provider='connect',channel_id=CB)
    assert race(d,[('group_reserve',dict(tenant=A,channel=CA,conversation=G,run_id=str(draft)))])==[False]
    assert not d.execute('SELECT * FROM saas_ai_group_deliveries');d.commit()


def test_group_competing_final_senders(group):
    from test_connect_channel_multiprocess import prepare_send
    ready(group);d=group['db'];incoming(group,CA,'FINAL_SEND_PROCESS');group['process']()
    draft=d.execute('SELECT id FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid',(A,))[0]['id'];d.commit()
    payload,key=prepare_send(group,CA)
    revision=d.execute('SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id=%s',(A,CA,G))[0]['revision'];d.commit()
    payload.update(lead_id=1001,conversation_id=G,run_id=str(draft),expected_revision=revision)
    values=race(d,[('send',payload)]*3,key)
    assert sum(v is not None for v in values)==1
    assert len(d.execute('SELECT * FROM test_external_sends WHERE run_id=%s',(str(draft),)))==1;d.commit()
