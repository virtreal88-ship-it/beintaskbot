"""Find open deals by confirmed contact identity. Only serialize authorized choices."""
from tenant_crm_native_access import scoped_sql
from tenant_crm_sync_store import connection_id
from tenant_kommo_board_provenance import revisions


def kommo_contact(cur, tenant, contact, binding):
    if not binding:
        return None
    cur.execute("""
        SELECT external_id FROM saas_crm_relation_identities
        WHERE tenant_id=%s::uuid AND internal_id=%s::uuid AND provider='kommo'
          AND entity_type='contact' AND account_id=%s AND connection_generation=%s
    """, (tenant, contact, binding.account_id, binding.connection_generation))
    row = cur.fetchone()
    external = str(row['external_id']) if row else ''
    return external if 0 < len(external) <= 19 and int(external) < 2**63 else None


def related_contact(deal, external_contact):
    if not external_contact:
        return False
    raw = deal.get('raw')
    embedded = raw.get('_embedded') if isinstance(raw, dict) else None
    contacts = embedded.get('contacts') if isinstance(embedded, dict) else None
    return isinstance(contacts, list) and any(
        isinstance(item, dict) and str(item.get('id')) == external_contact for item in contacts)


def allowed_kommo(policy, deal, *, update=False):
    if (not policy.kommo_access() or not policy.can_read_kommo_chat(deal)
            or policy.action_scope('deals','view') not in (None, 'all')):
        return False
    return not update or (
        policy.allows_action('deals','update') and policy.action_scope('deals','update') in (None, 'all'))


def kommo_scope(binding):
    return (binding.account_id, binding.connection_generation) if binding else ('', '')


def _catalog_complete(cur, tenant, binding, external_contact):
    if not binding:
        cur.execute("""
            SELECT 1 FROM saas_tenant_integrations
            WHERE tenant_id=%s::uuid AND provider IN ('kommo','kommo_manual') AND account_domain<>''
            LIMIT 1
        """, (tenant,))
        return not cur.fetchone()
    if not external_contact:
        return False
    cur.execute("SELECT to_regclass('saas_crm_sync_jobs') AS present")
    if not cur.fetchone()['present']:
        return False
    cur.execute("""
        SELECT 1 FROM saas_crm_sync_jobs WHERE tenant_id=%s::uuid AND resource='leads'
          AND connection_id=%s AND status='done' AND phase='deleted' AND last_full_sync>0
    """, (tenant, connection_id(cur, tenant)))
    return bool(cur.fetchone())


def choices(cur, tenant, contact, binding, policy, *, verified_kommo_contact=None):
    """Count before employee filtering. An incomplete catalog permits explicit choice."""
    native_view, view_args = scoped_sql(policy.profile, 'deals', 'view', 'd')
    native_update, update_args = scoped_sql(policy.profile, 'deals', 'update', 'd')
    cur.execute(f"""
        SELECT d.id, d.title, p.name AS pipeline_name, s.name AS stage_name,
               ({native_view}) AND ({native_update}) AS allowed, i.external_id
        FROM saas_crm_native_deals d
        JOIN saas_tenant_pipelines p USING(tenant_id,pipeline_id)
        JOIN saas_tenant_pipeline_stages s USING(tenant_id,pipeline_id,stage_id)
        LEFT JOIN saas_crm_relation_identities i ON i.tenant_id=d.tenant_id
          AND i.internal_id=d.id AND i.provider='kommo' AND i.entity_type='deal'
          AND i.account_id=%s AND i.connection_generation=%s
        WHERE d.tenant_id=%s::uuid AND d.contact_id=%s::uuid
          AND d.archived_at IS NULL AND p.active AND s.stage_type='open'
        ORDER BY d.id
    """, (*view_args, *update_args, *kommo_scope(binding), tenant, contact))
    native = cur.fetchall()
    external_contact = verified_kommo_contact or kommo_contact(cur, tenant, contact, binding)
    complete = _catalog_complete(cur, tenant, binding, external_contact)
    mirrors = {str(row['external_id']) for row in native if row.get('external_id')}
    kommo = []
    if external_contact:
        cur.execute("""
            SELECT * FROM saas_crm_deals
            WHERE tenant_id=%s::uuid
              AND raw->>'source' IS DISTINCT FROM 'connect'
              AND (
                raw#>'{_embedded,contacts}' @> jsonb_build_array(jsonb_build_object('id',%s::bigint))
                OR raw#>'{_embedded,contacts}' @> jsonb_build_array(jsonb_build_object('id',%s::text))
                OR kommo_lead_id=ANY(%s::bigint[])
              )
            ORDER BY kommo_lead_id
        """, (tenant, external_contact, external_contact,
              [int(value) for value in mirrors if len(value)<=19 and int(value)<2**63]))
        kommo = cur.fetchall()
    opened = {str(row['kommo_lead_id']) for row in kommo
              if row['deleted_at'] is None and row['status_id'] not in (142,143)
              and related_contact(row, external_contact)}
    proven = revisions(cur, tenant, 'leads', [row['kommo_lead_id'] for row in kommo], binding=binding,
                       expected_raw={str(row['kommo_lead_id']):row['raw'] for row in kommo}) if kommo else {}
    # A synced Kommo stage supersedes an out-of-date native representation.
    expired_mirrors = mirrors.intersection(proven.keys()) - opened
    available = []
    counted = set()
    for row in native:
        external = row.get('external_id')
        if external in expired_mirrors:
            continue
        counted.add(('kommo', external) if external else ('native', str(row['id'])))
        if row['allowed']:
            available.append({'source':'native','id':str(row['id']),'title':row['title'],
                              'pipeline_name':row['pipeline_name'],'stage_name':row['stage_name']})
    for row in kommo:
        ident = str(row['kommo_lead_id'])
        if ident not in opened:
            continue
        counted.add(('kommo', ident))
        if ident in proven and row['status_id'] > 0 and allowed_kommo(policy, row, update=True):
            pipeline = next((p.get('name', '') for p in policy.deal_board()
                             if str(p.get('pipeline_id')) == str(row['pipeline_id'])), '')
            available.append({'source':'kommo','id':ident,'title':row['name'],
                              'pipeline_name':pipeline,'stage_name':row['stage_name']})
    # Use the actual Kommo card for a confirmed native representation when both
    # can be selected. The native card is still available if it alone is allowed.
    remote = {choice['id'] for choice in available if choice['source']=='kommo'}
    duplicate = {str(row['id']) for row in native if row.get('external_id') in remote}
    available = [choice for choice in available if choice['source']!='native' or choice['id'] not in duplicate]
    return available, complete and not (mirrors - proven.keys()) and len(counted)==1 and len(available)==1
