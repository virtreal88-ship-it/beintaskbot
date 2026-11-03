"""Synthetic PGlite-only history/receipt semantics; no provider calls."""
import tenant_operator_copilot as copilot
from tenant_operator_copilot_threads import clear
from test_tenant_support_inbox import database,db,A,CA,PROFILE
from test_assigned_operator_copilot import ready


def test_completed_receipt_remains_replayable_after_later_clear(ready):
    db,_other,generated,data=ready
    data.update(mode='chat',prompt='Synthetic private question')
    result=copilot.run(PROFILE,data)
    with db.cursor() as cur:clear(cur,A,11,CA,'client')
    db.commit()
    replay=copilot.run(PROFILE,data)
    assert replay==result
    assert generated.call_count==1
    assert db.execute('SELECT turns,clear_revision FROM saas_operator_copilot_threads')[0]=={'turns':[],'clear_revision':1}
    stored=db.execute('SELECT status,result FROM saas_chat_ai_runs WHERE request_id=%s::uuid',(data['request_id'],))[0]
    assert stored['status']=='completed' and stored['result']['text']==result['text']
