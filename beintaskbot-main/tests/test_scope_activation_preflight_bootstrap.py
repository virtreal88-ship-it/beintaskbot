"""Exercise inventory queries against real application bootstrap, not guessed DDL."""
import re
from test_tenant_support_inbox import database, db
from test_scope_activation_preflight import p

# This shared support fixture does not promise optional native CRM/sync bootstrap.
# A missing optional table must still yield unknown, never a successful zero.
OPTIONAL_TABLES = {
    'saas_crm_native_contacts','saas_crm_native_companies','saas_crm_native_deals','saas_crm_native_tasks',
    'saas_crm_sync_outbox','saas_crm_legacy_delivery_attempts','saas_crm_entity_links',
    'saas_crm_relation_identities','saas_crm_relation_outbox','saas_crm_relation_heads',
    'saas_crm_scalar_outbox','saas_crm_scalar_heads',
}


def test_current_bootstrap_columns_types_and_fail_closed_optional_schema(db):
    tables={table for sql in p.QUERIES.values() for table in re.findall(r'public\.(saas_\w+)',sql)}
    present={table: bool(db.execute('SELECT to_regclass(%s) AS relation',('public.'+table,))[0]['relation'])
             for table in tables}
    db.commit()
    missing={table for table,exists in present.items() if not exists}
    assert missing <= OPTIONAL_TABLES, missing
    report=p.inventory(db)
    for name,sql in p.QUERIES.items():
        dependencies=set(re.findall(r'public\.(saas_\w+)',sql))
        check=report['checks'][name]
        if dependencies & missing:
            assert check=={'status':'unknown','reason':'query_unavailable_or_failed'}, name
        else:
            assert check['status']=='observed', name
            assert isinstance(check['count'],int),name
    assert report['checks']['connect_unassigned_sources']['count']==2
    assert report['checks']['ambiguous_company_owners']['count']==0
    assert report['safe_to_enable'] is False
