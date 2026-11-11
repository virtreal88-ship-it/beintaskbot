"""Atomic scoped scalar edits using isolated PostgreSQL/WASM and no network."""
import asyncio
import json
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database, db, A, B
from test_tenant_crm_relation_delivery_postgres import graph
from tenant_crm_scalar_delivery import dispatch
from tenant_crm_scalar_store import legacy_superseded
import tenant_crm_native_deals as deals
import tenant_crm_native_contacts as contacts


@pytest.fixture
def scalar(graph, monkeypatch):
    db=graph['db']
    monkeypatch.setattr(deals,'_connect',lambda:db)
    monkeypatch.setattr(contacts,'_connect',lambda:db)
    db.execute("UPDATE saas_crm_provider_settings SET allowed_fields='[\"title\",\"contact_id\"]'")
    db.execute('''INSERT INTO saas_crm_provider_settings(tenant_id,entity_type,provider,mode,primary_provider,
      sync_direction,allowed_fields) VALUES(%s::uuid,'contact','kommo','beintaskbot','beintaskbot',
      'to_external','["name","phone","email"]')''',(A,))
    db.commit()
    return graph


def edit(scalar,title='Changed'):
    return deals.update_deal(A,scalar['deal'].internal_id,title,'p','s',
                            contact_id=scalar['contacts'][0].internal_id,actor_id=81)


def event(scalar,ident):
    return scalar['db'].execute('SELECT * FROM saas_crm_scalar_outbox WHERE id=%s::uuid',(ident,))[0]


class Remote:
    def __init__(self,binding,name='Synthetic',id='10'):
        self.binding=binding; self.value={'id':int(id),'name':name,'_embedded':{'contacts':[{'id':1},{'id':2}]}}
        self.calls=[]; self.fail=False; self.crash=False
    def __call__(self,binding,method,path,**kwargs):
        assert binding==self.binding
        self.calls.append((method,path,kwargs))
        if method=='PATCH':
            payload=kwargs['json_body']
            assert '_embedded' not in payload and 'contact_id' not in payload and 'partner_id' not in payload
            self.value.update(payload)
            if self.crash: raise KeyboardInterrupt('synthetic crash')
            if self.fail: raise TimeoutError('synthetic timeout after apply')
        return dict(self.value)
    @property
    def writes(self): return [call for call in self.calls if call[0]=='PATCH']


def test_scalar_edit_queues_exact_values_actor_revision_and_no_relation(scalar):
    row=edit(scalar); queued=event(scalar,row['sync']['event_id'])
    assert queued['actor_id']==81 and queued['source_action']=='update'
    assert queued['field_mask']==['title'] and queued['values']=={'title':'Changed'}
    assert queued['before_values']=={'title':'Synthetic'}
    assert queued['binding']['account_id']=='synthetic.kommo.com'
    assert queued['external_id']=='10' and queued['state']=='pending'
    assert not scalar['db'].execute('SELECT id FROM saas_crm_sync_outbox')


def test_policy_additions_cannot_expand_scoped_event_payload(scalar):
    row=edit(scalar)
    scalar['db'].execute("UPDATE saas_crm_provider_settings SET allowed_fields='[\"title\",\"pipeline_id\",\"stage_id\",\"contact_id\"]' WHERE entity_type='deal'")
    scalar['db'].commit()
    remote=Remote(scalar['binding'])
    assert dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)=='synced'
    assert remote.writes==[('PATCH','leads/10',{'json_body':{'name':'Changed'}})]
    assert remote.value['_embedded']=={'contacts':[{'id':1},{'id':2}]}
    assert dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)=='synced'
    assert len(remote.writes)==1


@pytest.mark.parametrize('mutation',[
    "UPDATE saas_tenant_members SET active=false WHERE telegram_id=81",
    "UPDATE saas_tenants SET status='disabled'",
    "UPDATE saas_crm_native_deals SET access_responsible_id=11",
    "UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'",
    "UPDATE saas_tenant_integrations SET account_domain='other.kommo.com'",
    "UPDATE saas_crm_provider_settings SET allowed_fields='[\"contact_id\"]'",
    "UPDATE saas_crm_relation_identities SET external_id='999' WHERE entity_type='deal'",
    "UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{81,access_scopes,deals,update}','\"none\"') WHERE policy_key='members'",
])
def test_dispatch_reauthorizes_current_grants_policy_mapping_generation(scalar,mutation):
    row=edit(scalar); scalar['db'].execute(mutation); scalar['db'].commit()
    remote=Remote(scalar['binding'])
    assert dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)=='disabled'
    assert not remote.calls


def test_newer_actor_values_never_exported_by_old_event(scalar):
    first=edit(scalar,'First'); second=edit(scalar,'Second')
    remote=Remote(scalar['binding'])
    assert dispatch(lambda:scalar['db'],first['sync']['event_id'],remote)=='superseded'
    assert remote.calls==[]
    # The second event expects First on the provider, so a provider still on
    # Synthetic conflicts instead of pretending the unsent edit was applied.
    assert dispatch(lambda:scalar['db'],second['sync']['event_id'],remote)=='conflict'
    assert not remote.writes


def test_atomic_enqueue_failure_rolls_back_scalar_change(scalar,monkeypatch):
    from tenant_crm_scalar_store import queue_edit
    def fail(*args,**kwargs):
        queue_edit(*args,**kwargs)
        raise RuntimeError('synthetic crash before commit')
    monkeypatch.setattr(deals,'queue_edit',fail)
    with pytest.raises(RuntimeError):edit(scalar)
    assert scalar['db'].execute('SELECT title FROM saas_crm_native_deals WHERE id=%s::uuid',(scalar['deal'].internal_id,))[0]['title']=='Synthetic'
    assert not scalar['db'].execute('SELECT id FROM saas_crm_scalar_outbox')
    assert not scalar['db'].execute('SELECT * FROM saas_crm_scalar_heads')


def test_unbound_legacy_mapping_is_visible_needs_verification_not_claimed_synced(scalar):
    scalar['db'].execute("DELETE FROM saas_crm_relation_identities WHERE entity_type='deal'");scalar['db'].commit()
    row=edit(scalar)
    assert row['sync']['state']=='needs_verification' and row['sync']['error_code']=='scalar_mapping_unverified'
    remote=Remote(scalar['binding'])
    assert dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)=='needs_verification'
    assert remote.calls==[]


@pytest.mark.parametrize('crash',[False,True])
def test_uncertain_write_never_replayed_or_followed_by_newer_event(scalar,crash):
    row=edit(scalar);remote=Remote(scalar['binding']);remote.crash=crash;remote.fail=not crash
    if crash:
        with pytest.raises(KeyboardInterrupt):dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)
    else: assert dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)=='unknown'
    assert dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)=='unknown'
    assert len(remote.writes)==1
    later=edit(scalar,'Later')
    assert dispatch(lambda:scalar['db'],later['sync']['event_id'],remote)=='needs_verification'
    assert len(remote.writes)==1


def test_contact_scalar_edit_is_bound_and_preserves_phone_category(scalar):
    ident=scalar['contacts'][0].internal_id
    scalar['db'].execute("UPDATE saas_crm_native_contacts SET phone='111' WHERE id=%s::uuid",(ident,));scalar['db'].commit()
    row=contacts.update_contact(A,ident,'Synthetic','222',actor_id=81)
    remote=Remote(scalar['binding'],id='1')
    remote.value['custom_fields_values']=[{'field_code':'PHONE','values':[{'value':'111','enum_id':7}]}]
    assert dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)=='synced'
    assert remote.writes==[('PATCH','contacts/1',{'json_body':{'custom_fields_values':[{'field_code':'PHONE','values':[{'value':'222','enum_id':7}]}]}})]


def test_legacy_gate_off_behavior_and_unrelated_rows_preserved(scalar,monkeypatch):
    old={'tenant_id':A,'entity_type':'deal','internal_id':scalar['deal'].internal_id,
         'source_updated_at':scalar['db'].execute('SELECT updated_at FROM saas_crm_native_deals')[0]['updated_at']}
    edit(scalar)
    with scalar['db'].cursor() as cur: assert legacy_superseded(cur,old)
    with scalar['db'].cursor() as cur: assert not legacy_superseded(cur,{**old,'tenant_id':B})
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','false')
    with scalar['db'].cursor() as cur:
        assert legacy_superseded(cur,old)
        assert not legacy_superseded(cur,{**old,'tenant_id':B})
    row=edit(scalar,'Legacy')
    assert 'sync' not in row


def test_gate_disabled_never_broadens_queued_scoped_event(scalar,monkeypatch):
    row=edit(scalar);monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','false')
    remote=Remote(scalar['binding'])
    assert dispatch(lambda:scalar['db'],row['sync']['event_id'],remote)=='disabled'
    assert not remote.calls


def test_successive_different_fields_preserve_unsent_conflict_and_receipt(scalar):
    ident=scalar['contacts'][0].internal_id
    first=contacts.update_contact(A,ident,'First name','',actor_id=81)
    second=contacts.update_contact(A,ident,'First name','222',actor_id=81)
    first_event=event(scalar,first['sync']['event_id'])
    second_event=event(scalar,second['sync']['event_id'])
    assert first_event['state']=='superseded'
    assert first_event['error_code']=='scalar_superseded_before_send'
    assert first_event['attempt_outcome']=='not_started' and first_event['values']=={'name':'First name'}
    assert second['sync']['local_saved'] is True and second['sync']['review_required'] is True
    assert second['sync']['superseded_unsent_event_ids']==[str(first_event['id'])]
    assert second_event['field_mask']==['phone'] and second_event['values']=={'phone':'222'}
    remote=Remote(scalar['binding'],id='1')
    assert dispatch(lambda:scalar['db'],first['sync']['event_id'],remote)=='superseded'
    assert dispatch(lambda:scalar['db'],second['sync']['event_id'],remote)=='synced'
    assert remote.value['name']=='Synthetic'  # Never silently coalesce an earlier actor's unsent name.
    assert event(scalar,first['sync']['event_id'])['state']=='superseded'


def test_repeated_identical_save_keeps_pending_revision_and_single_event(scalar):
    first=edit(scalar)
    repeat=edit(scalar)
    assert repeat['sync']['event_id']==first['sync']['event_id']
    assert repeat['sync']['state']=='pending' and repeat['sync']['review_required'] is False
    assert len(scalar['db'].execute('SELECT id FROM saas_crm_scalar_outbox'))==1
    remote=Remote(scalar['binding'])
    assert dispatch(lambda:scalar['db'],first['sync']['event_id'],remote)=='synced'


def test_scoped_create_receipt_does_not_invent_update_authority_or_legacy_create(scalar):
    scalar['db'].execute("""UPDATE saas_tenant_workflow_policies SET value=jsonb_set(
      jsonb_set(value,'{81,access_scopes,customers,create}','\"self\"'),
      '{81,access_scopes,customers,update}','\"none\"') WHERE policy_key='members'""")
    scalar['db'].commit()
    row=contacts.create_contact(A,'New local',actor_id=81)
    assert row['sync']['local_saved'] is True and row['sync']['state']=='needs_verification'
    queued=event(scalar,row['sync']['event_id'])
    assert queued['source_action']=='create' and queued['actor_id']==81
    assert not scalar['db'].execute('SELECT id FROM saas_crm_sync_outbox')


@pytest.mark.parametrize('seconds', [-1, 0, 1])
def test_scoped_provenance_quarantines_old_equal_and_new_legacy_events(scalar, seconds):
    from datetime import datetime, timedelta
    saved = edit(scalar)
    queued = event(scalar, saved['sync']['event_id'])
    row = {'tenant_id': A, 'entity_type': 'deal',
           'internal_id': scalar['deal'].internal_id,
           'source_updated_at': datetime.fromisoformat(str(queued['local_revision']).replace('Z', '+00:00')) + timedelta(seconds=seconds)}
    with scalar['db'].cursor() as cur:
        assert legacy_superseded(cur, row)
        assert not legacy_superseded(cur, {**row, 'entity_type': 'contact'})
        assert not legacy_superseded(cur, {**row, 'internal_id': scalar['contacts'][1].internal_id})


def test_later_actorless_phone_edit_cannot_export_scoped_name(scalar, monkeypatch):
    import tenant_crm_sync_outbox as legacy
    ident = scalar['contacts'][0].internal_id
    saved = contacts.update_contact(A, ident, 'Scoped name', '', actor_id=81)
    scoped = event(scalar, saved['sync']['event_id'])
    # A service writer changes only phone, preserving the earlier scoped name.
    # Its actorless event is strictly newer than the scoped head.
    scalar['db'].execute("""UPDATE saas_crm_native_contacts SET phone='222',
      updated_at=%s::timestamptz + interval '1 second' WHERE tenant_id=%s::uuid AND id=%s::uuid""",
      (scoped['local_revision'], A, ident))
    scalar['db'].commit()
    monkeypatch.setattr(legacy, '_connect', lambda: scalar['db'])
    # PGlite cursor shim omits psycopg rowcount; this fixture inserts one new row.
    monkeypatch.setattr(type(scalar['db'].cursor()), 'rowcount', 1, raising=False)
    assert legacy.enqueue_change(A, 'contact', ident) == 1
    row = legacy.claim()
    assert legacy._time(row['source_updated_at']) > legacy._time(scoped['local_revision'])
    local = scalar['db'].execute('SELECT * FROM saas_crm_native_contacts WHERE id=%s::uuid', (ident,))[0]
    assert local['display_name'] == 'Scoped name' and local['phone'] == '222'
    request = Mock(side_effect=AssertionError('No provider request allowed'))
    credentials = Mock(side_effect=AssertionError('No credentials needed'))
    body = Mock(side_effect=AssertionError('No full-record serialization allowed'))
    monkeypatch.setattr(legacy, '_kommo_body', body)
    outcome = asyncio.run(legacy._send(row, None, request, credentials))
    assert outcome == ('conflict', 'legacy_scoped_revision_conflict')
    legacy.finish(row, *outcome)
    stored = scalar['db'].execute('SELECT state,error_code FROM saas_crm_sync_outbox WHERE id=%s::uuid', (row['id'],))[0]
    assert stored['state'] == 'conflict'
    assert stored['error_code'] == 'legacy_scoped_revision_conflict'
    request.assert_not_called(); credentials.assert_not_called(); body.assert_not_called()
    # Rollback cannot erase provenance and reopen the same full-record leak.
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    with scalar['db'].cursor() as cur:
        assert legacy_superseded(cur, row)
        assert not legacy_superseded(cur, {**row, 'internal_id': scalar['contacts'][1].internal_id})
    assert asyncio.run(legacy._send_unfenced(row, None, request, credentials)) == outcome
    request.assert_not_called(); body.assert_not_called()
    # An untouched card still enters the existing exporter after rollback.
    other = {**row, 'internal_id': scalar['contacts'][1].internal_id}
    with pytest.raises(AssertionError, match='No full-record serialization allowed'):
        asyncio.run(legacy._send_unfenced(other, None, request, credentials))
    body.assert_called_once()
    request.assert_not_called()
