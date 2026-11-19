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
    assert other_owner.value.status==404
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


def test_owner_can_start_new_cycle_from_dismissed_dialog(episodes):
    inbound=insert_message(episodes,'for-dismiss-start',created_at='2099-01-01T00:00:00Z')
    command('dismiss',0,message_id=inbound)
    start=payload('start',1)
    result=lifecycle.mutate(PROFILE,start)
    assert result['lifecycle']['state']=='accepted' and result['lifecycle']['revision']==2
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes WHERE tenant_id=%s::uuid',(A,))[0]['n']==1
    episodes.commit()


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



def test_employee_presence_is_self_scoped_and_tenant_isolated(episodes):
    import tenant_dialog_presence as presence
    own={'tenant_id':A,'telegram_id':33}
    assert presence.get_status(own)=={'available':True,'status':'online','configured':True}
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
    assert other is None or other['status']=='away'


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


def test_away_owner_keeps_existing_assignment_but_cannot_claim_new_dialog(episodes):
    import tenant_dialog_presence as presence
    presence.set_status({'tenant_id':A,'telegram_id':11},{'status':'away'})
    with pytest.raises(TenantLinearError) as denied:
        command(actor=11)
    assert denied.value.status==403
    assert state(episodes) is None
    command(actor=33)
    presence.set_status({'tenant_id':A,'telegram_id':33},{'status':'away'})
    with episodes.cursor() as cur:
        lifecycle.require_access(cur,A,33,'connect',CA,'client','update')
    episodes.commit()
    assert state(episodes)['operator_id']==33


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


def test_dismiss_requires_current_incoming_and_creates_no_episode_or_case(episodes):
    message=episodes.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41',(A,))[0]['id']
    episodes.commit()
    with pytest.raises(TenantLinearError):
        command('dismiss',0,message_id=str(uuid.uuid4()))
    request=payload('dismiss',0,message_id=str(message))
    result=lifecycle.mutate(PROFILE,request)
    assert result['lifecycle']['state']=='dismissed'
    assert lifecycle.mutate(PROFILE,request)==result
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==0
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0
    episodes.commit()
    with episodes.cursor() as cur:
        with pytest.raises(TenantLinearError):lifecycle.require_access(cur,A,33,'connect',CA,'client')
        with pytest.raises(TenantLinearError):lifecycle.require_access(cur,A,11,'connect',CA,'client','update')
    episodes.commit()
    with pytest.raises(TenantLinearError):command('case',1)
    with episodes.cursor() as cur:lifecycle.note_inbound(cur,A,CA,'client','fresh','2099-01-01T00:00:00Z')
    episodes.commit();assert state(episodes)['state']=='waiting'
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==0


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
