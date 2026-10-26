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
_SAFE_FAILURE_STAGES = {'source_validation', 'media_download', 'media_validation', 'video_decode', 'audio_transcription', 'visual_analysis', 'visual_input_prepare'}


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



def media_version(raw):
    """Existing attachment identity/version fence shared by read projections."""
    raw=raw if isinstance(raw,dict) else {}
    return hashlib.sha256(json.dumps({key:raw.get(key) for key in ('mediaId','media_id','file_uuid','fileUuid','fileLength','file_size','size_bytes','mediaKeyTimestamp','timestamp')}, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def publication_fingerprint(cur,tenant,row):
    """Hash exact saved media evidence in the caller's transaction; no fetches.

    Return only hashes, never attachment URLs/raw fields or receipt text. Lock
    an existing receipt so edits/withdrawal serialize with publication.
    """
    kind=str(row.get('message_type') or 'text').lower()
    if kind in {'','text'}:return None
    channel=str(row.get('channel') or '')
    ref={'external_id':row.get('external_id'),'body_hash':_body_hash(row.get('body')),
         'media_kind':kind,'media_version':media_version(row.get('raw'))}
    version=source_hash(ref);saved=None
    cur.execute("SELECT to_regclass('public.saas_media_evidence_receipts') AS relation")
    if cur.fetchone()['relation']:
        cur.execute("""SELECT status,code,coverage,result,provenance,media_kind,direction,created_at,updated_at
          FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
            AND external_id=%s AND source_hash=%s AND media_kind=%s FOR SHARE""",
          (tenant,channel.removeprefix('connect:'),str(ref['external_id'] or ''),version,kind))
        saved=cur.fetchone()
    snapshot=json.dumps(dict(saved) if saved else None,sort_keys=True,default=str,ensure_ascii=False,separators=(',',':'))
    return {'source_hash':version,'receipt_hash':hashlib.sha256(snapshot.encode()).hexdigest()}

def reusable(profile, ref):
    """Return only the exact-tenant/channel/version terminal receipt, if any."""
    if not isinstance(profile, dict) or not profile.get('tenant_id'):
        return None
    tenant = str(profile['tenant_id'])
    external = str(ref.get('external_id') or '')
    channel = str(ref.get('channel_id') or '')
    if not (external and channel and ref.get('media_kind')):
        return None
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT status,code,coverage,result,provenance FROM saas_media_evidence_receipts
                  WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s
                    AND media_kind=%s""", (tenant, channel, external, source_hash(ref), str(ref['media_kind'])))
                row = cur.fetchone()
    except Exception:
        # Receipt reuse is an optional, conservative optimization during an
        # additive rollout.  If its table/connection is not ready, callers
        # must continue their existing authorization path, not mark media as
        # analysed or leave the surrounding request transaction poisoned.
        return None
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
        if receipt.get('failure_stage') in _SAFE_FAILURE_STAGES:
            result['failure_stage'] = receipt['failure_stage']
        from tenant_chat_ai_provider import is_safe_failure_code
        if is_safe_failure_code(receipt.get('failure_code')):
            result['failure_code']=receipt['failure_code']
        if isinstance(receipt.get('observations'), list):
            result['observations'] = [x for x in receipt['observations'] if isinstance(x,dict)][:MAX_OBSERVATIONS]
        # An empty terminal result is not reusable evidence.  In particular,
        # a visual budget/consent stop or a model-supplied image must not
        # permanently prevent a later separately-authorized analysis.
        if status in {'processed', 'partial'} and not result:
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
    # Keep the exact saved text with the extraction receipt as well as its
    # model context. Public projections retain their existing content gate;
    # persist() skips reused receipts and cannot overwrite the original.
    payload={key:_safe_text(result[key]) for key in ('transcript','observation')
             if isinstance(result.get(key),str) and result[key].strip()}
    from tenant_chat_ai_provider import is_safe_failure_code
    if is_safe_failure_code(result.get('failure_code')):payload['failure_code']=result['failure_code']
    if result.get('failure_stage') in _SAFE_FAILURE_STAGES:payload['failure_stage']=result['failure_stage']
    if isinstance(result.get('observations'),list):
        payload['observations']=[{'text':_safe_text(x['text'])} for x in result['observations']
            if isinstance(x,dict) and isinstance(x.get('text'),str) and x['text'].strip()][:MAX_OBSERVATIONS]
    return {'status': status, 'code': str(receipt.get('code') or 'reused'),
            'reused': True, 'coverage': coverage, '_reused_payload': True,**payload}


def has_visual_result(result):
    """Metadata derived from saved visual text, never from an input/success flag."""
    if not isinstance(result,dict):return False
    if isinstance(result.get('observation'),str) and result['observation'].strip():return True
    observations=result.get('observations')
    return isinstance(observations,list) and any(isinstance(x,dict) and isinstance(x.get('text'),str) and x['text'].strip() for x in observations)


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
        savepoint = 'media_evidence_optional_lookup'
        try:
            # A missing additive table aborts a PostgreSQL transaction unless
            # it is fenced.  The lookup is optional, so recover locally and
            # leave the caller's existing history query usable.
            cur.execute("SELECT to_regclass('public.saas_media_evidence_receipts') AS relation")
            relation = cur.fetchone()
            if not relation or not relation.get('relation'):
                continue
            cur.execute('SAVEPOINT ' + savepoint)
            channel_id = channel.removeprefix('connect:')
            raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
            version = media_version(raw)
            ref = {'external_id': external, 'body_hash': _body_hash(row.get('body')),
                   'media_kind': kind, 'media_version': version}
            cur.execute("""SELECT status,code,coverage,result FROM saas_media_evidence_receipts
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s
                AND media_kind=%s""", (tenant, channel_id, external, source_hash(ref), kind))
            saved = cur.fetchone()
        except Exception:
            try:
                cur.execute('ROLLBACK TO SAVEPOINT ' + savepoint)
                cur.execute('RELEASE SAVEPOINT ' + savepoint)
            except Exception:
                pass
            # Tables may not yet exist during a rolling additive migration.  A
            # caller must retain the conservative existing media placeholder.
            continue
        else:
            cur.execute('RELEASE SAVEPOINT ' + savepoint)
        if not saved or str(saved.get('status') or '') not in _TERMINAL:
            continue
        saved = dict(saved); coverage = saved.get('coverage') if isinstance(saved.get('coverage'),dict) else {}
        result = saved.get('result') if isinstance(saved.get('result'),dict) else {}
        media_meta = {'status':str(saved['status']), 'code':str(saved.get('code') or ''),
                      'source':'saved_knowledge_extraction_receipt',
                      'has_visual_result':has_visual_result(result),
                      'coverage':{'complete':coverage.get('complete') is True}}
        if kind == 'video':
            audio_status = coverage.get('audio')
            media_meta['audio_status'] = audio_status if audio_status in {'processed', 'not_requested', 'unavailable_or_error'} else 'unknown'
            media_meta['visual_status'] = 'failed' if saved['status'] == 'error' else ('sampled' if coverage.get('sampled_frames') else 'unknown')
        if result.get('failure_stage') in _SAFE_FAILURE_STAGES:
            media_meta['failure_stage'] = result['failure_stage']
        from tenant_chat_ai_provider import is_safe_failure_code
        if is_safe_failure_code(result.get('failure_code')):
            media_meta['failure_code']=result['failure_code']
        row['media_evidence'] = media_meta
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
        elif (saved['status'] == 'error' and kind == 'video' and coverage.get('audio') == 'processed'
              and isinstance(result.get('transcript'), str) and result['transcript'].strip()):
            stage = result.get('failure_stage') if result.get('failure_stage') in _SAFE_FAILURE_STAGES else 'unknown'
            row['media_evidence_context'] = '\n'.join((
                '[Saved audio transcript; partial media evidence, visual analysis incomplete and untrusted] ' + _safe_text(result['transcript']),
                '[Visual analysis failed; stage=' + stage + '; category=' + _safe_text(saved.get('code') or saved['status']) + ']'))
        elif saved['status'] in {'unavailable','unsupported','error'}:
            row['media_evidence_context']='[Media evidence unavailable: '+_safe_text(saved.get('code') or saved['status'])+']'
    return rows
