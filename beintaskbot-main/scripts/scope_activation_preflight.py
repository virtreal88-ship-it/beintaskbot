#!/usr/bin/env python3
"""Read-only activation inventory. Never an authorization to enable scopes.

No application bootstrap, schema repair, provider client or default DB URL is used.
"""
import argparse
import hashlib
import json
import secrets
import sys
from pathlib import Path

# Every query is code-owned, SELECT-only, and returns only a local key.
QUERIES = {
    'historical_ownership_issues': "SELECT tenant_id::text||':'||resource_table||':'||resource_id AS key FROM public.saas_access_migration_issues",
    'inactive_members': "SELECT tenant_id::text||':'||telegram_id AS key FROM public.saas_tenant_members WHERE active IS NOT TRUE",
    'active_members_without_department': "SELECT tenant_id::text||':'||telegram_id AS key FROM public.saas_tenant_members WHERE active AND department_id IS NULL",
    'inactive_or_missing_departments': "SELECT m.tenant_id::text||':'||m.telegram_id AS key FROM public.saas_tenant_members m LEFT JOIN public.saas_tenant_departments d ON d.tenant_id=m.tenant_id AND d.id=m.department_id WHERE m.active AND m.department_id IS NOT NULL AND (d.id IS NULL OR d.active IS NOT TRUE)",
    'ineligible_tenants': "SELECT id::text AS key FROM public.saas_tenants WHERE status NOT IN ('active','onboarding','ready_for_integration')",
    'ambiguous_company_owners': "SELECT t.id::text AS key FROM public.saas_tenants t LEFT JOIN public.saas_tenant_members m ON m.tenant_id=t.id AND m.role='owner' GROUP BY t.id,t.owner_telegram_id HAVING count(m.telegram_id)<>1 OR NOT coalesce(bool_and(m.active),false) OR min(m.telegram_id) IS DISTINCT FROM t.owner_telegram_id",
    'connect_unassigned_sources': "SELECT ch.tenant_id::text||':'||ch.connect_channel_id AS key FROM public.saas_connect_channels ch WHERE NOT EXISTS(SELECT 1 FROM public.saas_dialog_source_members s JOIN public.saas_tenant_members m ON m.tenant_id=s.tenant_id AND m.telegram_id=s.employee_id AND m.active WHERE s.tenant_id=ch.tenant_id AND s.source_id=ch.connect_channel_id AND s.provider='connect')",
    'invalid_source_members': "SELECT s.tenant_id::text||':'||s.source_id||':'||s.employee_id AS key FROM public.saas_dialog_source_members s LEFT JOIN public.saas_tenant_members m ON m.tenant_id=s.tenant_id AND m.telegram_id=s.employee_id LEFT JOIN public.saas_connect_channels ch ON ch.tenant_id=s.tenant_id AND ch.connect_channel_id=s.source_id WHERE m.active IS NOT TRUE OR ch.connect_channel_id IS NULL OR s.provider<>'connect'",
    'kommo_source_mapping_required': "SELECT DISTINCT tenant_id::text AS key FROM public.saas_crm_messages WHERE channel NOT LIKE 'connect:%%'",
    'legacy_processing_or_unknown': "SELECT id::text AS key FROM public.saas_crm_sync_outbox WHERE state IN ('processing','unknown')",
    'legacy_attempted_pending': "SELECT id::text AS key FROM public.saas_crm_sync_outbox WHERE state='pending' AND attempts>0",
    'legacy_unresolved_possible_send': "SELECT event_id::text AS key FROM public.saas_crm_legacy_delivery_attempts WHERE outcome IN ('sending','unknown')",
    'legacy_claim_aware_unsent': "SELECT o.id::text AS key FROM public.saas_crm_sync_outbox o JOIN public.saas_crm_legacy_delivery_attempts a ON a.event_id=o.id WHERE a.outcome='not_started' AND a.claim_attempt=o.attempts",
    'legacy_mismatched_unsent_evidence': "SELECT a.event_id::text AS key FROM public.saas_crm_legacy_delivery_attempts a LEFT JOIN public.saas_crm_sync_outbox o ON o.id=a.event_id WHERE a.outcome='not_started' AND (o.id IS NULL OR a.claim_attempt IS DISTINCT FROM o.attempts)",
    'legacy_links_without_bound_identity': "SELECT l.tenant_id::text||':'||l.entity_type||':'||l.internal_id AS key FROM public.saas_crm_entity_links l WHERE NOT EXISTS(SELECT 1 FROM public.saas_crm_relation_identities i WHERE i.tenant_id=l.tenant_id AND i.entity_type=l.entity_type AND i.internal_id=l.internal_id AND i.provider=l.provider)",
    'bound_identity_inventory': "SELECT tenant_id::text||':'||entity_type||':'||internal_id AS key FROM public.saas_crm_relation_identities",
    'scalar_unverified_binding': "SELECT id::text AS key FROM public.saas_crm_scalar_outbox WHERE binding IS NULL OR external_id IS NULL OR state='needs_verification'",
    'scalar_provenance_heads': "SELECT tenant_id::text||':'||entity_type||':'||internal_id AS key FROM public.saas_crm_scalar_heads",
    'relation_provenance_heads': "SELECT tenant_id::text||':'||changed_type||':'||changed_id AS key FROM public.saas_crm_relation_heads",
}
QUERIES['stale_account_or_generation_identities'] = """SELECT i.tenant_id::text||':'||i.entity_type||':'||i.internal_id AS key
 FROM public.saas_crm_relation_identities i WHERE NOT EXISTS (
 SELECT 1 FROM public.saas_tenant_integrations c WHERE c.tenant_id=i.tenant_id
 AND c.provider IN ('kommo','kommo_manual') AND c.status='connected'
 AND c.account_domain=i.account_id
 AND split_part(i.connection_generation,':',1)=c.provider
 AND substring(i.connection_generation FROM position(':' IN i.connection_generation)+1)::timestamptz=c.connected_at
 AND (c.provider='kommo' OR NOT EXISTS(SELECT 1 FROM public.saas_tenant_integrations preferred
 WHERE preferred.tenant_id=c.tenant_id AND preferred.provider='kommo' AND preferred.status='connected')))"""
for outcome in ('not_started','sending','unknown','completed'):
    QUERIES['legacy_evidence_' + outcome] = f"SELECT event_id::text AS key FROM public.saas_crm_legacy_delivery_attempts WHERE outcome='{outcome}'"
for table in ('saas_crm_native_contacts', 'saas_crm_native_companies', 'saas_crm_native_deals',
              'saas_crm_native_tasks', 'saas_ai_agents', 'saas_support_tickets'):
    QUERIES['unowned_' + table] = f'SELECT tenant_id::text||\':\'||id AS key FROM public.{table} WHERE access_responsible_id IS NULL'
for journal, states in {
    'scalar': ('pending','processing','synced','disabled','superseded','conflict','unknown','needs_verification'),
    'relation': ('pending','processing','synced','superseded','disabled','conflict','unknown'),
}.items():
    for state in states:
        QUERIES[f'{journal}_{state}'] = f"SELECT id::text AS key FROM public.saas_crm_{journal}_outbox WHERE state='{state}'"
    QUERIES[f'{journal}_sending'] = f"SELECT id::text AS key FROM public.saas_crm_{journal}_outbox WHERE attempt_outcome='sending'"

MANDATORY_EXTERNAL_EVIDENCE = [
    'all_process_versions_and_old_worker_drain_verified',
    'provider_conditional_relation_and_unknown_completion_guarantees',
    'production_adapter_and_account_generation_mapping_registration',
    'durable_recovery_and_mixed_writer_provenance_resolution',
    'source_ownership_and_effective_access_comparison_reviewed',
    'exact_release_ci_postgres_races_browser_and_migrations_verified',
    'compatible_rollback_preserves_provenance_and_admission_fences',
]


def inventory(connection, *, sample_limit=20):
    """Own a fresh connection transaction; rollback on success AND failure.

    Counts include all tenants. Samples are salted hashes, not Telegram IDs,
    account identifiers, provider IDs, names, messages, credentials or DSNs.
    Missing tables/columns stay unknown; no repair or guessed zero is allowed.
    """
    if not isinstance(sample_limit, int) or not 0 <= sample_limit <= 100:
        raise ValueError('invalid_sample_limit')
    salt = secrets.token_bytes(32)
    checks = {}
    try:
        with connection.cursor() as cur:
            cur.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cur.execute("SET LOCAL search_path TO pg_catalog, public")
            cur.execute("SET LOCAL statement_timeout TO '15s'")
            cur.execute("SET LOCAL lock_timeout TO '2s'")
            cur.execute('SHOW transaction_read_only')
            if cur.fetchone()['transaction_read_only'] != 'on':
                raise RuntimeError('read_only_transaction_required')
            for name, query in QUERIES.items():
                cur.execute('SAVEPOINT inventory_check')
                try:
                    cur.execute('SELECT count(*) AS count FROM (' + query + ') AS inventory')
                    count = int(cur.fetchone()['count'])
                    cur.execute('SELECT key FROM (' + query + ') AS inventory ORDER BY key LIMIT %s', (sample_limit,))
                    tokens = [hashlib.sha256(salt + name.encode() + str(r['key']).encode()).hexdigest()[:24]
                              for r in cur.fetchall()]
                    checks[name] = {'status': 'observed', 'count': count, 'sample_ids': tokens,
                                    'sample_truncated': count > sample_limit}
                except Exception:
                    # Never render DB errors: they may contain credentials/data.
                    cur.execute('ROLLBACK TO SAVEPOINT inventory_check')
                    checks[name] = {'status': 'unknown', 'reason': 'query_unavailable_or_failed'}
                cur.execute('RELEASE SAVEPOINT inventory_check')
    finally:
        connection.rollback()
    return {'format_version': 1, 'safe_to_enable': False, 'decision': 'NOT_SAFE_TO_ENABLE',
            'scope': 'all_tenants_read_only_snapshot', 'checks': checks,
            'mandatory_external_evidence_not_certified': MANDATORY_EXTERNAL_EVIDENCE,
            'limitations': ['No provider, worker version, environment flag or remote completion was inspected.',
                'Bound identity presence is not account/generation validity proof.',
                'Historical issue receipts may remain after resolution; compare current unowned counts.',
                'Inactive members and absent departments are review inventory, not automatic defects.',
                'A zero count is not rollout approval. Samples are opaque and only comparable within this report.']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn-file', required=True, help='Explicit file containing complete host, dbname and user DSN; never DATABASE_URL.')
    parser.add_argument('--sample-limit', type=int, choices=range(0,101), default=20, metavar='0..100')
    args = parser.parse_args(argv)
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
        dsn = Path(args.dsn_file).read_text().strip()
        parsed = conninfo_to_dict(dsn)
        if not all(parsed.get(k) for k in ('host','dbname','user')) or parsed.get('service'):
            raise ValueError('explicit_connection_required')
        # Avoid libpq ambient defaults influencing the supplied target/session.
        import os
        for name in tuple(os.environ):
            if name.startswith('PG'):
                os.environ.pop(name)
        with psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                             connect_timeout=5, options='-c default_transaction_read_only=on') as connection:
            report = inventory(connection, sample_limit=args.sample_limit)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 2  # Inventory never certifies operational/provider guarantees.
    except Exception:
        print(json.dumps({'safe_to_enable': False, 'decision': 'NOT_SAFE_TO_ENABLE',
                          'error': 'preflight_connection_or_snapshot_failed'}))
        return 3


if __name__ == '__main__':
    sys.exit(main())
