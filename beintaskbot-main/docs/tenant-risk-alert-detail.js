/* A notification opens one freshly revalidated signal; this is not a findings list. */
(()=>{
  'use strict';
  const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  let profile=window.tenantProfileSnapshot||null,identity='',epoch=0,overlay=null;
  const memberOf=data=>data?.member||null;
  const scopeOf=data=>{const m=memberOf(data);return m?`${m.tenant_id||''}:${m.telegram_id||''}:${m.role||''}:${m.active===true}`:'';};
  function close(removeParam=false){epoch++;overlay?.remove();overlay=null;if(removeParam){const url=new URL(location.href);url.searchParams.delete('risk_alert_id');history.replaceState(history.state,'',url.pathname+url.search+url.hash);}}
  function addText(parent,tag,value,cls){const node=document.createElement(tag);node.textContent=String(value??'');if(cls)node.className=cls;parent.append(node);return node;}
  function showAlert(alert){
    overlay?.remove();const shade=document.createElement('div');shade.className='tenant-risk-alert-shade';shade.setAttribute('role','presentation');shade.style.cssText='position:fixed;inset:0;z-index:10050;background:#0008;display:grid;place-items:center;padding:16px';
    const dialog=document.createElement('section');dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-labelledby','riskAlertTitle');dialog.style.cssText='width:min(620px,100%);max-height:85vh;overflow:auto;background:var(--chakra-colors-white,#fff);color:var(--chakra-colors-gray-800,#222);border-radius:14px;padding:22px;box-shadow:0 16px 60px #0004';
    const head=document.createElement('header');head.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:12px';
    const title=addText(head,'h2',alert.source_title||'Уведомление о риске');title.id='riskAlertTitle';title.style.margin='0';
    const closeButton=addText(head,'button','Закрыть');closeButton.type='button';closeButton.onclick=()=>close(true);head.append(closeButton);dialog.append(head);
    addText(dialog,'p',`${alert.source||'Источник'} · ${alert.severity||'сигнал'} · уверенность: ${alert.confidence||'не указана'}`);
    addText(dialog,'h3','Что обнаружено');const facts=Array.isArray(alert.facts)?alert.facts:[];const list=document.createElement('ul');facts.forEach(f=>addText(list,'li',typeof f==='string'?f:(f?.text||JSON.stringify(f))));dialog.append(list);
    addText(dialog,'h3','Возможный смысл');addText(dialog,'p',alert.assessment||'');
    addText(dialog,'h3','Предлагаемый шаг');addText(dialog,'p',alert.recommended_step||'');
    addText(dialog,'small',`Проверено: ${alert.checked_at||'только что'}`);
    if(typeof alert.source_url==='string'&&alert.source_url.startsWith('https://linear.app/')){const a=addText(dialog,'a','Открыть задачу Linear');a.href=alert.source_url;a.target='_blank';a.rel='noopener noreferrer';a.style.display='block';}
    shade.append(dialog);shade.addEventListener('click',event=>{if(event.target===shade)close(true);});shade.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close(true);}});document.body.append(shade);overlay=shade;closeButton.focus();
  }
  function showMessage(message){showAlert({source_title:'Уведомление о риске',source:'',severity:'',confidence:'',facts:[message],assessment:'',recommended_step:'',checked_at:''});}
  async function route(){
    const id=new URL(location.href).searchParams.get('risk_alert_id');if(!id){if(overlay)close(false);return;}
    const token=++epoch,owner=memberOf(profile),scope=scopeOf(profile);if(!uuid.test(id)||owner?.role!=='owner'||owner.active!==true){close(false);return;}
    if(overlay)overlay.remove();const wait=document.createElement('div');wait.className='tenant-risk-alert-shade';wait.style.cssText='position:fixed;inset:0;z-index:10050;background:#0008;display:grid;place-items:center;padding:16px';const box=addText(wait,'p','Уведомление проверяется…');box.style.cssText='background:white;padding:24px;border-radius:12px';document.body.append(wait);overlay=wait;
    try{const response=await fetch(`/api/platform/risks/alerts/${encodeURIComponent(id)}`,{credentials:'same-origin',headers:{'Accept':'application/json','X-Tenant-Id':String(owner.tenant_id||''),'X-User-Id':String(owner.telegram_id||'')}});const data=await response.json();if(token!==epoch||scope!==scopeOf(profile)||!overlay?.isConnected)return;if(!response.ok||!data.success||!data.alert){showMessage(response.status===404?'Этот сигнал уже недоступен или больше не подтверждается источником.':'Не удалось проверить источник. Попробуйте открыть уведомление позже.');return;}showAlert(data.alert);
    }catch{if(token===epoch&&scope===scopeOf(profile)&&overlay?.isConnected)showMessage('Не удалось проверить источник. Попробуйте открыть уведомление позже.');}
  }
  function apply(data){const next=scopeOf(data);if(identity&&identity!==next)close(true);profile=data;identity=next;if(memberOf(data)?.role!=='owner'||memberOf(data)?.active!==true)close(false);else void route();}
  document.addEventListener('tenant-profile',event=>apply(event.detail));
  document.addEventListener('tenant-view',event=>{if(event.detail?.view!=='linear'&&overlay)close(true);});
  for(const type of ['tenant-auth-invalid','tenant-auth-pending'])document.addEventListener(type,()=>{profile=null;identity='';close(false);});
  window.addEventListener('popstate',()=>void route());
  if(profile)apply(profile);else if(new URL(location.href).searchParams.has('risk_alert_id'))document.addEventListener('tenant-profile',event=>apply(event.detail),{once:true});
})();
