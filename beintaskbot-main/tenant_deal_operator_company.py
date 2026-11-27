"""Verified Operator company details scoped to an individual CRM deal."""
import hashlib
import json
import re
import uuid

import tenant_beinsystems_operator as operator
from tenant_beinsystems_operator_access import dialogue_chooser_enabled
from tenant_crm_native_access import scoped_sql
from tenant_crm_relation_identity import current_binding
from tenant_dialog_company_binding import normalize_company_id
import tenant_dialog_company_operator as chooser
from tenant_dialog_company_public_link import link_fields, profile_fields, section_reader
from tenant_kommo_board_provenance import revisions
from tenant_member_access import page_capabilities
from tenant_policy import TenantPolicy


TABLE = 'saas_operator_deal_accounts'


def feature_for(profile):
    return bool(dialogue_chooser_enabled(profile.get('tenant_id'))
                and profile.get('active') is True and profile.get('tenant_status') == 'active'
                and TenantPolicy(profile).allows_action('operator_data', 'view')
                and page_capabilities(profile).get('deals'))


def target(source, deal_id):
    try:
        if not isinstance(deal_id, str):
            raise ValueError
        if source == 'native' and str(uuid.UUID(deal_id)) == deal_id:
            return source, deal_id
        if source == 'kommo' and re.fullmatch(r'[1-9][0-9]{0,18}', deal_id) and int(deal_id) < 2**63:
            return source, deal_id
    except ValueError:
        pass
    raise operator.OperatorError('Сделка недоступна.', 404)


def _deal_access(cur, profile, route):
    tenant = operator._tenant(profile)
    actor = int(profile['telegram_id'])
    current = operator._current_profile(cur, tenant, actor)
    if not feature_for({**current, 'tenant_id': tenant}):
        raise operator.OperatorError('Данные компании недоступны.', 403)
    source, ident = route
    policy = TenantPolicy(current)
    if source == 'native':
        view, view_args = scoped_sql(current, 'deals', 'view', 'd')
        update, update_args = scoped_sql(current, 'deals', 'update', 'd')
        cur.execute(f'''SELECT ({update}) AS can_manage
            FROM saas_crm_native_deals d WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid
              AND d.archived_at IS NULL AND ({view}) FOR SHARE OF d''',
            [*update_args, tenant, ident, *view_args])
        row = cur.fetchone()
        if row:
            return tenant, actor, policy, '', bool(row['can_manage'])
    else:
        binding = current_binding(cur, tenant, lock=False)
        cur.execute('''SELECT * FROM saas_crm_deals WHERE tenant_id=%s::uuid
            AND kommo_lead_id=%s AND deleted_at IS NULL FOR SHARE''', (tenant, int(ident)))
        row = cur.fetchone()
        if binding and policy.kommo_access() and policy.can_read_kommo_chat(row) and revisions(
                cur, tenant, 'leads', [ident], binding, expected_raw={ident: row['raw']}).get(ident):
            in_pipeline = any(int(row['pipeline_id']) == entry['pipeline_id'] and
                (not entry['status_ids'] or int(row['status_id']) in entry['status_ids'])
                for entry in policy.kommo_pipeline_scope())
            # An own-created read grant alone does not allow changing the binding.
            can_manage = bool(policy.allows_action('deals', 'update') and
                              (current.get('role') == 'owner' or in_pipeline)
                              and policy.action_scope('deals', 'update') in (None, 'all'))
            scope = json.dumps([binding.account_id, binding.connection_generation], separators=(',', ':'))
            return tenant, actor, policy, scope, can_manage
    raise operator.OperatorError('Сделка недоступна.', 404)


def _row(cur, tenant, route, scope, *, lock=False):
    cur.execute(f'''SELECT binding_id::text AS id,account,generation,instance_id::text
      FROM {TABLE} WHERE tenant_id=%s::uuid AND source=%s AND deal_id=%s AND deal_scope=%s'''
      + (' FOR UPDATE' if lock else ''), (tenant, *route, scope))
    return cur.fetchone()


def _projection(row, can_manage):
    has_company = row and row['account']
    return {'company_id': row['id'] if has_company else None,
            'company_name': row['account'] if has_company else None,
            'operator_account': row['account'] if has_company else None,
            'can_manage': can_manage, 'can_unlink': can_manage, 'can_search_operator': can_manage}


def binding(profile, route):
    with operator._connect() as conn, conn.cursor() as cur:
        tenant, _, _, scope, can_manage = _deal_access(cur, profile, route)
        return _projection(_row(cur, tenant, route, scope), can_manage)


def _save(cur, tenant, actor, route, scope, previous, account, generation=None, instance=None):
    if previous and (previous['account'], previous['generation'], previous['instance_id']) == (account, generation, instance):
        return previous
    cur.execute(f'''INSERT INTO {TABLE}(tenant_id,source,deal_id,deal_scope,
        binding_id,account,generation,instance_id,updated_by)
      VALUES(%s::uuid,%s,%s,%s,%s::uuid,%s,%s,%s::uuid,%s)
      ON CONFLICT(tenant_id,source,deal_id,deal_scope) DO UPDATE SET
        binding_id=EXCLUDED.binding_id,account=EXCLUDED.account,generation=EXCLUDED.generation,
        instance_id=EXCLUDED.instance_id,updated_by=EXCLUDED.updated_by,updated_at=clock_timestamp()
      WHERE {TABLE}.binding_id=%s::uuid
      RETURNING binding_id::text AS id,account,generation,instance_id::text''',
      (tenant, *route, scope, str(uuid.uuid4()), account, generation, instance, actor,
       previous['id'] if previous else None))
    saved = cur.fetchone()
    if not saved:
        raise operator.OperatorError('Компания сделки изменилась. Обновите карточку.', 409)
    return saved


def _expected(row, expected_company_id):
    if (row['id'] if row and row['account'] else None) != expected_company_id:
        raise operator.OperatorError('Компания сделки изменилась. Обновите карточку.', 409)


def select(profile, route, *, term, choice=False, selection_search=None, expected_company_id=None):
    term = chooser._term(term, exact=choice)
    if selection_search is not None:
        selection_search = chooser._term(selection_search)
    if choice:
        normalize_company_id(expected_company_id)
    scopes = []

    def authorize(cur, profile, route):
        tenant, actor, policy, scope, can_manage = _deal_access(cur, profile, route)
        if not can_manage:
            raise operator.OperatorError('Нет доступа к выбору компании в этой сделке.', 403)
        if scopes and scopes[0] != scope:
            raise operator.OperatorError('Подключение сделки изменилось.', 409)
        scopes.append(scope)
        return tenant, actor, policy

    tenant, actor, source, credentials, verified = chooser._read_live(
        profile, route, term, choice=choice, selection_search=selection_search, authorize=authorize)
    with operator._connect() as conn, conn.cursor() as cur:
        chooser._fenced_local(cur, profile, route, tenant, actor, source, credentials, authorize=authorize)
        chooser._assert_fresh(cur, tenant, actor, source, credentials)
        if not choice:
            return {'accounts': [{'account': account} for account in verified]}
        row = _row(cur, tenant, route, scopes[0], lock=True)
        _expected(row, expected_company_id)
        saved = _save(cur, tenant, actor, route, scopes[0], row, term,
                      credentials['generation'], credentials['connection_instance_id'])
        conn.commit()
        return {'binding': _projection(saved, True)}


def unlink(profile, route, expected_company_id):
    if not normalize_company_id(expected_company_id):
        raise operator.OperatorError('Обновите карточку перед снятием привязки.', 409)
    with operator._connect() as conn, conn.cursor() as cur:
        tenant, actor, _, scope, can_manage = _deal_access(cur, profile, route)
        if not can_manage:
            raise operator.OperatorError('Нет доступа к изменению компании сделки.', 403)
        row = _row(cur, tenant, route, scope, lock=True)
        _expected(row, expected_company_id)
        saved = _save(cur, tenant, actor, route, scope, row, None)
        conn.commit()
        return _projection(saved, True)


def _snapshot(profile, route, expected):
    with operator._connect() as conn, conn.cursor() as cur:
        tenant, _, _, scope, _ = _deal_access(cur, profile, route)
        row = _row(cur, tenant, route, scope)
        _expected(row, expected)
        integration = operator._operator_row(cur, tenant)
        generation, instance = operator._link_connection_scope(integration)
        if not generation or not instance or integration.get('status') != 'connected':
            return 'not_connected'
        if (row['generation'], row['instance_id']) != (generation, instance):
            return 'not_linked'
        return tenant, row['account'], generation, instance


def read(profile, route, expected_company_id, action):
    expected = normalize_company_id(expected_company_id)
    if not expected:
        raise operator.OperatorError('Компания сделки не указана.')
    before = _snapshot(profile, route, expected)
    if isinstance(before, str):
        return {'state': before}
    tenant, account, generation, instance = before
    if action == 'company_profile':
        endpoint, reader = 'account_info', profile_fields
    elif action == 'public_link':
        endpoint, reader = 'dialog_company_public_link', link_fields
    else:
        endpoint, reader = action, section_reader(action)
    credentials = operator._credentials(tenant)
    if (credentials['generation'], credentials['connection_instance_id']) != (generation, instance):
        raise operator.OperatorError('Подключение Operator изменилось.', 409)
    operator._record_operator_read(profile, endpoint, {'account': account})
    if _snapshot(profile, route, expected) != before:
        raise operator.OperatorError('Компания или подключение Operator изменились.', 409)
    fields = reader(account, token=credentials['token'], base_url=credentials['base_url'])
    if _snapshot(profile, route, expected) != before:
        raise operator.OperatorError('Компания или подключение Operator изменились.', 409)
    return {'state': 'available', **fields} if fields is not None else {'state': 'not_found'}
