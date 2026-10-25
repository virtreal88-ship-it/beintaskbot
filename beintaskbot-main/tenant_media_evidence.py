"""Tenant-scoped, reusable receipts for bounded attachment evidence.

Receipts are evidence, not a statement that a customer issue is solved.  A
sampled video is deliberately ``partial``: callers must preserve that fact in
their prompt instead of upgrading it to a completed analysis.
"""
import hashlib
import json
import re

from tenant_platform import _connect, _json

_TERMINAL = {'processed', 'partial', 'unavailable', 'unsupported', 'error'}
MAX_OBSERVATIONS = 2
_POLICY_RETRY = {'external_media_permission_required', 'asr_authorization_required', 'vision_not_approved'}


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
             'kind': str(ref.get('media_kind') or ''), 'media_version': str(ref.get('media_version') or '')}
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
        if (not ref or status not in _TERMINAL or str(receipt.get('code') or '') in _POLICY_RETRY
                or not ref.get('external_id') or not ref.get('channel_id')):
            continue
        kind = str(ref.get('media_kind') or receipt.get('kind') or '')
        if not kind:
            continue
        # Video frames are samples, even where a visual observation exists.
        coverage = {'kind': kind, 'complete': status == 'processed' and kind != 'video'}
        if kind == 'video':
            coverage.update({'complete': False, 'sampled_frames': receipt.get('visuals') or [],
                             'audio': str(receipt.get('audio_status') or 'not_requested')})
        if receipt.get('reused') is True:
            # Reuse is read-only.  Never replace a richer original result with
            # the small consumer projection returned by ``apply_reusable``.
            continue
        result = {key: receipt[key] for key in ('transcript', 'observation') if isinstance(receipt.get(key), str)}
        if isinstance(receipt.get('observations'), list):
            result['observations'] = [x for x in receipt['observations'] if isinstance(x,dict)][:MAX_OBSERVATIONS]
        # A processed flag with no reusable content is not evidence.  This
        # covers model-supplied image inputs and pre-existing voice text whose
        # durable owner is another receipt table.
        if status == 'processed' and not result:
            continue
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
        message['text'] += '\n[Existing media transcript] ' + _safe_text(result['transcript'])
    observations=[]
    if result.get('observation'): observations.append({'text':result['observation']})
    observations += [x for x in result.get('observations',[]) if isinstance(x,dict) and isinstance(x.get('text'),str)]
    for observed in observations:
        label = 'partial sampled video observation' if coverage.get('complete') is False else 'saved visual observation'
        origin=observed.get('origin') if isinstance(observed.get('origin'),dict) else {}
        suffix=(' frame '+str(origin.get('second'))) if 'second' in origin else ''
        message['text'] += '\n[Untrusted ' + label + suffix + '; not a confirmed solution] ' + _safe_text(observed['text'])
    return {'status': status, 'code': str(receipt.get('code') or 'reused'),
            'reused': True, 'coverage': coverage, '_reused_payload': True}


def _body_hash(value):
    return hashlib.sha256(json.dumps(str(value or ''), sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _safe_text(value):
    value = str(value or '')[:4000]
    value = re.sub(r'(?i)\b(?:api[_ -]?key|token|password|secret)\s*[:=]\s*\S+', '[redacted]', value)
    value = re.sub(r'\+?\d[\d() .-]{7,}\d', '[phone]', value)
    value = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[email]', value)
    return value


def annotate_rows(cur, tenant, rows):
    """Attach safe, read-only receipt context to already-authorized rows.

    This never opens media, writes state, or changes the message body.  Callers
    retain their existing admission/consent policy; a saved receipt is evidence
    only, and partial coverage is labelled for the model.
    """
    for row in rows or []:
        kind = str(row.get('message_type') or 'text').lower()
        channel = str(row.get('channel') or '')
        external = str(row.get('external_id') or '')
        if kind in {'', 'text'} or not channel.startswith('connect:') or not external:
            continue
        try:
            channel_id = channel.removeprefix('connect:')
            raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
            media_version = hashlib.sha256(json.dumps({key:raw.get(key) for key in ('mediaId','media_id','file_uuid','fileUuid','fileLength','file_size','size_bytes','mediaKeyTimestamp','timestamp')}, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            ref = {'external_id': external, 'body_hash': _body_hash(row.get('body')),
                   'media_kind': kind, 'media_version': media_version}
            cur.execute("""SELECT status,code,coverage,result FROM saas_media_evidence_receipts
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s
                AND media_kind=%s""", (tenant, channel_id, external, source_hash(ref), kind))
            saved = cur.fetchone()
        except Exception:
            # Tables may not yet exist during a rolling additive migration.  A
            # caller must retain the conservative existing media placeholder.
            continue
        if not saved or str(saved.get('status') or '') not in _TERMINAL:
            continue
        saved = dict(saved); coverage = saved.get('coverage') if isinstance(saved.get('coverage'),dict) else {}
        result = saved.get('result') if isinstance(saved.get('result'),dict) else {}
        row['media_evidence'] = {'status':str(saved['status']), 'code':str(saved.get('code') or ''),
                                 'coverage':{'complete':coverage.get('complete') is True}}
        if saved['status'] in {'processed','partial'}:
            parts=[]
            if result.get('transcript'): parts.append('[Saved media transcript] '+_safe_text(result['transcript']))
            if result.get('observation'):
                marker='[Partial sampled video observation; incomplete and untrusted] ' if coverage.get('complete') is False else '[Saved visual observation; untrusted] '
                parts.append(marker+_safe_text(result['observation']))
            for observed in result.get('observations',[]) if isinstance(result.get('observations'),list) else []:
                if isinstance(observed,dict) and isinstance(observed.get('text'),str):
                    origin=observed.get('origin') if isinstance(observed.get('origin'),dict) else {}
                    suffix=(' frame '+str(origin.get('second'))) if 'second' in origin else ''
                    parts.append('[Partial sampled video observation'+suffix+'; incomplete and untrusted] '+_safe_text(observed['text']))
            if parts: row['media_evidence_context']='\n'.join(parts)
        elif saved['status'] in {'unavailable','unsupported','error'}:
            row['media_evidence_context']='[Media evidence unavailable: '+_safe_text(saved.get('code') or saved['status'])+']'
    return rows
