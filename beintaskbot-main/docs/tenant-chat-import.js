/* Explicit page-by-page import, separate from cache browsing and sending. */
(() => {
  const $=id=>document.getElementById(id),identity=m=>m?.tenant_id+':'+m?.telegram_id;
  let member,lead,epoch=0,profileEpoch=0,cursor='',done=false,detail,busy=false,notice='';
  const clear=()=>{$('chatImportPages')?.remove();};
  function render(){
    clear();const box=$('chatRows');
    if(!member||!lead||!box||typeof detail?.reload_cache!=='function')return;
    const current=epoch,who=identity(member),selected=lead;
    const host=document.createElement('div');host.id='chatImportPages';host.style='margin-bottom:10px';
    const button=document.createElement('button');button.type='button';button.className='outline';
    button.textContent=cursor?'Növbəti Kommo səhifəsi':'Kommo tarixçəsini mərhələli yüklə';button.disabled=busy||done;
    const status=document.createElement('span');status.setAttribute('role','status');status.style='margin-left:8px';status.textContent=notice;
    host.append(button,status);box.before(host);
    const valid=()=>current===epoch&&who===identity(member)&&selected===lead&&box===$('chatRows')&&host.isConnected;
    button.onclick=async()=>{
      if(busy||done||!valid())return;
      busy=true;button.disabled=true;button.textContent='Yüklənir…';button.setAttribute('aria-busy','true');
      const reload=detail.reload_cache;
      try{
        const response=await fetch('/api/platform/crm/chat/import-history',{method:'POST',credentials:'same-origin',cache:'no-store',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({lead_id:Number(lead),cursor,
            expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id})});
        const result=await response.json();if(!valid())return;
        if(!response.ok||!result.success){const error=new Error(result.error||'Tarixçə yüklənmədi.');error.status=response.status;throw error;}
        cursor=result.next_cursor||'';done=!result.has_more;
        notice=done?'Kommo tarixçəsinin idxalı tamamlandı.':'Səhifə saxlanıldı. Daha köhnə mesajlar üçün «Daha çox» düyməsini istifadə edin.';
        busy=false;await reload();
      }catch(error){if(valid()){notice=error.message;if([403,409].includes(error.status)){cursor='';done=error.status===403;}}}
      finally{if(current===epoch){busy=false;render();}}
    };
  }
  document.addEventListener('tenant-profile',event=>{
    profileEpoch++;
    if(identity(member)!==identity(event.detail.member)){epoch++;cursor='';done=false;busy=false;detail=null;notice='';clear();}
    member=event.detail.member;
  });
  document.addEventListener('tenant-chat-open',event=>{epoch++;lead=event.detail.lead_id;cursor='';done=false;busy=false;detail=null;notice='';clear();});
  document.addEventListener('tenant-chat-loaded',event=>{if(String(event.detail.lead_id)===String(lead)){detail=event.detail;render();}});
  const initial=profileEpoch;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{
    if(initial===profileEpoch&&data?.member){member=data.member;render();}
  }).catch(()=>{});
})();
