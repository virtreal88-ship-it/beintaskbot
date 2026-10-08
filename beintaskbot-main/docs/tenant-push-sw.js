/* SaaS push only: no caching, no legacy handlers, no private task text. */
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
  let kind='';try{kind=event.data?.json()?.kind || '';}catch{}
  const hot=kind==='hot_order',review=['hot_order_review','hot_order_decided'].includes(kind);
  event.waitUntil(self.registration.showNotification('CRM Smart Assistant',{
    body:review ? 'İsti sifariş üzrə yenilik var. Kabinetdə yoxlayın.' : hot ? 'Yeni isti sifariş var. Kabinetdə yoxlayın.' : 'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',
    tag:hot||review ? 'crm-hot-order' : 'crm-task-approval',data:{url:kind==='hot_order_review' ? '/app?view=approvals' : hot||review ? '/app?view=hot_orders' : '/app'}
  }));
});
self.addEventListener('notificationclick',event=>{
  const url=event.notification.data?.url;
  event.notification.close();event.waitUntil(self.clients.openWindow(['/app?view=hot_orders','/app?view=approvals'].includes(url) ? url : '/app'));
});
