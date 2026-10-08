"""Bounded retention independent of connection status. Keep dedup tombstones."""
from tenant_platform import _connect
from tenant_linear_observer_schema import ensure_observer_schema


def cleanup() -> None:
    with _connect() as conn:
        ensure_observer_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''WITH expired AS (
              SELECT tenant_id,id FROM saas_linear_news
              WHERE COALESCE(published_at,created_at)<now()-interval '90 days'
              ORDER BY COALESCE(published_at,created_at) LIMIT 100 FOR UPDATE SKIP LOCKED)
              DELETE FROM saas_linear_news n USING expired e WHERE n.tenant_id=e.tenant_id AND n.id=e.id''')
            cur.execute('''WITH expired AS (
              SELECT tenant_id,id FROM saas_linear_notice_events WHERE created_at<now()-interval '90 days'
              ORDER BY created_at LIMIT 100 FOR UPDATE SKIP LOCKED)
              DELETE FROM saas_linear_notice_events n USING expired e WHERE n.tenant_id=e.tenant_id AND n.id=e.id''')
            # Retain state/version to avoid treating an old task as a new transition.
            cur.execute('''WITH expired AS (
              SELECT tenant_id,issue_id FROM saas_linear_snapshots
              WHERE source_version<now()-interval '90 days' AND snapshot<>'{}'::jsonb
              ORDER BY source_version LIMIT 100 FOR UPDATE SKIP LOCKED)
              UPDATE saas_linear_snapshots n SET snapshot='{}'::jsonb FROM expired e
              WHERE n.tenant_id=e.tenant_id AND n.issue_id=e.issue_id''')
        conn.commit()
