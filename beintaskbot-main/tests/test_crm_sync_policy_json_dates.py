"""Regression: PostgreSQL timestamp values are JSON-safe without changing policy."""
import ast
import asyncio
import json
import logging
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
TENANT = '00000000-0000-0000-0000-000000000001'
OTHER = '00000000-0000-0000-0000-000000000002'


def load_policy(connect):
    tree = ast.parse((ROOT / 'tenant_crm_sync_policy.py').read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or n.module != 'tenant_platform_core']
    ns = {'_connect': connect, '_ensure_schema': Mock(), '_json': json.dumps}
    exec(compile(tree, 'tenant_crm_sync_policy.py', 'exec'), ns)
    return SimpleNamespace(**ns)


def load_api(policy):
    tree = ast.parse((ROOT / 'tenant_crm_sync_policy_api.py').read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or n.module not in {'tenant_crm_sync_policy','tenant_request_origin'}]
    ns = {'read_settings': policy.read_settings, 'save_settings': policy.save_settings,
          'SyncPolicyError': policy.SyncPolicyError, 'require_json_post': Mock()}
    exec(compile(tree, 'tenant_crm_sync_policy_api.py', 'exec'), ns)
    return SimpleNamespace(**ns)


def db_with_rows(rows):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = [rows, [], [], [{'provider':'kommo_manual','status':'connected'}]]
    return conn, cur, Mock(return_value=conn)


class Request:
    def __init__(self, method, body=None): self.method, self.body = method, body
    async def json(self): return self.body


class SettingsSerializationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.utc = datetime(2026,11,9,15,14,32,tzinfo=timezone.utc)
        self.offset = datetime(2026,11,9,19,14,32,tzinfo=timezone(timedelta(hours=4)))
        self.rows = [
            {'entity_type':'deal','provider':'kommo','mode':'external','primary_provider':'kommo',
             'sync_direction':'from_external','allowed_fields':['title','pipeline_id','stage_id'],
             'enabled_since':self.utc,'updated_at':self.offset},
            {'entity_type':'contact','provider':'kommo','mode':'external','primary_provider':'kommo',
             'sync_direction':'off','allowed_fields':[],'enabled_since':None,'updated_at':self.utc},
        ]
        self.conn,self.cur,self.connect = db_with_rows(self.rows)
        self.policy = load_policy(self.connect)
        self.profile = {'tenant_id':TENANT,'telegram_id':1,'role':'owner','active':True}
        self.api = load_api(self.policy)
        self.log = Mock(spec=logging.Logger)

    async def test_get_real_json_response_preserves_policy_and_offset(self):
        response = await self.api.handler(lambda req:self.profile,'https://example.test',self.log)(Request('GET'))
        self.assertEqual(response.status,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        data = json.loads(response.body)['result']
        self.assertEqual(data['settings'][0]['enabled_since'],self.utc.isoformat())
        self.assertEqual(data['settings'][0]['updated_at'],self.offset.isoformat())
        self.assertEqual(data['settings'][0]['sync_direction'],'from_external')
        self.assertEqual(data['settings'][1]['sync_direction'],'off')
        self.assertIsNone(data['settings'][1]['enabled_since'])
        self.assertEqual(data['settings'][0]['allowed_fields'],self.rows[0]['allowed_fields'])
        self.assertIs(self.rows[0]['enabled_since'],self.utc)
        self.assertEqual(data['integrations'],{'kommo':'connected'})
        for call in self.cur.execute.call_args_list:
            self.assertEqual(call.args[1][0],TENANT)
        self.assertNotIn(OTHER,response.body.decode())
        self.log.exception.assert_not_called()

    async def test_post_after_commit_returns_json_not_503(self):
        item={'entity_type':'deal','provider':'kommo','mode':'external','primary_provider':'kommo',
              'sync_direction':'off','allowed_fields':[]}
        response = await self.api.handler(lambda req:self.profile,'https://example.test',self.log)(Request('POST',{'settings':[item]}))
        self.assertEqual(response.status,200)
        self.assertEqual(json.loads(response.body)['result']['settings'][0]['updated_at'],self.offset.isoformat())
        self.conn.commit.assert_called_once()
        self.log.exception.assert_not_called()
        self.api.require_json_post.assert_called_once()

    async def test_native_postgres_get_and_post_are_tenant_scoped_and_json_safe(self):
        dsn=os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
        if not dsn: self.skipTest('isolated PostgreSQL DSN not configured')
        import psycopg
        from psycopg.rows import dict_row
        from contextlib import nullcontext
        conn=psycopg.connect(dsn, row_factory=dict_row)
        try:
            with conn.cursor() as cur:
                cur.execute("""CREATE TEMP TABLE saas_crm_provider_settings (
                    tenant_id uuid, entity_type text, provider text, mode text,
                    primary_provider text, sync_direction text, allowed_fields jsonb,
                    enabled_since timestamptz, updated_at timestamptz DEFAULT now(),
                    updated_by bigint, PRIMARY KEY(tenant_id,entity_type))""")
                cur.execute("""CREATE TEMP TABLE saas_crm_sync_outbox (
                    tenant_id uuid,provider text,entity_type text,state text)""")
                cur.execute("""CREATE TEMP TABLE saas_crm_sync_conflicts (
                    tenant_id uuid,provider text,entity_type text,resolved_at timestamptz)""")
                cur.execute("CREATE TEMP TABLE saas_tenant_integrations (tenant_id uuid,provider text,status text)")
                cur.execute("""CREATE TEMP TABLE saas_tenant_audit_events (
                    id uuid,tenant_id uuid,action text,entity_type text,entity_id text,payload jsonb)""")
                cur.execute("""INSERT INTO saas_crm_provider_settings
                    (tenant_id,entity_type,provider,mode,primary_provider,sync_direction,allowed_fields,enabled_since,updated_at)
                    VALUES (%s,'deal','kommo','external','kommo','from_external',
                    '["title","pipeline_id","stage_id"]'::jsonb,%s,%s),
                    (%s,'deal','kommo','external','kommo','from_external','["title","pipeline_id","stage_id"]'::jsonb,%s,%s)""",
                    (TENANT,self.utc,self.offset,OTHER,self.utc,self.utc))
                cur.execute("INSERT INTO saas_tenant_integrations VALUES (%s,'kommo_manual','connected')",(TENANT,))
            conn.commit()
            policy=load_policy(lambda:nullcontext(conn))
            api=load_api(policy)
            handler=api.handler(lambda req:self.profile,'https://example.test',self.log)
            get_response=await handler(Request('GET'))
            self.assertEqual(get_response.status,200)
            before=json.loads(get_response.body)['result']
            self.assertEqual(len(before['settings']),1)
            self.assertEqual(before['settings'][0]['sync_direction'],'from_external')
            self.assertEqual(datetime.fromisoformat(before['settings'][0]['enabled_since']),self.utc)
            item={'entity_type':'deal','provider':'kommo','mode':'external','primary_provider':'kommo',
                  'sync_direction':'off','allowed_fields':[]}
            post_response=await handler(Request('POST',{'settings':[item]}))
            self.assertEqual(post_response.status,200)
            after=json.loads(post_response.body)['result']
            self.assertEqual(after['settings'][0]['sync_direction'],'off')
            self.assertIsNone(after['settings'][0]['enabled_since'])
            self.assertIsNotNone(datetime.fromisoformat(after['settings'][0]['updated_at']))
            with conn.cursor() as cur:
                cur.execute('SELECT sync_direction FROM saas_crm_provider_settings WHERE tenant_id=%s',(OTHER,))
                self.assertEqual(cur.fetchone()['sync_direction'],'from_external')
                cur.execute('SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s',(TENANT,))
                self.assertEqual(cur.fetchone()['n'],1)
            denied=await api.handler(lambda req:{**self.profile,'role':'manager'},'https://example.test',self.log)(Request('GET'))
            self.assertEqual(denied.status,403)
        finally:
            conn.close()

    async def test_manager_and_inactive_owner_do_not_get_settings(self):
        for profile in ({**self.profile,'role':'manager'}, {**self.profile,'active':False}):
            self.connect.reset_mock()
            response=await self.api.handler(lambda req:profile,'https://example.test',self.log)(Request('GET'))
            self.assertEqual(response.status,403)
            self.connect.assert_not_called()

if __name__ == '__main__': unittest.main()
