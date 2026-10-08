import asyncio
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aiohttp import web
from kommo_deal_tags import mutation, public_tags, tag_list, tag_ref, tags_handler

ROOT = Path(__file__).resolve().parents[1]
LEAD = {"_embedded": {"tags": [{"id": 1, "name": "Müştəri"}, {"id": 2, "name": "Yeni"}]}}


class TagTests(unittest.TestCase):
    def test_reference_and_deduplication(self):
        self.assertEqual(tag_list([{"id": "1"}, {"id": 1}, {"name": " Yeni "}]),
                         [{"id": 1}, {"name": "Yeni"}])
        for value in ({"id": True}, {"id": -1}, {"id": "1.2"}, {}, {"name": "\n"}, {"name": "x"*101}):
            with self.assertRaises(ValueError): tag_ref(value)
        with self.assertRaises(ValueError): tag_list([{"id": 1}]*51)

    def test_delta_preserves_unseen_tags(self):
        self.assertEqual(mutation({"operation": "add", "tag": {"name": "Demo"}}, LEAD),
                         {"tags_to_add": [{"name": "Demo"}]})
        self.assertEqual(mutation({"operation": "remove", "remove_id": 1}, LEAD),
                         {"tags_to_delete": [{"id": 1}]})

    def test_replace_is_not_global_rename(self):
        self.assertEqual(mutation({"operation": "replace", "remove_id": 2, "tag": {"name": "Test"}}, LEAD),
                         {"tags_to_delete": [{"id": 2}], "tags_to_add": [{"name": "Test"}]})
        with self.assertRaises(ValueError): mutation({"operation": "remove", "remove_id": 3}, LEAD)
        with self.assertRaises(ValueError): mutation({"operation": "rename"}, LEAD)

    def test_empty_tags_and_safe_public_fields(self):
        self.assertEqual(public_tags({}), [])
        self.assertEqual(public_tags(LEAD), [{"id": 1, "name": "Müştəri"}, {"id": 2, "name": "Yeni"}])

    def test_creation_attaches_tags_in_same_request(self):
        tree = ast.parse((ROOT/'bot.py').read_text(encoding='utf-8-sig'))
        tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'create_lead_for_contact']
        http = Mock(); http.post.return_value = SimpleNamespace(status_code=200, json=lambda: {"_embedded": {"leads": [{"id": 4}]}})
        ns = {"_http": http, "KOMMO_BASE_URL": "https://test.kommo.com", "KOMMO_TOKEN": "test", "PIPELINE_ID": 1, "logger": Mock()}
        exec(compile(tree, '<create-deal>', 'exec'), ns)
        self.assertEqual(ns['create_lead_for_contact'](1, 'Client', tags=[{"id": 2}]), 4)
        self.assertEqual(http.post.call_args.kwargs['json'][0]['_embedded'], {"contacts": [{"id": 1}], "tags": [{"id": 2}]})


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.authorize = Mock(return_value=(LEAD, None))
        self.http = Mock(); self.http.get.return_value = SimpleNamespace(status_code=200, json=lambda: {**LEAD, "_links": {"next": {"href": "next"}}})
        self.update = Mock(return_value={"id": 4}); self.invalidate = Mock()
        self.handler = tags_handler(identify=lambda request: request.user, authorize=self.authorize,
            http=self.http, base_url='https://test.kommo.com', headers={}, update=self.update,
            invalidate=self.invalidate, logger=Mock())

    def request(self, method='GET', user=10, **data):
        async def body(): return data
        return SimpleNamespace(method=method, user=user, query=data, json=body)

    async def test_signed_identity_required(self):
        response = await self.handler(self.request(user=None, lead_id=4))
        self.assertEqual(response.status, 401); self.http.get.assert_not_called()

    async def test_foreign_deal_denied_before_catalog_or_mutation(self):
        self.authorize.return_value = (None, web.json_response({}, status=403))
        response = await self.handler(self.request('POST', lead_id=4, operation='add', tag={'id': 1}))
        self.assertEqual(response.status, 403); self.http.get.assert_not_called(); self.update.assert_not_called()

    async def test_bounded_catalog_with_search_and_pagination(self):
        response = await self.handler(self.request(lead_id=4, page=2, query='Demo'))
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.text)['more'])
        self.assertEqual(self.http.get.call_args.kwargs['params'], {'page':2,'limit':50,'query':'Demo'})

    async def test_provider_error_not_reported_as_success(self):
        self.update.return_value = None
        response = await self.handler(self.request('POST', lead_id=4, operation='add', tag={'name':'Demo'}))
        self.assertEqual(response.status, 502); self.invalidate.assert_not_called()

    async def test_success_refreshes_tags_and_invalidates_cache(self):
        response = await self.handler(self.request('POST', lead_id=4, operation='remove', remove_id=1))
        self.assertEqual(response.status, 200)
        self.update.assert_called_once_with(4, {'tags_to_delete': [{'id': 1}]})
        self.invalidate.assert_called_once(); self.assertEqual(json.loads(response.text)['tags'], public_tags(LEAD))

    async def test_missing_deal_and_unknown_tag_rejected(self):
        for data in ({'operation':'add','tag':{'id':1}}, {'lead_id':4,'operation':'remove','remove_id':9}):
            self.assertEqual((await self.handler(self.request('POST', **data))).status, 400)
        self.update.assert_not_called()


if __name__ == '__main__': unittest.main()
