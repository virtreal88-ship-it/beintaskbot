"""Protect BeinSystems Operator-owned CRM names across the actual name writers."""
from tenant_beinsystems_operator_access import dialogue_chooser_enabled
from tenant_crm_native_access import CRMAccessError


def lock_name_writes(cur, tenant, *, wait=False):
    """Same transaction lock as both existing Operator import writers, without DDL."""
    if not dialogue_chooser_enabled(tenant):
        return False
    if wait:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(tenant) + ':operator-company-import',))
        return True
    cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired",
                (str(tenant) + ':operator-company-import',))
    if not cur.fetchone()['acquired']:
        raise CRMAccessError('company_name_busy',409)
    return True


def require_unreserved_name(cur, tenant, name, company_id):
    """Called only for an actual name write, under the shared tenant lock."""
    if not dialogue_chooser_enabled(tenant):
        return
    cur.execute("""WITH owned(tenant_id,company_id,account) AS (
        SELECT tenant_id,company_id,external_account FROM saas_beinsystems_operator_company_imports
          WHERE tenant_id=%s::uuid
        UNION ALL
        SELECT tenant_id,local_entity_id,external_id FROM saas_beinsystems_operator_links
          WHERE tenant_id=%s::uuid AND local_entity_type='company'
            AND external_entity_type='account' AND external_scope_id='' AND revoked_at IS NULL
      ) SELECT EXISTS(
        SELECT 1 FROM saas_crm_native_companies c
         WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid AND lower(c.name)=lower(%s)
      ) AS unchanged, EXISTS(
        SELECT 1 FROM owned m
         JOIN saas_crm_native_companies c ON c.tenant_id=m.tenant_id AND c.id=m.company_id
         WHERE m.company_id<>%s::uuid AND (lower(m.account)=lower(%s) OR lower(c.name)=lower(%s))
      ) OR (EXISTS(
        SELECT 1 FROM owned m WHERE m.company_id=%s::uuid
      ) AND EXISTS(
        SELECT 1 FROM saas_crm_native_companies c
         WHERE c.tenant_id=%s::uuid AND c.id<>%s::uuid AND lower(c.name)=lower(%s)
      )) AS reserved""", (tenant,tenant,tenant,company_id,name,company_id,name,name,
                          company_id,tenant,company_id,name))
    result=cur.fetchone()
    if not result['unchanged'] and result['reserved']:
        raise CRMAccessError('company_name_conflict',409)


def require_unreserved_account_pair(cur,tenant,account,company_id):
    """Reject contradictory stable/manual evidence, not an authorized exact retry."""
    cur.execute("""SELECT EXISTS(SELECT 1 FROM saas_beinsystems_operator_company_imports
      WHERE tenant_id=%s::uuid AND (
        (lower(external_account)=lower(%s) AND (external_account<>%s OR company_id<>%s::uuid))
        OR (company_id=%s::uuid AND external_account<>%s)))
      OR EXISTS(SELECT 1 FROM saas_beinsystems_operator_links
      WHERE tenant_id=%s::uuid AND local_entity_type='company' AND external_entity_type='account'
        AND external_scope_id='' AND revoked_at IS NULL AND (
        (lower(external_id)=lower(%s) AND (external_id<>%s OR local_entity_id<>%s::uuid))
        OR (local_entity_id=%s::uuid AND external_id<>%s))) AS conflict""",
      (tenant,account,account,company_id,company_id,account,tenant,account,account,company_id,company_id,account))
    if cur.fetchone()['conflict']:
        raise CRMAccessError('operator_company_identity_conflict',409)
