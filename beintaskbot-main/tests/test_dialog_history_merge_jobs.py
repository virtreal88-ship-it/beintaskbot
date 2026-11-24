"""Synthetic real SQL; provider reads mocked, no messages sent."""
import asyncio
import uuid

import pytest
import tenant_dialog_history_merge_jobs as jobs
from tenant_linear_policy import TenantLinearError
from test_contact_phone import active, setup, staged_source, managed, gateway, pilot, database, db
from test_tenant_support_inbox import A, B, PROFILE
from test_whatsapp_history_pairs import PN, LID


def command(channel):
    def route(conversation, lead):
        return {'provider':'connect','channel_id':channel,'conversation_id':conversation,'lead_id':lead}
    return {'operation':'create','left':route(PN,41),'right':route(LID,50),'request_id':str(uuid.uuid4())}


@pytest.fixture
def merge_case(active, monkeypatch):
    database, channel, _, _, _ = active
    monkeypatch.setenv(jobs.FLAG,'1')
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_SCOPE',f'{A}:{channel}')
    monkeypatch.setattr(jobs,'_connect',lambda:database)
    # Projection has its own real UI/SQL fixture; this fixture isolates the
    # commit and reversible job mechanism from that independent read path.
    import tenant_dialog_history_merge_projection as projection
    monkeypatch.setattr(projection,'available_job',lambda *_:True)
    database.execute("""UPDATE saas_connect_conversations SET connect_channel_id=%s,conversation_id=%s,
        conversation_kind='contact' WHERE tenant_id=%s AND lead_id=41""",(channel,PN,A))
    database.execute("""INSERT INTO saas_connect_conversations
      (tenant_id,connect_channel_id,lead_id,conversation_id,conversation_kind) VALUES(%s,%s,50,%s,'contact')""",(A,channel,LID))
    database.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s,33,'manager','Synthetic','["dialogs"]'::jsonb)""",(A,))
    database.execute('UPDATE saas_crm_messages SET channel=%s WHERE tenant_id=%s AND kommo_lead_id=41',('connect:'+channel,A))
    from tenant_chat_history_schema import ensure_schema
    ensure_schema(database)
    with database.cursor() as cur:
        from tenant_dialog_lifecycle import migrate
        migrate(cur)
        jobs.provision(cur)
    database.commit()
    return database,channel


def sample_snapshot(db):
    tables=('saas_connect_conversations','saas_connect_direct_identities','saas_crm_messages',
            'saas_dialog_workflows','saas_dialog_episodes','saas_dialog_episode_sends',
            'saas_connect_dialog_links')
    with db as conn:
        return {name:conn.execute('SELECT row_to_json(x) AS row FROM '+name+' x ORDER BY row_to_json(x)::text')
                for name in tables}


def test_queue_activation_is_lazy_idempotent_and_reversible_with_large_histories(merge_case,monkeypatch):
    db,channel=merge_case
    db.execute("""INSERT INTO saas_crm_messages
        (id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at)
        SELECT gen_random_uuid(),%s,CASE WHEN mod(n,2)=0 THEN 41 ELSE 50 END,'synthetic-merge-'||n,
          %s,CASE WHEN mod(n,3)=0 THEN 'incoming' ELSE 'outgoing' END,repeat('fixture ',40),
          '2026-11-12 09:00+00'::timestamptz - n * interval '1 second'
          FROM generate_series(1,4100) n""",(A,'connect:'+channel));db.commit()
    before=sample_snapshot(db)
    before_auth=db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s",(A,));db.commit()
    traces=[]
    original=db.query
    def traced(sql,params=()):
        traces.append(sql)
        return original(sql,params)
    monkeypatch.setattr(db,'query',traced)
    req=command(channel)
    assert jobs.action(PROFILE,{'operation':'latest','left':req['left'],'right':req['right']}) is None
    job=jobs.action(PROFILE,req)
    assert jobs.action(PROFILE,req)==job and job['processed']==0 and job['state']=='queued'
    assert jobs.action(PROFILE,{**req,'left':req['right'],'right':req['left'],'request_id':str(uuid.uuid4())})==job
    with pytest.raises(TenantLinearError):
        jobs.action({'tenant_id':A,'telegram_id':33},req)
    with pytest.raises(TenantLinearError):
        jobs.action({'tenant_id':B,'telegram_id':22},{'operation':'status','job_id':job['id']})
    calls=[]
    async def fetch(route):
        assert not db.transaction, 'provider read must run after every SQL lock releases'
        assert route['conversation_id']==LID and route['native']['scope']['source']==channel
        calls.append(route['conversation_id'])
        with db as conn:
            assert not conn.execute("SELECT 1 FROM saas_dialog_history_merge_jobs WHERE state='completed'")
        return {'phone':'+'+PN.split('@')[0]}
    asyncio.run(jobs.tick(fetch))
    assert jobs.action(PROFILE,{'operation':'status','job_id':job['id']})['processed']==2
    asyncio.run(jobs.tick(fetch))
    complete=jobs.action(PROFILE,{'operation':'status','job_id':job['id']})
    assert complete['state']=='completed' and complete['processed']==3 and complete['dialog']['lead_id']==41
    assert calls==[LID]
    assert not any('saas_crm_messages' in query for query in traces), 'queue did unnecessary history work'
    assert jobs.action(PROFILE,{'operation':'undo','job_id':job['id']})['state']=='undone'
    assert sample_snapshot(db)==before
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s",(A,))[0]['n']==before_auth[0]['n']+2
    db.commit()


def test_duplicate_search_omits_effective_completed_pairs_and_restores_undone(merge_case,monkeypatch):
    import tenant_dialog_duplicates as discovery
    database,channel=merge_case
    monkeypatch.setattr(discovery,'_connect',lambda:database)
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    phone='+'+PN.split('@')[0]
    with database as conn:
        conn.execute("UPDATE saas_connect_conversations SET contact_name='Fixture Pair', phone=%s "
                     'WHERE tenant_id=%s AND connect_channel_id=%s AND lead_id IN (41,50)',(phone,A,channel))

    def matches(mode):
        return discovery.discover(PROFILE,{'mode':mode})['dialogs']

    job=jobs.action(PROFILE,command(channel))
    assert job['state']=='queued'
    assert {row['lead_id'] for row in matches('phone')}=={41,50}
    async def fetch(_):
        return {'phone':phone}
    asyncio.run(jobs.tick(fetch))
    asyncio.run(jobs.tick(fetch))
    assert jobs.action(PROFILE,{'operation':'status','job_id':job['id']})['state']=='completed'
    for mode in ('phone','name','both'):
        assert matches(mode)==[]

    with database as conn:
        conn.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,lead_id,conversation_id,"
                     "conversation_kind,contact_name,phone) VALUES(%s,%s,51,'995551234567@lid','contact','Fixture Pair',%s)",
                     (A,channel,phone))
    for mode in ('phone','name','both'):
        rows=matches(mode)
        assert {row['lead_id'] for row in rows}=={41,51}
        assert {row['group_size'] for row in rows}=={2}

    # When only the alternate matches another contact by name, do not hide
    # the independent duplicate or label the primary with the alternate name.
    with database as conn:
        conn.execute("UPDATE saas_connect_conversations SET contact_name='Different name' "
                     'WHERE tenant_id=%s AND connect_channel_id=%s AND lead_id=41',(A,channel))
    assert {row['lead_id'] for row in matches('name')}=={50,51}
    assert jobs.action(PROFILE,{'operation':'undo','job_id':job['id']})['state']=='undone'
    assert {row['lead_id'] for row in matches('phone')}=={41,50,51}


@pytest.mark.parametrize('change',['cancel','wrong_phone','gateway','lifecycle','role'])
def test_no_publication_on_cancel_staleness_or_unproven(merge_case,change):
    db,channel=merge_case
    job=jobs.action(PROFILE,command(channel))
    async def fetch(route):
        assert not db.transaction
        if change=='cancel':
            assert jobs.action(PROFILE,{'operation':'cancel','job_id':job['id']})['state']=='cancelled'
        elif change=='gateway':
            with db as conn: conn.execute('UPDATE saas_browser_call_sources SET enabled=FALSE WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',(A,channel))
        elif change=='lifecycle':
            with db as conn:
                conn.execute("""INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id,closed_at)
                              VALUES(%s,%s,%s,%s,'closed',11,now())""",(A,channel,PN,str(uuid.uuid4())))
        elif change=='role':
            with db as conn:conn.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s AND telegram_id=11",(A,))
        return {'phone':'+994123456789' if change=='wrong_phone' else '+'+PN.split('@')[0]}
    asyncio.run(jobs.tick(fetch))
    asyncio.run(jobs.tick(fetch))
    with db as conn:
        row=conn.execute('SELECT state,code FROM saas_dialog_history_merge_jobs WHERE id=%s::uuid',(job['id'],))[0]
    assert row=={'state':'cancelled' if change=='cancel' else 'blocked',
                 'code':{'cancel':'','wrong_phone':'identity_unproven','gateway':'scope_changed',
                         'lifecycle':'lifecycle_in_use','role':'scope_changed'}[change]}


def test_disabled_has_no_database_demand(monkeypatch):
    monkeypatch.delenv(jobs.FLAG,raising=False)
    def no(*args): pytest.fail('disabled feature must not touch DB')
    monkeypatch.setattr(jobs,'_connect',no)
    assert not jobs.available({'role':'owner','active':True})
    asyncio.run(jobs.tick())
    with pytest.raises(TenantLinearError):
        jobs.action(PROFILE,{'operation':'status','job_id':str(uuid.uuid4())})


@pytest.mark.parametrize('blocked_by',['accepted_workflow','closed_episode','legacy_conflict'])
def test_protected_exact_pair_never_calls_provider_or_changes_records(merge_case,blocked_by):
    database,channel=merge_case
    with database as conn:
        if blocked_by=='accepted_workflow':
            conn.execute("""INSERT INTO saas_dialog_workflows
                (tenant_id,channel_id,conversation_id,id,state,operator_id)
                VALUES(%s,%s,%s,%s,'accepted',11)""",(A,channel,LID,str(uuid.uuid4())))
        elif blocked_by=='closed_episode':
            conn.execute("""INSERT INTO saas_dialog_episodes
                (tenant_id,id,channel_id,conversation_id,lead_id,accepted_by,operator_id,closed_at)
                VALUES(%s,%s,%s,%s,50,33,33,now())""",(A,str(uuid.uuid4()),channel,LID))
        else:
            conn.execute("""INSERT INTO saas_connect_identity_conflict_events
                (tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES(%s,%s,%s,'split','{}')""",
                (A,channel,PN.replace('@s.whatsapp.net','@c.us')))
    snapshot=sample_snapshot(database)
    job=jobs.action(PROFILE,command(channel))
    async def forbidden(_):
        pytest.fail('protected exact route must fail before provider I/O')
    asyncio.run(jobs.tick(forbidden))
    result=jobs.action(PROFILE,{'operation':'status','job_id':job['id']})
    assert result['state']=='blocked'
    assert result['code']==('identity_unproven' if blocked_by=='legacy_conflict' else 'lifecycle_in_use')
    assert 'dialog' not in result
    assert sample_snapshot(database)==snapshot


@pytest.mark.parametrize('after_proof',['message','new_workflow'])
def test_replay_and_worker_resume_keep_exact_inbound_before_atomic_commit(merge_case,monkeypatch,after_proof):
    database,channel=merge_case
    req=command(channel)
    pending=jobs.action(PROFILE,req)
    item=pending['id']
    proof=[]
    async def reader(route):
        proof.append(route['conversation_id'])
        return {'phone':'+'+PN.split('@')[0]}
    asyncio.run(jobs.tick(reader))
    prepared=jobs.action(PROFILE,{'operation':'status','job_id':item})
    assert prepared['state']=='running' and prepared['processed']==2
    assert jobs.action(PROFILE,req)==prepared
    with database as conn:
        assert not conn.execute("SELECT 1 FROM saas_dialog_history_merge_jobs WHERE state='completed'")
        conn.execute("""INSERT INTO saas_crm_messages
            (id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at)
            VALUES(gen_random_uuid(),%s,50,'synthetic-after-proof',%s,'incoming','synthetic',now())""",
            (A,'connect:'+channel))
        if after_proof=='new_workflow':
            conn.execute("""INSERT INTO saas_dialog_workflows
                (tenant_id,channel_id,conversation_id,id,state,operator_id)
                VALUES(%s,%s,%s,%s,'accepted',11)""",(A,channel,LID,str(uuid.uuid4())))
    snapshot=sample_snapshot(database)
    # The next tick runs in a new async loop, with a new DB opener and no
    # in-memory state from the first worker. Only its durable proof is usable.
    calls=[]
    def new_opener():
        calls.append(True)
        return database
    monkeypatch.setattr(jobs,'_connect',new_opener)
    async def forbidden(_):
        pytest.fail('resume should apply or reject the stored proof, not request it again')
    asyncio.run(jobs.tick(forbidden))
    status=jobs.action(PROFILE,{'operation':'status','job_id':item})
    assert calls and proof==[LID]
    if after_proof=='new_workflow':
        assert status['state']=='blocked' and status['code']=='lifecycle_in_use'
        assert 'dialog' not in status
    else:
        assert status['state']=='completed' and status['processed']==3
        assert jobs.action(PROFILE,req)==status
    assert sample_snapshot(database)==snapshot


@pytest.mark.parametrize('delta',['anchored_history','contradiction_after_proof'])
def test_lifecycle_worker_requires_exact_historical_proof_then_keeps_both_episodes(merge_case,monkeypatch,delta):
    import json
    database,channel=merge_case
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    proof={'pair_matches_conversation':True,'directIdentity':{'jid':PN,'lid':LID},
           'originalConversationId':PN,'canonicalConversationId':LID}
    self_only={'pair_matches_conversation':False,'directIdentity':{'jid':'','lid':''},
               'originalConversationId':LID,'canonicalConversationId':LID}
    def ledger(conn,event,age,body,route=LID):
        conn.execute("""INSERT INTO saas_connect_identity_conflict_events
            (tenant_id,connect_channel_id,conversation_id,event_id,evidence,created_at)
            VALUES(%s,%s,%s,%s,%s::jsonb,now()-%s*interval '1 day')""",
            (A,channel,route,event,json.dumps(body),age))
    with database as conn:
        ledger(conn,'before',3,proof)
        ledger(conn,'legacy-only',2,self_only)
        ledger(conn,'after',1,proof)
        mirror=PN.replace('@s.whatsapp.net','@c.us')
        phone_only={**self_only,'originalConversationId':PN,'canonicalConversationId':PN}
        ledger(conn,'phone-before',3,proof,PN)
        ledger(conn,'phone-self',2,phone_only,PN)
        ledger(conn,'phone-self',2,phone_only,mirror)
        ledger(conn,'phone-after',1,proof,PN)
        conn.execute("""INSERT INTO saas_connect_direct_identities
              (tenant_id,connect_channel_id,identity_key,canonical_conversation_id)
              VALUES(%s,%s,%s,%s)""",(A,channel,PN.replace('@s.whatsapp.net','@c.us'),PN))
        conn.execute("""INSERT INTO saas_connect_identity_conflicts
               (tenant_id,connect_channel_id,conversation_id,event_id,evidence)
             SELECT tenant_id,connect_channel_id,conversation_id,event_id,evidence
             FROM saas_connect_identity_conflict_events WHERE tenant_id=%s AND event_id='legacy-only'""",(A,))
        for i,route in enumerate((PN,LID)):
            conn.execute("""INSERT INTO saas_dialog_workflows
                 (tenant_id,channel_id,conversation_id,id,state,operator_id)
                 VALUES(%s,%s,%s,%s,'accepted',%s)""",(A,channel,route,str(uuid.uuid4()),(11,33)[i]))
            conn.execute("""INSERT INTO saas_dialog_episodes
                 (tenant_id,id,channel_id,conversation_id,lead_id,accepted_by,operator_id,closed_at)
                 VALUES(%s,%s,%s,%s,%s,%s,%s,now())""",
                 (A,str(uuid.uuid4()),channel,route,(41,50)[i],(11,33)[i],(11,33)[i]))
    job=jobs.action(PROFILE,command(channel))
    probes=[]
    async def scoped(route):
        assert not database.transaction
        assert route['conversation_id']==LID
        probes.append(route)
        return {'phone':'+'+PN.split('@')[0]}
    asyncio.run(jobs.tick(scoped))
    assert jobs.action(PROFILE,{'operation':'status','job_id':job['id']})['processed']==2
    if delta=='contradiction_after_proof':
        with database as conn:
            ledger(conn,'mismatch',0,{**proof,'directIdentity':{'jid':PN,'lid':'222222222222222@lid'}},
                   PN.replace('@s.whatsapp.net','@c.us'))
    before=sample_snapshot(database)
    asyncio.run(jobs.tick(scoped))
    final=jobs.action(PROFILE,{'operation':'status','job_id':job['id']})
    if delta=='anchored_history':
        assert final['state']=='completed' and final['processed']==3
    else:
        assert final['state']=='blocked' and final['code']=='identity_unproven'
    assert len(probes)==1 and sample_snapshot(database)==before


@pytest.mark.parametrize('third_physical_route',[False,True])
def test_exact_legacy_phone_domain_is_safe_only_when_not_a_third_route(merge_case,monkeypatch,third_physical_route):
    database,channel=merge_case
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    legacy=PN.replace('@s.whatsapp.net','@c.us')
    with database as conn:
        conn.execute("""UPDATE saas_connect_conversations SET conversation_id=%s
          WHERE tenant_id=%s AND connect_channel_id=%s AND conversation_id=%s""",(legacy,A,channel,PN))
        if third_physical_route:
            conn.execute("""INSERT INTO saas_connect_conversations
                (tenant_id,connect_channel_id,lead_id,conversation_id,conversation_kind)
                VALUES(%s,%s,51,%s,'contact')""",(A,channel,PN))
    req=command(channel)
    req['left']['conversation_id']=legacy
    queued=jobs.action(PROFILE,req)
    seen=[]
    async def fetch(route):
        seen.append(route['conversation_id'])
        return {'phone':'+'+PN.split('@')[0]}
    asyncio.run(jobs.tick(fetch))
    asyncio.run(jobs.tick(fetch))
    received=jobs.action(PROFILE,{'operation':'status','job_id':queued['id']})
    if third_physical_route:
        assert received['state']=='blocked' and received['code']=='identity_unproven' and not seen
    else:
        assert received['state']=='completed' and received['dialog']['conversation_id']==legacy and seen==[LID]


def test_search_and_comparison_stay_metadata_only_even_when_merge_is_available(merge_case,monkeypatch):
    from tenant_dialog_duplicate_actions import preview
    from tenant_dialog_duplicates import discover
    import tenant_dialog_duplicate_actions as actions
    import tenant_dialog_duplicates as discovery
    database,channel=merge_case
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED','1')
    monkeypatch.setattr(actions,'_connect',lambda:database)
    monkeypatch.setattr(discovery,'_connect',lambda:database)
    monkeypatch.setattr(discovery,'PAGE_SIZE',1)
    with database as conn:
        conn.execute("""UPDATE saas_connect_conversations SET contact_name='Synthetic match'
          WHERE tenant_id=%s AND connect_channel_id=%s AND conversation_id=ANY(%s::text[])""",
          (A,channel,[PN,LID]))
    sql=[]
    original=database.query
    def traced(query,params=()):
        sql.append(query)
        return original(query,params)
    monkeypatch.setattr(database,'query',traced)
    first=discover(PROFILE,{'mode':'name'})
    assert len(first['dialogs'])==1 and first['next_offset']
    second=discover(PROFILE,{'mode':'name','offset':first['next_offset']})
    assert second['next_offset'] is None
    assert {d['lead_id'] for d in first['dialogs']+second['dialogs']}=={41,50}
    command_payload=command(channel)
    routes={key:command_payload[key] for key in ('left','right')}
    assert preview(PROFILE,routes)['conflicts']==['merge_deferred']
    assert jobs.action(PROFILE,{'operation':'latest',**routes}) is None
    assert not any('saas_crm_messages' in query for query in sql), [line.strip() for query in sql for line in query.splitlines() if 'saas_crm_messages' in line]
    assert not any('INSERT INTO saas_dialog_history_merge_jobs' in query for query in sql)


@pytest.mark.parametrize('operation',['create','latest','status','cancel','undo'])
def test_every_merge_entry_fails_closed_without_exact_pilot_scope(merge_case,monkeypatch,operation):
    _,channel=merge_case
    req=command(channel)
    existing=jobs.action(PROFILE,req)
    if operation in ('create','latest'):
        payload={**req,'operation':operation}
        if operation=='latest':
            payload.pop('request_id')
    else:
        payload={'operation':operation,'job_id':existing['id']}
    # A different tenant does not even touch SQL. A wrong source pair cannot
    # be looked up preflight, and persisted IDs cannot surface another source.
    with monkeypatch.context() as m:
        def no(*_):pytest.fail('Out-of-tenant API must not reach DB')
        m.setattr(jobs,'_connect',no)
        with pytest.raises(TenantLinearError) as denied:
            jobs.action({**PROFILE,'tenant_id':B},payload)
        assert denied.value.status==404
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_SCOPE',f'{A}:{uuid.uuid4()}')
    with pytest.raises(TenantLinearError) as denied:
        jobs.action(PROFILE,payload)
    assert denied.value.status==404


@pytest.mark.parametrize('input_value',['','*','invalid','__cross__','uppercase','trailing_space'])
def test_rollout_malformed_scope_denies_capability_api_and_worker_without_database(monkeypatch,input_value):
    from tenant_dialog_history_merge_scope import configured, source_for
    channel='a0000000-0000-0000-0000-000000000001'
    value={'uppercase':f'{A}:{channel.upper()}','trailing_space':f'{A}:{channel} ',
           '__cross__':f'{A}:{channel},{B}:{channel}'}.get(input_value,input_value)
    monkeypatch.setenv(jobs.FLAG,'1')
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_SCOPE',value)
    def no(*_):pytest.fail('Unscoped rollout cannot reach DB')
    monkeypatch.setattr(jobs,'_connect',no)
    assert configured() is None and source_for(A) is None
    assert not jobs.available({**PROFILE,'role':'owner','active':True})
    asyncio.run(jobs.tick())
    with pytest.raises(TenantLinearError) as denied:
        jobs.action(PROFILE,command(channel))
    assert denied.value.status==404


def test_scoped_rollback_pauses_queue_without_writes_and_retry_uses_same_job(merge_case,monkeypatch):
    database,channel=merge_case
    queued=jobs.action(PROFILE,command(channel))
    other_channel=str(uuid.uuid4())
    with database as conn:
        # Out-of-scope persisted rows never win the claim or block this source.
        conn.execute("""INSERT INTO saas_dialog_history_merge_jobs
           (tenant_id,id,actor_id,channel_id,left_id,right_id,left_lead,right_lead,state,lease_until,due_at)
           VALUES(%s,%s,11,%s,%s,%s,41,50,'running',now()+interval '60 seconds',now()-interval '1 minute')""",
           (A,str(uuid.uuid4()),other_channel,LID,PN))
    profile={**PROFILE,'role':'owner','active':True}
    assert jobs.available(profile)
    before=sample_snapshot(database)
    monkeypatch.delenv('DIALOG_HISTORY_MERGE_SCOPE')
    def forbidden(_):
        pytest.fail('Worker must not probe after rollout rollback')
    asyncio.run(jobs.tick(forbidden))
    assert not jobs.available(profile)
    with database as conn:
        assert conn.execute('SELECT state FROM saas_dialog_history_merge_jobs WHERE id=%s::uuid',(queued['id'],))==[{'state':'queued'}]
    monkeypatch.setenv('DIALOG_HISTORY_MERGE_SCOPE',f'{A}:{channel}')
    calls=[]
    async def fetch(route):
        calls.append(route['conversation_id'])
        return {'phone':'+'+PN.split('@')[0]}
    asyncio.run(jobs.tick(fetch))
    asyncio.run(jobs.tick(forbidden))
    assert jobs.action(PROFILE,{'operation':'status','job_id':queued['id']})['state']=='completed'
    assert calls==[LID] and sample_snapshot(database)==before
    with database as conn:
        assert conn.execute('SELECT state FROM saas_dialog_history_merge_jobs WHERE channel_id=%s::uuid',(other_channel,))==[{'state':'running'}]


def test_worker_shutdown_leaves_only_recoverable_lease_not_unproven_publication(merge_case):
    database,channel=merge_case
    request=command(channel)
    job=jobs.action(PROFILE,request)
    async def cancelled():
        entered=asyncio.Event()
        async def blocked(_):
            entered.set()
            await asyncio.Event().wait()
        worker=asyncio.create_task(jobs.tick(blocked))
        await asyncio.wait_for(entered.wait(),timeout=2)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    asyncio.run(cancelled())
    with database as conn:
        assert conn.execute("""SELECT state,step,attempts FROM saas_dialog_history_merge_jobs WHERE id=%s::uuid""",(job['id'],))==[
            {'state':'running','step':1,'attempts':1}]
        conn.execute("""UPDATE saas_dialog_history_merge_jobs SET lease_until=now()-interval '1 sec',
                        due_at=now()-interval '1 sec' WHERE id=%s::uuid""",(job['id'],))
    async def verified(route):
        assert route['conversation_id']==LID
        return {'phone':'+'+PN.split('@')[0]}
    asyncio.run(jobs.tick(verified))
    asyncio.run(jobs.tick(verified))
    assert jobs.action(PROFILE,request)['state']=='completed'
    with database as conn:
        assert conn.execute("""SELECT state,step,attempts FROM saas_dialog_history_merge_jobs WHERE id=%s::uuid""",(job['id'],))==[
            {'state':'completed','step':3,'attempts':2}]


def _compared_payload(database,channel,monkeypatch):
    import tenant_dialog_duplicate_actions as duplicate
    monkeypatch.setattr(duplicate,'_connect',lambda:database)
    duplicate.prepare_schema(database)
    with database as cur:
        cur.execute("""UPDATE saas_connect_conversations SET contact_name=CASE
            WHEN lead_id=41 THEN 'Original name' ELSE 'Another name' END WHERE tenant_id=%s AND connect_channel_id=%s""",
            (A,channel))
        cur.execute("""INSERT INTO saas_connect_dialog_display_phones
           (tenant_id,channel_id,conversation_id,lead_id,phone)
           VALUES(%s,%s,%s,50,'+994123456789')""",(A,channel,LID))
    request=command(channel)
    preview=duplicate.preview(PROFILE,{k:request[k] for k in ('left','right')})
    assert any(n['lead_id']==41 and n['display_name']=='Original name' for n in preview['name_choices'])
    assert any(n['lead_id']==50 and n['display_phone']=='+994123456789' and n['display_only']
               for n in preview['phone_choices'])
    return {**request,'display':{'name_lead_id':41,'phone_lead_id':50,'revision':preview['display_revision']}}


def test_independent_display_choices_snapshot_never_affects_provider_route_or_proof(merge_case,monkeypatch):
    database,channel=merge_case
    request=_compared_payload(database,channel,monkeypatch)
    before=sample_snapshot(database)
    created=jobs.action(PROFILE,request)
    assert not {'display_name','display_phone'} & created.keys()
    assert jobs.action(PROFILE,request)==created
    with pytest.raises(TenantLinearError) as changed:
        jobs.action(PROFILE,{**request,'display':{**request['display'],'name_lead_id':50}})
    assert changed.value.status==409
    calls=[]
    async def verifier(route):
        calls.append(route['conversation_id'])
        return {'phone':'+'+PN.split('@')[0]} # strictly different from chosen display phone
    asyncio.run(jobs.tick(verifier))
    asyncio.run(jobs.tick(verifier))
    complete=jobs.action(PROFILE,request)
    assert complete['state']=='completed' and complete['dialog']['conversation_id']==PN and calls==[LID]
    with database as cur:
        row=cur.execute("""SELECT display_name,display_phone,display_name_lead,display_phone_lead
            FROM saas_dialog_history_merge_jobs WHERE id=%s::uuid""",(created['id'],))[0]
        assert row=={'display_name':'Original name','display_phone':'+994123456789',
                     'display_name_lead':41,'display_phone_lead':50}
    assert sample_snapshot(database)==before


def test_stale_or_unoffered_display_choices_leave_no_job(merge_case,monkeypatch):
    database,channel=merge_case
    request=_compared_payload(database,channel,monkeypatch)
    with pytest.raises(TenantLinearError) as wrong_route:
        jobs.action(PROFILE,{**request,'display':{**request['display'],'phone_lead_id':999}})
    assert wrong_route.value.status==400
    with database as cur:
        cur.execute("""UPDATE saas_connect_conversations SET manual_display_name='Changed name'
            WHERE tenant_id=%s AND connect_channel_id=%s AND lead_id=41""",(A,channel))
    with pytest.raises(TenantLinearError) as stale:
        jobs.action(PROFILE,request)
    assert stale.value.status==409
    with database as cur:
        assert not cur.execute('SELECT 1 FROM saas_dialog_history_merge_jobs')


@pytest.mark.parametrize('changed',['name','phone'])
def test_display_edit_between_proof_and_publication_requires_fresh_comparison(merge_case,monkeypatch,changed):
    database,channel=merge_case
    payload=_compared_payload(database,channel,monkeypatch)
    queued=jobs.action(PROFILE,payload)
    calls=[]

    async def verifier(route):
        calls.append(route['conversation_id'])
        return {'phone':'+'+PN.split('@')[0]}

    asyncio.run(jobs.tick(verifier))
    with database as cur:
        assert cur.execute("SELECT state FROM saas_dialog_history_merge_jobs WHERE id=%s",(queued['id'],))[0]['state']=='verified'
        if changed=='name':
            cur.execute("""UPDATE saas_connect_conversations SET manual_display_name='New exact name'
              WHERE tenant_id=%s AND connect_channel_id=%s AND lead_id=41""",(A,channel))
        else:
            cur.execute("""DELETE FROM saas_connect_dialog_display_phones
              WHERE tenant_id=%s AND channel_id=%s AND lead_id=50""",(A,channel))
    asyncio.run(jobs.tick(verifier))
    outcome=jobs.action(PROFILE,{'operation':'status','job_id':queued['id']})
    assert outcome['state']=='blocked' and outcome['code']=='display_changed' and not outcome.get('dialog')
    assert calls==[LID]
    with database as cur:
        assert not cur.execute("SELECT 1 FROM saas_dialog_history_merge_jobs WHERE state='completed' AND tenant_id=%s",(A,))
        assert not cur.execute("SELECT 1 FROM saas_tenant_audit_events WHERE action='dialog_history_joined' AND tenant_id=%s",(A,))


@pytest.mark.parametrize('before',['queue','publish'])
def test_capacity_limits_new_work_without_invalidating_existing_pairs(merge_case,monkeypatch,before):
    database,channel=merge_case
    monkeypatch.setattr(jobs,'MAX_ACTIVE_PAIRS',1)
    payload=command(channel)
    new=None
    if before=='publish':
        new=jobs.action(PROFILE,payload)
        async def verifier(route):
            return {'phone':'+'+PN.split('@')[0]}
        asyncio.run(jobs.tick(verifier))
    other_id=str(uuid.uuid4())
    with database as cur:
        cur.execute("""INSERT INTO saas_dialog_history_merge_jobs
          (tenant_id,id,actor_id,channel_id,left_id,right_id,left_lead,right_lead,state,step,principal_id,
           proof_generation,proof_gateway_id,proof_at)
          SELECT tenant_id,%s,11,channel_id,'15550001111@lid','15550001111@s.whatsapp.net',
                 88,89,'completed',3,'15550001111@s.whatsapp.net',generation,gateway_id,now()
          FROM saas_whatsapp_capture_generations WHERE tenant_id=%s AND channel_id=%s""",(other_id,A,channel))
    if before=='queue':
        with pytest.raises(TenantLinearError) as at_capacity:
            jobs.action(PROFILE,payload)
        assert at_capacity.value.status==409
    else:
        asyncio.run(jobs.tick())
        outcome=jobs.action(PROFILE,{'operation':'status','job_id':new['id']})
        assert outcome['state']=='blocked' and outcome['code']=='capacity_reached'
    with database as cur:
        only=cur.execute("SELECT id::text AS id FROM saas_dialog_history_merge_jobs WHERE state='completed'")
        assert only==[{'id':other_id}]
