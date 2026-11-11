"""Approved source-card company/deal and create-contact rules on isolated SQL."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from test_tenant_support_inbox import database, db, A, B
from test_crm_scoped_access_postgres import native, set_scopes, configure, KINDS
import tenant_crm_native_companies as companies
import tenant_crm_native_deals as deals
import tenant_crm_native_company_api as company_api
import tenant_crm_native_deal_api as deal_api
import tenant_member_store as members
from tenant_crm_native_access import CRMAccessError


@pytest.fixture
def staff(db,monkeypatch):
    monkeypatch.setattr(members,'_connect',lambda:db)
    return {'factory':lambda:db}


def matrix(**changes):
    scopes={kind:{op:'none' for op in ('create','view','update','delete')} for kind in KINDS}
    for kind,values in changes.items(): scopes[kind].update(values)
    return scopes


@pytest.mark.parametrize('source', ['company','deal'])
@pytest.mark.parametrize('scope,allowed', [('none',set()),('self',{'self'}),('department',{'self','peer'}),('all',{'self','peer','other','system'})])
def test_company_deal_relations_use_only_exact_source_update(native,source,scope,allowed):
    module='companies' if source=='company' else 'deals'
    set_scopes(native,81,matrix(**{module:{'update':scope}}))
    for key in ('self','peer','other','system','foreign'):
        company=native['ids']['companies'][key if source=='company' else 'other']
        deal=native['ids']['deals'][key if source=='deal' else 'other']
        with native['factory']() as conn:
            result=conn.execute('SELECT * FROM saas_crm_native_deals WHERE id=%s::uuid',(deal,))
            before=dict(result.fetchone()) if hasattr(result,'fetchone') else result[0]
        link=(lambda:companies.link_entity(A,company,'deal',deal,actor_id=81)) if source=='company' else (lambda:deals.set_deal_company_link(A,deal,company,actor_id=81))
        unlink=(lambda:companies.unlink_entity(A,company,'deal',deal,actor_id=81)) if source=='company' else (lambda:deals.set_deal_company_link(A,deal,company,unlink=True,actor_id=81))
        if scope=='none':
            with pytest.raises(CRMAccessError):link()
        elif key in allowed:
            assert link()
            # Source U changes just FK/revision, not target contents/responsibility.
            with native['factory']() as conn:
                result=conn.execute('SELECT * FROM saas_crm_native_deals WHERE id=%s::uuid',(deal,))
                after=dict(result.fetchone()) if hasattr(result,'fetchone') else result[0]
            assert str(after['company_id'])==company
            for field in ('title','contact_id','responsible_id','access_responsible_id','pipeline_id','stage_id','description'):
                assert after[field]==before[field]
            assert unlink()
        else:assert link() is None


def test_unlink_is_exact_and_targets_are_same_tenant_active(native):
    set_scopes(native,81,matrix(companies={'update':'self'},deals={'update':'self'}))
    company=native['ids']['companies']['self'];deal=native['ids']['deals']['self']
    assert companies.link_entity(A,company,'deal',deal,actor_id=81)
    assert deals.set_deal_company_link(A,deal,native['ids']['companies']['other'],unlink=True,actor_id=81) is None
    assert companies.unlink_entity(A,company,'deal',native['ids']['deals']['foreign'],actor_id=81) is None
    assert deals.set_deal_company_link(A,deal,native['ids']['companies']['foreign'],actor_id=81) is None
    with native['factory']() as conn:
        conn.execute('UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid',(company,))
    assert deals.set_deal_company_link(A,deal,company,unlink=True,actor_id=81) is None


@pytest.mark.parametrize('scope,allowed', [('none',set()),('self',{'self'}),('department',{'self','peer'}),('all',{'self','peer','other','system'})])
def test_initial_contact_requires_deal_create_and_current_contact_read(native,scope,allowed):
    set_scopes(native,81,matrix(deals={'create':'self'},customers={'view':scope}))
    options=deals.deal_options(A,actor_id=81,action='create')
    assert {str(row['id']) for row in options['contacts']}=={native['ids']['customers'][k] for k in allowed}
    for key in ('self','peer','other','system','foreign'):
        contact=native['ids']['customers'][key]
        if key in allowed:
            saved=deals.create_deal(A,'New','p','s',contact_id=contact,actor_id=81)
            assert str(saved['contact_id'])==contact
        else:
            with pytest.raises(CRMAccessError,match='contact_unavailable'):
                deals.create_deal(A,'No','p','s',contact_id=contact,actor_id=81)
    assert deals.create_deal(A,'Without contact','p','s',actor_id=81)


@pytest.mark.parametrize('operation',['company','deal','create'])
def test_outbound_relation_blocker_is_atomic(native,operation):
    set_scopes(native,81,matrix(companies={'update':'self'},deals={'update':'self','create':'self'},customers={'view':'self'}))
    company=native['ids']['companies']['self'];deal=native['ids']['deals']['self']
    with native['factory']() as conn:
        conn.execute('''INSERT INTO saas_crm_provider_settings(tenant_id,entity_type,provider,mode,primary_provider,sync_direction,allowed_fields)
          VALUES(%s::uuid,'deal','kommo','beintaskbot','beintaskbot','to_external','["title"]')''',(A,))
    with pytest.raises(CRMAccessError,match='scoped_relation_sync_not_configured'):
        if operation=='company':companies.link_entity(A,company,'deal',deal,actor_id=81)
        elif operation=='deal':deals.set_deal_company_link(A,deal,company,actor_id=81)
        else:deals.create_deal(A,'Blocked','p','s',contact_id=native['ids']['customers']['self'],actor_id=81)
    with native['factory']() as conn:
        result=conn.execute('SELECT company_id FROM saas_crm_native_deals WHERE id=%s::uuid',(deal,))
        row=result.fetchone() if hasattr(result,'fetchone') else result[0]
        assert row['company_id'] is None
        result=conn.execute("SELECT id FROM saas_crm_native_deals WHERE title='Blocked'")
        assert not (result.fetchall() if hasattr(result,'fetchall') else result)
        result=conn.execute('SELECT id FROM saas_crm_sync_outbox')
        assert not (result.fetchall() if hasattr(result,'fetchall') else result)


@pytest.mark.parametrize('source',['company','deal'])
def test_source_specific_api_no_export_and_no_field_smuggling(native,source):
    module='companies' if source=='company' else 'deals'
    configure(native,module,'update','self')
    profile=members.member(A,81)
    company=native['ids']['companies']['self' if source=='company' else 'other']
    deal=native['ids']['deals']['self' if source=='deal' else 'other']
    data={'action':'link_deal','entity_id':deal} if source=='company' else {'company_id':company,'unlink':False}
    async def body():return data
    request=SimpleNamespace(method='POST',match_info={'id':company if source=='company' else deal},query={},
        content_type='application/json',headers={'Origin':'https://test.invalid'},json=body)
    api=company_api if source=='company' else deal_api
    handle=api.handler(lambda _:profile,'https://test.invalid',Mock(),**({'company_relation':True} if source=='deal' else {}))
    with patch('tenant_crm_sync_outbox.enqueue_change') as enqueue:
        assert asyncio.run(handle(request)).status==200
        enqueue.assert_not_called()
        data['title']='not allowed'
        assert asyncio.run(handle(request)).status==400
        del data['title']
        configure(native,module,'update','none')
        assert asyncio.run(handle(request)).status==403


def test_new_deal_relation_endpoint_cannot_bypass_gate_off_legacy(native,monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','false')
    with pytest.raises(CRMAccessError,match='scoped_relation_unavailable'):
        deals.set_deal_company_link(A,native['ids']['deals']['self'],native['ids']['companies']['self'],actor_id=81)


def test_initial_contact_read_revocation_and_archive_fail_in_creation_transaction(native):
    contact=native['ids']['customers']['self']
    set_scopes(native,81,matrix(deals={'create':'self'},customers={'view':'self'}))
    profile=members.member(A,81)
    data={'title':'Not created','pipeline_id':'p','stage_id':'s','contact_id':contact}
    async def body():return data
    request=SimpleNamespace(method='POST',match_info={},query={},content_type='application/json',
        headers={'Origin':'https://test.invalid'},json=body)
    handle=deal_api.handler(lambda _:profile,'https://test.invalid',Mock())
    set_scopes(native,81,matrix(deals={'create':'self'}))
    with patch('tenant_crm_sync_outbox.enqueue_change') as enqueue:
        assert asyncio.run(handle(request)).status==404
        enqueue.assert_not_called()
        set_scopes(native,81,matrix(deals={'create':'self'},customers={'view':'self'}))
        with native['factory']() as conn:
            conn.execute('UPDATE saas_crm_native_contacts SET archived_at=now() WHERE id=%s::uuid',(contact,))
        assert asyncio.run(handle(request)).status==404
        enqueue.assert_not_called()
    with native['factory']() as conn:
        result=conn.execute("SELECT id FROM saas_crm_native_deals WHERE title='Not created'")
        assert not (result.fetchall() if hasattr(result,'fetchall') else result)


def test_deal_relation_capabilities_are_row_specific_and_do_not_open_target_lists(native):
    set_scopes(native,81,matrix(deals={'view':'all','update':'self'}))
    own=deals.get_deal(A,native['ids']['deals']['self'],actor_id=81)
    other=deals.get_deal(A,native['ids']['deals']['other'],actor_id=81)
    assert own['scoped_relations'] is True
    assert own['relation_actions']=={'link_company':True,'unlink_company':True}
    assert other['relation_actions']=={'link_company':False,'unlink_company':False}
    assert deals.deal_options(A,actor_id=81)['contacts']==[]
    with pytest.raises(CRMAccessError):companies.list_companies(A,actor_id=81)
