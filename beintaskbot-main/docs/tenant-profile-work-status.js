/* Manual self-status in the shared /app profile; never infers activity. */
(()=>{
  'use strict';
  const copy={
    ru:{title:'Рабочий статус',online:'В сети',away:'Отошёл',lunch:'Обед',offline:'Не в сети',save:'Сохранить',saved:'Статус сохранён.',error:'Не удалось сохранить статус.'},
    az:{title:'İş statusu',online:'Onlayn',away:'Kənarda',lunch:'Nahar',offline:'Oflayn',save:'Yadda saxla',saved:'Status saxlanıldı.',error:'Statusu saxlamaq mümkün olmadı.'},
    en:{title:'Work status',online:'Online',away:'Away',lunch:'Lunch',offline:'Offline',save:'Save',saved:'Status saved.',error:'Could not save status.'}
  };
  const states=['online','away','lunch','offline'], language=()=>window.tenantI18n?.language||document.documentElement.lang;
  const label=(key,locale=language())=>Object.hasOwn(copy.en,key)?(copy[locale]||copy.en)[key]:'';
  window.tenantWorkStatus=Object.freeze({label});
  const form=document.getElementById('profileWorkStatusForm');
  if(!form)return;
  const view=document.getElementById('view-profile'), title=document.getElementById('profileWorkStatusTitle'),
    picker=document.getElementById('profileWorkStatusSelect'), button=document.getElementById('profileWorkStatusSave'),
    feedback=document.getElementById('profileWorkStatusFeedback');
  const url='/api/platform/ai/profile/availability';
  const scope=member=>{
    if(!member||member.active!==true||!member.tenant_id||!member.telegram_id)return null;
    return {expected_tenant_id:String(member.tenant_id),expected_user_id:Number(member.telegram_id)};
  };
  const key=identity=>identity?JSON.stringify(identity):'';
  const paint=()=>{
    title.textContent=label('title');button.textContent=label('save');
    const value=picker.value;
    picker.replaceChildren(...states.map(status=>new Option(label(status),status)));
    picker.value=value;
  };
  let identity=scope(window.tenantProfileSnapshot?.member), epoch=0, pending=false, confirmed=null;
  const visible=()=>view?.classList.contains('active');
  const current=(stamp,expected)=>epoch===stamp&&key(identity)===key(expected)&&visible();
  const clear=()=>{
    epoch++;pending=false;confirmed=null;form.hidden=true;feedback.textContent='';
    button.disabled=true;picker.disabled=false;
  };
  const valid=result=>result?.available===true&&result.can_set_offline===true&&states.includes(result.status);
  const load=async()=>{
    clear();
    if(!identity||!visible())return;
    const stamp=epoch, expected=identity;
    try{
      const response=await fetch(url+'?'+new URLSearchParams(expected),{credentials:'same-origin',cache:'no-store'});
      const value=await response.json();
      if(!current(stamp,expected)||!response.ok||!value.success||!valid(value.result)||value.result.can_update!==true)return;
      confirmed=value.result.status;picker.value=confirmed;form.hidden=false;
    }catch{}
  };
  picker.onchange=()=>{button.disabled=pending||!confirmed||!states.includes(picker.value)||picker.value===confirmed;feedback.textContent='';};
  form.onsubmit=async event=>{
    event.preventDefault();
    if(!identity||!visible()||form.hidden||pending||!confirmed||!states.includes(picker.value)||picker.value===confirmed)return;
    const stamp=epoch, expected=identity, selected=picker.value;
    pending=true;picker.disabled=true;button.disabled=true;feedback.textContent='';
    try{
      const response=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({...expected,status:selected})});
      const value=await response.json();
      if(!current(stamp,expected))return;
      if([403,409].includes(response.status)){void load();return;}
      if(!response.ok||!value.success||!valid(value.result)||value.result.status!==selected)throw Error('save');
      confirmed=selected;picker.value=confirmed;feedback.textContent=label('saved');
      document.dispatchEvent(new CustomEvent('tenant-employee-status-changed',{detail:{source:'profile'}}));
    }catch{
      if(current(stamp,expected))feedback.textContent=label('error');
    }finally{
      if(current(stamp,expected)){pending=false;picker.disabled=false;button.disabled=picker.value===confirmed;}
    }
  };
  document.addEventListener('tenant-profile',event=>{
    const next=scope(event.detail?.member);
    if(key(next)===key(identity))return;
    identity=next;void load();
  });
  for(const event of ['tenant-auth-invalid','tenant-auth-pending'])document.addEventListener(event,()=>{identity=null;clear();});
  document.addEventListener('tenant-view',event=>{if(event.detail?.view==='profile')void load();else clear();});
  document.addEventListener('tenant-language',()=>{paint();feedback.textContent='';});
  paint();void load();
})();
