"""Choose a verified Operator account for one exact BeinSystems dialogue."""
import hashlib
import uuid

import tenant_beinsystems_operator as operator
import tenant_beinsystems_operator_company_import as imports
import tenant_dialog_company_binding as dialogue
from tenant_beinsystems_operator_access import dialogue_chooser_enabled
import tenant_dialog_operator_account as accounts
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access


_LIMIT = 5


def _term(value, *, exact=False):
    if not isinstance(value,str) or any(ord(ch)<32 or ord(ch)==127 for ch in value):
        raise operator.OperatorError('Некорректный запрос Operator.')
    term=value.strip()
    if not 2<=len(term)<=120 or (exact and term!=value):
        raise operator.OperatorError('Укажите от 2 до 120 символов аккаунта Operator.')
    return term


def _enumerate(response, limit):
    if (not isinstance(response,dict) or set(response)!={'status','data'} or response['status']!='success'
            or not isinstance(response['data'],list) or len(response['data'])>limit):
        raise operator.OperatorError('Operator не вернул подтверждённый список аккаунтов.',502)
    accounts=[]
    for item in response['data']:
        value=item.get('account') if isinstance(item,dict) else None
        if (not isinstance(value,str) or not value or value.strip()!=value or len(value)>240
                or any(ord(ch)<32 or ord(ch)==127 for ch in value)):
            raise operator.OperatorError('Operator вернул некорректный идентификатор аккаунта.',502)
        if 2<=len(value)<=120 and value not in accounts:
            accounts.append(value)
    return accounts


def _authorized(cur,profile,route):
    tenant,actor,policy=access(cur,profile,'dialogs','view')
    if not dialogue_chooser_enabled(tenant):
        raise operator.OperatorError('Выбор аккаунта Operator пока недоступен.',404)
    if policy.profile.get('tenant_status')!='active' or not policy.allows_action('operator_data','view'):
        raise operator.OperatorError('Нет доступа к аккаунтам Operator.',403)
    access(cur,profile,'dialogs','update')
    if not dialogue._operator_choice_allowed(cur,tenant,policy,route,enforce=True):
        raise operator.OperatorError('Нет доступа к выбору аккаунта в этом диалоге.',403)
    dialogue._prove(cur,tenant,policy,route)
    if not accounts.ready(cur):
        raise operator.OperatorError('Привязка компании для этого источника недоступна.',503)
    return tenant,actor,policy


def _read_live(profile, route, term, *, choice, selection_search=None):
    # Do not disclose provider data or spend upstream calls for a forged route.
    with dialogue._connect() as conn:
        with conn.cursor() as cur:
            expected_tenant, expected_actor, policy = _authorized(cur,profile,route)
    owner = policy.profile.get('role') == 'owner'
    if owner:
        tenant,actor,source,credentials=imports._selected_source_credentials(profile)
    else:
        tenant,actor,source=expected_tenant,expected_actor,'active'
        credentials=operator._credentials(tenant)
    if ((tenant,actor)!=(expected_tenant,expected_actor)
            or source not in {'active','accounts_only'}
            or (source=='active' and credentials.get('status')!='connected')):
        raise operator.OperatorError('Сначала подтвердите действующий доступ к аккаунтам Operator.',409)
    if owner:
        imports._verify_selected_identity(profile,tenant,actor,source,credentials)
    operator._record_operator_read(profile,'accounts',{'account':term} if choice else {})
    if owner:
        imports._selected_source_fence(profile,tenant,actor,source,credentials)
    # The search can rank similar accounts first. Re-run the bounded search
    # that exposed the choice and require the unchanged account ID in it.
    query = selection_search if choice and selection_search is not None else term
    try:
        response=operator._request_operator('accounts',{'search':query,'limit':_LIMIT},
            token=credentials['token'],base_url=credentials['base_url'])
    except operator.OperatorError as error:
        if error.upstream_http_status in (401,403):
            raise operator.OperatorError('Operator не разрешил поиск аккаунтов.',424) from None
        raise
    accounts=_enumerate(response,_LIMIT)
    if choice and term not in accounts:
        raise operator.OperatorError('Operator не подтвердил точное совпадение выбранного аккаунта.',409)
    return tenant,actor,source,credentials,accounts


def _fenced_local(cur,profile,route,tenant,actor,source,credentials):
    # Same source/row ordering as the authorized single-account import. The
    # dialogue, credential, and identity are not trusted from the earlier read.
    operator._lock_accounts_capability_scope(cur,tenant)
    if source=='accounts_only':
        imports._owner(profile,operator._current_profile(cur,tenant,actor))
    imports._lock_and_check_selected_source(cur,tenant,actor,source,credentials)
    if source=='active':
        row=operator._operator_row(cur,tenant,lock=True)
        if not row or row.get('status')!='connected':
            raise operator.OperatorError('Доступ к аккаунтам Operator изменился. Повторите поиск.',409)
    checked_tenant,checked_actor,policy=_authorized(cur,profile,route)
    if (checked_tenant,checked_actor)!=(tenant,actor):
        raise operator.OperatorError('Контекст сотрудника изменился. Обновите страницу.',409)
    return policy


def _assert_fresh(cur,tenant,actor,source,credentials):
    if source=='accounts_only':
        cur.execute("""SELECT 1 FROM saas_beinsystems_operator_accounts_capabilities
          WHERE tenant_id=%s::uuid AND owner_telegram_id=%s AND expires_at>clock_timestamp()""",(tenant,actor))
        if not cur.fetchone():
            raise operator.OperatorError('Доступ к аккаунтам Operator истёк. Подтвердите его заново.',409)


def chooser(profile, *, provider, lead_id, channel_id, conversation_id, term, choice=False,
            expected_company_id=dialogue._UNCHECKED, selection_search=None):
    route=dialogue._key(provider,lead_id,channel_id,conversation_id)
    term=_term(term,exact=choice)
    if selection_search is not None:
        if not choice:
            raise operator.OperatorError('Некорректный запрос Operator.')
        selection_search=_term(selection_search)
    if expected_company_id is not dialogue._UNCHECKED:
        dialogue.normalize_company_id(expected_company_id)
    try:
        tenant,actor,source,credentials,verified=_read_live(profile,route,term,choice=choice,selection_search=selection_search)
        with dialogue._connect() as conn, conn.cursor() as cur:
            policy=_fenced_local(cur,profile,route,tenant,actor,source,credentials)
            _assert_fresh(cur,tenant,actor,source,credentials)
            if not choice:
                return {'accounts':[{'account':account} for account in verified]}
            current=accounts.row(cur,tenant,route,lock=True)
            actual=current['id'] if current and current['account'] else None
            if expected_company_id is dialogue._UNCHECKED:
                if policy.profile.get('role')!='owner' or (actual and current['account']!=term):
                    raise operator.OperatorError('Обновите диалог перед выбором компании.',409)
            else:
                dialogue.assert_expected_company(actual,expected_company_id)
            saved=accounts.save(cur,tenant,actor,route,current,term,
                                credentials['generation'],credentials['connection_instance_id'])
            if not current or current['id']!=saved['id']:
                account_hash=hashlib.sha256(term.encode('utf-8')).hexdigest()
                cur.execute("""INSERT INTO saas_tenant_audit_events
                  (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'operator_dialog_company_selected','operator_endpoint',%s,%s::jsonb)""",
                  (str(uuid.uuid4()),tenant,actor,account_hash,
                   operator._json({'account_hash':account_hash,'source':source})))
            result={'binding':accounts.snapshot(cur,tenant,policy,route,True,record=saved)}
            conn.commit()
            return result
    except TenantLinearError as error:
        raise operator.OperatorError(str(error),error.status) from error


def unlink(profile, *, provider, lead_id, channel_id, conversation_id, expected_company_id):
    route=dialogue._key(provider,lead_id,channel_id,conversation_id)
    expected=dialogue.normalize_company_id(expected_company_id)
    if not expected:
        raise operator.OperatorError('Обновите диалог перед снятием привязки.',409)
    try:
        with dialogue._connect() as conn, conn.cursor() as cur:
            tenant,actor,policy=_authorized(cur,profile,route)
            current=accounts.row(cur,tenant,route,lock=True)
            dialogue.assert_expected_company(current['id'] if current and current['account'] else None,expected)
            saved=accounts.save(cur,tenant,actor,route,current,None)
            result=accounts.snapshot(cur,tenant,policy,route,True,record=saved)
            conn.commit()
            return result
    except TenantLinearError as error:
        raise operator.OperatorError(str(error),error.status) from error
