"""Private operator reminders. No messaging transport, worker or implicit DDL."""
from datetime import datetime, timezone
import os
import uuid

from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route


def enabled():
    return os.environ.get('TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED') == '1'


class SessionUnavailable(TenantLinearError):
    """Stable distinction from dialog-only errors and request conflicts."""


def capability(policy):
    if not enabled():
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
    if not enabled():
        raise TenantLinearError('Напоминания недоступны.', 404)
    from tenant_platform import _connect
    with _connect() as conn, conn.cursor() as cur:
        try:
            tenant, actor, policy = access(cur, profile, 'dialogs', 'view')
        except TenantLinearError as error:
            if error.status == 403:
                raise SessionUnavailable(str(error), 403) from error
            raise
        _ready(cur)
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
