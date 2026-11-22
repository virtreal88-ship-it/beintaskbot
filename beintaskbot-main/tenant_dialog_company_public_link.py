"""Read the existing Operator link for the current company of an exact dialogue."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

import tenant_beinsystems_operator as operator
import tenant_dialog_company_binding as dialogue
from tenant_dialog_access import require_source
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access


_ENDPOINT = 'dialog_company_public_link'
_API_PATH = '/task/getPublicLink'
_SHORT_URL = re.compile(r'https://1000\.az/[A-Za-z0-9_-]{6}', re.ASCII)
_MAX_RESULT_BYTES = 16_384


def _public_link(response, account):
    """Accept only the observed Operator receipt shape and pinned short-link origin."""
    if not isinstance(response, dict) or response.get('status') != 'success':
        raise operator.OperatorError('Operator не подтвердил публичную ссылку.', 502)
    data = response.get('data')
    if 'data' in response and data is None:
        return None
    if not isinstance(data, dict) or ('account' in data and data['account'] != account):
        raise operator.OperatorError('Operator не подтвердил публичную ссылку.', 502)
    url = data.get('short_url')
    if isinstance(url, str) and _SHORT_URL.fullmatch(url):
        return url
    raise operator.OperatorError('Operator вернул некорректную публичную ссылку.', 502)


def _lookup_public_link(account, *, token, base_url, opener=None):
    """Use only Operator's read endpoint, with no redirects or issuance fallback."""
    if base_url != operator.OPERATOR_API_BASE_URL or base_url != operator.api_base_url():
        raise operator.OperatorError('Адрес Operator API не настроен.', 503)
    query = urlencode({'account': account})
    request = Request(f'{base_url}{_API_PATH}?{query}', method='GET', headers={
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/json',
    })
    try:
        with (opener or build_opener(operator._NoRedirect())).open(
                request, timeout=operator.REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESULT_BYTES + 1)
        if len(raw) <= _MAX_RESULT_BYTES:
            return _public_link(json.loads(raw.decode('utf-8')), account)
    except HTTPError as error:
        if error.code in (401, 403):
            raise operator.OperatorError('Operator не разрешил прочитать ссылку.', 424) from None
        if error.code == 404:
            return None
        if 400 <= error.code < 500:
            raise operator.OperatorError('Operator отклонил запрос ссылки.', 424) from None
    except (URLError, TimeoutError, OSError, ValueError):
        pass
    raise operator.OperatorError('Чтение ссылки Operator не подтверждено.', 502)


def _linked_account(cur, tenant, company, generation, instance):
    # Both reviewed identity paths are valid only for this connection generation.
    # Reject ambiguous mappings, including conflicting active explicit links.
    cur.execute("""SELECT external_account AS account,
                         last_seen_generation=%s AND last_seen_connection_instance_id=%s::uuid AS current
       FROM saas_beinsystems_operator_company_imports
       WHERE tenant_id=%s::uuid AND company_id=%s::uuid
       UNION ALL
       SELECT external_id AS account,
              connection_generation=%s AND connection_instance_id=%s::uuid AS current
       FROM saas_beinsystems_operator_links
       WHERE tenant_id=%s::uuid AND local_entity_type='company' AND local_entity_id=%s::uuid
         AND external_entity_type='account' AND external_scope_id='' AND revoked_at IS NULL
       LIMIT 101""", (generation, instance, tenant, company, generation, instance, tenant, company))
    rows = cur.fetchall()
    accounts = {row['account'] for row in rows}
    if len(rows) > 100 or len(accounts) != 1 or not any(row['current'] for row in rows):
        return None
    account = accounts.pop()
    if (type(account) is str and 0 < len(account) <= 240 and account.strip() == account
            and all(ord(c) >= 32 and ord(c) != 127 for c in account)):
        cur.execute("""SELECT EXISTS (
                SELECT 1 FROM saas_beinsystems_operator_company_imports
                WHERE tenant_id=%s::uuid AND lower(external_account)=lower(%s)
                  AND company_id<>%s::uuid
            ) OR EXISTS (
                SELECT 1 FROM saas_beinsystems_operator_links
                WHERE tenant_id=%s::uuid AND lower(external_id)=lower(%s)
                  AND local_entity_type='company' AND local_entity_id<>%s::uuid
                  AND external_entity_type='account' AND external_scope_id='' AND revoked_at IS NULL
            ) AS conflict""", (tenant, account, company, tenant, account, company))
        if not (cur.fetchone() or {}).get('conflict', True):
            return account
    return None


def _snapshot(profile, route, expected_company):
    """Recheck route, current grants, exact binding and link generation."""
    with dialogue._connect() as conn:
        with conn.cursor() as cur:
            tenant, actor, policy = access(cur, profile, 'dialogs', 'view')
            if (policy.profile.get('tenant_status') != 'active'
                    or not policy.allows_action('operator_data', 'view')):
                raise operator.OperatorError('Публичная ссылка недоступна.', 403)
            access(cur, profile, 'dialogs', 'update')
            # Retain the existing link-access policy. A closed, owned dialogue
            # remains eligible without starting another conversation.
            require_source(cur, tenant, policy, route[0], route[2], 'update')
            dialogue._prove(cur, tenant, policy, route)
            table, _, _ = dialogue._pred(tenant, route)
            if not dialogue._ready(cur, table):
                raise operator.OperatorError('Привязка компании недоступна.', 503)
            binding = dialogue._read(cur, tenant, actor, route, False)
            if binding['company_id'] != expected_company:
                raise operator.OperatorError('Компания диалога изменилась или недоступна.', 409)
            integration = operator._operator_row(cur, tenant)
            generation, instance = operator._link_connection_scope(integration)
            if not generation or not instance or integration.get('status') != 'connected':
                return 'not_connected'
            account = _linked_account(cur, tenant, expected_company, generation, instance)
            return (tenant, account, generation, instance) if account else 'not_linked'


def lookup_link(profile, *, provider, lead_id, channel_id, conversation_id, expected_company_id):
    operator._tenant(profile)
    route = dialogue._key(provider, lead_id, channel_id, conversation_id)
    company = dialogue.normalize_company_id(expected_company_id)
    if company is None:
        raise operator.OperatorError('Компания диалога не указана.')
    try:
        before = _snapshot(profile, route, company)
        if isinstance(before, str):
            return {'state': before}
        tenant, account, generation, instance = before
        credentials = operator._credentials(tenant)
        if (credentials['generation'], credentials['connection_instance_id']) != (generation, instance):
            raise operator.OperatorError('Подключение Operator изменилось.', 409)
        operator._record_operator_read(profile, _ENDPOINT, {'account': account})
        if _snapshot(profile, route, company) != before:
            raise operator.OperatorError('Компания или подключение Operator изменились.', 409)
        # The account never comes from the request or the company display name.
        url = _lookup_public_link(account, token=credentials['token'], base_url=credentials['base_url'])
        if _snapshot(profile, route, company) != before:
            raise operator.OperatorError('Компания или подключение Operator изменились.', 409)
        return {'state': 'available', 'url': url} if url else {'state': 'not_found'}
    except TenantLinearError as error:
        raise operator.OperatorError(str(error), error.status) from error
