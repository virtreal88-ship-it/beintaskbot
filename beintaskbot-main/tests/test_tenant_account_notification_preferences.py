"""Self-service notification preference writes persist through PostgreSQL JSONB."""
import json
import re
import subprocess
from pathlib import Path

import pytest

pytest.importorskip('psycopg')
import tenant_account_store as account_store
import tenant_member_store
import tenant_telegram_binding

ROOT = Path(__file__).resolve().parents[1]
TENANT = '00000000-0000-0000-0000-000000000101'
ACTOR = 1628569350
OTHER_ACTOR = 1628569351


class Database:
    def __init__(self):
        helper = ROOT / 'tests' / 'support_pglite.cjs'
        self.process = subprocess.Popen(
            ['node', str(helper)], cwd=ROOT, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
        )

    def query(self, sql, params=()):
        index = iter(range(1, 1000))
        sql = re.sub(r'%s', lambda _: '$' + str(next(index)), sql)
        self.process.stdin.write(json.dumps({'sql': sql, 'params': params or []}) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(self.process.stderr.read())
        result = json.loads(line)
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result['rows']

    execute = query

    def cursor(self):
        return Cursor(self)

    def commit(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


class Cursor:
    def __init__(self, database):
        self.database = database
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, sql, params=None):
        self.rows = self.database.execute(sql, params)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


def test_save_updates_only_current_members_notification_preferences(monkeypatch):
    database = Database()
    profile = {
        'tenant_id': TENANT,
        'telegram_id': ACTOR,
        'role': 'owner',
        'active': True,
        'permissions': [],
        'workflow': {'policies': {'members': {str(ACTOR): {'display_name': 'Owner', 'role': 'owner'}}}},
    }
    monkeypatch.setattr(account_store, '_connect', lambda: database)
    monkeypatch.setattr(account_store, '_ensure_schema', lambda _conn: None)
    monkeypatch.setattr(tenant_member_store, 'member', lambda _tenant, _actor: profile)
    monkeypatch.setattr(tenant_telegram_binding, 'binding_status', lambda **_kwargs: {'bound': False})
    monkeypatch.setattr(account_store, '_json', json.dumps)
    try:
        database.execute('''CREATE TABLE saas_tenant_workflow_policies(
            tenant_id UUID NOT NULL, policy_key TEXT NOT NULL, value JSONB NOT NULL,
            updated_by BIGINT, updated_at TIMESTAMPTZ DEFAULT now(), UNIQUE(tenant_id,policy_key))''')
        database.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value,updated_by)
            VALUES(%s::uuid,'members',%s::jsonb,%s)''',
            (TENANT, json.dumps({
                str(ACTOR): {'role': 'owner', 'ai_agents_manage': True},
                str(OTHER_ACTOR): {'role': 'staff', 'notifications': {'incoming_message': {'push': False}}},
            }), ACTOR))
        preferences = {
            'incoming_message': {'push': True, 'internal': True},
            'ai_developer_consultation': {'push': True, 'internal': True},
        }

        result = account_store.notification_preferences(tenant_id=TENANT, actor_id=ACTOR, value=preferences)
        assert result['preferences']['incoming_message'] == preferences['incoming_message']
        assert result['preferences']['ai_developer_consultation'] == preferences['ai_developer_consultation']

        saved = database.execute('''SELECT value FROM saas_tenant_workflow_policies
            WHERE tenant_id=%s::uuid AND policy_key='members' ''', (TENANT,))[0]['value']
        assert saved[str(ACTOR)]['role'] == 'owner'
        assert saved[str(ACTOR)]['ai_agents_manage'] is True
        assert saved[str(ACTOR)]['notifications'] == preferences
        assert saved[str(OTHER_ACTOR)]['notifications'] == {'incoming_message': {'push': False}}

        updated = {'incoming_message': {'push': False, 'internal': True}}
        account_store.notification_preferences(tenant_id=TENANT, actor_id=ACTOR, value=updated)
        saved_again = database.execute('''SELECT value FROM saas_tenant_workflow_policies
            WHERE tenant_id=%s::uuid AND policy_key='members' ''', (TENANT,))[0]['value']
        assert saved_again[str(ACTOR)]['notifications'] == updated
        assert saved_again[str(ACTOR)]['ai_agents_manage'] is True
        assert saved_again[str(OTHER_ACTOR)]['notifications'] == {'incoming_message': {'push': False}}
    finally:
        database.close()
