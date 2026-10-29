"""Regression: an unknown receipt only applies to its exact source ID."""
import tenant_case_media_extension as extension

PHOTO = '00000000-0000-0000-0000-000000000011'
OTHER = '00000000-0000-0000-0000-000000000012'


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ''

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return {'relation': 'present'} if 'to_regclass' in self.sql else None


def test_prior_unknown_is_scoped_to_message_not_extraction(monkeypatch):
    monkeypatch.setattr(extension, '_historical_attempt_reconciliation', lambda *a, **kw:
                        {'state': 'unknown', 'reason': 'historical_source_version_unverified'})
    row = {'id': '00000000-0000-0000-0000-000000000001', 'status': 'completed',
           'source_refs': [{'id': PHOTO, 'media_kind': 'image'}, {'id': OTHER, 'media_kind': 'image'}],
           'media_results': [
               {'id': PHOTO, 'kind': 'image', 'status': 'unavailable', 'code': 'external_media_permission_required'},
               {'id': OTHER, 'kind': 'image', 'status': 'pending_processing', 'code': 'processing_limit'}]}
    assert extension._verified_byte_history(Cursor([row]), 'tenant', PHOTO, 'hash', allow_incomplete=True)['state'] == 'incomplete_history_eligible'
    assert extension._verified_byte_history(Cursor([row]), 'tenant', OTHER, 'hash', allow_incomplete=True)['reason'] == 'prior_media_result_unknown'


def test_other_extraction_for_same_source_blocks(monkeypatch):
    monkeypatch.setattr(extension, '_historical_attempt_reconciliation', lambda *a, **kw:
                        {'state': 'unknown', 'reason': 'historical_source_version_unverified'})
    ref = {'id': PHOTO, 'media_kind': 'image'}
    rows = [
        {'id': '00000000-0000-0000-0000-000000000001', 'status': 'completed', 'source_refs': [ref],
         'media_results': [{'id': PHOTO, 'kind': 'image', 'status': 'unavailable', 'code': 'external_media_permission_required'}]},
        {'id': '00000000-0000-0000-0000-000000000002', 'status': 'completed', 'source_refs': [ref],
         'media_results': [{'id': PHOTO, 'kind': 'image', 'status': 'unknown', 'code': 'media_result_unknown'}]},
    ]
    assert extension._verified_byte_history(Cursor(rows), 'tenant', PHOTO, 'hash', allow_incomplete=True)['reason'] == 'prior_media_result_unknown'
