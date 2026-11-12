"""Stale active leases are recoverable only after provider session expiry."""
import uuid
from unittest.mock import patch

from tenant_operator_voice_lease import (
    ACTIVE_RECOVERY_GRACE_SECONDS,
    ACTIVE_RECOVERY_AFTER_SECONDS,
    REALTIME_SESSION_MAX_SECONDS,
    VoiceSessionLease,
)
from tenant_operator_voice_session import PROVIDER_OPEN_DEADLINE


class Authority:
    session_scope = (
        '00000000-0000-0000-0000-000000000001',
        77,
        '00000000-0000-0000-0000-000000000002',
        'dialog-1',
    )

    def recheck(self, _cursor):
        return True


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, params=None):
        self.statements.append((statement, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


def _lease():
    return VoiceSessionLease(Authority(), str(uuid.uuid4()))


def test_expired_active_lease_is_not_reclaimed_before_provider_lifetime():
    cursor = Cursor([{
        'session_id': str(uuid.uuid4()), 'actor_id': 77, 'owner_id': str(uuid.uuid4()),
        'fence': 4, 'state': 'active', 'live': False,
        'provider_session_expired': False,
    }])
    conn = Connection(cursor)

    with patch('tenant_operator_voice_lease._connect', return_value=conn):
        assert _lease()._transaction('claim') is None

    select, params = cursor.statements[-1]
    assert "provider_opened_at <= now() - (%s * interval '1 second')" in select
    assert params == (ACTIVE_RECOVERY_AFTER_SECONDS, *Authority.session_scope[:2])
    assert ACTIVE_RECOVERY_AFTER_SECONDS > REALTIME_SESSION_MAX_SECONDS
    assert ACTIVE_RECOVERY_GRACE_SECONDS > PROVIDER_OPEN_DEADLINE
    assert len(cursor.statements) == 2  # lock + read; no claim/update


def test_active_lease_reclaimed_only_after_provider_lifetime_with_new_fence():
    cursor = Cursor([
        {
            'session_id': str(uuid.uuid4()), 'actor_id': 77, 'owner_id': str(uuid.uuid4()),
            'fence': 4, 'state': 'active', 'live': False,
            'provider_session_expired': True,
        },
        {'fence': 5},
        {'fence': 5},
    ])
    conn = Connection(cursor)
    lease = _lease()

    with patch('tenant_operator_voice_lease._connect', return_value=conn):
        assert lease._transaction('claim') == 5

    update, params = cursor.statements[-2]
    assert "state='starting'" in update
    assert params[4] == 5  # new fencing token
    assert params[-1] == 4  # compare-and-swap against the old token
    assert conn.commits == 1


def test_old_starting_lease_is_never_reclaimed_without_confirmed_provider_open(caplog):
    cursor = Cursor([{
        'session_id': str(uuid.uuid4()), 'actor_id': 77, 'owner_id': str(uuid.uuid4()),
        'fence': 4, 'state': 'starting', 'live': False,
        'provider_session_expired': False,
    }])
    conn = Connection(cursor)

    with patch('tenant_operator_voice_lease._connect', return_value=conn), caplog.at_level('INFO'):
        assert _lease()._transaction('claim') is None

    select, _params = cursor.statements[-1]
    assert "provider_opened_at IS NOT NULL" in select
    assert len(cursor.statements) == 2
    assert 'state=starting lease_live=False provider_lifetime_elapsed=False' in caplog.text
    assert cursor.rows == []
    assert 'dialog-1' not in caplog.text


def test_constructor_accepts_only_known_provider_kind():
    for kind in ('openai_realtime', 'openai_elevenlabs_tts', 'unknown'):
        assert VoiceSessionLease(Authority(), str(uuid.uuid4()), provider_kind=kind).provider_kind == kind
    import pytest
    with pytest.raises(ValueError):
        VoiceSessionLease(Authority(), str(uuid.uuid4()), provider_kind='other')


def test_claim_uses_provider_kind_and_transactional_exact_fence():
    cursor = Cursor([None, {'fence': 1}, {'fence': 1}])
    conn = Connection(cursor)
    lease = VoiceSessionLease(Authority(), str(uuid.uuid4()), provider_kind='openai_elevenlabs_tts')
    with patch('tenant_operator_voice_lease._connect', return_value=conn):
        assert lease._transaction('claim') == 1
    statement, params = cursor.statements[-1]
    assert "provider_kind='unknown'" in statement
    assert "provider_opened_at IS NULL" in statement
    assert params[0] == 'openai_elevenlabs_tts'
    assert params[-3:] == (lease.session_id, lease.owner_id, 1)
    assert conn.commits == 1


def test_release_confirms_only_the_same_already_closed_fenced_row():
    lease = _lease()
    lease.fence = 7
    cursor = Cursor([None, {'fence': 7}])
    conn = Connection(cursor)
    with patch('tenant_operator_voice_lease._connect', return_value=conn):
        assert lease._transaction('release', 7) is True
    update, params = cursor.statements[-2]
    verify, verify_params = cursor.statements[-1]
    assert "state IN ('starting','active')" in update
    assert "state='closed' AND closed_at IS NOT NULL" in verify
    assert params == verify_params
    assert params[-1] == 7
    assert conn.commits == 1


def test_release_retries_a_transient_database_failure_with_same_fence():
    import asyncio
    from tenant_operator_voice_lease import TenantLinearError

    lease = _lease()
    lease.fence = 8
    with patch.object(lease, '_transaction', side_effect=[
        TenantLinearError('unavailable', 503), True,
    ]) as transaction:
        assert asyncio.run(lease.release(lease.scope, 8)) is True
    assert transaction.call_count == 2
    assert transaction.call_args_list[0].args == ('release', 8)
    assert transaction.call_args_list[1].args == ('release', 8)
    assert lease.fence is None
