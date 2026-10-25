"""Tenant-scoped, reusable receipts for bounded attachment evidence.

Receipts are evidence, not a statement that a customer issue is solved.  A
sampled video is deliberately ``partial``: callers must preserve that fact in
their prompt instead of upgrading it to a completed analysis.
"""
import hashlib
import json

from tenant_platform import _connect, _json

_TERMINAL = {'processed', 'partial', 'unavailable', 'unsupported', 'error'}


def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_media_evidence_receipts(
          tenant_id UUID NOT NULL, channel_id UUID NOT NULL, external_id TEXT NOT NULL,
          source_hash TEXT NOT NULL, media_kind TEXT NOT NULL, direction TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('processed','partial','unavailable','unsupported','error')),
          code TEXT NOT NULL DEFAULT '', coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
          result JSONB NOT NULL DEFAULT '{}'::jsonb, provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,channel_id,external_id,source_hash))""")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_media_evidence_lookup_idx ON saas_media_evidence_receipts(tenant_id,channel_id,external_id,updated_at DESC)")


def source_hash(ref):
    """Bind a receipt to the exact selected message version, never a bare JID."""
    value = {'id': str(ref.get('external_id') or ''), 'body_hash': str(ref.get('body_hash') or ''),
             'kind': str(ref.get('media_kind') or '')}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def reusable(profile, ref):
    """Return only the exact-tenant/channel/version terminal receipt, if any."""
    if not isinstance(profile, dict) or not profile.get('tenant_id'):
        return None
    tenant = str(profile['tenant_id'])
    external = str(ref.get('external_id') or '')
    channel = str(ref.get('channel_id') or '')
    if not (external and channel and ref.get('media_kind')):
        return None
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT status,code,coverage,result,provenance FROM saas_media_evidence_receipts
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s
                AND media_kind=%s""", (tenant, channel, external, source_hash(ref), str(ref['media_kind'])))
            row = cur.fetchone()
    return dict(row) if row and row.get('status') in _TERMINAL else None


def persist(cur, tenant, refs, receipts):
    """Persist terminal outcomes after the extraction's source revalidation.

    This accepts neither URLs nor provider payloads.  The selected extraction
    reference supplies the tenant/channel/message/version fence.
    """
    by_id = {str(ref.get('id')): ref for ref in refs or []}
    for receipt in receipts or []:
        ref = by_id.get(str(receipt.get('id')))
        status = str(receipt.get('status') or '')
        if not ref or status not in _TERMINAL or not ref.get('external_id') or not ref.get('channel_id'):
            continue
        kind = str(ref.get('media_kind') or receipt.get('kind') or '')
        if not kind:
            continue
        # Video frames are samples, even where a visual observation exists.
        coverage = {'kind': kind, 'complete': status == 'processed' and kind != 'video'}
        if kind == 'video':
            coverage.update({'complete': False, 'sampled_frames': receipt.get('visuals') or [],
                             'audio': str(receipt.get('audio_status') or 'not_requested')})
        result = {key: receipt[key] for key in ('transcript', 'observation') if isinstance(receipt.get(key), str)}
        provenance = {'processor': 'knowledge_extraction', 'source_version': source_hash(ref),
                      'untrusted': True}
        cur.execute("""INSERT INTO saas_media_evidence_receipts
          (tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,coverage,result,provenance)
          VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
          ON CONFLICT(tenant_id,channel_id,external_id,source_hash) DO UPDATE SET
            status=EXCLUDED.status,code=EXCLUDED.code,coverage=EXCLUDED.coverage,result=EXCLUDED.result,
            provenance=EXCLUDED.provenance,updated_at=now()""",
          (tenant, str(ref['channel_id']), str(ref['external_id']), source_hash(ref), kind,
           str(ref.get('role') or 'unknown'), status, str(receipt.get('code') or ''),
           _json(coverage), _json(result), _json(provenance)))


def apply_reusable(message, receipt):
    """Add only clearly-labelled saved evidence to a new extraction request."""
    status = str(receipt.get('status') or '')
    result = receipt.get('result') if isinstance(receipt.get('result'), dict) else {}
    coverage = receipt.get('coverage') if isinstance(receipt.get('coverage'), dict) else {}
    if result.get('transcript'):
        message['text'] += '\n[Existing media transcript] ' + str(result['transcript'])[:4000]
    if result.get('observation'):
        label = 'partial sampled video observation' if coverage.get('complete') is False else 'saved visual observation'
        message['text'] += '\n[Untrusted ' + label + '; not a confirmed solution] ' + str(result['observation'])[:4000]
    return {'status': status, 'code': str(receipt.get('code') or 'reused'),
            'reused': True, 'coverage': coverage}
