"""Optional genuine process races; disposable localhost PostgreSQL only."""
from test_connect_channel_multiprocess import database, race
from test_tenant_support_inbox import db, A, CA, CB, PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels
from test_operator_channel_continuation import stopped, request, blocked
import tenant_operator_continuation as continuation
import tenant_ai_agents_store as agents


def test_two_administrators_cannot_apply_one_preview_twice(stopped):
    d=stopped['db'];payload=request()
    values=race(d,[('continue_channel',payload)]*2)
    assert sum(isinstance(v,dict) for v in values)==1
    assert len(d.execute('SELECT * FROM saas_ai_legacy_permits'))==1
    assert len(d.execute("SELECT * FROM saas_tenant_audit_events WHERE action='ai_channel_continuation_granted'"))==1
    assert all(r['state']=='human' for r in d.execute('SELECT state FROM saas_ai_conversation_state'));d.commit()


def test_duplicate_webhooks_and_workers_after_resume(stopped):
    f=stopped;d=f['db'];continuation.grant(PROFILE,request())
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    mid,n=f['incoming'](CA);assert n==1
    payload=dict(tenant=A,channel_id=CA,conversation_id='client',lead_id=41,message_id=mid,event_id=mid,text='duplicate')
    assert race(d,[('enqueue',payload)]*2)==[0,0]
    assert sum(bool(v) for v in race(d,[('claim',{})]*2))==1
    assert blocked(d,CB)
    assert not d.execute('SELECT * FROM test_external_sends');d.commit()


def test_grant_racing_inbound_never_resumes_or_sends(stopped):
    d=stopped['db'];payload=request()
    inbound=dict(tenant=A,channel_id=CA,conversation_id='client',lead_id=41,message_id='race',event_id='race',text='hello')
    race(d,[('continue_channel',payload),('enqueue',inbound)])
    assert all(r['state']=='human' for r in d.execute('SELECT state FROM saas_ai_conversation_state'))
    assert not d.execute("SELECT * FROM saas_ai_agent_jobs WHERE state IN ('queued','processing')")
    assert not d.execute('SELECT * FROM test_external_sends');d.commit()
