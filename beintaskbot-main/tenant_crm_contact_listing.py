"""Read-only, source-qualified contact directory over existing CRM records."""
from __future__ import annotations

import re
from uuid import UUID
from psycopg.errors import SerializationFailure

from tenant_crm_queries import deal_filters
from tenant_platform_core import _connect
from tenant_policy import TenantPolicy
from tenant_workflow_store import _read_tenant_workflow
from tenant_crm_request_context import native_policy_profile


class ContactsDirectoryError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def _require_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''SELECT bool_and(to_regclass(name) IS NOT NULL) AS ready
          FROM unnest(%s::text[]) AS required(name)''', ([
            'saas_tenants', 'saas_tenant_members', 'saas_tenant_workflow_policies',
            'saas_tenant_pipelines', 'saas_tenant_pipeline_stages',
            'saas_crm_native_contacts', 'saas_crm_native_companies',
            'saas_crm_deals', 'saas_tenant_integrations'],))
        if not (cur.fetchone() or {}).get('ready'):
            raise ContactsDirectoryError('contacts_unavailable', 503)


def _current_policy(cur, tenant, actor):
    # Same order as current task/member/workflow writers. These are read
    # fences, held only until the selected directory snapshot is complete.
    cur.execute('SELECT id,modules,onboarding FROM saas_tenants WHERE id=%s::uuid FOR SHARE', (tenant,))
    company = cur.fetchone()
    if not company:
        raise ContactsDirectoryError('contacts_forbidden', 403)
    cur.execute('''SELECT policy_key FROM saas_tenant_workflow_policies
      WHERE tenant_id=%s::uuid AND policy_key=ANY(%s::text[])
      ORDER BY policy_key FOR SHARE''', (tenant, ['members', 'modules']))
    cur.fetchall()
    cur.execute('''SELECT * FROM saas_tenant_members WHERE tenant_id=%s::uuid
      AND telegram_id=%s FOR SHARE''', (tenant, actor))
    member = cur.fetchone()
    if not member or member.get('active') is not True:
        raise ContactsDirectoryError('contacts_forbidden', 403)
    member = native_policy_profile(dict(member))
    return TenantPolicy({**dict(member), 'tenant_id': tenant,
        'modules': company.get('modules') or {}, 'onboarding': company.get('onboarding') or {},
        'workflow': _read_tenant_workflow(cur, tenant)})


def _positive_json(expression):
    # JSON booleans/fractions/null must not become account/contact IDs. Cast
    # only a bounded integer spelling; numeric strings normalize losslessly.
    text = f"({expression} #>> '{{}}')"
    return f'''CASE WHEN jsonb_typeof({expression}) IN ('number','string')
      AND {text} ~ '^[0-9]{{1,19}}$' THEN
        CASE WHEN {text}::numeric BETWEEN 1 AND 9223372036854775807
          THEN ({text}::numeric)::text END END'''


def _parameters(source, search, limit, offset, row_key, legacy_deal_id):
    if source not in (None, '', 'native', 'kommo'):
        raise ContactsDirectoryError('contacts_invalid_query')
    if type(limit) is bool or type(offset) is bool:
        raise ContactsDirectoryError('contacts_invalid_query')
    try:
        limit, offset = int(limit), int(offset)
    except (ValueError, TypeError):
        raise ContactsDirectoryError('contacts_invalid_query') from None
    if not 1 <= limit <= 100 or offset < 0 or offset > 2_147_483_647:
        raise ContactsDirectoryError('contacts_invalid_query')
    if row_key and legacy_deal_id:
        raise ContactsDirectoryError('contacts_invalid_query')
    if row_key and not re.fullmatch(r'(?:kommo:[1-9][0-9]{0,18}:[1-9][0-9]{0,18}|legacy-deal:[1-9][0-9]{0,18})', str(row_key)):
        raise ContactsDirectoryError('contacts_invalid_query')
    if legacy_deal_id and not re.fullmatch(r'[1-9][0-9]{0,18}', str(legacy_deal_id)):
        raise ContactsDirectoryError('contacts_invalid_query')
    if (row_key or legacy_deal_id) and source == 'native':
        raise ContactsDirectoryError('contacts_invalid_query')
    needle = str(search or '').strip()[:160]
    needle = '%' + needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    return source or None, needle, limit, offset


def directory(profile, **query):
    # A waiting SHARE fence under RR may observe a concurrently changed row.
    # Retry only that serialization failure, with an entirely fresh snapshot;
    # never reuse partial rows, counts or policy from the failed attempt.
    for attempt in range(2):
        try:
            return _directory_snapshot(profile, **query)
        except SerializationFailure:
            if attempt:
                raise ContactsDirectoryError('contacts_unavailable', 503) from None


def _directory_snapshot(profile, *, source=None, search='', limit=50, offset=0, row_key='', legacy_deal_id=''):
    """Return one authorized page/count snapshot, without provider operations."""
    source, needle, limit, offset = _parameters(source, search, limit, offset, row_key, legacy_deal_id)
    try:
        tenant, actor = str(UUID(str(profile['tenant_id']))), int(profile['telegram_id'])
    except (ValueError, TypeError, KeyError):
        raise ContactsDirectoryError('contacts_forbidden', 403) from None
    lookup = bool(row_key or legacy_deal_id)
    with _connect() as conn:
        # Workflow is stored across several tables. One request must never
        # combine old pipeline ownership with newly visible stages/data.
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        _require_schema(conn)
        with conn.cursor() as cur:
            policy = _current_policy(cur, tenant, actor)
            native_allowed = policy.allows_action('customers', 'view')
            scope = policy.pipeline_scope()
            external_allowed = bool((policy.allows('deals') or policy.allows('customers')) and scope)
            selected = source or ('kommo' if lookup else 'native' if native_allowed else 'kommo')
            if not (native_allowed if selected == 'native' else external_allowed):
                raise ContactsDirectoryError('contacts_forbidden', 403)
            where, scoped_args = deal_filters(tenant_id=tenant, scope=scope if external_allowed else [])
            lead_account = _positive_json("s.raw->'account_id'")
            contact_account = _positive_json("p.contact->'account_id'")
            contact_id = _positive_json("p.contact->'id'")
            provenance = '''split_part(lower(trim(COALESCE(s.channel,''))),':',1)
                NOT IN ('connect','beintaskbot','beinsystems')
              AND COALESCE(lower(trim(s.raw->>'source')),'') IN ('','kommo')
              AND COALESCE(lower(trim(s.raw->>'provider')),'') IN ('','kommo')
              AND COALESCE(lower(trim(p.contact->>'source')),'') IN ('','kommo')
              AND COALESCE(lower(trim(p.contact->>'provider')),'') IN ('','kommo')'''
            # Authority is applied before primary selection and identity rank.
            # An inaccessible newer lead can never supply display/search data.
            ctes = f'''
              scoped AS MATERIALIZED (
                SELECT kommo_lead_id,contact_name,phone,name,stage_name,channel,source_updated_at,synced_at,
                  CASE WHEN jsonb_typeof(raw)='object' THEN raw ELSE '{{}}'::jsonb END AS raw
                FROM saas_crm_deals WHERE {where} AND kommo_lead_id>0
              ),
              primary_rows AS MATERIALIZED (
                SELECT s.*,p.contact,p.invalid,{lead_account} AS lead_account,
                  {contact_account} AS contact_account,{contact_id} AS contact_id,
                  s.raw ? 'account_id' AS lead_declared,
                  COALESCE(p.contact ? 'account_id',FALSE) AS contact_declared,
                  ({provenance}) AS is_kommo
                FROM scoped s LEFT JOIN LATERAL (
                  SELECT (array_agg(c.value ORDER BY CASE WHEN c.value->'is_main'='true'::jsonb
                    THEN 0 ELSE 1 END,c.position))[1] AS contact,
                    COALESCE(bool_or(jsonb_typeof(c.value)<>'object'
                      OR (c.value ? 'is_main' AND c.value->'is_main' NOT IN ('true'::jsonb,'false'::jsonb))),FALSE)
                      OR (count(*)>1 AND count(*) FILTER(WHERE c.value->'is_main'='true'::jsonb)<>1) AS invalid
                  FROM jsonb_array_elements(CASE WHEN jsonb_typeof(s.raw#>'{{_embedded,contacts}}')='array'
                    THEN s.raw#>'{{_embedded,contacts}}' ELSE '[]'::jsonb END)
                    WITH ORDINALITY AS c(value,position)
                ) p ON TRUE
              ),
              identities AS (
                SELECT *,CASE WHEN NOT invalid AND contact_id IS NOT NULL
                  AND (lead_declared OR contact_declared)
                  AND (NOT lead_declared OR lead_account IS NOT NULL)
                  AND (NOT contact_declared OR contact_account IS NOT NULL)
                  AND (NOT (lead_declared AND contact_declared) OR lead_account=contact_account)
                  THEN COALESCE(lead_account,contact_account) END AS account_id
                FROM primary_rows WHERE is_kommo
              ),
              keyed AS (
                SELECT *,CASE WHEN account_id IS NOT NULL
                  THEN 'kommo:'||account_id||':'||contact_id
                  ELSE 'legacy-deal:'||kommo_lead_id::text END AS key
                FROM identities
              ),
              ranked AS (
                SELECT *,row_number() OVER(PARTITION BY key ORDER BY
                  source_updated_at DESC NULLS LAST,synced_at DESC NULLS LAST,kommo_lead_id DESC) AS position
                FROM keyed
              ),
              external_directory AS MATERIALIZED (
                SELECT key,CASE WHEN account_id IS NOT NULL THEN 'kommo_contact'
                  ELSE 'legacy_deal_context' END AS kind,
                  CASE WHEN account_id IS NOT NULL THEN 'kommo' ELSE 'legacy' END AS source,
                  account_id,CASE WHEN account_id IS NOT NULL THEN contact_id END AS contact_id,
                  COALESCE(contact_name,'') AS display_name,COALESCE(phone,'') AS phone,
                  kommo_lead_id::text AS lead_id,name AS lead_name,stage_name,source_updated_at,synced_at,
                  TRUE AS historical,COALESCE(source_updated_at,synced_at) AS sort_at
                FROM ranked WHERE position=1
              )'''
            args = list(scoped_args)
            if legacy_deal_id:
                # Exact old deal references remain old deal objects, including
                # explicit Connect snapshots, without acquiring contact IDs.
                base = '''SELECT 'legacy-deal:'||kommo_lead_id::text AS key,
                  'legacy_deal_context'::text AS kind,'legacy'::text AS source,
                  contact_name AS display_name,phone,kommo_lead_id::text AS lead_id,name AS lead_name,
                  stage_name,source_updated_at,synced_at,TRUE AS historical,
                  COALESCE(source_updated_at,synced_at) AS sort_at FROM scoped
                  WHERE kommo_lead_id::text=%s'''
                args.append(str(legacy_deal_id))
            elif selected == 'kommo':
                base = 'SELECT * FROM external_directory'
                if row_key:
                    base += ' WHERE key=%s'; args.append(str(row_key))
            else:
                base = '''SELECT 'native:'||c.id::text AS key,'native_contact'::text AS kind,
                  'native'::text AS source,c.id,c.display_name,c.phone,c.email,c.company_id,
                  co.name AS company_name,c.created_at,c.updated_at,c.updated_at AS sort_at
                  FROM saas_crm_native_contacts c LEFT JOIN saas_crm_native_companies co
                    ON co.tenant_id=c.tenant_id AND co.id=c.company_id
                  WHERE c.tenant_id=%s::uuid AND c.archived_at IS NULL'''
                args.append(tenant)
            search_columns = ['display_name', 'phone'] + (['email'] if selected == 'native' else ['lead_name', 'stage_name'])
            search_sql = 'TRUE' if lookup else '(' + ' OR '.join(f"{name} ILIKE %s ESCAPE '\\'" for name in search_columns) + ')'
            if not lookup:
                args.extend([needle] * len(search_columns))
            page_limit, page_offset = (1, 0) if lookup else (limit, offset)
            args.extend([page_limit, page_offset, tenant])
            cur.execute(f'''WITH {ctes},directory AS MATERIALIZED ({base}),
              matched AS MATERIALIZED (SELECT * FROM directory WHERE {search_sql}),
              page AS (SELECT * FROM matched ORDER BY sort_at DESC NULLS LAST,key LIMIT %s OFFSET %s)
              SELECT (SELECT count(*) FROM directory) AS source_total,
                (SELECT count(*) FROM matched) AS total,
                COALESCE((SELECT jsonb_agg(to_jsonb(p)-'sort_at' ORDER BY sort_at DESC NULLS LAST,key)
                  FROM page p),'[]'::jsonb) AS data,
                EXISTS(SELECT 1 FROM primary_rows WHERE is_kommo) AS has_external_cache,
                EXISTS(SELECT 1 FROM saas_tenant_integrations WHERE tenant_id=%s::uuid
                  AND provider='kommo' AND status='connected') AS connected''', args)
            result = cur.fetchone()
            if lookup and not result['data']:
                raise ContactsDirectoryError('contacts_not_found', 404)
            actions = {action: policy.allows_action('customers', grant) for action, grant in (
                ('view', 'view'), ('create', 'create'), ('update', 'update'), ('archive', 'delete'), ('assign', 'assign'))}
            actions['update_deals'] = policy.allows_action('deals', 'update')
            readonly = {key: key == 'view' for key in actions}
            for entry in result['data']:
                entry['actions'] = actions if entry['source'] == 'native' else readonly
            external_available = external_allowed and (result['connected'] or result['has_external_cache'])
            sources = [
                {'source': 'native', 'allowed': native_allowed, 'available': native_allowed,
                 'state': 'ready' if native_allowed else 'forbidden',
                 'reason': '' if native_allowed else 'permission_denied'},
                {'source': 'kommo', 'allowed': external_allowed, 'available': external_available,
                 'state': 'forbidden' if not external_allowed else 'cached' if external_available else 'unavailable',
                 'reason': 'permission_denied' if not external_allowed else '' if result['connected'] else 'integration_not_connected',
                 'historical': True}]
            state = ('unavailable' if selected == 'kommo' and not external_available and not lookup else
                     'empty' if not result['source_total'] else 'no_matches' if not result['total'] else 'ready')
            total = int(result['total'])
            return {'data': result['data'], 'total': total, 'source_total': int(result['source_total']),
                'limit': page_limit, 'offset': page_offset,
                'next_offset': page_offset + page_limit if page_offset + page_limit < total else None,
                'source': selected, 'sources': sources, 'state': state,
                'actions': actions if selected == 'native' else readonly}
