"""Native create entry is a page capability, never a read/provider grant."""
import pytest
from test_scoped_access_foundation import person
from tenant_policy import TenantPolicy


@pytest.mark.parametrize('contact_view',['none','self'])
def test_create_only_deal_page_is_available_without_read_modules(monkeypatch,contact_view):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    profile=person({'deals':{'create':'self','view':'none','update':'none','delete':'none'},
                    'customers':{'create':'none','view':contact_view,'update':'none','delete':'none'}},permissions=[])
    caps=TenantPolicy(profile).public_capabilities()
    assert caps['pages']['deals'] is True
    assert caps['actions']['deals']=={'view':False,'create':True,'update':False,'delete':False}
    assert caps['actions']['customers']['view'] is (contact_view=='self')
    assert caps['modules']['deals'] is False and caps['modules']['customers'] is False
