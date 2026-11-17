"""Permanent-source SQL/retention tests; synthetic PCM, no provider calls."""
import hashlib
import uuid
from datetime import datetime,timedelta,timezone
from unittest.mock import Mock
import pytest
from test_call_recordings import pilot,bundle,CONVERSATION,ROUTE
from test_browser_calls import gateway,snapshot,command,provider,BOOT
from test_tenant_support_inbox import database,db,PROFILE,A,B,CA,CB
from tenant_linear_policy import TenantLinearError
import tenant_call_sources as sources
import tenant_browser_calls as calls
import tenant_call_recordings as recordings

MANAGED=str(uuid.UUID(int=605));TOKEN='z'*48


@pytest.fixture
def managed(gateway,monkeypatch):
    db,key,legacy=gateway
    monkeypatch.setenv('BEINTASKBOT_CALLS_SERVICE_URL','https://calls.invalid')
    monkeypatch.setenv('BEINTASKBOT_CALLS_SERVICE_TOKEN','x'*48)
    monkeypatch.setattr(sources,'_connect',lambda:db)
    import tenant_platform
    monkeypatch.setattr(tenant_platform,'_connect',lambda:db)
    monkeypatch.setattr(sources,'confirmed_account',Mock(return_value=legacy['account_jid']))
    monkeypatch.setattr(sources,'remote',Mock(side_effect=AssertionError('No provider traffic in SQL tests')))
    db.execute('''INSERT INTO saas_browser_call_sources(tenant_id,channel_id,gateway_id,account_jid,
      public_key,token_sha256,encrypted_token,created_by,state) VALUES(%s,%s,%s,%s,%s,%s,%s,11,'ready')''',
      (A,CA,MANAGED,legacy['account_jid'],legacy['public_key'],hashlib.sha256(TOKEN.encode()).hexdigest(),b'not-a-real-secret'))
    db.commit()
    cfg=sources.configs(PROFILE,channel=CA)[0];calls.sync(cfg,snapshot())
    return db,key,cfg


def test_source_replaces_pilot_without_expiry_or_cross_source_route(managed):
    db,key,cfg=managed
    assert cfg['managed'] and cfg['retention_hours']==720 and 'expires_at' not in cfg and 'routes' not in cfg
    assert len(calls.configurations(PROFILE))==1
    assert calls.gateway_auth('Bearer '+TOKEN)['gateway_id']==MANAGED
    assert calls.offer(PROFILE,ROUTE)
    with pytest.raises(TenantLinearError):calls.action(PROFILE,command(channel_id=CB,media='websocket'))
    with pytest.raises(TenantLinearError):calls.action({'tenant_id':B,'telegram_id':22},command(media='websocket'))
    started=calls.action(PROFILE,command(media='websocket'))
    row=db.execute('SELECT gateway_id,channel_id,peer_jid FROM saas_browser_calls WHERE id=%s',(started['id'],))[0];db.commit()
    assert str(row['gateway_id'])==MANAGED and str(row['channel_id'])==CA and row['peer_jid']==CONVERSATION
    source=calls.status(PROFILE)['sources'][0]
    assert source['number']==cfg['account_jid'].split('@')[0] and source['retention_hours']==720 and source['media']=='websocket'


def test_actual_connect_account_must_match_before_dial(managed,monkeypatch):
    db,key,cfg=managed
    monkeypatch.setattr(sources,'confirmed_account',lambda *a:'111111111@s.whatsapp.net')
    with pytest.raises(TenantLinearError):calls.action(PROFILE,command(media='websocket'))
    assert db.execute('SELECT id FROM saas_browser_calls')==[];db.commit()


def test_managed_audio_nonce_actor_source_and_closure_checks(managed):
    db,key,cfg=managed;cmd=command(media='websocket');started=calls.action(PROFILE,cmd)
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    assert work['payload']['media']=='websocket'
    calls.sync(cfg,snapshot(3,results=[{'id':work['id'],'ok':True,'provider_id':'provider-managed'}]))
    assert sources.audio_authority(PROFILE,started['id'],cmd['request_id'])==(MANAGED,'provider-managed',started['id'])
    with pytest.raises(TenantLinearError):sources.audio_authority(PROFILE,started['id'],str(uuid.uuid4()))
    with pytest.raises(TenantLinearError):sources.audio_authority({'tenant_id':B,'telegram_id':22},started['id'],cmd['request_id'])
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s',(A,));db.commit()
    with pytest.raises(TenantLinearError):sources.audio_authority(PROFILE,started['id'],cmd['request_id'])
    db.execute('UPDATE saas_tenant_members SET active=TRUE WHERE tenant_id=%s',(A,));db.commit()
    calls.sync(cfg,snapshot(4,ended=[provider('ended',CONVERSATION,'outbound','provider-managed')]))
    with pytest.raises(TenantLinearError):sources.audio_authority(PROFILE,started['id'],cmd['request_id'])


def test_recording_kept_thirty_days_independent_of_pairing_then_purged(managed):
    db,key,cfg=managed;cmd=command(media='websocket');started=calls.action(PROFILE,cmd)
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    calls.sync(cfg,snapshot(3,results=[{'id':work['id'],'ok':True,'provider_id':'provider-managed'}],ended=[provider('ended',CONVERSATION,'outbound','provider-managed')]))
    now=datetime.now(timezone.utc);start=now-timedelta(seconds=1)
    raw,wav=bundle(key,call_id='provider-managed',peer_jid=CONVERSATION,started_at=start.isoformat(),ended_at=now.isoformat(),expires_at=(start+timedelta(days=30)).isoformat())
    saved=calls.save_recording(cfg,started['id'],raw)
    db.execute("UPDATE saas_browser_call_sources SET state='offline',enabled=FALSE");db.commit()
    audio,media_type=recordings.load(PROFILE,saved['id'],with_type=True)
    assert audio.startswith(b'OggS') and media_type=='audio/ogg'
    row=db.execute('SELECT expires_at,happened_at FROM saas_test_call_recordings WHERE id=%s',(saved['id'],))[0];db.commit()
    assert datetime.fromisoformat(row['expires_at'])-datetime.fromisoformat(row['happened_at'])==timedelta(days=30)
    meta=db.execute("SELECT raw FROM saas_crm_messages WHERE message_type='call_recording'")[0]['raw'];db.commit()
    assert meta['test_recording'] is False
    recordings.purge();assert recordings.load(PROFILE,saved['id'])==audio
    db.execute("UPDATE saas_test_call_recordings SET expires_at=now()-interval '1 second'");db.commit()
    recordings.purge()
    assert db.execute('SELECT encrypted FROM saas_test_call_recordings')[0]['encrypted'] is None
    assert db.execute("SELECT id FROM saas_crm_messages WHERE message_type='call_recording'");db.commit()


def test_incoming_new_exact_jid_stays_in_its_source(managed):
    db,key,cfg=managed;peer='888444222@s.whatsapp.net'
    calls.sync(cfg,snapshot(2,active=[provider(peer=peer)]))
    row=db.execute('SELECT channel_id,conversation_id,peer_jid FROM saas_browser_calls')[0];db.commit()
    assert str(row['channel_id'])==CA and row['conversation_id']==peer and row['peer_jid']==peer
    calls.sync(cfg,snapshot(3,active=[provider(peer=peer)]))
    assert len(db.execute('SELECT id FROM saas_browser_calls'))==1;db.commit()


def test_same_boot_never_delivers_or_acknowledges_other_source_command(managed):
    db,key,cfg=managed
    started=calls.action(PROFILE,command(media='websocket'))
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    other=calls.config()  # separately authenticated legacy gateway, same tenant and boot
    assert other['gateway_id']!=cfg['gateway_id']
    assert calls.sync(other,snapshot(2))['commands']==[]
    calls.sync(other,snapshot(3,results=[{'id':work['id'],'ok':True,'provider_id':'foreign-provider'}]))
    row=db.execute('SELECT provider_id FROM saas_browser_calls WHERE id=%s',(started['id'],))[0];db.commit()
    assert row['provider_id'] is None
    assert calls.sync(cfg,snapshot(3))['commands'][0]['id']==work['id']


def test_reject_unclaimed_call_without_accepting_dialog_and_accept_uses_episode(managed):
    import tenant_dialog_lifecycle as lifecycle
    from tenant_dialog_rollout import transition
    db,key,cfg=managed
    with db.cursor() as cur:
        lifecycle.migrate(cur);transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    db.commit()
    calls.sync(cfg,snapshot(2,active=[provider(peer=CONVERSATION)]))
    incoming=calls.status(PROFILE)['calls'][0]
    rejected=calls.action(PROFILE,command('reject',call_id=incoming['id']))
    assert rejected['state']=='ending'
    assert db.execute('SELECT id FROM saas_dialog_workflows')==[];db.commit()
    calls.sync(cfg,snapshot(3,ended=[provider('ended',CONVERSATION)]))
    calls.sync(cfg,snapshot(4,active=[provider(peer=CONVERSATION,id='next-incoming')]))
    incoming=next(c for c in calls.status(PROFILE)['calls'] if c['state']=='incoming')
    accepted=calls.action(PROFILE,command('accept',call_id=incoming['id'],media='websocket'))
    assert accepted['state']=='starting'
    row=db.execute('SELECT state,operator_id FROM saas_dialog_workflows')[0];db.commit()
    assert row=={'state':'accepted','operator_id':11}


def test_gateway_database_auth_is_off_loop_and_bounded(monkeypatch):
    import asyncio,threading
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_browser_calls_api as api
    gate=threading.Event();started=threading.Event();threads=[];main=threading.get_ident()
    def slow_auth(_):
        threads.append(threading.get_ident())
        if len(threads)==4:started.set()
        assert gate.wait(3)
        return {}
    monkeypatch.setattr(calls,'gateway_auth',slow_auth)
    monkeypatch.setattr(calls,'sync',lambda *_:{})
    async def run():
        monkeypatch.setattr(api,'SLOTS',asyncio.Semaphore(4))
        app=web.Application();app.router.add_post('/sync',api.handler(lambda _:None,'https://example.invalid',Mock(),'sync'))
        async with TestClient(TestServer(app)) as client:
            tasks=[asyncio.create_task(client.post('/sync',headers={'Authorization':'Bearer '+'a'*40},json={})) for _ in range(4)]
            try:
                assert await asyncio.to_thread(started.wait,2)
                response=await client.post('/sync',headers={'Authorization':'Bearer '+'a'*40},json={})
                assert response.status==429
            finally:gate.set()
            assert all(r.status==200 for r in await asyncio.gather(*tasks))
        assert len(threads)==4 and all(t!=main for t in threads)
    asyncio.run(run())


def test_managed_restart_requires_closed_process_proof_and_fences_late_boot(managed):
    from test_browser_calls import OTHER_BOOT
    db,key,cfg=managed;started=calls.action(PROFILE,command(media='websocket'))
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    calls.sync(cfg,snapshot(3,results=[{'id':work['id'],'ok':True,'provider_id':'provider-managed'}]))
    with pytest.raises(TenantLinearError,match='previous_process_unconfirmed'):
        calls.sync(cfg,snapshot(1,boot=OTHER_BOOT))
    changed={**snapshot(1,boot=OTHER_BOOT),'closed_process_boots':[BOOT]}
    reply=calls.sync(cfg,changed)
    row=db.execute('SELECT state,recording_state FROM saas_browser_calls WHERE id=%s',(started['id'],))[0];db.commit()
    assert row=={'state':'ended','recording_state':'unconfirmed'}
    assert reply['recordings'][0]['call_id']==started['id']
    # Missing snapshots, elapsed time or a late request cannot resurrect the old owner.
    db.execute("UPDATE saas_browser_call_gateways SET last_seen=now()-interval '1 day' WHERE gateway_id=%s",(cfg['gateway_id'],));db.commit()
    with pytest.raises(TenantLinearError,match='retired_process'):calls.sync(cfg,snapshot(100))
    calls.sync(cfg,{**changed,'seq':2})
    new=calls.action(PROFILE,command(media='websocket'))
    assert new['id']!=started['id']
    with pytest.raises(TenantLinearError,match='retired_process'):
        calls.sync(cfg,snapshot(101,ended=[provider('ended',CONVERSATION,'outbound','provider-managed')]))
    assert db.execute('SELECT state FROM saas_browser_calls WHERE id=%s',(new['id'],))[0]['state']=='starting';db.commit()
