"""Exact dialogue support metadata on a disposable PostgreSQL database."""
import uuid

import pytest

import tenant_operator_dialogs as dialogs
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_company_binding_postgres import _deal
from test_dialog_lifecycle_pglite import episodes
from test_support_inbox_scopes import configure, seeded
from test_tenant_support_inbox import A, CA, PROFILE, db


def _insert(db, channel, conversation, status, employee, *, days, resolved=None, outcome=""):
    ident=str(uuid.uuid4())
    db.execute("""INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,
      source,status,assigned_to,created_at,resolved_at,completion_outcome)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,'manual',%s,%s,
             now()-make_interval(days=>%s),now()-make_interval(days=>%s),%s)""",
      (A,ident,channel,conversation,status,employee,days,resolved,outcome))
    db.commit()
    return ident


def test_history_case_is_independent_from_group_assignee_and_exact_source(episodes, monkeypatch):
    db=episodes
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    second=str(uuid.uuid4())
    group='120000000000000001@g.us'
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second source')",(A,second))
    for channel,lead in ((CA,51),(second,52)):
        db.execute("""INSERT INTO saas_connect_conversations
          (tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind)
          VALUES(%s::uuid,%s::uuid,%s,%s,'group')""",(A,channel,group,lead))
    db.execute("""INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,assigned_to)
      VALUES(%s::uuid,%s,'human',11)""",(A,group))
    db.commit()
    def summary(channel,lead):
        result=dialogs.history(PROFILE,'connect',lead,channel,group,support_case_only=True)
        assert set(result)=={'dialog'}
        assert set(result['dialog'])=={'provider','lead_id','channel_id','conversation_id','support_case'}
        return result['dialog']
    _insert(db,CA,group,'resolved',33,days=5,resolved=1)
    _insert(db,CA,group,'resolved',11,days=4,resolved=0,outcome='test_stopped')
    active=_insert(db,CA,group,'in_progress',33,days=3)
    _insert(db,second,group,'waiting',None,days=0)
    regular=dialogs.history(PROFILE,'connect',51,CA,group)
    assert regular['dialog']['assignee_name'] and regular['dialog']['assignee_name']!='Employee'
    assert 'support_case' not in regular['dialog']
    assert summary(CA,51)['support_case']=={'status':'in_progress','responsible_name':'Employee'}
    assert summary(second,52)['support_case']=={'status':'waiting','responsible_name':None}
    with pytest.raises(TenantLinearError):
        summary(second,51)
    for missing_channel, missing_conversation in (('',group),(CA,'')):
        with pytest.raises(TenantLinearError):
            dialogs.history(PROFILE,'connect',51,missing_channel,missing_conversation,support_case_only=True)
    db.execute("DELETE FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,active))
    db.commit()
    assert summary(CA,51)['support_case']=={'status':'resolved','responsible_name':'Employee'}
    kommo=_deal(db)
    assert dialogs.history(PROFILE,kommo['provider'],kommo['lead_id'],
        kommo['channel_id'],kommo['conversation_id'],support_case_only=True)['dialog']['support_case'] is None


def _permitted_summary(db, profile, conversation):
    with db.cursor() as cur:
        tenant,_,policy=access(cur,profile,'dialogs')
        result=dialogs._linked_support_case(cur,tenant,policy,CA,[conversation])
    db.commit()
    return result


@pytest.mark.parametrize('scope,visible',[('none',False),('self',False),('department',True),('all',True)])
def test_resolved_case_inherits_exact_support_visibility(db, seeded, scope, visible):
    keys,_,_=seeded
    profile=configure(db,view=scope)
    db.execute("UPDATE saas_tenant_members SET display_name='Actual case worker' WHERE tenant_id=%s::uuid AND telegram_id=33",(A,))
    db.execute("""UPDATE saas_support_tickets SET status='resolved',resolved_at=now(),assigned_to=33
      WHERE tenant_id=%s::uuid AND id=%s::uuid""",(A,keys[1]))
    db.commit()
    expected={'status':'resolved','responsible_name':'Actual case worker'} if visible else None
    assert _permitted_summary(db,profile,'case-1')==expected
    assert _permitted_summary(db,profile,'no-such-route') is None


def test_legacy_dialog_view_is_not_support_view(db, seeded, monkeypatch):
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    db.execute("""UPDATE saas_tenant_members SET permissions='["dialogs"]'::jsonb
      WHERE tenant_id=%s::uuid AND telegram_id=31""",(A,))
    db.commit()
    assert _permitted_summary(db,{'tenant_id':A,'telegram_id':31},'case-0') is None

def test_compact_http_read_is_no_store_and_skips_enrichment(monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock

    import tenant_message_delivery
    import tenant_operator_api

    reply={'dialog':{'provider':'connect','lead_id':41,'channel_id':CA,
                     'conversation_id':'client','support_case':None}}
    history=Mock(return_value=reply)
    monkeypatch.setattr(tenant_operator_api,'history',history)
    enrichment=Mock(side_effect=AssertionError('Compact read must not enrich message history'))
    monkeypatch.setattr(tenant_message_delivery,'enrich_history',enrichment)
    endpoint=tenant_operator_api.handler(lambda _request:PROFILE,'https://example.test',Mock(),'history')
    query={'provider':'connect','lead_id':'41','channel_id':CA,'conversation_id':'client',
           'support_case_only':'1','before':'ignored','anchor':'ignored'}
    response=asyncio.run(endpoint(SimpleNamespace(method='GET',query=query,headers={})))
    assert response.status==200 and response.headers['Cache-Control']=='no-store'
    assert json.loads(response.body)=={'success':True,'result':reply}
    history.assert_called_once_with(PROFILE,'connect','41',CA,'client',None,None,support_case_only=True)
    enrichment.assert_not_called()
