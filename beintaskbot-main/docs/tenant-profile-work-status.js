/* Manual self-status in the shared account menu. Never infers activity. */
(()=>{
  'use strict';
  const copy={
    ru:{title:'Рабочий статус',online:'В сети',away:'Отошёл',lunch:'Обед',offline:'Не в сети',saved:'Статус сохранён.',error:'Не удалось сохранить статус.',confirm:'Подтвердите статус «В сети», чтобы получать новые диалоги.'},
    az:{title:'İş statusu',online:'Onlayn',away:'Kənarda',lunch:'Nahar',offline:'Oflayn',saved:'Status saxlanıldı.',error:'Statusu saxlamaq mümkün olmadı.',confirm:'Yeni dialoqları almaq üçün «Onlayn» statusunu təsdiqləyin.'},
    en:{title:'Work status',online:'Online',away:'Away',lunch:'Lunch',offline:'Offline',saved:'Status saved.',error:'Could not save status.',confirm:'Confirm Online to receive new dialogues.'}
  };
  const states=['online','away','lunch','offline'];
  const label=(key,locale=window.tenantI18n?.language||document.documentElement.lang)=>Object.hasOwn(copy.en,key)?(copy[locale]||copy.en)[key]:'';
  window.tenantWorkStatus=Object.freeze({label});
  const urls={profile:'/api/platform/ai/profile/availability',dialogs:'/api/platform/ai/dialogs/presence'};
  const scope=member=>member?.active===true&&member.tenant_id&&member.telegram_id?
    {expected_tenant_id:String(member.tenant_id),expected_user_id:Number(member.telegram_id)}:null;
  const key=identity=>identity?JSON.stringify(identity):'';
  const validStatus=(route,result)=>result?.available===true&&states.includes(result.status)&&
    (route==='profile'?result.can_set_offline===true:result.status!=='offline'||result.can_set_offline===true);
  const mount=()=>{
    const menu=document.getElementById('appUserMenu'),name=document.getElementById('appUserName');
    const avatar=document.querySelector('.app-avatar-button');
    if(!menu||!name||!avatar||document.getElementById('appWorkStatus'))return;
    const header=name.parentElement;
    const control=document.createElement('div');control.id='appWorkStatus';control.hidden=true;
    control.innerHTML='<button type="button" id="appWorkStatusTrigger" aria-expanded="false" aria-controls="appWorkStatusOptions"><span class="app-work-status-dot" aria-hidden="true"></span><span></span><small></small><svg aria-hidden="true" viewBox="0 0 16 16" fill="none"><path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button><div id="appWorkStatusOptions" role="group" hidden><p id="appWorkStatusConfirm" hidden></p><div id="appWorkStatusChoices"></div></div><p id="appWorkStatusFeedback" role="status" aria-live="polite"></p>';
    header.after(control);
    const trigger=control.querySelector('button'), triggerDot=trigger.firstElementChild, triggerLabel=triggerDot.nextElementSibling,
      triggerValue=trigger.querySelector('small'), options=control.querySelector('#appWorkStatusOptions'),
      choices=control.querySelector('#appWorkStatusChoices'),hint=control.querySelector('#appWorkStatusConfirm'),feedback=control.querySelector('#appWorkStatusFeedback');
    let identity=scope(window.tenantProfileSnapshot?.member), epoch=0, confirmed=null, route=null, pending=false;
    const current=(stamp,expected)=>epoch===stamp&&key(identity)===key(expected);
    const legacyConfirmation=()=>route==='dialogs'&&confirmed?.routing_enforced===true&&confirmed.configured!==true&&confirmed.status==='online'&&confirmed.can_set_offline!==true;
    const selectOptions=()=>states.filter(state=>state!=='offline'||confirmed?.can_set_offline===true);
    const toggle=(open,restore=false)=>{options.hidden=!open;trigger.setAttribute('aria-expanded',String(open));if(restore&&!menu.hidden)trigger.focus();};
    const paint=()=>{
      control.hidden=!confirmed;
      if(!confirmed){delete avatar.dataset.workStatus;return;}
      avatar.dataset.workStatus=triggerDot.dataset.status=confirmed.status;
      triggerLabel.textContent=label('title');triggerValue.textContent=label(confirmed.status);trigger.disabled=pending;
      options.setAttribute('aria-label',label('title'));
      hint.textContent=legacyConfirmation()?label('confirm'):'';hint.hidden=!legacyConfirmation();
      choices.replaceChildren(...selectOptions().map(status=>{
        const button=document.createElement('button');button.type='button';button.dataset.status=status;
        button.setAttribute('aria-pressed',String(status===confirmed.status));button.disabled=pending;
        const mark=document.createElement('span');mark.className='app-work-status-dot';mark.dataset.status=status;mark.setAttribute('aria-hidden','true');
        button.append(mark,document.createTextNode(label(status)));button.addEventListener('click',()=>void save(status));
        return button;
      }));
    };
    const clear=()=>{epoch++;confirmed=route=null;pending=false;feedback.textContent='';toggle(false);paint();};
    const load=async()=>{
      clear();if(!identity)return;
      const stamp=epoch, expected=identity, query='?'+new URLSearchParams(expected);
      try{
        for(const source of ['profile','dialogs']){
          const response=await fetch(urls[source]+query,{credentials:'same-origin',cache:'no-store'});
          const data=await response.json();
          if(!current(stamp,expected)||!response.ok||data.success!==true)return;
          if(source==='profile'&&data.result?.available===false)continue;
          if(!validStatus(source,data.result)||data.result.can_update!==true)return;
          confirmed=data.result;route=source;paint();return;
        }
      }catch{}
    };
    const save=async status=>{
      if(!identity||!confirmed||!route||pending||!selectOptions().includes(status))return;
      if(status===confirmed.status&&!legacyConfirmation()){toggle(false,true);return;}
      const stamp=epoch, expected=identity, source=route;
      pending=true;feedback.textContent='';paint();
      try{
        const response=await fetch(urls[source],{method:'POST',credentials:'same-origin',cache:'no-store',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({...expected,status})});
        const data=await response.json();
        if(!current(stamp,expected))return;
        if([403,409].includes(response.status)){void load();return;}
        if(!response.ok||data.success!==true||!validStatus(source,data.result)||data.result.status!==status)throw Error('save');
        confirmed=data.result;feedback.textContent=label('saved');pending=false;paint();toggle(false,true);
        document.dispatchEvent(new CustomEvent('tenant-employee-status-changed',{detail:{source:'profile'}}));
      }catch{
        if(current(stamp,expected))feedback.textContent=label('error');
      }finally{
        if(current(stamp,expected)&&pending){pending=false;paint();if(!options.hidden&&!menu.hidden)choices.querySelector('[data-status="'+status+'"]')?.focus();}
      }
    };
    trigger.addEventListener('click',()=>{
      const open=options.hidden;toggle(open);if(open)(choices.querySelector('[aria-pressed="true"]')||choices.firstElementChild)?.focus();
    });
    control.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&!options.hidden){event.preventDefault();event.stopPropagation();toggle(false,true);}
    });
    menu.addEventListener('click',event=>{if(!control.contains(event.target))toggle(false);});
    new MutationObserver(()=>{
      if(menu.hidden)toggle(false);else if(!confirmed&&!pending)void load();
    }).observe(menu,{attributes:true,attributeFilter:['hidden']});
    document.addEventListener('tenant-profile',event=>{
      const next=scope(event.detail?.member);if(key(next)===key(identity))return;identity=next;void load();
    });
    for(const event of ['tenant-auth-invalid','tenant-auth-pending'])document.addEventListener(event,()=>{identity=null;clear();});
    document.addEventListener('tenant-language',()=>{paint();feedback.textContent='';});
    void load();
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
