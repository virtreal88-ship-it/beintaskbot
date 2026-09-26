const CACHE_NAME = 'beintaskbot-v2026-09-25-123';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME && k !== MEDIA_CACHE_NAME).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

const MEDIA_CACHE_NAME = 'beintaskbot-media-v1';

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;

  // Cache media files (photos, audio notes, attachments)
  if (url.pathname === '/api/deal/file' || url.pathname.startsWith('/api/wa/media/')) {
    e.respondWith(
      caches.open(MEDIA_CACHE_NAME).then(async cache => {
        const cached = await cache.match(e.request);
        if (cached) return cached;
        try {
          const networkRes = await fetch(e.request);
          if (networkRes.ok && networkRes.status === 200) {
            cache.put(e.request, networkRes.clone());
          }
          return networkRes;
        } catch (_err) {
          return cached || new Response('', { status: 408 });
        }
      })
    );
    return;
  }

  if (url.pathname.startsWith('/api/')) return;
  const isAppShell = e.request.destination === 'document' || url.pathname.endsWith('.html') || url.pathname.endsWith('/');
  if (isAppShell) {
    e.respondWith(fetch(e.request, { cache: 'no-store' }).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(
    fetch(e.request).then(r => {
      if (r.ok && url.origin === self.location.origin) {
        const clone = r.clone();
        caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
      }
      return r;
    }).catch(() => caches.match(e.request))
  );
});

self.addEventListener('push', e => {
  let data = {title: 'Bein Systems', body: 'Yeni bildiriş', icon: 'icon-192.png'};
  try { data = e.data.json(); } catch(err) { data.body = e.data ? e.data.text() : 'Yeni bildiriş'; }
  const opts = {
    body: data.body || '',
    icon: data.icon || 'icon-192.png',
    badge: 'icon-192.png',
    data: data.url || '/',
    vibrate: data.urgent ? [200, 100, 200, 100, 200] : [200, 100, 200]
  };
  if (data.chat || data.lead_id) {
    opts.tag = 'chat-' + (data.lead_id || data.url || '');
    opts.renotify = true;
  }
  if (data.urgent) {
    opts.tag = 'tecili-' + Date.now();
    opts.renotify = true;
    opts.requireInteraction = true;
  }
  e.waitUntil(
    self.registration.showNotification(data.title || 'Bein Systems', opts).then(() => {
      return self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(cls => {
        cls.forEach(c => {
          if (data.urgent) c.postMessage({type: 'URGENT_ALARM'});
          if (data.chat || data.lead_id) c.postMessage({
            type: 'CHAT_INCOMING',
            leadId: data.lead_id || '',
            phone: data.phone || '',
            contactName: data.contact_name || '',
            preview: data.preview || '',
            waLine: data.wa_line || ''
          });
        });
      });
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const raw = e.notification.data;
  const target = raw && typeof raw === 'object' ? String(raw.url || '') : String(raw || '');
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
