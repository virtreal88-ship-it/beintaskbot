/* SaaS push only: no caching, no legacy handlers, no private task text. */
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
  let hot=false;try{hot=event.data?.json()?.kind==='hot_order';}catch{}
  event.waitUntil(self.registration.showNotification('CRM Smart Assistant',{
    body:hot ? 'Yeni isti sifariş var. Kabinetdə yoxlayın.' : 'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',
    tag:hot ? 'crm-hot-order' : 'crm-task-approval',data:{url:hot ? '/app?view=hot_orders' : '/app'}
  }));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();event.waitUntil(self.clients.openWindow(event.notification.data?.url==='/app?view=hot_orders' ? '/app?view=hot_orders' : '/app'));
});
