"""Synthetic DB only: exact named fragment, lazy scoped media, no provider sends."""
import uuid
from unittest.mock import AsyncMock
import pytest
from test_dialog_consultations import ready, prepared
from test_tenant_support_inbox import database, db, A, CA, PROFILE
import tenant_dialog_consultations as flow
import tenant_public_case as public
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError


def enable(data):
    shown=flow.preview(PROFILE,{k:data[k] for k in ('channel_id','conversation_id')})
    chosen=[r for r in shown['messages'] if r['id'] in data['message_ids']]
    data.update(public_dialog_version=1,public_header_version=1,public_header_hash=shown['public_header_hash'],
        public_header_source_versions={r['id']:r['source_version'] for r in chosen},
        public_sender_versions={r['id']:r['public_sender_version'] for r in chosen},
        media_ids=[r['id'] for r in chosen if r['media_shareable']])
    return shown


def seed_fragment(db,data,count=5):
    db.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid",(A,))
    ids=[]
    for i in range(count+1):
        key=str(uuid.uuid4());ids.append(key)
        db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,
          author_name,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,%s,%s,
          '2026-11-01T10:00:00Z'::timestamptz+(%s * interval '1 minute'))""",
          (key,A,'connect:'+str(uuid.uuid4()),'incoming' if i%2==0 else 'outgoing','connect:'+CA,
          'Synthetic Customer' if i%2==0 else 'Actual Employee', 'selected '+str(i),
          ['image','audio','video','voice','document','text'][i%6],i))
    db.commit();data['message_ids']=ids[:-1];return ids[-1]


def test_named_exact_fragment_and_click_media(ready,monkeypatch):
    db,data,sender=ready;outside=seed_fragment(db,data);enable(data)
    loader=AsyncMock(return_value=(b'x','image/png'))
    import tenant_connect_media
    monkeypatch.setattr(tenant_connect_media,'download_media',loader)
    result,token=prepared(ready);snapshot=public.read(token)
    assert not loader.called and not sender.called
    assert snapshot['selected_media_ids']==data['media_ids']
    assert [m['id'] for m in snapshot['public_messages']]==data['message_ids']
    assert [m['direction'] for m in snapshot['public_messages']]==['incoming','outgoing','incoming','outgoing','incoming']
    assert snapshot['public_messages'][1]['speaker']=='Actual Employee'
    assert snapshot['public_messages'][1]['speaker']!=snapshot['public_identity']['operator_name']
    for key in data['media_ids']:assert public.media(token,key)['base64']=='eA=='
    assert loader.call_count==5
    for args in loader.call_args_list:
        assert args.args[0]['route']['channel_id']==CA and args.args[0]['route']['conversation_id']=='client'
        assert str(uuid.UUID(args.args[0]['message_id']))==args.args[0]['message_id']
    with pytest.raises(TenantLinearError):public.media(token,outside)
    assert loader.call_count==5
    db.execute("UPDATE saas_crm_messages SET author_name='Later Name' WHERE id=%s::uuid",(data['message_ids'][1],));db.commit()
    assert public.read(token)==snapshot
    flow.revoke(PROFILE,result['id'])
    with pytest.raises(TenantLinearError):public.media(token,data['message_ids'][0])
    assert loader.call_count==5


@pytest.mark.parametrize('change',['missing_media','outside_media','name_changed','bad_version','missing_proof'])
def test_new_mode_fails_closed_before_publication(ready,change):
    db,data,sender=ready;outside=seed_fragment(db,data);enable(data)
    if change=='missing_media':data['media_ids']=[]
    if change=='outside_media':data['media_ids'].append(outside)
    if change=='name_changed':
        db.execute("UPDATE saas_crm_messages SET author_name='Changed' WHERE id=%s::uuid",(data['message_ids'][0],));db.commit()
    if change=='bad_version':data['public_dialog_version']=True
    if change=='missing_proof':data.pop('public_sender_versions')
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,data,'https://crm.test')
    assert db.execute('SELECT count(*) AS n FROM saas_public_case_shares')[0]['n']==0
    assert not sender.called


def test_thirty_fragment_preserves_media_and_secret_withholding(ready):
    db,data,_=ready;seed_fragment(db,data,30)
    db.execute("UPDATE saas_crm_messages SET body='password: synthetic' WHERE id=%s::uuid",(data['message_ids'][0],));db.commit()
    enable(data);_,token=prepared(ready);snapshot=public.read(token)
    assert len(snapshot['public_messages'])==30
    assert data['message_ids'][0] not in snapshot['selected_media_ids']
    assert 'synthetic' not in snapshot['public_messages'][0]['text']
    assert len(snapshot['selected_media_ids'])==24


@pytest.mark.parametrize('direction,author,group,expected',[
    ('outgoing','Actual Employee',False,'Actual Employee'),
    ('outgoing','',False,'Əməkdaş'),('outgoing','unknown sender',False,'Əməkdaş'),
    ('incoming','Group Person',True,'Group Person'),('incoming','',True,'İştirakçı'),
    ('incoming','Person',False,'CRM Customer'),('unexpected','',False,'Göndərən göstərilməyib'),
    ('outgoing','AI Agent',False,'AI Agent')])
def test_sender_names_are_source_based_not_share_creator(direction,author,group,expected):
    row={'id':'one','direction':direction,'author_name':author,'body':'hi','message_type':'text','happened_at':''}
    item=flow.public_dialog_message(row,{'conversation_kind':'group' if group else 'contact'},
        {'customer_name':'CRM Customer','operator_name':'Share Creator'},[])
    assert item['speaker']==expected
    assert item['direction']==(direction if direction in {'incoming','outgoing'} else 'unknown')


@pytest.mark.parametrize('mutation',['revoke','expire','replace'])
def test_lazy_download_rechecks_snapshot_after_fetch(ready,monkeypatch,mutation):
    import tenant_connect_media
    db,data,_=ready;seed_fragment(db,data);enable(data);_,token=prepared(ready)
    async def changed(item,**kwargs):
        if mutation=='revoke':db.execute('UPDATE saas_public_case_shares SET revoked_at=now()')
        elif mutation=='expire':db.execute("UPDATE saas_public_case_shares SET expires_at=now()-interval '1 second'")
        else:db.execute("UPDATE saas_crm_messages SET body='Changed' WHERE id=%s::uuid",(data['message_ids'][0],))
        db.commit();return b'x','image/png'
    monkeypatch.setattr(tenant_connect_media,'download_media',changed)
    with pytest.raises(TenantLinearError):public.media(token,data['message_ids'][0])


@pytest.mark.parametrize('mutation',['token','expire','route','raw_route','source'])
def test_lazy_download_denials_never_hit_connector(ready,monkeypatch,mutation):
    import tenant_connect_media
    db,data,_=ready;seed_fragment(db,data);enable(data);_,token=prepared(ready)
    loader=AsyncMock(return_value=(b'x','image/png'));monkeypatch.setattr(tenant_connect_media,'download_media',loader)
    if mutation=='token':token='x'*40
    elif mutation=='expire':db.execute("UPDATE saas_public_case_shares SET expires_at=now()-interval '1 second'");db.commit()
    elif mutation=='route':monkeypatch.setattr(transport,'connect_route',lambda *a,**k:{'channel_id':CA,'conversation_id':'other','api_key':'synthetic'})
    elif mutation=='raw_route':
        # A source change after admission is withheld, even before route checks.
        db.execute("UPDATE saas_crm_messages SET raw='{"+'"conversationId":"other"'+"}'::jsonb WHERE id=%s::uuid",(data['message_ids'][0],));db.commit()
    else:db.execute("UPDATE saas_crm_messages SET external_id=%s WHERE id=%s::uuid",('connect:'+str(uuid.uuid4()),data['message_ids'][0]));db.commit()
    with pytest.raises(TenantLinearError):public.media(token,data['message_ids'][0])
    assert not loader.called


def test_changed_sender_cannot_reuse_older_named_snapshot(ready):
    db,data,_=ready;seed_fragment(db,data);enable(data);result,token=prepared(ready)
    data.update(request_id=str(uuid.uuid4()),reuse_existing=True)
    assert flow.prepare(PROFILE,data,'https://crm.test')['id']==result['id']
    db.execute("UPDATE saas_crm_messages SET author_name='New Employee' WHERE id=%s::uuid",(data['message_ids'][1],));db.commit()
    enable(data)
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,data,'https://crm.test')
    assert public.read(token)['public_messages'][1]['speaker']=='Actual Employee'


def test_group_incoming_source_name_fallback_never_attributes_outgoing_peer_name():
    row={'id':'one','direction':'incoming','author_name':'','raw':{'participantName':'Source Participant'},
         'body':'hi','message_type':'text','happened_at':''}
    assert flow.public_dialog_message(row,{'conversation_kind':'group'},{},[])['speaker']=='Source Participant'
    row['direction']='outgoing'
    assert flow.public_dialog_message(row,{'conversation_kind':'group'},{},[])['speaker']=='Əməkdaş'
