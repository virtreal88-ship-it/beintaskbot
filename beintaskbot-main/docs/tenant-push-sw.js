/* SaaS push only: no caching, no legacy handlers, no private task text. */
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>event.waitUntil(self.registration.showNotification('CRM Smart Assistant',{
  body:'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',tag:'crm-task-approval',data:{url:'/app'}
})));
self.addEventListener('notificationclick',event=>{
  event.notification.close();event.waitUntil(self.clients.openWindow('/app'));
});
