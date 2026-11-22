"""Due timeline behavior on disposable PostgreSQL/WASM, no actual sends."""
from contextlib import contextmanager
import uuid

from psycopg.errors import QueryCanceled
from psycopg.sql import Composable
import pytest

import tenant_dialog_presence as presence
import tenant_operator_dialogs as dialogs
import tenant_shared_dialog_reminders as reminders
from tenant_linear_policy import TenantLinearError
from test_dialog_lifecycle_pglite import command, episodes
from test_tenant_support_inbox import A, B, CA, CB, database, db


class TimelineConnection:
    """Bridge psycopg's nested transaction interface to the single WASM session."""
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.__enter__()
        return self

    def __exit__(self, *args):
        return self.db.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.db, name)

    @contextmanager
    def transaction(self):
        self.db.execute('SAVEPOINT timeline_test')
        try:
            yield
        except Exception:
            self.db.execute('ROLLBACK TO SAVEPOINT timeline_test')
            raise
        finally:
            self.db.execute('RELEASE SAVEPOINT timeline_test')


@pytest.fixture
def shared_timeline(episodes, monkeypatch):
    monkeypatch.setenv(presence.STATUS_ENABLED_ENV, 'true')
    monkeypatch.setenv(presence.ROUTING_SOURCES_ENV, CA)
    monkeypatch.setenv(reminders.ENABLE, '1')
    monkeypatch.setattr(dialogs, '_connect', lambda: TimelineConnection(episodes))
    with episodes.cursor() as cur:
        execute = cur.execute
        cur.execute = lambda query, params=None: execute(
            query.as_string() if isinstance(query, Composable) else query, params)
        presence.provision_status_schema(cur)
        reminders.provision_schema(cur)
        cur.execute('TRUNCATE saas_shared_dialog_reminders')
    episodes.commit()
    accepted = command(actor=33)['lifecycle']
    command('close', accepted['revision'], actor=33)
    return episodes


def add(db, note='Synthetic due note', *, offset=-10, opened=False,
        tenant=A, channel=CA, conversation='client', lead=41):
    ident = str(uuid.uuid4())
    stamp = db.execute("""INSERT INTO saas_shared_dialog_reminders
        (tenant_id,id,channel_id,conversation_id,lead_id,created_by,note,due_at,opened_at,opened_by)
        VALUES (%s::uuid,%s::uuid,%s::uuid,%s,%s,33,%s,
          now()+(%s*interval '1 day'),CASE WHEN %s THEN now() END,CASE WHEN %s THEN 33 END)
        RETURNING due_at""",
        (tenant, ident, channel, conversation, lead, note, offset, opened, opened))[0]['due_at'].isoformat()
    return {'id': ident, 'note': note, 'at': stamp}


def read(**options):
    return dialogs.history({'tenant_id': A, 'telegram_id': 33}, 'connect', 41, CA, 'client', **options)


def test_due_notes_on_closed_exact_history_are_separate_from_badges_and_messages(shared_timeline, monkeypatch):
    db = shared_timeline
    previously_opened = add(db, 'Synthetic previously opened note', opened=True, offset=-14)
    add(db, ' \n\t', opened=True)
    future = add(db, 'Synthetic upcoming note', opened=True, offset=2)
    pending = add(db)
    add(db, 'Wrong tenant', tenant=B)
    add(db, 'Wrong source', channel=CB)
    add(db, 'Wrong conversation', conversation='synthetic other')
    add(db, 'Wrong lead', lead=43)
    db.commit()

    ordinary = read()
    assert 'shared_reminder_events' not in ordinary
    assert ordinary['dialog']['lifecycle']['state'] == 'closed'
    revision = ordinary['dialog']['lifecycle']['revision']
    result = read(include_shared_reminder_events=True, include_operator_events=True,
                  operator_events_revision=revision)
    assert 'operator_events' not in result
    assert result['shared_reminder_events'] == [previously_opened, pending]
    for key in ('messages', 'next_before', 'read_through_message_id', 'dialog'):
        assert result[key] == ordinary[key]
    assert db.execute("SELECT opened_at FROM saas_shared_dialog_reminders WHERE id=%s::uuid",
                      (pending['id'],)) == [{'opened_at': None}]
    db.execute("UPDATE saas_shared_dialog_reminders SET due_at=now() WHERE id=%s::uuid",
               (future['id'],))
    db.commit()
    assert [e['id'] for e in read(include_shared_reminder_events=True,
                                  operator_events_revision=revision)['shared_reminder_events']] == [
                                      previously_opened['id'], pending['id'], future['id']]
    assert 'shared_reminder_events' not in read(
        include_shared_reminder_events=True, anchor=ordinary['messages'][-1]['id'])
    assert 'shared_reminder_events' not in read(
        include_shared_reminder_events=True, support_case_only=True)
    assert 'shared_reminder_events' not in dialogs.history(
        {'tenant_id': A, 'telegram_id': 33}, 'connect', 41, include_shared_reminder_events=True)
    with pytest.raises(TenantLinearError):
        dialogs.history({'tenant_id': B, 'telegram_id': 22}, 'connect', 41, CA, 'client',
                        include_shared_reminder_events=True)
    with pytest.raises(TenantLinearError):
        dialogs.history({'tenant_id': A, 'telegram_id': 99999}, 'connect', 41, CA, 'client',
                        include_shared_reminder_events=True)
    monkeypatch.setenv(reminders.ENABLE, '0')
    assert 'shared_reminder_events' not in read(include_shared_reminder_events=True)


def test_reminder_events_use_message_cursor_and_bounded_payload(shared_timeline):
    db = shared_timeline
    for i in range(60):
        row_id = str(uuid.uuid4())
        db.execute("""INSERT INTO saas_crm_messages
            (id,tenant_id,kommo_lead_id,external_id,channel,direction,body,created_at,happened_at)
            VALUES (%s::uuid,%s::uuid,41,%s,%s,'incoming','Synthetic',
                    now()-(%s*interval '1 day'),now()-(%s*interval '1 day'))""",
            (row_id, A, row_id, 'connect:'+CA, i+1, i+1))
    prior = add(db, 'Synthetic old note', offset=-59)
    dense = [add(db, f'Synthetic same-time note {i}', opened=True, offset=-2)
             for i in range(reminders.MAX_VISIBLE+1)]
    db.commit()

    first = read(include_shared_reminder_events=True)
    assert len(first['messages']) == 50
    assert first['next_before']
    expected = sorted(dense, key=lambda event: event['id'])[1:]
    assert first['shared_reminder_events'] == expected
    assert first['shared_reminder_events_omitted_at'] == expected[0]['at']
    older = read(include_shared_reminder_events=True, before=first['next_before'])
    assert older['shared_reminder_events'] == [prior]
    assert 'shared_reminder_events_omitted_at' not in older
    assert older['next_before'] is None


def test_optional_due_read_does_not_break_history_after_timeout(shared_timeline, monkeypatch):
    import tenant_dialog_operator_timeline as timeline
    db = shared_timeline
    event = add(db)
    db.commit()
    ordinary = read()
    original = timeline.history_window

    def timed_out(cur, *args, **kwargs):
        # Put the disposable transaction into the PostgreSQL error state
        # before simulating the actual timeout caught by the optional reader.
        from psycopg.errors import DivisionByZero
        try:
            cur.execute('SELECT 1/0')
        except DivisionByZero:
            raise QueryCanceled('synthetic reminder timeout') from None

    monkeypatch.setattr(timeline, 'history_window', timed_out)
    after_timeout = read(include_shared_reminder_events=True)
    assert 'shared_reminder_events' not in after_timeout
    for key in ('messages', 'next_before', 'read_through_message_id'):
        assert after_timeout[key] == ordinary[key]
    monkeypatch.setattr(timeline, 'history_window', original)
    assert read(include_shared_reminder_events=True)['shared_reminder_events'] == [event]
