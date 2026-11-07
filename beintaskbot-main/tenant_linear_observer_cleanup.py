"""Bounded retention independent of connection status. Keep dedup tombstones."""
from tenant_platform import _connect
from tenant_linear_observer_schema import ensure_observer_schema
from tenant_proactive_risk_schema import ensure_schema as ensure_risk_schema


def cleanup() -> None:
    with _connect() as conn:
        ensure_observer_schema(conn)
        ensure_risk_schema(conn)
        with conn.cursor() as cur:
            # Backfill the small risk-only payload before the 90-day full-snapshot
            # expiry, in bounded batches so old open tasks keep due-date metadata.
            cur.execute('''WITH pending AS (
              SELECT tenant_id,issue_id,snapshot FROM saas_linear_snapshots
              WHERE snapshot<>'{}'::jsonb AND risk_snapshot='{}'::jsonb
              ORDER BY source_version LIMIT 100 FOR UPDATE SKIP LOCKED)
              UPDATE saas_linear_snapshots n SET risk_snapshot=(
                SELECT COALESCE(jsonb_object_agg(k,v),'{}'::jsonb) FROM jsonb_each(p.snapshot) AS fields(k,v)
                WHERE k=ANY(ARRAY['id','team','title','description','priority','assignee','dueDate','updatedAt','url','state'])
              ) FROM pending p WHERE n.tenant_id=p.tenant_id AND n.issue_id=p.issue_id''')
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
                AND (risk_snapshot<>'{}'::jsonb OR NOT EXISTS (
                  SELECT 1 FROM saas_tenant_integrations i
                  WHERE i.tenant_id=saas_linear_snapshots.tenant_id AND i.provider='linear' AND i.status='connected'
                    AND i.metadata->'settings'->'workflow'->'enabled'='true'::jsonb
                    AND i.metadata->'settings'->'workflow'->'sync_enabled'='true'::jsonb
                    AND saas_linear_snapshots.snapshot->'team'->>'id'=i.metadata->'settings'->>'team_id'
                    AND saas_linear_snapshots.state_id<>ALL(ARRAY(SELECT jsonb_array_elements_text(
                      COALESCE(i.metadata->'settings'->'done_state_ids','[]'::jsonb))))
                ))
              ORDER BY source_version LIMIT 100 FOR UPDATE SKIP LOCKED)
              UPDATE saas_linear_snapshots n SET snapshot='{}'::jsonb,
                risk_snapshot=CASE WHEN EXISTS (
                  SELECT 1 FROM saas_tenant_integrations i
                  WHERE i.tenant_id=n.tenant_id AND i.provider='linear' AND i.status='connected'
                    AND i.metadata->'settings'->'workflow'->'enabled'='true'::jsonb
                    AND i.metadata->'settings'->'workflow'->'sync_enabled'='true'::jsonb
                    AND n.risk_snapshot->'team'->>'id'=i.metadata->'settings'->>'team_id'
                    AND n.state_id<>ALL(ARRAY(SELECT jsonb_array_elements_text(
                      COALESCE(i.metadata->'settings'->'done_state_ids','[]'::jsonb))))
                ) THEN n.risk_snapshot ELSE '{}'::jsonb END
              FROM expired e
              WHERE n.tenant_id=e.tenant_id AND n.issue_id=e.issue_id''')
            cur.execute('''WITH expired AS (
              SELECT tenant_id,source_kind,source_id,rule_id FROM saas_proactive_risk_findings
              WHERE checked_at<now()-interval '90 days' AND (
                (source_kind='linear' AND NOT EXISTS (
                  SELECT 1 FROM saas_tenant_integrations i
                  WHERE i.tenant_id=saas_proactive_risk_findings.tenant_id AND i.provider='linear'
                    AND i.status='connected' AND i.metadata->'settings'->'workflow'->'enabled'='true'::jsonb
                    AND i.metadata->'settings'->'workflow'->'sync_enabled'='true'::jsonb
                    AND saas_proactive_risk_findings.source_scope=
                      floor(extract(epoch from i.updated_at)*1000000)::bigint::text||':'||(i.metadata->'settings'->>'team_id')))
                OR (source_kind='crm_task' AND NOT EXISTS (
                  SELECT 1 FROM saas_tenant_integrations i
                  WHERE i.tenant_id=saas_proactive_risk_findings.tenant_id AND i.provider='kommo'
                    AND i.status='connected' AND saas_proactive_risk_findings.source_scope=
                      i.account_domain||':'||floor(extract(epoch from i.connected_at)*1000000)::bigint::text))
              ) ORDER BY checked_at LIMIT 100 FOR UPDATE SKIP LOCKED)
              DELETE FROM saas_proactive_risk_findings f USING expired e
              WHERE f.tenant_id=e.tenant_id AND f.source_kind=e.source_kind
                AND f.source_id=e.source_id AND f.rule_id=e.rule_id''')
        conn.commit()
