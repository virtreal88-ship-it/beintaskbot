"""Private notes share dialogue access, but never become customer messages."""
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_pglite import episodes, payload
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db
from tenant_dialog_internal_comments import provision_schema, save
from tenant_dialog_lifecycle import mutate
from tenant_linear_policy import TenantLinearError


def test_comment_follows_dialogue_through_transfer_close_and_start(episodes, monkeypatch):
    import tenant_operator_dialogs as operator

    monkeypatch.setattr(operator, '_connect', lambda: psycopg.connect(episodes.dsn, row_factory=dict_row))
    with episodes.cursor() as cur:
        provision_schema(cur)
    episodes.commit()
    next_operator = {'tenant_id': A, 'telegram_id': 33}

    def read(profile, **opts):
        return operator.history(profile, 'connect', 41, CA, 'client', **opts)

    note = {'provider': 'connect', 'channel_id': CA, 'conversation_id': 'client',
            'lead_id': 41, 'request_id': str(uuid4()), 'note': 'Staff only <script>synthetic</script>'}
    with pytest.raises(TenantLinearError):
        save(PROFILE, note)
    mutate(PROFILE, payload())
    before = read(PROFILE)
    receipt = save(PROFILE, note)
    assert save(PROFILE, note) == receipt
    assert receipt['comment']['employee_name'] == 'Owner'
    assert receipt['comment']['note'] == note['note']
    with pytest.raises(TenantLinearError) as conflict:
        save(PROFILE, {**note, 'note': 'Different text'})
    assert conflict.value.status == 409
    with pytest.raises(TenantLinearError):
        save(PROFILE, {**note, 'channel_id': CB})
    with pytest.raises(TenantLinearError):
        save({'tenant_id': B, 'telegram_id': PROFILE['telegram_id']}, note)

    staff = read(PROFILE, include_internal_comments=True)
    assert staff['internal_comments'] == [receipt['comment']]
    assert staff['dialog']['can_add_internal_comment'] is True
    assert staff['messages'] == before['messages']
    assert staff['read_through_message_id'] == before['read_through_message_id']
    assert 'internal_comments' not in read(PROFILE)
    assert 'internal_comments' not in read(PROFILE, include_internal_comments=True, support_case_only=True)
    count = episodes.execute('SELECT COUNT(*) AS n FROM saas_dialog_internal_comments WHERE tenant_id=%s::uuid', (A,))
    assert count[0]['n'] == 1
    customers = episodes.execute('SELECT COUNT(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid AND body=%s', (A, note['note']))
    assert customers[0]['n'] == 0
    episodes.commit()

    mutate(PROFILE, payload('transfer', 1, target_employee_id=33))
    received = read(next_operator, include_internal_comments=True)
    assert received['internal_comments'] == [receipt['comment']]
    observer = read(PROFILE, include_internal_comments=True, owner_overview=True)
    assert observer['internal_comments'] == [receipt['comment']]
    assert observer['dialog']['can_add_internal_comment'] is False
    with pytest.raises(TenantLinearError):
        save(PROFILE, {**note, 'request_id': str(uuid4())})

    mutate(next_operator, payload('close', 2))
    archive = read(PROFILE, include_internal_comments=True)
    assert archive['internal_comments'] == [receipt['comment']]
    assert archive['dialog']['can_add_internal_comment'] is False
    mutate(next_operator, payload('start', 3))
    assert read(next_operator, include_internal_comments=True)['internal_comments'] == [receipt['comment']]
