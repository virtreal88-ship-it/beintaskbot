"""Local redacted CRM journal reads with current source-card R only."""
import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from multidict import MultiDict
from test_tenant_support_inbox import database, db, A, B
from test_crm_source_card_relations import staff, matrix
from test_crm_scoped_access_postgres import native, set_scopes
import tenant_crm_sync_history as history
import tenant_crm_sync_history_api as api
import tenant_member_store as members
from tenant_crm_native_access import CRMAccessError


@pytest.fixture
def journal(native,monkeypatch):
    monkeypatch.setattr(history,'_connect',native['factory'])
    set_scopes(native,81,matrix(deals={'view':'self'},customers={'view':'self'},companies={'view':'self'}))
    return native


def rows(conn,sql,params=()):
    result=conn.execute(sql,params)
    return result.fetchall() if hasattr(result,'fetchall') else result


def add_event(f,kind='scalar',key='self',entity='deal',state='needs_verification',tenant=A,created='2026-11-01T12:00:00+00:00',ident=None):
    event=str(uuid.uuid4());source=ident or f['ids'][{'deal':'deals','contact':'customers','company':'companies'}[entity]][key]
    with f['factory']() as conn:
        if kind=='scalar':
            conn.execute('''INSERT INTO saas_crm_scalar_outbox(id,tenant_id,provider,entity_type,internal_id,actor_id,
              source_action,local_revision,field_mask,before_values,values,binding,external_id,content_hash,state,error_code,created_at,updated_at)
              VALUES(%s::uuid,%s::uuid,'kommo',%s,%s::uuid,81,'update',%s::timestamptz,'["title"]',
              '{"title":"SECRET OLD"}','{"title":"SECRET NEW"}','{"account_id":"SECRET ACCOUNT"}','SECRET EXTERNAL',
              'hash',%s,'SECRET ERROR',%s::timestamptz,%s::timestamptz)''',(event,tenant,entity,source,created,state,created,created))
        elif kind=='relation':
            intent={'source':{'entity_type':entity,'internal_id':source},'local_revision':created,
                    'binding':{'account_id':'SECRET ACCOUNT'},'before':{'external_id':'SECRET TARGET'}}
            conn.execute('''INSERT INTO saas_crm_relation_outbox(id,tenant_id,changed_type,changed_id,relation,intent,intent_hash,
              state,error_code,observation,created_at,updated_at) VALUES(%s::uuid,%s::uuid,'deal',%s::uuid,
              'deal_contact',%s::jsonb,'hash',%s,'SECRET ERROR','{"secret":"SECRET OBSERVATION"}',%s::timestamptz,%s::timestamptz)''',
              (event,tenant,f['ids']['deals']['other'],json.dumps(intent),state,created,created))
        else:
            conn.execute('''INSERT INTO saas_crm_sync_outbox(id,tenant_id,provider,entity_type,internal_id,source_updated_at,
              event_key,state,error_code,created_at,updated_at) VALUES(%s::uuid,%s::uuid,'kommo',%s,%s::uuid,
              %s::timestamptz,%s,%s,'SECRET ERROR',%s::timestamptz,%s::timestamptz)''',
              (event,tenant,entity,source,created,event,state,created,created))
    return event


@pytest.mark.parametrize('kind',['scalar','relation','legacy'])
def test_history_redacts_snapshots_targets_provider_and_actor(journal,kind):
    event=add_event(journal,kind,state='conflict')
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)
    assert result['external_state_checked'] is False
    row=result['events'][0]
    assert row['event_id']==event and row['kind']==kind and row['state']=='conflict'
    assert set(row)=={'event_id','kind','state','attempt_outcome','local_revision','created_at','recorded_at','issue'}
    assert row['issue']=='conflict'
    assert 'SECRET' not in json.dumps(result)
    assert 'provider' not in json.dumps(result) and 'actor_id' not in json.dumps(result)


@pytest.mark.parametrize('entity,module',[('deal','deals'),('contact','customers'),('company','companies')])
def test_current_exact_source_read_not_update_grants(journal,entity,module):
    event=add_event(journal,'relation',entity=entity,state='pending')
    ident=journal['ids'][module]['self']
    assert history.read_history(A,entity,ident,actor_id=81)['events'][0]['event_id']==event
    assert history.read_history(A,entity,journal['ids'][module]['other'],actor_id=81) is None
    set_scopes(journal,81,matrix(**{module:{'view':'none','update':'all'}}))
    with pytest.raises(CRMAccessError):history.read_history(A,entity,ident,actor_id=81)
    with pytest.raises(CRMAccessError):history.read_history(A,entity,ident,actor_id=None)


def test_reverse_relation_history_belongs_to_source_not_changed_target(journal):
    event=add_event(journal,'relation',entity='contact',state='pending')
    set_scopes(journal,81,matrix(deals={'view':'all'},customers={'view':'self'}))
    assert history.read_history(A,'deal',journal['ids']['deals']['other'],actor_id=81)['events']==[]
    assert history.read_history(A,'contact',journal['ids']['customers']['self'],actor_id=81)['events'][0]['event_id']==event


def test_cross_tenant_archive_and_current_membership(journal):
    add_event(journal,key='foreign',tenant=B,state='pending')
    assert history.read_history(A,'deal',journal['ids']['deals']['foreign'],actor_id=81) is None
    ident=journal['ids']['deals']['self'];add_event(journal,state='pending')
    with journal['factory']() as conn:conn.execute('UPDATE saas_crm_native_deals SET archived_at=now() WHERE id=%s::uuid',(ident,))
    assert history.read_history(A,'deal',ident,actor_id=81) is None
    with journal['factory']() as conn:conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s::uuid AND telegram_id=81',(A,))
    with pytest.raises(CRMAccessError):history.read_history(A,'deal',ident,actor_id=81)


def test_pagination_is_bounded_stable_and_scope_preserving(journal):
    ids={add_event(journal,state='pending') for _ in range(5)}
    add_event(journal,key='other',state='pending')
    seen=[];cursor={}
    while True:
        result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81,limit=2,**cursor)
        assert len(result['events'])<=2
        seen.extend(row['event_id'] for row in result['events'])
        if not result['has_more']:break
        cursor=result['next_cursor']
    assert len(seen)==5 and set(seen)==ids
    with pytest.raises(ValueError):history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81,limit=51)
    with pytest.raises(ValueError):history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81,before_kind='legacy')


def test_unknown_attempt_overrides_legacy_recorded_success(journal):
    event=add_event(journal,'legacy',state='synced')
    with journal['factory']() as conn:conn.execute("INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome) VALUES(%s::uuid,'unknown')",(event,))
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)['events'][0]
    assert result['state']=='unknown' and result['issue']=='outcome_unknown'


def test_http_no_store_context_and_stale_auth(journal):
    add_event(journal,state='pending')
    profile=members.member(A,81)
    request=SimpleNamespace(method='GET',match_info={'id':journal['ids']['deals']['self']},query=MultiDict())
    handle=api.handler(lambda _:profile,Mock(),entity='deal')
    def get():
        response=asyncio.run(handle(request))
        assert 'no-store' in response.headers['Cache-Control']
        assert response.headers['Vary']=='Cookie'
        return response
    assert get().status==200
    request.query=MultiDict({'expected_tenant_id':B,'expected_user_id':'81'})
    assert get().status==409
    request.query=MultiDict([('limit','1'),('limit','2')])
    assert get().status==400
    request.query=MultiDict({'provider':'kommo'})
    assert get().status==400
    request.query=MultiDict()
    set_scopes(journal,81,matrix(deals={'update':'all'}))
    assert get().status==403
    request.method='POST'
    assert get().status==405


def test_historical_retry_is_unknown_without_durable_no_send_evidence(journal):
    event=add_event(journal,'legacy',state='pending')
    with journal['factory']() as conn:
        conn.execute('UPDATE saas_crm_sync_outbox SET attempts=2 WHERE id=%s::uuid',(event,))
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)['events'][0]
    assert result['state']=='unknown' and result['issue']=='outcome_unknown'
    with journal['factory']() as conn:
        conn.execute("INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome,claim_attempt) VALUES(%s::uuid,'not_started',2)",(event,))
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)['events'][0]
    assert result['state']=='pending' and result['attempt_outcome']=='not_started'


def test_relation_revision_text_and_unknown_errors_never_echo(journal):
    event=add_event(journal,'relation',state='disabled')
    with journal['factory']() as conn:
        conn.execute("UPDATE saas_crm_relation_outbox SET intent=jsonb_set(intent,'{local_revision}','\"SECRET MALFORMED\"') WHERE id=%s::uuid",(event,))
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)
    assert result['events'][0]['local_revision'] is None
    assert result['events'][0]['issue']=='not_completed'
    assert 'SECRET' not in json.dumps(result)


def test_department_transfer_and_tenant_suspension_apply_to_history(journal):
    set_scopes(journal,81,matrix(deals={'view':'department'}))
    event=add_event(journal,key='peer',state='pending')
    ident=journal['ids']['deals']['peer']
    assert history.read_history(A,'deal',ident,actor_id=81)['events'][0]['event_id']==event
    with journal['factory']() as conn:
        conn.execute('UPDATE saas_tenant_members SET department_id=%s::uuid WHERE tenant_id=%s::uuid AND telegram_id=82',(journal['departments'][1],A))
    assert history.read_history(A,'deal',ident,actor_id=81) is None
    with journal['factory']() as conn:conn.execute("UPDATE saas_tenants SET status='suspended' WHERE id=%s::uuid",(A,))
    with pytest.raises(CRMAccessError):history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)


@pytest.mark.parametrize('state',['pending','processing'])
@pytest.mark.parametrize('evidence', ['missing', None, 0, 1, 2, 3])
def test_unsent_history_evidence_must_match_exact_legacy_claim(journal,state,evidence):
    event=add_event(journal,'legacy',state=state)
    with journal['factory']() as conn:
        conn.execute('UPDATE saas_crm_sync_outbox SET attempts=2 WHERE id=%s::uuid',(event,))
        if evidence!='missing':
            conn.execute("INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome,claim_attempt) VALUES(%s::uuid,'not_started',%s)",(event,evidence))
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)['events'][0]
    if evidence==2:
        assert result['state']==state and result['attempt_outcome']=='not_started'
    else:
        assert result['state']=='unknown' and result['attempt_outcome']=='unknown'
        assert result['issue']=='outcome_unknown'


@pytest.mark.parametrize('state',['pending','processing'])
@pytest.mark.parametrize('evidence',[None,1,2])
def test_completed_attempt_does_not_prove_current_queued_claim_finished(journal,state,evidence):
    event=add_event(journal,'legacy',state=state)
    with journal['factory']() as conn:
        conn.execute('UPDATE saas_crm_sync_outbox SET attempts=2 WHERE id=%s::uuid',(event,))
        conn.execute("INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome,claim_attempt) VALUES(%s::uuid,'completed',%s)",(event,evidence))
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)['events'][0]
    assert result['state']=='unknown' and result['issue']=='outcome_unknown'


def test_synced_record_with_no_send_marker_is_contradictory(journal):
    event=add_event(journal,'legacy',state='synced')
    with journal['factory']() as conn:
        conn.execute('UPDATE saas_crm_sync_outbox SET attempts=2 WHERE id=%s::uuid',(event,))
        conn.execute("INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome,claim_attempt) VALUES(%s::uuid,'not_started',2)",(event,))
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)['events'][0]
    assert result['state']=='unknown' and result['issue']=='outcome_unknown'


@pytest.mark.parametrize('outcome',['sending','unknown','not_started','completed'])
def test_outbox_unknown_is_not_downgraded_by_attempt_evidence(journal,outcome):
    event=add_event(journal,'legacy',state='unknown')
    with journal['factory']() as conn:
        conn.execute('UPDATE saas_crm_sync_outbox SET attempts=2 WHERE id=%s::uuid',(event,))
        conn.execute('INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome,claim_attempt) VALUES(%s::uuid,%s,2)',(event,outcome))
    result=history.read_history(A,'deal',journal['ids']['deals']['self'],actor_id=81)['events'][0]
    assert result['state']=='unknown' and result['issue']=='outcome_unknown'
    if outcome in ('sending','unknown'):assert result['attempt_outcome']==outcome
