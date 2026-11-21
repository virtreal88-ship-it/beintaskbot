"""Private operator reminders. No messaging transport, worker or implicit DDL."""
from datetime import datetime, timezone
import os
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route


LEGACY_ENABLE = 'TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED'
WORKSPACE_ENABLE = 'TENANT_PERSONAL_DIALOG_REMINDERS_BEINSYSTEMS_ENABLED'
PROVISION_ON_STARTUP = 'TENANT_PERSONAL_DIALOG_REMINDERS_PROVISION_ON_STARTUP'


def configured():
    return os.environ.get(LEGACY_ENABLE) == '1' or os.environ.get(WORKSPACE_ENABLE) == '1'


def enabled(tenant_id):
    if os.environ.get(LEGACY_ENABLE) == '1':
        return True
    if os.environ.get(WORKSPACE_ENABLE) != '1':
        return False
    from tenant_beinsystems_operator_access import is_operator_workspace
    return is_operator_workspace(tenant_id)


class SessionUnavailable(TenantLinearError):
    """Stable distinction from dialog-only errors and request conflicts."""


def capability(policy):
    if not enabled(policy.profile.get('tenant_id')):
        return False
    from tenant_scoped_access import ACTIVE_TENANT_STATUSES
    if (policy.profile.get('active') is not True
            or policy.profile.get('tenant_status', 'active') not in ACTIVE_TENANT_STATUSES):
        return False
    scope = policy.action_scope('dialogs', 'view')
    return scope != 'none' if scope is not None else policy.legacy_allows('dialogs')


def migrate(cur):
    """Explicit, additive database provisioning; never invoked from startup or HTTP."""
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_personal_dialog_reminders (
        tenant_id UUID NOT NULL,
        actor_id BIGINT NOT NULL,
        id UUID NOT NULL,
        provider TEXT,
        channel_id TEXT,
        conversation_id TEXT,
        lead_id BIGINT,
        note TEXT NOT NULL CHECK(length(note) BETWEEN 1 AND 280),
        due_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        CHECK ((provider IS NULL AND channel_id IS NULL AND conversation_id IS NULL AND lead_id IS NULL)
            OR (provider IS NOT NULL AND provider='connect' AND channel_id IS NOT NULL
                AND channel_id<>'' AND conversation_id IS NOT NULL AND conversation_id<>''
                AND lead_id IS NOT NULL AND lead_id>0)),
        PRIMARY KEY(tenant_id,actor_id,id),
        FOREIGN KEY(tenant_id,actor_id)
          REFERENCES saas_tenant_members(tenant_id,telegram_id) ON DELETE CASCADE
    )""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_personal_dialog_reminders_pending
        ON saas_personal_dialog_reminders(tenant_id,actor_id,due_at,id)
        WHERE completed_at IS NULL""")


_SCHEMA_COLUMNS = (
    ('tenant_id','uuid',True), ('actor_id','bigint',True), ('id','uuid',True),
    ('provider','text',False), ('channel_id','text',False), ('conversation_id','text',False),
    ('lead_id','bigint',False), ('note','text',True),
    ('due_at','timestamp with time zone',True), ('completed_at','timestamp with time zone',False),
)
_SCHEMA_CONSTRAINTS = frozenset({
    'PRIMARY KEY (tenant_id, actor_id, id)',
    'FOREIGN KEY (tenant_id, actor_id) REFERENCES saas_tenant_members(tenant_id, telegram_id) ON DELETE CASCADE',
    'CHECK (((length(note) >= 1) AND (length(note) <= 280)))',
    "CHECK ((((provider IS NULL) AND (channel_id IS NULL) AND (conversation_id IS NULL) AND (lead_id IS NULL)) OR ((provider IS NOT NULL) AND (provider = 'connect'::text) AND (channel_id IS NOT NULL) AND (channel_id <> ''::text) AND (conversation_id IS NOT NULL) AND (conversation_id <> ''::text) AND (lead_id IS NOT NULL) AND (lead_id > 0))))",
})


def provision_schema(cur):
    """Create only missing private-reminder objects, or verify without repair."""
    cur.execute("SELECT to_regclass('saas_personal_dialog_reminders') IS NULL AS absent")
    if cur.fetchone()['absent']:
        migrate(cur)
    verify_provisioned_schema(cur)


def verify_provisioned_schema(cur):
    cur.execute("""SELECT c.oid, n.nspname FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE c.oid=to_regclass('saas_personal_dialog_reminders')
        AND n.nspname=current_schema() AND c.relkind='r' AND c.relpersistence='p'
        AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
        AND NOT EXISTS (SELECT FROM pg_catalog.pg_trigger t
                        WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
        AND NOT EXISTS (SELECT FROM pg_catalog.pg_inherits i
                        WHERE c.oid IN (i.inhrelid,i.inhparent))""")
    table = cur.fetchone()
    if not table:
        raise RuntimeError('Unexpected personal-reminder table; provisioning rolled back.')
    oid = table['oid']
    cur.execute("""SELECT a.attname,format_type(a.atttypid,a.atttypmod) AS data_type,a.attnotnull,
        (a.atthasdef OR a.attidentity<>'' OR a.attgenerated<>'') AS generated
      FROM pg_catalog.pg_attribute a
      WHERE a.attrelid=%s::oid AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (oid,))
    cols = cur.fetchall()
    if (tuple((r['attname'],r['data_type'],r['attnotnull']) for r in cols) != _SCHEMA_COLUMNS
            or any(r['generated'] for r in cols)):
        raise RuntimeError('Unexpected personal-reminder columns; provisioning rolled back.')
    cur.execute("""SELECT pg_get_constraintdef(oid) AS definition,convalidated
      FROM pg_catalog.pg_constraint WHERE conrelid=%s::oid AND contype IN ('c','f','p','u','x')""", (oid,))
    constraints = cur.fetchall()
    if (len(constraints) != len(_SCHEMA_CONSTRAINTS)
            or frozenset(r['definition'] for r in constraints) != _SCHEMA_CONSTRAINTS
            or not all(r['convalidated'] for r in constraints)):
        raise RuntimeError('Unexpected personal-reminder constraints; provisioning rolled back.')
    cur.execute("""SELECT 1 FROM pg_catalog.pg_index i
      WHERE i.indexrelid=to_regclass('saas_personal_dialog_reminders_pending')
        AND i.indrelid=%s::oid AND i.indisvalid AND i.indisready AND i.indislive
        AND pg_get_indexdef(i.indexrelid)=format(
           'CREATE INDEX saas_personal_dialog_reminders_pending ON %%I.saas_personal_dialog_reminders USING btree (tenant_id, actor_id, due_at, id) WHERE (completed_at IS NULL)',
           %s::text)""", (oid,table['nspname']))
    if not cur.fetchone():
        raise RuntimeError('Unexpected personal-reminder index; provisioning rolled back.')


def _uuid(value):
    return str(uuid.UUID(str(value)))


def _route(data):
    provider, channel, conversation, lead = (data[key] for key in
        ('provider', 'channel_id', 'conversation_id', 'lead_id'))
    if (provider != 'connect' or type(lead) is not int
            or not 0 < lead < 2**63 or not isinstance(channel, str)
            or not isinstance(conversation, str) or len(channel) > 250
            or len(conversation) > 250 or any(ord(ch) < 32 for ch in channel+conversation)
            or not (channel and conversation)):
        raise ValueError('invalid route')
    return provider, channel, conversation, lead


def _time(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError('invalid date')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('missing offset')
    return parsed.astimezone(timezone.utc)


def _ready(cur):
    cur.execute("SELECT to_regclass('saas_personal_dialog_reminders') IS NOT NULL AS ready")
    if not cur.fetchone()['ready']:
        raise TenantLinearError('Напоминания ещё не доступны.', 503)


def execute(profile, data=None):
    if not enabled(profile.get('tenant_id')):
        raise TenantLinearError('Напоминания недоступны.', 404)
    from tenant_platform import _connect
    with _connect() as conn, conn.cursor() as cur:
        try:
            tenant, actor, policy = access(cur, profile, 'dialogs', 'view')
        except TenantLinearError as error:
            if error.status == 403:
                raise SessionUnavailable(str(error), 403) from error
            raise
        if os.environ.get(LEGACY_ENABLE) == '1':
            _ready(cur)
        else:
            # An accidental pilot opt-in after a failed or skipped preflight
            # must not expose content from an unverified relation.
            try:
                verify_provisioned_schema(cur)
            except RuntimeError:
                raise TenantLinearError('Напоминания ещё не доступны.', 503) from None
        if data is None:
            cur.execute("""SELECT id::text AS id,note,due_at, provider IS NOT NULL AS can_open,
                due_at<=CURRENT_TIMESTAMP AS due, count(*) OVER () AS total,
                count(*) FILTER (WHERE due_at<=CURRENT_TIMESTAMP) OVER () AS due_count
                FROM saas_personal_dialog_reminders
                WHERE tenant_id=%s::uuid AND actor_id=%s AND completed_at IS NULL
                ORDER BY due_at,id LIMIT 100""", (tenant, actor))
            rows = cur.fetchall()
            return {'reminders': [{'id': r['id'], 'note': r['note'],
                     'due_at': r['due_at'].isoformat(), 'due': r['due'],
                     'can_open': r['can_open']} for r in rows],
                    'due_count': rows[0]['due_count'] if rows else 0,
                    'total': rows[0]['total'] if rows else 0}
        action, ident = data['action'], _uuid(data['id'])
        if action == 'create':
            required = {'action','id','note','due_at'}
            fields = set(data)
            if fields == required:
                provider, channel, conversation, lead = None, None, None, None
            elif fields == required | {'provider','channel_id','conversation_id','lead_id'}:
                provider, channel, conversation, lead = _route(data)
            else:
                raise ValueError('invalid operation')
            note = data['note']
            if not isinstance(note, str):
                raise ValueError('invalid note')
            note = note.strip()
            if not 1 <= len(note) <= 280 or any(ord(ch) < 32 and ch not in '\t\n\r' for ch in note):
                raise ValueError('invalid note')
            due_at = _time(data['due_at'])
            if provider is not None:
                assert_route(cur, tenant, policy, provider, conversation, channel, lead)
            cur.execute("""SELECT (provider IS NOT DISTINCT FROM %s AND channel_id IS NOT DISTINCT FROM %s
                AND conversation_id IS NOT DISTINCT FROM %s AND lead_id IS NOT DISTINCT FROM %s
                AND note=%s AND due_at=%s::timestamptz) AS same_request
                FROM saas_personal_dialog_reminders
                WHERE tenant_id=%s::uuid AND actor_id=%s AND id=%s::uuid""",
                (provider, channel, conversation, lead, note, due_at, tenant, actor, ident))
            row = cur.fetchone()
            if row:
                if not row['same_request']:
                    raise TenantLinearError('Запрос изменился. Создайте новое напоминание.', 409)
                return {'id': ident}
            if due_at <= datetime.now(timezone.utc):
                raise TenantLinearError('Выберите время в будущем.', 400)
            cur.execute("""INSERT INTO saas_personal_dialog_reminders
                (tenant_id,actor_id,id,provider,channel_id,conversation_id,lead_id,note,due_at)
                VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id,actor_id,id) DO NOTHING
                RETURNING id""", (tenant,actor,ident,provider,channel,conversation,lead,note,due_at))
            if not cur.fetchone():
                raise TenantLinearError('Повторите запрос с теми же данными.', 409)
            conn.commit()
            return {'id': ident}
        if set(data) != {'action','id'}:
            raise ValueError('invalid operation')
        if action == 'complete':
            cur.execute("""UPDATE saas_personal_dialog_reminders
                SET completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP)
                WHERE tenant_id=%s::uuid AND actor_id=%s AND id=%s::uuid RETURNING id""",
                (tenant, actor, ident))
            if not cur.fetchone():
                raise TenantLinearError('Напоминание не найдено.', 404)
            conn.commit()
            return {'id': ident}
        if action == 'open':
            cur.execute("""SELECT provider,channel_id,conversation_id,lead_id
                FROM saas_personal_dialog_reminders
                WHERE tenant_id=%s::uuid AND actor_id=%s AND id=%s::uuid AND completed_at IS NULL""",
                (tenant, actor, ident))
            row = cur.fetchone()
            if not row or row['provider'] is None:
                raise TenantLinearError('Напоминание не найдено.', 404)
            provider, channel, conversation, lead = _route(row)
            assert_route(cur, tenant, policy, provider, conversation, channel, lead)
            return {'dialog': dict(row)}
        raise ValueError('invalid operation')
