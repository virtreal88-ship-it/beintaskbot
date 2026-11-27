"""Actual isolated PostgreSQL/WASM SQL tests; no external services or production.
PGlite is single-session: these verify constraints/CAS/idempotency, not parallel
transaction timing. Native PostgreSQL concurrent claim tests remain required.
"""
import uuid
from types import SimpleNamespace
import pytest
pytest_plugins = ('test_tenant_support_inbox',)
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db
from tenant_linear_policy import TenantLinearError
import tenant_dialog_lifecycle as lifecycle


@pytest.fixture
def episodes(db, monkeypatch, request):
    import tenant_platform
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', A)
    monkeypatch.setenv('TENANT_DIALOG_PRESENCE_ROUTING_ENABLED', 'true')
    monkeypatch.setattr(tenant_platform, '_connect', lambda: db)
    from tenant_chat_send_schema import ensure_schema as send_schema
    send_schema(db)
    with db.cursor() as cur:
        lifecycle.migrate(cur)
        from tenant_dialog_rollout import transition
        mode=getattr(request,'param','active')
        transition(cur,A,mode=mode,admission_enabled=mode=='active',expected_generation=0)
        # Receipts intentionally survive tenant business-row cleanup; isolate
        # this disposable fixture without adding destructive runtime cleanup.
        cur.execute('TRUNCATE saas_dialog_lifecycle_receipts')
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions) VALUES(%s::uuid,33,'operator','Employee','[\"dialogs\",\"support\"]'::jsonb)", (A,))
    db.commit()
    from tenant_dialog_presence import set_status
    set_status({'tenant_id':A,'telegram_id':11},{'status':'online'})
    set_status({'tenant_id':A,'telegram_id':33},{'status':'online'})
    return db


def command(action='accept', revision=0, actor=11, **extra):
    return lifecycle.mutate({'tenant_id': A, 'telegram_id': actor}, payload(action, revision, **extra))


def payload(action='accept', revision=0, **extra):
    return dict(provider='connect', channel_id=CA, conversation_id='client', lead_id=41,
                action=action, expected_revision=revision, request_id=str(uuid.uuid4()), **extra)


def seed_legacy_dismissed(db, incoming):
    """Fixture representing old persisted terminal state, not a new action."""
    ident=str(uuid.uuid4())
    db.execute("""INSERT INTO saas_dialog_workflows
      (tenant_id,channel_id,conversation_id,id,state,operator_id,revision,closed_at,last_inbound_id)
      VALUES(%s::uuid,%s::uuid,'client',%s::uuid,'dismissed',11,1,clock_timestamp(),%s)""",(A,CA,ident,str(incoming)))
    db.commit()
    return {'id':ident,'state':'dismissed','operator_id':11,'revision':1}


def state(db, channel=CA):
    result=db.execute('SELECT * FROM saas_dialog_workflows WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s', (A,channel,'client'))
    db.commit()
    return result[0] if result else None


def test_migration_is_additive_and_repeated_without_backfill(episodes):
    before=episodes.execute('SELECT id,tenant_id,body FROM saas_crm_messages ORDER BY id')
    episodes.commit()
    with episodes.cursor() as cur:
        lifecycle.migrate(cur)
        lifecycle.migrate(cur)
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_workflows')[0]['n']==0
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==0
    assert episodes.execute('SELECT id,tenant_id,body FROM saas_crm_messages ORDER BY id')==before


def test_claim_cas_receipts_and_no_automatic_case(episodes):
    request=payload()
    first=lifecycle.mutate(PROFILE,request)
    assert first['lifecycle']['revision']==1
    assert lifecycle.mutate(PROFILE,request)==first
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==1
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0
    episodes.commit()
    with pytest.raises(TenantLinearError) as lost:
        command(actor=33)
    assert lost.value.status==409
    assert state(episodes)['operator_id']==11
    with pytest.raises(TenantLinearError):
        lifecycle.mutate(PROFILE,{**request,'action':'close'})
    assert state(episodes)['state']=='accepted'
    close=payload('close',1)
    closed=lifecycle.mutate(PROFILE,close)
    assert lifecycle.mutate(PROFILE,close)==closed
    assert state(episodes)['state']=='closed'
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0


def test_owner_can_start_new_cycle_from_closed_dialog_without_sending(episodes):
    command()
    command('close',1)
    before_messages=episodes.execute('SELECT id FROM saas_crm_messages ORDER BY id')
    old_episode=episodes.execute('SELECT id,closed_at FROM saas_dialog_episodes ORDER BY accepted_at')[0]
    start=payload('start',2)
    with pytest.raises(TenantLinearError) as other_owner:
        lifecycle.mutate({'tenant_id':A,'telegram_id':33},start)
    assert other_owner.value.status==403  # No connected source in this fixture.
    first=lifecycle.mutate(PROFILE,start)
    assert first['lifecycle']['state']=='accepted' and first['lifecycle']['revision']==3
    assert lifecycle.mutate(PROFILE,start)==first
    assert episodes.execute('SELECT id FROM saas_crm_messages ORDER BY id')==before_messages
    episodes.commit()
    workflows=episodes.execute('SELECT state,operator_id,revision FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,))
    assert workflows==[{'state':'accepted','operator_id':11,'revision':3}]
    history=episodes.execute('SELECT id,closed_at FROM saas_dialog_episodes ORDER BY accepted_at')
    assert len(history)==2 and history[0]['id']==old_episode['id'] and history[0]['closed_at']
    assert history[1]['closed_at'] is None


def test_manual_restart_remains_for_own_closed_history_after_operator_routing(episodes, monkeypatch):
    from tenant_dialog_presence import (provision_status_schema, ROUTING_SOURCES_ENV,
        STATUS_ENABLED_ENV, set_status)
    from tenant_operator_access import access
    import tenant_operator_dialogs
    monkeypatch.setattr(tenant_operator_dialogs, '_connect', lambda: episodes)
    from tenant_operator_dialogs import history, listing

    command()
    command('close', 1)
    # The production provisioner safely composes the constraint identifier;
    # PGlite's transport accepts only already-rendered SQL.
    query=episodes.query
    monkeypatch.setattr(episodes,'query',
        lambda sql,params=():query(sql.as_string() if hasattr(sql,'as_string') else sql,params))
    with episodes.cursor() as cur:
        provision_status_schema(cur)
    episodes.commit()
    monkeypatch.setenv(STATUS_ENABLED_ENV, 'true')
    monkeypatch.setenv(ROUTING_SOURCES_ENV, CA)
    set_status(PROFILE, {'status': 'away'})

    # The owner's work status is informational. The ordinary history and
    # action capabilities remain available even while away.
    page=history(PROFILE,'connect',41,CA,'client')
    assert page['dialog']['lifecycle']['can_start']
    rows=listing(PROFILE,lifecycle='completed')['dialogs']
    assert [r['lifecycle']['can_start'] for r in rows if r['lead_id']==41]==[True]
    with episodes.cursor() as cur:
        from tenant_dialog_presence import is_online
        assert not is_online(cur,A,11,source_channel=CA)
        _,_,policy=access(cur,PROFILE)
        row={'provider':'connect','lead_id':41,'conversation_id':'client','channel_id':CA}
        lifecycle.decorate(cur,A,11,row,details=False,_policy=policy,_can_update=False)
        assert not row['lifecycle']['can_start']
    episodes.commit()
    with pytest.raises(TenantLinearError) as other:
        command('start',2,actor=33)
    assert other.value.status==403  # No connected source, unlike the prior owner's preserved behavior.

    episodes.execute("UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,CA))
    episodes.commit()
    assert not history(PROFILE,'connect',41,CA,'client')['dialog']['lifecycle']['can_start']
    with pytest.raises(TenantLinearError) as inactive:
        command('start',2)
    assert inactive.value.status==403
    episodes.execute("UPDATE saas_connect_channels SET active=TRUE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,CA))
    episodes.commit()

    from tenant_dialog_rollout import transition
    with episodes.cursor() as cur:
        transition(cur,A,mode='active',admission_enabled=False,expected_generation=1)
    episodes.commit()
    assert not history(PROFILE,'connect',41,CA,'client')['dialog']['lifecycle']['can_start']
    with pytest.raises(TenantLinearError) as paused:
        command('start',2)
    assert paused.value.status==409
    with episodes.cursor() as cur:
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=2)
    episodes.commit()

    restarted=command('start',2)['lifecycle']
    assert restarted['state']=='accepted' and restarted['operator_id']==11
    command('transfer',restarted['revision'],target_employee_id=33)
    command('close',4,actor=33)
    operator={'tenant_id':A,'telegram_id':33}
    set_status(operator, {'status': 'offline'})
    assert not history(operator,'connect',41,CA,'client')['dialog']['lifecycle']['can_start']
    with pytest.raises(TenantLinearError) as offline:
        command('start',5,actor=33)
    assert offline.value.status==403
    set_status(operator, {'status': 'online'})
    assert history(operator,'connect',41,CA,'client')['dialog']['lifecycle']['can_start']
    assert command('start',5,actor=33)['lifecycle']['operator_id']==33


def test_owner_can_start_new_cycle_from_dismissed_dialog(episodes):
    inbound=insert_message(episodes,'for-dismiss-start',created_at='2099-01-01T00:00:00Z')
    seed_legacy_dismissed(episodes,inbound)
    start=payload('start',1)
    result=lifecycle.mutate(PROFILE,start)
    assert result['lifecycle']['state']=='accepted' and result['lifecycle']['revision']==2
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes WHERE tenant_id=%s::uuid',(A,))[0]['n']==1
    episodes.commit()


@pytest.mark.parametrize('state_before',('closed','dismissed'))
def test_transfer_completed_dialogue_preserves_episode_and_history(episodes,monkeypatch,state_before):
    from tenant_dialog_presence import set_status
    import tenant_operator_dialogs
    monkeypatch.setattr(tenant_operator_dialogs,'_connect',lambda:episodes)
    recipient={'tenant_id':A,'telegram_id':33}
    def read(profile):
        return tenant_operator_dialogs.history(profile,'connect',41,CA,'client')
    if state_before=='dismissed':
        inbound=insert_message(episodes,'for-completed-transfer',created_at='2099-01-01T00:00:00Z')
        seed_legacy_dismissed(episodes,inbound)
    else:
        command()
        assert read(PROFILE)['dialog']['lifecycle']['accepted_by_name']=='Owner'
        command('close',1)
    before=read(PROFILE)
    workflow=state(episodes)
    original_episodes=episodes.execute('SELECT * FROM saas_dialog_episodes ORDER BY id')
    episodes.commit()
    assert before['dialog']['lifecycle']['can_transfer'] is True
    expected_acceptor='Owner' if state_before=='closed' else None
    assert before['dialog']['lifecycle'].get('accepted_by_name')==expected_acceptor
    assert {'employee_id':33,'name':'Employee'} in before['dialog']['lifecycle']['transfer_targets']
    assert read(recipient)['dialog']['archive_read_only']
    set_status(recipient,{'status':'away'})
    with pytest.raises(TenantLinearError) as unavailable:
        command('transfer',workflow['revision'],target_employee_id=33)
    assert unavailable.value.status==403
    assert state(episodes)['operator_id']==11
    set_status(recipient,{'status':'online'})
    result=command('transfer',workflow['revision'],target_employee_id=33)
    assert result['lifecycle']['state']==state_before
    assert result['lifecycle']['operator_id']==33
    assert state(episodes)['closed_at']==workflow['closed_at']
    assert episodes.execute('SELECT * FROM saas_dialog_episodes ORDER BY id')==original_episodes
    episodes.commit()
    after=read(recipient)
    assert after['messages']==before['messages']
    assert after['dialog']['lifecycle']['can_start'] is True
    assert after['dialog']['lifecycle'].get('accepted_by_name')==expected_acceptor
    assert after['dialog']['lifecycle']['can_submit_case'] is False
    assert after['dialog']['can_send'] is False
    assert read(PROFILE)['dialog']['archive_read_only']
    assert lifecycle.transfer_details(recipient,payload())['transfer']['to_name']=='Employee'
    listed=tenant_operator_dialogs.listing(recipient,lifecycle='completed')['dialogs']
    assert any(item['lead_id']==41 for item in listed)
    assert all('accepted_by_name' not in item.get('lifecycle',{}) for item in listed)
    command('start',result['lifecycle']['revision'],actor=33)
    assert read(recipient)['dialog']['lifecycle']['accepted_by_name']=='Employee'


def test_owner_has_no_bypass_transfer_revokes_prior_operator(episodes):
    command(actor=33)
    with episodes.cursor() as cur:
        with pytest.raises(TenantLinearError) as hidden:
            lifecycle.require_access(cur,A,11,'connect',CA,'client')
        assert hidden.value.status==404
    episodes.commit()
    transfer=payload('transfer',1,target_employee_id=11)
    result=lifecycle.mutate({'tenant_id':A,'telegram_id':33},transfer)
    assert result['lifecycle']['operator_id']==11
    from tenant_dialog_presence import set_status
    set_status(PROFILE,{'status':'away'})
    # Idempotency never grants the former operator access after transfer.
    with pytest.raises(TenantLinearError) as replay:
        lifecycle.mutate({'tenant_id':A,'telegram_id':33},transfer)
    assert replay.value.status==404
    with episodes.cursor() as cur:
        with pytest.raises(TenantLinearError):lifecycle.require_access(cur,A,33,'connect',CA,'client')
        lifecycle.require_access(cur,A,11,'connect',CA,'client','update')
    episodes.commit()
    assert state(episodes)['revision']==2
    with pytest.raises(TenantLinearError):command('close',2,actor=33)
    assert state(episodes)['state']=='accepted'


def test_transfer_details_show_only_the_current_visible_episode(episodes):
    route = payload()
    command(actor=33)
    assert lifecycle.transfer_details({'tenant_id': A, 'telegram_id': 33}, route) == {'transfer': None}
    command('transfer', 1, actor=33, target_employee_id=11)

    transfer = lifecycle.transfer_details(PROFILE, route)['transfer']
    assert transfer['from_name'] == 'Employee'
    assert transfer['to_name']
    assert transfer['at']
    with pytest.raises(TenantLinearError) as denied:
        lifecycle.transfer_details({'tenant_id': A, 'telegram_id': 33}, route)
    assert denied.value.status == 404
    with pytest.raises(TenantLinearError):
        lifecycle.transfer_details(PROFILE, {**route, 'channel_id': CB})

    command('close', 2)
    assert lifecycle.transfer_details(PROFILE, route)['transfer'] == transfer
    command('start', 3)
    assert lifecycle.transfer_details(PROFILE, route) == {'transfer': None}



def test_employee_presence_is_self_scoped_and_tenant_isolated(episodes):
    import tenant_dialog_presence as presence
    own={'tenant_id':A,'telegram_id':33}
    status=presence.get_status(own)
    assert status['available'] and status['can_update'] and status['routing_enforced']
    assert status['status']=='online' and status['configured']
    presence.set_status(own,{'status':'lunch'})
    assert presence.get_status(own)['status']=='lunch'
    with pytest.raises(TenantLinearError):
        presence.set_status(own,{'status':'online','employee_id':11})
    with pytest.raises(TenantLinearError):
        presence.set_status(own,{'status':'not-a-status'})
    try:
        other=presence.get_status({'tenant_id':B,'telegram_id':33})
    except TenantLinearError:
        other=None
    assert other is None or other['status'] is None


def test_stale_transfer_candidate_is_rejected_and_assignment_is_preserved(episodes):
    import tenant_dialog_presence as presence
    from tenant_operator_access import access
    command(actor=33)
    with episodes.cursor() as cur:
        tenant,_,policy=access(cur,{'tenant_id':A,'telegram_id':33},'dialogs')
        listed=lifecycle._targets(cur,tenant,policy,CA,33)
        assert 11 in {item['employee_id'] for item in listed}
    episodes.commit()
    presence.set_status({'tenant_id':A,'telegram_id':11},{'status':'lunch'})
    with pytest.raises(TenantLinearError) as stale:
        lifecycle.mutate({'tenant_id':A,'telegram_id':33},
                         payload('transfer',1,target_employee_id=11))
    assert stale.value.status==403
    assert state(episodes)['operator_id']==33
    with episodes.cursor() as cur:
        lifecycle.require_access(cur,A,33,'connect',CA,'client','update')
        with pytest.raises(TenantLinearError):
            lifecycle.require_access(cur,A,11,'connect',CA,'client','update')
    episodes.commit()


def test_away_owner_can_claim_waiting_dialog_and_assignment_stays_protected(episodes):
    import tenant_dialog_presence as presence
    presence.set_status({'tenant_id':A,'telegram_id':11},{'status':'away'})
    with episodes.cursor() as cur:
        preview=lifecycle.decorate(cur,A,11,{'provider':'connect','channel_id':CA,
            'conversation_id':'client','lead_id':41,'can_send':True},details=False,
            _rollout=SimpleNamespace(enforced=True,admission_enabled=True),_can_update=True,
            _policy=SimpleNamespace(profile={'role':'owner'}))
        assert preview['lifecycle']['can_accept'] is True
    episodes.commit()
    command(actor=11)
    assert state(episodes)['operator_id']==11
    # Once the owner explicitly accepts an unassigned conversation, normal
    # compare-and-set ownership still prevents an operator from taking it.
    with pytest.raises(TenantLinearError) as lost:
        command(actor=33)
    assert lost.value.status in {404,409}
    with episodes.cursor() as cur:
        with pytest.raises(TenantLinearError):
            lifecycle.require_access(cur,A,33,'connect',CA,'client','update')
    episodes.commit()
    assert state(episodes)['operator_id']==11


def test_ai_and_visibility_stay_exact_source_and_tenant(episodes):
    command()
    with episodes.cursor() as cur:
        assert lifecycle.ai_blocked(cur,A,'connect',CA,'client')
        assert not lifecycle.ai_blocked(cur,B,'connect',CB,'client')
        assert not lifecycle.ai_blocked(cur,A,'connect',CB,'client')
        assert not lifecycle.ai_blocked(cur,A,'connect',CA,'another')
        assert not lifecycle.ai_blocked(cur,A,'kommo',CA,'client')
    hidden_policy=SimpleNamespace(profile={'tenant_id':A,'telegram_id':33,'role':'owner'})
    allowed_policy=SimpleNamespace(profile={'tenant_id':A,'telegram_id':11,'role':'owner'})
    for policy,visible in [(hidden_policy,False),(allowed_policy,True)]:
        sql=lifecycle.visibility_sql(cur,policy,"'connect'","c.connect_channel_id::text",'c.conversation_id')
        assert bool(episodes.execute('SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=%s::uuid AND '+sql,(A,))) is visible
    episodes.commit()


def test_reopen_only_new_real_incoming_preserves_episode(episodes):
    command();command('close',1)
    closed=state(episodes)
    for message,event,replay in [('duplicate','2099-01-01T00:00:00Z',True),('old','2000-01-01T00:00:00Z',False),('missing',None,False),('', '2099-01-01T00:00:00Z',False)]:
        with episodes.cursor() as cur:lifecycle.note_inbound(cur,A,CA,'client',message,event,is_replay=replay)
        episodes.commit();assert state(episodes)['state']=='closed'
    with episodes.cursor() as cur:lifecycle.note_inbound(cur,A,CA,'client','fresh','2099-01-01T00:00:00Z')
    episodes.commit();assert state(episodes)['state']=='waiting';assert state(episodes)['revision']==3
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==1
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0
    episodes.commit()
    with episodes.cursor() as cur:lifecycle.note_inbound(cur,A,CA,'client','fresh','2099-01-01T00:00:00Z')
    episodes.commit();assert state(episodes)['revision']==3
    result=command(revision=3,actor=33)
    assert result['lifecycle']['id']!=str(closed['id'])
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==2


def insert_message(db, external, *, direction='incoming', created_at='2000-01-01T00:00:00Z'):
    key=str(uuid.uuid4())
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,created_at)
      VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,'Synthetic message',%s::timestamptz)""",
      (key,A,external,'connect:'+CA,direction,created_at))
    with db.cursor() as cur:lifecycle.record_message(cur,A,CA,'client',key)
    db.commit()
    return key


def test_explicit_case_captures_durable_membership_not_timestamps(episodes):
    # The same historical timestamp can be observed before, during and after
    # the workflow. Membership is admission under the route lock, not clocks.
    before=insert_message(episodes,'before')
    command()
    inside=[insert_message(episodes,'inside-old',created_at='1900-01-01T00:00:00Z'),
            insert_message(episodes,'inside-future',created_at='2099-01-01T00:00:00Z')]
    command('close',1)
    after=insert_message(episodes,'after')
    from tenant_dialog_episode_cases import scoped,snapshot_rows
    selected=state(episodes)['id']
    with episodes.cursor() as cur:
        rows=snapshot_rows(cur,scoped(PROFILE,selected),{'channel_id':CA,'conversation_id':'client','history_mode':'channel'},{})
    assert {str(r['id']) for r in rows}==set(inside)
    assert before not in {str(r['id']) for r in rows} and after not in {str(r['id']) for r in rows}
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0


def test_case_waits_for_pending_dispatch_and_local_history(episodes):
    import json
    accepted=command()['lifecycle'];request_id=str(uuid.uuid4())
    with episodes.cursor() as cur:
        lifecycle.record_dispatch(cur,A,CA,'client',request_id,{'message_id':'provider-delayed','provider_accepted':True})
    episodes.execute("""INSERT INTO saas_chat_send_receipts(tenant_id,actor_id,request_id,lead_id,input_hash,state,route)
      VALUES(%s::uuid,11,%s::uuid,41,'synthetic','unknown',%s::jsonb)""",(A,request_id,json.dumps({'lifecycle_episode_id':accepted['id']})))
    episodes.commit();command('close',1)
    with pytest.raises(TenantLinearError) as pending:command('case',2)
    assert pending.value.status==409
    assert not episodes.execute('SELECT * FROM saas_support_tickets');episodes.commit()
    episodes.execute("UPDATE saas_chat_send_receipts SET state='accepted' WHERE tenant_id=%s::uuid AND request_id=%s::uuid",(A,request_id));episodes.commit()
    with pytest.raises(TenantLinearError) as history:command('case',2)
    assert history.value.status==409
    assert not episodes.execute('SELECT * FROM saas_support_tickets');episodes.commit()
    message=insert_message(episodes,'connect:provider-delayed',direction='outgoing')
    rows=episodes.execute('SELECT episode_id::text,message_id::text FROM saas_dialog_episode_messages');episodes.commit()
    assert rows==[{'episode_id':accepted['id'],'message_id':message}]
    from tenant_dialog_episode_cases import scoped,snapshot_rows
    with episodes.cursor() as cur:
        rows=snapshot_rows(cur,scoped(PROFILE,accepted['id']),{'channel_id':CA,'conversation_id':'client','history_mode':'channel'},{})
    assert [str(row['id']) for row in rows]==[message]
    assert not episodes.execute('SELECT * FROM saas_support_tickets')


def test_delayed_old_send_stays_in_original_episode_after_new_acceptance(episodes):
    old=command()['lifecycle'];request_id=str(uuid.uuid4())
    with episodes.cursor() as cur:lifecycle.record_dispatch(cur,A,CA,'client',request_id,{'message_id':'old-provider-send'})
    episodes.commit();command('close',1)
    with episodes.cursor() as cur:lifecycle.note_inbound(cur,A,CA,'client','reopened','2099-01-01T00:00:00Z')
    episodes.commit();new=command(revision=3,actor=33)['lifecycle']
    delayed=insert_message(episodes,'connect:old-provider-send',direction='outgoing')
    fresh=insert_message(episodes,'fresh-episode-message')
    bindings=episodes.execute('SELECT episode_id::text,message_id::text FROM saas_dialog_episode_messages');episodes.commit()
    assert {row['message_id']:row['episode_id'] for row in bindings}=={delayed:old['id'],fresh:new['id']}
    assert not episodes.execute('SELECT * FROM saas_support_tickets')


def test_legacy_dismiss_receipt_replays_audited_alias_but_new_marks_require_exact_route(episodes):
    import hashlib
    import json
    from tenant_connect_direct_reconcile import ensure_schema
    ensure_schema(episodes)
    message=str(episodes.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41',(A,))[0]['id'])
    historical=seed_legacy_dismissed(episodes,message)
    alias={'conversation_id':'historical-alias','lead_id':99}
    episodes.execute("""INSERT INTO saas_connect_direct_merge_backups
      (id,tenant_id,channel_id,pair_id,canonical_conversation_id,split_conversation_id,snapshot,state,created_by)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'fixture-pair','client',%s,%s::jsonb,'merged',11)""",
      (str(uuid.uuid4()),A,CA,alias['conversation_id'],json.dumps({'version':2,'leads':[41,99]})))
    old=payload('dismiss',0,message_id=message)
    old.update(alias)
    result={'lifecycle':historical}
    digest=hashlib.sha256(json.dumps(old,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    episodes.execute("""INSERT INTO saas_dialog_lifecycle_receipts(tenant_id,request_id,actor_id,input_hash,result)
      VALUES(%s::uuid,%s::uuid,11,%s,%s::jsonb)""",(A,old['request_id'],digest,json.dumps(result)))
    episodes.commit()
    assert lifecycle.mutate(PROFILE,old)==result
    for changes in (alias,{'channel_id':''},{'conversation_id':''}):
        forged=payload('dismiss',1,message_id=message,individual_no_reply=True)
        forged.update(changes)
        with pytest.raises(TenantLinearError) as not_exact:
            lifecycle.mutate(PROFILE,forged)
        assert not_exact.value.status==409
        assert 'Обновите диалог' in str(not_exact.value)
    assert not episodes.execute("SELECT 1 FROM saas_tenant_audit_events WHERE action='dialog_no_reply'")
    assert state(episodes)['state']=='dismissed'


def test_rhetorical_marks_only_assigned_accepted_latest_message_without_completing(episodes,monkeypatch):
    import tenant_operator_dialogs as dialogs
    monkeypatch.setattr(dialogs,'_connect',lambda:episodes)
    message=str(episodes.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41',(A,))[0]['id'])
    episodes.commit()
    waiting=dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['lifecycle']
    assert waiting['state']=='waiting' and not waiting['can_dismiss']
    with pytest.raises(TenantLinearError) as not_yet:
        command('dismiss',0,message_id=message,individual_no_reply=True)
    assert not_yet.value.status==409
    assert state(episodes) is None
    operator={'tenant_id':A,'telegram_id':33}
    accepted=command(actor=33)['lifecycle']
    detail_before=dialogs.history(operator,'connect',41,CA,'client')['dialog']
    current=detail_before['lifecycle']
    assert current['can_dismiss'] and current['can_close']
    with pytest.raises(TenantLinearError) as bulk:
        command('dismiss',1,actor=33,message_id=message)
    assert bulk.value.status==422
    with pytest.raises(TenantLinearError) as foreign:
        command('dismiss',1,actor=11,message_id=message,individual_no_reply=True)
    assert foreign.value.status==404
    with pytest.raises(TenantLinearError) as stale:
        command('dismiss',1,actor=33,message_id=str(uuid.uuid4()),individual_no_reply=True)
    assert stale.value.status==409
    request=payload('dismiss',1,message_id=message,individual_no_reply=True)
    result=lifecycle.mutate(operator,request)
    status=result['lifecycle']
    assert status=={**accepted,'revision':2,'no_reply_needed':True}
    assert state(episodes)['closed_at'] is None
    assert lifecycle.mutate(operator,request)==result
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==1
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0
    events=episodes.execute("SELECT entity_id,actor_telegram_id,payload FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='dialog_no_reply'",(A,))
    assert len(events)==1 and events[0]['payload']['message_id']==message and events[0]['payload']['counts_as_missed'] is False
    assert str(events[0]['entity_id'])==accepted['id'] and events[0]['actor_telegram_id']==33
    episodes.commit()
    item=next(row for row in dialogs.listing(operator,lifecycle='active')['dialogs'] if row['lead_id']==41)
    assert item['lifecycle']['no_reply_needed'] is True
    details=dialogs.history(operator,'connect',41,CA,'client')['dialog']
    assert details['lifecycle']['no_reply_needed'] and details['lifecycle']['can_close']
    assert details['can_send']==detail_before['can_send']
    assert not details['lifecycle']['can_dismiss']
    assert not [r for r in dialogs.listing(operator,lifecycle='completed')['dialogs'] if r['lead_id']==41]
    transferred=command('transfer',2,actor=33,target_employee_id=11)['lifecycle']
    assert transferred['state']=='accepted' and transferred['id']==accepted['id']
    assert dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['lifecycle']['no_reply_needed']
    with pytest.raises(TenantLinearError) as old_actor:
        lifecycle.mutate(operator,request)
    assert old_actor.value.status==404
    newer=insert_message(episodes,'real-new-inbound',created_at='2099-01-01T00:00:00Z')
    with episodes.cursor() as cur:lifecycle.note_inbound(cur,A,CA,'client',newer,'2099-01-01T00:00:00Z')
    episodes.commit()
    fresh=dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['lifecycle']
    assert fresh['state']=='accepted' and not fresh['no_reply_needed'] and fresh['can_dismiss']
    assert command('close',fresh['revision'])['lifecycle']['state']=='closed'


@pytest.mark.parametrize('episodes',['legacy','shadow'],indirect=True)
def test_unactivated_tenant_preserves_legacy_reads_ai_and_no_episode_writes(episodes):
    with episodes.cursor() as cur:
        lifecycle.require_access(cur,A,11,'connect',CA,'client','update')
        assert lifecycle.ai_blocked(cur,A,'connect',CA,'client') is False
        assert lifecycle.visibility_sql(cur,SimpleNamespace(profile={'tenant_id':A,'telegram_id':11}))=='TRUE'
        dialog={'provider':'connect','channel_id':CA,'conversation_id':'client','can_send':True}
        assert lifecycle.decorate(cur,A,11,dict(dialog))==dialog
        lifecycle.note_inbound(cur,A,CA,'client','new-disabled','2099-01-01T00:00:00Z')
    episodes.commit()
    assert state(episodes) is None
    with pytest.raises(TenantLinearError) as disabled:command()
    assert disabled.value.status==409
    assert state(episodes) is None


def test_env_flags_cannot_disable_activated_ownership(episodes,monkeypatch):
    command(actor=33)
    monkeypatch.setenv('TENANT_DIALOG_LIFECYCLE_ENABLED','false')
    monkeypatch.setenv('TENANT_DIALOG_ACCEPT_ENABLED','false')
    with episodes.cursor() as cur:
        with pytest.raises(TenantLinearError) as denied:
            lifecycle.require_access(cur,A,11,'connect',CA,'client','view')
        assert denied.value.status==404
    episodes.rollback()
    assert state(episodes)['operator_id']==33


def test_source_binding_revocation_and_none_scope_cannot_be_bypassed_by_claim(episodes,monkeypatch):
    import json
    from tenant_operator_access import access, assert_route
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    settings={'33':{'access_scopes':{'dialogs':{'view':'self','update':'self'}}}}
    episodes.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,json.dumps(settings)))
    episodes.execute("INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by) VALUES(%s::uuid,'connect',%s::uuid,33,11)",(A,CA));episodes.commit()
    request=payload()
    lifecycle.mutate({'tenant_id':A,'telegram_id':33},request)
    episodes.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND employee_id=33',(A,));episodes.commit()
    with pytest.raises(TenantLinearError) as denied:command('close',1,actor=33)
    assert denied.value.status==403
    # A formerly successful receipt is not an authorization bypass after revocation.
    with pytest.raises(TenantLinearError):lifecycle.mutate({'tenant_id':A,'telegram_id':33},request)
    with episodes.cursor() as cur:
        tenant,actor,policy=access(cur,{'tenant_id':A,'telegram_id':33})
        with pytest.raises(TenantLinearError):assert_route(cur,tenant,policy,'connect','client',CA,41)
    episodes.commit()
    assert state(episodes)['state']=='accepted'
    settings['33']['access_scopes']['dialogs']['update']='none'
    episodes.execute("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s::uuid AND policy_key='members'",(json.dumps(settings),A));episodes.commit()
    with pytest.raises(TenantLinearError):command('close',1,actor=33)


def test_admission_gate_does_not_disable_existing_ownership(episodes,monkeypatch):
    command(actor=33)
    from tenant_dialog_rollout import transition
    with episodes.cursor() as cur:
        transition(cur,A,mode='active',admission_enabled=False,expected_generation=1)
    episodes.commit()
    with episodes.cursor() as cur:
        with pytest.raises(TenantLinearError) as denied:
            lifecycle.require_access(cur,A,11,'connect',CA,'client')
        assert denied.value.status==404
    episodes.rollback()
    command('close',1,actor=33)
    with pytest.raises(TenantLinearError) as denied:
        command('accept',2,actor=33)
    assert denied.value.status==409
    assert state(episodes)['state']=='closed'


def test_claim_rejects_identity_change_while_waiting_for_route_lock(episodes, monkeypatch):
    import tenant_operator_access
    original = tenant_operator_access._resolve_connect_route
    reads = []
    def changed(*args, **kwargs):
        route = original(*args, **kwargs)
        reads.append(route)
        return {**route, 'conversation_id': 'different-canonical'} if len(reads) == 2 else route
    monkeypatch.setattr(tenant_operator_access, '_resolve_connect_route', changed)
    with pytest.raises(TenantLinearError) as error:
        command()
    assert error.value.status == 409
    assert len(reads) == 2
    assert state(episodes) is None
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n'] == 0


def test_read_rejects_canonical_changed_during_lock_without_workflow_lookup(episodes, monkeypatch):
    import tenant_connect_dialog_equivalence as equivalence
    values = iter(['client', 'different-canonical'])
    monkeypatch.setattr(equivalence, 'canonical', lambda *a: next(values))
    def forbidden(*args):
        raise AssertionError('A stale route must not reach the workflow/read permission check')
    monkeypatch.setattr(lifecycle, '_row', forbidden)
    with episodes.cursor() as cur:
        with pytest.raises(TenantLinearError) as error:
            lifecycle.require_access(cur,A,11,'connect',CA,'client')
    assert error.value.status == 409


def test_authorized_reader_rechecks_raw_proof_after_canonical_lock(episodes, monkeypatch):
    import tenant_operator_access as access_module
    original = access_module._resolve_connect_route
    reads = []
    def detached(*args, **kwargs):
        route = original(*args, **kwargs)
        reads.append(route)
        return {**route, 'lead_id': 999} if len(reads) == 2 else route
    monkeypatch.setattr(access_module, '_resolve_connect_route', detached)
    with episodes.cursor() as cur:
        tenant, actor, policy = access_module.access(cur, PROFILE)
        with pytest.raises(TenantLinearError) as error:
            access_module.assert_route(cur, tenant, policy, 'connect', 'client', CA, 41, allow_merged=True)
    assert error.value.status == 409
    assert len(reads) == 2

def test_presence_gate_is_limited_to_configured_beinsystems_workspace(episodes):
    import tenant_dialog_presence as presence
    from tenant_dialog_rollout import transition

    other = {'tenant_id': B, 'telegram_id': 22}
    status = presence.get_status(other)
    assert status == {
        'available': False, 'status': None, 'configured': False, 'can_update': False,
        'routing_enforced': False,
    }
    with pytest.raises(TenantLinearError) as disabled:
        presence.set_status(other, {'status': 'online'})
    assert disabled.value.status == 403

    # The shared presence table exists, but a non-Operator workspace keeps its
    # previous admission behavior even when no presence row exists.
    with episodes.cursor() as cur:
        assert presence.is_online(cur, B, 22) is True
        transition(cur, B, mode='active', admission_enabled=True, expected_generation=0)
    episodes.commit()
    result = lifecycle.mutate(other, {
        'provider': 'connect', 'channel_id': CB, 'conversation_id': 'client',
        'lead_id': 42, 'action': 'accept', 'expected_revision': 0,
        'request_id': str(uuid.uuid4()),
    })
    assert result['lifecycle']['state'] == 'accepted'
    row = episodes.execute(
        "SELECT state,operator_id FROM saas_dialog_workflows "
        "WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id='client'",
        (B, CB),
    )[0]
    episodes.commit()
    assert row == {'state': 'accepted', 'operator_id': 22}


def test_beinsystems_employee_without_presence_can_set_online_then_accept(episodes):
    import tenant_dialog_presence as presence
    employee = {'tenant_id': A, 'telegram_id': 33}
    episodes.execute(
        "DELETE FROM saas_dialog_employee_presence WHERE tenant_id=%s::uuid AND employee_id=33",
        (A,),
    )
    episodes.commit()
    initial = presence.get_status(employee)
    assert initial['available'] is True
    assert initial['status'] == 'online'
    assert initial['routing_enforced'] is True
    assert initial['configured'] is False
    with pytest.raises(TenantLinearError) as offline:
        lifecycle.mutate(employee, payload())
    assert offline.value.status == 403
    assert state(episodes) is None

    saved = presence.set_status(employee, {'status': 'online'})
    assert saved['status'] == 'online'
    assert presence.get_status(employee)['status'] == 'online'
    request = payload()
    accepted = lifecycle.mutate(employee, request)
    assert accepted['lifecycle']['state'] == 'accepted'
    presence.set_status(employee, {'status': 'lunch'})
    assert lifecycle.mutate(employee, request) == accepted
    assert state(episodes)['operator_id'] == 33

def test_off_preserves_claim_and_transfer_without_presence_table(episodes, monkeypatch):
    import tenant_dialog_presence as presence

    monkeypatch.delenv(presence.ROUTING_ENV)
    episodes.execute('DROP TABLE saas_dialog_employee_presence')
    episodes.commit()
    response=presence.get_status(PROFILE)
    assert not response['available'] and not response['routing_enforced']
    with pytest.raises(TenantLinearError) as unmigrated:
        presence.set_status(PROFILE, {'status':'online'})
    assert unmigrated.value.status==409

    with monkeypatch.context() as routing_only:
        def unexpected(_):
            pytest.fail('Off mode must not even look up the presence table')
        routing_only.setattr(presence, '_available', unexpected)
        with episodes.cursor() as cur:
            result=lifecycle.decorate(cur,A,11,{'provider':'connect','channel_id':CA,
                'conversation_id':'client','lead_id':41,'can_send':True},details=False,
                _rollout=SimpleNamespace(enforced=True,admission_enabled=True),_can_update=True)
            assert result['lifecycle']['can_accept'] is True
        episodes.commit()
        command()
        assert command('transfer',1,target_employee_id=33)['lifecycle']['operator_id']==33
        command('close',2,actor=33)
        assert command('start',3,actor=33)['lifecycle']['operator_id']==33

    # Even a prematurely configured switch must not issue undefined-table SQL.
    monkeypatch.setenv(presence.ROUTING_ENV, 'true')
    assert not presence.get_status(PROFILE)['routing_enforced']
    assert command('transfer',4,actor=33,target_employee_id=11)['lifecycle']['operator_id']==11


def test_enrollment_is_ready_while_routing_off(episodes, monkeypatch):
    import tenant_dialog_presence as presence

    monkeypatch.delenv(presence.ROUTING_ENV)
    employee={'tenant_id':A,'telegram_id':33}
    episodes.execute('DELETE FROM saas_dialog_employee_presence')
    with episodes.cursor() as cur:
        lifecycle.migrate(cur)
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_employee_presence')[0]['n']==0
    episodes.commit()
    assert presence.get_status(employee)=={'available':True,'status':'online','configured':False,
        'can_update':True,'routing_enforced':False}
    assert not presence.set_status(employee, {'status':'lunch'})['routing_enforced']
    assert presence.get_status(employee)['status']=='lunch'
    command(actor=11)
    assert command('transfer',1,target_employee_id=33)['lifecycle']['operator_id']==33
    monkeypatch.setenv(presence.ROUTING_ENV, 'true')
    assert presence.get_status(employee)['routing_enforced']
    with episodes.cursor() as cur:
        lifecycle.require_access(cur,A,33,'connect',CA,'client','update')
    episodes.commit()
    assert command('close',2,actor=33)['lifecycle']['state']=='closed'
    with pytest.raises(TenantLinearError) as blocked:
        command('start',3,actor=33)
    assert blocked.value.status==403


def test_offline_operator_cannot_claim_but_owner_can_manually_accept(episodes):
    import tenant_dialog_presence as presence

    # First close a previous episode, then a genuinely new inbound reopens it.
    command()
    command('close',1)
    presence.set_status(PROFILE, {'status':'away'})
    presence.set_status({'tenant_id':A,'telegram_id':33}, {'status':'lunch'})
    with episodes.cursor() as cur:
        lifecycle.note_inbound(cur,A,CA,'client',str(uuid.uuid4()),'2099-01-01T00:00:00Z')
    episodes.commit()
    with pytest.raises(TenantLinearError) as blocked:
        command(revision=3,actor=33)
    assert blocked.value.status==403
    waiting=state(episodes)
    assert waiting['state']=='waiting' and waiting['operator_id'] is None
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==1
    episodes.commit()
    # Owner can act while away; this explicit action does not change automatic
    # routing eligibility for operators.
    accepted=command(revision=3,actor=11)['lifecycle']
    assert accepted['state']=='accepted' and accepted['operator_id']==11
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==2
    episodes.commit()


def test_sidebar_transfer_only_current_online_employee_same_source(episodes, monkeypatch):
    import tenant_dialog_presence as presence
    # Manual picker checks real current status even when automatic routing is off.
    monkeypatch.delenv('TENANT_DIALOG_PRESENCE_ROUTING_ENABLED', raising=False)
    command()
    route = dict(provider='connect', channel_id=CA, conversation_id='client', lead_id='41', expected_revision='1')
    employee = {'tenant_id':A,'telegram_id':33}
    assert lifecycle.transfer_candidates(PROFILE, route) == {'targets':[{'employee_id':33,'name':'Employee'}]}
    for profile, changed in [(employee,route), (PROFILE,{**route,'channel_id':CB}),
                             (PROFILE,{**route,'expected_revision':'2'})]:
        with pytest.raises(TenantLinearError):
            lifecycle.transfer_candidates(profile,changed)
    presence.set_status(employee,{'status':'away'})
    assert lifecycle.transfer_candidates(PROFILE,route)=={'targets':[]}
    with pytest.raises(TenantLinearError) as error:
        command('transfer',1,target_employee_id=33,online_only=True)
    assert error.value.status==403
    assert state(episodes)['operator_id']==11 and state(episodes)['revision']==1
    # No saved status follows existing picker semantics (default Online).
    episodes.execute('DELETE FROM saas_dialog_employee_presence WHERE tenant_id=%s::uuid AND employee_id=33',(A,))
    episodes.commit()
    assert lifecycle.transfer_candidates(PROFILE,route)['targets'][0]['employee_id']==33
    with pytest.raises(TenantLinearError) as bad_episode:
        command('transfer',1,target_employee_id=33,online_only=True,episode_id=str(uuid.uuid4()))
    assert bad_episode.value.status==400
    accepted_id=str(state(episodes)['id'])
    assert command('transfer',1,target_employee_id=33,online_only=True)['lifecycle']['operator_id']==33
    events=episodes.execute("SELECT entity_id FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action='dialog_transfer'",(A,))
    assert events[-1]['entity_id']==accepted_id
    assert episodes.execute('SELECT accepted_by FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,accepted_id))[0]['accepted_by']==11
    assert lifecycle.transfer_candidates(employee,{**route,'expected_revision':'2'})=={
        'targets':[{'employee_id':11,'name':'Owner'}]}


@pytest.mark.parametrize('online_only', [True, False])
def test_transfer_to_online_manager_keeps_assignment_on_routed_source(episodes, monkeypatch, online_only):
    import json
    import tenant_dialog_presence as presence

    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, 'true')
    monkeypatch.setenv(presence.ROUTING_SOURCES_ENV, CA)
    query=episodes.query
    monkeypatch.setattr(episodes,'query',
        lambda sql,params=():query(sql.as_string() if hasattr(sql,'as_string') else sql,params))
    with episodes.cursor() as cur:
        presence.provision_status_schema(cur)
    episodes.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s::uuid,34,'manager','Manager','["dialogs"]'::jsonb),
              (%s::uuid,35,'worker','Read only','["dialogs"]'::jsonb),
              (%s::uuid,36,'manager','No source','["dialogs"]'::jsonb)""", (A,A,A))
    grants={str(actor):{'access_scopes':{'dialogs':{'view':'self','update':update}}}
            for actor,update in [(33,'self'),(34,'self'),(35,'none'),(36,'self')]}
    episodes.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
        VALUES(%s::uuid,'members',%s::jsonb)
        ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value""",(A,json.dumps(grants)))
    episodes.execute("""INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by)
        SELECT %s::uuid,'connect',%s::uuid,id,11 FROM (VALUES(33),(34),(35)) AS staff(id)""",(A,CA))
    episodes.commit()

    operator={'tenant_id':A,'telegram_id':33}
    manager={'tenant_id':A,'telegram_id':34}
    route={key:value for key,value in payload().items() if key in ('provider','channel_id','conversation_id','lead_id')}
    route['expected_revision']='1'
    with episodes.cursor() as cur:
        assert not presence.is_online(cur,A,34,source_channel=CA)
    episodes.commit()
    with pytest.raises(TenantLinearError) as cannot_claim:
        command(actor=34)
    assert cannot_claim.value.status==403
    accepted=command(actor=33)['lifecycle']
    assert lifecycle.transfer_candidates(operator,route)['targets']==[
        {'employee_id':11,'name':'Owner'},{'employee_id':34,'name':'Manager'}]
    for recipient in (35,36):
        with pytest.raises(TenantLinearError) as forbidden:
            command('transfer',1,actor=33,target_employee_id=recipient,online_only=online_only)
        assert forbidden.value.status==403
    presence.set_status(manager,{'status':'offline'})
    assert lifecycle.transfer_candidates(operator,route)['targets']==[{'employee_id':11,'name':'Owner'}]
    with pytest.raises(TenantLinearError) as offline:
        command('transfer',1,actor=33,target_employee_id=34,online_only=online_only)
    assert offline.value.status==403
    assert state(episodes)['operator_id']==33

    presence.set_status(manager,{'status':'online'})
    transferred=command('transfer',1,actor=33,target_employee_id=34,online_only=online_only)
    assert transferred['lifecycle']['id']==accepted['id']
    assert transferred['lifecycle']['operator_id']==34
    presence.set_status(manager,{'status':'away'})
    with episodes.cursor() as cur:
        lifecycle.note_inbound(cur,A,CA,'client',str(uuid.uuid4()),'2099-01-01T00:00:00Z')
        lifecycle.require_access(cur,A,34,'connect',CA,'client','update')
    episodes.commit()
    assert state(episodes)['operator_id']==34


def test_sidebar_transfer_rechecks_source_active_under_lock(episodes, monkeypatch):
    import tenant_dialog_presence as presence
    command()
    # Simulate deactivation after the presence helper observed an active source.
    episodes.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA))
    episodes.commit()
    monkeypatch.setattr(presence,'is_sidebar_transfer_online',lambda *args,**kwargs:True)
    with pytest.raises(TenantLinearError) as error:
        command('transfer',1,target_employee_id=33,online_only=True)
    assert error.value.status==403 and 'Источник' in str(error.value)
    assert state(episodes)['operator_id']==11 and state(episodes)['revision']==1


def test_owner_explicit_takeover_keeps_episode_and_replays_receipt(episodes):
    initial = command(actor=33)['lifecycle']
    with pytest.raises(TenantLinearError) as denied:
        command('takeover', 1, actor=33)
    assert denied.value.status == 403
    with pytest.raises(TenantLinearError) as stale:
        command('takeover', 0, actor=11)
    assert stale.value.status == 409
    assert state(episodes)['operator_id'] == 33
    request = payload('takeover', 1)
    profile = {'tenant_id': A, 'telegram_id': 11}
    result = lifecycle.mutate(profile, request)
    assert lifecycle.mutate(profile, request) == result
    assert result['lifecycle'] == dict(initial, operator_id=11, revision=2)
    event = result['operator_event']
    assert event['kind'] == 'takeover' and event['employee_name'] == 'Owner'
    audit = episodes.execute("SELECT action,payload,created_at FROM saas_tenant_audit_events WHERE id=%s::uuid", (event['id'],))
    assert audit[0]['action'] == 'dialog_transfer' and audit[0]['payload']['takeover'] is True
    assert audit[0]['created_at'].isoformat() == event['at']
    rows = episodes.execute('SELECT accepted_by,operator_id FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, initial['id']))
    assert (rows[0]['accepted_by'], rows[0]['operator_id']) == (33, 11)
