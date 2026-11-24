'use strict';

const RETIRED_CACHE_PREFIX = 'beintaskbot-v';
const RETIRED_CACHE_NAMES = new Set(['beintaskbot-media-v1']);

function retiredAppUrl() {
  const scope = new URL(self.registration.scope);
  // GitHub Pages hosts the archived static shell under a repository path;
  // sending that client to /app on github.io would lead to a 404.
  return scope.hostname.endsWith('.github.io')
    ? scope.href
    : new URL('/app', self.location.origin).href;
}

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));

self.addEventListener('activate', event => event.waitUntil((async () => {
  const keys = await caches.keys();
  await Promise.all(keys
    .filter(key => key.startsWith(RETIRED_CACHE_PREFIX) || RETIRED_CACHE_NAMES.has(key))
    .map(key => caches.delete(key)));

  await self.clients.claim();
  const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
  await Promise.all(windows
    .filter(client => {
      try {
        const url = new URL(client.url);
        return url.origin === self.location.origin && (url.pathname === '/webapp' || url.pathname.startsWith('/webapp/'));
      } catch (_) {
        return false;
      }
    })
    .map(client => client.navigate(retiredAppUrl()).catch(() => null)));

  await self.registration.unregister();
})()));

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const target = retiredAppUrl();
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    const existing = windows.find(client => {
      try { return new URL(client.url).origin === self.location.origin; }
      catch (_) { return false; }
    });
    if (existing) {
      await existing.navigate(target);
      await existing.focus();
      return;
    }
    await self.clients.openWindow(target);
  })());
});
