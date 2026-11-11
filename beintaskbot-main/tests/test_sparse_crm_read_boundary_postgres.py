"""Real-DB external directory guard with sparse, current stored matrices."""
import pytest
from test_crm_scoped_access_postgres import database, db, staff, native, set_scopes, A
import tenant_crm_contact_listing as listing
import tenant_member_store as members
from tenant_policy import TenantPolicy


@pytest.mark.parametrize('module',['deals','customers'])
@pytest.mark.parametrize('scope',['none','self','department'])
def test_sparse_external_contact_read_reloads_current_matrix(native,module,scope):
    with native['factory']() as conn:
        conn.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name,owner_telegram_id) VALUES(%s::uuid,'10','Visible legacy pipeline',81)",(A,))
        conn.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'10','100','Visible stage')",(A,))
    # Baseline proves this fixture would pass the old cross-module fallback.
    stale=members.member(A,81)
    assert TenantPolicy(stale).pipeline_scope()
    assert listing.directory(stale,source='kommo')['total']==0
    set_scopes(native,81,{module:{'view':scope}})
    current=members.member(A,81)
    other='customers' if module=='deals' else 'deals'
    assert TenantPolicy(current).allows(other)
    with pytest.raises(listing.ContactsDirectoryError,match='contacts_forbidden'):
        listing.directory(stale,source='kommo')
    # Restricted native view remains available when granted, independently.
    if module=='customers' and scope!='none':
        assert listing.directory(current,source='native')['total']>0
