"""Read Operator details for the current company of an exact dialogue."""
import hashlib
import json
import math
import re
from urllib.parse import urlencode
from requests import RequestException

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


def _lookup_public_link(account, *, token, base_url):
    """Pinned, read-only GET with the same direct TLS transport as account search."""
    if base_url != operator.OPERATOR_API_BASE_URL or base_url != operator.api_base_url():
        raise operator.OperatorError('Адрес Operator API не настроен.', 503)
    url = f"{base_url}{_API_PATH}?{urlencode({'account': account})}"
    try:
        with operator._NoRedirectRequestsSession() as session:
            session.trust_env = False
            with session.get(url,headers={'Authorization':'Bearer '+token,'Accept':'application/json'},
                             timeout=operator.REQUEST_TIMEOUT_SECONDS,allow_redirects=False,stream=True) as reply:
                if reply.status_code in (401,403):
                    raise operator.OperatorError('Operator не разрешил прочитать ссылку.',424)
                if 400 <= reply.status_code < 500:
                    raise operator.OperatorError('Operator отклонил запрос ссылки.',424)
                if reply.status_code == 200:
                    raw = bytearray()
                    for chunk in reply.iter_content(4096):
                        if len(raw)+len(chunk)>_MAX_RESULT_BYTES:
                            break
                        raw.extend(chunk)
                    else:
                        return _public_link(json.loads(raw),account)
    except (RequestException,ValueError):
        pass
    raise operator.OperatorError('Чтение ссылки Operator не подтверждено.',502)


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


def _snapshot(profile, route, expected_company, **read_mode):
    """Recheck route, current grants, exact binding and link generation."""
    with dialogue._connect() as conn:
        with conn.cursor() as cur:
            return _locked_snapshot(cur,profile,route,expected_company,**read_mode)


def _locked_snapshot(cur,profile,route,expected_company,*,owner_read=False):
    tenant, actor, policy = access(cur, profile, 'dialogs', 'view')
    if (policy.profile.get('tenant_status') != 'active'
            or not policy.allows_action('operator_data', 'view')):
        raise operator.OperatorError('Публичная ссылка недоступна.', 403)
    overview = owner_read and policy.profile.get('role')=='owner'
    if overview:
        dialogue._prove(cur,tenant,policy,route,owner_overview=True)
    else:
        access(cur, profile, 'dialogs', 'update')
        require_source(cur, tenant, policy, route[0], route[2], 'update')
        dialogue._prove(cur, tenant, policy, route)
    from tenant_beinsystems_operator_access import dialogue_chooser_enabled
    if dialogue_chooser_enabled(tenant):
        from tenant_dialog_operator_account import ready, row
        if not ready(cur):
            raise operator.OperatorError('Привязка компании недоступна.',503)
        direct=row(cur,tenant,route,lock=True)
        if not direct or not direct['account'] or direct['id']!=expected_company:
            raise operator.OperatorError('Компания диалога изменилась или недоступна.',409)
        integration=operator._operator_row(cur,tenant)
        generation,instance=operator._link_connection_scope(integration)
        if not generation or not instance or integration.get('status')!='connected':
            return 'not_connected'
        if (generation,instance)!=(direct['generation'],direct['instance_id']):
            return 'not_linked'
        return tenant,direct['account'],generation,instance
    table, _, _ = dialogue._pred(tenant, route)
    if not dialogue._ready(cur, table):
        raise operator.OperatorError('Привязка компании недоступна.', 503)
    from tenant_beinsystems_operator_access import operator_api_allowed
    if operator_api_allowed(policy.profile):
        binding = dialogue._binding_snapshot(cur,tenant,policy,route,False)
    else:
        binding = dialogue._read(cur,tenant,actor,route,False)
    if binding['company_id'] != expected_company:
        raise operator.OperatorError('Компания диалога изменилась или недоступна.', 409)
    integration = operator._operator_row(cur, tenant)
    generation, instance = operator._link_connection_scope(integration)
    if not generation or not instance or integration.get('status') != 'connected':
        return 'not_connected'
    account = _linked_account(cur, tenant, expected_company, generation, instance)
    return (tenant, account, generation, instance) if account else 'not_linked'


def _action_binding_token(actor, route, company, scope):
    # Compare-only, never used to select the account, grant rights or sign in.
    payload = ['operator-company-action/v1',str(actor),list(route),company,list(scope)]
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _read_bound_route(profile, *, provider, lead_id, channel_id, conversation_id,
                      expected_company_id, endpoint, lookup, finish=None):
    operator._tenant(profile)
    route = dialogue._key(provider, lead_id, channel_id, conversation_id)
    company = dialogue.normalize_company_id(expected_company_id)
    if company is None:
        raise operator.OperatorError('Компания диалога не указана.')
    read_mode = {'owner_read':True} if profile.get('role')=='owner' else {}
    try:
        before = _snapshot(profile, route, company, **read_mode)
        if isinstance(before, str):
            return {'state': before}
        tenant, account, generation, instance = before
        credentials = operator._credentials(tenant)
        if (credentials['generation'], credentials['connection_instance_id']) != (generation, instance):
            raise operator.OperatorError('Подключение Operator изменилось.', 409)
        operator._record_operator_read(profile, endpoint, {'account': account})
        if _snapshot(profile, route, company, **read_mode) != before:
            raise operator.OperatorError('Компания или подключение Operator изменились.', 409)
        # The account never comes from the request or the company display name.
        fields = lookup(account, token=credentials['token'], base_url=credentials['base_url'])
        if finish is not None:
            with dialogue._connect() as conn, conn.cursor() as cur:
                if _locked_snapshot(cur,profile,route,company,**read_mode) != before:
                    raise operator.OperatorError('Компания или подключение Operator изменились.', 409)
                if fields is not None:
                    fields = finish(cur,route,company,before,fields)
        elif _snapshot(profile, route, company, **read_mode) != before:
            raise operator.OperatorError('Компания или подключение Operator изменились.', 409)
        return {'state': 'available', **fields} if fields is not None else {'state': 'not_found'}
    except TenantLinearError as error:
        raise operator.OperatorError(str(error), error.status) from error


def lookup_link(profile, **query):
    def link_fields(account, **transport):
        url = _lookup_public_link(account, **transport)
        return {'url': url} if url else None

    return _read_bound_route(profile, endpoint=_ENDPOINT, lookup=link_fields, **query)


def _issue(account,credentials):
    base=credentials['base_url']
    if base!=operator.OPERATOR_API_BASE_URL or base!=operator.api_base_url():
        raise operator.OperatorError('Operator configuration changed.',409)
    with operator._NoRedirectRequestsSession() as session:
        session.trust_env=False
        with session.post(base+'/task/publicLink',json={'account':account,'public_base_url':'https://operator.beinsystems.az'},
                          headers={'Authorization':'Bearer '+credentials['token'],'Accept':'application/json'},
                          timeout=operator.REQUEST_TIMEOUT_SECONDS,allow_redirects=False,stream=True) as reply:
            if reply.status_code in (401,403):
                raise operator.OperatorError('Operator denied the request.',424)
            if reply.status_code==200:
                raw=bytearray()
                for part in reply.iter_content(4096):
                    if len(raw)+len(part)>_MAX_RESULT_BYTES:
                        break
                    raw.extend(part)
                else:
                    url=_public_link(json.loads(raw),account)
                    if url:
                        return url
    raise operator.OperatorError('Operator issuance not confirmed.',502)


_KEY = 'tenant_id=%s::uuid AND instance_id=%s::uuid AND generation=%s AND account=%s'


def copy_link(profile, *, provider, lead_id, channel_id, conversation_id, expected_company_id):
    route=dialogue._key(provider,lead_id,channel_id,conversation_id)
    company=dialogue.normalize_company_id(expected_company_id)
    if not company:
        raise operator.OperatorError('Missing company.')
    try:
        scope=_snapshot(profile,route,company)
        if isinstance(scope,str):
            return {'state':scope}
        tenant,account,generation,instance=scope
        key=(tenant,instance,generation,account)
        credentials=operator._credentials(tenant)
        if (credentials['generation'],credentials['connection_instance_id'])!=(generation,instance):
            raise operator.OperatorError('Connection changed.',409)
        cipher=operator._fernet()
        operator._record_operator_read(profile,_ENDPOINT,{'account':account})
        with dialogue._connect() as conn,conn.cursor() as cur:
            if _locked_snapshot(cur,profile,route,company)!=scope:
                raise operator.OperatorError('Binding changed.',409)
            cur.execute("""INSERT INTO saas_operator_customer_links(tenant_id,instance_id,generation,account)
                VALUES(%s::uuid,%s::uuid,%s,%s) ON CONFLICT DO NOTHING RETURNING account""",key)
            reserved=bool(cur.fetchone())
            if not reserved:
                cur.execute('SELECT encrypted_url FROM saas_operator_customer_links WHERE '+_KEY,key)
                row=cur.fetchone()
                if not row or not row['encrypted_url']:
                    return {'state':'unconfirmed'}
                saved=cipher.decrypt(bytes(row['encrypted_url'])).decode()
                if not _SHORT_URL.fullmatch(saved):
                    raise operator.OperatorError('Invalid stored link.',502)
                return {'state':'available','url':saved}
            conn.commit()
        # A committed reservation prevents a second POST after timeout or worker restart.
        with dialogue._connect() as conn,conn.cursor() as cur:
            if _locked_snapshot(cur,profile,route,company)!=scope:
                cur.execute('DELETE FROM saas_operator_customer_links WHERE '+_KEY+' AND encrypted_url IS NULL',key)
                conn.commit()
                raise operator.OperatorError('Binding changed.',409)
            try:
                url=_issue(account,credentials)
            except operator.OperatorError as error:
                if error.status==424:
                    cur.execute('DELETE FROM saas_operator_customer_links WHERE '+_KEY+' AND encrypted_url IS NULL',key)
                    conn.commit()
                    raise
                return {'state':'unconfirmed'}
            except (ValueError, RequestException):
                return {'state':'unconfirmed'}
            cur.execute('UPDATE saas_operator_customer_links SET encrypted_url=%s WHERE '+_KEY,
                        (cipher.encrypt(url.encode()),*key))
            conn.commit()
        if _snapshot(profile,route,company)!=scope:
            raise operator.OperatorError('Binding changed.',409)
        return {'state':'available','url':url}
    except TenantLinearError as error:
        raise operator.OperatorError(str(error),error.status) from error


def _company_profile_fields(response, account):
    # Verify the returned account before projecting its company details.
    operator.account_info_payload(response, account)
    data = response.get('data', response)
    if not isinstance(data, dict):
        raise operator.OperatorError('Operator не подтвердил профиль компании.', 502)
    phone = data.get('phone')
    if not (isinstance(phone, str) and re.fullmatch(r'\+?[0-9 ()-]{7,24}', phone)
            and 7 <= sum(char.isdigit() for char in phone) <= 15):
        phone = None
    balance = data.get('balance')
    if type(balance) not in (int, float) or not -10**12 <= balance <= 10**12 or not math.isfinite(balance):
        balance = None
    result = {'registration_phone':phone, 'balance':balance}
    name = data.get('partner_name')
    if isinstance(name, str):
        name = re.sub(r'[\x00-\x1f\x7f]+',' ',name).strip()[:240].rstrip()
        if name:
            result['partner_name'] = name
    return result


_SECTIONS = {'company_tasks': ('/task/list', ('linear_id','title','status')),
             'company_modules': ('/user/modules', ('module',)),
             'company_history': ('/user/history', ('moment','amount','payment_source'))}


def _section_rows(response, section, account):
    if not isinstance(response,dict) or response.get('status')!='success' or not isinstance(response.get('data'),list):
        raise operator.OperatorError('Operator не подтвердил список.',502)
    fields=_SECTIONS[section][1]
    rows=[]
    for item in response['data']:
        if not isinstance(item,dict) or ('account' in item and item['account']!=account):
            raise operator.OperatorError('Operator не подтвердил данные выбранной компании.',502)
        if section=='company_tasks' and item.get('account')!=account:
            raise operator.OperatorError('Operator не подтвердил аккаунт задачи.',502)
        values=[item.get(field) for field in fields]
        if section=='company_tasks' and not values[0]:
            values[0]=item.get('id')
        if any(type(value) not in (str,int,float) or not str(value).strip() for value in values):
            raise operator.OperatorError('В ответе Operator отсутствуют поля списка.',502)
        row = [str(value)[:320] for value in values]
        # Optional currency is only projected when explicitly supplied by
        # Operator; amounts lacking currency are never assumed to be AZN.
        currency = item.get('currency')
        if section == 'company_history' and isinstance(currency,str) and re.fullmatch(r'[A-Z]{3}',currency):
            row.append(currency)
        rows.append(row)
    return {'rows':rows}


def lookup_section(profile, *, section, **query):
    path,_=_SECTIONS[section]
    def read(account, *, token, base_url):
        if base_url!=operator.OPERATOR_API_BASE_URL or base_url!=operator.api_base_url():
            raise operator.OperatorError('Подключение Operator изменилось.',409)
        try:
            raw=operator._post_read_with_requests(base_url+path,{'account':account},token,accept_json=True)
            return _section_rows(json.loads(raw),section,account)
        except operator.OperatorError as exc:
            if exc.upstream_http_status in (401,403):
                raise operator.OperatorError('Operator отклонил чтение данных.',424) from None
            raise
        except (ValueError,UnicodeError):
            raise operator.OperatorError('Operator вернул неизвестный формат.',502) from None
    return _read_bound_route(profile,endpoint=section,lookup=read,**query)


def lookup_profile(profile, **query):
    def profile_fields(account, **transport):
        response = operator._request_operator('account_info', {'account':account},
                                              requests_account_info=True, **transport)
        return _company_profile_fields(response, account)

    def with_actions(cur, route, company, scope, fields):
        from tenant_dialog_company_actions import PRIORITIES, PROJECTS, capabilities
        tenant, account, _, _ = scope
        actor = int(profile['telegram_id'])
        actions = capabilities(operator._current_profile(cur,tenant,actor))
        result = {**fields,'actions':actions}
        if any(actions.values()):
            result['action_binding'] = _action_binding_token(actor,route,company,scope)
        if actions['task']:
            result['task_options'] = {'account':account, 'priorities':PRIORITIES, 'projects':PROJECTS}
        return result

    return _read_bound_route(profile,endpoint='account_info',lookup=profile_fields,finish=with_actions,**query)
