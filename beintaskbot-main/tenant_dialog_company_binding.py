"""Exact, tenant-local company binding for a visible dialogue source."""
import uuid

from tenant_beinsystems_operator import OperatorError
from tenant_beinsystems_operator_access import is_operator_workspace
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect
from tenant_policy import TenantPolicy

CONNECT = 'saas_beinsystems_operator_dialog_company_bindings'
KOMMO = 'saas_beinsystems_kommo_dialog_company_bindings'
COMPANIES = 'saas_crm_native_companies'
PROVISION_ENV = 'BEINSYSTEMS_DIALOG_COMPANY_BINDINGS_PROVISION_ON_STARTUP'


def feature_for(profile):
    return bool(is_operator_workspace(profile.get('tenant_id'))
                and profile.get('active') is not False
                and profile.get('tenant_status') == 'active'
                and TenantPolicy(profile).allows_action('dialogs', 'view'))


def _ready(cur, table):
    from tenant_schema_preflight import ready
    cols = ('tenant_id', 'channel_id', 'conversation_id', 'company_id',
            'created_by', 'updated_by', 'created_at', 'updated_at')
    return ready(cur, {table: cols + (('lead_id',) if table == KOMMO else ()),
                       COMPANIES: ('tenant_id','id','name','archived_at','access_responsible_id')}, ())


def provision_kommo(cur):
    """Add one empty table on explicit, bounded startup, never during requests."""
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {KOMMO} (
        tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
        lead_id BIGINT NOT NULL CHECK(lead_id>0),
        channel_id TEXT NOT NULL CHECK(length(channel_id)<=256),
        conversation_id TEXT NOT NULL CHECK(length(conversation_id)<=256),
        company_id UUID NOT NULL,
        created_by BIGINT NOT NULL CHECK(created_by>0),
        updated_by BIGINT NOT NULL CHECK(updated_by>0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,lead_id,channel_id,conversation_id)
    )""")
    cur.execute("""SELECT c.relkind='r' AS regular, ARRAY(
        SELECT a.attname::text FROM unnest(p.conkey) WITH ORDINALITY AS k(num,ord)
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num
        ORDER BY k.ord) AS pk, ARRAY(
        SELECT a.attname::text || ':' || format_type(a.atttypid,a.atttypmod)
        FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0
          AND NOT a.attisdropped AND a.attnotnull ORDER BY a.attnum) AS cols
      FROM pg_class c JOIN pg_constraint p ON p.conrelid=c.oid AND p.contype='p'
      WHERE c.oid=to_regclass(%s)""", (KOMMO,))
    row = cur.fetchone() or {}
    cols = ['tenant_id:uuid','lead_id:bigint','channel_id:text','conversation_id:text',
            'company_id:uuid','created_by:bigint','updated_by:bigint',
            'created_at:timestamp with time zone','updated_at:timestamp with time zone']
    if (not row.get('regular') or row.get('pk') != ['tenant_id','lead_id','channel_id','conversation_id']
            or row.get('cols') != cols):
        raise RuntimeError('Unexpected Kommo dialogue company binding schema.')


def _key(provider, lead_id, channel_id, conversation_id):
    if provider not in ('connect','kommo') or type(lead_id) not in (int,str):
        raise OperatorError('Источник диалога недоступен.',404)
    try:
        lead = int(lead_id)
        if not 0 < lead < 2**63 or str(lead) != str(lead_id):
            raise ValueError
        for value in (channel_id, conversation_id):
            if not isinstance(value,str) or len(value)>256 or any(ord(c)<32 for c in value):
                raise ValueError
        if provider=='connect' and (str(uuid.UUID(channel_id))!=channel_id
                                    or not conversation_id or len(conversation_id)>160):
            raise ValueError
    except (ValueError, OverflowError):
        raise OperatorError('Источник диалога недоступен.',404) from None
    return provider, lead, channel_id, conversation_id


def _prove(cur, tenant, policy, route, *, action='view'):
    from tenant_operator_access import assert_route
    from tenant_operator_dialogs import projection, runtime_flag
    provider, lead, channel, conversation = route
    assert_route(cur,tenant,policy,provider,conversation,channel,lead,action=action)
    # Same exact visible route as history. Kommo assert_route alone proves only
    # the deal and permissions; a fabricated channel/conversation will not do.
    sql, params = projection(cur,policy)
    cur.execute(sql+"""SELECT 1 FROM visible WHERE provider=%s AND lead_id=%s
      AND channel_id=%s AND conversation_id=%s LIMIT 2""",
      (tenant,tenant,*params,runtime_flag('AI_AGENTS_ENABLED'),*route))
    if len(cur.fetchall()) != 1:
        raise OperatorError('Источник диалога недоступен.',404)


def _pred(tenant, route):
    provider, lead, channel, conversation = route
    if provider=='connect':
        return (CONNECT, 'tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s',
                (tenant,channel,conversation))
    return (KOMMO, 'tenant_id=%s::uuid AND lead_id=%s AND channel_id=%s AND conversation_id=%s',
            (tenant,lead,channel,conversation))


def _manage(cur, tenant, actor, policy, route, *, write):
    from tenant_dialog_access import require_source
    from tenant_crm_native_access import CRMAccessError, current_access
    from tenant_operator_access import assert_route
    if policy.profile.get('role')!='owner':
        return False
    provider, lead, channel, conversation = route
    try:
        for action in ('view','update'):
            current_access(cur,tenant,actor,'companies',action)
        # Take source grants before the lifecycle dialogue lock.
        require_source(cur,tenant,policy,provider,channel,'view')
        require_source(cur,tenant,policy,provider,channel,'update')
        assert_route(cur,tenant,policy,provider,conversation,channel,lead,action='update')
    except (CRMAccessError,TenantLinearError):
        if write:
            raise
        return False
    return True


def _read(cur, tenant, actor, route, can_manage):
    from tenant_crm_native_access import CRMAccessError, current_access, locked_resource
    empty = {'company_id':None,'company_name':None,'can_manage':can_manage}
    try:
        view = current_access(cur,tenant,actor,'companies','view')
    except CRMAccessError:
        return empty
    table, pred, key = _pred(tenant,route)
    cur.execute(f'SELECT company_id::text AS company_id FROM {table} WHERE {pred}', key)
    binding = cur.fetchone()
    if not binding:
        return empty
    visible = locked_resource(cur,view,'companies','view',COMPANIES,tenant,binding['company_id'])
    return {**empty,'company_id':binding['company_id'],'company_name':visible['name']} if visible else empty


_NO_WRITE = object()


def binding(profile, *, provider, lead_id, channel_id, conversation_id, company_id=_NO_WRITE):
    if not is_operator_workspace(profile.get('tenant_id')):
        raise OperatorError('Диалоги этого рабочего пространства недоступны.',404)
    route = _key(provider,lead_id,channel_id,conversation_id)
    write = company_id is not _NO_WRITE
    if write and company_id is not None:
        try:
            if type(company_id) is not str or str(uuid.UUID(company_id)) != company_id:
                raise ValueError
        except ValueError:
            raise OperatorError('Выберите CRM-компанию из списка.') from None
    from tenant_crm_native_access import CRMAccessError, current_access, locked_resource
    from tenant_operator_access import access
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                tenant, actor, policy = access(cur,profile,'dialogs','view')
                if policy.profile.get('tenant_status') != 'active':
                    raise OperatorError('Нет доступа к активному рабочему пространству.',403)
                if write:
                    access(cur,profile,'dialogs','update')
                allow_manage = _manage(cur,tenant,actor,policy,route,write=write)
                if write and not allow_manage:
                    raise OperatorError('Менять компанию диалога может только владелец.',403)
                _prove(cur,tenant,policy,route)
                table, pred, keys = _pred(tenant,route)
                if not _ready(cur,table):
                    raise OperatorError('Привязка компании для этого источника недоступна.',503)
                if write and company_id is None:
                    cur.execute(f'DELETE FROM {table} WHERE {pred}',keys)
                elif write:
                    for action in ('view','update'):
                        rights = current_access(cur,tenant,actor,'companies',action)
                        if not locked_resource(cur,rights,'companies',action,COMPANIES,tenant,company_id):
                            raise CRMAccessError()
                    fields = 'tenant_id,channel_id,conversation_id' if provider=='connect' else 'tenant_id,lead_id,channel_id,conversation_id'
                    values = '%s::uuid,%s::uuid,%s' if provider=='connect' else '%s::uuid,%s,%s,%s'
                    cur.execute(f"""INSERT INTO {table} ({fields},company_id,created_by,updated_by)
                          VALUES({values},%s::uuid,%s,%s) ON CONFLICT({fields}) DO UPDATE SET
                          company_id=EXCLUDED.company_id,updated_by=EXCLUDED.updated_by,updated_at=now()""",
                          (*keys,company_id,actor,actor))
                result = _read(cur,tenant,actor,route,allow_manage)
            conn.commit()
            return result
    except TenantLinearError as error:
        raise OperatorError(str(error),error.status) from error
    except CRMAccessError as error:
        raise OperatorError('CRM-компания недоступна по вашим правам.',error.status) from error
