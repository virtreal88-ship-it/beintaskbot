from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import requests
import test_tenant_hot_orders as loaders
from tenant_chat_send_policy import command, resolve_route
from tenant_chat_send_provider import send
from tenant_linear_policy import TenantLinearError

TENANT = 'a9c11a16-01d5-469a-a6c1-9a726975ae42'
INPUT = {'request_id': '79d6467b-3ca6-4557-9e12-b9da059c0611', 'lead_id': 7, 'text': 'Salam'}
SESSION = {'tenant_id': TENANT, 'telegram_id': 20}


def talk(number):
    return {'talk_id': number, 'chat_id': str(number), 'entity_id': 7, 'entity_type': 'lead', 'is_in_work': True}


def page(number, at=100, origin='instagram', direction='incoming'):
    return {'messages': [{'id': 'msg-' + str(number), 'chat_id': str(number), 'created_at': at, 'type': direction, 'origin': origin}]}


class Routing(unittest.IsolatedAsyncioTestCase):
    def test_command_is_bounded_and_does_not_accept_browser_route(self):
        self.assertEqual(command({**INPUT, 'talk_id': 999, 'channel': 'wrong'}), INPUT)
        for value in ({**INPUT, 'request_id': 'bad'}, {**INPUT, 'lead_id': -1}, {**INPUT, 'text': ''}, {**INPUT, 'text': 'x' * 8001}):
            with self.assertRaises(TenantLinearError): command(value)

    async def test_last_inbound_not_latest_updated_talk(self):
        provider = AsyncMock(side_effect=[{'_embedded': {'talks': [talk(1), talk(2)]}}, page(1, 200, 'whatsapp', 'outgoing'), page(2, 100)])
        result = await resolve_route(TENANT, 7, provider)
        self.assertEqual(result['talk_id'], 2)
        self.assertEqual(result['origin'], 'instagram')
        self.assertTrue(all(call.args[0] == TENANT for call in provider.call_args_list))

    async def test_tiktok_and_instagram_origins_not_guessed_from_names(self):
        provider = AsyncMock(side_effect=[{'_embedded': {'talks': [talk(1), talk(2)]}}, page(1, 100), page(2, 200, 'custom-tiktok-channel')])
        self.assertEqual((await resolve_route(TENANT, 7, provider))['origin'], 'custom-tiktok-channel')

    async def test_foreign_lead_or_chat_rejected(self):
        for conversations, messages in (([{**talk(1), 'entity_id': 8}], page(1)), ([talk(1)], page(2))):
            provider = AsyncMock(side_effect=[{'_embedded': {'talks': conversations}}, messages])
            with self.assertRaises(TenantLinearError): await resolve_route(TENANT, 7, provider)

    async def test_partial_catalog_or_history_blocks_instead_of_fallback(self):
        for catalog, history in (({'_links': {'next': {}}, '_embedded': {'talks': [talk(1)]}}, page(1)), ({'_embedded': {'talks': [talk(1)]}}, {**page(1), '_links': {'next': {'href': 'more'}}})):
            if catalog.get('_links'): catalog['_links']['next'] = {'href': 'more'}
            with self.assertRaises(TenantLinearError): await resolve_route(TENANT, 7, AsyncMock(side_effect=[catalog, history]))

    async def test_closed_latest_chat_not_replaced_with_other_channel(self):
        with self.assertRaises(TenantLinearError):
            await resolve_route(TENANT, 7, AsyncMock(side_effect=[{'_embedded': {'talks': [{**talk(1), 'is_in_work': False}]}}, page(1)]))

    async def test_no_inbound_and_ambiguous_time_are_rejected(self):
        for payload in ([{'_embedded': {'talks': [talk(1)]}}, page(1, direction='outgoing')], [{'_embedded': {'talks': [talk(1), talk(2)]}}, page(1), page(2)]):
            with self.assertRaises(TenantLinearError): await resolve_route(TENANT, 7, AsyncMock(side_effect=payload))


class Provider(unittest.TestCase):
    def test_single_post_and_provider_message_id(self):
        response = Mock(status_code=202); response.json.return_value = {'id': 'server-message'}
        with patch('tenant_chat_send_provider.requests.post', return_value=response) as post:
            self.assertEqual(send({'account_domain': 'client.kommo.com', 'access_token': 'key'}, {'talk_id': 3}, 'Salam', Mock()), 'server-message')
        post.assert_called_once()
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(post.call_args.kwargs['json'], {'text': 'Salam'})

    def test_timeout_is_unknown_and_is_not_retried(self):
        with patch('tenant_chat_send_provider.requests.post', side_effect=requests.Timeout) as post:
            with self.assertRaises(RuntimeError): send({'account_domain': 'client.kommo.com', 'access_token': 'key'}, {'talk_id': 3}, 'text', Mock())
        post.assert_called_once()

    def test_explicit_rejection_and_bad_acceptance(self):
        for status in (401, 402, 403, 422):
            with patch('tenant_chat_send_provider.requests.post', return_value=Mock(status_code=status)):
                with self.assertRaises(TenantLinearError): send({'account_domain': 'client.kommo.com', 'access_token': 'key'}, {'talk_id': 3}, 'text', Mock())
        response = Mock(status_code=202); response.json.return_value = {}
        with patch('tenant_chat_send_provider.requests.post', return_value=response):
            with self.assertRaises(RuntimeError): send({'account_domain': 'client.kommo.com', 'access_token': 'key'}, {'talk_id': 3}, 'text', Mock())


class Service(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item = {'key': (TENANT, 20, INPUT['request_id']), 'command': INPUT, 'connection': ['client.kommo.com', 'version']}
        self.reserve = Mock(return_value=self.item); self.finish = Mock()
        self.mark = Mock(return_value=(SESSION, {'account_domain': 'client.kommo.com', 'access_token': 'tenant-key'}))
        self.send = Mock(return_value='provider-id')
        self.route = AsyncMock(return_value={'talk_id': 3, 'origin': 'instagram'})
        self.provider = AsyncMock(return_value={'id': 7, 'pipeline_id': 10})
        self.module = loaders.load('tenant_chat_send_service', {
            'reserve': self.reserve, 'finish': self.finish, 'mark_sending': self.mark, 'send': self.send,
            'command': command, 'resolve_route': self.route,
            'TenantPolicy': lambda person: SimpleNamespace(can_access_deal=lambda lead: lead['pipeline_id'] == 10)},
            {'tenant_chat_send_policy', 'tenant_chat_send_store', 'tenant_chat_send_provider', 'tenant_policy'})

    async def run_send(self):
        return await self.module.run(SESSION, INPUT, self.provider, Mock(), Mock())

    async def test_success_has_acceptance_receipt(self):
        self.assertEqual((await self.run_send())['state'], 'accepted')
        self.finish.assert_called_once_with(self.item, 'accepted', 'provider-id')

    async def test_repeated_accepted_request_never_calls_provider(self):
        self.reserve.return_value = {'cached_result': {'state': 'accepted', 'message_id': 'old'}}
        self.assertEqual((await self.run_send())['message_id'], 'old')
        self.provider.assert_not_awaited(); self.send.assert_not_called()

    async def test_live_pipeline_move_blocks_send(self):
        self.provider.return_value = {'id': 7, 'pipeline_id': 11}
        with self.assertRaises(TenantLinearError): await self.run_send()
        self.send.assert_not_called(); self.finish.assert_called_once_with(self.item, 'blocked')

    async def test_unknown_result_keeps_same_request(self):
        self.send.side_effect = RuntimeError('timeout')
        with self.assertRaises(TenantLinearError) as raised: await self.run_send()
        self.assertTrue(raised.exception.keep_request)
        self.finish.assert_called_once_with(self.item, 'unknown')

    async def test_lost_receipt_write_does_not_resend(self):
        self.finish.side_effect = RuntimeError('database unavailable')
        with self.assertRaises(TenantLinearError) as raised: await self.run_send()
        self.assertTrue(raised.exception.keep_request)
        self.send.assert_called_once()

    async def test_preflight_failure_does_not_call_send(self):
        self.route.side_effect = TenantLinearError('ambiguous', 409)
        with self.assertRaises(TenantLinearError): await self.run_send()
        self.send.assert_not_called(); self.mark.assert_not_called()


if __name__ == '__main__': unittest.main()
