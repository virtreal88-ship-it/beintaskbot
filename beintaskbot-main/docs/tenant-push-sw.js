/* SaaS push only: no caching, no legacy handlers, no private task text. */
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
  let kind='';try{kind=event.data?.json()?.kind || '';}catch{}
  if(kind==='crm_notice'){
    let eventKey='';try{eventKey=event.data.json().event || '';}catch{}
    const task=['task_assigned','task_overdue'].includes(eventKey);
    event.waitUntil(self.registration.showNotification('CRM Smart Assistant',{
      body:task?'Tapşırıq üzrə yenilik var. Kabinetdə yoxlayın.':'Yeni mesaj və ya sövdələşmə var. Kabinetdə yoxlayın.',
      tag:'crm-notice',data:{url:task?'/app?view=tasks':'/app?view=customers'}
    }));return;
  }
  const linear=kind==='linear',hot=kind==='hot_order',review=['hot_order_review','hot_order_decided'].includes(kind);
  event.waitUntil(self.registration.showNotification('CRM Smart Assistant',{
    body:linear ? 'Linear tapşırığının statusu dəyişib. Kabinetdə yoxlayın.' : review ? 'İsti sifariş üzrə yenilik var. Kabinetdə yoxlayın.' : hot ? 'Yeni isti sifariş var. Kabinetdə yoxlayın.' : 'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',
    tag:linear ? 'crm-linear' : hot||review ? 'crm-hot-order' : 'crm-task-approval',data:{url:linear ? '/app?view=linear' : kind==='hot_order_review' ? '/app?view=approvals' : hot||review ? '/app?view=hot_orders' : '/app'}
  }));
});
self.addEventListener('notificationclick',event=>{
  const url=event.notification.data?.url;
  event.notification.close();event.waitUntil(self.clients.openWindow(['/app?view=tasks','/app?view=customers','/app?view=hot_orders','/app?view=approvals','/app?view=linear'].includes(url) ? url : '/app'));
});
