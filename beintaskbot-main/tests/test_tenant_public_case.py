"""Isolated database tests for case-bound opaque public shares; all media is synthetic."""
import hashlib
import json
import uuid
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
import tenant_public_case as cases
import tenant_support_inbox_store as tickets
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError
GROUP='120363411822115409@g.us'


def create_case(db, monkeypatch):
    monkeypatch.setattr(cases,'_connect',lambda:db)
    cases._schema(db)
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,99,'Developer group',now()) ON CONFLICT DO NOTHING",(A,CA,GROUP));db.commit()
    with db.cursor() as cur:
        receipt=tickets.create_in_transaction(cur,A,CA,'client','synthetic','public-test',actor=11)
    db.commit()
    return receipt['id']


def add_message(db, tenant, channel, lead, text, kind='text', external='synthetic'):
    key=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming',%s,%s)''',(key,tenant,lead,external,'connect:'+channel,text,kind));db.commit()
    return key


def share(db, monkeypatch, ticket_id, messages, media=None, **extra):
    monkeypatch.setattr(cases,'_authorize',lambda cur,profile:(A,11))
    return cases.create(PROFILE,ticket_id,{'message_ids':messages,'media_message_ids':media or [],
      'confirm_media_review':bool(media),'group_channel_id':CA,'group_conversation_id':GROUP,**extra},'https://example.test')


def test_share_hash_expiry_revoke_and_tamper_are_enforced(db,monkeypatch):
    ticket=create_case(db,monkeypatch);msg=add_message(db,A,CA,41,'The receipt total differs.')
    result=share(db,monkeypatch,ticket,[msg]);token=result['url'].rsplit('/',1)[1]
    row=db.execute('SELECT token_hash,expires_at,revoked_at FROM saas_public_case_shares WHERE id=%s::uuid',(result['id'],))[0]
    assert row['token_hash']==hashlib.sha256(token.encode()).hexdigest() and token not in row['token_hash']
    audit=db.execute("SELECT payload FROM saas_tenant_audit_events WHERE entity_type='support_ticket' AND entity_id=%s ORDER BY created_at DESC LIMIT 1",(ticket,))[0]['payload']
    assert msg in audit['selected_message_ids'] and audit['share_id']==result['id']
    assert cases.read(token)['public_messages'][0]['text']=='The receipt total differs.'
    with pytest.raises(TenantLinearError):cases.read(token+'tampered')
    db.execute("UPDATE saas_public_case_shares SET expires_at=now()-interval '1 second' WHERE id=%s::uuid",(result['id'],));db.commit()
    with pytest.raises(TenantLinearError):cases.read(token)
    db.execute("UPDATE saas_public_case_shares SET expires_at=now()+interval '1 hour' WHERE id=%s::uuid",(result['id'],));db.commit()
    monkeypatch.setattr(cases,'_authorize',lambda cur,profile:(A,11))
    assert cases.revoke(PROFILE,result['id'])=={'revoked':True}
    with pytest.raises(TenantLinearError):cases.read(token)


def test_cross_tenant_and_unselected_media_id_swap_are_denied(db,monkeypatch):
    ticket=create_case(db,monkeypatch);selected=add_message(db,A,CA,41,'Selected complaint','image','selected')
    other_tenant=add_message(db,B,CB,42,'private media','image','foreign')
    result=share(db,monkeypatch,ticket,[selected],[selected]);token=result['url'].rsplit('/',1)[1]
    with pytest.raises(TenantLinearError):cases.media(token,other_tenant)
    with pytest.raises(TenantLinearError):cases.media(token,str(uuid.uuid4()))


def test_public_share_group_must_be_in_case_tenant(db,monkeypatch):
    ticket=create_case(db,monkeypatch);msg=add_message(db,A,CA,41,'Selected report')
    db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,98,'Foreign group',now()) ON CONFLICT DO NOTHING",(B,CB,GROUP));db.commit()
    monkeypatch.setattr(cases,'_authorize',lambda cur,profile:(A,11))
    with pytest.raises(TenantLinearError):cases.create(PROFILE,ticket,{'message_ids':[msg],'group_channel_id':CB,'group_conversation_id':GROUP},'https://example.test')


def test_selected_media_is_resolved_by_scoped_message_id_and_bytes_are_server_fetched(db,monkeypatch):
    ticket=create_case(db,monkeypatch)
    media_ids=[add_message(db,A,CA,41,'',kind,'connect:'+kind+'-exact') for kind in ('image','video','audio','voice','document')]
    result=share(db,monkeypatch,ticket,media_ids,media_ids);token=result['url'].rsplit('/',1)[1]
    calls=[]
    monkeypatch.setattr(transport,'connect_route',lambda cur,tenant,lead_id,**kw:
      {'channel_id':CA,'conversation_id':'client','api_key':'synthetic-test'})
    expected={'type':'image','base64':'c3ludGhldGljLWJ5dGVz','mimeType':'image/png','fileName':'case-attachment'}
    monkeypatch.setattr(transport,'download_connect_message_media',lambda route,external,kind:
      (calls.append((route['channel_id'],route['conversation_id'],external,kind)) or {**expected,'type':kind}))
    for media_id in media_ids:assert cases.media(token,media_id)['type'] in {'image','video','audio','document'}
    assert calls==[(CA,'client','image-exact','image'),(CA,'client','video-exact','video'),
      (CA,'client','audio-exact','audio'),(CA,'client','voice-exact','audio'),(CA,'client','document-exact','document')]
    saved=db.execute('SELECT group_channel_id::text,group_conversation_id,selected_message_ids FROM saas_public_case_shares WHERE id=%s::uuid',(result['id'],))[0]
    assert saved['group_channel_id']==CA and saved['group_conversation_id']==GROUP and all(key in saved['selected_message_ids'] for key in media_ids)


def test_secret_lines_are_removed_and_media_requires_explicit_review(db,monkeypatch):
    ticket=create_case(db,monkeypatch);text=add_message(db,A,CA,41,'Issue persists.\nAnyDesk password: supersecret\n[Image supplied for analysis: fake-media-id]\nPlease check.')
    result=share(db,monkeypatch,ticket,[text]);token=result['url'].rsplit('/',1)[1]
    body=cases.read(token)['public_messages'][0]['text']
    assert 'Issue persists.' in body and 'Please check.' in body and 'supersecret' not in body and 'AnyDesk' not in body and 'fake-media-id' not in body
    image=add_message(db,A,CA,41,'[Image supplied for analysis: fake]','image','image-review')
    with pytest.raises(TenantLinearError):cases.create(PROFILE,ticket,{'message_ids':[image],'media_message_ids':[image],
      'group_channel_id':CA,'group_conversation_id':GROUP},'https://example.test')


def test_public_page_renders_media_kinds_read_only_and_never_uses_old_deal_endpoints():
    from pathlib import Path
    page=(Path(__file__).resolve().parents[2]/'docs/public-case.html').read_text()
    for kind,tag in [('image','img'),('video','video'),('audio','audio')]:
        assert f"kind==='{kind}'" in page
        assert f"createElement('{tag}')" in page
    assert "createElement('a')" in page  # document fallback
    assert "textContent" in page and 'api/deal/file' not in page and 'api/deal/public' not in page
    assert 'form' not in page.lower() and 'fetch(' not in page
    from tenant_public_case_api import _PUBLIC_HEADERS
    assert _PUBLIC_HEADERS['Cache-Control']=='no-store, private, max-age=0'
    assert _PUBLIC_HEADERS['X-Robots-Tag']=='noindex, nofollow, noarchive'
    assert _PUBLIC_HEADERS['Referrer-Policy']=='no-referrer'
