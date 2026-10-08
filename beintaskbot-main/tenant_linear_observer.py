"""One company/page per tick. Cursor and immutable news dedup survive deployments."""
import json
import uuid
from datetime import datetime, timezone, timedelta
from tenant_platform import _connect
from tenant_linear_context import context
from tenant_linear_provider import query
from tenant_linear_tasks_provider import FIELDS
from tenant_linear_observer_schema import ensure_observer_schema
from tenant_linear_observer_policy import source_time, event_kind, event_id, news_candidate
from tenant_linear_task_inputs import body_text


def store_issue(cur, tenant: str, config: dict, issue: dict, *, baseline: bool) -> None:
    if (issue.get('team') or {}).get('id') != config['team_id']:
        return
    version = source_time(issue['updatedAt'])
    cur.execute('SELECT state_id,source_version FROM saas_linear_snapshots WHERE tenant_id=%s::uuid AND issue_id=%s::uuid FOR UPDATE', (tenant, issue['id']))
    previous = cur.fetchone()
    if previous and version < previous['source_version']:
        return
    kind = event_kind(config, previous, issue, baseline=baseline)
    cur.execute('''INSERT INTO saas_linear_snapshots(tenant_id,issue_id,source_version,state_id,snapshot)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb) ON CONFLICT(tenant_id,issue_id) DO UPDATE
      SET source_version=EXCLUDED.source_version,state_id=EXCLUDED.state_id,snapshot=EXCLUDED.snapshot''',
      (tenant, issue['id'], version, (issue.get('state') or {}).get('id', ''), json.dumps(issue)))
    if kind:
        cur.execute('''INSERT INTO saas_linear_notice_events(tenant_id,id,issue_id,source_version,event)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s) ON CONFLICT DO NOTHING''', (tenant, event_id(tenant, issue), issue['id'], version, kind))
    if news_candidate(config, issue):
        cur.execute('''INSERT INTO saas_linear_news_seen(tenant_id,issue_id) VALUES(%s::uuid,%s::uuid)
          ON CONFLICT DO NOTHING RETURNING issue_id''', (tenant, issue['id']))
        if cur.fetchone():
            # This is a source excerpt, NOT an AI-written or approved announcement.
            summary = body_text(issue.get('description'), config.get('workflow', {}))[:1200]
            cur.execute('''INSERT INTO saas_linear_news(tenant_id,id,issue_id,identifier,project_name,title,summary,source)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s::jsonb)''',
              (tenant, str(uuid.uuid4()), issue['id'], issue.get('identifier', ''), (issue.get('project') or {}).get('name', ''),
               str(issue.get('title') or '')[:255], summary, json.dumps(issue)))


def sync_one() -> dict | None:
    with _connect() as conn:
        ensure_observer_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO saas_linear_sync(tenant_id)
              SELECT i.tenant_id FROM saas_tenant_integrations i JOIN saas_tenants t ON t.id=i.tenant_id
              WHERE i.provider='linear' AND i.status='connected' AND t.status='active'
                AND i.metadata->'settings'->'workflow'->'sync_enabled'='true'::jsonb
                AND i.metadata->'settings'->'workflow'->'enabled'='true'::jsonb ON CONFLICT DO NOTHING''')
            cur.execute('''SELECT s.*,t.owner_telegram_id FROM saas_linear_sync s
              JOIN saas_tenants t ON t.id=s.tenant_id JOIN saas_tenant_integrations i ON i.tenant_id=s.tenant_id AND i.provider='linear'
              WHERE s.due_at<=now() AND t.status='active' AND i.status='connected'
                AND i.metadata->'settings'->'workflow'->'sync_enabled'='true'::jsonb
                AND i.metadata->'settings'->'workflow'->'enabled'='true'::jsonb
              ORDER BY s.due_at LIMIT 1 FOR UPDATE OF s SKIP LOCKED''')
            state = cur.fetchone()
            if not state:
                return None
            tenant = str(state['tenant_id'])
            # Persist a backoff even on provider failure; other companies get their turn.
            cur.execute("UPDATE saas_linear_sync SET due_at=now()+interval '5 minutes' WHERE tenant_id=%s::uuid", (tenant,))
        conn.commit()
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('linear-sync:' + tenant,))
                cur.execute('SELECT * FROM saas_linear_sync WHERE tenant_id=%s::uuid FOR UPDATE', (tenant,))
                state = cur.fetchone()
                _, config, key, config_version = context(cur, {'tenant_id': tenant, 'telegram_id': state_owner(state, tenant, cur)})
                if not config['workflow'].get('sync_enabled'):
                    return None
                if state['config_version'] != config_version:
                    state = {**state, 'cursor': None, 'since_at': None, 'until_at': None}
                until = state['until_at'] or datetime.now(timezone.utc)
                since = state['since_at']
                issue_filter = {'team': {'id': {'eq': config['team_id']}}, 'updatedAt': {'lte': until.isoformat()}}
                if since:
                    issue_filter['updatedAt']['gte'] = (since - timedelta(seconds=2)).isoformat()
                page = query(key, 'query($filter:IssueFilter,$after:String) { issues(first:30,filter:$filter,after:$after,orderBy:updatedAt) { nodes { '
                    + FIELDS + ' parent { id } labels(first:50) { nodes { name } } } pageInfo { hasNextPage endCursor } } }',
                    {'filter': issue_filter, 'after': state['cursor']})['issues']
                for row in page['nodes']:
                    store_issue(cur, tenant, config, row, baseline=since is None)
                more = page['pageInfo']['hasNextPage']
                if more and not page['pageInfo'].get('endCursor'):
                    raise ValueError('Missing page cursor')
                cur.execute('''UPDATE saas_linear_sync SET config_version=%s,cursor=%s,since_at=%s,until_at=%s,
                  due_at=now()+interval '1 minute',updated_at=now() WHERE tenant_id=%s::uuid''',
                  (config_version, page['pageInfo']['endCursor'] if more else None, since if more else until, until if more else None, tenant))
            conn.commit()
            return {'tenant_id': tenant, 'count': len(page['nodes']), 'has_next': more}
        except Exception:
            conn.rollback()
            raise


def state_owner(state: dict, tenant: str, cur) -> int:
    cur.execute('SELECT owner_telegram_id FROM saas_tenants WHERE id=%s::uuid', (tenant,))
    return int(cur.fetchone()['owner_telegram_id'])
