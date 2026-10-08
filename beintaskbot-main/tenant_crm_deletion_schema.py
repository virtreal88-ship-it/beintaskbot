"""Additive soft-deletion metadata; historical CRM rows are never removed."""


def migrate_crm_deletions(cur) -> None:
    cur.execute('ALTER TABLE saas_crm_deals ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL')


def migrate_deletion_scan(cur) -> None:
    cur.execute("ALTER TABLE saas_crm_sync_jobs ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT 'active'")
