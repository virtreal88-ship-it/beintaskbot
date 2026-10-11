"""Linear release-news persistence and publication lifecycle."""
from datetime import datetime, timedelta, timezone
import uuid

from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json

def upsert_linear_news(*, source_issue_id: str, identifier: str, project_key: str,
                       title: str, summary: str, body: str, account: str,
                       environment: str, source_created_at: str | None,
                       done_at: str | None, source_url: str, raw: dict | None = None) -> dict:
    """Persist one customer-facing Linear release note for 90 days."""
    issue_id = str(source_issue_id or "").strip()
    project = str(project_key or "").strip().upper()
    if not issue_id or project not in {"AKUL", "DINE", "BEINSYSTEMS"}:
        raise TenantPlatformError("Linear xəbəri üçün mənbə və layihə tələb olunur.")
    expires_at = datetime.now(timezone.utc) + timedelta(days=90)
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_news
                (id, source_issue_id, identifier, project_key, title, summary, body,
                 account, environment, source_created_at, done_at, expires_at,
                 source_url, raw, approval_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::timestamptz, COALESCE(%s::timestamptz, now()), %s,
                        %s, %s::jsonb, 'pending')
                ON CONFLICT (source_issue_id) DO UPDATE SET
                    identifier = EXCLUDED.identifier,
                    project_key = EXCLUDED.project_key,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    body = EXCLUDED.body,
                    account = EXCLUDED.account,
                    environment = EXCLUDED.environment,
                    source_created_at = EXCLUDED.source_created_at,
                    source_url = EXCLUDED.source_url,
                    raw = EXCLUDED.raw,
                    updated_at = now()
                RETURNING id, source_issue_id, identifier, project_key, title,
                          summary, body, account, environment, source_created_at,
                          done_at, expires_at, source_url, approval_status,
                          media_url, media_type, created_at, updated_at
            """, (
                uuid.uuid4(), issue_id, str(identifier or ""), project,
                str(title or "")[:500], str(summary or "")[:1200], str(body or "")[:12000],
                str(account or "")[:255], str(environment or "")[:255],
                source_created_at or None, done_at or None, expires_at,
                str(source_url or "")[:1000], _json(raw or {}),
            ))
            row = cur.fetchone()
        conn.commit()
    item = dict(row)
    for key in ("id",):
        if item.get(key):
            item[key] = str(item[key])
    for key in ("source_created_at", "done_at", "expires_at", "telegram_published_at", "reviewed_at", "created_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def sync_linear_news_environments(issues: list[dict]) -> None:
    """Refresh live metadata only; never rewrite reviewed copy or delivery marks."""
    from linear_metadata import issue_environment
    from linear_news_policy import news_source_key
    rows = [{"source_id": str(issue.get("source_id") or issue.get("id") or ""),
             "environment": issue_environment(issue), "issue": issue, "source_key": news_source_key(issue)}
            for issue in issues if isinstance(issue, dict) and (issue.get("source_id") or issue.get("id"))]
    if not rows:
        return
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE crm_linear_news AS news
                SET environment=source.environment,
                    raw=jsonb_set(
                        CASE WHEN news.raw->'news_classification'->>'source_key' IS DISTINCT FROM source.source_key
                             THEN COALESCE(news.raw, '{}'::jsonb) - 'news_classification'
                             ELSE COALESCE(news.raw, '{}'::jsonb) END,
                        '{linear}', source.issue, true)
                FROM jsonb_to_recordset(%s::jsonb) AS source(source_id text, environment text, issue jsonb, source_key text)
                WHERE news.source_issue_id=source.source_id
                  AND (news.environment IS DISTINCT FROM source.environment
                       OR news.raw->'linear' IS DISTINCT FROM source.issue)
            """, (_json(rows),))
        conn.commit()


def save_linear_news_classification(source_issue_id: str, classification: dict) -> None:
    """Cache eligibility without changing text, approval or delivery history."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE crm_linear_news
                SET raw=jsonb_set(COALESCE(raw, '{}'::jsonb), '{news_classification}', %s::jsonb, true)
                WHERE source_issue_id=%s
            """, (_json(classification), str(source_issue_id)))
        conn.commit()


def list_linear_news(*, project_key: str = "", limit: int = 60, approval_status: str = "approved", offset: int = 0) -> list[dict]:
    """Return non-expired release notes, newest first."""
    from linear_metadata import news_environment
    project = str(project_key or "").strip().upper()
    status = str(approval_status or "").strip().lower()
    try:
        size = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        size = 60
    start = max(0, int(offset or 0))
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            if project in {"AKUL", "DINE"}:
                cur.execute("""
                    SELECT * FROM crm_linear_news
                    WHERE project_key = %s AND expires_at > now()
                      AND (%s = '' OR approval_status = %s)
                      AND (%s <> 'pending' OR (first_published_at IS NULL AND telegram_published_at IS NULL AND telegram_message_id IS NULL))
                      AND (%s <> 'pending' OR source_issue_id LIKE 'manual-news-%%'
                           OR (raw->'news_classification'->>'kind' IN ('feature', 'other')
                               AND COALESCE(raw->'linear'->>'parent_id', '') = ''))
                    ORDER BY done_at DESC, created_at DESC, id DESC LIMIT %s OFFSET %s
                """, (project, status, status, status, status, size, start))
            else:
                cur.execute("""
                    SELECT * FROM crm_linear_news
                    WHERE expires_at > now()
                      AND (%s = '' OR approval_status = %s)
                      AND (%s <> 'pending' OR (first_published_at IS NULL AND telegram_published_at IS NULL AND telegram_message_id IS NULL))
                      AND (%s <> 'pending' OR source_issue_id LIKE 'manual-news-%%'
                           OR (raw->'news_classification'->>'kind' IN ('feature', 'other')
                               AND COALESCE(raw->'linear'->>'parent_id', '') = ''))
                    ORDER BY done_at DESC, created_at DESC, id DESC LIMIT %s OFFSET %s
                """, (status, status, status, status, size, start))
            rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item.get("id") or "")
        if not isinstance(item.get("raw"), dict):
            item["raw"] = {}
        item["environment"] = news_environment(item)
        for key in ("source_created_at", "done_at", "expires_at", "telegram_published_at", "first_published_at", "reviewed_at", "created_at", "updated_at"):
            if item.get(key):
                item[key] = item[key].isoformat()
        result.append(item)
    return result


def update_linear_news_review(*, source_issue_id: str, title: str, summary: str,
                              body: str, media_url: str = "", media_type: str = "",
                              approval_status: str = "pending") -> dict | None:
    """Save an administrator's edits and review decision for a release note."""
    issue_id = str(source_issue_id or "").strip()
    status = str(approval_status or "pending").strip().lower()
    if status not in {"pending", "approved", "rejected"}:
        status = "pending"
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE crm_linear_news
                SET title=%s, summary=%s, body=%s, media_url=%s, media_type=%s,
                    approval_status=%s,
                    reviewed_at=CASE WHEN %s IN ('approved','rejected') THEN now() ELSE NULL END,
                    updated_at=now()
                WHERE source_issue_id=%s
                  AND (%s <> 'approved' OR source_issue_id LIKE 'manual-news-%%'
                       OR (raw->'news_classification'->>'kind' IN ('feature', 'other')
                           AND COALESCE(raw->'linear'->>'parent_id', '') = ''))
                RETURNING *
            """, (str(title or "")[:500], str(summary or "")[:1200],
                   str(body or "")[:12000], str(media_url or "")[:1000],
                   str(media_type or "")[:20], status, status, issue_id, status))
            row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    item = dict(row)
    item["id"] = str(item.get("id") or "")
    for key in ("source_created_at", "done_at", "expires_at", "telegram_published_at", "first_published_at", "reviewed_at", "created_at", "updated_at"):
        if item.get(key):
            item[key] = item[key].isoformat()
    return item


def mark_linear_news_published(*, source_issue_id: str, telegram_message_id: int | None = None) -> None:
    """Record the channel message so each release is published only once."""
    issue_id = str(source_issue_id or "").strip()
    if not issue_id:
        return
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE crm_linear_news
                SET telegram_message_id = %s,
                    telegram_published_at = now(),
                    first_published_at = COALESCE(first_published_at, now()),
                    updated_at = now()
                WHERE source_issue_id = %s
            """, (telegram_message_id, issue_id))
        conn.commit()


def delete_linear_news(*, source_issue_id: str) -> bool:
    """Remove one archived release note by its source Linear issue id."""
    issue_id = str(source_issue_id or "").strip()
    if not issue_id:
        return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crm_linear_news WHERE source_issue_id = %s", (issue_id,))
            deleted = cur.rowcount
        conn.commit()
    return bool(deleted)
