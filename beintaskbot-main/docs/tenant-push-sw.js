/* SaaS push only: no caching, no legacy handlers, no private task text. */
self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
function allowedTarget(value){
  try{
    const url=new URL(value||'/app',self.location.origin);
    if(url.origin!==self.location.origin||url.pathname!=='/app')return '/app';
    const view=url.searchParams.get('view')||'';
    if(['tasks','customers','hot_orders','approvals','linear'].includes(view))return url.pathname+url.search;
    const channel=url.searchParams.get('channel_id')||'',lead=url.searchParams.get('lead_id')||'',conversation=url.searchParams.get('conversation_id')||'';
    if(view==='dialogs'&&url.searchParams.get('open_dialog')==='1'&&url.searchParams.get('provider')==='connect'
      &&/^[0-9a-f-]{36}$/i.test(channel)&&/^\d{1,20}$/.test(lead)&&conversation.length>0&&conversation.length<=220&&!/[\u0000-\u001f\u007f]/.test(conversation))return url.pathname+url.search;
  }catch{}
  return '/app';
}
self.addEventListener('push',event=>{
  let kind='';try{kind=event.data?.json()?.kind || '';}catch{}
  if(kind==='crm_notice'){
    let eventKey='',target='/app';try{const payload=event.data.json();eventKey=payload.event||'';target=allowedTarget(payload.url);}catch{}
    const task=['task_assigned','task_overdue'].includes(eventKey);
    event.waitUntil(self.registration.showNotification('CRM Smart Assistant',{
      body:task?'Tapşırıq üzrə yenilik var. Kabinetdə yoxlayın.':eventKey==='incoming_message'?'Yeni mesaj var. Kabinetdə yoxlayın.':'Yeni mesaj və ya sövdələşmə var. Kabinetdə yoxlayın.',
      tag:task?'crm-task-notice':'crm-notice',data:{url:task?'/app?view=tasks':target}
    }));return;
  }
  const linear=kind==='linear',hot=kind==='hot_order',review=['hot_order_review','hot_order_decided'].includes(kind);
  event.waitUntil(self.registration.showNotification('CRM Smart Assistant',{
    body:linear ? 'Linear tapşırığının statusu dəyişib. Kabinetdə yoxlayın.' : review ? 'İsti sifariş üzrə yenilik var. Kabinetdə yoxlayın.' : hot ? 'Yeni isti sifariş var. Kabinetdə yoxlayın.' : 'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',
    tag:linear ? 'crm-linear' : hot||review ? 'crm-hot-order' : 'crm-task-approval',data:{url:linear ? '/app?view=linear' : kind==='hot_order_review' ? '/app?view=approvals' : hot||review ? '/app?view=hot_orders' : '/app'}
  }));
});
self.addEventListener('notificationclick',event=>{
  const url=allowedTarget(event.notification.data?.url);
  event.notification.close();event.waitUntil(self.clients.openWindow(url));
});
