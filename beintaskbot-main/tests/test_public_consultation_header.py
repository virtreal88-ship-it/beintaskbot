"""Frozen public metadata: isolated PGlite only, synthetic identities and messages."""
import uuid
from datetime import datetime
import pytest
from test_dialog_consultations import ready, prepared, TARGET
from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
import tenant_dialog_consultations as flow
import tenant_public_case as public
from tenant_linear_policy import TenantLinearError


def preview(data,profile=PROFILE):
    return flow.preview(profile,{k:data[k] for k in ('channel_id','conversation_id')})


def opt_in(data,profile=PROFILE):
    shown=preview(data,profile)
    data.update(public_header_version=1,public_header_hash=shown['public_header_hash'],include_customer_identity=False,
        public_header_source_versions={row['id']:row['source_version'] for row in shown['messages'] if row['id'] in data['message_ids']})
    return shown['public_header']


def contact(db,tenant=A,channel=CA,conversation='client'):
    company,person=str(uuid.uuid4()),str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_native_companies(id,tenant_id,name) VALUES(%s::uuid,%s::uuid,'Customer Company')",(company,tenant))
    db.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone,company_id) VALUES(%s::uuid,%s::uuid,'Synthetic Customer','+19999999999',%s::uuid)",(person,tenant,company))
    db.execute("INSERT INTO saas_crm_native_contact_conversations(tenant_id,provider,channel_id,conversation_id,contact_id) VALUES(%s::uuid,'connect',%s::uuid,%s,%s::uuid)",(tenant,channel,conversation,person))
    db.commit()
    return company,person


def verified_phone(db):
    db.execute('''CREATE TABLE IF NOT EXISTS saas_connect_direct_identities(
      tenant_id UUID,connect_channel_id UUID,identity_key TEXT,canonical_conversation_id TEXT,verified_at TIMESTAMPTZ DEFAULT now())''')
    db.execute("INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s::uuid,%s::uuid,'15551234567@s.whatsapp.net','client')",(A,CA))
    db.commit()


def test_new_header_frozen_verified_selected_timestamp_and_actual_sender(ready):
    db,data,_=ready;company,person=contact(db);verified_phone(db)
    db.execute("UPDATE saas_crm_messages SET happened_at='2026-10-01T09:00:00Z' WHERE id=%s::uuid",(data['message_ids'][0],))
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,happened_at) VALUES(%s::uuid,%s::uuid,41,'later','incoming',%s,'Not selected','2026-10-02T09:00:00Z')",(str(uuid.uuid4()),A,'connect:'+CA));db.commit()
    employee={'tenant_id':A,'telegram_id':33}
    header=opt_in(data,employee)
    assert header=={'version':1,'customer_name':'Synthetic Customer','verified_phone':'+15551234567','phone_status':'verified','company_name':'Customer Company','operator_name':'Employee'}
    result,token=prepared(ready,employee)
    frozen=public.read(token)['public_identity']
    assert {k:v for k,v in frozen.items() if k!='last_message_at'}==header
    assert datetime.fromisoformat(frozen['last_message_at']).day==1
    assert 'hash' not in frozen
    db.execute("UPDATE saas_crm_native_contacts SET display_name='Changed' WHERE id=%s::uuid",(person,))
    db.execute("UPDATE saas_crm_native_companies SET name='Changed Company' WHERE id=%s::uuid",(company,))
    db.execute("UPDATE saas_tenant_members SET display_name='Changed Employee' WHERE tenant_id=%s::uuid AND telegram_id=33",(A,));db.commit()
    assert public.read(token)['public_identity']==frozen
    # An exact request retry returns the original receipt, never creates/changes a share.
    assert not flow.prepare(employee,data,'https://crm.test')['created']
    assert db.execute('SELECT count(*) AS n FROM saas_public_case_shares')[0]['n']==1


@pytest.mark.parametrize('field',['customer','company','operator','phone'])
def test_metadata_change_requires_preview_refresh_before_first_prepare(ready,field):
    db,data,_=ready;company,person=contact(db);opt_in(data)
    if field=='customer':db.execute("UPDATE saas_crm_native_contacts SET display_name='New Customer' WHERE id=%s::uuid",(person,))
    elif field=='company':db.execute("UPDATE saas_crm_native_companies SET name='New Company' WHERE id=%s::uuid",(company,))
    elif field=='operator':db.execute("UPDATE saas_tenant_members SET display_name='New Owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,))
    else:verified_phone(db)
    db.commit()
    with pytest.raises(TenantLinearError,match='Публичные данные изменились'):flow.prepare(PROFILE,data,'https://crm.test')
    assert db.execute('SELECT count(*) AS n FROM saas_public_case_shares')[0]['n']==0
    opt_in(data);prepared(ready)


def test_preview_does_not_opt_in_old_requests_or_mutate_old_links(ready):
    db,data,_=ready;contact(db);preview(data)
    _,token=prepared(ready)
    assert public.read(token)['public_identity']=={}
    data.update(request_id=str(uuid.uuid4()),reuse_existing=True)
    opt_in(data)
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,data,'https://crm.test')
    assert public.read(token)['public_identity']=={}


def test_v1_reuse_exact_header_only_and_legacy_cannot_reuse_it(ready):
    db,data,_=ready;opt_in(data);result,_=prepared(ready)
    data.update(request_id=str(uuid.uuid4()),reuse_existing=True)
    assert flow.prepare(PROFILE,data,'https://crm.test')['id']==result['id']
    data.pop('public_header_version');data.pop('public_header_hash');data.pop('public_header_source_versions')
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,data,'https://crm.test')


@pytest.mark.parametrize('scope',['tenant','channel','conversation','archived_contact','archived_company'])
def test_company_exact_binding_only_and_no_tenant_brand(ready,scope):
    db,data,_=ready
    company,person=contact(db,tenant=B if scope=='tenant' else A,channel=CB if scope=='channel' else CA,conversation='other' if scope=='conversation' else 'client')
    if scope=='archived_contact':db.execute('UPDATE saas_crm_native_contacts SET archived_at=now() WHERE id=%s::uuid',(person,))
    if scope=='archived_company':db.execute('UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid',(company,))
    db.commit();header=opt_in(data)
    assert header['company_name']=='' and header['verified_phone']=='' and header['phone_status']=='unknown'
    prepared(ready)


def test_group_header_never_uses_customer_identity_or_company(ready):
    db,data,_=ready;contact(db,conversation=TARGET)
    with db.cursor() as cur:
        header,proof=flow._public_header(cur,A,33,CA,{'conversation_id':TARGET,'contact_name':'Participant','phone':'+15551234567'})
    assert header=={'version':1,'customer_name':'','verified_phone':'','phone_status':'unknown','company_name':'','operator_name':'Employee'}
    assert len(proof)==64


@pytest.mark.parametrize('change',[{'public_header_version':True},{'public_header_version':2},{'public_header_hash':'bad'},{'public_header_version':1},{'public_header_hash':'a'*64}])
def test_invalid_or_partial_header_proof_rejected(ready,change):
    _,data,_=ready;data.update(change)
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,data,'https://crm.test')


def test_missing_names_stay_empty_and_phone_not_inferred_from_contact_edit(ready):
    db,data,_=ready
    db.execute("UPDATE saas_connect_conversations SET contact_name='',manual_display_name='',display_name='',phone='+19999999999' WHERE tenant_id=%s::uuid AND conversation_id='client'",(A,))
    db.execute("UPDATE saas_tenant_members SET display_name='' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    header=opt_in(data)
    assert header['customer_name']==header['operator_name']==header['company_name']==header['verified_phone']==''
    prepared(ready)


@pytest.mark.parametrize('field',['happened_at','body'])
def test_selected_message_changes_require_preview_refresh(ready,field):
    db,data,_=ready;opt_in(data)
    if field=='happened_at':db.execute("UPDATE saas_crm_messages SET happened_at='2026-11-01T09:00:00Z' WHERE id=%s::uuid",(data['message_ids'][0],))
    else:db.execute("UPDATE saas_crm_messages SET body='Changed selected message' WHERE id=%s::uuid",(data['message_ids'][0],))
    db.commit()
    with pytest.raises(TenantLinearError,match='Выбранный фрагмент изменился'):flow.prepare(PROFILE,data,'https://crm.test')
    assert db.execute('SELECT count(*) AS n FROM saas_public_case_shares')[0]['n']==0


def test_missing_selection_proof_rejected_and_proof_bound_to_request_identity(ready):
    _,data,_=ready;opt_in(data);proof=data.pop('public_header_source_versions')
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,data,'https://crm.test')
    data['public_header_source_versions']=proof;prepared(ready)
    data['public_header_source_versions']={key:'0'*64 for key in proof}
    with pytest.raises(TenantLinearError,match='Код запроса уже использован'):flow.prepare(PROFILE,data,'https://crm.test')


def test_actor_proof_binding_even_with_same_display_name(ready):
    db,data,_=ready
    db.execute("UPDATE saas_tenant_members SET display_name='Owner' WHERE tenant_id=%s::uuid AND telegram_id=33",(A,));db.commit()
    opt_in(data)
    with pytest.raises(TenantLinearError,match='Публичные данные изменились'):
        flow.prepare({'tenant_id':A,'telegram_id':33},data,'https://crm.test')
    prepared(ready)
    data.update(request_id=str(uuid.uuid4()),reuse_existing=True)
    employee={'tenant_id':A,'telegram_id':33};opt_in(data,employee)
    with pytest.raises(TenantLinearError):flow.prepare(employee,data,'https://crm.test')


@pytest.mark.parametrize('state',['sending','pending','unknown','sent'])
def test_v1_cannot_bypass_existing_delivery_fence_after_metadata_refresh(ready,state):
    db,data,_=ready;opt_in(data);prepared(ready)
    db.execute('UPDATE saas_consultation_group_deliveries SET state=%s',(state,))
    db.execute("UPDATE saas_tenant_members SET display_name='New Owner' WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));db.commit()
    data.update(request_id=str(uuid.uuid4()),reuse_existing=True);opt_in(data)
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,data,'https://crm.test')
    assert db.execute('SELECT count(*) AS n FROM saas_public_case_shares')[0]['n']==1


def test_v1_share_expiry_and_revoke_unchanged(ready):
    db,data,_=ready;opt_in(data);result,token=prepared(ready)
    db.execute("UPDATE saas_public_case_shares SET expires_at=now()-interval '1 day'");db.commit()
    with pytest.raises(TenantLinearError):public.read(token)
    db.execute("UPDATE saas_public_case_shares SET expires_at=now()+interval '1 day'");db.commit()
    flow.revoke(PROFILE,result['id'])
    with pytest.raises(TenantLinearError):public.read(token)
