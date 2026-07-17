const CACHE_NAME = 'beintask-v86';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  e.respondWith(
    fetch(e.request).then(r => {
      if(r.ok && e.request.method === 'GET') {
        const clone = r.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
      }
      return r;
    }).catch(() => caches.match(e.request))
  );
});

// Push notification handler
self.addEventListener('push', e => {
  let data = {title: 'Bein Systems', body: 'Yeni bildiriş', icon: '/docs/icon-192.png'};
  try { data = e.data.json(); } catch(err) { data.body = e.data ? e.data.text() : 'Yeni bildiriş'; }
  e.waitUntil(
    self.registration.showNotification(data.title || 'Bein Systems', {
      body: data.body || '',
      icon: data.icon || '/docs/icon-192.png',
      badge: '/docs/icon-192.png',
      data: data.url || '/',
      vibrate: [200, 100, 200]
    })
  );
});

// Click on notification -> open app
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const target = String(e.notification.data || '');
  const pwaBaseUrl = 'https://virtreal88-ship-it.github.io/beintaskbot/';
  const hashIndex = target.indexOf('#');
  const hash = hashIndex >= 0 ? target.slice(hashIndex) : '';
  const destination = hash ? pwaBaseUrl + hash : (target || pwaBaseUrl);
  e.waitUntil(
    clients.matchAll({type: 'window'}).then(list => {
      for(const client of list) {
        if(client.url.includes('beintaskbot') && 'focus' in client) {
          if(hash && client.url !== destination && 'navigate' in client) {
            return client.navigate(destination).then(() => client.focus());
          }
          return client.focus();
        }
      }
      return clients.openWindow(destination);
    })
  );
});
