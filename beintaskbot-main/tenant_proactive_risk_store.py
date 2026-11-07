"""Dry-run finding persistence and owner-only preview reads; never delivers messages."""
import json
from datetime import datetime, timedelta, timezone
from tenant_platform import _connect, _read_tenant_workflow
from tenant_linear_policy import TenantLinearError, owner
from tenant_policy import TenantPolicy
from tenant_proactive_risk_policy import fingerprint
from tenant_proactive_risk_schema import ensure_schema


def replace_source_findings(cur, tenant_id: str, source_kind: str, source_id: str,
                            source_version: str, source_title: str,
                            findings: list[dict]) -> None:
    """Replace the current dry-run view atomically when a source is observed."""
    if source_kind not in {'linear', 'crm_task'}:
        raise ValueError('Invalid risk source')
    cur.execute('DELETE FROM saas_proactive_risk_findings WHERE tenant_id=%s::uuid AND source_kind=%s AND source_id=%s',
                (tenant_id, source_kind, str(source_id)))
    digest = fingerprint(findings)
    for item in findings:
        cur.execute('''INSERT INTO saas_proactive_risk_findings
          (tenant_id,source_kind,source_id,rule_id,source_version,severity,confidence,source_title,source_url,
           facts,assessment,recommended_step,fingerprint,checked_at,active)
          VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,now(),TRUE)
          ON CONFLICT(tenant_id,source_kind,source_id,rule_id) DO UPDATE SET
            source_version=EXCLUDED.source_version,severity=EXCLUDED.severity,confidence=EXCLUDED.confidence,
            source_title=EXCLUDED.source_title,source_url=EXCLUDED.source_url,facts=EXCLUDED.facts,
            assessment=EXCLUDED.assessment,recommended_step=EXCLUDED.recommended_step,
            fingerprint=EXCLUDED.fingerprint,checked_at=EXCLUDED.checked_at,active=TRUE''',
          (tenant_id, source_kind, str(source_id), item['rule_id'], str(source_version), item['severity'],
           item['confidence'], str(source_title or '')[:200], item.get('source_url') or '',
           json.dumps(item.get('facts') or [], ensure_ascii=False), str(item['assessment'])[:800],
           str(item['recommended_step'])[:400], digest))


def mark_checked(cur, tenant_id: str, source_kind: str) -> None:
    if source_kind not in {'linear', 'crm_task'}:
        raise ValueError('Invalid risk source')
    cur.execute('''INSERT INTO saas_proactive_risk_checks(tenant_id,source_kind,checked_at)
      VALUES(%s::uuid,%s,now()) ON CONFLICT(tenant_id,source_kind) DO UPDATE SET checked_at=EXCLUDED.checked_at''',
      (tenant_id, source_kind))


def _active_sources(cur, tenant: str, profile: dict) -> list[str]:
    policy = TenantPolicy(profile)
    sources = []
    if policy.allows('linear'):
        cur.execute("""SELECT 1 FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='linear'
          AND status='connected' AND metadata->'settings'->'workflow'->'enabled'='true'::jsonb
          AND metadata->'settings'->'workflow'->'sync_enabled'='true'::jsonb""", (tenant,))
        if cur.fetchone():
            sources.append('linear')
    if policy.allows('tasks'):
        cur.execute("SELECT 1 FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected'", (tenant,))
        if cur.fetchone():
            sources.append('crm_task')
    return sources


def list_findings(session: dict, *, limit: int = 50) -> dict:
    tenant, actor = owner(session)
    safe_limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules,t.notification_rules
              FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
              WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, actor))
            profile = cur.fetchone()
            if (not profile or profile.get('active') is not True or profile.get('role') != 'owner'
                    or profile.get('tenant_status') != 'active'):
                raise TenantLinearError('Yalnız aktiv şirkət sahibi üçün.', 403)
            profile = {**profile, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)}
            sources = _active_sources(cur, tenant, profile)
            if not sources:
                return {'tenant_id': tenant, 'user_id': actor, 'mode': 'dry_run', 'deliveries_enabled': False,
                        'findings': [], 'checked_at': None, 'unavailable_sources': ['Linear', 'CRM']}
            cur.execute('''SELECT source_kind,source_id,rule_id,source_version,severity,confidence,source_title,
                    source_url,facts,assessment,recommended_step,fingerprint,checked_at
              FROM saas_proactive_risk_findings WHERE tenant_id=%s::uuid AND active=TRUE
                AND source_kind=ANY(%s) ORDER BY CASE severity WHEN 'P1' THEN 0 ELSE 1 END,checked_at DESC
              LIMIT %s''', (tenant, sources, safe_limit))
            rows = cur.fetchall()
            cur.execute('''SELECT max(checked_at) AS checked_at FROM saas_proactive_risk_findings
              WHERE tenant_id=%s::uuid AND source_kind=ANY(%s)''', (tenant, sources))
            last_finding = cur.fetchone()['checked_at']
            cur.execute('''SELECT max(checked_at) AS checked_at FROM saas_proactive_risk_checks
              WHERE tenant_id=%s::uuid AND source_kind=ANY(%s)''', (tenant, sources))
            last_check = cur.fetchone()['checked_at']
    findings = []
    now = datetime.now(timezone.utc)
    for row in rows:
        source_url = row.get('source_url') or ''
        if row['source_kind'] == 'linear' and not source_url.startswith('https://linear.app/'):
            source_url = ''
        elif row['source_kind'] == 'crm_task':
            source_url = '/app?view=tasks'
        findings.append({**row, 'source_url': source_url, 'source_updated_at': row['source_version'],
            'checked_at': row['checked_at'].isoformat(),
            'freshness': 'stale' if now - row['checked_at'].astimezone(timezone.utc) > timedelta(minutes=15) else 'recent',
            'source': 'Linear' if row['source_kind'] == 'linear' else 'CRM task'})
    return {'tenant_id': tenant, 'user_id': actor, 'mode': 'dry_run', 'deliveries_enabled': False,
            'findings': findings, 'checked_at': max((value for value in (last_finding, last_check) if value), default=None).isoformat() if (last_finding or last_check) else None,
            'unavailable_sources': [name for kind, name in (('linear', 'Linear'), ('crm_task', 'CRM')) if kind not in sources]}
