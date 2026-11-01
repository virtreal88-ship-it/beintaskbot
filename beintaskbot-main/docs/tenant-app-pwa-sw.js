/* /app install shell support only. Never cache app documents, APIs, media, or credentials. */
'use strict';

const APP_CACHE_PREFIX = 'beintaskbot-app-pwa-';
// Bump the cache suffix when changing the cacheable asset policy or required assets.
const APP_CACHE_NAME = 'beintaskbot-app-pwa-v1';
const SAFE_APP_ASSET = /^\/assets\/[A-Za-z0-9._/-]+$/;
const SAFE_APP_ICONS = new Set(['/app/pwa-icon-192.png', '/app/pwa-icon-512.png']);
const CACHEABLE_TYPES = /^(?:application\/javascript|text\/javascript|text\/css|image\/(?:png|svg\+xml)|font\/woff2?)(?:\s*;|$)/i;

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));

self.addEventListener('activate', event => event.waitUntil((async () => {
  const keys = await caches.keys();
  await Promise.all(keys
    .filter(key => key.startsWith(APP_CACHE_PREFIX) && key !== APP_CACHE_NAME)
    .map(key => caches.delete(key)));
  await self.clients.claim();
})()));

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Authentication, CRM data, attachment/media endpoints and all documents always go to the network.
  if (url.pathname.startsWith('/api/') || request.destination === 'document') return;
  if (url.search || request.headers.has('authorization')) return;
  if (!['script', 'style', 'font', 'image'].includes(request.destination)) return;
  if (!SAFE_APP_ASSET.test(url.pathname) && !SAFE_APP_ICONS.has(url.pathname)) return;

  event.respondWith((async () => {
    const cache = await caches.open(APP_CACHE_NAME);
    try {
      // Public, versioned app assets do not need session cookies. Cache API storage is
      // intentionally limited to these static paths; it is never an offline CRM store.
      const networkRequest = new Request(request, {cache: 'no-store', credentials: 'omit'});
      const response = await fetch(networkRequest);
      const contentType = response.headers.get('content-type') || '';
      if (response.ok && response.type === 'basic' && CACHEABLE_TYPES.test(contentType)) {
        try { await cache.put(request, response.clone()); } catch (_) { /* keep network response usable */ }
      }
      return response;
    } catch (_) {
      return await cache.match(request) || Response.error();
    }
  })());
});
