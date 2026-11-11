"""Isolated writer-transition journals; all provider I/O is mocked."""
import asyncio
import uuid
from unittest.mock import AsyncMock, Mock
import pytest
from test_tenant_support_inbox import database, db, A
from test_tenant_crm_relation_delivery_postgres import graph
from test_crm_scoped_scalar_postgres import scalar, Remote
import tenant_crm_native_contacts as contacts
import tenant_crm_sync_outbox as legacy
from tenant_crm_scalar_delivery import dispatch
from tenant_crm_writer_fence import legacy_uncertain


def legacy_event(scalar, state='processing', attempts=1):
    ident = scalar['contacts'][0].internal_id
    row = scalar['db'].execute('''INSERT INTO saas_crm_sync_outbox
      (id,tenant_id,provider,entity_type,internal_id,source_updated_at,event_key,state,attempts)
      SELECT %s::uuid,tenant_id,'kommo','contact',id,updated_at,%s,%s,%s
      FROM saas_crm_native_contacts WHERE id=%s::uuid RETURNING *''',
      (str(uuid.uuid4()),str(uuid.uuid4()),state,attempts,ident))[0]
    scalar['db'].commit()
    return row


def test_legacy_timeout_blocks_scalar_and_duplicate_even_after_rollback(scalar,monkeypatch):
    row=legacy_event(scalar)
    monkeypatch.setattr(legacy,'_connect',lambda:scalar['db'])
    monkeypatch.setattr(legacy,'_linked',lambda *_:{'external_id':'1'})
    monkeypatch.setattr(legacy,'_conflicts',AsyncMock(return_value=False))
    request=AsyncMock(side_effect=TimeoutError('after apply'))
    with pytest.raises(TimeoutError): asyncio.run(legacy._send(row,None,request,Mock()))
    # Existing retry scheduling cannot remove durable uncertain-send evidence.
    legacy.finish(row,'pending','provider_unavailable')
    ident=scalar['contacts'][0].internal_id
    saved=contacts.update_contact(A,ident,'First scoped name','',actor_id=81)
    assert saved['sync']['state']=='needs_verification'
    assert saved['sync']['error_code']=='scalar_legacy_outcome_unknown'
    remote=Remote(scalar['binding'],id='1')
    assert dispatch(lambda:scalar['db'],saved['sync']['event_id'],remote)=='needs_verification'
    assert not remote.calls
    scalar['db'].execute("UPDATE saas_crm_sync_outbox SET state='processing',attempts=attempts+1 WHERE id=%s::uuid",(row['id'],))
    scalar['db'].commit()
    row={**row,'attempts':row['attempts']+1}
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','false')
    assert asyncio.run(legacy._send(row,None,request,Mock()))==('unknown','legacy_outcome_unknown')
    assert request.await_count==1


@pytest.mark.parametrize('state,attempts',[('processing',1),('unknown',1),('pending',2)])
def test_historical_legacy_uncertainty_blocks_first_scoped_dispatch(scalar,state,attempts):
    legacy_event(scalar,state,attempts)
    saved=contacts.update_contact(A,scalar['contacts'][0].internal_id,'Scoped','',actor_id=81)
    assert saved['sync']['state']=='needs_verification'
    assert saved['sync']['error_code']=='scalar_legacy_outcome_unknown'


def test_stale_worker_cannot_send_with_old_claim(scalar,monkeypatch):
    row=legacy_event(scalar)
    monkeypatch.setattr(legacy,'_connect',lambda:scalar['db'])
    scalar['db'].execute('UPDATE saas_crm_sync_outbox SET attempts=attempts+1 WHERE id=%s::uuid',(row['id'],));scalar['db'].commit()
    request=AsyncMock()
    assert asyncio.run(legacy._send(row,None,request,Mock()))==('stale','legacy_claim_changed')
    request.assert_not_called()


def test_historical_retry_without_send_evidence_is_not_replayed(scalar,monkeypatch):
    row=legacy_event(scalar,attempts=2)
    monkeypatch.setattr(legacy,'_connect',lambda:scalar['db'])
    request=AsyncMock()
    assert asyncio.run(legacy._send(row,None,request,Mock()))==('unknown','legacy_historical_attempt_unverified')
    request.assert_not_called()


def test_gate_off_unmarked_legacy_success_retains_payload(scalar,monkeypatch):
    row=legacy_event(scalar,attempts=2)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','false')
    monkeypatch.setattr(legacy,'_connect',lambda:scalar['db'])
    monkeypatch.setattr(legacy,'_linked',lambda *_:{'external_id':'1'})
    monkeypatch.setattr(legacy,'_conflicts',AsyncMock(return_value=False))
    request=AsyncMock(return_value={})
    assert asyncio.run(legacy._send(row,None,request,Mock()))==('synced','')
    assert request.await_args.args==(A,'PATCH','contacts/1')
    assert request.await_args.kwargs=={'json_body':[{'name':'Synthetic'}]}
    assert not scalar['db'].execute('SELECT * FROM saas_crm_legacy_delivery_attempts WHERE event_id=%s::uuid',(row['id'],))


def test_dispatch_rechecks_legacy_uncertainty_after_local_save(scalar):
    saved=contacts.update_contact(A,scalar['contacts'][0].internal_id,'Scoped','',actor_id=81)
    assert saved['sync']['state']=='pending'
    legacy_event(scalar,'unknown',1)
    remote=Remote(scalar['binding'],id='1')
    assert dispatch(lambda:scalar['db'],saved['sync']['event_id'],remote)=='needs_verification'
    assert not remote.calls


def test_disabled_policy_does_not_erase_historical_uncertainty(scalar,monkeypatch):
    row=legacy_event(scalar,'pending',1)
    scalar['db'].execute("UPDATE saas_crm_provider_settings SET sync_direction='off' WHERE entity_type='contact'")
    scalar['db'].commit()
    monkeypatch.setattr(legacy,'_connect',lambda:scalar['db'])
    request=AsyncMock()
    asyncio.run(legacy.run_batch(request,Mock(),Mock()))
    stored=scalar['db'].execute('SELECT state FROM saas_crm_sync_outbox WHERE id=%s::uuid',(row['id'],))[0]
    assert stored['state']=='unknown'
    request.assert_not_called()


def link_event(scalar,row):
    with scalar['db'].cursor() as cur:
        legacy.record_link(cur,tenant_id=A,provider='kommo',entity_type='contact',
            internal_id=row['internal_id'],external_id='1',sync_state='synced')
    scalar['db'].commit()


def attempt(scalar,row):
    return scalar['db'].execute('''SELECT outcome,claim_attempt FROM saas_crm_legacy_delivery_attempts
      WHERE event_id=%s::uuid''',(row['id'],))[0]


def first_busy_claim(scalar,monkeypatch,*,linked=True):
    row=legacy_event(scalar,'pending',0)
    monkeypatch.setattr(legacy,'_connect',lambda:scalar['db'])
    if linked:link_event(scalar,row)
    claimed=legacy.claim()
    # PGlite is one session. Mock only lock admission; every journal/claim/state
    # transition below uses actual SQL, not a mocked delivery implementation.
    query=scalar['db'].query
    def busy(sql,params=()):
        if 'pg_try_advisory_lock' in sql:return [{'locked':False}]
        return query(sql,params)
    request=AsyncMock()
    with monkeypatch.context() as patch:
        patch.setattr(scalar['db'],'query',busy)
        assert asyncio.run(legacy._send(claimed,None,request,Mock()))==('busy','legacy_card_busy')
    request.assert_not_called()
    assert attempt(scalar,row)=={'outcome':'not_started','claim_attempt':1}
    return claimed


def expire_claim(scalar,row):
    scalar['db'].execute("UPDATE saas_crm_sync_outbox SET lease_until=now()-interval '1 hour' WHERE id=%s::uuid",(row['id'],))
    scalar['db'].commit()


@pytest.mark.parametrize('linked',[False,True])
def test_first_busy_claim_retries_without_inventing_possible_send(scalar,monkeypatch,linked):
    first=first_busy_claim(scalar,monkeypatch,linked=linked)
    expire_claim(scalar,first)
    second=legacy.claim()
    assert second['attempts']==2
    assert attempt(scalar,second)=={'outcome':'not_started','claim_attempt':2}
    request=AsyncMock(return_value={'_embedded':{'contacts':[{'id':1}]}})
    monkeypatch.setattr(legacy,'_conflicts',AsyncMock(return_value=False))
    assert asyncio.run(legacy._send(first,None,request,Mock()))==('stale','legacy_claim_changed')
    assert asyncio.run(legacy._send(second,None,request,Mock()))==('synced','')
    assert request.await_count==1
    assert request.await_args.args[1]==('PATCH' if linked else 'POST')
    assert attempt(scalar,second)=={'outcome':'completed','claim_attempt':2}


@pytest.mark.parametrize('state',['processing','pending'])
def test_proven_unsent_busy_claim_does_not_block_scalar_save_or_dispatch(scalar,monkeypatch,state):
    row=first_busy_claim(scalar,monkeypatch)
    if state=='pending':legacy.finish(row,'pending','legacy_card_busy')
    saved=contacts.update_contact(A,row['internal_id'],'Scoped','',actor_id=81)
    assert saved['sync']['state']=='pending'
    remote=Remote(scalar['binding'],id='1')
    assert dispatch(lambda:scalar['db'],saved['sync']['event_id'],remote)=='synced'
    assert len(remote.writes)==1
    # The old actorless event still encounters the persistent scoped barrier.
    scalar['db'].execute("UPDATE saas_crm_sync_outbox SET lease_until=now()-interval '1 hour',next_attempt_at=now() WHERE id=%s::uuid",(row['id'],))
    scalar['db'].commit()
    retry=legacy.claim();request=AsyncMock()
    assert retry['attempts']==2
    assert asyncio.run(legacy._send(retry,None,request,Mock()))==('conflict','legacy_scoped_revision_conflict')
    request.assert_not_called()


@pytest.mark.parametrize('outcome',['sending','unknown'])
def test_reclaim_never_relabels_possible_send_as_not_started(scalar,monkeypatch,outcome):
    row=legacy_event(scalar,'processing',1);link_event(scalar,row)
    scalar['db'].execute('''INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome,claim_attempt)
      VALUES(%s::uuid,%s,1)''',(row['id'],outcome));scalar['db'].commit()
    monkeypatch.setattr(legacy,'_connect',lambda:scalar['db'])
    expire_claim(scalar,row);retry=legacy.claim()
    assert retry['attempts']==2
    assert attempt(scalar,retry)=={'outcome':outcome,'claim_attempt':1}
    request=AsyncMock()
    assert asyncio.run(legacy._send(retry,None,request,Mock()))==('unknown','legacy_outcome_unknown')
    saved=contacts.update_contact(A,row['internal_id'],'Scoped','',actor_id=81)
    assert saved['sync']['error_code']=='scalar_legacy_outcome_unknown'
    request.assert_not_called()


def test_mismatched_unsent_claim_is_not_evidence_for_a_newer_attempt(scalar,monkeypatch):
    row=first_busy_claim(scalar,monkeypatch)
    scalar['db'].execute('UPDATE saas_crm_sync_outbox SET attempts=2 WHERE id=%s::uuid',(row['id'],))
    scalar['db'].commit()
    with scalar['db'].cursor() as cur:assert legacy_uncertain(cur,A,'contact',row['internal_id'])
    expire_claim(scalar,row);retry=legacy.claim()
    assert retry['attempts']==3 and attempt(scalar,row)['claim_attempt']==1
    request=AsyncMock()
    assert asyncio.run(legacy._send(retry,None,request,Mock()))==('unknown','legacy_historical_attempt_unverified')
    request.assert_not_called()


def test_reclaim_preserves_unknown_recorded_after_its_initial_journal_read(scalar,monkeypatch):
    row=first_busy_claim(scalar,monkeypatch)
    expire_claim(scalar,row)
    query=scalar['db'].query
    def late_unknown(sql,params=()):
        if "VALUES(%s::uuid,'not_started',%s) ON CONFLICT" in sql:
            # A sender's exception handler can mark unknown after rolling back
            # its admission transaction. The claim upsert must not overwrite it.
            query("UPDATE saas_crm_legacy_delivery_attempts SET outcome='unknown' WHERE event_id=%s::uuid",(row['id'],))
        return query(sql,params)
    with monkeypatch.context() as patch:
        patch.setattr(scalar['db'],'query',late_unknown)
        retry=legacy.claim()
    assert retry['attempts']==2
    assert attempt(scalar,row)=={'outcome':'unknown','claim_attempt':1}
    request=AsyncMock()
    assert asyncio.run(legacy._send(retry,None,request,Mock()))==('unknown','legacy_outcome_unknown')
    request.assert_not_called()


def test_unknown_state_is_not_cleared_by_unsent_claim_evidence(scalar,monkeypatch):
    row=first_busy_claim(scalar,monkeypatch)
    scalar['db'].execute("UPDATE saas_crm_sync_outbox SET state='unknown' WHERE id=%s::uuid",(row['id'],))
    scalar['db'].commit()
    with scalar['db'].cursor() as cur:assert legacy_uncertain(cur,A,'contact',row['internal_id'])
    saved=contacts.update_contact(A,row['internal_id'],'Scoped','',actor_id=81)
    assert saved['sync']['error_code']=='scalar_legacy_outcome_unknown'


def test_gate_off_unmarked_claim_does_not_change_legacy_journal_behavior(scalar,monkeypatch):
    row=legacy_event(scalar,'pending',0);link_event(scalar,row)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','false')
    monkeypatch.setattr(legacy,'_connect',lambda:scalar['db'])
    monkeypatch.setattr(legacy,'_conflicts',AsyncMock(return_value=False))
    claimed=legacy.claim();request=AsyncMock(return_value={})
    assert asyncio.run(legacy._send(claimed,None,request,Mock()))==('synced','')
    assert not scalar['db'].execute('SELECT * FROM saas_crm_legacy_delivery_attempts WHERE event_id=%s::uuid',(row['id'],))


def test_known_unsent_claim_survives_gate_off_without_becoming_historical(scalar,monkeypatch):
    row=first_busy_claim(scalar,monkeypatch)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','false')
    expire_claim(scalar,row);retry=legacy.claim()
    assert attempt(scalar,row)=={'outcome':'not_started','claim_attempt':2}
    monkeypatch.setattr(legacy,'_conflicts',AsyncMock(return_value=False))
    request=AsyncMock(return_value={})
    assert asyncio.run(legacy._send(retry,None,request,Mock()))==('synced','')
    assert request.await_count==1


def test_claim_schema_upgrade_preserves_historical_evidence(scalar):
    from tenant_crm_scalar_schema import migrate_scalar_schema
    db=scalar['db']
    db.execute('''CREATE TEMP TABLE saas_crm_legacy_delivery_attempts (
      event_id UUID PRIMARY KEY,outcome TEXT NOT NULL CHECK(outcome IN ('sending','unknown','completed')),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())''')
    try:
        for outcome in ('sending','unknown','completed'):
            db.execute('INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome) VALUES(%s::uuid,%s)',(str(uuid.uuid4()),outcome))
        with db.cursor() as cur:migrate_scalar_schema(cur);migrate_scalar_schema(cur)
        rows=db.execute('SELECT outcome,claim_attempt FROM saas_crm_legacy_delivery_attempts ORDER BY outcome')
        assert rows==[{'outcome':value,'claim_attempt':None} for value in ('completed','sending','unknown')]
        db.execute("INSERT INTO saas_crm_legacy_delivery_attempts(event_id,outcome,claim_attempt) VALUES(%s::uuid,'not_started',1)",(str(uuid.uuid4()),))
    finally:
        db.execute('DROP TABLE pg_temp.saas_crm_legacy_delivery_attempts');db.commit()
