"""Isolated Postgres + local WS regression; never contacts a provider."""
import asyncio
import os
import re
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tenant_account_store as accounts
from tenant_browser_session import TenantBrowserSession, COOKIE_NAME
from tenant_operator_voice_session import Session
from tenant_operator_voice_api import handler
import tenant_operator_voice_orchestration as service


def local_test_dsn():
    value=os.environ.get('TEST_DATABASE_URL','')
    if not value:raise unittest.SkipTest('isolated local TEST_DATABASE_URL not configured')
    if not re.fullmatch(r'host=/tmp/bein-lease-pg\.[A-Za-z0-9]+ dbname=voice_lease_test user=postgres',value):
        raise RuntimeError('TEST_DATABASE_URL must identify the isolated local Unix socket')
    return value


class BrowserRevocationPostgresTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn=local_test_dsn();cls.schema='voice_auth_'+uuid.uuid4().hex
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            database,local=conn.execute('SELECT current_database(), inet_server_addr() IS NULL').fetchone()
            if database!='voice_lease_test' or not local:raise RuntimeError('unexpected database or nonlocal connection')
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(cls.schema)))
            conn.execute('CREATE TABLE saas_tenant_members (tenant_id uuid,telegram_id bigint,active bool)')
            conn.execute('CREATE TABLE saas_auth_users (id uuid,actor_id bigint,disabled_at timestamptz,status text)')
            conn.execute('CREATE TABLE saas_tenant_browser_sessions (id uuid,tenant_id uuid,actor_id bigint,revoked_at timestamptz,expires_at timestamptz)')

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))

    def connect(self):
        return psycopg.connect(self.dsn,options='-csearch_path='+self.schema,row_factory=dict_row)

    async def test_live_websocket_stops_input_after_browser_revoke_or_account_disable(self):
        for mode in ('session_revoked','account_disabled'):
            with self.subTest(mode=mode):
                tenant=str(uuid.uuid4());sid=str(uuid.uuid4());actor=7
                with self.connect() as conn:
                    conn.execute('INSERT INTO saas_tenant_members VALUES (%s,%s,true)',(tenant,actor))
                    conn.execute("INSERT INTO saas_auth_users VALUES (%s,%s,NULL,'active')",(str(uuid.uuid4()),actor))
                    conn.execute("INSERT INTO saas_tenant_browser_sessions VALUES (%s,%s,%s,NULL,now()+interval '1 hour')",(sid,tenant,actor))
                browser=TenantBrowserSession(secret=lambda:'synthetic-secret',member=lambda t,a:{'tenant_id':t,'telegram_id':a},
                    identity_allowed=lambda *_:False,membership_error=ValueError,
                    create_session=lambda **_:None,session_active=accounts.session_is_active)
                # Build a real signed cookie referring to the isolated row.
                payload=f'{tenant}.{actor}.{int(time.time())+3600}.{sid}'
                cookie=payload+'.'+browser._signature(payload)
                scope=SimpleNamespace(tenant=tenant,actor=actor,channel_id='channel',conversation_id='dialog',
                                      lead_id=1,agent_binding='synthetic',config_version='v1')
                sent=[];first=asyncio.Event();closed=asyncio.Event();sessions=[]
                class Provider:
                    mock_only=True
                    async def bind_claim(self,*_):pass
                    async def send(self,event):sent.append(event['type']);first.set()
                provider=Provider()
                lease=SimpleNamespace(assert_current=AsyncMock())
                def compose(authority,client,_lease,session_id):
                    s=Session(authority.session_scope,authority=authority,ledger=object(),lease=lease,
                              context=object(),provider=provider,client=client,session_id=session_id,duplex=True)
                    async def start():s.closed=False;s.fence=1;s.started=time.monotonic()
                    async def end(reason):s.closed=True;closed.set();return True
                    s.start=start;s.end=end;sessions.append(s);return s
                app=web.Application();app.router.add_get('/ws',handler(browser.profile,'https://test.local',SimpleNamespace(warning=lambda *_:None),'ws'))
                client=TestClient(TestServer(app));await client.start_server()
                try:
                    with patch.object(accounts,'_connect',self.connect),patch.object(accounts,'_ensure_schema',lambda *_:None), \
                         patch.object(service.voice,'check',return_value=scope),patch.object(service,'require_pilot'), \
                         patch.object(service,'_create_server_lease',return_value=lease),patch.object(service,'_compose_pilot_session',side_effect=compose):
                        ws=await client.ws_connect('/ws',params={'expected_tenant_id':tenant,'expected_user_id':str(actor),
                            'channel_id':'channel','conversation_id':'dialog','lead_id':'1'},
                            headers={'Origin':'https://test.local','Cookie':f'{COOKIE_NAME}={cookie}'})
                        await ws.send_bytes(bytes(2));await asyncio.wait_for(first.wait(),3)
                        def revoke():
                            with self.connect() as conn:
                                if mode=='session_revoked':conn.execute('UPDATE saas_tenant_browser_sessions SET revoked_at=now() WHERE id=%s',(sid,))
                                else:conn.execute("UPDATE saas_auth_users SET disabled_at=now() WHERE actor_id=%s",(actor,))
                        await asyncio.to_thread(revoke)
                        await ws.send_bytes(bytes(2));await asyncio.wait_for(closed.wait(),3)
                        self.assertEqual(sent,['input_audio_buffer.append'])
                        self.assertTrue(sessions[0].closed)
                        await ws.close()
                finally:
                    await client.close()
                    with self.connect() as conn:
                        conn.execute('DELETE FROM saas_tenant_browser_sessions');conn.execute('DELETE FROM saas_auth_users');conn.execute('DELETE FROM saas_tenant_members')
