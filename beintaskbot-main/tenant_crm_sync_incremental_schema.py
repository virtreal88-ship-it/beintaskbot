"""New additive migration; do not rewrite the original queue schema."""


def migrate_incremental_sync(cur) -> None:
    cur.execute('ALTER TABLE saas_crm_sync_jobs ADD COLUMN IF NOT EXISTS window_from BIGINT NOT NULL DEFAULT 0')
    cur.execute('ALTER TABLE saas_crm_sync_jobs ADD COLUMN IF NOT EXISTS window_to BIGINT NOT NULL DEFAULT 0')
    cur.execute('ALTER TABLE saas_crm_sync_jobs ADD COLUMN IF NOT EXISTS watermark BIGINT NOT NULL DEFAULT 0')
    cur.execute('ALTER TABLE saas_crm_sync_jobs ADD COLUMN IF NOT EXISTS last_full_sync BIGINT NOT NULL DEFAULT 0')
