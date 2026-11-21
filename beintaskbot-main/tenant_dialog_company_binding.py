"""Exact, tenant-local company binding for a visible dialogue source."""
import uuid

from tenant_beinsystems_operator import OperatorError
from tenant_beinsystems_operator_access import dialogue_chooser_enabled, is_operator_workspace
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect
from tenant_policy import TenantPolicy

CONNECT = 'saas_beinsystems_operator_dialog_company_bindings'
KOMMO = 'public.saas_beinsystems_kommo_dialog_company_bindings'
COMPANIES = 'saas_crm_native_companies'


def feature_for(profile):
    return bool(is_operator_workspace(profile.get('tenant_id'))
                and profile.get('active') is not False
                and profile.get('tenant_status') == 'active'
                and TenantPolicy(profile).allows_action('dialogs', 'view'))


def _ready(cur, table):
    from tenant_schema_preflight import ready
    cols = ('tenant_id', 'channel_id', 'conversation_id', 'company_id',
            'created_by', 'updated_by', 'created_at', 'updated_at')
    company = {COMPANIES: ('tenant_id','id','name','archived_at','access_responsible_id')}
    if table == KOMMO:
        return _kommo_contract(cur) and ready(cur,company,())
    return ready(cur,{table:cols,**company},())


_KOMMO_COLS = {
    'tenant_id':['uuid',True,None], 'lead_id':['bigint',True,None],
    'channel_id':['text',True,None], 'conversation_id':['text',True,None],
    'company_id':['uuid',True,None], 'created_by':['bigint',True,None],
    'updated_by':['bigint',True,None],
    'created_at':['timestamp with time zone',True,'now()'],
    'updated_at':['timestamp with time zone',True,'now()'],
}
_KOMMO_CONSTRAINTS = {
    'beinsystems_kommo_binding_lead':'CHECK ((lead_id > 0))',
    'beinsystems_kommo_binding_channel':'CHECK ((length(channel_id) <= 256))',
    'beinsystems_kommo_binding_conversation':'CHECK ((length(conversation_id) <= 256))',
    'beinsystems_kommo_binding_creator':'CHECK ((created_by > 0))',
    'beinsystems_kommo_binding_updater':'CHECK ((updated_by > 0))',
    'beinsystems_kommo_binding_pk':'PRIMARY KEY (tenant_id, lead_id, channel_id, conversation_id)',
    'beinsystems_kommo_binding_tenant':'FOREIGN KEY (tenant_id) REFERENCES saas_tenants(id) ON DELETE CASCADE',
}


def _kommo_contract(cur):
    cur.execute("""SELECT
        c.relkind='r' AND c.relpersistence='p' AND n.nspname='public'
          AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relispartition
          AND c.reloptions IS NULL AND am.amname='heap'
          AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE c.oid IN (i.inhrelid,i.inhparent))
          AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
          AND (SELECT count(*) FROM pg_trigger t WHERE t.tgconstraint IN (
            SELECT fk.oid FROM pg_constraint fk WHERE fk.conrelid=c.oid AND fk.contype='f')
            AND t.tgisinternal AND t.tgenabled='O')=4
          AND (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid=c.oid)=2
          AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_rewrite r WHERE r.ev_class=c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0
            AND (a.attisdropped OR a.attidentity<>'' OR a.attgenerated<>''))
          AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid
            AND (NOT k.convalidated OR k.condeferrable OR k.condeferred
              OR (k.contype='f' AND k.confrelid<>to_regclass('public.saas_tenants'))))
          AND (SELECT count(*) FROM pg_index x WHERE x.indrelid=c.oid)=1
          AND EXISTS (SELECT 1 FROM pg_index x WHERE x.indrelid=c.oid
            AND x.indisprimary AND x.indisvalid AND x.indisready AND x.indislive)
          AS structurally_safe,
        (SELECT jsonb_object_agg(a.attname, jsonb_build_array(format_type(a.atttypid,a.atttypmod),
          a.attnotnull, pg_get_expr(d.adbin,d.adrelid))) FROM pg_attribute a
          LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
          WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
        (SELECT jsonb_object_agg(k.conname, replace(pg_get_constraintdef(k.oid), 'REFERENCES public.', 'REFERENCES '))
          FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype<>'n') AS constraints
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      LEFT JOIN pg_am am ON am.oid=c.relam WHERE c.oid=to_regclass(%s)""",(KOMMO,))
    row=cur.fetchone() or {}
    return bool(row.get('structurally_safe') and row.get('columns')==_KOMMO_COLS
                and row.get('constraints')==_KOMMO_CONSTRAINTS)


def provision_kommo(cur):
    """On reviewed startup only: create when absent, verify without adopting."""
    cur.execute('SELECT to_regclass(%s) IS NOT NULL AS present',(KOMMO,))
    if not cur.fetchone()['present']:
        cur.execute(f"""CREATE TABLE {KOMMO} (
            tenant_id UUID NOT NULL,
            lead_id BIGINT NOT NULL CONSTRAINT beinsystems_kommo_binding_lead CHECK(lead_id>0),
            channel_id TEXT NOT NULL CONSTRAINT beinsystems_kommo_binding_channel CHECK(length(channel_id)<=256),
            conversation_id TEXT NOT NULL CONSTRAINT beinsystems_kommo_binding_conversation CHECK(length(conversation_id)<=256),
            company_id UUID NOT NULL,
            created_by BIGINT NOT NULL CONSTRAINT beinsystems_kommo_binding_creator CHECK(created_by>0),
            updated_by BIGINT NOT NULL CONSTRAINT beinsystems_kommo_binding_updater CHECK(updated_by>0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT beinsystems_kommo_binding_pk PRIMARY KEY(tenant_id,lead_id,channel_id,conversation_id),
            CONSTRAINT beinsystems_kommo_binding_tenant FOREIGN KEY(tenant_id)
              REFERENCES public.saas_tenants(id) ON DELETE CASCADE
        )""")
    if not _kommo_contract(cur):
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


def attach_list_company_names(cur, tenant, actor, cards):
    """Enrich the visible page from exact dialogue routes and current CRM grants."""
    if not cards or not is_operator_workspace(tenant):
        return
    from tenant_crm_native_access import CRMAccessError, current_access, scoped_sql
    try:
        view = current_access(cur, tenant, actor, 'companies', 'view')
    except CRMAccessError:
        return

    def route_key(row):
        try:
            return _key(row.get('provider'), row.get('lead_id'), row.get('channel_id'), row.get('conversation_id'))
        except OperatorError:
            return None

    keys_by_card = [[route_key(row) for row in card.get('group_sources', [card])] for card in cards]
    keys = {key for routes in keys_by_card for key in routes if key is not None}
    where, scope = scoped_sql(view, 'companies', 'view', 'c')
    companies = {}
    for provider, table, match in (
        ('connect', CONNECT, 'b.channel_id=r.channel_id::uuid'),
        ('kommo', KOMMO, 'b.lead_id=r.lead_id AND b.channel_id=r.channel_id'),
    ):
        routes = [(lead, channel, peer) for kind, lead, channel, peer in keys if kind == provider]
        if not routes or not _ready(cur, table):
            continue
        lead_ids, channels, peers = zip(*routes)
        cur.execute(f"""WITH routes(lead_id, channel_id, conversation_id) AS (
                SELECT * FROM unnest(%s::bigint[], %s::text[], %s::text[]))
            SELECT r.lead_id,r.channel_id,r.conversation_id,c.id::text AS company_id,c.name
            FROM routes r JOIN {table} b
              ON b.tenant_id=%s::uuid AND {match} AND b.conversation_id=r.conversation_id
            JOIN {COMPANIES} c ON c.tenant_id=b.tenant_id AND c.id=b.company_id
            WHERE c.archived_at IS NULL AND ({where})""",
            [list(lead_ids), list(channels), list(peers), tenant, *scope])
        for row in cur.fetchall():
            companies[(provider, row['lead_id'], row['channel_id'], row['conversation_id'])] = (row['company_id'], row['name'])

    for card, routes in zip(cards, keys_by_card):
        # A folded group shows a name only when its visible routes agree on one company.
        linked = {companies.get(key) for key in routes}
        if len(linked) == 1 and None not in linked:
            card['dialog_company_name'] = linked.pop()[1]


_NO_WRITE = object()
_UNCHECKED = object()


def normalize_company_id(value):
    if value is not None:
        try:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError
        except ValueError:
            raise OperatorError('Выберите CRM-компанию из списка.') from None
    return value


def assert_expected_company(actual, expected):
    if expected is not _UNCHECKED and actual != expected:
        raise OperatorError('Компания диалога изменилась. Обновите диалог.',409)


def write_binding(cur, route, table, pred, keys, company, actor, expected=_UNCHECKED):
    """An inline edit never clears a newer company selected elsewhere."""
    if company is None:
        condition, params = ('', keys) if expected is _UNCHECKED else (' AND company_id=%s::uuid', (*keys,expected))
        cur.execute(f'DELETE FROM {table} WHERE {pred}{condition}',params)
    elif expected is not None and expected is not _UNCHECKED:
        cur.execute(f"""UPDATE {table} SET company_id=%s::uuid,updated_by=%s,updated_at=now()
            WHERE {pred} AND company_id=%s::uuid""",(company,actor,*keys,expected))
    else:
        fields='tenant_id,channel_id,conversation_id' if route[0]=='connect' else 'tenant_id,lead_id,channel_id,conversation_id'
        values='%s::uuid,%s::uuid,%s' if route[0]=='connect' else '%s::uuid,%s,%s,%s'
        conflict = ('UPDATE SET company_id=EXCLUDED.company_id,updated_by=EXCLUDED.updated_by,updated_at=now()'
                    if expected is _UNCHECKED else 'NOTHING')
        cur.execute(f"""INSERT INTO {table} ({fields},company_id,created_by,updated_by)
            VALUES({values},%s::uuid,%s,%s) ON CONFLICT({fields}) DO {conflict}""",
            (*keys,company,actor,actor))
    if expected is not _UNCHECKED and cur.rowcount != 1:
        raise OperatorError('Компания диалога изменилась. Обновите диалог.',409)


def read_dialogue_binding(cur, tenant, actor, policy, route):
    """The history route was already verified. Reuse the company endpoint's grants."""
    if not is_operator_workspace(tenant):
        return None
    try:
        exact = _key(route['provider'],route['lead_id'],route['channel_id'],route['conversation_id'])
    except OperatorError:
        return None
    table,_,_ = _pred(tenant,exact)
    if not _ready(cur,table):
        return None
    return _binding_snapshot(cur,tenant,actor,policy,exact,_manage(cur,tenant,actor,policy,exact,write=False))


def _binding_snapshot(cur, tenant, actor, policy, route, can_manage):
    result = _read(cur,tenant,actor,route,can_manage)
    if can_manage and policy.allows_action('operator_data','view') and dialogue_chooser_enabled(tenant):
        result['can_search_operator'] = True
    return result


def binding(profile, *, provider, lead_id, channel_id, conversation_id, company_id=_NO_WRITE,
            expected_company_id=_UNCHECKED):
    if not is_operator_workspace(profile.get('tenant_id')):
        raise OperatorError('Диалоги этого рабочего пространства недоступны.',404)
    route = _key(provider,lead_id,channel_id,conversation_id)
    write = company_id is not _NO_WRITE
    if write:
        normalize_company_id(company_id)
    if expected_company_id is not _UNCHECKED:
        normalize_company_id(expected_company_id)
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
                if write and company_id is not None:
                    for action in ('view','update'):
                        rights = current_access(cur,tenant,actor,'companies',action)
                        if not locked_resource(cur,rights,'companies',action,COMPANIES,tenant,company_id):
                            raise CRMAccessError()
                if write:
                    write_binding(cur,route,table,pred,keys,company_id,actor,expected_company_id)
                result = _binding_snapshot(cur,tenant,actor,policy,route,allow_manage)
            conn.commit()
            return result
    except TenantLinearError as error:
        raise OperatorError(str(error),error.status) from error
    except CRMAccessError as error:
        raise OperatorError('CRM-компания недоступна по вашим правам.',error.status) from error
