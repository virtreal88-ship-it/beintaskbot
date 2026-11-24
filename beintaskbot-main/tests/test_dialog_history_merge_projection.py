"""Two-source chronological presentation without route, cursor or lifecycle rewrites."""
import uuid
from datetime import datetime
import pytest
from test_whatsapp_history_pairs import (A,B,PROFILE,EMPLOYEE,PN,LID,
    active,setup,staged_source,managed,gateway,pilot,database,db,split)
from tenant_linear_policy import TenantLinearError
from tenant_operator_dialogs import history, listing


@pytest.fixture
def merged(split, monkeypatch):
    database,channel=split
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_ENABLED','1')
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_SCOPE',f'{A}:{channel}')
    from tenant_chat_history_schema import ensure_schema
    ensure_schema(database)
    database.execute("DELETE FROM saas_dialog_workflows WHERE tenant_id=%s",(A,))
    database.execute("""CREATE TABLE IF NOT EXISTS saas_dialog_history_merge_jobs (
      id UUID PRIMARY KEY,tenant_id UUID NOT NULL,channel_id UUID NOT NULL,left_id TEXT NOT NULL,right_id TEXT NOT NULL,
      left_lead BIGINT NOT NULL,right_lead BIGINT NOT NULL,state TEXT NOT NULL,principal_id TEXT,
      proof_generation TEXT,proof_gateway_id UUID,proof_at TIMESTAMPTZ,display_name TEXT,display_phone TEXT,display_revision TEXT)""")
    database.execute("DELETE FROM saas_dialog_history_merge_jobs WHERE tenant_id=%s",(A,))
    database.execute("""INSERT INTO saas_dialog_history_merge_jobs
      SELECT gen_random_uuid(),r.tenant_id,r.channel_id,%s,%s,50,41,'completed',%s,r.generation,g.gateway_id,now(),NULL,NULL,NULL
      FROM saas_whatsapp_native_routes r JOIN saas_whatsapp_capture_generations g USING (tenant_id,channel_id,generation)
      WHERE r.tenant_id=%s AND r.channel_id=%s""",(LID,PN,PN,A,channel))
    database.execute("""UPDATE saas_crm_messages SET direction='incoming',
         happened_at='2026-11-12 09:00+00'::timestamptz
       WHERE tenant_id=%s AND kommo_lead_id=50 AND external_id IN ('synthetic-1','synthetic-50')""",(A,))
    database.commit()
    return database,channel


def test_overlay_is_single_owner_card_and_full_stable_history(merged):
    database,channel=merged
    tables=('saas_crm_messages','saas_connect_conversations','saas_dialog_workflows','saas_dialog_episodes','saas_operator_dialog_reads')
    # Existing (non-merged) list initializes exact route cursors. The overlay
    # must not change or coalesce those physical cursors.
    assert {r['lead_id'] for r in listing(PROFILE,lifecycle='all')['dialogs']}=={41,50}
    assert {r['lead_id'] for r in listing(EMPLOYEE,lifecycle='all',combine_history=True)['dialogs']}=={41,50}
    before={t:database.execute('SELECT * FROM '+t+' ORDER BY 1,2') for t in tables}
    assert [r['lead_id'] for r in listing(PROFILE,lifecycle='all',combine_history=True,owner_overview=True)['dialogs']]==[41]
    exact=history(PROFILE,'connect',41,channel,PN)
    first=history(PROFILE,'connect',41,channel,PN,combine_history=True)
    older=history(PROFILE,'connect',41,channel,PN,before=first['next_before'],combine_history=True)
    saved_alt=history(PROFILE,'connect',50,channel,LID,combine_history=True,redirect_paired_history=True)
    assert 'history_merge_id' in first['dialog'] and 'history_merge_id' not in saved_alt['dialog']
    assert first['dialog']['can_send']==exact['dialog']['can_send']
    assert first['read_through_message_id']==exact['read_through_message_id']
    assert older['next_before'] is None
    all_messages=older['messages']+first['messages']
    assert len(all_messages)==len({m['id'] for m in all_messages})==53
    assert len([m for m in all_messages if m['direction']=='incoming' and m['source_lead_id']==50])==2
    assert {(m['source_conversation_id'],m['source_lead_id'],m['source_channel_id']) for m in all_messages}=={(PN,41,channel),(LID,50,channel)}
    times=[datetime.fromisoformat(m['happened_at'] or m['created_at']) for m in all_messages]
    assert times==sorted(times)
    assert {t:database.execute('SELECT * FROM '+t+' ORDER BY 1,2') for t in tables}==before


@pytest.mark.parametrize('boundary',['worker','generation','conflict','episode','pending','disabled','transport','legacy_conflict','scope_removed','different_source','different_tenant'])
def test_no_companion_on_revoked_or_protected_route(merged,monkeypatch,boundary):
    database,channel=merged
    profile=PROFILE
    if boundary=='worker':profile=EMPLOYEE
    if boundary=='generation':database.execute("UPDATE saas_dialog_history_merge_jobs SET proof_generation='older'")
    if boundary=='pending':database.execute("UPDATE saas_dialog_history_merge_jobs SET state='working'")
    if boundary=='disabled':monkeypatch.delenv('DIALOG_HISTORY_MERGE_ENABLED')
    if boundary=='transport':monkeypatch.delenv('BEINTASKBOT_UNIFIED_WHATSAPP_SEND')
    if boundary=='scope_removed':monkeypatch.delenv('DIALOG_HISTORY_MERGE_SCOPE')
    if boundary=='different_source':monkeypatch.setenv('DIALOG_HISTORY_MERGE_SCOPE',f'{A}:{uuid.uuid4()}')
    if boundary=='different_tenant':monkeypatch.setenv('DIALOG_HISTORY_MERGE_SCOPE',f'{B}:{channel}')
    if boundary in ('conflict','legacy_conflict'):
        database.execute("""INSERT INTO saas_connect_identity_conflict_events
          (tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES(%s,%s,%s,'split','{}')""",
          (A,channel,LID if boundary=='conflict' else PN.replace('@s.whatsapp.net','@c.us')))
    if boundary=='episode':
        database.execute("""INSERT INTO saas_dialog_episodes
          (tenant_id,id,channel_id,conversation_id,lead_id,accepted_by,operator_id,closed_at)
          VALUES(%s,%s,%s,%s,50,33,33,now())""",(A,str(uuid.uuid4()),channel,LID))
    database.commit()
    rows=listing(profile,lifecycle='all',combine_history=True)['dialogs']
    assert {r['lead_id'] for r in rows}=={41,50}
    page=history(profile,'connect',41,channel,PN,combine_history=True)
    assert 'history_merge_id' not in page['dialog']
    assert {m['source_lead_id'] for m in page['messages']}=={41}
    with pytest.raises(TenantLinearError):
        history({'tenant_id':B,'telegram_id':22},'connect',41,channel,PN,combine_history=True)


def test_completed_status_rechecks_exact_current_owner_and_generation(merged):
    from tenant_dialog_history_merge_projection import available_job
    from tenant_operator_access import access
    database,_=merged
    with database.cursor() as cur:
        tenant,_,policy=access(cur,PROFILE,'dialogs')
        cur.execute("SELECT * FROM saas_dialog_history_merge_jobs WHERE tenant_id=%s",(tenant,))
        job=cur.fetchone()
        assert available_job(cur,tenant,policy,job)
        _,_,other_policy=access(cur,EMPLOYEE,'dialogs')
        assert not available_job(cur,tenant,other_policy,job)
        assert not available_job(cur,B,policy,job)
        cur.execute("UPDATE saas_dialog_history_merge_jobs SET proof_generation='older' WHERE id=%s",(job['id'],))
        assert not available_job(cur,tenant,policy,job)
        database.commit()


def _accept(database,channel,conversation,actor):
    database.execute("""INSERT INTO saas_dialog_workflows
        (tenant_id,channel_id,conversation_id,id,state,operator_id)
        VALUES(%s,%s,%s,%s,'accepted',%s)""",(A,channel,conversation,str(uuid.uuid4()),actor))


def test_proved_same_operator_can_open_both_exact_routes_but_actions_stay_selected(merged,monkeypatch):
    database,channel=merged
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    _accept(database,channel,PN,33)
    _accept(database,channel,LID,33)
    database.commit()
    routes=[{'provider':'connect','channel_id':channel,'conversation_id':PN,'lead_id':41},
            {'provider':'connect','channel_id':channel,'conversation_id':LID,'lead_id':50}]
    for chosen in routes:
        exact=history(EMPLOYEE,'connect',chosen['lead_id'],channel,chosen['conversation_id'])
        first=history(EMPLOYEE,'connect',chosen['lead_id'],channel,chosen['conversation_id'],combine_history=True)
        older=history(EMPLOYEE,'connect',chosen['lead_id'],channel,chosen['conversation_id'],before=first['next_before'],combine_history=True)
        assert first['history_scope']['canonical_route']==chosen and first['history_scope']['requested_route']==chosen
        assert first['dialog']['can_send']==exact['dialog']['can_send']
        assert first['dialog']['lifecycle']==exact['dialog']['lifecycle']
        assert first['dialog']['history_merge_action_routes']==routes
        assert first['dialog']['history_merge_id']==older['dialog']['history_merge_id']
        assert 'combined_dialog' not in first
        assert len({m['id'] for m in first['messages']+older['messages']})==53


def test_transfer_removes_companion_from_operator_and_owner_overview_redacts_each_passive_route(merged,monkeypatch):
    database,channel=merged
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    _accept(database,channel,PN,33)
    _accept(database,channel,LID,33)
    database.execute("""UPDATE saas_crm_messages SET direction='system' WHERE tenant_id=%s AND external_id='synthetic-1'""",(A,))
    database.commit()
    before=history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert 'history_merge_id' in before['dialog'] and 'history_merge_action_routes' in before['dialog']
    assert any(m['external_id']=='synthetic-1' for m in before['messages'])
    database.execute("UPDATE saas_dialog_workflows SET operator_id=11,revision=revision+1 WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(A,channel,LID))
    database.commit()
    employee=history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert not {'history_merge_id','history_merge_action_routes'} & employee['dialog'].keys()
    assert {m['source_lead_id'] for m in employee['messages']}=={41}
    with pytest.raises(TenantLinearError):
        history(EMPLOYEE,'connect',50,channel,LID,combine_history=True)
    with pytest.raises(TenantLinearError):
        history(PROFILE,'connect',41,channel,PN,combine_history=True)
    owner=history(PROFILE,'connect',41,channel,PN,combine_history=True,owner_overview=True)
    assert owner['dialog']['history_merge_id']!=before['dialog']['history_merge_id']
    assert owner['dialog']['owner_overview_read_only'] and not owner['dialog']['can_send']
    assert 'history_merge_action_routes' not in owner['dialog']
    assert {m['source_lead_id'] for m in owner['messages']}=={41,50}


def test_active_and_archived_routes_keep_original_filters_counts_and_archive_actions(merged,monkeypatch):
    database,channel=merged
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    _accept(database,channel,PN,33)
    database.execute("""INSERT INTO saas_dialog_workflows
        (tenant_id,channel_id,conversation_id,id,state,operator_id,closed_at)
        VALUES(%s,%s,%s,%s,'closed',11,now())""",(A,channel,LID,str(uuid.uuid4())))
    database.execute("""UPDATE saas_crm_messages SET direction='system' WHERE tenant_id=%s AND external_id='synthetic-1'""",(A,))
    database.commit()
    for mode,expected in (('active',{41}),('completed',{50}),('all',{41,50})):
        raw=listing(EMPLOYEE,lifecycle=mode)
        combined=listing(EMPLOYEE,lifecycle=mode,combine_history=True)
        assert {r['lead_id'] for r in combined['dialogs']}==expected
        assert tuple(raw[k] for k in ('total_count','unread_count','attention_count'))==tuple(
            combined[k] for k in ('total_count','unread_count','attention_count'))
        mixed=listing(EMPLOYEE,lifecycle=mode,combine_history=True,allow_mixed_history_card=True)
        assert {r['lead_id'] for r in mixed['dialogs']}==({41} if mode=='all' else expected)
        assert mixed['total_count']==(1 if mode=='all' else len(expected))
        assert (mixed['unread_count'],mixed['attention_count'])==(raw['unread_count'],raw['attention_count'])
        if mode=='all':
            assert [(r['lead_id'],r['state'],r['read_only']) for r in mixed['dialogs'][0]['history_merge_read_routes']]==[
                (41,'accepted',False),(50,'closed',True)]
    closed=history(EMPLOYEE,'connect',50,channel,LID,combine_history=True)
    assert closed['dialog']['archive_read_only'] and not closed['dialog']['can_send']
    assert closed['read_through_message_id'] is None
    assert 'history_merge_action_routes' not in closed['dialog']
    opened=history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert opened['dialog']['history_merge_certificate']==closed['dialog']['history_merge_certificate']
    assert opened['dialog']['history_merge_id']!=closed['dialog']['history_merge_id']
    assert [(r['lead_id'],r['state'],r['read_only']) for r in closed['dialog']['history_merge_read_routes']]==[
        (41,'accepted',False),(50,'closed',True)]
    pages=closed['messages']+history(EMPLOYEE,'connect',50,channel,LID,before=closed['next_before'],combine_history=True)['messages']
    assert {m['source_lead_id'] for m in pages}=={41,50}
    assert not any(m['external_id']=='synthetic-1' for m in pages)


def test_different_assignees_each_keep_one_original_card_owner_overview_folds_passively(merged,monkeypatch):
    database,channel=merged
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    database.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
         VALUES(%s,44,'operator','Second synthetic employee','["dialogs"]'::jsonb)""",(A,))
    _accept(database,channel,PN,33)
    _accept(database,channel,LID,44)
    database.execute("""UPDATE saas_crm_messages SET direction='system' WHERE tenant_id=%s AND external_id='synthetic-1'""",(A,))
    database.commit()
    for actor,lead,route in ((33,41,PN),(44,50,LID)):
        who={'tenant_id':A,'telegram_id':actor}
        active=listing(who,lifecycle='active',combine_history=True)
        assert [d['lead_id'] for d in active['dialogs']]==[lead]
        page=history(who,'connect',lead,channel,route,combine_history=True)
        assert 'history_merge_id' not in page['dialog']
        assert {m['source_lead_id'] for m in page['messages']}=={lead}
    passive=listing(PROFILE,lifecycle='all',owner_overview=True,combine_history=True)
    assert [d['lead_id'] for d in passive['dialogs']]==[41]
    assert passive['dialogs'][0]['owner_overview_read_only']
    page=history(PROFILE,'connect',41,channel,PN,combine_history=True,owner_overview=True)
    assert not page['dialog']['can_send'] and page['read_through_message_id'] is None
    assert 'history_merge_action_routes' not in page['dialog']
    assert {m['source_lead_id'] for m in page['messages']}=={41,50}
    assert not any(m['direction']=='system' for m in page['messages'])


def test_selected_display_metadata_is_owner_only_without_changing_source_state(merged,monkeypatch):
    database,channel=merged
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    from tenant_dialog_merge_display import exact_options
    database.execute("""UPDATE saas_connect_conversations SET manual_display_name='Chosen for display'
       WHERE tenant_id=%s AND connect_channel_id=%s AND lead_id=41""",(A,channel))
    with database.cursor() as cur:
        options=exact_options(cur,A,channel,((LID,50),(PN,41)))
    expected_phone='+'+PN.split('@')[0]
    database.execute("""UPDATE saas_dialog_history_merge_jobs
        SET display_name='Chosen for display',display_phone=%s,display_revision=%s WHERE tenant_id=%s AND channel_id=%s""",
        (expected_phone,options['display_revision'],A,channel))
    database.commit()
    rows=listing(PROFILE,lifecycle='all',owner_overview=True,combine_history=True)['dialogs']
    assert rows[0]['history_merge_display_name']=='Chosen for display'
    assert rows[0]['history_merge_display_phone']==expected_phone
    plain=history(PROFILE,'connect',41,channel,PN)
    chosen=history(PROFILE,'connect',41,channel,PN,combine_history=True)
    assert chosen['dialog']['history_merge_display_name']=='Chosen for display'
    assert chosen['dialog']['history_merge_display_phone']==expected_phone
    for k in ('phone','conversation_id','lead_id','channel_id','can_send','lifecycle'):
        assert chosen['dialog'][k]==plain['dialog'][k]
    employee=history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert 'history_merge_id' in employee['dialog']
    assert not {'history_merge_display_phone','history_merge_display_name'} & employee['dialog'].keys()
    database.execute("UPDATE saas_connect_conversations SET manual_display_name='' WHERE tenant_id=%s AND lead_id=41",(A,))
    database.commit()
    after_edit=history(PROFILE,'connect',41,channel,PN,combine_history=True)
    after_list=listing(PROFILE,lifecycle='all',owner_overview=True,combine_history=True)['dialogs']
    for projection in (after_edit['dialog'],after_list[0]):
        assert projection['history_merge_id']
        assert not {'history_merge_display_phone','history_merge_display_name'} & projection.keys()
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_SCOPE',f'{A}:{uuid.uuid4()}')
    former=history(PROFILE,'connect',41,channel,PN,combine_history=True)
    assert not {'history_merge_id','history_merge_display_phone','history_merge_display_name'} & former['dialog'].keys()


def test_reaction_capability_only_for_selected_actionable_history_route(merged,monkeypatch):
    database,channel=merged
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    _accept(database,channel,PN,33)
    _accept(database,channel,LID,33)
    database.commit()
    attached=[]

    def attach(cur,tenant,messages):
        assert str(tenant)==A
        for message in messages:
            attached.append((message['source_channel_id'],message['source_conversation_id'],message['source_lead_id']))
            message['wa_reaction_available']=True

    monkeypatch.setattr('tenant_whatsapp_native_reactions.attach',attach)
    for lead,conversation in [(41,PN),(50,LID)]:
        current=history(EMPLOYEE,'connect',lead,channel,conversation,combine_history=True)
        assert current['dialog']['can_send'] is True
        assert current['dialog']['history_merge_id']
        assert {m['source_lead_id'] for m in current['messages']}=={41,50}
        assert {m['source_lead_id'] for m in current['messages'] if m.get('wa_reaction_available')}=={lead}
        assert set(attached)=={(channel,conversation,lead)}
        attached.clear()
    passive=history(PROFILE,'connect',41,channel,PN,combine_history=True,owner_overview=True)
    assert passive['dialog']['history_merge_id'] and passive['dialog']['owner_overview_read_only']
    assert {m['source_lead_id'] for m in passive['messages']}=={41,50}
    assert not attached and not any(m.get('wa_reaction_available') for m in passive['messages'])
