"""Quick replies against the isolated PostgreSQL engine and the scoped HTTP API."""

import asyncio
import json
import logging
import uuid

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tenant_dialog_quick_replies as replies
from tenant_chat_send_schema import ensure_schema
from tenant_operator_api import handler
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db


def test_personal_confirmed_history_and_dismissal(db, monkeypatch):
    monkeypatch.setattr(replies, '_connect', lambda: db)
    ensure_schema(db)
    second_channel = str(uuid.uuid4())
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES (%s,%s,'Other channel')",
               (A, second_channel))
    db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id)
      VALUES (%s,%s,'client',43),(%s,%s,'another-client',44)""", (A, second_channel, A, CA))
    db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions)
      VALUES (%s,33,'worker','["dialogs"]'),(%s,34,'worker','[]')""", (A, A))

    def sent(key, body, *, tenant=A, actor=11, channel=CA, lead=41, age='1 day',
             state='accepted', direction='outgoing', kind='text', raw=None, origin='connect'):
        db.execute("""INSERT INTO saas_crm_messages
          (id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,happened_at,raw)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()-%s::interval,%s::jsonb)""",
                   (str(uuid.uuid4()), tenant, lead, 'connect:'+key, 'connect:'+channel,
                    direction, body, kind, age, json.dumps(raw or {})))
        db.execute("""INSERT INTO saas_chat_send_receipts
          (tenant_id,actor_id,request_id,lead_id,input_hash,state,route,message_id)
          VALUES (%s,%s,%s,%s,'synthetic',%s,%s::jsonb,%s)""",
                   (tenant, actor, str(uuid.uuid4()), lead, state, json.dumps({'origin': origin}), key))

    sent('repeated-1', 'Добрый день!')
    sent('repeated-2', 'Добрый день!', lead=44)
    sent('another', 'Добрый вечер.')
    sent('months-old', 'Добрый устаревший', age='4 months')
    sent('foreign-actor', 'Добрый оператор', actor=33)
    sent('foreign-channel', 'Добрый другой канал', channel=second_channel, lead=43)
    sent('foreign-tenant', 'Добрый другая компания', tenant=B, actor=22, channel=CB, lead=42)
    sent('uncertain', 'Добрый неизвестно', state='unknown')
    sent('incoming', 'Добрый входящий', direction='incoming')
    sent('ai', 'Добрый агент', raw={'run_id': 'synthetic'})
    sent('media', 'Добрый файл', kind='document')
    sent('left', 'Добрый удалённый', raw={'status': 'revoked'})
    sent('other-provider', 'Добрый другой источник', origin='whatsapp')
    sent('markup', 'Стоимость 10% и выше')
    db.commit()

    origin = 'https://crm.example'
    path = '/api/platform/ai/dialogs/quick-replies'
    payload = dict(action='search', prefix='ДОБРЫ', channel_id=CA, conversation_id='client',
                   lead_id=41, expected_tenant_id=A, expected_user_id=11)
    active = dict(PROFILE)
    app = web.Application()
    app.router.add_post(path, handler(lambda _: active, origin, logging.getLogger(__name__), 'quick_replies'))

    async def exercise():
        async with TestClient(TestServer(app)) as client:
            async def ask(patch=None, expected=200, caller_origin=origin):
                response = await client.post(path, json={**payload, **(patch or {})}, headers={'Origin': caller_origin})
                assert response.status == expected
                assert response.headers.get('Cache-Control') == 'no-store'
                return (await response.json()).get('result', {}).get('items')

            items = await ask()
            assert [(i['body'], i['uses']) for i in items] == [('Добрый день!', 2), ('Добрый вечер.', 1)]
            assert [i['body'] for i in await ask({'prefix': 'Стоимость 10%'})] == ['Стоимость 10% и выше']
            assert await ask({'prefix': 'Стоимость 1%'}) == []
            await ask({'expected_tenant_id': B}, expected=409)
            await ask({'expected_user_id': 33}, expected=409)
            await ask({'channel_id': CB}, expected=404)
            await ask({'lead_id': 43}, expected=404)
            await ask(expected=403, caller_origin='https://foreign.example')
            old_match = items[0]['id']
            hidden = dict(action='dismiss', reply_id=old_match)
            assert [i['body'] for i in await ask(hidden)] == ['Добрый вечер.']
            assert await ask(hidden) == await ask()
            assert [i['body'] for i in await ask({'channel_id': second_channel, 'lead_id': 43})] == [
                'Добрый другой канал'
            ]
            active['telegram_id'] = 33
            assert [i['body'] for i in await ask({'expected_user_id': 33})] == ['Добрый оператор']
            monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
            db.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES
              (%s,'members','{"33":{"access_scopes":{"dialogs":{"view":"self","update":"all"}}}}')""", (A,))
            db.execute("""INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by)
              VALUES (%s,%s,33,11)""", (A, CA))
            db.commit()
            assert [i['body'] for i in await ask({'expected_user_id': 33})] == ['Добрый оператор']
            await ask({'expected_user_id': 33, 'channel_id': second_channel, 'lead_id': 43}, expected=403)
            active['telegram_id'] = 34
            await ask({'expected_user_id': 34}, expected=403)
            active['telegram_id'] = 11
            sent('after-dismiss', 'Добрый день!', age='0 days')
            db.commit()
            restored = await ask()
            assert next(i for i in restored if i['id'] == old_match)['uses'] == 1
            assert len(db.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s AND body='Добрый день!'", (A,))) == 3

    asyncio.run(exercise())
