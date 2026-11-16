"""Execute the unchanged authorized cursor reader against isolated PostgreSQL/WASM."""
import pytest
from test_dialog_lifecycle_group_projection import database, db, flow, channels, group, history_db, projection, A, CA, CB, add
import tenant_chat_history_store as history
from tenant_linear_policy import TenantLinearError


def test_equal_timestamps_insert_during_pagination_and_scope(projection, monkeypatch):
    d=projection;monkeypatch.setattr(history,'_connect',lambda:d)
    session={'tenant_id':A,'telegram_id':11}
    d.execute('''UPDATE saas_tenant_workflow_policies SET value=value || '{"11":{"access_scopes":{"dialogs":{"view":"all"}}}}'::jsonb WHERE tenant_id=%s::uuid AND policy_key='members' ''',(A,));d.commit()
    for n in range(10,135):add(d,n,CA)
    d.execute("UPDATE saas_crm_messages SET happened_at='2026-11-16T00:00:00Z' WHERE tenant_id=%s::uuid AND kommo_lead_id=1001",(A,));d.commit()
    original={r['external_id'] for r in d.execute('SELECT external_id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=1001',(A,))}
    first=history.page(session,1001,limit=40)
    assert len(first['messages'])==40 and first['next_cursor']
    add(d,200,CA)
    d.execute("UPDATE saas_crm_messages SET happened_at='2026-11-17T00:00:00Z' WHERE external_id='connect:stored_200' AND tenant_id=%s::uuid",(A,));d.commit()
    found=[m['external_id'] for m in first['messages']];cursor=first['next_cursor']
    while cursor:
        result=history.page(session,1001,cursor,40)
        found=[m['external_id'] for m in result['messages']]+found;cursor=result['next_cursor']
    assert set(found)==original and len(found)==len(set(found))
    assert history.page(session,1001,limit=40)['messages'][-1]['external_id']=='connect:stored_200'
    with pytest.raises(ValueError):history.page({**session,'telegram_id':33},1001,first['next_cursor'],40)
    with pytest.raises(TenantLinearError):history.page({**session,'telegram_id':33},1001,limit=40)
    allowed=history.page({**session,'telegram_id':33},1002,limit=40)
    assert allowed['messages'] and all(m['channel']=='connect:'+CB for m in allowed['messages'])
    d.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=11',(A,));d.commit()
    with pytest.raises(TenantLinearError):history.page(session,1001,first['next_cursor'],40)
