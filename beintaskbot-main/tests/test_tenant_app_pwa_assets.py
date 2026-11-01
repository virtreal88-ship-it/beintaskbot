"""Exact /app PWA routes: public install metadata only, no CRM data cache route."""
import json
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tenant_web_assets import register_assets

ROOT = Path(__file__).resolve().parents[1]


class TenantAppPwaAssets(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_worker_and_icons_are_app_scoped_and_uncached_by_http(self):
        app = web.Application()
        register_assets(app)
        async with TestClient(TestServer(app)) as client:
            manifest_response = await client.get('/app/manifest.webmanifest')
            self.assertEqual(manifest_response.status, 200)
            self.assertEqual(manifest_response.content_type, 'application/manifest+json')
            self.assertIn('no-store', manifest_response.headers['Cache-Control'])
            manifest = await manifest_response.json()
            self.assertEqual(manifest['start_url'], '/app')
            self.assertEqual(manifest['scope'], '/app')
            self.assertEqual(manifest['display'], 'standalone')
            self.assertEqual({icon['sizes'] for icon in manifest['icons']}, {'192x192', '512x512'})
            self.assertTrue(all(icon['src'].startswith('/app/pwa-icon-') for icon in manifest['icons']))

            worker_response = await client.get('/app/pwa-sw.js')
            self.assertEqual(worker_response.status, 200)
            self.assertEqual(worker_response.headers.get('Service-Worker-Allowed'), '/app')
            self.assertIn('no-store', worker_response.headers['Cache-Control'])
            worker = await worker_response.text()
            self.assertIn("url.pathname.startsWith('/api/')", worker)
            self.assertIn("request.destination === 'document'", worker)
            self.assertIn('url.search || request.headers.has(\'authorization\')', worker)
            self.assertIn("SAFE_APP_ASSET.test(url.pathname)", worker)
            self.assertNotIn("'/api/wa/media/'", worker)

            for url, size in (('/app/pwa-icon-192.png', 192), ('/app/pwa-icon-512.png', 512), ('/app/apple-touch-icon.png', 180)):
                response = await client.get(url)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.content_type, 'image/png')
                self.assertGreater(len(await response.read()), 1000)

    async def test_real_app_declares_own_manifest_and_pwa_registration_without_push_opt_in(self):
        html = (ROOT / 'docs' / 'tenant-app.html').read_text(encoding='utf-8')
        pwa_script = (ROOT / 'docs' / 'tenant-pwa.js').read_text(encoding='utf-8')
        legacy_manifest = json.loads((ROOT / 'docs' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertIn('href="/app/manifest.webmanifest"', html)
        self.assertIn('src="/assets/tenant-pwa.js"', html)
        self.assertEqual(legacy_manifest['start_url'], '/webapp')
        self.assertIn("register('/app/pwa-sw.js'", pwa_script)
        self.assertIn("scope: '/app'", pwa_script)
        self.assertNotIn('requestPermission', pwa_script)
        self.assertNotIn('pushManager', pwa_script)

