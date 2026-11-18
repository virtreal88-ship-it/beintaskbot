"""Synthetic audio and isolated PostgreSQL/WASM only; never a customer call."""
import io
import json
import hashlib
import struct
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
from tenant_linear_policy import TenantLinearError
import tenant_call_recordings as store
from tenant_call_recording_bundle import decode

PEER='987654321@lid'
CONVERSATION='999888777@s.whatsapp.net'
ROUTE={'channel_id':CA,'conversation_id':CONVERSATION,'lead_id':41}


def bundle(key, **changes):
    now=datetime.now(timezone.utc)
    frames=changes.pop('_frames',320)
    pcm=struct.pack('<hh',1000,-2000)*frames
    wav=struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',36+len(pcm),b'WAVE',b'fmt ',16,1,2,16000,64000,4,16,b'data',len(pcm))+pcm
    m={'version':1,'session_id':'test-session','call_id':'test-call','account_jid':'123456789@s.whatsapp.net',
       'peer_jid':PEER,'started_at':(now-timedelta(seconds=max(1,frames/16000))).isoformat(),'ended_at':now.isoformat(),
       'expires_at':(now+timedelta(hours=23)).isoformat(),'duration_ms':frames//16,'sample_rate':16000,
       'channels':['employee','caller'],'samples_received':[frames,frames],'non_silent_samples':[frames,frames],
       'gaps':[0,0],'complete':True,'end_code':'normal','sha256':hashlib.sha256(wav).hexdigest(),'consent_confirmed':True}
    m.update(changes);raw=json.dumps(m).encode()
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        z.writestr('manifest.json',raw);z.writestr('signature.ed25519',key.sign(raw));z.writestr('recording.wav',wav)
    return output.getvalue(),wav


@pytest.fixture
def pilot(db,monkeypatch):
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    cfg={'tenant_id':A,'actor_id':11,'channel_id':CA,'account_jid':'123456789@s.whatsapp.net',
         'expires_at':(datetime.now(timezone.utc)+timedelta(hours=23)).isoformat(),'public_key':public.hex(),
         'routes':{PEER:{'conversation_id':CONVERSATION,'lead_id':41}}}
    monkeypatch.setenv('BEINTASKBOT_CALL_RECORDING_PILOT',json.dumps(cfg))
    monkeypatch.setattr(store,'_connect',lambda:db)
    cipher=Fernet(Fernet.generate_key());monkeypatch.setattr(store,'_fernet',lambda:cipher)
    import tenant_connect_transport as transport
    send=Mock(side_effect=AssertionError('No external sends permitted'))
    monkeypatch.setattr(transport,'send_ai_reply',send)
    db.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s::uuid',(CONVERSATION,A));db.commit()
    return db,key,public,cfg,send


def test_signature_tracks_and_malformed_archives():
    key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    raw,wav=bundle(key);manifest,audio=decode(raw,public)
    assert audio==wav and manifest['channels']==['employee','caller']
    with pytest.raises(TenantLinearError):decode(raw,bytes(32))
    broken=bytearray(raw);position=raw.index(b'RIFF');broken[position+45]^=1
    with pytest.raises(TenantLinearError):decode(bytes(broken),public)
    for changes in ({'consent_confirmed':False},{'sample_rate':48000},{'duration_ms':120001},
                    {'channels':['caller','employee']},{'complete':True,'gaps':[1,0]},
                    {'non_silent_samples':[321,0]},{'account_jid':'wrong'},
                    {'expires_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}):
        with pytest.raises(TenantLinearError):decode(bundle(key,**changes)[0],public)
    extra=io.BytesIO(raw)
    with zipfile.ZipFile(extra,'a') as z:z.writestr('../secret',b'bad')
    with pytest.raises(TenantLinearError):decode(extra.getvalue(),public)


def test_thirty_day_retention_requires_managed_source_policy():
    key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    now=datetime.now(timezone.utc);start=now-timedelta(seconds=1)
    expiry=start+timedelta(days=30)
    raw,wav=bundle(key,started_at=start.isoformat(),ended_at=now.isoformat(),expires_at=expiry.isoformat())
    assert decode(raw,public,now=now,retention_hours=720)[1]==wav
    with pytest.raises(TenantLinearError):decode(raw,public,now=now)
    too_long,_=bundle(key,started_at=start.isoformat(),expires_at=(expiry+timedelta(seconds=1)).isoformat())
    with pytest.raises(TenantLinearError):decode(too_long,public,now=now,retention_hours=720)
    for retention in (0,True,721,8760):
        with pytest.raises(TenantLinearError):decode(raw,public,now=now,retention_hours=retention)


def test_import_is_encrypted_idempotent_internal_and_readable(pilot):
    db,key,public,cfg,send=pilot;raw,wav=bundle(key)
    first=store.save(PROFILE,ROUTE,raw);second=store.save(PROFILE,ROUTE,raw)
    assert first['id']==second['id'] and not first['duplicate'] and second['duplicate']
    assert store.load(PROFILE,first['id'])==wav
    row=db.execute('SELECT * FROM saas_test_call_recordings')[0];db.commit()
    assert b'RIFF' not in bytes(row['encrypted']) and row['actor_id']==11
    messages=db.execute("SELECT direction,message_type,media_url,raw FROM saas_crm_messages WHERE message_type='call_recording'");db.commit()
    assert len(messages)==1 and messages[0]['direction']=='system' and messages[0]['media_url']==''
    assert messages[0]['raw']['recording_id']==first['id'];send.assert_not_called()


def test_wrong_source_peer_account_signature_and_replay_conflict(pilot):
    db,key,public,cfg,send=pilot
    for changes in ({'peer_jid':'555666777@lid'},{'account_jid':'888777666@s.whatsapp.net'}):
        with pytest.raises(TenantLinearError):store.save(PROFILE,ROUTE,bundle(key,**changes)[0])
    for route in ({**ROUTE,'lead_id':42},{**ROUTE,'channel_id':CB},{**ROUTE,'conversation_id':'other'}):
        with pytest.raises(TenantLinearError):store.save(PROFILE,route,bundle(key)[0])
    with pytest.raises(TenantLinearError):store.save(PROFILE,ROUTE,bundle(Ed25519PrivateKey.generate())[0])
    raw,_=bundle(key);record=store.save(PROFILE,ROUTE,raw)
    with pytest.raises(TenantLinearError):store.save(PROFILE,ROUTE,bundle(key,complete=False,end_code='interrupted')[0])
    with pytest.raises(TenantLinearError):store.load({'tenant_id':B,'telegram_id':22},record['id'])
    assert len(db.execute('SELECT id FROM saas_test_call_recordings'))==1;db.commit();send.assert_not_called()


def test_revoked_member_and_source_and_expired_audio(pilot):
    db,key,public,cfg,send=pilot;record=store.save(PROFILE,ROUTE,bundle(key)[0])
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    with pytest.raises(TenantLinearError):store.load(PROFILE,record['id'])
    db.execute('UPDATE saas_tenant_members SET active=TRUE WHERE tenant_id=%s::uuid',(A,));db.commit()
    db.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    with pytest.raises(TenantLinearError):store.load(PROFILE,record['id'])
    db.execute('UPDATE saas_connect_channels SET active=TRUE WHERE tenant_id=%s::uuid',(A,));db.commit()
    db.execute("UPDATE saas_test_call_recordings SET expires_at=now()-interval '1 second'");db.commit()
    with pytest.raises(TenantLinearError) as error:store.load(PROFILE,record['id'])
    assert error.value.status==410
    store.purge();assert db.execute('SELECT encrypted FROM saas_test_call_recordings')[0]['encrypted'] is None;db.commit()


def test_disabled_pilot_and_nonowner_cannot_import(pilot,monkeypatch):
    db,key,public,cfg,send=pilot
    db.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):store.save(PROFILE,ROUTE,bundle(key)[0])
    monkeypatch.delenv('BEINTASKBOT_CALL_RECORDING_PILOT')
    with pytest.raises(TenantLinearError):store.capability(PROFILE,ROUTE)


def test_explicit_archive_binding_accepts_only_the_one_approved_recording(pilot,monkeypatch):
    db,key,public,cfg,send=pilot;raw,wav=bundle(key)
    db.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    with pytest.raises(TenantLinearError):store.save(PROFILE,ROUTE,raw)
    cfg.update(source_mode='archived_owner_binding',archive_call={'call_id':'test-call','sha256':hashlib.sha256(wav).hexdigest()})
    monkeypatch.setenv('BEINTASKBOT_CALL_RECORDING_PILOT',json.dumps(cfg))
    first=store.save(PROFILE,ROUTE,raw)
    assert store.load(PROFILE,first['id'])==wav
    with pytest.raises(TenantLinearError):store.save(PROFILE,ROUTE,bundle(key,call_id='another-call')[0])
    send.assert_not_called()


def test_http_origin_identity_bounds_range_and_expiry(pilot,monkeypatch):
    import asyncio
    from urllib.parse import urlencode
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_call_recordings_api as api
    from tenant_chat_attachment_api import REQUESTS
    REQUESTS.clear()
    db,key,public,cfg,send=pilot;raw,wav=bundle(key)
    async def run():
        app=web.Application();logger=Mock()
        app.router.add_route('*','/recordings',api.handler(lambda r:PROFILE,'https://test.local',logger))
        app.router.add_get('/recordings/{record_id}',api.handler(lambda r:PROFILE,'https://test.local',logger,'audio'))
        query=urlencode({**ROUTE,'expected_tenant_id':A,'expected_user_id':11})
        async with TestClient(TestServer(app)) as client:
            bad=await client.post('/recordings?'+query,data=raw,headers={'Content-Type':'application/zip','Origin':'https://evil.invalid'})
            assert bad.status==403
            changed=await client.get('/recordings?'+query.replace('expected_user_id=11','expected_user_id=22'))
            assert changed.status==409
            saved=await client.post('/recordings?'+query,data=raw,headers={'Content-Type':'application/zip','Origin':'https://test.local'})
            assert saved.status==200,await saved.text()
            record=(await saved.json())['result']['id']
            playback=await client.get('/recordings/'+record+'?'+query,headers={'Range':'bytes=44-47'})
            assert playback.status==206 and await playback.read()==wav[44:48]
            assert playback.headers['Cache-Control']=='no-store'
            assert playback.headers['Content-Range']==f'bytes 44-47/{len(wav)}'
            bad_range=await client.get('/recordings/'+record+'?'+query,headers={'Range':'bytes=999999-'})
            assert bad_range.status==416
            db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
            revoked=await client.get('/recordings/'+record+'?'+query)
            assert revoked.status==403
            logger.warning.assert_not_called();send.assert_not_called()
    asyncio.run(run())


def test_employee_reads_only_granted_source_and_loses_access_after_revocation(pilot,monkeypatch):
    from test_tenant_operator_workspace import employee
    from test_dialog_source_scopes import grants
    db,key,public,cfg,send=pilot;raw,wav=bundle(key);record=store.save(PROFILE,ROUTE,raw)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    person=employee(db,['dialogs']);grants(db,33,view='self',update='self')
    with pytest.raises(TenantLinearError):store.load(person,record['id'])
    db.execute('INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by) VALUES(%s::uuid,%s::uuid,33,11)',(A,CA));db.commit()
    assert store.load(person,record['id'])==wav
    with pytest.raises(TenantLinearError):store.save(person,ROUTE,raw)
    db.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND source_id=%s::uuid',(A,CA));db.commit()
    with pytest.raises(TenantLinearError):store.load(person,record['id'])


def test_storage_limit_and_internal_history_projection(pilot,monkeypatch):
    db,key,public,cfg,send=pilot
    import tenant_operator_dialogs as dialogs
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    first=store.save(PROFILE,ROUTE,bundle(key)[0])
    page=dialogs.history(PROFILE,'connect',41,CA,CONVERSATION)
    found=[m for m in page['messages'] if m['message_type']=='call_recording']
    assert len(found)==1 and found[0]['call_recording']['id']==first['id']
    assert found[0]['author_type']=='system' and 'raw' not in found[0]
    assert page['dialog']['test_recording_import_allowed'] is True
    for n in range(1,20):store.save(PROFILE,ROUTE,bundle(key,call_id=f'call-{n}')[0])
    with pytest.raises(TenantLinearError) as error:store.save(PROFILE,ROUTE,bundle(key,call_id='over-limit')[0])
    assert error.value.status==429
    assert db.execute('SELECT count(*) AS n FROM saas_test_call_recordings')[0]['n']==20;db.commit()


def test_managed_recording_history_preserves_duration_above_two_minutes():
    import uuid
    from tenant_operator_dialogs import _message
    base={'direction':'system','message_type':'call_recording','raw':{'recording_id':str(uuid.uuid4()),'duration_ms':216645,'test_recording':False}}
    assert _message(base)['call_recording']['duration_ms']==216645
    assert _message({**base,'raw':{**base['raw'],'duration_ms':999999}})['call_recording']['duration_ms']==900000
    assert _message({**base,'raw':{**base['raw'],'test_recording':True}})['call_recording']['duration_ms']==120000


def test_route_changed_after_authorization_is_not_silently_rebound(pilot,monkeypatch):
    db,key,public,cfg,send=pilot
    original=store.assert_route
    def changed(cur,*args,**kwargs):
        route=original(cur,*args,**kwargs)
        cur.execute('UPDATE saas_connect_conversations SET lead_id=999 WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA))
        return route
    monkeypatch.setattr(store,'assert_route',changed)
    with pytest.raises(TenantLinearError) as error:store.save(PROFILE,ROUTE,bundle(key)[0])
    assert error.value.status==409
    assert not db.execute('SELECT id FROM saas_test_call_recordings');db.commit()


def test_full_fifteen_minute_bundle_requires_trusted_duration_and_encodes():
    import shutil, subprocess, time
    from tenant_call_audio_storage import compress
    key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    raw,wav=bundle(key,_frames=16000*900)
    with pytest.raises(TenantLinearError):decode(raw,public)
    started=time.monotonic()
    manifest,audio=decode(raw,public,max_seconds=900,retention_hours=720)
    assert manifest['duration_ms']==900000 and audio==wav
    encoded=compress(audio)
    assert encoded.startswith(b'OggS') and len(encoded)<8*1024*1024
    result=subprocess.run([shutil.which('ffmpeg'),'-v','error','-nostdin','-i','pipe:0','-map','0:a:0',
        '-f','s16le','-acodec','pcm_s16le','-ac','2','-ar','16000','pipe:1'],input=encoded,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60,check=True)
    assert len(result.stdout)==900*16000*4
    print({'synthetic_duration_ms':900000,'signed_bundle_bytes':len(raw),'stored_opus_bytes':len(encoded),
           'validation_encode_decode_seconds':round(time.monotonic()-started,2)})
    for invalid in (True,0,901,3600):
        with pytest.raises(TenantLinearError):decode(raw,public,max_seconds=invalid)


def test_duration_constraint_upgrade_is_idempotent(pilot):
    db,key,public,cfg,send=pilot
    db.execute('ALTER TABLE saas_test_call_recordings DROP CONSTRAINT saas_test_call_recordings_duration_ms_check')
    db.execute('ALTER TABLE saas_test_call_recordings ADD CONSTRAINT saas_test_call_recordings_duration_ms_check CHECK(duration_ms BETWEEN 1 AND 120000)');db.commit()
    with db.cursor() as cur:store.migrate(cur);store.migrate(cur)
    db.commit()
    definition=db.execute("SELECT pg_get_constraintdef(oid) AS rule FROM pg_constraint WHERE conname='saas_test_call_recordings_duration_ms_check'")[0]['rule'];db.commit()
    assert '900000' in definition and '120000' not in definition
