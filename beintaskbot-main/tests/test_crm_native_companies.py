"""Native customer-company CRUD and tenant isolation on isolated PGlite."""
import uuid
import asyncio
from types import SimpleNamespace
from unittest import mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB
import tenant_crm_native_companies as companies
import tenant_crm_native_contacts as contacts
import tenant_crm_native_deals as deals
import tenant_crm_native_company_api as company_api


@pytest.fixture
def company_db(db,monkeypatch):
    monkeypatch.setattr(companies,'_connect',lambda:db)
    monkeypatch.setattr(companies,'_ensure_schema',lambda _conn:None)
    monkeypatch.setattr(contacts,'_connect',lambda:db)
    monkeypatch.setattr(contacts,'_ensure_schema',lambda _conn:None)
    monkeypatch.setattr(deals,'_connect',lambda:db)
    monkeypatch.setattr(deals,'_ensure_schema',lambda _conn:None)
    return db


def test_company_crud_contact_relationship_and_tenant_isolation(company_db):
    db=company_db
    company=companies.create_company(A,'Acme','+994501234567','info@acme.test','Customer',11)
    replay=companies.create_company(A,'Acme duplicate name','+994501234567','info@acme.test','Customer',11)
    assert replay['id']==company['id']
    contact=contacts.create_contact(A,'Aysel','+994501234567')
    contact2=contacts.create_contact(A,'Murad','+994501234568')
    assert companies.link_entity(A,company['id'],'contact',contact['id'])
    assert companies.link_entity(A,company['id'],'contact',contact2['id'])
    db.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s::uuid,'p1','Sales')",(A,))
    db.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'p1','s1','New')",(A,))
    deal_id=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,contact_id) VALUES(%s::uuid,%s::uuid,'First deal','p1','s1',%s::uuid)",(deal_id,A,contact['id']))
    assert companies.link_entity(A,company['id'],'deal',deal_id)
    item=companies.get_company(A,company['id'])
    assert item['name']=='Acme' and len(item['contacts'])==2 and len(item['deals'])==1
    assert contacts.get_contact(A,contact['id'])['company_name']=='Acme'
    assert contacts.get_contact(A,contact['id'])['company_id']==company['id']
    assert deals.get_deal(A,deal_id)['company_name']=='Acme'
    assert deals.get_deal(A,deal_id)['company_id']==company['id']
    assert companies.list_companies(A,'Acme')[0]['contact_count']==2
    assert companies.get_company(B,company['id']) is None
    assert companies.link_entity(B,company['id'],'contact',contact['id']) is None
    updated=companies.update_company(A,company['id'],'Acme Ltd','+994501234567','sales@acme.test','Updated',11)
    assert updated['name']=='Acme Ltd'
    assert companies.archive_company(A,company['id'])
    assert contacts.get_contact(A,contact['id']) is not None
    assert companies.get_company(A,company['id']) is None
    db.commit()


def test_company_link_rejects_invalid_entity_and_cross_tenant_ids(company_db):
    company=companies.create_company(A,'Tenant A')
    foreign_contact=contacts.create_contact(B,'Tenant B contact')
    with pytest.raises(ValueError):companies.link_entity(A,company['id'],'company',foreign_contact['id'])
    assert companies.link_entity(A,company['id'],'contact',foreign_contact['id']) is None
    company_db.commit()


def test_company_api_requires_member_and_scopes_reads_to_tenant():
    handler=company_api.handler(lambda _request:None,'https://crm.test',mock.MagicMock())
    denied=asyncio.run(handler(SimpleNamespace(method='GET',match_info={},query={})))
    assert denied.status==403
    handler=company_api.handler(lambda _request:{'tenant_id':'tenant-1','role':'manager'},'https://crm.test',mock.MagicMock())
    with mock.patch.object(company_api,'list_companies',return_value=[]) as listing:
        response=asyncio.run(handler(SimpleNamespace(method='GET',match_info={},query={'search':'Acme'})))
    assert response.status==200
    listing.assert_called_once_with('tenant-1','Acme',50)
