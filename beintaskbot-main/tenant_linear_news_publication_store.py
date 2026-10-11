"""Linear news publication markers, migrations and retention."""
from tenant_platform_core import _connect, _ensure_schema

def reset_linear_news_publishing_once(*, migration_key: str) -> bool:
    """Clear channel delivery marks once for an intentional feed reseed."""
    key = str(migration_key or "").strip()
    if not key:
        return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_news_migrations (migration_key)
                VALUES (%s)
                ON CONFLICT (migration_key) DO NOTHING
                RETURNING migration_key
            """, (key,))
            applied = bool(cur.fetchone())
            if applied:
                cur.execute("""
                    UPDATE crm_linear_news
                    SET telegram_message_id = NULL,
                        telegram_published_at = NULL,
                        updated_at = now()
                """)
        conn.commit()
    return applied


def require_linear_news_review_once(*, migration_key: str) -> bool:
    """Move legacy unreviewed releases into the explicit admin review queue."""
    key = str(migration_key or "").strip()
    if not key:
        return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_news_migrations (migration_key)
                VALUES (%s)
                ON CONFLICT (migration_key) DO NOTHING
                RETURNING migration_key
            """, (key,))
            applied = bool(cur.fetchone())
            if applied:
                cur.execute("""
                    UPDATE crm_linear_news
                    SET approval_status='pending',
                        reviewed_at=NULL,
                        updated_at=now()
                    WHERE approval_status='approved' AND reviewed_at IS NULL
                      AND first_published_at IS NULL
                      AND telegram_message_id IS NULL AND telegram_published_at IS NULL
                """)
        conn.commit()
    return applied


def restore_linear_news_publications_once() -> None:
    """Preserve delivery history and recover the release reported by its owner."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE crm_linear_news
                SET first_published_at=COALESCE(first_published_at, telegram_published_at, updated_at),
                    approval_status='approved',
                    reviewed_at=COALESCE(reviewed_at, telegram_published_at, updated_at)
                WHERE (telegram_message_id IS NOT NULL OR telegram_published_at IS NOT NULL)
                  AND first_published_at IS NULL
            """)
            cur.execute("""
                INSERT INTO crm_linear_news_migrations (migration_key)
                VALUES ('restore-owner-confirmed-bs-1321-v1')
                ON CONFLICT (migration_key) DO NOTHING RETURNING migration_key
            """)
            if cur.fetchone():
                # The old migration erased delivery marks. The owner explicitly
                # confirmed this identifier was already shared; do not resend it.
                cur.execute("""
                    UPDATE crm_linear_news
                    SET approval_status='approved',
                        reviewed_at=COALESCE(reviewed_at, now()),
                        first_published_at=COALESCE(first_published_at, now()),
                        updated_at=now()
                    WHERE identifier='BS-1321'
                """)
        conn.commit()


def claim_linear_news_publish_slot(*, slot_key: str) -> bool:
    """Atomically reserve one scheduled Telegram publication slot."""
    key = str(slot_key or "").strip()
    if not key:
        return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_news_migrations (migration_key)
                VALUES (%s)
                ON CONFLICT (migration_key) DO NOTHING
                RETURNING migration_key
            """, (key,))
            claimed = bool(cur.fetchone())
        conn.commit()
    return claimed


def seed_linear_news_review_once(*, migration_key: str) -> bool:
    """Put one existing approved release into review for the first UX test."""
    key = str(migration_key or "").strip()
    if not key:
        return False
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO crm_linear_news_migrations (migration_key)
                VALUES (%s)
                ON CONFLICT (migration_key) DO NOTHING
                RETURNING migration_key
            """, (key,))
            applied = bool(cur.fetchone())
            if applied:
                cur.execute("""
                    UPDATE crm_linear_news
                    SET approval_status='pending', telegram_message_id=NULL,
                        telegram_published_at=NULL, updated_at=now()
                    WHERE id = (
                        SELECT id FROM crm_linear_news
                        WHERE approval_status='approved' AND expires_at > now()
                        ORDER BY done_at DESC, created_at DESC LIMIT 1
                    )
                """)
        conn.commit()
    return applied


def prune_linear_news() -> int:
    """Delete release notes after their 90-day public retention window."""
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM crm_linear_news WHERE expires_at <= now()")
            deleted = cur.rowcount
        conn.commit()
    return int(deleted or 0)
