"""Synthetic integration seam; not imported by any production endpoint.

The caller must own authorization and provider I/O, commit before send, and
prove all workers and DB privileges obey the scheme. This module does none of
those things and must not be used for paid calls.
"""
import tenant_voice_test_grant as grant
from tenant_linear_policy import TenantLinearError


def issue(cur,scope):
    grant._ready();s=grant._scope(scope);grant._lock(cur)
    # Global lock -> exact voice row -> tenant advisory lock, same ordering as
    # the candidate. No automatic legacy backfill is assumed.
    cur.execute('''SELECT h.*,p.reservation_id AS pinned_id,p.tenant_id AS pinned_tenant,
      p.call_key AS pinned_call_key,p.kind AS pinned_kind,p.model AS pinned_model,
      p.quote_version AS pinned_quote,p.initial_reserved AS pinned_reserved,p.fingerprint AS pinned_fingerprint
      FROM saas_voice_test_hold h LEFT JOIN saas_voice_provenance p ON p.request_id=h.request_id
      WHERE h.request_id=%s AND h.fingerprint=%s FOR UPDATE OF h''',(s['request_id'],grant._fingerprint(s)))
    row=cur.fetchone()
    if not row or row['state']!='claimed':return None
    if (str(row['pinned_id'] or '')!=str(row['tenant_reservation_id']) or
        str(row['pinned_tenant'] or '')!=s['tenant_id'] or row['pinned_call_key']!='voice-test:'+s['request_id'] or
        row['pinned_kind']!='realtime_voice_test' or row['pinned_model']!=row['model'] or
        row['pinned_quote']!=row['quote_version'] or row['pinned_reserved']!=row['reserved_microusd'] or
        row['pinned_fingerprint']!=row['fingerprint']):
        raise TenantLinearError('voice_test_provenance_conflict',409)
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
    # issue_dispatch repeats the tenant hold, quote, cap and freeze checks and
    # consumes the one-use claim in this transaction.
    return grant.issue_dispatch(cur,s)
