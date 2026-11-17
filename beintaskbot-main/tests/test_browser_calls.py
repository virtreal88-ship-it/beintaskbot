"""Real disposable PostgreSQL/WASM; synthetic signaling and audio only."""
import hashlib
import json
import uuid
import pytest
from test_call_recordings import pilot,bundle,PEER,CONVERSATION,ROUTE
from test_tenant_support_inbox import database,db,PROFILE,A,B,CA,CB
from tenant_linear_policy import TenantLinearError
import tenant_browser_calls as calls
import tenant_call_recordings as recordings

BOOT=str(uuid.UUID(int=601));OTHER_BOOT=str(uuid.UUID(int=602));GATEWAY=str(uuid.UUID(int=603))
SDP='v=0\r\n'+'x'*100

@pytest.fixture
def gateway(pilot,monkeypatch):
    db,key,public,old,send=pilot
    cfg={**old,'actors':[11],'gateway_id':GATEWAY,'token_sha256':hashlib.sha256(b'a'*40).hexdigest(),
         'routes':{PEER:{'conversation_id':CONVERSATION,'lead_id':41,'dial_jid':CONVERSATION},
                   CONVERSATION:{'conversation_id':CONVERSATION,'lead_id':41,'dial_jid':CONVERSATION}}}
    monkeypatch.setenv('BEINTASKBOT_BROWSER_CALL_PILOT',json.dumps(cfg));monkeypatch.setattr(calls,'_connect',lambda:db)
    calls.sync(cfg,snapshot())
    return db,key,cfg

def snapshot(seq=1,active=None,ended=None,results=None,boot=BOOT):
    return {'boot_id':boot,'seq':seq,'connected':True,'active':active or [],'ended':ended or [],'results':results or []}

def provider(state='ringing',peer=PEER,direction='inbound',id='provider-test-1'):
    return {'provider_id':id,'peer_jid':peer,'direction':direction,'state':state}

def command(op='start',**fields):
    return {**ROUTE,'op':op,'request_id':str(uuid.uuid4()),'sdp_offer':SDP,'consent_confirmed':True,**fields}

def test_start_idempotency_busy_and_explicit_termination(gateway):
    db,key,cfg=gateway;cmd=command();one=calls.action(PROFILE,cmd);two=calls.action(PROFILE,cmd)
    assert one['id']==two['id']
    with pytest.raises(TenantLinearError):calls.action(PROFILE,command())
    outgoing=calls.sync(cfg,snapshot(2))['commands'][0]
    ack={'id':outgoing['id'],'ok':True,'provider_id':'outgoing-test','sdp_answer':SDP}
    calls.sync(cfg,snapshot(3,active=[provider('connected',CONVERSATION,'outbound','outgoing-test')],results=[ack]))
    assert 'sdp_answer' not in calls.status(PROFILE)['calls'][0]
    assert calls.status(PROFILE,cmd['request_id'])['calls'][0]['sdp_answer']==SDP
    calls.action(PROFILE,command('end',call_id=one['id']))
    # Absence from a snapshot is deliberately not evidence of closing.
    calls.sync(cfg,snapshot(4));assert calls.status(PROFILE)['calls'][0]['state']=='ending'
    with pytest.raises(TenantLinearError):calls.action(PROFILE,command())
    calls.sync(cfg,snapshot(5,ended=[provider('ended',CONVERSATION,'outbound','outgoing-test')]))
    assert calls.status(PROFILE)['calls'][0]['state']=='ended'
    assert calls.action(PROFILE,command())['id']!=one['id']

def test_incoming_claim_recording_exact_route_and_replay(gateway):
    db,key,cfg=gateway
    calls.sync(cfg,snapshot(2,active=[provider()]))
    incoming=calls.status(PROFILE)['calls'][0];accept=command('accept',call_id=incoming['id'])
    calls.action(PROFILE,accept)
    with pytest.raises(TenantLinearError):calls.action(PROFILE,command('accept',call_id=incoming['id']))
    work=calls.sync(cfg,snapshot(3,active=[provider()]))['commands'][0]
    calls.sync(cfg,snapshot(4,ended=[provider('ended')],results=[{'id':work['id'],'ok':True,'provider_id':'provider-test-1','sdp_answer':SDP}]))
    raw,wav=bundle(key,call_id='provider-test-1')
    saved=calls.save_recording(cfg,incoming['id'],raw)
    assert calls.save_recording(cfg,incoming['id'],raw)['id']==saved['id']
    assert recordings.load(PROFILE,saved['id'])==wav
    assert calls.status(PROFILE)['calls'][0]['recording_state']=='ready'
    with pytest.raises(TenantLinearError):calls.save_recording(cfg,incoming['id'],bundle(key,call_id='another-test')[0])
    row=db.execute('SELECT direction,message_type FROM saas_crm_messages WHERE message_type=\'call_recording\'');db.commit()
    assert row==[{'direction':'system','message_type':'call_recording'}]

def test_tenant_source_member_consent_and_signature_boundaries(gateway):
    db,key,cfg=gateway
    for change in ({'channel_id':CB},{'lead_id':42},{'conversation_id':'another@s.whatsapp.net'},{'consent_confirmed':False}):
        with pytest.raises(TenantLinearError):calls.action(PROFILE,command(**change))
    with pytest.raises(TenantLinearError):calls.action({'tenant_id':B,'telegram_id':22},command())
    assert calls.status({'tenant_id':B,'telegram_id':22})=={'enabled':False}
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    with pytest.raises(TenantLinearError):calls.action(PROFILE,command())
    with pytest.raises(TenantLinearError):calls.gateway_auth('Bearer '+'b'*40)
    assert calls.gateway_auth('Bearer '+'a'*40)['gateway_id']==GATEWAY

def test_late_events_boot_restart_and_browser_loss_fail_closed(gateway):
    db,key,cfg=gateway;call=calls.action(PROFILE,command())
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    calls.sync(cfg,snapshot(3,active=[provider('connected',CONVERSATION,'outbound')],results=[{'id':work['id'],'ok':True,'provider_id':'provider-test-1','sdp_answer':SDP}]))
    db.execute("UPDATE saas_browser_calls SET browser_seen=now()-interval '20 seconds'");db.commit()
    ending=calls.sync(cfg,snapshot(4))['commands'];assert ending[0]['op']=='end'
    with pytest.raises(TenantLinearError):calls.sync(cfg,snapshot(5,boot=OTHER_BOOT))
    with pytest.raises(TenantLinearError):calls.sync(cfg,snapshot(2))
    calls.sync(cfg,snapshot(5,ended=[provider('ended',CONVERSATION,'outbound')]))
    newer=calls.action(PROFILE,command());assert newer['id']!=call['id']
    calls.sync(cfg,snapshot(6,ended=[provider('ended',CONVERSATION,'outbound')]))
    assert next(c for c in calls.status(PROFILE)['calls'] if c['id']==newer['id'])['state']=='starting'

def test_changed_route_and_revocation_terminate_owned_call(gateway):
    db,key,cfg=gateway;calls.action(PROFILE,command())
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
    result=calls.sync(cfg,snapshot(2));assert result['commands'][0]['cancelled'] is True
    db.execute('UPDATE saas_tenant_members SET active=TRUE WHERE tenant_id=%s::uuid',(A,));db.commit()
    db.execute("UPDATE saas_connect_conversations SET conversation_id='changed' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):calls.action(PROFILE,command())

def test_retry_not_started_does_not_place_second_call(gateway):
    db,key,cfg=gateway;cmd=command();one=calls.action(PROFILE,cmd)
    work=calls.sync(cfg,snapshot(2))['commands'][0]
    calls.sync(cfg,snapshot(3,results=[{'id':work['id'],'ok':False,'not_started':True,'error_code':'busy'}]))
    assert calls.action(PROFILE,cmd)['id']==one['id']
    assert calls.status(PROFILE)['calls'][0]['state']=='failed'
    assert calls.action(PROFILE,command())['id']!=one['id']


def test_cancel_before_delayed_start_and_private_recovery(gateway):
    db,key,cfg=gateway;cmd=command()
    assert calls.action(PROFILE,{**cmd,'op':'end'})['state']=='not_started'
    with pytest.raises(TenantLinearError):calls.action(PROFILE,cmd)
    later=command();one=calls.action(PROFILE,later)
    assert not calls.status(PROFILE)['calls'][0].get('request_matches')
    assert calls.status(PROFILE,later['request_id'],False)['calls'][0]['request_matches']
    db.execute("UPDATE saas_browser_calls SET browser_seen=now()-interval '30 seconds'");db.commit()
    calls.status(PROFILE,later['request_id'],False)
    assert calls.sync(cfg,snapshot(2))['commands'][0]['cancelled']


def test_api_origin_identity_and_gateway_credential_separation(gateway):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from unittest.mock import Mock
    import tenant_browser_calls_api as api
    async def run():
        app=web.Application();logger=Mock()
        app.router.add_route('*','/browser',api.handler(lambda r:PROFILE,'https://test.local',logger))
        app.router.add_post('/gateway',api.handler(lambda r:None,'https://test.local',logger,'sync'))
        q='?expected_tenant_id='+A+'&expected_user_id=11'
        async with TestClient(TestServer(app)) as client:
            response=await client.post('/browser'+q,json=command(),headers={'Origin':'https://evil.invalid'});assert response.status==403
            response=await client.get('/browser'+q.replace('user_id=11','user_id=22'));assert response.status==409
            response=await client.post('/gateway',json=snapshot(2));assert response.status==403
            response=await client.post('/gateway',json=snapshot(2),headers={'Authorization':'Bearer '+'a'*40,'Origin':'https://test.local'});assert response.status==403
            response=await client.post('/gateway',json=snapshot(2),headers={'Authorization':'Bearer '+'a'*40});assert response.status==200
            response=await client.post('/browser'+q,json=command(),headers={'Origin':'https://test.local'});assert response.status==200
            assert response.headers['Cache-Control']=='no-store';logger.warning.assert_not_called()
    asyncio.run(run())


def test_explicit_archived_owner_pilot_never_reopens_support_or_grants_employee_access(gateway,monkeypatch):
    from tenant_dialog_rollout import transition
    from tenant_dialog_lifecycle import migrate
    from test_tenant_operator_workspace import employee
    db,key,cfg=gateway
    with db.cursor() as cur:
        migrate(cur)
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    db.commit()
    with pytest.raises(TenantLinearError,match='Сначала примите'):calls.action(PROFILE,command())
    cfg={**cfg,'archived_test_route':True,'actors':[11,33]}
    monkeypatch.setenv('BEINTASKBOT_BROWSER_CALL_PILOT',json.dumps(cfg))
    person=employee(db,['dialogs'])
    with pytest.raises(TenantLinearError):calls.action(person,command())
    one=calls.action(PROFILE,command());assert one['state']=='starting'
    assert not db.execute('SELECT 1 FROM saas_dialog_workflows');db.commit()
    # Even the explicit archive binding cannot override another operator.
    db.execute("INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id,revision) VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'accepted',33,1)",(A,CA,CONVERSATION,str(uuid.uuid4())));db.commit()
    with pytest.raises(TenantLinearError):calls.action(PROFILE,command('end',call_id=one['id']))
    assert calls.status(PROFILE)['calls']==[]
    # Gateway independently stops media after the operator loses authorization.
    assert calls.sync(cfg,snapshot(2))['commands'][0]['cancelled']
