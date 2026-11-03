"""Current, read-only projections of existing customer companies and deals."""
from __future__ import annotations

from datetime import date, datetime
import re
from uuid import UUID

from psycopg.errors import SerializationFailure

from tenant_platform_core import _connect
from tenant_policy import TenantPolicy
from tenant_workflow_store import _read_tenant_workflow
from tenant_crm_queries import deal_filters
from tenant_crm_request_context import native_policy_profile
from crm_stage_labels import stage_display_name


class CRMListError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def public(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public(item) for item in value]
    return value


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise CRMListError('crm_invalid_query') from None


def _positive(value):
    if not re.fullmatch(r'[1-9][0-9]{0,18}', str(value)) or int(value) > 9223372036854775807:
        raise CRMListError('crm_invalid_query')
    return str(value)


def query(request, profile):
    """Keep optional browser intent separate from server-owned authority."""
    params = request.query if hasattr(request, 'query') else request.rel_url.query
    result = {}
    for key in ('listing', 'read', 'search', 'limit', 'offset', 'lead_id',
                'expected_tenant_id', 'expected_user_id'):
        values = params.getall(key, []) if hasattr(params, 'getall') else (
            [params[key]] if key in params else [])
        if len(values) > 1 or (values and not str(values[0]) and key != 'search'):
            raise CRMListError('crm_invalid_query')
        if values:
            result[key] = values[0]
    if result.get('listing') not in (None, '1') or result.get('read') not in (None, '1'):
        raise CRMListError('crm_invalid_query')
    if result.get('listing') and result.get('read'):
        raise CRMListError('crm_invalid_query')
    if (result.get('listing') or result.get('read')) and any(key in params for key in ('refresh', 'full', 'options')):
        raise CRMListError('crm_invalid_query')
    pair = ('expected_tenant_id', 'expected_user_id')
    if any(key in result for key in pair):
        if not all(key in result for key in pair):
            raise CRMListError('crm_invalid_query')
        tenant, actor = _uuid(result[pair[0]]), _positive(result[pair[1]])
        if tenant != _uuid(profile.get('tenant_id')) or int(actor) != int(profile.get('telegram_id') or 0):
            raise CRMListError('crm_context_changed', 409)
    for key in ('pipeline_id', 'status_id'):
        values = params.getall(key, []) if hasattr(params, 'getall') else (
            [params[key]] if key in params else [])
        if any(not isinstance(value, str) or not value or len(value) > 120 for value in values):
            raise CRMListError('crm_invalid_query')
        result[key+'s'] = values
    return result


def _require_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''SELECT bool_and(to_regclass(name) IS NOT NULL) AS ready
          FROM unnest(%s::text[]) AS required(name)''', ([
            'saas_tenants', 'saas_tenant_members', 'saas_tenant_workflow_policies',
            'saas_tenant_pipelines', 'saas_tenant_pipeline_stages',
            'saas_crm_native_companies', 'saas_crm_native_contacts', 'saas_crm_native_deals',
            'saas_crm_deals', 'saas_crm_messages'],))
        if not (cur.fetchone() or {}).get('ready'):
            raise CRMListError('crm_unavailable', 503)


def _policy(cur, tenant, actor):
    # Existing workflow/member writers use this same order. Pipeline/stage
    # reads remain unlocked and share the transaction's coherent RR snapshot.
    cur.execute('SELECT id,modules,onboarding FROM saas_tenants WHERE id=%s::uuid FOR SHARE', (tenant,))
    company = cur.fetchone()
    if not company:
        raise CRMListError('crm_forbidden', 403)
    cur.execute('''SELECT policy_key FROM saas_tenant_workflow_policies
      WHERE tenant_id=%s::uuid AND policy_key=ANY(%s::text[])
      ORDER BY policy_key FOR SHARE''', (tenant, ['members', 'modules']))
    cur.fetchall()
    cur.execute('''SELECT tenant_id,telegram_id,display_name,role,permissions,active
      FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE''', (tenant, actor))
    member = cur.fetchone()
    if not member or member.get('active') is not True:
        raise CRMListError('crm_forbidden', 403)
    return TenantPolicy({**native_policy_profile(dict(member)), 'tenant_id': tenant,
        'modules': company.get('modules') or {}, 'onboarding': company.get('onboarding') or {},
        'workflow': _read_tenant_workflow(cur, tenant)})


def _attempt(profile, operation):
    tenant, actor = _uuid(profile.get('tenant_id')), int(_positive(profile.get('telegram_id')))
    for attempt in range(2):
        try:
            with _connect() as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                _require_schema(conn)
                with conn.cursor() as cur:
                    return operation(cur, _policy(cur, tenant, actor))
        except SerializationFailure:
            if attempt:
                raise CRMListError('crm_unavailable', 503) from None


def current_profile(profile):
    return _attempt(profile, lambda _cur, policy: policy.profile)


def _authorize(policy, entity, source):
    allowed = (policy.allows_action('customers' if entity == 'company' else 'deals', 'view')
               if source == 'native' else
               bool((policy.allows('deals') or policy.allows('customers')) and policy.pipeline_scope()))
    if not allowed:
        raise CRMListError('crm_forbidden', 403)


def _actions(policy, entity, source):
    if source == 'saved':
        return dict(view=True, create=False, update=False, archive=False,
                    sync=policy.privileged, view_chat=True)
    module = 'customers' if entity == 'company' else 'deals'
    result = dict(view=True, create=policy.allows_action(module, 'create'),
        update=policy.allows_action(module, 'update'), archive=policy.allows_action(module, 'delete'),
        view_contacts=policy.allows_action('customers', 'view'))
    if entity == 'company':
        result.update(assign=policy.allows_action('customers', 'assign'),
                      view_deals=policy.allows_action('deals', 'view'))
    else:
        result.update(assign_company=policy.allows_action('customers', 'assign'),
            unlink_company=policy.allows_action('customers', 'delete'),
            link_contact=policy.allows_action('deals', 'update'),
            view_companies=policy.allows_action('customers', 'view'))
    return result


def _needle(search):
    value = str(search or '').strip()[:160]
    return value, '%'+value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')+'%'


def _native_where(tenant, entity, search, pipeline_ids, status_ids):
    _, needle = _needle(search)
    if entity == 'company':
        if pipeline_ids or status_ids:
            raise CRMListError('crm_invalid_query')
        return "c.tenant_id=%s::uuid AND c.archived_at IS NULL AND (c.name ILIKE %s ESCAPE '\\' OR c.phone ILIKE %s ESCAPE '\\' OR c.email ILIKE %s ESCAPE '\\')", [tenant, needle, needle, needle]
    where = "d.tenant_id=%s::uuid AND d.archived_at IS NULL AND (d.title ILIKE %s ESCAPE '\\' OR c.display_name ILIKE %s ESCAPE '\\')"
    args = [tenant, needle, needle]
    if pipeline_ids:
        where += ' AND d.pipeline_id=ANY(%s::text[])'; args.append(pipeline_ids)
    if status_ids:
        where += ' AND d.stage_id=ANY(%s::text[])'; args.append(status_ids)
    return where, args


_COMPANY_FROM = '''FROM saas_crm_native_companies c LEFT JOIN saas_tenant_members m
  ON m.tenant_id=c.tenant_id AND m.telegram_id=c.responsible_id'''
_COMPANY_FIELDS = '''c.id,c.name,c.phone,c.email,c.description,c.responsible_id,
  m.display_name AS responsible_name,c.created_at,c.updated_at,
  (SELECT count(*) FROM saas_crm_native_contacts x WHERE x.tenant_id=c.tenant_id
    AND x.company_id=c.id AND x.archived_at IS NULL) AS contact_count,
  (SELECT count(*) FROM saas_crm_native_deals x WHERE x.tenant_id=c.tenant_id
    AND x.company_id=c.id AND x.archived_at IS NULL) AS deal_count'''
_DEAL_FROM = '''FROM saas_crm_native_deals d
  LEFT JOIN saas_crm_native_contacts c ON c.tenant_id=d.tenant_id AND c.id=d.contact_id
  LEFT JOIN saas_crm_native_companies co ON co.tenant_id=d.tenant_id AND co.id=d.company_id
  LEFT JOIN saas_tenant_members m ON m.tenant_id=d.tenant_id AND m.telegram_id=d.responsible_id
  LEFT JOIN saas_tenant_pipelines p ON p.tenant_id=d.tenant_id AND p.pipeline_id=d.pipeline_id
  LEFT JOIN saas_tenant_pipeline_stages s ON s.tenant_id=d.tenant_id
    AND s.pipeline_id=d.pipeline_id AND s.stage_id=d.stage_id'''
_DEAL_FIELDS = '''d.id,d.title,d.pipeline_id,d.stage_id,d.contact_id,c.display_name AS contact_name,
  d.company_id,co.name AS company_name,d.responsible_id,m.display_name AS responsible_name,
  d.channel_id,d.conversation_id,d.description,d.created_at,d.updated_at,
  p.name AS pipeline_name,s.name AS stage_name'''


def _saved_where(policy, search, pipeline_ids=(), status_ids=()):
    where, args = deal_filters(tenant_id=policy.profile['tenant_id'],
        pipeline_ids=[int(_positive(value)) for value in pipeline_ids],
        status_ids=[int(_positive(value)) for value in status_ids], scope=policy.pipeline_scope())
    value, needle = _needle(search)
    if value:
        where += " AND (name ILIKE %s ESCAPE '\\' OR contact_name ILIKE %s ESCAPE '\\' OR phone ILIKE %s ESCAPE '\\' OR stage_name ILIKE %s ESCAPE '\\')"
        args.extend([needle]*4)
    return where, args


def _row(row, source, entity):
    row = public(dict(row))
    row.update(source=source, kind=entity)
    if source == 'saved':
        row['id'] = row['kommo_lead_id'] = str(row['kommo_lead_id'])
        row['stage_name'] = stage_display_name(row.get('status_id'),row.get('stage_name'))
        for key in ('pipeline_id', 'status_id'):
            row[key] = str(row[key])
        raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
        channel = str(row.get('channel') or '').casefold()
        sources = [str(raw.get(key) or '').casefold() for key in ('source', 'provider')]
        row['provenance'] = ('connect' if channel=='connect' or channel.startswith('connect:') or 'connect' in sources else
                             'kommo' if channel == 'kommo' or 'kommo' in sources else 'unknown')
    return row


def read(profile, *, entity, source='native', identifier=None, search='',
         pipeline_ids=(), status_ids=(), limit=None, offset=0, chat=False):
    if entity not in ('company', 'deal') or source not in ('native', 'saved') or entity == 'company' and source != 'native':
        raise CRMListError('crm_invalid_query')
    cap = 50 if source == 'native' else 100
    try:
        limit = cap if limit is None else int(limit)
        offset = int(offset)
    except (TypeError, ValueError):
        raise CRMListError('crm_invalid_query') from None
    if not 1 <= limit <= cap or not 0 <= offset <= 2147483647 or source == 'native' and offset:
        raise CRMListError('crm_invalid_query')
    if chat and (not identifier or source != 'saved'):
        raise CRMListError('crm_invalid_query')
    if identifier is not None:
        identifier = _uuid(identifier) if source == 'native' else _positive(identifier)
    search, _ = _needle(search)
    def operation(cur, policy):
        _authorize(policy, entity, source)
        result = dict(entity=entity, source=source, actions=_actions(policy, entity, source),
                      scope='tenant' if source == 'native' else 'pipelines', historical=source == 'saved')
        if source == 'saved':
            where, args = _saved_where(policy, '' if identifier else search, pipeline_ids, status_ids)
            if identifier:
                where += ' AND kommo_lead_id=%s'; args.append(identifier)
            tables, fields, order = 'FROM saas_crm_deals', '*', 'last_message_at DESC NULLS LAST,source_updated_at DESC NULLS LAST,synced_at DESC,kommo_lead_id'
        else:
            where, args = _native_where(policy.profile['tenant_id'], entity, '' if identifier else search,
                                        pipeline_ids, status_ids)
            alias = 'c' if entity == 'company' else 'd'
            if identifier:
                where += ' AND '+alias+'.id=%s::uuid'; args.append(identifier)
            tables, fields = (_COMPANY_FROM, _COMPANY_FIELDS) if entity == 'company' else (_DEAL_FROM, _DEAL_FIELDS)
            order = alias+'.updated_at DESC,'+alias+'.id'
        if identifier:
            cur.execute('SELECT '+fields+' '+tables+' WHERE '+where, args)
            row = cur.fetchone()
            if not row:
                raise CRMListError('crm_not_found', 404)
            row = _row(row, source, entity)
            if chat:
                cur.execute('''SELECT * FROM (
                  SELECT external_id,direction,channel,author_name,body,message_type,media_url,happened_at,created_at
                  FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
                  ORDER BY COALESCE(happened_at,created_at) DESC,external_id DESC LIMIT 120) recent
                  ORDER BY COALESCE(happened_at,created_at),external_id''', (policy.profile['tenant_id'], identifier))
                messages = [dict(public(dict(item)), raw={}) for item in cur.fetchall()]
                result.update(messages=messages, stale=False, warning='', state='ready' if messages else 'empty')
            else:
                if entity == 'company':
                    cur.execute('''SELECT id,display_name,phone,email FROM saas_crm_native_contacts
                      WHERE tenant_id=%s::uuid AND company_id=%s::uuid AND archived_at IS NULL ORDER BY display_name,id''', (policy.profile['tenant_id'], identifier))
                    row['contacts'] = public(cur.fetchall())
                    cur.execute('''SELECT id,title,pipeline_id,stage_id,contact_id,responsible_id
                      FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND company_id=%s::uuid
                      AND archived_at IS NULL ORDER BY updated_at DESC,id''', (policy.profile['tenant_id'], identifier))
                    row['deals'] = public(cur.fetchall())
                result[entity] = row
        else:
            cur.execute('SELECT count(*) AS total '+tables+' WHERE '+where, args)
            total = int(cur.fetchone()['total'])
            cur.execute('SELECT '+fields+' '+tables+' WHERE '+where+' ORDER BY '+order+' LIMIT %s OFFSET %s', [*args, limit, offset])
            rows = [_row(row, source, entity) for row in cur.fetchall()]
            result.update({('companies' if entity == 'company' else 'deals'): rows},
                total=total, shown=len(rows), limit=limit, offset=offset,
                has_more=offset+len(rows)<total, search=search,
                state='ready' if total else 'no_matches' if search or pipeline_ids or status_ids else 'empty')
        return result
    return _attempt(profile, operation)
