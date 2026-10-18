/* Explicit bounded downloads. No remote URLs or credentials are put in the DOM. */
(() => {
  let member, lead, epoch=0;
  const active=new Set(), urls=new Set(), previews=new Map();
  const text=(key,fallback)=>globalThis.window?.tenantI18n?.t(key)||fallback;
  const identity=m=>m?.tenant_id+':'+m?.telegram_id;
  const size=value=>{const bytes=Number(value);if(!Number.isFinite(bytes)||bytes<0)return '';if(bytes<1024)return `${bytes} B`;if(bytes<1024*1024)return `${Math.round(bytes/1024)} KB`;return `${(bytes/(1024*1024)).toFixed(1)} MB`;};
  const duration=value=>{const seconds=Number(value);if(!Number.isFinite(seconds)||seconds<0)return '';return `${Math.floor(seconds/60)}:${String(Math.round(seconds)%60).padStart(2,'0')}`;};
  function audioPlayer(url,seconds){
    const wrap=document.createElement('div'),audio=document.createElement('audio'),play=document.createElement('button'),range=document.createElement('input'),clock=document.createElement('span');
    wrap.className='operator-media-audio';if(wrap.dataset)wrap.dataset.mediaPreview='audio';audio.preload='none';audio.src=url;play.type='button';play.className='outline';play.textContent='▶';play.setAttribute('aria-label',text('playMedia','Play'));
    range.type='range';range.min='0';range.max='1000';range.value='0';range.setAttribute('aria-label',text('mediaProgress','Progress'));clock.className='operator-audio-duration';clock.textContent=duration(seconds)||'—';
    play.onclick=()=>{if(audio.paused){audio.play?.().catch(()=>{});play.textContent='❚❚';play.setAttribute('aria-label',text('pauseMedia','Pause'));}else{audio.pause?.();play.textContent='▶';play.setAttribute('aria-label',text('playMedia','Play'));}};
    audio.addEventListener?.('timeupdate',()=>{if(Number.isFinite(audio.duration)&&audio.duration>0){range.value=String(Math.round(audio.currentTime/audio.duration*1000));clock.textContent=`${duration(audio.currentTime)} / ${duration(audio.duration)}`;}});
    audio.addEventListener?.('ended',()=>{play.textContent='▶';range.value='0';});range.oninput=()=>{if(Number.isFinite(audio.duration)&&audio.duration>0)audio.currentTime=Number(range.value)/1000*audio.duration;};
    wrap.append(play,range,clock,audio);return {node:wrap,media:audio};
  }
  function clear(){
    epoch++;
    active.forEach(controller=>controller.abort());active.clear();
    urls.forEach(url=>URL.revokeObjectURL(url));urls.clear();previews.clear();
    document.querySelectorAll('[data-chat-attachment]').forEach(host=>{
      host.querySelectorAll('[data-media-preview],img,audio,video,a').forEach(node=>node.remove());
      const button=host.querySelector('button');if(button){button.hidden=false;button.disabled=false;}
    });
  }
  function bind(){
    document.querySelectorAll('#chatRows [data-chat-attachment]').forEach(host=>{
      const button=host.querySelector('button'),status=host.querySelector('[role="status"]');
      if(!button)return;
      button.onclick=async()=>{
        if(button.disabled||!member||!lead)return;
        if(active.size>=3){status.textContent=text('mediaBusy','Bir az sonra yoxlayın.');return;}
        const current=epoch,who=identity(member),selected=lead,controller=new AbortController();
        active.add(controller);button.disabled=true;button.setAttribute('aria-busy','true');status.textContent=' '+text('loading','Yüklənir…');
        const valid=()=>current===epoch&&who===identity(member)&&selected===lead&&host.isConnected;
        const timer=setTimeout(()=>controller.abort(),70000);
        let blobUrl;
        try{
          const query=new URLSearchParams({lead_id:host.dataset.attachmentLeadId||selected,message_id:host.dataset.chatAttachment,
            expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
          const response=await fetch('/api/platform/crm/chat/attachment?'+query,
            {credentials:'same-origin',cache:'no-store',signal:controller.signal});
          if(!response.ok){const data=await response.json();const key={404:'mediaUnavailable',410:'mediaUnavailable',413:'mediaLarge',415:'mediaInvalid',429:'mediaBusy'}[response.status]||'mediaFailed';throw Error(host.dataset.connectMedia?text(key,data.error||'Fayl yüklənmədi.'):data.error||'Fayl yüklənmədi.');}
          const chunks=[],reader=response.body.getReader(),total=Number(response.headers.get('Content-Length'));
          let size=0;
          while(true){
            const {done,value}=await reader.read();if(done)break;
            if(!valid()){await reader.cancel();return;}
            size+=value.length;
            if(size>20*1024*1024){await reader.cancel();throw Error('Fayl çox böyükdür.');}
            chunks.push(value);status.textContent=total>0?' Yüklənir: '+Math.min(100,Math.round(size/total*100))+'%':' Yüklənir…';
          }
          const contentType=response.headers.get('Content-Type')||'application/octet-stream',contentLength=response.headers.get('Content-Length');
          const blob=new Blob(chunks,{type:contentType});if(!valid())return;
          if(!blob.size||blob.size>20*1024*1024)throw Error('Fayl ölçüsü düzgün deyil.');
          blobUrl=URL.createObjectURL(blob);urls.add(blobUrl);
          const type=blob.type.split(';')[0],kind=host.dataset.mediaKind;let node,media;
          if(['image/jpeg','image/png','image/webp','image/gif'].includes(type)){
            node=document.createElement('img');if(node.dataset)node.dataset.mediaPreview='image';node.className='operator-media-preview';node.alt=text('photo','Chat photo');node.title=text('openOriginal','Open original');node.onclick=()=>node.classList?.toggle('is-expanded');media=node;
          }else if(type.startsWith('audio/')||type==='application/ogg'){
            ({node,media}=audioPlayer(blobUrl,host.dataset.mediaDuration));
          }else if(['video/mp4','video/webm'].includes(type)){
            node=document.createElement('video');if(node.dataset)node.dataset.mediaPreview='video';node.className='operator-media-video';node.controls=true;node.preload='metadata';media=node;
          }else{
            node=document.createElement('a');if(node.dataset)node.dataset.mediaPreview='file';node.href=blobUrl;node.download='attachment'+(type==='application/pdf'?'.pdf':'');node.textContent=text('downloadAttachment','Download attachment');
            const meta=host.querySelector('[data-media-meta]');if(meta?.dataset)meta.textContent=[type==='application/pdf'?'PDF':type,size(contentLength||blob.size)].filter(Boolean).join(' · ');
          }
          if(node.tagName!=='A'){
            media.onerror=()=>{if(valid()){node.remove();button.hidden=false;status.textContent=' '+text('mediaInvalid','Fayl açıla bilmədi. Yenidən yoxlayın.');}URL.revokeObjectURL(blobUrl);urls.delete(blobUrl);};
            if(kind!=='audio')media.src=blobUrl;
          }
          // Keep at most three decoded previews in this tab, not the whole history.
          if(previews.size>=3){const [oldUrl,old]=previews.entries().next().value;old.node.remove();old.button.hidden=false;URL.revokeObjectURL(oldUrl);urls.delete(oldUrl);previews.delete(oldUrl);}
          previews.set(blobUrl,{node,button});
          host.append(node);button.hidden=true;status.textContent='';
        }catch(error){if(valid())status.textContent=controller.signal.aborted?' '+text('mediaTimeout','Yükləmə vaxtı bitdi. Yenidən yoxlayın.'):' '+error.message;}
        finally{clearTimeout(timer);active.delete(controller);button.disabled=false;button.setAttribute('aria-busy','false');}
      };
    });
  }
  document.addEventListener('tenant-profile',event=>{if(identity(member)!==identity(event.detail.member)||JSON.stringify(member?.permissions)!==JSON.stringify(event.detail.member?.permissions)||member?.role!==event.detail.member?.role||member?.active!==event.detail.member?.active)clear();member=event.detail.member;});
  document.addEventListener('tenant-chat-open',event=>{clear();lead=event.detail.lead_id;});
  document.addEventListener('tenant-chat-history-reset',clear);
  document.addEventListener('tenant-chat-close',clear);
  // Operator history only needs preview binding; legacy loaded events carry a different contract.
  document.addEventListener('tenant-chat-attachments-ready',bind);
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
