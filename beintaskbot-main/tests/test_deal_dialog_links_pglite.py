"""Test a synthetic TikTok deal and first phone send through the real CRM paths."""
import asyncio
import json
import logging
import uuid

import pytest

import tenant_deal_dialog_links as links
import tenant_deal_dialogs
import tenant_operator_dialogs
from tenant_crm_sync_schema import ensure_sync_schema
from tenant_crm_sync_store import connection_id
from tenant_crm_relation_contract import BoundIdentity, RecordRef
from tenant_crm_relation_identity import bind_identity, current_binding
from tenant_kommo_board_provenance import digest
from tenant_linear_policy import TenantLinearError
from tenant_whatsapp_native_consumer import consume
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_call_sources import managed
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db
from test_whatsapp_native_consumer import activate, ready
from test_whatsapp_native_inbox import capture, delivery, store
from test_whatsapp_native_projection import message
from test_whatsapp_native_rollout import staged_source
from test_whatsapp_source_control import active
from test_whatsapp_integration import setup

PHONE = '15550000003'
JID = PHONE+'@s.whatsapp.net'
LID = '18887776660@lid'
TEXT = 'Synthetic follow-up sent from the phone'
LEAD = '917'
CONTACT = str(uuid.UUID(int=515))


def kommo_deal(db, lead=LEAD, contact=312, stage=25, name='Test TikTok sale'):
    raw = {'id':int(lead),'pipeline_id':18,'status_id':stage,
           '_embedded':{'contacts':[{'id':contact,'is_main':True}]}}
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,pipeline_id,status_id,name,raw)
            VALUES(%s::uuid,%s,18,%s,%s,%s::jsonb)
        """, (A, int(lead), stage, name, json.dumps(raw)))
        binding = current_binding(cur, A)
        cur.execute("""
            INSERT INTO saas_crm_board_provenance
              (tenant_id,resource,external_id,account_id,connection_generation,raw_digest,source_revision)
            VALUES(%s::uuid,'leads',%s,%s,%s,%s,'fixture')
        """, (A, int(lead), binding.account_id, binding.connection_generation, digest(raw)))
    db.commit()


def phone_event(reference, *, provider_id='FIRST_PHONE_MESSAGE', chat=LID, alternate=JID):
    publication = delivery(after=2)
    publication['event'] = {'sequence':3,'id':'f'*64,'payload':message(
        chat=chat,from_me=True,sender='123456789@s.whatsapp.net',sender_alt='',
        recipient_alt=alternate,provider_id=provider_id,text=TEXT)}
    store(publication)
    return (*reference[:3], publication['event']['id'])


@pytest.fixture
def sale(ready, monkeypatch):
    db, reference = ready
    activate(db)
    monkeypatch.setenv('CRM_DEAL_DIALOG_LINK_TENANTS', A)
    for module in (links, tenant_deal_dialogs, tenant_operator_dialogs):
        monkeypatch.setattr(module, '_connect', lambda: db)
    import tenant_crm_sync_schema as sync_schema
    monkeypatch.setattr(sync_schema, '_ready', False)
    ensure_sync_schema(db)
    from tenant_chat_send_schema import ensure_schema as ensure_send_schema
    ensure_send_schema(db)
    from tenant_dialog_lifecycle import migrate
    from tenant_dialog_rollout import transition
    with db.cursor() as cur:
        cur.execute("DELETE FROM saas_crm_board_provenance WHERE tenant_id=%s::uuid", (A,))
        migrate(cur)
        transition(cur, A, mode='active', admission_enabled=True, expected_generation=0)
        cur.execute("""
            INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,connected_at)
            VALUES(%s::uuid,'kommo','connected','tiktok-fixture.kommo.com',now())
        """, (A,))
        cur.execute("""
            INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone)
            VALUES(%s::uuid,%s::uuid,'Test buyer',%s)
        """, (CONTACT,A,PHONE))
        # Independent, confirmed provider identities for this test customer.
        bind_identity(cur, BoundIdentity(RecordRef('contact', CONTACT), '312', current_binding(cur, A)),
                      provenance='owner_verified', verified_by=11, evidence_id='synthetic-test-contact')
        cur.execute("""
            INSERT INTO saas_crm_sync_jobs
              (tenant_id,resource,run_id,connection_id,status,phase,finished_at,last_full_sync)
            VALUES(%s::uuid,'leads',%s::uuid,%s,'done','deleted',now(),1)
        """, (A,str(uuid.uuid4()),connection_id(cur,A)))
    db.commit()
    kommo_deal(db)
    db.execute("""
        INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
        VALUES(%s::uuid,%s::uuid,%s,'TIKTOK_TEST_MESSAGE','TikTok','incoming','Test TikTok enquiry')
    """, (str(uuid.uuid4()),A,int(LEAD)))
    db.commit()
    return db, reference


def confirm_whatsapp(db):
    db.execute("""
        INSERT INTO saas_crm_native_contact_identities(tenant_id,identity_type,identity_value,contact_id)
        VALUES(%s::uuid,'whatsapp_phone',%s,%s::uuid)
    """, (A, PHONE, CONTACT))
    db.commit()


def open_phone(db, actor=PROFILE):
    row = db.execute("""
        SELECT lead_id FROM saas_connect_conversations
        WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s
    """, (A,CA,JID))[0]
    db.commit()
    return tenant_operator_dialogs.history(actor, 'connect', row['lead_id'], CA, JID)


def command(page, **changes):
    return {'channel_id':CA,'conversation_id':JID,'lead_id':page['dialog']['lead_id'],
            'context_id':page['dialog']['deal_proposal']['context_id'],
            'deal_source':'kommo','deal_id':LEAD,'suggested':True,**changes}


async def attach_from_browser(request):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from tenant_operator_api import handler

    app = web.Application()
    origin = 'https://test-crm.example'
    app.router.add_post('/deal-link', handler(lambda _:PROFILE, origin, logging.getLogger(__name__), 'deal_dialog_link'))
    async with TestClient(TestServer(app)) as client:
        data = {**request,'expected_tenant_id':A,'expected_user_id':11}
        denied = await client.post('/deal-link', json={**data,'expected_user_id':99}, headers={'Origin':origin})
        assert denied.status == 409
        first = await client.post('/deal-link', json=data, headers={'Origin':origin})
        replay = await client.post('/deal-link', json=data, headers={'Origin':origin})
        assert first.status == replay.status == 200
        result = await first.json()
        assert result == await replay.json()
        return result['result']


def test_first_phone_message_and_one_click_keep_the_original_tiktok_deal(sale):
    db, reference = sale
    confirm_whatsapp(db)
    assert not db.execute("SELECT 1 FROM saas_connect_conversations WHERE conversation_id=%s", (JID,))
    db.commit()
    ref = phone_event(reference)
    consume(ref)
    consume(ref)
    page = open_phone(db)
    assert [(m['direction'],m['body']) for m in page['messages']] == [('outgoing',TEXT)]
    assert page['messages'][0]['author_name'] == 'Unknown sender'
    assert page['dialog']['deal_proposal']['suggested'] is True
    assert [(d['source'],d['id']) for d in page['dialog']['deal_proposal']['deals']] == [('kommo',LEAD)]
    assert [r['channel_name'] for r in tenant_deal_dialogs.linked_dialogs(PROFILE,'kommo',LEAD)['dialogs']] == ['TikTok']

    request = command(page)
    saved = asyncio.run(attach_from_browser(request))
    assert links.attach(PROFILE,request) == saved
    assert open_phone(db)['dialog']['deal_proposal'] == {'linked':True}
    routes = tenant_deal_dialogs.linked_dialogs(PROFILE,'kommo',LEAD)['dialogs']
    assert [(r['provider'],r['channel_name']) for r in routes] == [('kommo','TikTok'),('connect','WhatsApp')]
    assert routes[1]['deal_link_id'] == saved['link_id']
    assert db.execute("SELECT kommo_lead_id FROM saas_crm_deals WHERE tenant_id=%s", (A,)) == [{'kommo_lead_id':int(LEAD)}]
    assert not db.execute("SELECT id FROM saas_crm_native_deals WHERE tenant_id=%s", (A,))
    assert not db.execute("SELECT id FROM saas_crm_sync_outbox WHERE tenant_id=%s", (A,))
    assert db.execute("SELECT COUNT(*) AS n FROM saas_crm_deal_dialog_links")[0]['n'] == 1
    assert db.execute("SELECT COUNT(*) AS n FROM saas_tenant_audit_events WHERE action='deal_dialog_link'")[0]['n'] == 1

    outside = str(uuid.uuid4())
    db.execute("""
        INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
        VALUES(%s::uuid,%s::uuid,%s,'later-message',%s,'incoming','Outside this deal')
    """, (outside,A,request['lead_id'],'connect:'+CA))
    db.commit()
    transcript = links.history(PROFILE,'kommo',LEAD,saved['link_id'])
    assert [m['body'] for m in transcript['messages']] == [TEXT]
    assert links.history(PROFILE,'kommo',LEAD,saved['link_id'],outside)['messages'] == []
    with pytest.raises(TenantLinearError) as foreign:
        links.history({'tenant_id':B,'telegram_id':22},'kommo',LEAD,saved['link_id'])
    assert foreign.value.status == 404

    db.execute("UPDATE saas_tenant_integrations SET account_domain='another.kommo.com' WHERE tenant_id=%s AND provider='kommo'", (A,))
    db.commit()
    with pytest.raises(TenantLinearError):
        links.history(PROFILE,'kommo',LEAD,saved['link_id'])


def test_matching_phone_alone_does_not_attach_customer_or_deal(sale):
    db, reference = sale
    consume(phone_event(reference))
    page = open_phone(db)
    assert page['messages'][0]['body'] == TEXT
    assert 'deal_proposal' not in page['dialog']
    assert not db.execute("""
        SELECT contact_id FROM saas_crm_native_contact_conversations
        WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s
    """, (A,CA,JID))
    assert not db.execute("SELECT id FROM saas_crm_deal_dialog_links")
    db.commit()


def test_changed_candidates_reject_stale_one_click(sale):
    db, reference = sale
    confirm_whatsapp(db)
    consume(phone_event(reference))
    page = open_phone(db)
    assert page['dialog']['deal_proposal']['suggested'] is True
    kommo_deal(db,lead='918',name='Second actual sale')
    with pytest.raises(TenantLinearError) as stale:
        links.attach(PROFILE,command(page))
    assert stale.value.status == 409
    current = open_phone(db)['dialog']['deal_proposal']
    assert current['suggested'] is False
    assert {d['id'] for d in current['deals']} == {LEAD,'918'}
    linked = links.attach(PROFILE,command(page,suggested=False))
    assert links.history(PROFILE,'kommo',LEAD,linked['link_id'])['messages']
    with pytest.raises(TenantLinearError):
        links.attach(PROFILE,command(page,deal_id='918',suggested=False))


def test_missing_source_permission_hides_an_existing_link(sale, monkeypatch):
    db, reference = sale
    confirm_whatsapp(db)
    consume(phone_event(reference))
    page = open_phone(db)
    saved = links.attach(PROFILE,command(page))
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    db.execute("""
        INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s::uuid,33,'admin','Restricted employee','["deals","dialogs"]'::jsonb)
    """, (A,))
    db.execute("""
        INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
        VALUES(%s::uuid,'members','{"33":{"access_scopes":{"dialogs":{"view":"self","update":"self"}}}}'::jsonb)
        ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value
    """, (A,))
    db.commit()
    restricted = {'tenant_id':A,'telegram_id':33}
    assert tenant_deal_dialogs.linked_dialogs(restricted,'kommo',LEAD) == {'dialogs':[]}
    with pytest.raises(TenantLinearError):
        links.history(restricted,'kommo',LEAD,saved['link_id'])
    with pytest.raises(TenantLinearError):
        links.attach(restricted,command(page))


def test_one_click_uses_the_exact_accepted_episode_without_kommo(sale, monkeypatch):
    import tenant_crm_native_deals
    import tenant_platform
    from tenant_dialog_lifecycle import mutate

    db, reference = sale
    monkeypatch.setattr(tenant_platform, '_connect', lambda: db)
    monkeypatch.setattr(tenant_crm_native_deals, '_connect', lambda: db)
    confirm_whatsapp(db)
    consume(phone_event(reference))
    first = open_phone(db)
    native = str(uuid.uuid4())
    db.execute("DELETE FROM saas_tenant_integrations WHERE tenant_id=%s AND provider='kommo'", (A,))
    db.execute("""
        INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s::uuid,'test','Sales')
    """, (A,))
    db.execute("""
        INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name)
        VALUES(%s::uuid,'test','open','In work')
    """, (A,))
    db.execute("""
        INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,contact_id)
        VALUES(%s::uuid,%s::uuid,'Native sale','test','open',%s::uuid)
    """, (native,A,CONTACT))
    db.commit()
    mutate(PROFILE,{'provider':'connect','channel_id':CA,'conversation_id':JID,
        'lead_id':first['dialog']['lead_id'],'action':'accept','expected_revision':0,
        'request_id':str(uuid.uuid4())})
    event = delivery(after=3)
    event['event'] = {'sequence':4,'id':'d'*64,'payload':message(
        chat=JID,sender=JID,sender_alt='',provider_id='TEST_EPISODE_MESSAGE',text='In this episode')}
    store(event)
    consume((*reference[:3], event['event']['id']))
    present = open_phone(db)
    suggestion = present['dialog']['deal_proposal']
    assert suggestion['suggested'] and suggestion['deals'][0]['source']=='native'
    saved = links.attach(PROFILE,command(present,deal_source='native',deal_id=native))
    assert tenant_deal_dialogs.linked_dialogs(PROFILE,'native',native)['dialogs'][0]['deal_link_id'] == saved['link_id']
    assert [m['body'] for m in links.history(PROFILE,'native',native,saved['link_id'])['messages']] == ['In this episode']


def test_phone_from_tiktok_message_can_find_and_link_an_exact_whatsapp_chat(sale):
    db, reference = sale
    number_message = str(uuid.uuid4())
    other_message = str(uuid.uuid4())
    db.execute("DELETE FROM saas_crm_relation_identities WHERE tenant_id=%s", (A,))
    db.execute("""
        INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type)
        VALUES(%s::uuid,%s::uuid,%s,'TIKTOK_NUMBER','tiktok_kommo','incoming','055 500 01 03','text'),
              (%s::uuid,%s::uuid,%s,'TIKTOK_NUMBER_2','tiktok_kommo','outgoing','055 500 01 03','text')
    """, (number_message,A,int(LEAD),other_message,A,int(LEAD)))
    db.commit()
    assert tenant_deal_dialogs.linked_dialogs(PROFILE, 'kommo', LEAD)['can_find_whatsapp'] is True
    assert links.phone_candidates(PROFILE, LEAD, number_message) == {'candidates':[]}
    with pytest.raises(TenantLinearError) as outside:
        links.phone_candidates(PROFILE, LEAD, other_message)
    assert outside.value.status == 404

    peer = '994555000103@s.whatsapp.net'
    consume(phone_event(reference, chat=peer, alternate=''))
    candidates = links.phone_candidates(PROFILE, LEAD, number_message)['candidates']
    assert len(candidates) == 1 and candidates[0]['conversation_id'] == peer
    request = {key:value for key,value in candidates[0].items() if key!='channel_name'}
    request.update(deal_source='kommo',deal_id=LEAD,phone_message_id=number_message,suggested=False)
    kommo_deal(db, lead='918', contact=313)
    with pytest.raises(TenantLinearError) as other_deal:
        links.attach(PROFILE, {**request, 'deal_id':'918'})
    assert other_deal.value.status == 404
    saved = asyncio.run(attach_from_browser(request))
    assert links.attach(PROFILE, request) == saved
    assert [m['body'] for m in links.history(PROFILE, 'kommo', LEAD, saved['link_id'])['messages']] == [TEXT]
    assert db.execute("""
        SELECT phone, raw#>'{_embedded,contacts}' AS contacts FROM saas_crm_deals
        WHERE tenant_id=%s AND kommo_lead_id=%s
    """, (A,int(LEAD))) == [{'phone':'','contacts':[{'id':312,'is_main':True}]}]
    assert not db.execute("SELECT internal_id FROM saas_crm_relation_identities WHERE tenant_id=%s", (A,))
    assert db.execute("""
        SELECT kommo_phone_message_id::text, kommo_contact_id FROM saas_crm_deal_dialog_links
        WHERE tenant_id=%s
    """, (A,)) == [{'kommo_phone_message_id':number_message,'kommo_contact_id':'312'}]
    db.commit()


def test_phone_lookup_links_an_existing_confirmed_crm_send_without_a_contact(sale):
    from tenant_connect_transport import _upsert_conversation
    from tenant_dialog_lifecycle import mutate, record_message

    db, _ = sale
    peer = '994555000103@s.whatsapp.net'
    selected, sent = str(uuid.uuid4()), str(uuid.uuid4())
    with db.cursor() as cur:
        lead = _upsert_conversation(cur, A, CA, peer, {'kind':'contact'})
        cur.execute("""
            INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type)
            VALUES(%s::uuid,%s::uuid,%s,'TIKTOK_PRIOR_NUMBER','tiktok_kommo','incoming','0555000103','text')
        """, (selected, A, int(LEAD)))
    db.commit()
    accepted = mutate(PROFILE, {'provider':'connect','channel_id':CA,'conversation_id':peer,
        'lead_id':lead,'action':'accept','expected_revision':0,'request_id':str(uuid.uuid4())})
    assert accepted
    receipt = {'fromMe':True,'channelId':CA,'conversationId':peer,'status':'sent',
               'externalMessageId':'TEST_NATIVE_SENT'}
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,raw)
            VALUES(%s::uuid,%s::uuid,%s,'connect:TEST_NATIVE_SENT',%s,'outgoing',%s,%s::jsonb)
        """, (sent, A, lead, 'connect:'+CA, TEXT, json.dumps(receipt)))
        record_message(cur, A, CA, peer, sent)
    db.commit()
    assert links.phone_candidates(PROFILE, LEAD, selected) == {'candidates':[]}

    receipt['_crm_native_send_receipt'] = True
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE id=%s::uuid", (json.dumps(receipt), sent))
    db.commit()
    candidates = links.phone_candidates(PROFILE, LEAD, selected)['candidates']
    assert len(candidates) == 1 and candidates[0]['conversation_id'] == peer
    assert not db.execute("""
        SELECT contact_id FROM saas_crm_native_contact_conversations
        WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s
    """, (A, CA, peer))
    db.commit()

    request = {key:value for key,value in candidates[0].items() if key!='channel_name'}
    request.update(deal_source='kommo',deal_id=LEAD,phone_message_id=selected,suggested=False)
    existing = str(uuid.uuid4())
    db.execute("""
        INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone)
        VALUES(%s::uuid,%s::uuid,'Unverified contact','055 500 01 03')
    """, (existing, A))
    db.commit()
    with pytest.raises(TenantLinearError) as unresolved:
        links.attach(PROFILE, request)
    assert unresolved.value.status == 404
    assert not db.execute("SELECT id FROM saas_crm_deal_dialog_links")
    db.execute("DELETE FROM saas_crm_native_contacts WHERE tenant_id=%s AND id=%s", (A, existing))
    db.commit()

    automatic = {'deal_id':LEAD, 'auto_phone':True}
    related = '919'
    kommo_deal(db, lead=related, name='Another eligible deal for this client')
    assert links.attach(PROFILE, automatic) == {'linked':False}
    db.execute("DELETE FROM saas_crm_deals WHERE tenant_id=%s AND kommo_lead_id=%s", (A,int(related)))
    db.commit()
    unrelated = '920'
    kommo_deal(db, lead=unrelated, contact=313)
    db.execute("""
        INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type)
        VALUES(%s::uuid,%s::uuid,%s,'TIKTOK_SAME_NUMBER','tiktok_kommo','incoming','+994 55 500 01 03','text')
    """, (str(uuid.uuid4()),A,int(unrelated)))
    db.commit()
    assert links.attach(PROFILE, automatic) == {'linked':False}
    assert not db.execute("SELECT id FROM saas_crm_deal_dialog_links")
    assert not db.execute("SELECT id FROM saas_crm_native_contacts WHERE phone=%s", ('994555000103',))
    db.execute("DELETE FROM saas_crm_deals WHERE tenant_id=%s AND kommo_lead_id=%s", (A,int(unrelated)))
    db.commit()

    saved = asyncio.run(attach_from_browser(automatic))
    assert [m['body'] for m in links.history(PROFILE,'kommo',LEAD,saved['link_id'])['messages']] == [TEXT]
    assert db.execute("""
        SELECT count(*) AS n FROM saas_crm_native_contact_identities
        WHERE tenant_id=%s AND identity_type='whatsapp_phone' AND identity_value='994555000103'
    """, (A,)) == [{'n':1}]
    assert db.execute("SELECT count(*) AS n FROM saas_crm_deal_dialog_links") == [{'n':1}]
    assert db.execute("SELECT count(*) AS n FROM saas_crm_deals WHERE tenant_id=%s", (A,)) == [{'n':1}]
    assert db.execute("""SELECT action FROM saas_tenant_audit_events
        WHERE entity_id=%s AND entity_type='deal_dialog_link'""", (saved['link_id'],)) == [
            {'action':'deal_dialog_auto_link'}]
    db.commit()


def test_saved_whatsapp_source_only_links_the_selected_chat_for_the_deal(sale):
    from tenant_connect_transport import _upsert_conversation

    db, reference = sale
    peer = '994555000103@s.whatsapp.net'
    other = [str(uuid.uuid4()), str(uuid.uuid4())]
    with db.cursor() as cur:
        cur.execute("""
            INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
            VALUES(%s::uuid,%s::uuid,%s,'TIKTOK_SCOPED_NUMBER','tiktok_kommo','incoming','0555000103')
        """, (str(uuid.uuid4()), A, int(LEAD)))
        for source in other:
            cur.execute("""
                INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name)
                VALUES(%s::uuid,%s::uuid,'Other source')
            """, (A, source))
            _upsert_conversation(cur, A, source, peer, {'kind':'contact'})
    db.commit()
    consume(phone_event(reference, chat=peer, alternate=''))

    automatic = {'deal_id':LEAD, 'auto_phone':True}
    assert links.attach(PROFILE, automatic) == {'linked':False}
    settings = tenant_deal_dialogs.linked_dialogs(PROFILE, 'kommo', LEAD)['auto_whatsapp']
    assert settings['channel_id'] is None
    assert {source['id'] for source in settings['sources']} == {CA, *other}
    select = {'deal_id':LEAD,'auto_source':True,'expected_revision':settings['revision']}
    with pytest.raises(TenantLinearError):
        links.attach(PROFILE, {**select, 'channel_id':CB})
    saved = asyncio.run(attach_from_browser({**select, 'channel_id':CA}))
    assert saved['channel_id'] == CA
    with pytest.raises(TenantLinearError) as stale:
        links.attach(PROFILE, {**select, 'channel_id':other[0]})
    assert stale.value.status == 409

    selected = tenant_deal_dialogs.linked_dialogs(PROFILE,'kommo',LEAD)['auto_whatsapp']
    assert selected['channel_id'] == CA
    db.execute("""UPDATE saas_connect_channels SET active=FALSE
                  WHERE tenant_id=%s::uuid AND connect_channel_id IN (%s::uuid,%s::uuid)""",
               (A, CA, other[1]))
    db.commit()
    unavailable = tenant_deal_dialogs.linked_dialogs(PROFILE,'kommo',LEAD)['auto_whatsapp']
    assert unavailable['unavailable'] and unavailable['channel_id'] is None
    assert unavailable['revision'] == selected['revision']
    assert links.attach(PROFILE, automatic) == {'linked':False}
    assert not db.execute("SELECT 1 FROM saas_crm_deal_dialog_links")
    db.execute("""UPDATE saas_connect_channels SET active=TRUE
                  WHERE tenant_id=%s::uuid AND connect_channel_id IN (%s::uuid,%s::uuid)""",
               (A, CA, other[1]))
    db.commit()
    first = asyncio.run(attach_from_browser(automatic))
    assert first['linked'] is True
    assert links.attach(PROFILE, automatic) == first
    assert db.execute("""
        SELECT channel_id::text, conversation_id FROM saas_crm_deal_dialog_links
        WHERE tenant_id=%s::uuid AND deal_id=%s
    """, (A, LEAD)) == [{'channel_id':CA, 'conversation_id':peer}]
    assert db.execute("""
        SELECT count(*) AS n FROM saas_tenant_audit_events
        WHERE tenant_id=%s::uuid AND action='deal_dialog_auto_link'
    """, (A,)) == [{'n':1}]
    assert [row['body'] for row in links.history(PROFILE,'kommo',LEAD,first['link_id'])['messages']] == [TEXT]
    with pytest.raises(TenantLinearError) as locked:
        links.attach(PROFILE, {**select, 'channel_id':other[0], 'expected_revision':selected['revision']})
    assert locked.value.status == 409
