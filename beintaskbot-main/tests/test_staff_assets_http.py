"""Exercise the production asset router, not a permissive file-preview server."""
from html.parser import HTMLParser
from pathlib import Path
import unittest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from tenant_web_assets import register_assets

ROOT=Path(__file__).resolve().parents[1]


class StaffAssets(unittest.IsolatedAsyncioTestCase):
    async def test_declared_staff_assets_are_served_with_revalidation_and_correct_mime(self):
        class References(HTMLParser):
            urls=[]
            def handle_starttag(self,tag,attributes):
                attrs=dict(attributes)
                if tag=='script':self.urls.append(attrs.get('src',''))
                if tag=='link':self.urls.append(attrs.get('href',''))
        refs=References();refs.feed((ROOT/'docs/tenant-app.html').read_text(encoding='utf-8'))
        urls=[url for url in refs.urls if 'tenant-members-ui.' in url]
        self.assertEqual(len(urls),2)
        app=web.Application();register_assets(app)
        async with TestClient(TestServer(app)) as client:
            for url in urls:
                response=await client.get(url)
                self.assertEqual(response.status,200,url)
                self.assertEqual(await response.read(),(ROOT/'docs'/url.split('/')[-1]).read_bytes())
                self.assertIn('must-revalidate',response.headers['Cache-Control'])
                self.assertIn('javascript' if url.endswith('.js') else 'text/css',response.content_type)
                head=await client.head(url)
                self.assertEqual(head.status,200)
                cached=await client.get(url,headers={'If-None-Match':response.headers['ETag']})
                self.assertEqual(cached.status,304)
