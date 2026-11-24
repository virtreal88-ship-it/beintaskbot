"""Bounded read-only historical pair evidence, never a current device proof.

Rows must originate from the signed provider-ingestion ledger. Callers still
must validate current scoped provider identity, source generation, ACL and all
other merge fences. A safe result alone does not authorize a merge.
"""
from tenant_connect_direct_identity import direct_identity, phone_jid

MAX_EVIDENCE_ROWS = 512


def evaluate_pair_evidence(rows, *, scan_limit=MAX_EVIDENCE_ROWS):
    """Evaluate normalized SQL facts; overflow and unknown facts fail closed."""
    if not isinstance(scan_limit, int) or isinstance(scan_limit, bool) or not 1 <= scan_limit <= MAX_EVIDENCE_ROWS:
        return {'safe': False, 'reason': 'invalid_scan_limit', 'scanned': 0}
    if len(rows) > scan_limit:
        return {'safe': False, 'reason': 'evidence_scan_limit', 'scanned': len(rows)}
    proofs = {}
    for row in rows:
        if row.get('bracketing_proof') is True and row.get('at') is not None:
            proofs.setdefault(row['conversation_id'], []).append(row['at'])
    for row in rows:
        if row.get('matching_pair') is True:
            continue
        at = row.get('at')
        times = proofs.get(row.get('conversation_id'), [])
        if not (row.get('historical_self_only') is True and at is not None and times
                and min(times) < at < max(times)):
            return {'safe': False, 'reason': 'identity_conflict', 'scanned': len(rows)}
    return {'safe': True, 'reason': 'historical_evidence_clear', 'scanned': len(rows)}


def check_pair_evidence(cur, tenant, channel, primary_id, alternate_id, verified_at, *, scan_limit=MAX_EVIDENCE_ROWS):
    """Check only two exact ledger routes, using at most scan_limit+1 rows.

    Matching pair rows are harmless. A self-only row is harmless only if
    strictly bracketed by matching same-route receipts, all predating
    verified_at. An exact mirror under the same number's other phone address
    is evaluated through its original row. Missing, contradictory or
    one-sided evidence blocks.
    No provider calls, message-body reads, channel discovery or mutations.
    """
    if (direct_identity(primary_id) != primary_id or not phone_jid(primary_id)
            or direct_identity(alternate_id) != alternate_id or not alternate_id.endswith('@lid')
            or verified_at is None):
        return {'safe': False, 'reason': 'invalid_pair', 'scanned': 0}
    if not isinstance(scan_limit, int) or isinstance(scan_limit, bool) or not 1 <= scan_limit <= MAX_EVIDENCE_ROWS:
        return {'safe': False, 'reason': 'invalid_scan_limit', 'scanned': 0}
    aliases = [phone_jid(primary_id), phone_jid(primary_id).replace('@s.whatsapp.net', '@c.us')]
    mirror_id = next(alias for alias in aliases if alias != primary_id)
    routes = [primary_id, alternate_id]
    keys=list(set(routes+aliases))
    # The summary is first-write-only. Reject legacy summaries without an
    # identical receipt rather than pretending the historical proof covers it.
    cur.execute("""SELECT 1 FROM saas_connect_identity_conflicts f
        WHERE f.tenant_id=%s::uuid AND f.connect_channel_id=%s::uuid
          AND f.conversation_id=ANY(%s::text[]) AND NOT EXISTS (
            SELECT 1 FROM saas_connect_identity_conflict_events e
             WHERE (e.tenant_id,e.connect_channel_id,e.conversation_id,e.event_id)=
                   (f.tenant_id,f.connect_channel_id,f.conversation_id,f.event_id)
               AND e.evidence=f.evidence) LIMIT 1""",(tenant,channel,keys))
    if cur.fetchone():
        return {'safe':False,'reason':'summary_missing_evidence','scanned':0}
    cur.execute('''SELECT conversation_id,EXTRACT(EPOCH FROM created_at) AS at,
        (evidence->>'pair_matches_conversation'='true'
          AND evidence->'directIdentity'->>'lid'=%s
          AND evidence->'directIdentity'->>'jid'=ANY(%s::text[])) IS TRUE AS matching_pair,
        (evidence->>'pair_matches_conversation'='true'
          AND evidence->'directIdentity'->>'lid'=%s
          AND evidence->'directIdentity'->>'jid'=ANY(%s::text[])
          AND evidence->>'originalConversationId'=ANY(%s::text[])
          AND evidence->>'canonicalConversationId'=ANY(%s::text[])
          AND created_at<%s::timestamptz) IS TRUE AS bracketing_proof,
        (evidence->>'pair_matches_conversation'='false'
          AND conversation_id=ANY(%s::text[])
          AND evidence->'directIdentity'->>'jid'=''
          AND evidence->'directIdentity'->>'lid'=''
          AND evidence->>'originalConversationId'=conversation_id
          AND evidence->>'canonicalConversationId'=conversation_id
          AND created_at<%s::timestamptz) IS TRUE AS historical_self_only
      FROM saas_connect_identity_conflict_events f
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
        AND conversation_id=ANY(%s::text[])
        AND NOT (f.conversation_id=%s AND EXISTS (
          SELECT 1 FROM saas_connect_identity_conflict_events original
          WHERE original.tenant_id=f.tenant_id AND original.connect_channel_id=f.connect_channel_id
            AND original.conversation_id=%s AND original.event_id=f.event_id
            AND original.created_at=f.created_at AND original.evidence=f.evidence))
      LIMIT %s''',
      (alternate_id, aliases, alternate_id, aliases, routes, routes, verified_at,
       routes, verified_at, tenant, channel, keys, mirror_id, primary_id, scan_limit + 1))
    return evaluate_pair_evidence(cur.fetchall(), scan_limit=scan_limit)
