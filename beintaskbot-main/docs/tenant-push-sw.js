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
function dialogNoticeTag(target){
  try{
    const url=new URL(target||'/app',self.location.origin),seed=['channel_id','lead_id','conversation_id'].map(key=>url.searchParams.get(key)||'').join('|');
    let hash=2166136261;for(let i=0;i<seed.length;i++){hash^=seed.charCodeAt(i);hash=Math.imul(hash,16777619);}
    return 'crm-dialog-'+(hash>>>0).toString(16);
  }catch{return 'crm-notice';}
}
function safeMessageText(value,limit){
  if(typeof value!=='string')return '';
  return value.replace(/[\u0000-\u001f\u007f]+/g,' ').replace(/\S+@(?:g\.us|s\.whatsapp\.net|c\.us|lid)\b/ig,'').replace(/\s+/g,' ').trim().slice(0,limit);
}
self.addEventListener('push',event=>{
  let kind='';try{kind=event.data?.json()?.kind || '';}catch{}
  if(kind==='crm_notice'){
    let eventKey='',target='/app',title='CRM Smart Assistant',body='';try{const payload=event.data.json();eventKey=payload.event||'';target=allowedTarget(payload.url);if(eventKey==='incoming_message'){title=safeMessageText(payload.title,120)||title;body=safeMessageText(payload.body,180);}}catch{}
    const task=['task_assigned','task_overdue'].includes(eventKey);
    event.waitUntil(self.registration.showNotification(title,{
      body:task?'Tapşırıq üzrə yenilik var. Kabinetdə yoxlayın.':eventKey==='incoming_message'?(body||'Yeni mesaj var. Kabinetdə yoxlayın.'):'Yeni mesaj və ya sövdələşmə var. Kabinetdə yoxlayın.',
      tag:task?'crm-task-notice':eventKey==='incoming_message'?dialogNoticeTag(target):'crm-notice',data:{url:task?'/app?view=tasks':target}
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
