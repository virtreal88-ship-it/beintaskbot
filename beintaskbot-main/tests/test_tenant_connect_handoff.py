import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if 'psycopg' not in sys.modules:
    psycopg = types.ModuleType('psycopg')
    psycopg.connect = MagicMock()
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg'] = psycopg
    sys.modules['psycopg.rows'] = rows

import tenant_ai_agents_store as agents_store
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError


TENANT = '00000000-0000-0000-0000-000000000101'
CHANNEL = '00000000-0000-0000-0000-000000000102'


class Cursor:
    def __init__(self, rows=None, ones=None):
        self.rows = list(rows or [])
        self.ones = list(ones or [])
        self.executed = []
        self.connection = None

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def execute(self, sql, params=None): self.executed.append((sql, params))
    def fetchall(self): return self.rows.pop(0) if self.rows else []
    def fetchone(self): return self.ones.pop(0) if self.ones else None


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        cursor.connection = self
        self.committed = False

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def cursor(self): return self.cursor_value
    def commit(self): self.committed = True
    def rollback(self): pass


class TenantConnectHandoffTests(unittest.TestCase):
    def test_phone_outbound_event_stops_only_its_tenant_connect_conversation(self):
        cursor = Cursor(
            rows=[[{'tenant_id': TENANT, 'secrets': b'encrypted'}]],
            ones=[{'active': True}, {'event_id': 'event-1'}, {'lead_id': 42}],
        )
        connection = Connection(cursor)
        event = {'id': 'event-1', 'type': 'message.outbound.observed', 'occurred_at': '2026-10-16T12:00:00Z',
                 'data': {'id': 'phone-1', 'channelId': CHANNEL, 'conversationId': 'client@s.whatsapp.net', 'type': 'text', 'text': 'I will help'}}
        cipher = MagicMock()
        cipher.decrypt.return_value = json.dumps({'webhook_secret': 'secret'}).encode()
        with patch.object(transport, '_connect', return_value=connection), \
             patch.object(transport, '_fernet', return_value=cipher), \
             patch.object(transport, '_verify_signature', return_value=True), \
             patch.object(transport, '_schema'), \
             patch('tenant_ai_agent_connect.mark_operator_reply') as stop:
            self.assertTrue(transport.ingest(b'{}', 'sha256=valid', event))
        stop.assert_called_once_with(TENANT, 42, CHANNEL, 'client@s.whatsapp.net')
        self.assertTrue(connection.committed)
        insert = next(params for sql, params in cursor.executed if 'saas_crm_messages' in sql)
        self.assertEqual(insert[1], TENANT)
        self.assertEqual(insert[4], 'connect:' + CHANNEL)

    def test_connect_resume_verifies_the_tenant_owned_channel(self):
        cursor = Cursor(rows=[[{'channel_id': CHANNEL}]])
        connection = Connection(cursor)
        profile = {'tenant_id': TENANT, 'telegram_id': 7}
        with patch.object(agents_store, '_connect', return_value=connection), \
             patch.object(agents_store, 'ensure_schema'), \
             patch.object(agents_store, '_authorize', return_value=(TENANT, 7)), \
             patch('tenant_connect_transport._schema'):
            result = agents_store.set_conversation_state(profile, 'client@s.whatsapp.net', 'active',
                                                         'manager_resume', 'connect', CHANNEL)
        self.assertEqual(result['provider'], 'connect')
        self.assertEqual(result['channel_id'], CHANNEL)
        ownership = next(params for sql, params in cursor.executed if 'saas_connect_conversations' in sql)
        self.assertEqual(ownership, (TENANT, 'client@s.whatsapp.net', CHANNEL))
        update = next(params for sql, params in cursor.executed if 'INSERT INTO saas_ai_conversation_state' in sql)
        self.assertEqual(update[:4], (TENANT, 'connect', 'client@s.whatsapp.net', 'active'))

    def test_connect_resume_rejects_an_ambiguous_or_foreign_conversation(self):
        cursor = Cursor(rows=[[]])
        profile = {'tenant_id': TENANT, 'telegram_id': 7}
        with patch.object(agents_store, '_connect', return_value=Connection(cursor)), \
             patch.object(agents_store, 'ensure_schema'), \
             patch.object(agents_store, '_authorize', return_value=(TENANT, 7)), \
             patch('tenant_connect_transport._schema'):
            with self.assertRaises(TenantLinearError):
                agents_store.set_conversation_state(profile, 'foreign@s.whatsapp.net', 'active',
                                                    provider='connect', channel_id=CHANNEL)

    def test_ai_send_is_rejected_after_human_takeover_without_calling_connect(self):
        """The final state/revision check must happen while the send is reserved."""
        row = {
            'connect_channel_id': CHANNEL,
            'conversation_id': 'client@s.whatsapp.net',
            'secrets': b'encrypted',
            'revision': 6,
            'state': 'human',
        }
        cursor = Cursor(ones=[row])
        connection = Connection(cursor)
        with patch.object(transport, '_connect', return_value=connection), \
             patch.object(transport, '_schema'), \
             patch.object(transport, 'send_ai_reply') as send:
            result = transport.send_ai_reply_if_active(
                TENANT, 42, CHANNEL, 'client@s.whatsapp.net', 'AI reply',
                '00000000-0000-0000-0000-000000000103', 6,
            )
        self.assertIsNone(result)
        send.assert_not_called()
        self.assertFalse(connection.committed)


if __name__ == '__main__':
    unittest.main()
