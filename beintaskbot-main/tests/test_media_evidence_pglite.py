"""Durable media-evidence receipt fences on the isolated PGlite harness.

These tests deliberately use the same local WASM PostgreSQL process as the
existing support inbox suite.  They never start a provider, fetch media, or
connect to a production database.
"""
import hashlib
import json

import pytest

pytest_plugins = ('test_tenant_support_inbox',)

from test_tenant_support_inbox import A, B, CA, CB, db
import tenant_media_evidence as evidence


def _version(raw):
    return hashlib.sha256(json.dumps(
        {key: raw.get(key) for key in (
            'mediaId', 'media_id', 'file_uuid', 'fileUuid', 'fileLength',
            'file_size', 'size_bytes', 'mediaKeyTimestamp', 'timestamp',
        )},
        sort_keys=True, ensure_ascii=False, separators=(',', ':'),
    ).encode()).hexdigest()


def _ref(message_id, channel, external, body, kind='video', direction='incoming', raw=None):
    raw = raw or {'mediaId': 'media-1', 'fileLength': 100}
    return {
        'id': message_id,
        'channel_id': channel,
        'external_id': external,
        'body_hash': evidence._body_hash(body),
        'media_kind': kind,
        'media_version': _version(raw),
        'role': direction,
    }


def _row(ref, body, raw=None):
    return {
        'message_type': ref['media_kind'],
        'channel': 'connect:' + ref['channel_id'],
        'external_id': ref['external_id'],
        'body': body,
        'raw': raw or {'mediaId': 'media-1', 'fileLength': 100},
    }


@pytest.fixture
def receipt_db(db, monkeypatch):
    evidence.create_schema(db)
    db.execute('DELETE FROM saas_media_evidence_receipts')
    db.commit()
    monkeypatch.setattr(evidence, '_connect', lambda: db)
    return db


def _save(db, tenant, ref, **receipt):
    evidence.persist(db.cursor(), tenant, [ref], [{
        'id': ref['id'], 'status': 'partial', 'code': 'vision_observation',
        'kind': ref['media_kind'], 'observations': [
            {'origin': {'second': 0}, 'text': 'first frame'},
            {'origin': {'second': 60}, 'text': 'second frame'},
        ], 'visuals': [{'second': 0}, {'second': 60}], **receipt,
    }])
    db.commit()


def test_reusable_receipt_revalidates_exact_tenant_channel_and_media_version(receipt_db):
    ref = _ref('m-in', CA, 'media-in', 'same body')
    _save(receipt_db, A, ref)

    assert evidence.reusable({'tenant_id': A}, ref)['status'] == 'partial'
    assert evidence.reusable({'tenant_id': B}, ref) is None
    assert evidence.reusable({'tenant_id': A}, {**ref, 'channel_id': CB}) is None
    # Replacing media while retaining a message body must invalidate reuse.
    assert evidence.reusable({'tenant_id': A}, {
        **ref, 'media_version': _version({'mediaId': 'replacement', 'fileLength': 100}),
    }) is None


def test_multigeneration_reuse_keeps_original_frames_and_both_directions(receipt_db):
    incoming = _ref('m-in', CA, 'media-in', 'question', direction='incoming')
    outgoing = _ref('m-out', CA, 'media-out', 'staff demonstration', direction='operator')
    _save(receipt_db, A, incoming)
    _save(receipt_db, A, outgoing)

    first = evidence.reusable({'tenant_id': A}, incoming)
    projected = evidence.apply_reusable({'text': ''}, first)
    # A consumer projection is deliberately not allowed to replace durable evidence.
    evidence.persist(receipt_db.cursor(), A, [incoming], [{
        'id': incoming['id'], **projected,
    }])
    receipt_db.commit()
    repeated = evidence.reusable({'tenant_id': A}, incoming)
    assert [item['origin']['second'] for item in repeated['result']['observations']] == [0, 60]
    assert evidence.reusable({'tenant_id': A}, outgoing)['provenance']['untrusted'] is True


def test_annotation_keeps_frame_origins_and_never_crosses_channel_or_tenant(receipt_db):
    ref = _ref('m-in', CA, 'media-in', 'question')
    _save(receipt_db, A, ref)
    row = _row(ref, 'question')
    evidence.annotate_rows(receipt_db.cursor(), A, [row])
    assert 'frame 0' in row['media_evidence_context']
    assert 'frame 60' in row['media_evidence_context']
    assert row['media_evidence']['coverage']['complete'] is False

    foreign_tenant = _row(ref, 'question')
    evidence.annotate_rows(receipt_db.cursor(), B, [foreign_tenant])
    assert 'media_evidence' not in foreign_tenant
    foreign_channel = _row({**ref, 'channel_id': CB}, 'question')
    evidence.annotate_rows(receipt_db.cursor(), A, [foreign_channel])
    assert 'media_evidence' not in foreign_channel


def test_policy_denial_is_not_persisted_and_later_authorized_analysis_can_save(receipt_db):
    ref = _ref('m-in', CA, 'consent-change', 'question')
    evidence.persist(receipt_db.cursor(), A, [ref], [{
        'id': ref['id'], 'status': 'unavailable',
        'code': 'vision_not_approved', 'kind': 'video',
    }])
    receipt_db.commit()
    assert evidence.reusable({'tenant_id': A}, ref) is None

    _save(receipt_db, A, ref)
    assert evidence.reusable({'tenant_id': A}, ref)['status'] == 'partial'


def test_missing_optional_receipt_table_does_not_abort_history_transaction(receipt_db):
    ref = _ref('m-in', CA, 'rolling-migration', 'question')
    receipt_db.execute('DROP TABLE saas_media_evidence_receipts')
    receipt_db.commit()
    row = _row(ref, 'question')
    evidence.annotate_rows(receipt_db.cursor(), A, [row])
    # The optional lookup has its own savepoint, so callers can continue using
    # the same transaction after a rolling node has not created the table yet.
    assert receipt_db.execute('SELECT 1 AS ok')[0]['ok'] == 1
    assert 'media_evidence' not in row


@pytest.mark.parametrize('kind',['audio','voice'])
def test_old_download_failure_can_be_rechecked_without_deleting_receipt(receipt_db,kind):
    ref=_ref('audio-row',CA,'connect:download-failed','',kind=kind)
    evidence.persist(receipt_db.cursor(),A,[ref],[{'id':ref['id'],'kind':kind,
        'status':'unsupported','code':'media_unavailable','failure_stage':'media_download',
        'failure_code':'media_processing_failed'}])
    receipt_db.commit()
    assert evidence.reusable({'tenant_id':A},ref) is None
    assert receipt_db.execute('SELECT count(*) AS n FROM saas_media_evidence_receipts')[0]['n']==1


@pytest.mark.parametrize('change',[
    {'status':'processed','result':{'transcript':'paid transcript'}},
    {'status':'partial','result':{'transcript':'known part'}},
    {'code':'media_result_unknown'},
    {'result':{'failure_stage':'media_validation','failure_code':'media_processing_failed'}},
    {'result':{'failure_stage':'audio_transcription','failure_code':'media_processing_failed'}},
    {'result':{'failure_stage':'media_download'}},
    {'result':{'failure_stage':'media_download','failure_code':'media_processing_failed','transcript':'keep'}},
    {'provenance':{}},
])
def test_download_recovery_never_reclaims_paid_unknown_or_ambiguous(change):
    ref=_ref('audio-row',CA,'connect:failed','',kind='voice')
    receipt={'status':'unsupported','code':'media_unavailable',
        'result':{'failure_stage':'media_download','failure_code':'media_processing_failed'},
        'provenance':{'processor':'knowledge_extraction','source_version':evidence.source_hash(ref)},**change}
    assert not evidence.retryable_audio_download_failure(receipt,ref)
