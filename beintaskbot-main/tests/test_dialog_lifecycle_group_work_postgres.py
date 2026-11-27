"""Two operators, exact WeCall routes and real PostgreSQL transactions.

The HTTP peer is a fixture. No WhatsApp session or customer data is used.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import asyncio
import json
import logging
from threading import Barrier
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest
import requests

import tenant_chat_send_store as sends
import tenant_connect_transport as transport
import tenant_dialog_group_work as group_work
import tenant_dialog_lifecycle as lifecycle
import tenant_operator_dialogs as dialogs
from tenant_linear_policy import TenantLinearError
from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_pglite import episodes
from test_tenant_support_inbox import db, A, CA, CB


GROUP = '120363000000700000@g.us'
SOURCES = {33: (CA, 1001, '994500000111@s.whatsapp.net'),
           44: (CB, 1002, '994500000112@s.whatsapp.net')}
LIDS = {source[2]:f'12345678{actor}@lid' for actor,source in SOURCES.items()}


def profile(actor):
    return {'tenant_id':A,'telegram_id':actor}


def action(actor, name, revision=0, **extra):
    channel,lead,_=SOURCES[actor]
    return lifecycle.mutate(profile(actor),{
        'provider':'connect','channel_id':channel,'conversation_id':GROUP,'lead_id':lead,
        'request_id':str(uuid.uuid4()),'action':name,'expected_revision':revision,**extra})


def row(connection, actor):
    with connection.cursor() as cur:
        return lifecycle._row(cur,A,SOURCES[actor][0],GROUP)


def history(actor, **extra):
    channel,lead,_=SOURCES[actor]
    return dialogs.history(profile(actor),'connect',lead,channel,GROUP,**extra)


def test_acceptance_wakes_the_peer_card_on_its_authorized_source(collaboration):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from tenant_operator_api import GroupWorkUpdates, handler

    async def verify():
        origin='https://fixture.invalid'
        updates=GroupWorkUpdates(timeout=5)
        app=web.Application()
        def member(request):
            return profile(int(request.headers['X-Fixture-Actor']))
        logger=logging.getLogger(__name__)
        for method,resource in [('GET','history'),('POST','dialogs_lifecycle')]:
            app.router.add_route(method,'/'+resource,handler(
                member,origin,logger,resource,group_work_updates=updates))
        async with TestClient(TestServer(app)) as client:
            channel,lead,_=SOURCES[44]
            query={'provider':'connect','channel_id':channel,'conversation_id':GROUP,
                   'lead_id':lead,'expected_tenant_id':A,'expected_user_id':44}
            peer={'X-Fixture-Actor':'44'}
            initial=await client.get('/history',params=query,headers=peer)
            known=(await initial.json())['result']['dialog']['group_work_revision']
            watch={**query,'group_work_revision':'1','known_group_work_revision':known}
            forbidden=await client.get('/history',params={**watch,'channel_id':CA,'lead_id':1001},headers=peer)
            assert forbidden.status in (403,404)
            assert not updates.waiters
            pending=asyncio.create_task(client.get('/history',params=watch,headers=peer))
            try:
                async def subscribed():
                    while not updates.waiters:
                        await asyncio.sleep(.01)
                await asyncio.wait_for(subscribed(),timeout=2)
                updates.publish({'tenant_id':str(uuid.uuid4())},GROUP)
                assert not any(waiter.is_set() for waiter in updates.waiters)
                accepted=await client.post('/dialogs_lifecycle',
                    headers={'X-Fixture-Actor':'33','Origin':origin},json={
                        **query,'expected_user_id':33,'channel_id':CA,'lead_id':1001,
                        'action':'accept','request_id':str(uuid.uuid4()),'expected_revision':0})
                assert accepted.status==200
                receipt=(await accepted.json())['result']
                assert receipt['operator_event']['kind']=='accept'
                response=await asyncio.wait_for(pending,timeout=2)
                assert response.status==200
                assert (await response.json())['result']['revision']!=known
                updated=await client.get('/history',params={**query,'operator_events':'1',
                    'group_work_refresh':'1'},headers=peer)
                page=(await updated.json())['result']
                assert page['dialog']['lifecycle']['group_work']['can_join']
                assert not page['dialog']['can_send']
                assert page['operator_events']==[receipt['operator_event']]
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending,return_exceptions=True)
        assert not updates.waiters

    asyncio.run(verify())


def card(actor, **options):
    return next((r for r in dialogs.listing(profile(actor),**options)['dialogs']
                 if r['conversation_id']==GROUP),None)


def incoming(connection, actor, provider, *, sender='994500000199@s.whatsapp.net', happened=None):
    channel,lead,_=SOURCES[actor]
    mid=str(uuid.uuid4())
    raw={'channelId':channel,'conversationId':GROUP,'externalMessageId':provider,'participantJid':sender,
         'transport':'beintaskbot_whatsapp','nativeEventId':uuid.uuid4().hex*2,'fromMe':False}
    at=happened or datetime.now(timezone.utc)
    connection.execute("""INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,message_type,happened_at,created_at,raw)
      VALUES(%s,%s,%s,%s,%s,'incoming','Sender','Same text','text',%s,clock_timestamp(),%s::jsonb)""",
      (mid,A,lead,'connect:'+mid,'connect:'+channel,at,json.dumps(raw)))
    with connection.cursor() as cur:
        lifecycle.note_inbound(cur,A,channel,GROUP,mid,at)
    connection.commit()
    return mid


class GatewayResponse:
    def __init__(self, status, body):
        self.status_code=status
        self.headers={}
        self.body=json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        return False

    def iter_content(self, _):
        yield self.body


@pytest.fixture
def collaboration(episodes, monkeypatch):
    import tenant_call_sources as call_sources
    import tenant_platform
    import tenant_whatsapp_native_rollout as native_rollout
    import tenant_whatsapp_native_transport as native
    import tenant_dialog_presence as presence

    def connection():
        return psycopg.connect(episodes.dsn,row_factory=dict_row)

    for module in (tenant_platform,dialogs,sends,transport,native):
        monkeypatch.setattr(module,'_connect',connection)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_SEND','1')
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV,'true')
    monkeypatch.setenv(presence.ROUTING_SOURCES_ENV,','.join((CA,CB)))
    monkeypatch.setattr(call_sources,'service',lambda:('https://isolated-test.invalid','fixture'))
    with episodes.cursor() as cur:
        call_sources.migrate(cur)
        native_rollout.migrate(cur)
        presence.provision_status_schema(cur)
    episodes.execute("UPDATE saas_tenant_members SET display_name='Operator A' WHERE tenant_id=%s AND telegram_id=33",(A,))
    episodes.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
      VALUES(%s,44,'operator','Operator B','["dialogs","support"]')""",(A,))
    episodes.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Source B')",(A,CB))
    policies={str(actor):{'access_scopes':{'dialogs':{'view':'self','update':'self'}}} for actor in SOURCES}
    episodes.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value""",
      (A,json.dumps(policies)))
    for actor,(channel,lead,account) in SOURCES.items():
        gateway=str(uuid.uuid4())
        episodes.execute("""INSERT INTO saas_connect_conversations
          (tenant_id,connect_channel_id,conversation_id,conversation_kind,lead_id)
          VALUES(%s,%s,%s,'group',%s)""",(A,channel,GROUP,lead))
        episodes.execute("""INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by)
          VALUES(%s,'connect',%s,%s,11)""",(A,channel,actor))
        episodes.execute("""INSERT INTO saas_browser_call_sources
          (tenant_id,channel_id,gateway_id,account_jid,token_sha256,encrypted_token,created_by)
          VALUES(%s,%s,%s,%s,%s,%s,11)""",(A,channel,gateway,account,'synthetic-'+gateway,b'x'))
        episodes.execute("""INSERT INTO saas_whatsapp_capture_generations
          (tenant_id,channel_id,generation,gateway_id,account_jid,enabled,projection_enabled)
          VALUES(%s,%s,'group-test',%s,%s,TRUE,TRUE)""",(A,channel,gateway,account))
        episodes.execute("""INSERT INTO saas_whatsapp_native_routes(tenant_id,channel_id,generation,enabled)
          VALUES(%s,%s,'group-test',TRUE)""",(A,channel))
        episodes.execute("""INSERT INTO saas_dialog_employee_presence(tenant_id,employee_id,status,updated_by)
          VALUES(%s,%s,'online',%s) ON CONFLICT(tenant_id,employee_id) DO UPDATE SET status='online'""",
          (A,actor,actor))
    episodes.commit()
    membership={source[2]:True for source in SOURCES.values()}
    delivered=[]
    def post(url, *, json, **_):
        scope=json['scope']
        assert scope['tenant']==A and json['chat']==GROUP
        assert scope['account'] in membership
        if url.endswith('/group-membership'):
            if membership[scope['account']]:
                return GatewayResponse(200,{'member':True,'scope':scope,'chat':GROUP,'account_lid':LIDS[scope['account']]})
            return GatewayResponse(409,{'error':'native_group_membership_unconfirmed'})
        assert json['verify_group_membership'] is True
        if not membership[scope['account']]:
            return GatewayResponse(422,{'error':'native_group_membership_unconfirmed','confirmed':False,
                                        'delivery_phase':'not_sent'})
        provider='OUTBOUND_'+str(len(delivered)+1)
        delivered.append((scope['source'],scope['account'],provider))
        return GatewayResponse(200,{'confirmed':True,'result':{'state':'accepted','provider_id':provider}})
    monkeypatch.setattr(native.requests,'post',post)
    for actor in SOURCES:
        incoming(episodes,actor,'INITIAL_SHARED')
    return episodes,membership,delivered


def join(connection):
    result=action(33,'accept')
    episode=result['lifecycle']['id']
    assert card(44)['lifecycle']['group_work']['can_join']
    assert card(44)['channel_id']==CB
    action(44,'join_group',expected_group_episode_id=episode)
    return episode


def test_eligible_employee_reuses_a_closed_group_source(collaboration):
    database,_,sent=collaboration
    previous=action(44,'accept')['lifecycle']['id']
    action(44,'close',row(database,44)['revision'],expected_group_episode_id=previous)
    database.execute('''INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by)
      VALUES(%s,'connect',%s,33,11)''',(A,CB))
    database.commit()

    # Another visible employee may start from the closed source even when
    # only the current Native WhatsApp connection is configured.
    archive=dialogs.history(profile(33),'connect',1002,CB,GROUP)['dialog']
    assert archive['archive_read_only'] and not archive['can_send']
    assert archive['lifecycle']['can_start']
    command={'provider':'connect','channel_id':CB,'conversation_id':GROUP,'lead_id':1002}
    started=lifecycle.mutate(profile(33),{**command,'action':'start','request_id':str(uuid.uuid4()),
        'expected_revision':archive['lifecycle']['revision']})
    assert started['controls']['can_send']
    assert started['lifecycle']['id']!=previous
    lifecycle.mutate(profile(33),{**command,'action':'close','request_id':str(uuid.uuid4()),
        'expected_group_episode_id':started['lifecycle']['id'],
        'expected_revision':started['lifecycle']['revision']})

    own=history(33)['dialog']
    assert own['lifecycle']['can_start']
    active=action(33,'start',own['lifecycle']['revision'],
                  expected_group_episode_id=own['lifecycle']['group_restart_episode_id'])
    episode=active['lifecycle']['id']
    archived_peer=history(44)['dialog']
    assert archived_peer['archive_read_only'] and not archived_peer['can_send']
    assert not archived_peer['lifecycle']['can_start']
    work=archived_peer['lifecycle']['group_work']
    assert work['can_join'] and [source['channel_id'] for source in work['sources']]==[CB]
    action(44,'join_group',archived_peer['lifecycle']['revision'],expected_group_episode_id=episode)
    assert history(44)['dialog']['can_send']
    assert history(44)['dialog']['lifecycle']['group_work']['role']=='participant'
    assert history(33)['dialog']['lifecycle']['group_work']['role']=='leader'
    assert not sent
    assert database.execute('SELECT accepted_by FROM saas_dialog_episodes WHERE id=%s',(previous,))==[
        {'accepted_by':44}]


def add_shared_source_operator(database, monkeypatch, actor, update='self'):
    monkeypatch.setitem(SOURCES,actor,SOURCES[33])
    database.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
      VALUES(%s,%s,'operator',%s,'["dialogs","support"]')""",(A,actor,f'Operator {actor}'))
    database.execute("""UPDATE saas_tenant_workflow_policies SET value=value||%s::jsonb
      WHERE tenant_id=%s AND policy_key='members'""",
      (json.dumps({str(actor):{'access_scopes':{'dialogs':{'view':'self','update':update}}}}),A))
    database.execute("""INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by)
      VALUES(%s,'connect',%s,%s,11)""",(A,SOURCES[actor][0],actor))
    database.execute("""INSERT INTO saas_dialog_employee_presence(tenant_id,employee_id,status,updated_by)
      VALUES(%s,%s,'online',%s)""",(A,actor,actor))
    database.commit()


def test_operators_can_share_an_authorized_whatsapp_source(collaboration, monkeypatch):
    database,_,delivered=collaboration
    episode=join(database)
    add_shared_source_operator(database,monkeypatch,55)
    add_shared_source_operator(database,monkeypatch,66,'none')

    candidate=card(55)
    assert candidate['channel_id']==CA and not candidate['can_send']
    assert candidate['lifecycle']['group_work']['can_join']
    peer=history(55)['dialog']
    assert peer['lifecycle']['group_work']['can_join'] and not peer['can_send']
    assert [source['channel_id'] for source in peer['lifecycle']['group_work']['sources']]==[CA]
    assert not history(66)['dialog']['lifecycle']['group_work']['can_join']
    with pytest.raises(TenantLinearError):
        prepare(55)
    with pytest.raises(TenantLinearError):
        action(66,'join_group',row(database,66)['revision'],expected_group_episode_id=episode)
    with pytest.raises(TenantLinearError):
        action(55,'join_group',row(database,44)['revision'],expected_group_episode_id=episode,
               channel_id=CB,lead_id=1002)

    # Simulate the two indexes still present in an existing installation.
    for column in ('channel_id','account_jid'):
        database.execute(f"""CREATE UNIQUE INDEX saas_dialog_group_active_{column}_idx
          ON saas_dialog_group_participants(tenant_id,episode_id,{column}) WHERE left_at IS NULL""")
    database.commit()
    with pytest.raises(TenantLinearError, match='после обновления CRM'):
        action(55,'join_group',peer['lifecycle']['revision'],expected_group_episode_id=episode)
    with database.cursor() as cur:
        group_work.migrate(cur)
        group_work.migrate(cur)
    database.commit()

    action(55,'join_group',peer['lifecycle']['revision'],expected_group_episode_id=episode)
    assert row(database,55)['operator_id']==33
    own=history(55)['dialog']
    assert own['can_send'] and own['lifecycle']['group_work']['role']=='participant'
    assert card(55)['lifecycle']['group_work']['role']=='participant'
    for actor in (33,55):
        item,author,route=prepare(actor)
        result=dispatch(item,route)
        assert delivered[-1][:2]==(CA,SOURCES[33][2])
        transport.record_outbound(A,item['command']['lead_id'],route,item['command']['text'],result['message_id'],
            author=author['display_name'],actor=actor,receipt=result['receipt'])
        sends.finish(item,'accepted',result['message_id'])
    assert {(m['author_name'],m['sending_number']) for m in history(55)['messages'] if m.get('sending_number')}=={
        ('Operator A','+994500000111'),('Operator 55','+994500000111')}

    action(33,'leave_group',row(database,33)['revision'],expected_group_episode_id=episode)
    assert row(database,55)['operator_id']==55 and row(database,55)['state']=='accepted'
    assert history(55)['dialog']['lifecycle']['group_work']['role']=='leader'
    assert history(44)['dialog']['can_send']
    assert not history(33)['dialog']['can_send']
    with pytest.raises(TenantLinearError):
        prepare(33)
    action(44,'leave_group',row(database,44)['revision'],expected_group_episode_id=episode)
    action(55,'close',row(database,55)['revision'],expected_group_episode_id=episode)
    assert row(database,55)['state']=='closed'
    assert not database.execute("""SELECT 1 FROM saas_dialog_group_participants
      WHERE episode_id=%s AND left_at IS NULL""",(episode,))


def test_two_sources_share_episode_and_only_leader_can_transfer_or_close(collaboration):
    database,_,_=collaboration
    episode=join(database)
    for actor,role in ((33,'leader'),(44,'participant')):
        group=card(actor)['lifecycle']['group_work']
        assert group['id']==episode and group['role']==role
        assert group['number']=='+'+SOURCES[actor][2].split('@')[0]
        assert card(actor)['can_send']
        assert len(history(actor)['messages'])==1
    with pytest.raises(TenantLinearError):
        dialogs.history(profile(44),'connect',1001,CA,GROUP)
    with pytest.raises(TenantLinearError,match='Коллега ещё подключён'):
        action(33,'close',row(database,33)['revision'],expected_group_episode_id=episode,close_group_confirmed=True)
    database.commit()
    with pytest.raises(TenantLinearError,match='только последний участник'):
        action(44,'close',row(database,44)['revision'],expected_group_episode_id=episode)
    database.commit()
    action(44,'leave_group',row(database,44)['revision'],expected_group_episode_id=episode)
    database.commit()
    assert card(33)['lifecycle']['can_close']
    departed=card(44,lifecycle='completed')
    assert departed['lifecycle']['state']=='closed' and departed['lifecycle']['group_work']['can_join']
    action(44,'join_group',row(database,44)['revision'],expected_group_episode_id=episode)
    database.commit()
    action(33,'transfer_group',row(database,33)['revision'],expected_group_episode_id=episode,target_employee_id=44)
    database.commit()
    assert card(44)['lifecycle']['group_work']['role']=='leader'
    assert card(33)['lifecycle']['group_work']['role']=='participant'
    events=history(44,include_operator_events=True)['operator_events']
    assert {'accept','join_group','leave_group','transfer_group'} <= {event['kind'] for event in events}
    action(33,'leave_group',row(database,33)['revision'],expected_group_episode_id=episode)
    database.commit()
    action(44,'close',row(database,44)['revision'],expected_group_episode_id=episode)
    database.commit()
    assert all(row(database,actor)['state']=='closed' for actor in SOURCES)
    assert database.execute("""SELECT accepted_by,operator_id,closed_at IS NOT NULL AS closed
      FROM saas_dialog_episodes WHERE conversation_id=%s""",(GROUP,))==[{'accepted_by':33,'operator_id':44,'closed':True}]
    audits=database.execute("""SELECT action,count(*) AS n FROM saas_tenant_audit_events
        WHERE action IN ('dialog_accept','dialog_close') AND entity_id=%s GROUP BY action""",(episode,))
    assert {r['action']:r['n'] for r in audits}=={'dialog_accept':1,'dialog_close':1}
    incoming(database,33,'LATE_OLD_COPY',happened=datetime.now(timezone.utc)-timedelta(hours=1))
    assert row(database,33)['state']=='closed'
    incoming(database,44,'NEXT_EPISODE')
    current=action(44,'accept',row(database,44)['revision'])
    assert current['lifecycle']['id']!=episode


@pytest.mark.parametrize('ai_scoped', [False, True])
def test_acceptance_is_visible_before_join_on_the_authorized_peer_source(collaboration, ai_scoped):
    database,_,_=collaboration
    if ai_scoped:
        database.execute("""UPDATE saas_tenant_workflow_policies
          SET value=jsonb_set(value,'{44,access_scopes,ai_agents}',%s::jsonb)
          WHERE tenant_id=%s AND policy_key='members'""",
          (json.dumps({'view':'none','update':'none'}),A))
        database.commit()
    peer_channel,peer_lead,_=SOURCES[44]
    first=history(44,include_operator_events=True)
    assert first['operator_events']==[]
    if ai_scoped:
        assert first['dialog']['ai_availability']=='restricted'
        assert 'group_assignment_preview' not in first['dialog']
    assert group_work.read_revision(profile(44),peer_channel,GROUP,peer_lead)=={
        'revision':first['dialog']['group_work_revision']}

    accepted=action(33,'accept')
    assert accepted['controls']['can_send']
    assert accepted['controls']['lifecycle']['group_work']['role']=='leader'
    refreshed=group_work.read_revision(profile(44),peer_channel,GROUP,peer_lead)
    assert refreshed['revision']!=first['dialog']['group_work_revision']
    second=history(44,include_operator_events=True,operator_events_revision=first['operator_events_revision'])
    assert second['dialog']['group_work_revision']==refreshed['revision']
    assert second['operator_events']==[accepted['operator_event']]
    assert second['dialog']['lifecycle']['group_work']['can_join']
    assert not second['dialog']['can_send']
    with pytest.raises(TenantLinearError):
        group_work.read_revision(profile(44),CA,GROUP,1001)


def test_initial_leader_can_leave_and_last_operator_completes(collaboration):
    database,_,_=collaboration
    episode=join(database)
    previous_peer_revision=history(44)['dialog']['group_work_revision']
    action(33,'leave_group',row(database,33)['revision'],expected_group_episode_id=episode)
    departed=card(33,lifecycle='completed')
    assert departed['lifecycle']['state']=='closed'
    assert not departed['can_send']
    assert not any(item['conversation_id']==GROUP for item in dialogs.listing(profile(33),lifecycle='active')['dialogs'])
    remaining=history(44,include_operator_events=True)
    assert remaining['dialog']['group_work_revision']!=previous_peer_revision
    assert remaining['dialog']['lifecycle']['can_close']
    assert remaining['dialog']['can_send']
    assert any(event['kind']=='transfer_group' for event in remaining['operator_events'])
    assert database.execute('SELECT operator_id,closed_at FROM saas_dialog_episodes WHERE id=%s',(episode,))==[
        {'operator_id':44,'closed_at':None}]
    database.commit()
    incoming(database,33,'BEFORE_LAST_CLOSE')
    assert card(33,lifecycle='completed')['lifecycle']['state']=='closed'
    with pytest.raises(TenantLinearError,match='последним участником'):
        action(44,'leave_group',row(database,44)['revision'],expected_group_episode_id=episode)
    database.commit()
    action(44,'close',row(database,44)['revision'],expected_group_episode_id=episode)
    assert card(33,lifecycle='completed')['lifecycle']['can_start']


def test_waiting_peer_can_start_after_the_group_closes(collaboration):
    database,membership,_=collaboration
    previous=action(33,'accept')['lifecycle']['id']
    action(33,'close',row(database,33)['revision'],expected_group_episode_id=previous)
    peer=history(44)['dialog']['lifecycle']
    assert peer['state']=='closed' and peer['can_start']
    assert not peer['can_accept']
    with pytest.raises(TenantLinearError,match='изменился'):
        action(44,'start',peer['revision'],expected_group_episode_id=str(uuid.uuid4()))
    membership[SOURCES[44][2]]=False
    with pytest.raises(TenantLinearError,match='WeCall не подтвердил'):
        action(44,'start',peer['revision'],expected_group_episode_id=previous)
    membership[SOURCES[44][2]]=True
    restarted=action(44,'start',peer['revision'],expected_group_episode_id=previous)
    assert restarted['lifecycle']['id']!=previous
    assert restarted['controls']['can_send']
    assert history(33,include_operator_events=True)['operator_events'][-1]==restarted['operator_event']


def test_simultaneous_first_accept_across_sources(collaboration):
    database,_,_=collaboration
    barrier=Barrier(2)
    def claim(actor):
        barrier.wait(timeout=5)
        try:
            return action(actor,'accept')
        except TenantLinearError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=[pool.submit(claim,actor) for actor in SOURCES]
        results=[first.result(timeout=10),second.result(timeout=10)]
    assert [r for r in results if isinstance(r,int)]==[409]
    assert database.execute("""SELECT count(*) AS n FROM saas_dialog_episodes
      WHERE conversation_id=%s AND closed_at IS NULL""",(GROUP,))[0]['n']==1
    assert database.execute("""SELECT count(*) AS n FROM saas_dialog_group_participants
      WHERE conversation_id=%s AND left_at IS NULL""",(GROUP,))[0]['n']==1


def test_existing_source_episode_finishes_before_shared_admission(collaboration,monkeypatch):
    database,_,_=collaboration
    # Create an accepted source-local episode as if it predated the release.
    with monkeypatch.context() as before_rollout:
        before_rollout.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_SEND','0')
        old=action(33,'accept')['lifecycle']['id']
    incumbent=history(33)['dialog']
    assert 'group_work' not in incumbent['lifecycle']
    assert incumbent['can_send'] and incumbent['lifecycle']['can_close']
    owner=dialogs.history(profile(11),'connect',SOURCES[33][1],CA,GROUP,owner_overview=True)['dialog']
    assert owner['owner_overview_read_only'] and not owner['lifecycle']['can_takeover']
    with pytest.raises(TenantLinearError,match='ранее принятые'):
        lifecycle.mutate(profile(11),{'provider':'connect','channel_id':CA,'conversation_id':GROUP,
            'lead_id':SOURCES[33][1],'request_id':str(uuid.uuid4()),'action':'takeover',
            'expected_revision':row(database,33)['revision']})
    waiting=history(44)['dialog']
    assert waiting['lifecycle']['can_accept'] is False
    assert 'group_work' not in waiting['lifecycle']
    with pytest.raises(TenantLinearError,match='ранее принятые'):
        action(44,'accept')
    assert action(33,'close',row(database,33)['revision'])['lifecycle']['state']=='closed'
    available=history(44)['dialog']
    assert available['lifecycle']['can_accept'] is True
    assert available['group_work_revision'] != waiting['group_work_revision']
    assert action(44,'accept')['group_changed']
    observer=dialogs.history(profile(11),'connect',SOURCES[44][1],CB,GROUP,owner_overview=True)['dialog']
    assert observer['owner_overview_read_only'] and not observer['lifecycle']['can_takeover']
    # A colleague on a different eligible source may now join (existing
    # archived routes retain their existing source-level access rules).
    assert history(33)['dialog']['lifecycle']['group_work']['can_join']
    assert database.execute("""SELECT group_leader_channel_id,closed_at IS NOT NULL AS closed
      FROM saas_dialog_episodes WHERE tenant_id=%s AND id=%s""",(A,old))==[
          {'group_leader_channel_id':None,'closed':True}]



def test_old_pilot_selector_cannot_exclude_a_native_group(collaboration, monkeypatch):
    monkeypatch.setenv('BEINTASKBOT_WHATSAPP_GROUP_COLLABORATION','{}')
    assert history(33)['dialog']['lifecycle']['group_available']
    assert action(33,'accept')['group_changed']


def test_group_with_a_legacy_sender_preserves_source_local_work(collaboration):
    database,membership,_=collaboration
    database.execute('DELETE FROM saas_whatsapp_native_routes WHERE tenant_id=%s AND channel_id=%s',(A,CB))
    database.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,status)
      VALUES(%s::uuid,'connect','connected') ON CONFLICT DO NOTHING""",(A,))
    database.commit()
    # No group membership probe should be needed to start either source's
    # preexisting local text workflow. Voice still checks at send time.
    membership[SOURCES[33][2]]=False
    assert not history(33)['dialog']['lifecycle'].get('group_available')
    first=action(33,'accept')
    other=action(44,'accept')
    assert not first.get('group_changed') and not other.get('group_changed')
    assert first['lifecycle']['id'] != other['lifecycle']['id']
    assert history(33)['dialog']['can_send'] and history(44)['dialog']['can_send']


def test_failed_membership_at_join_keeps_the_existing_leader(collaboration):
    database,membership,_=collaboration
    episode=action(33,'accept')['lifecycle']['id']
    membership[SOURCES[44][2]]=False
    with pytest.raises(TenantLinearError,match='WeCall не подтвердил'):
        action(44,'join_group',expected_group_episode_id=episode)
    assert row(database,44) is None
    assert card(33)['lifecycle']['group_work']['role']=='leader'
    assert card(44)['lifecycle']['group_work']['can_join']


def prepare(actor):
    command={'lead_id':SOURCES[actor][1],'request_id':str(uuid.uuid4()),'text':'Fixture reply'}
    item=sends.reserve(profile(actor),command)
    author,route=sends.mark_sending(item,{'origin':'connect'})
    return item,author,route


def dispatch(item, route):
    return sends.dispatch_guarded(item,route,transport.send,item['command']['text'],
        item['command']['request_id'],return_receipt=True)


def test_guarded_replies_show_real_sender_without_leaking_other_source(collaboration):
    database,_,delivered=collaboration
    join(database)
    for actor,other in ((33,44),(44,33)):
        item,author,route=prepare(actor)
        result=dispatch(item,route)
        transport.record_outbound(A,item['command']['lead_id'],route,item['command']['text'],result['message_id'],
            author=author['display_name'],actor=actor,receipt=result['receipt'])
        sends.finish(item,'accepted',result['message_id'])
        source,account,provider=delivered[-1]
        assert (source,account)==(SOURCES[actor][0],SOURCES[actor][2])
        incoming(database,other,provider,sender=LIDS[account])
    for actor in SOURCES:
        page=history(actor)
        projected=[(m['author_name'],m['sending_number']) for m in page['messages'] if m.get('sending_number')]
        assert set(projected)=={('Operator A','+994500000111'),('Operator B','+994500000112')}
        assert {s['channel_id'] for m in page['messages'] for s in m['sources']}=={SOURCES[actor][0]}
    assert database.execute("SELECT count(*) AS n FROM saas_dialog_episode_sends")[0]['n']==2


@pytest.mark.parametrize('failure',['membership','permissions','disconnected','presence','left_and_rejoined'])
def test_send_rechecks_at_dispatch(collaboration,failure):
    database,membership,delivered=collaboration
    episode=join(database)
    item,_,route=prepare(44)
    if failure=='membership':
        membership[SOURCES[44][2]]=False
    elif failure=='permissions':
        database.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s AND employee_id=44',(A,))
    elif failure=='disconnected':
        database.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s AND connect_channel_id=%s',(A,CB))
    elif failure=='presence':
        from tenant_dialog_presence import set_status
        set_status(profile(44),{'status':'lunch'})
    else:
        action(44,'leave_group',row(database,44)['revision'],expected_group_episode_id=episode)
        action(44,'join_group',row(database,44)['revision'],expected_group_episode_id=episode)
    database.commit()
    with pytest.raises(TenantLinearError):
        dispatch(item,route)
    assert not delivered


@pytest.mark.parametrize('fault,phase,receipt', [
    ('check_timeout','not_sent','blocked'),
    ('removed_during_dispatch','not_sent','blocked'),
    ('send_timeout','unknown','unknown'),
])
def test_group_reply_preserves_a_known_refusal_and_an_uncertain_send(collaboration, monkeypatch, fault, phase, receipt):
    from tenant_chat_send_policy import send_error_payload
    from tenant_chat_send_service import run

    database,_,delivered=collaboration
    join(database)
    original=requests.post
    attempts=[]

    def post(url, *, json, **options):
        assert json['scope']['source']==CB and json['scope']['account']==SOURCES[44][2]
        if url.endswith('/group-membership'):
            attempts.append('check')
            if fault=='check_timeout':
                raise requests.ReadTimeout()
            return original(url,json=json,**options)
        attempts.append('send')
        if fault=='removed_during_dispatch':
            return GatewayResponse(422,{'error':'native_group_membership_unconfirmed','confirmed':False,
                                        'delivery_phase':'not_sent'})
        raise requests.ReadTimeout()

    monkeypatch.setattr(requests,'post',post)
    command={'lead_id':SOURCES[44][1],'request_id':str(uuid.uuid4()),'text':'Fixture reply'}
    with pytest.raises(TenantLinearError) as rejected:
        asyncio.run(run(profile(44),command,None,None,logging.getLogger(__name__)))
    error=send_error_payload(rejected.value)
    assert error['delivery_phase']==phase and error['keep_request']==(phase=='unknown')
    assert attempts==(['check'] if fault=='check_timeout' else ['check','send'])
    assert not delivered
    assert database.execute("SELECT state FROM saas_chat_send_receipts WHERE request_id=%s",
                            (command['request_id'],))[0]['state']==receipt
    if phase=='unknown':
        with pytest.raises(TenantLinearError):
            asyncio.run(run(profile(44),{**command,'request_id':str(uuid.uuid4())},None,None,logging.getLogger(__name__)))
        assert attempts==['check','send']


def test_pair_count_and_actor_read_cursor(collaboration):
    database,_,_=collaboration
    owner=profile(11)
    def grouped():
        return next(r for r in dialogs.listing(owner)['dialogs'] if r['conversation_id']==GROUP)
    assert grouped()['unread_count']==0
    assert card(33)['unread_count']==0
    first=incoming(database,33,'UNREAD_BOTH')
    incoming(database,44,'UNREAD_BOTH')
    assert grouped()['unread_count']==1
    incoming(database,44,'UNREAD_DIFFERENT')
    assert grouped()['unread_count']==2
    dialogs.mark_read(owner,{'provider':'connect','channel_id':CA,'conversation_id':GROUP,'lead_id':1001,'message_id':first})
    assert grouped()['unread_count']==1
    assert card(33)['unread_count']==1
    assert len(history(33)['messages'])==2
    assert len(dialogs.history(owner,'connect',1001,CA,GROUP)['messages'])==3
