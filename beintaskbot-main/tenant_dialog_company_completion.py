"""Independent opt-in for a verified local company on human completion."""
import os
import uuid
from tenant_beinsystems_operator_access import is_operator_workspace
from tenant_linear_policy import TenantLinearError

ENV = 'TENANT_DIALOG_COMPANY_COMPLETION_REQUIRED'
_BINDINGS = 'saas_beinsystems_operator_dialog_company_bindings'
_COMPANIES = 'saas_crm_native_companies'


def available(tenant):
    # Optional choices are private to BeinSystems and independent of the
    # historical rollout that requires selecting a company on legacy closes.
    return is_operator_workspace(tenant)


def enabled(tenant):
    return os.environ.get(ENV, '').lower() == 'true' and available(tenant)


def _ready(cur):
    from tenant_schema_preflight import ready
    return ready(cur, {
        _BINDINGS: ('tenant_id','channel_id','conversation_id','company_id','created_by','updated_by'),
        _COMPANIES: ('tenant_id','id','archived_at','access_responsible_id'),
    }, ())


def _binding(cur, tenant, channel, conversation, *, lock=False):
    cur.execute(f'''SELECT company_id::text AS company_id FROM {_BINDINGS}
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s'''
      + (' FOR SHARE' if lock else ''), (tenant, channel, conversation))
    return (cur.fetchone() or {}).get('company_id')


def _company_status(cur, tenant, company, *, lock=False):
    cur.execute(f'''SELECT archived_at IS NOT NULL AS archived FROM {_COMPANIES}
      WHERE tenant_id=%s::uuid AND id=%s::uuid''' + (' FOR SHARE' if lock else ''), (tenant, company))
    row = cur.fetchone()
    return ('archived' if row['archived'] else 'ready') if row else 'invalid'


def status(cur, tenant, channel, conversation, *, identity_conflict=False):
    if not _ready(cur):
        return 'unavailable'
    if identity_conflict:
        return 'identity_unresolved'
    company = _binding(cur, tenant, channel, conversation)
    return _company_status(cur,tenant,company) if company else 'missing'


def bulk_prelinked_routes(cur, tenant, actor):
    """Read-only link snapshot; dialogue identity is not verified in bulk."""
    if not _ready(cur):
        return set()
    cur.execute(f'''SELECT b.channel_id::text AS channel_id,b.conversation_id
      FROM {_BINDINGS} b JOIN {_COMPANIES} c
      ON c.tenant_id=b.tenant_id AND c.id=b.company_id AND c.archived_at IS NULL
      JOIN saas_dialog_workflows w ON w.tenant_id=b.tenant_id
        AND w.channel_id=b.channel_id AND w.conversation_id=b.conversation_id
        AND w.state='accepted' AND w.operator_id=%s
      WHERE b.tenant_id=%s::uuid''', (actor, tenant))
    return {(row['channel_id'],row['conversation_id']) for row in cur.fetchall()}


def can_preview(policy):
    # Restricted row scopes must not be able to infer a hidden company from
    # the difference between 'missing' and any other readiness.
    return bool(policy and policy.action_scope('companies','view') in {None,'all'}
                and policy.allows_action('companies','view'))


def can_choose(policy):
    return bool(policy and policy.allows_action('companies','view')
                and policy.allows_action('companies','update'))


def require_on_close(cur, tenant, actor, channel, conversation, payload, *, policy=None):
    """Under the authorized canonical lock; initialize or repair archived only.

    Existing valid bindings need no CRM access to close and reveal no company
    data. Selecting a company needs current independent scoped CRM permissions.
    A live or unknown binding cannot be replaced here.
    """
    supplied = 'company_id' in payload
    skip = 'company_choice' in payload
    # Old clients and all other tenants keep their ordinary close behavior.
    if not available(tenant):
        if supplied or skip:
            raise TenantLinearError('Выбор компании при завершении недоступен.',422)
        return None
    if skip and (supplied or payload['company_choice'] != 'none'):
        raise TenantLinearError('Выберите компанию или «Без компании».',422)
    opaque = not enabled(tenant) and not can_preview(policy)
    if not enabled(tenant) and (not (supplied or skip) or (opaque and skip)):
        # Same behavior as ordinary optional close, without probing whether
        # a company exists or is outside this actor's scope.
        return None
    if opaque and not can_choose(policy):
        raise TenantLinearError('Выбор компании при завершении недоступен.',422)
    choice = None
    if supplied:
        raw = payload['company_id']
        try:
            if not isinstance(raw,str):
                raise ValueError
            choice = str(uuid.UUID(raw))
            if choice != raw:
                raise ValueError
        except ValueError:
            raise TenantLinearError('Выберите CRM-компанию из списка.',422) from None
    if opaque and supplied:
        _require_company(cur,tenant,actor,choice)
    # 422 is recoverable in the existing UI; 403/404/409 remove the open route.
    if not _ready(cur):
        raise TenantLinearError('Проверка компании временно недоступна. Диалог не завершён.',422)
    from tenant_connect_identity import conflicted
    if conflicted(cur,tenant,channel,conversation):
        raise TenantLinearError('Сначала нужно проверить адрес диалога WhatsApp.',422)
    bound = _binding(cur,tenant,channel,conversation,lock=True)
    if bound:
        if opaque:
            # The actor cannot rely on knowing the current relationship. Do
            # not overwrite it, classify it, or return any of its metadata.
            raise TenantLinearError('Не удалось применить выбор компании. Завершите диалог без изменения привязки.',422)
        previous_status = _company_status(cur,tenant,bound,lock=True)
        if previous_status == 'invalid':
            raise TenantLinearError('Не удалось подтвердить текущую компанию диалога.',422)
        if previous_status == 'ready':
            # Reject any new selection for a live binding, including the same
            # value. Without CRM rights, guessing IDs must not reveal the link.
            # Successful exact retries already returned their stored receipt.
            if supplied:
                raise TenantLinearError('У диалога уже есть компания. Обновите диалог.',422)
            return None
    if skip and not bound:
        return None
    if not supplied:
        message = ('Компания диалога архивирована. Выберите действующую CRM-компанию.' if bound
                   else 'Перед завершением выберите CRM-компанию.')
        raise TenantLinearError(message,422)
    if not opaque:
        _require_company(cur,tenant,actor,choice)
    if bound:
        cur.execute(f'''UPDATE {_BINDINGS} SET company_id=%s::uuid,updated_by=%s,updated_at=now()
          WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
            AND company_id=%s::uuid RETURNING company_id''',
          (choice,actor,tenant,channel,conversation,bound))
    else:
        cur.execute(f'''INSERT INTO {_BINDINGS}
          (tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
          VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s)
          ON CONFLICT(tenant_id,channel_id,conversation_id) DO NOTHING RETURNING company_id''',
          (tenant,channel,conversation,choice,actor,actor))
    if not cur.fetchone():
        raise TenantLinearError('Компания диалога изменилась. Обновите диалог.',422)
    return {'previous_company_id':bound,'company_id':choice}


def _require_company(cur, tenant, actor, choice):
    from tenant_crm_native_access import CRMAccessError, current_access, locked_resource
    try:
        view=current_access(cur,tenant,actor,'companies','view')
        update=current_access(cur,tenant,actor,'companies','update')
        if not (locked_resource(cur,view,'companies','view',_COMPANIES,tenant,choice)
                and locked_resource(cur,update,'companies','update',_COMPANIES,tenant,choice)):
            raise CRMAccessError()
    except CRMAccessError:
        raise TenantLinearError('Компания недоступна для привязки по вашим правам.',422) from None
