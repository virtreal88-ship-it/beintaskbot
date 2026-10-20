"""Automatic-send receipt and reply-and-stop boundary (no provider calls)."""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import tenant_ai_agent_send as sender
import tenant_ai_agent_queue as queue


TENANT = '00000000-0000-0000-0000-000000000101'
AGENT = '00000000-0000-0000-0000-000000000301'
RUN = '00000000-0000-0000-0000-000000000401'
JOB = {'tenant_id': TENANT, 'agent_id': AGENT, 'provider': 'kommo', 'chat_id': 'chat-1',
       'talk_id': '12', 'lead_id': 41, 'event_fingerprint': 'event-1', 'revision': 2,
       'lease_token': 'lease-1'}
ROUTE = {'talk_id': '12', 'chat_id': 'chat-1', 'message_id': 'source-1',
         'source_happened_at': '2026-10-20T10:00:00+00:00', 'conversation_revision': 7,
         'handoff_after_reply': True}
RESERVATION = {'run_id': RUN, 'route': ROUTE, 'conversation_revision': 7,
               'settings_version': 'settings-v1', 'agent_version': 3, 'config_version': 'config-v1'}


class Cursor:
    def __init__(self, receipt=None):
        self.receipt, self.row, self.sql = receipt, None, []

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params=None):
        self.sql.append(sql)
        if 'SELECT j.state,j.lease_token' in sql:
            self.row = {'state': 'processing', 'lease_token': 'lease-1', 'revision': 2,
                'enabled': True, 'operating_mode': 'active', 'auto_send_enabled': True,
                'emergency_stop': False, 'settings_version': 'settings-v1',
                'agent_enabled': True, 'agent_version': 3, 'tenant_status': 'active',
                'config_version': 'config-v1'}
        elif 'SELECT state,revision FROM saas_ai_conversation_state' in sql or 'SELECT state,revision FROM saas_ai_conversation_state' in sql:
            self.row = {'state': 'active', 'revision': 7}
        elif 'SELECT state,message_id FROM saas_chat_send_receipts' in sql:
            self.row = self.receipt
        elif 'SELECT state FROM saas_chat_send_receipts' in sql:
            self.row = {'state': 'sending'}
        else:
            self.row = None
    def fetchone(self):
        value, self.row = self.row, None
        return value


class Connection:
    def __init__(self, cursor): self.c, self.commits = cursor, 0
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return self.c
    def commit(self): self.commits += 1


def harness(receipt=None):
    connections = [Connection(Cursor(receipt)), Connection(Cursor())]
    return connections, iter(connections)


def messages():
    return [{'id': 'source-1', 'chat_id': 'chat-1', 'created_at': 1792490400, 'type': 'incoming'}]


def test_reply_and_stop_commits_acceptance_and_handoff_after_one_provider_acceptance():
    connections, dbs = harness()
    post = AsyncMock(return_value={'id': 'out-1'})
    with patch.object(sender, '_connect', side_effect=lambda: next(dbs)), \
         patch.object(sender, 'ensure_schema'), patch.object(sender, 'ensure_receipt_schema'), \
         patch('tenant_ai_agent_runtime_flags.enabled', return_value=True), \
         patch('tenant_chat_reader.read_messages', new=AsyncMock(return_value=messages())):
        result = asyncio.run(sender.send_agent(JOB, RESERVATION, 'Approved answer', post, Mock(), Mock()))
    assert result['state'] == 'accepted' and result['message_id'] == 'out-1'
    post.assert_awaited_once_with(TENANT, 'POST', 'talks/12/send_message',
                                  json_body={'text': 'Approved answer'})
    statements = '\n'.join(sql for connection in connections for sql in connection.c.sql)
    assert "state='accepted'" in statements
    assert "state='human',reason='trigger_reply_sent_then_handoff'" in statements
    assert "author_name,body,message_type,raw" in statements


def test_unknown_provider_outcome_is_not_marked_accepted_or_retried():
    connections, dbs = harness()
    post = AsyncMock(side_effect=RuntimeError('ambiguous network result'))
    with patch.object(sender, '_connect', side_effect=lambda: next(dbs)), \
         patch.object(sender, 'ensure_schema'), patch.object(sender, 'ensure_receipt_schema'), \
         patch('tenant_ai_agent_runtime_flags.enabled', return_value=True), \
         patch('tenant_chat_reader.read_messages', new=AsyncMock(return_value=messages())):
        try:
            asyncio.run(sender.send_agent(JOB, RESERVATION, 'Approved answer', post, Mock(), Mock()))
        except RuntimeError:
            pass
        else:
            raise AssertionError('unknown delivery must propagate')
    post.assert_awaited_once()
    statements = '\n'.join(sql for connection in connections for sql in connection.c.sql)
    assert "state='unknown'" in statements
    assert "state='accepted'" not in statements
    assert "state='human',reason='trigger_reply_sent_then_handoff'" not in statements


def test_existing_accepted_receipt_never_reposts():
    accepted = {'state': 'accepted', 'message_id': 'out-1'}
    connections, dbs = harness(receipt=accepted)
    post = AsyncMock()
    with patch.object(sender, '_connect', side_effect=lambda: next(dbs)), \
         patch.object(sender, 'ensure_schema'), patch.object(sender, 'ensure_receipt_schema'), \
         patch('tenant_ai_agent_runtime_flags.enabled', return_value=True), \
         patch('tenant_chat_reader.read_messages', new=AsyncMock(return_value=messages())):
        result = asyncio.run(sender.send_agent(JOB, RESERVATION, 'Approved answer', post, Mock(), Mock()))
    assert result['cached'] is True
    post.assert_not_awaited()


def test_global_auto_send_switch_is_rechecked_before_any_provider_call():
    post = AsyncMock()
    with patch('tenant_ai_agent_runtime_flags.enabled', return_value=False), \
         patch.object(sender, '_connect') as connect, \
         patch('tenant_chat_reader.read_messages', new=AsyncMock()) as read:
        try:
            asyncio.run(sender.send_agent(JOB, RESERVATION, 'Approved answer', post, Mock(), Mock()))
        except Exception as error:
            assert getattr(error, 'ai_agent_send_reason', '') == 'automatic_send_disabled'
        else:
            raise AssertionError('global switch off must block the write')
    connect.assert_not_called()
    read.assert_not_awaited()
    post.assert_not_awaited()


def test_closed_schedule_requeues_without_provider_or_database_access():
    route = {**ROUTE, 'schedule': {'timezone': 'Asia/Baku', 'days': [0], 'start': '09:00', 'end': '18:00'}}
    reservation = {**RESERVATION, 'route': route}
    post = AsyncMock()
    with patch('tenant_ai_agent_runtime_flags.enabled', return_value=True), \
         patch('tenant_ai_agent_schedule.next_open', return_value='2026-10-26T05:00:00+00:00'), \
         patch('tenant_ai_agent_run_store.defer_job') as defer, \
         patch.object(sender, '_connect') as connect, \
         patch('tenant_chat_reader.read_messages', new=AsyncMock()) as read:
        result = asyncio.run(sender.send_agent(JOB, reservation, 'Approved answer', post, Mock(), Mock()))
    assert result['state'] == 'deferred'
    defer.assert_called_once_with(JOB, '2026-10-26T05:00:00+00:00', reservation)
    connect.assert_not_called()
    read.assert_not_awaited()
    post.assert_not_awaited()


class EchoCursor:
    def __init__(self, receipt): self.receipt, self.row, self.rows, self.sql = receipt, None, [], []
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params=None):
        self.sql.append(sql)
        if 'SELECT i.tenant_id' in sql:
            self.rows = [{'tenant_id': TENANT}]
        elif 'SELECT request_id,state,route FROM saas_chat_send_receipts' in sql:
            self.row = self.receipt
        elif "SET state='human',reason='trigger_reply_sent_then_handoff'" in sql:
            self.row = {'revision': 8}
        else:
            self.row = None
    def fetchone(self): value, self.row = self.row, None; return value
    def fetchall(self): rows, self.rows = self.rows, []; return rows


class EchoConnection:
    def __init__(self, cursor): self.c, self.commits = cursor, 0
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return self.c
    def commit(self): self.commits += 1


def test_worker_echo_recovers_handoff_only_for_unresolved_send_receipt():
    route = {'event_fingerprint': 'event-1', 'revision': 2, 'conversation_revision': 7,
             'chat_id': 'chat-1', 'text_hash': 'unused', 'handoff_after_reply': True}
    cursor = EchoCursor({'request_id': RUN, 'state': 'unknown', 'route': route})
    connection = EchoConnection(cursor)
    row = {'type': 'outgoing', 'chat_id': 'chat-1', 'id': 'out-1', 'entity_id': 41,
           'contact_id': 51, 'talk_id': 12, 'origin': 'whatsapp', 'text': 'Approved answer'}
    with patch.object(queue, '_connect', return_value=connection), \
         patch.object(queue, 'ensure_schema'), patch.object(queue, 'allowed_event', return_value=True):
        changed = queue.observe_kommo_outgoing({'account': {'subdomain': 'demo'}}, [row])
    assert changed == 1
    assert any("SET state='human',reason='trigger_reply_sent_then_handoff'" in sql for sql in cursor.sql)
    assert any("SET state='accepted',message_id=%s" in sql for sql in cursor.sql)


def test_replayed_echo_after_resume_does_not_reapply_handoff():
    route = {'event_fingerprint': 'event-1', 'revision': 2, 'conversation_revision': 7,
             'chat_id': 'chat-1', 'text_hash': 'unused', 'handoff_after_reply': True}
    cursor = EchoCursor({'request_id': RUN, 'state': 'accepted', 'route': route})
    connection = EchoConnection(cursor)
    row = {'type': 'outgoing', 'chat_id': 'chat-1', 'id': 'out-1', 'entity_id': 41,
           'contact_id': 51, 'talk_id': 12, 'origin': 'whatsapp', 'text': 'Approved answer'}
    with patch.object(queue, '_connect', return_value=connection), \
         patch.object(queue, 'ensure_schema'), patch.object(queue, 'allowed_event', return_value=True):
        changed = queue.observe_kommo_outgoing({'account': {'subdomain': 'demo'}}, [row])
    assert changed == 1
    assert not any("SET state='human',reason='trigger_reply_sent_then_handoff'" in sql for sql in cursor.sql)
