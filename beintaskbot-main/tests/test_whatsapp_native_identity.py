"""Identity boundary tests; no network, credentials or production DB."""
import uuid
from unittest.mock import Mock
import pytest
from tenant_whatsapp_native_identity import existing_message_id, NativeIdentityConflict


def lookup(rows, **changes):
    cur = Mock()
    cur.fetchall.return_value = rows
    args = dict(tenant=str(uuid.UUID(int=1)), source=str(uuid.UUID(int=2)),
                lead_id=-42, provider_id='PROVIDER_123', outgoing=False)
    args.update(changes)
    return existing_message_id(cur, **args), cur


def test_keeps_legacy_identity_and_scopes_every_lookup():
    result, cur = lookup([{'external_id':'connect:LEGACY_456'}])
    assert result == 'LEGACY_456'
    sql, params = cur.execute.call_args.args
    assert params == (str(uuid.UUID(int=1)), 'connect:'+str(uuid.UUID(int=2)),
                      -42, 'incoming', 'PROVIDER_123')
    for clause in ('tenant_id=%s::uuid', 'channel=%s', 'kommo_lead_id=%s',
                   'direction=%s', "raw->>'externalMessageId'=%s"):
        assert clause in sql
    assert lookup([], outgoing=True)[1].execute.call_args.args[1][3] == 'outgoing'


def test_missing_identity_does_not_invent_mapping():
    assert lookup([])[0] is None


@pytest.mark.parametrize('rows', [
    [{'external_id':'connect:ONE_123'}, {'external_id':'connect:TWO_123'}],
    [{'external_id':None}], [{'external_id':'other:LEGACY_456'}]])
def test_conflicting_or_unusable_legacy_rows_fail_closed(rows):
    with pytest.raises(NativeIdentityConflict):
        lookup(rows)


@pytest.mark.parametrize('changes', [dict(tenant='bad'), dict(source='bad'),
    dict(lead_id=True), dict(outgoing=1), dict(provider_id="bad' OR 1=1")])
def test_invalid_scope_is_rejected(changes):
    with pytest.raises(NativeIdentityConflict):
        lookup([], **changes)
