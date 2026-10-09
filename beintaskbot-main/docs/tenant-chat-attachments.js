/* Explicit bounded downloads. No remote URLs or credentials are put in the DOM. */
(() => {
  let member, lead, epoch=0;
  const active=new Set(), urls=new Set();
  const identity=m=>m?.tenant_id+':'+m?.telegram_id;
  function clear(){
    epoch++;
    active.forEach(controller=>controller.abort());active.clear();
    urls.forEach(url=>URL.revokeObjectURL(url));urls.clear();
    document.querySelectorAll('[data-chat-attachment]').forEach(host=>{
      host.querySelectorAll('img,audio,video,a').forEach(node=>node.remove());
      const button=host.querySelector('button');if(button){button.hidden=false;button.disabled=false;}
    });
  }
  function bind(){
    document.querySelectorAll('#chatRows [data-chat-attachment]').forEach(host=>{
      const button=host.querySelector('button'),status=host.querySelector('[role="status"]');
      if(!button)return;
      button.onclick=async()=>{
        if(button.disabled||!member||!lead)return;
        const current=epoch,who=identity(member),selected=lead,controller=new AbortController();
        active.add(controller);button.disabled=true;button.setAttribute('aria-busy','true');status.textContent=' Yüklənir…';
        const valid=()=>current===epoch&&who===identity(member)&&selected===lead&&host.isConnected;
        const timer=setTimeout(()=>controller.abort(),70000);
        let blobUrl;
        try{
          const query=new URLSearchParams({lead_id:selected,message_id:host.dataset.chatAttachment,
            expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
          const response=await fetch('/api/platform/crm/chat/attachment?'+query,
            {credentials:'same-origin',cache:'no-store',signal:controller.signal});
          if(!response.ok){const data=await response.json();throw Error(data.error||'Fayl yüklənmədi.');}
          const chunks=[],reader=response.body.getReader(),total=Number(response.headers.get('Content-Length'));
          let size=0;
          while(true){
            const {done,value}=await reader.read();if(done)break;
            if(!valid()){await reader.cancel();return;}
            size+=value.length;
            if(size>20*1024*1024){await reader.cancel();throw Error('Fayl çox böyükdür.');}
            chunks.push(value);status.textContent=total>0?' Yüklənir: '+Math.min(100,Math.round(size/total*100))+'%':' Yüklənir…';
          }
          const blob=new Blob(chunks,{type:response.headers.get('Content-Type')||'application/octet-stream'});if(!valid())return;
          if(!blob.size||blob.size>20*1024*1024)throw Error('Fayl ölçüsü düzgün deyil.');
          blobUrl=URL.createObjectURL(blob);urls.add(blobUrl);
          const type=blob.type.split(';')[0];let node;
          if(['image/jpeg','image/png','image/webp','image/gif'].includes(type)){
            node=document.createElement('img');node.alt='Çat şəkli';node.style='display:block;max-width:100%;max-height:420px';
          }else if(type.startsWith('audio/')||type==='application/ogg'){
            node=document.createElement('audio');node.controls=true;node.style='max-width:100%';
          }else if(['video/mp4','video/webm'].includes(type)){
            node=document.createElement('video');node.controls=true;node.style='max-width:100%;max-height:420px';
          }else{
            node=document.createElement('a');node.href=blobUrl;node.download='attachment'+(type==='application/pdf'?'.pdf':'');node.textContent='Faylı endir';
          }
          if(node.tagName!=='A'){
            node.onerror=()=>{if(valid()){node.remove();button.hidden=false;status.textContent=' Fayl açıla bilmədi. Yenidən yoxlayın.';}URL.revokeObjectURL(blobUrl);urls.delete(blobUrl);};
            node.src=blobUrl;
          }
          host.append(node);button.hidden=true;status.textContent='';
        }catch(error){if(valid())status.textContent=controller.signal.aborted?' Yükləmə vaxtı bitdi. Yenidən yoxlayın.':' '+error.message;}
        finally{clearTimeout(timer);active.delete(controller);button.disabled=false;button.setAttribute('aria-busy','false');}
      };
    });
  }
  document.addEventListener('tenant-profile',event=>{if(identity(member)!==identity(event.detail.member))clear();member=event.detail.member;});
  document.addEventListener('tenant-chat-open',event=>{clear();lead=event.detail.lead_id;});
  document.addEventListener('tenant-chat-history-reset',clear);
  document.addEventListener('tenant-chat-loaded',bind);
  document.addEventListener('tenant-chat-history-added',bind);
  document.getElementById('closeModal')?.addEventListener('click',clear);
  document.getElementById('dealModal')?.addEventListener('click',event=>{if(event.target.id==='dealModal')clear();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clear();});
  const initial=epoch;
  fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(response=>response.ok?response.json():null).then(data=>{
    if(initial===epoch&&data?.member){member=data.member;bind();}
  }).catch(()=>{});
})();
