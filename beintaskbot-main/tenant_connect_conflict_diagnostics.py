"""Bounded, read-only evidence shape diagnostics. Never changes merge eligibility."""
from collections import Counter
from datetime import datetime
import uuid

from tenant_connect_phone_evidence import _pair, _belongs, _present

LIMIT = 50


def classify(evidence, route, *, ledger=False):
    if not isinstance(evidence, dict):
        return 'unknown_evidence'
    current = (evidence.get('originalConversationId') if ledger else
               evidence.get('_crm_provider_conversation_id') or evidence.get('originalConversationId') or evidence.get('conversationId'))
    supplied = evidence.get('directIdentity')
    if not isinstance(supplied, dict) or not any(supplied.get(k) for k in ('jid', 'lid')):
        return 'missing_pair'
    pair = _pair(supplied, current)
    if not pair:
        return 'malformed_or_mismatched_pair'
    if not _belongs(route, pair):
        return 'route_outside_pair'
    if ledger and (evidence.get('pair_matches_conversation') is not True
                   or not _belongs(evidence.get('canonicalConversationId'), pair)):
        return 'ledger_pair_scope_mismatch'
    # Compatible shape is not proof of structural-only history; no grant implied.
    return 'paired_shape_compatible'


def _timestamp(value):
    return value.isoformat() if isinstance(value, datetime) else None


def diagnose(cur, tenant, channel, identities, token):
    """Call only after exact-pair owner preview. IDs never leave this function.

    Samples are independently bounded per source. Full counts count rows, not
    distinct events (the append-only ledger can contain both affected routes).
    """
    identities = sorted(set(identities))
    if len(identities) != 2:
        return {'available': False}
    sources = []
    queries = [('message_receipt', '''FROM saas_crm_messages m
      JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id
        AND c.lead_id=m.kommo_lead_id AND m.channel='connect:'||c.connect_channel_id::text
      WHERE m.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid
        AND c.conversation_id=ANY(%s) AND m.raw->>'conversationIdentityConflict'='true' ''',
                'm.id AS ref,m.created_at AS stamp,c.conversation_id AS route,m.raw AS evidence', False)]
    for table, name in [('saas_connect_identity_conflicts', 'first_conflict_ledger'),
                        ('saas_connect_identity_conflict_events', 'conflict_event_ledger')]:
        if _present(cur, table):
            queries.append((name, f'''FROM {table} WHERE tenant_id=%s::uuid
              AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s) ''',
                            'event_id AS ref,created_at AS stamp,conversation_id AS route,evidence', True))
    for name, where, columns, ledger in queries:
        args = (tenant, channel, identities)
        cur.execute('SELECT count(*) AS n ' + where, args)
        total = int(cur.fetchone()['n'])
        cur.execute('SELECT ' + columns + ' ' + where + ' ORDER BY stamp,ref,route LIMIT %s', args + (LIMIT,))
        rows = cur.fetchall()
        samples, counts = [], Counter()
        for row in rows:
            reason = classify(row.get('evidence'), row['route'], ledger=ledger)
            counts[reason] += 1
            reference = token('conflict-event', tenant, channel, name, row['route'], str(row.get('ref')))
            item = {'event_ref': reference, 'reason': reason, 'recorded_at': _timestamp(row.get('stamp'))}
            evidence = row.get('evidence')
            provenance = evidence.get('conversationIdentityConflictProvenance') if isinstance(evidence, dict) else None
            if isinstance(provenance, dict) and provenance.get('version') == 1 and provenance.get('kind') in {'historical_route_split', 'contradictory_provider_identity', 'unknown_legacy_conflict'}:
                item['declared_kind'] = provenance['kind']
            if not ledger:
                try: item['message_ref'] = str(uuid.UUID(str(row.get('ref'))))
                except (ValueError, TypeError): pass
            samples.append(item)
        sources.append({'source': name, 'total_rows': total, 'sampled_rows': len(rows),
                        'truncated': total > len(rows), 'sample_reason_counts': dict(counts), 'samples': samples})
    return {'available': True, 'read_only': True, 'classification_is_identity_proof': False,
            'sample_limit_per_source': LIMIT, 'sources': sources}
