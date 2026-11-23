import asyncio
import copy
import json
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
import tenant_contact_phone as phone
import tenant_call_sources as sources
from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError
from test_whatsapp_source_control import active
from test_whatsapp_integration import setup
from test_whatsapp_native_rollout import staged_source
from test_call_sources import managed
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE

LID='111111111111111@lid'
PN='994100000002@s.whatsapp.net'


@pytest.mark.parametrize('fault',['none','scope','peer','phone','oversize','html','missing','technical_name','generic_name'])
def test_device_proof_is_exact_scoped_bounded_and_display_only(monkeypatch,fault):
    scope={'tenant':'t','source':'s','account':'994100000001@s.whatsapp.net','generation':'g'}
    route={'conversation_id':LID,'native':{'scope':scope,'gateway_id':'synthetic'}}
    async def run():
        async def serve(request):
            assert request.headers['Authorization']=='Bearer synthetic'
            assert await request.json()=={'scope':scope,'chat':LID}
            data={'scope':copy.deepcopy(scope),'peer_jid':LID,'caller':{'phone_jid':PN,'display_name':'Synthetic'}}
            if fault=='scope':data['scope']['source']='other'
            if fault=='peer':data['peer_jid']='222222222222222@lid'
            if fault=='phone':data['caller']['phone_jid']=LID
            if fault=='technical_name':data['caller']['display_name']=LID
            if fault=='generic_name':data['caller']['display_name']='Контакт WhatsApp'
            body=json.dumps(data).encode()
            if fault=='oversize':body+=b' '*5000
            if fault=='html':body=b'<html>error</html>'
            return web.Response(body=body,status=404 if fault=='missing' else 200)
        app=web.Application();app.router.add_post('/v1/sources/synthetic/messages/contact',serve)
        async with TestServer(app) as server:
            monkeypatch.setattr(sources,'service',lambda:(str(server.make_url('')).rstrip('/'),'synthetic'))
            details = await phone.fetch_contact(route)
            if fault in ('none', 'technical_name', 'generic_name'):
                assert details == {'phone': '+' + PN.split('@')[0],
                                   'display_name': 'Synthetic' if fault == 'none' else ''}
            else:
                assert details == {}
    asyncio.run(run())


def prepare(active,monkeypatch):
    db,source,row,good,provider=active
    db.execute('UPDATE saas_connect_conversations SET connect_channel_id=%s,conversation_id=%s,phone=%s WHERE tenant_id=%s',(source,LID,'',A));db.commit()
    from tenant_connect_transport import _upsert_conversation
    with db.cursor() as cur:_upsert_conversation(cur,A,source,PN,{'kind':'contact','display_name':'Separate old history'})
    db.commit();monkeypatch.setattr(phone,'_connect',lambda:db)
    return db,source,{'provider':'connect','channel_id':source,'conversation_id':LID,'lead_id':41}


def test_two_histories_get_display_proof_without_writing_any_identity(active,monkeypatch):
    db,source,params=prepare(active,monkeypatch)
    tables=('saas_connect_conversations','saas_connect_direct_identities','saas_crm_messages','saas_dialog_source_members')
    before={t:db.execute('SELECT * FROM '+t) for t in tables};db.commit()
    seen=[]
    async def fetch(route):seen.append(route);return {'phone':'+'+PN.split('@')[0],'display_name':'Verified name'}
    monkeypatch.setattr(phone,'fetch_contact',fetch)
    result=asyncio.run(phone.get_phone(PROFILE,params))
    current=seen[0]['native']
    revision=phone.contact_source_revision(current['gateway_id'],current['scope']['account'],current['scope']['generation'])
    assert result.pop('contact_source_revision')==revision
    with pytest.raises(TenantLinearError) as wrong_revision:
        asyncio.run(phone.get_phone(PROFILE,{**params,'contact_source_revision':'previous device'}))
    assert wrong_revision.value.status==409
    assert result=={**params,'status':'available','display_only':True,'phone':'+994100000002','display_name':'Verified name'}
    assert {t:db.execute('SELECT * FROM '+t) for t in tables}==before;db.commit()
    for wrong in ({**params,'channel_id':CB},{**params,'lead_id':98765},{**params,'conversation_id':'123456@g.us'}):
        with pytest.raises(TenantLinearError):asyncio.run(phone.get_phone(PROFILE,wrong))
    with pytest.raises(TenantLinearError):asyncio.run(phone.get_phone({**PROFILE,'tenant_id':B},params))
    assert len(seen)==1


@pytest.mark.parametrize('change',['member','source','gateway'])
def test_permissions_and_generation_rechecked_after_lookup(active,monkeypatch,change):
    db,source,params=prepare(active,monkeypatch)
    async def fetch(route):
        if change=='member':db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s',(A,))
        elif change=='source':db.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s AND connect_channel_id=%s',(A,source))
        else:db.execute('UPDATE saas_browser_call_sources SET enabled=FALSE WHERE tenant_id=%s AND channel_id=%s',(A,source))
        db.commit();return {'phone':'+994100000002','display_name':'Verified name'}
    monkeypatch.setattr(phone,'fetch_contact',fetch)
    with pytest.raises((TenantLinearError,TenantPlatformError)):asyncio.run(phone.get_phone(PROFILE,params))


def test_replaced_generation_does_not_return_old_proof(active,monkeypatch):
    db,source,params=prepare(active,monkeypatch)
    original=phone.authorized_route
    async def fetch(route):
        def changed(profile,params):
            current=original(profile,params)
            current['native']['scope']['generation']='next-device-generation'
            return current
        monkeypatch.setattr(phone,'authorized_route',changed)
        return {'phone':'+994100000002','display_name':'Verified name'}
    monkeypatch.setattr(phone,'fetch_contact',fetch)
    with pytest.raises(TenantLinearError) as error:asyncio.run(phone.get_phone(PROFILE,params))
    assert error.value.status==409



def test_list_lookup_generation_is_exact_and_not_advertised_on_archive_or_conflict(active,monkeypatch):
    db,source,params=prepare(active,monkeypatch)
    current=phone.authorized_route(PROFILE,params)['native']
    revision=phone.contact_source_revision(current['gateway_id'],current['scope']['account'],current['scope']['generation'])
    rows=[dict(params),{**params,'archive_read_only':True},{**params,'contact_identity_conflict':True},
          {**params,'channel_id':CB},{**params,'conversation_id':'123456@g.us','customer_kind':'group'}]
    with db.cursor() as cur:phone.attach_list_source_revisions(cur,A,rows)
    assert rows[0]['contact_source_revision']==revision
    assert all('contact_source_revision' not in row for row in rows[1:])
