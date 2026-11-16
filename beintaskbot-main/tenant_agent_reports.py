"""Read-only agent facts. No historical attribution from current assignments."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError, identifier
from tenant_night_report_access import authorize, read_scope, source_filter, access_version, require_version


def period(first='', last='', zone='UTC', *, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(zone)
        start = date.fromisoformat(first) if first else now.astimezone(tz).date()
        end = date.fromisoformat(last) if last else start
        if end < start or (end-start).days > 30:
            raise ValueError()
        left = datetime.combine(start, time(), tz)
        right = datetime.combine(end+timedelta(days=1), time(), tz)
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        raise TenantLinearError('Выберите корректный период до 31 дня и часовой пояс.') from None
    return {'from':start.isoformat(), 'to':end.isoformat(), 'timezone':zone,
            'starts_at':left.astimezone(timezone.utc).isoformat(),
            'ends_at':right.astimezone(timezone.utc).isoformat()}


# EXISTS avoids duplicated runs even if route tables are unexpectedly duplicated.
# agent_id comes from the historical run, never today's conversation assignment.
VISIBLE_RUNS = '''SELECT r.id,r.tenant_id,r.agent_id,r.state,r.created_at,
 e.payload->>'channel_id' AS channel_id,e.payload->>'conversation_id' AS conversation_id
 FROM saas_ai_agent_runs r JOIN saas_tenant_webhook_events e
 ON e.tenant_id=r.tenant_id AND e.fingerprint=r.event_fingerprint
 WHERE r.tenant_id=%s::uuid AND e.provider='connect'
 AND EXISTS(SELECT 1 FROM saas_connect_conversations c
   WHERE c.tenant_id=e.tenant_id
   AND c.connect_channel_id::text=e.payload->>'channel_id'
   AND c.conversation_id=e.payload->>'conversation_id' AND (__SOURCE_SCOPE__))'''


def report(profile, *, agent_id='', first='', last='', zone='', expected_access_version='', now=None):
    now = now or datetime.now(timezone.utc)
    selected = identifier(agent_id) if agent_id else None
    with _connect() as conn:
        with conn.cursor() as cur:
            # Schema creation/migration is not a report read side effect.
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            tenant, company = authorize(cur, profile)
            version = access_version(cur, tenant, company)
            require_version(version, expected_access_version)
            onboarding = company.get('onboarding') or {}
            zone = zone or onboarding.get('timezone') or (onboarding.get('company') or {}).get('timezone') or 'UTC'
            dates = period(first, last, zone, now=now)
            visible = VISIBLE_RUNS.replace('__SOURCE_SCOPE__', source_filter(company))
            # Narrow report readers see only names evidenced by authorized runs.
            narrow = read_scope(company) in {'self', 'department'}
            cur.execute('WITH visible AS ('+visible+''') SELECT a.id,a.name,
              a.archived_at IS NOT NULL AS archived FROM saas_ai_agents a
              WHERE a.tenant_id=%s::uuid '''+('AND EXISTS(SELECT 1 FROM visible v WHERE v.tenant_id=a.tenant_id AND v.agent_id=a.id) ' if narrow else '')+
              'ORDER BY a.name,a.id', (tenant,tenant))
            agents = [{'id':str(a['id']), 'name':a['name'], 'archived':a['archived']} for a in cur.fetchall()]
            if selected and selected not in {a['id'] for a in agents}:
                raise TenantLinearError('Агент недоступен.',404)
            cur.execute('WITH visible AS ('+visible+''') SELECT agent_id,state,count(*) AS count,
              count(DISTINCT (channel_id,conversation_id)) AS conversations
              FROM visible WHERE created_at>=%s::timestamptz AND created_at<%s::timestamptz
              AND (%s::uuid IS NULL OR agent_id=%s::uuid)
              GROUP BY agent_id,state''',(tenant,dates['starts_at'],dates['ends_at'],selected,selected))
            facts = [{**dict(row),'agent_id':str(row['agent_id']), 'count':int(row['count']),
                      'conversations':int(row['conversations'])} for row in cur.fetchall()]
    return {'tenant_id':tenant, 'company_name':company['name'], 'access_version':version,
            'access_scope':read_scope(company), 'period':dates, 'agents':agents,
            'selected_agent_id':selected, 'facts':facts, 'generated_at':now.isoformat(),
            'coverage':'retained_connect_agent_runs',
            'unavailable_metrics':['operator_resolution','delivery','unattributed_handoffs'],
            'note':'Завершённый запуск не означает доставку ответа. Показаны только сохранённые запуски Connect; остальные каналы и события без связи с агентом не включены.'}
