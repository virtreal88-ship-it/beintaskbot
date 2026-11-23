"""One verified Operator account in one exact BeinSystems CRM dialogue."""
import hashlib
import uuid

import tenant_beinsystems_operator as operator
import tenant_beinsystems_operator_company_import as imports
import tenant_dialog_company_binding as dialogue
from tenant_beinsystems_operator_access import dialogue_chooser_enabled
from tenant_crm_native_access import CRMAccessError, authorize_create, current_access, locked_resource
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from tenant_operator_company_name_guard import lock_name_writes


_LIMIT = 5
_CONFLICT = 'Аккаунт нельзя однозначно связать с компанией. Проверьте существующую связь в CRM.'


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
    table,_,_=dialogue._pred(tenant,route)
    if not dialogue._ready(cur,table):
        raise operator.OperatorError('Привязка компании для этого источника недоступна.',503)
    return tenant,actor,policy


def _read_live(profile, route, term, *, choice):
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
    limit=1 if choice else _LIMIT
    # Same documented, bounded read as the existing exact-account workflow.
    # For accounts-only, /me was verified against the stored fingerprint.
    try:
        response=operator._request_operator('accounts',{'search':term,'limit':limit},
            token=credentials['token'],base_url=credentials['base_url'])
    except operator.OperatorError as error:
        if error.upstream_http_status in (401,403):
            raise operator.OperatorError('Operator не разрешил поиск аккаунтов.',424) from None
        raise
    accounts=_enumerate(response,limit)
    if choice and accounts!=[term]:
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


def _resolve(cur,tenant,account,credentials):
    maps,prior=imports._first_batch_prior_identity(cur,tenant,[account])
    cur.execute("""SELECT local_entity_id::text AS company_id FROM saas_beinsystems_operator_links
      WHERE tenant_id=%s::uuid AND local_entity_type='company' AND external_entity_type='account'
        AND external_scope_id='' AND revoked_at IS NULL AND external_id=%s
        AND connection_generation=%s AND connection_instance_id=%s::uuid FOR UPDATE""",
      (tenant,account,credentials['generation'],credentials['connection_instance_id']))
    explicit={r['company_id'] for r in cur.fetchall()}
    mapped=maps.get(account)
    proven=explicit | ({mapped} if mapped else set())
    if len(proven)>1 or (prior.get(account,set())-proven):
        raise operator.OperatorError(_CONFLICT,409)
    if not proven:
        return None
    target=next(iter(proven))
    cur.execute("""SELECT EXISTS(SELECT 1 FROM saas_beinsystems_operator_company_imports
       WHERE tenant_id=%s::uuid AND company_id=%s::uuid AND external_account<>%s)
      OR EXISTS(SELECT 1 FROM saas_beinsystems_operator_links WHERE tenant_id=%s::uuid
       AND local_entity_type='company' AND local_entity_id=%s::uuid AND external_entity_type='account'
       AND external_scope_id='' AND revoked_at IS NULL AND external_id<>%s) AS other""",
      (tenant,target,account,tenant,target,account))
    if cur.fetchone()['other']:
        raise operator.OperatorError(_CONFLICT,409)
    return target


def _current_binding(cur,table,pred,key):
    cur.execute(f'SELECT company_id::text AS company_id FROM {table} WHERE {pred} FOR UPDATE',key)
    return (cur.fetchone() or {}).get('company_id')


def _bind_once(cur,route,table,pred,key,company,actor):
    fields='tenant_id,channel_id,conversation_id' if route[0]=='connect' else 'tenant_id,lead_id,channel_id,conversation_id'
    values='%s::uuid,%s::uuid,%s' if route[0]=='connect' else '%s::uuid,%s,%s,%s'
    cur.execute(f"""INSERT INTO {table}({fields},company_id,created_by,updated_by)
      VALUES({values},%s::uuid,%s,%s) ON CONFLICT({fields}) DO NOTHING RETURNING company_id""",
      (*key,company,actor,actor))
    if not cur.fetchone() and _current_binding(cur,table,pred,key)!=company:
        raise operator.OperatorError('Компания диалога изменилась. Обновите диалог.',409)


def _choose(cur,route,tenant,actor,source,credentials,account,expected):
    table,pred,key=dialogue._pred(tenant,route)
    bound=_current_binding(cur,table,pred,key)
    dialogue.assert_expected_company(bound,expected)
    company=_resolve(cur,tenant,account,credentials)
    if expected is dialogue._UNCHECKED and bound and bound!=company:
        raise operator.OperatorError('У диалога уже есть компания. Текущая привязка не изменена.',409)
    created=False
    if not company:
        create=current_access(cur,tenant,actor,'companies','create')
        authorize_create(cur,create,'companies',tenant,actor)
        # Matching names (including archived) are only reason to stop, never
        # identity evidence. All ordinary name writers share the same lock.
        cur.execute("""SELECT 1 FROM saas_crm_native_companies WHERE tenant_id=%s::uuid
          AND lower(name)=lower(%s) LIMIT 1""",(tenant,account))
        if cur.fetchone():
            raise operator.OperatorError(_CONFLICT,409)
        company=str(uuid.uuid4())
        cur.execute("""INSERT INTO saas_crm_native_companies
          (id,tenant_id,name,phone,email,description,responsible_id,access_responsible_id)
          VALUES(%s::uuid,%s::uuid,%s,'','','',NULL,%s) RETURNING id""",(company,tenant,account,actor))
        if not cur.fetchone() or not imports._put_mapping(cur,tenant,account,company,actor,credentials):
            raise operator.OperatorError('Связь аккаунта с CRM-компанией изменилась. Операция отменена.',409)
        created=True
    grant=current_access(cur,tenant,actor,'companies','view')
    if not locked_resource(cur,grant,'companies','view',dialogue.COMPANIES,tenant,company):
        raise CRMAccessError()
    if not created and source=='active':
        # Refresh only a pre-existing, exact imported identity after live re-verification.
        # A manually confirmed link remains its own sole identity record.
        cur.execute("""UPDATE saas_beinsystems_operator_company_imports SET
          last_seen_generation=%s,last_seen_connection_instance_id=%s::uuid,
          updated_by=%s,last_seen_at=now(),updated_at=now()
          WHERE tenant_id=%s::uuid AND external_account=%s AND company_id=%s::uuid
            AND (last_seen_generation,last_seen_connection_instance_id)
                IS DISTINCT FROM (%s,%s::uuid)""",
          (credentials['generation'],credentials['connection_instance_id'],actor,tenant,account,company,
           credentials['generation'],credentials['connection_instance_id']))
    _assert_fresh(cur,tenant,actor,source,credentials)
    if bound!=company:
        if expected is dialogue._UNCHECKED:
            _bind_once(cur,route,table,pred,key,company,actor)
        else:
            dialogue.write_binding(cur,route,table,pred,key,company,actor,expected)
        account_hash=hashlib.sha256(account.encode('utf-8')).hexdigest()
        cur.execute("""INSERT INTO saas_tenant_audit_events
          (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
          VALUES(%s,%s::uuid,%s,'operator_dialog_company_selected','operator_endpoint',%s,%s::jsonb)""",
          (str(uuid.uuid4()),tenant,actor,account_hash,
           operator._json({'account_hash':account_hash,'result':'created' if created else 'reused','source':source})))
    return {**dialogue._read(cur,tenant,actor,route,True),'can_search_operator':True}


def chooser(profile, *, provider, lead_id, channel_id, conversation_id, term, choice=False,
            expected_company_id=dialogue._UNCHECKED):
    tenant=operator._tenant(profile)
    if not dialogue_chooser_enabled(tenant):
        raise operator.OperatorError('Выбор аккаунта Operator пока недоступен.',404)
    route=dialogue._key(provider,lead_id,channel_id,conversation_id)
    term=_term(term,exact=choice)
    if expected_company_id is not dialogue._UNCHECKED:
        dialogue.normalize_company_id(expected_company_id)
    try:
        tenant,actor,source,credentials,accounts=_read_live(profile,route,term,choice=choice)
        with dialogue._connect() as conn:
            with conn.cursor() as cur:
                if choice and not lock_name_writes(cur,tenant):
                    raise operator.OperatorError('Выбор аккаунта Operator пока недоступен.',404)
                policy=_fenced_local(cur,profile,route,tenant,actor,source,credentials)
                if choice:
                    if policy.profile.get('role')!='owner' and expected_company_id is dialogue._UNCHECKED:
                        raise operator.OperatorError('Обновите диалог перед выбором компании.',409)
                    bound=_choose(cur,route,tenant,actor,source,credentials,term,expected_company_id)
                    bound['can_unlink']=dialogue._manage(cur,tenant,actor,policy,route,write=False)
                    result={'binding':bound}
                else:
                    _assert_fresh(cur,tenant,actor,source,credentials)
                    result={'accounts':[{'account':account} for account in accounts]}
            conn.commit()
        return result
    except TenantLinearError as error:
        raise operator.OperatorError(str(error),error.status) from error
    except CRMAccessError as error:
        if error.code=='company_name_busy':
            raise operator.OperatorError('Компании сейчас обновляются. Повторите выбор через несколько секунд.',409) from error
        raise operator.OperatorError('CRM-компания недоступна по вашим правам.',error.status) from error
