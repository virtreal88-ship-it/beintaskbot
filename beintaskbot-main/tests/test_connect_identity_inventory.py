"""Historical screenshot-shaped fixtures; all customer values are synthetic."""
import json
import uuid
import pytest
from test_tenant_support_inbox import A,B,CA,CB,PROFILE,database,db
import tenant_ai_dialogue_merge as mcp_merge
import tenant_connect_direct_reconcile as reconcile
import tenant_connect_transport as transport
from tenant_connect_identity import inventory
from tenant_linear_policy import TenantLinearError

JID='994700000001@s.whatsapp.net';LID='80000000000001@lid'

def routes(db, *, channel=CA, tenant=A, names=('Anar','Anar'), linked=True):
    for index,identity in enumerate((JID,LID)):
        lead=(6101 if tenant==A else 7101)+(100 if channel==CB else 0)+index
        db.execute('''INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,
          lead_id,contact_name,phone,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,'contact')''',
          (tenant,channel,identity,lead,names[index],'994700000001' if index==0 else ''))
        db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,
          message_type,media_url,happened_at,author_name,raw) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,
          'image','private-media','2026-10-23T14:09:43Z','Synthetic employee','{}'::jsonb)''',
          (str(uuid.uuid4()),tenant,lead,'connect:'+str(lead),'connect:'+channel,'incoming' if index==0 else 'outgoing'))
    if linked:
        db.execute('''INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id)
          VALUES(%s::uuid,%s::uuid,%s,%s)''',(tenant,channel,LID,JID))
    db.commit()

def config(tenant=A,actor=11,channel=CA):
    return {'AI_LEARNING_MCP_TENANT_ID':tenant,'AI_LEARNING_MCP_CHANNEL_ID':channel,
            'AI_LEARNING_MCP_CURSOR_SECRET':'synthetic-secret','actor_id':actor}

def test_screenshot_shape_reports_unproven_contact_without_guessing(db):
    mcp_merge.ensure_schema(db);routes(db,linked=False)
    with db.cursor() as cur:
        result=mcp_merge.preview(cur,config(),{'search':'Anar'})
    assert {r['lead_id'] for r in result['routes']}=={6101,6102}
    assert result['scanned_routes']==3 and result['scan_complete']
    assert len(result['items'])==1
    assert result['items'][0]['conflicts']==['provider_identity_unproven']
    assert result['items'][0]['eligible'] is False
    text=json.dumps(result)
    assert JID not in text and LID not in text and 'Anar' not in text and '994700000001' not in text

def test_persisted_alias_without_event_pair_is_discovered_and_survives_restart(db):
    mcp_merge.ensure_schema(db);routes(db)
    with db.cursor() as cur:
        first=mcp_merge.preview(cur,config(),{})
        assert first['items'][0]['eligible']
        assert 'persisted_provider_alias' in first['items'][0]['evidence_sources']
        assert transport._canonical_direct_conversation(cur,A,CA,LID,{}, {'kind':'contact'})==(JID,False)
    db.commit()
    with db.cursor() as cur:
        assert mcp_merge.preview(cur,config(),{})==first

def test_same_customer_on_other_channel_or_tenant_is_never_a_pair(db):
    mcp_merge.ensure_schema(db)
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id) VALUES(%s::uuid,%s::uuid)",(A,CB));db.commit()
    routes(db);routes(db,channel=CB,linked=False);routes(db,tenant=B,channel=CB,linked=False)
    with db.cursor() as cur:
        a=mcp_merge.preview(cur,config(),{})
        other=mcp_merge.preview(cur,config(channel=CB),{})
        foreign=mcp_merge.preview(cur,config(B,22,CB),{})
        assert a['items'][0]['eligible']
        assert not other['items'][0]['eligible'] and not foreign['items'][0]['eligible']
        assert a['items'][0]['pair_id']!=other['items'][0]['pair_id']!=foreign['items'][0]['pair_id']

def test_groups_and_ambiguous_aliases_are_excluded_or_blocked(db):
    mcp_merge.ensure_schema(db);routes(db)
    db.execute("UPDATE saas_connect_conversations SET conversation_kind='group' WHERE tenant_id=%s::uuid AND conversation_id=%s",(A,LID));db.commit()
    with db.cursor() as cur:
        item=mcp_merge.preview(cur,config(),{})['items'][0]
        assert not item['eligible'] and 'route_missing' in item['conflicts']
    db.execute("UPDATE saas_connect_conversations SET conversation_kind='contact' WHERE tenant_id=%s::uuid AND conversation_id=%s",(A,LID))
    other='994700000003@s.whatsapp.net'
    db.execute('''UPDATE saas_crm_messages SET raw=%s::jsonb WHERE tenant_id=%s::uuid AND kommo_lead_id=6102''',
               (json.dumps({'conversationId':LID,'directIdentity':{'jid':other,'lid':LID}}),A));db.commit()
    with db.cursor() as cur:
        items=mcp_merge.preview(cur,config(),{})['items']
    assert all(not item['eligible'] and 'provider_identity_ambiguous' in item['conflicts'] for item in items)

@pytest.mark.parametrize('actor',[22,33])
def test_inventory_requires_active_owner(db,actor):
    mcp_merge.ensure_schema(db)
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s::uuid,33,'admin')",(A,));db.commit()
    with db.cursor() as cur,pytest.raises(TenantLinearError,match='owner'):
        mcp_merge.preview(cur,config(actor=actor),{})

def test_scan_does_not_miss_evidence_after_first_thousand_messages(db):
    mcp_merge.ensure_schema(db);routes(db,linked=False)
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,raw)
      SELECT ('00000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,%s::uuid,6101,
        'connect:older:'||n,%s,'{"directIdentity":{}}'::jsonb FROM generate_series(1,1001) n''',(A,'connect:'+CA))
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,raw)
      VALUES('ffffffff-ffff-4fff-8fff-ffffffffffff',%s::uuid,6102,'connect:last',%s,%s::jsonb)''',
      (A,'connect:'+CA,json.dumps({'conversationId':LID,'directIdentity':{'jid':JID,'lid':LID}})));db.commit()
    with db.cursor() as cur:
        assert mcp_merge.preview(cur,config(),{})['items'][0]['eligible']

def test_existing_reconcile_accepts_alias_evidence_and_preserves_media(db,monkeypatch):
    monkeypatch.setattr(reconcile,'_connect',lambda:db);reconcile.ensure_schema(db);routes(db)
    # Listing initializes absent cursors. Seed its cursor before the backup so
    # an exact rollback is not asked to erase a later employee-visible change.
    db.execute("""INSERT INTO saas_operator_dialog_reads(tenant_id,telegram_id,provider,channel_id,
      conversation_id,lead_id,last_read_at) VALUES(%s::uuid,11,'connect',%s,%s,6101,now())""",(A,CA,JID));db.commit()
    before=db.execute('SELECT id,external_id,media_url,happened_at,author_name FROM saas_crm_messages ORDER BY id');db.commit()
    candidate=reconcile.preview(PROFILE,CA)['items'][0]
    result=reconcile.apply(PROFILE,{'operation':'merge','pair_id':candidate['pair_id'],
      'precondition':candidate['precondition'],'request_id':str(uuid.uuid4()),'confirm':True})
    assert result['moved_messages']==1
    assert db.execute('SELECT id,external_id,media_url,happened_at,author_name FROM saas_crm_messages ORDER BY id')==before
    import tenant_operator_dialogs as dialogs
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    listed=dialogs.listing(PROFILE)['dialogs']
    assert len([r for r in listed if r['conversation_id'] in {JID,LID}])==1
    item=reconcile.preview(PROFILE,CA)['items'][0]
    reconcile.apply(PROFILE,{'operation':'rollback','pair_id':candidate['pair_id'],
      'precondition':item['precondition'],'request_id':str(uuid.uuid4()),'confirm':True})
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages WHERE kommo_lead_id=6102')[0]['n']==1
    assert len([r for r in dialogs.listing(PROFILE)['dialogs'] if r['conversation_id'] in {JID,LID}])==2


def test_case_evidence_and_assignments_quarantine_physical_reconciliation(db,monkeypatch):
    monkeypatch.setattr(reconcile,'_connect',lambda:db);reconcile.ensure_schema(db);routes(db)
    # Unknown participant-level data must not be removed by FK cascade.
    db.execute('''INSERT INTO saas_connect_participant_display_names(tenant_id,connect_channel_id,
      conversation_id,participant_jid,display_name,updated_by)
      VALUES(%s::uuid,%s::uuid,%s,'synthetic@lid','Synthetic',11)''',(A,CA,LID));db.commit()
    candidate=reconcile.preview(PROFILE,CA)['items'][0]
    assert not candidate['eligible'] and 'unsupported_route_dependency:saas_connect_participant_display_names' in candidate['conflicts']
    with pytest.raises(TenantLinearError):
        reconcile.apply(PROFILE,{'operation':'merge','pair_id':candidate['pair_id'],
          'precondition':candidate['precondition'],'request_id':str(uuid.uuid4()),'confirm':True})

def test_unrelated_current_identity_cannot_join_provider_pair():
    class NoDatabase:
        def execute(self,*args):raise AssertionError('must reject before DB access')
    assert transport._canonical_direct_conversation(NoDatabase(),A,CA,'994700000009@s.whatsapp.net',
      {'directIdentity':{'jid':JID,'lid':LID}},{'kind':'contact'})[1] is True


def test_linked_device_echo_and_legacy_rows_are_diagnostic_only(db):
    mcp_merge.ensure_schema(db);routes(db,linked=False)
    db.execute('''UPDATE saas_crm_messages SET raw='{"externalMessageId":"synthetic-echo"}'::jsonb
      WHERE tenant_id=%s::uuid AND kommo_lead_id IN (6101,6102)''',(A,))
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel)
      VALUES(%s::uuid,%s::uuid,6101,'kommo:legacy','kommo'),
            (%s::uuid,%s::uuid,6199,'connect:orphan',%s)''',(str(uuid.uuid4()),A,str(uuid.uuid4()),A,'connect:'+CA));db.commit()
    with db.cursor() as cur:
        result=mcp_merge.preview(cur,config(),{})
    assert 'linked_device_echo_hint' in result['items'][0]['evidence_sources']
    assert result['items'][0]['eligible'] is False and result['unmapped_messages']==1
    assert next(r for r in result['routes'] if r['lead_id']==6101)['other_channel_messages']==1
