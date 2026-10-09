/* Older cached pages only. Never syncs all Kommo history or invokes send/AI. */
(() => {
  const $=id=>document.getElementById(id);
  let member, lead, epoch=0, detail;
  const identity=m=>m?.tenant_id+':'+m?.telegram_id;
  const clear=()=>{$('chatHistoryPages')?.remove();};
  function render() {
    clear();
    const box=$('chatRows');
    if(!member||!detail?.next_cursor||!box||typeof detail.render_message!=='function')return;
    const current=epoch, who=identity(member), selected=lead;
    let cursor=detail.next_cursor;
    const seen=new Set((detail.messages||[]).map(row=>String(row.external_id)));
    const host=document.createElement('div');host.id='chatHistoryPages';host.style='margin-bottom:10px';
    const button=document.createElement('button');button.type='button';button.className='outline';button.textContent='Daha çox';
    const notice=document.createElement('span');notice.setAttribute('role','status');notice.style='margin-left:8px';
    host.append(button,notice);box.before(host);
    const valid=()=>current===epoch&&who===identity(member)&&selected===lead&&box===$('chatRows')&&host.isConnected;
    const line=detail.render_message;
    button.onclick=async()=>{
      if(button.disabled||!cursor||!valid())return;
      button.disabled=true;button.textContent='Yüklənir…';button.setAttribute('aria-busy','true');notice.textContent='';
      const scroller=$('dealModal')?.querySelector('.modal-card');
      try{
        const query=new URLSearchParams({lead_id:selected,cursor,expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
        const response=await fetch('/api/platform/crm/chat/history?'+query,{credentials:'same-origin',cache:'no-store'});
        const result=await response.json();if(!valid())return;
        if(!response.ok||!result.success){const error=new Error(result.error||'Tarixçə yüklənmədi.');error.status=response.status;throw error;}
        const fresh=(result.messages||[]).filter(row=>!seen.has(String(row.external_id)));
        const oldHeight=scroller?.scrollHeight,oldTop=scroller?.scrollTop;
        box.insertAdjacentHTML('afterbegin',fresh.map(line).join(''));
        fresh.forEach(row=>seen.add(String(row.external_id)));
        cursor=result.next_cursor;
        if(scroller)scroller.scrollTop=oldTop+scroller.scrollHeight-oldHeight;
        if(!cursor){button.hidden=true;notice.textContent='Saxlanmış tarixçənin əvvəlinə çatdınız.';}
        document.dispatchEvent(new CustomEvent('tenant-chat-history-added',{detail:{lead_id:selected}}));
      }catch(error){if(valid()){if([403,409].includes(error.status)){box.textContent=error.message;host.remove();cursor=null;}else notice.textContent=error.message;}}
      finally{button.disabled=false;button.textContent='Daha çox';button.setAttribute('aria-busy','false');}
    };
  }
  document.addEventListener('tenant-profile',event=>{
    if(identity(member)!==identity(event.detail.member)){epoch++;detail=null;clear();}
    member=event.detail.member;
  });
  document.addEventListener('tenant-chat-open',event=>{epoch++;lead=event.detail.lead_id;detail=null;clear();});
  document.addEventListener('tenant-chat-history-reset',event=>{if(String(event.detail.lead_id)===String(lead)){epoch++;detail=null;clear();}});
  document.addEventListener('tenant-chat-loaded',event=>{
    if(String(event.detail.lead_id)!==String(lead))return;
    epoch++;detail=event.detail;render();
  });
  const initial=epoch;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.ok?r.json():null).then(data=>{
    if((initial===epoch||!member)&&data?.member){member=data.member;render();}
  }).catch(()=>{});
})();
