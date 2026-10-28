/* Explicit bounded downloads. No remote URLs or credentials are put in the DOM. */
(() => {
  let member, lead, epoch=0;
  const active=new Set(), urls=new Set(), previews=new Map(), mediaCache=new Map();
  let cacheBytes=0;
  const MAX_PREVIEW_BYTES=100*1024*1024,MAX_CACHE_BYTES=32*1024*1024;
  const text=(key,fallback)=>globalThis.window?.tenantI18n?.t(key)||fallback;
  const identity=m=>m?.tenant_id+':'+m?.telegram_id;
  const formatSize=value=>{const bytes=Number(value);if(!Number.isFinite(bytes)||bytes<0)return '';if(bytes<1024)return `${bytes} B`;if(bytes<1024*1024)return `${Math.round(bytes/1024)} KB`;return `${(bytes/(1024*1024)).toFixed(1)} MB`;};
  const duration=value=>{const seconds=Number(value);if(!Number.isFinite(seconds)||seconds<0)return '';const whole=Math.round(seconds);return `${Math.floor(whole/60)}:${String(whole%60).padStart(2,'0')}`;};
  const dispositionName=header=>{const encoded=String(header||'').match(/filename\*=UTF-8''([^;]+)/i);if(encoded){try{return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g,''));}catch(_error){}}return String(header||'').match(/filename="?([^";]+)"?/i)?.[1]?.trim()||'';};
  function audioPlayer(url,seconds){
    const wrap=document.createElement('div'),audio=document.createElement('audio'),play=document.createElement('button'),range=document.createElement('input'),clock=document.createElement('span');
    wrap.className='operator-media-audio';if(wrap.dataset)wrap.dataset.mediaPreview='audio';audio.preload='none';audio.src=url;play.type='button';play.className='outline';play.textContent='▶';play.setAttribute('aria-label',text('playMedia','Play'));
    range.type='range';range.min='0';range.max='1000';range.value='0';range.setAttribute('aria-label',text('mediaProgress','Progress'));clock.className='operator-audio-duration';clock.textContent=duration(seconds)||'—';
    const sync=()=>{play.textContent=audio.paused?'▶':'❚❚';play.setAttribute('aria-label',text(audio.paused?'playMedia':'pauseMedia',audio.paused?'Play':'Pause'));};
    play.onclick=async()=>{try{if(audio.paused)await audio.play();else audio.pause();}catch(_error){const status=wrap.parentElement?.querySelector('[role="status"]');if(status)status.textContent=text('mediaInvalid','Media cannot be played.');}sync();};
    audio.addEventListener?.('play',sync);audio.addEventListener?.('pause',sync);
    audio.addEventListener?.('timeupdate',()=>{if(Number.isFinite(audio.duration)&&audio.duration>0){range.value=String(Math.round(audio.currentTime/audio.duration*1000));clock.textContent=`${duration(audio.currentTime)} / ${duration(audio.duration)}`;}});
    audio.addEventListener?.('ended',()=>{sync();range.value='0';});range.oninput=()=>{if(Number.isFinite(audio.duration)&&audio.duration>0)audio.currentTime=Number(range.value)/1000*audio.duration;};
    wrap.append(play,range,clock,audio);return {node:wrap,media:audio};
  }
  let photoViewer=null;
  function closePhotoViewer(fromNavigation=false){
    const viewer=photoViewer;if(!viewer)return;if(!fromNavigation&&history.state?.operatorPhotoViewer===viewer.token){history.back();return;}photoViewer=null;
    window.removeEventListener('popstate',viewer.pop);document.removeEventListener('keydown',viewer.key,true);
    viewer.dialog.remove();if(viewer.origin.isConnected)viewer.origin.focus();
  }
  function openPhotoViewer(origin){
    closePhotoViewer(true);
    const dialog=document.createElement('div'),stage=document.createElement('div'),image=document.createElement('img'),close=document.createElement('button');
    const lang=window.tenantI18n?.language||'ru',labels={ru:['Закрыть фото','Увеличить фото','Уменьшить фото'],az:['Şəkli bağla','Şəkli böyüt','Şəkli kiçilt'],en:['Close photo','Zoom in','Zoom out']}[lang]||['Close photo','Zoom in','Zoom out'];
    dialog.className='operator-photo-viewer';dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label',text('photo','Chat photo'));
    stage.className='operator-photo-stage';image.className='operator-photo-image';image.src=origin.src;image.alt=origin.alt;image.tabIndex=0;image.setAttribute('role','button');image.setAttribute('aria-label',labels[1]);
    close.type='button';close.className='operator-photo-close';close.textContent='×';close.setAttribute('aria-label',labels[0]);
    stage.append(image);dialog.append(stage,close);document.body.append(dialog);
    let zoomed=false,drag=null,suppress=false;
    const toggle=()=>{if(suppress){suppress=false;return;}if(!zoomed){const rect=image.getBoundingClientRect();image.style.width=`${Math.max(1,rect.width)*2}px`;image.style.height=`${Math.max(1,rect.height)*2}px`;stage.classList.add('is-zoomed');zoomed=true;stage.scrollLeft=(stage.scrollWidth-stage.clientWidth)/2;stage.scrollTop=(stage.scrollHeight-stage.clientHeight)/2;}else{image.style.width='';image.style.height='';stage.classList.remove('is-zoomed');zoomed=false;stage.scrollLeft=stage.scrollTop=0;}image.setAttribute('aria-label',labels[zoomed?2:1]);};
    image.addEventListener('click',toggle);image.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();toggle();}});
    stage.addEventListener('pointerdown',event=>{if(!zoomed||event.target!==image||event.pointerType==='touch'||event.button!==0)return;drag={x:event.clientX,y:event.clientY,left:stage.scrollLeft,top:stage.scrollTop,moved:false};image.setPointerCapture?.(event.pointerId);});
    stage.addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>5)drag.moved=true;if(drag.moved){stage.scrollLeft=drag.left-dx;stage.scrollTop=drag.top-dy;event.preventDefault();}});
    const finish=()=>{if(drag?.moved){suppress=true;setTimeout(()=>{suppress=false;},0);}drag=null;};stage.addEventListener('pointerup',finish);stage.addEventListener('pointercancel',finish);
    const token=String(Date.now())+Math.random();const pop=()=>closePhotoViewer(true),key=event=>{if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();closePhotoViewer();}else if(event.key==='Tab'){event.preventDefault();event.stopImmediatePropagation();(document.activeElement===close?image:close).focus();}};
    photoViewer={dialog,origin,token,pop,key};history.pushState({...history.state,operatorPhotoViewer:token},'',location.href);window.addEventListener('popstate',pop);document.addEventListener('keydown',key,true);
    close.onclick=()=>closePhotoViewer();stage.onclick=event=>{if(event.target===stage)closePhotoViewer();};close.focus();
  }
  const viewerStyle=document.createElement('style');viewerStyle.textContent=`.operator-photo-viewer{position:fixed;inset:0;z-index:25000;background:#000e;display:flex;align-items:stretch;justify-content:stretch;padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)}.operator-photo-stage{width:100%;height:100%;overflow:auto;display:flex;align-items:center;justify-content:center;touch-action:pan-x pan-y pinch-zoom;overscroll-behavior:contain}.operator-photo-stage.is-zoomed{display:block;cursor:grab}.operator-photo-stage.is-zoomed:active{cursor:grabbing}.operator-photo-image{display:block;max-width:100%;max-height:100%;object-fit:contain;cursor:zoom-in}.operator-photo-stage.is-zoomed .operator-photo-image{max-width:none;max-height:none;margin:auto;cursor:zoom-out}.operator-photo-image:focus-visible,.operator-photo-close:focus-visible{outline:3px solid white;outline-offset:3px}.operator-photo-close{position:absolute;right:max(8px,env(safe-area-inset-right));top:max(8px,env(safe-area-inset-top));width:44px;height:44px;border:0;border-radius:50%;background:#222c;color:white;font-size:28px;line-height:1}`;document.head?.append(viewerStyle);
  const stop=media=>{if(media&&['AUDIO','VIDEO'].includes(media.tagName))media.pause?.();};
  function clear(dropCache=false){
    if(photoViewer){if(history.state?.operatorPhotoViewer===photoViewer.token){const state={...history.state};delete state.operatorPhotoViewer;history.replaceState(state,'',location.href);}closePhotoViewer(true);}
    epoch++;
    active.forEach(controller=>controller.abort());active.clear();
    urls.forEach(url=>URL.revokeObjectURL(url));urls.clear();previews.forEach(preview=>stop(preview.media));previews.clear();
    document.querySelectorAll('[data-native-audio] audio').forEach(audio=>{audio.pause?.();audio.removeAttribute?.('src');audio.load?.();});
    if(dropCache){mediaCache.clear();cacheBytes=0;}
    document.querySelectorAll('[data-chat-attachment]').forEach(host=>{
      host.querySelectorAll('[data-media-preview],img,audio,video,a').forEach(node=>node.remove());
      const button=host.querySelectorAll('button[data-media-open]')[0]||host.querySelector('button');if(button){button.hidden=false;button.disabled=false;}delete host.dataset.mediaPreviewState;
    });
  }
  function bind(){
    document.querySelectorAll('#chatRows [data-chat-attachment], [data-learning-chat] [data-chat-attachment]').forEach(host=>{
      const button=host.querySelectorAll('button[data-media-open]')[0]||host.querySelector('button'),status=host.querySelector('[role="status"]');
      if(!button)return;
      const load=async(auto=false)=>{
        if(button.disabled||!member||!(lead||host.dataset.attachmentLeadId))return;
        const retryAt=Number(host.dataset.mediaRetryAt||0);
        if(retryAt>Date.now()){
          const seconds=Math.ceil((retryAt-Date.now())/1000);
          status.textContent=' '+text('mediaRetryAfter','Try again in {seconds} seconds.').replace('{seconds}',String(seconds));
          return;
        }
        if(active.size>=3){status.textContent=text('mediaBusy','Bir az sonra yoxlayın.');if(auto)button.hidden=false;return;}
        const current=epoch,who=identity(member),selected=lead||host.dataset.attachmentLeadId,controller=new AbortController();
        active.add(controller);button.disabled=true;button.setAttribute('aria-busy','true');status.textContent=' '+text('loading','Yüklənir…');
        const valid=()=>current===epoch&&who===identity(member)&&selected===(lead||host.dataset.attachmentLeadId)&&host.isConnected;
        const timer=setTimeout(()=>controller.abort(),70000);
        let blobUrl;
        try{
          const query=new URLSearchParams({lead_id:host.dataset.attachmentLeadId||selected,message_id:host.dataset.chatAttachment,
            expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id,...(host.dataset.channelId&&host.dataset.conversationId?{channel_id:host.dataset.channelId,conversation_id:host.dataset.conversationId}:{})});
          const cacheKey=query.toString();let blob=mediaCache.get(cacheKey),contentLength='',servedFilename=host.querySelector('[data-media-meta]')?.textContent||'';
          if(blob){mediaCache.delete(cacheKey);mediaCache.set(cacheKey,blob);}
          else{
            const response=await fetch('/api/platform/crm/chat/attachment?'+query,
              {credentials:'same-origin',cache:'no-store',signal:controller.signal});
            if(!response.ok){const data=await response.json().catch(()=>({}));const key=data.code==='media_duration_limit'?'mediaDuration':({404:'mediaUnavailable',410:'mediaUnavailable',413:'mediaLarge',415:'mediaInvalid',429:'mediaBusy'}[response.status]||'mediaFailed');if(response.status===429){const retry=Math.max(1,Math.min(3600,parseInt(response.headers.get('Retry-After')||'60',10)||60));host.dataset.mediaRetryAt=String(Date.now()+retry*1000);throw Error(text('mediaRetryAfter','Try again in {seconds} seconds.').replace('{seconds}',String(retry)));}throw Error(host.dataset.connectMedia?text(key,data.error||'Fayl yüklənmədi.'):data.error||'Fayl yüklənmədi.');}
            servedFilename=dispositionName(response.headers.get('Content-Disposition'))||servedFilename;const declaredSize=Number(response.headers.get('Content-Length'));contentLength=response.headers.get('Content-Length')||'';if(auto&&declaredSize>5*1024*1024)throw Error(text('mediaLarge','Fayl çox böyükdür.'));
            const chunks=[],reader=response.body.getReader(),total=declaredSize;
            let size=0;
            while(true){
              const {done,value}=await reader.read();if(done)break;
              if(!valid()){await reader.cancel();return;}
              size+=value.length;if(auto&&size>5*1024*1024){await reader.cancel();throw Error(text('mediaLarge','Fayl çox böyükdür.'));}
              if(size>MAX_PREVIEW_BYTES){await reader.cancel();throw Error('Fayl çox böyükdür.');}
              chunks.push(value);status.textContent=total>0?' Yüklənir: '+Math.min(100,Math.round(size/total*100))+'%':' Yüklənir…';
            }
            const contentType=response.headers.get('Content-Type')||'application/octet-stream';
            blob=new Blob(chunks,{type:contentType});if(!valid())return;
            // Large previews remain one-shot Blob URLs. They are never retained
            // in the 32 MiB in-tab cache after playback or preview is closed.
            if(blob.size<=MAX_CACHE_BYTES){while(cacheBytes+blob.size>MAX_CACHE_BYTES&&mediaCache.size){const [oldKey,oldBlob]=mediaCache.entries().next().value;mediaCache.delete(oldKey);cacheBytes-=oldBlob.size;}if(cacheBytes+blob.size<=MAX_CACHE_BYTES){mediaCache.set(cacheKey,blob);cacheBytes+=blob.size;}}
          }
          if(auto&&blob.size>5*1024*1024)throw Error(text('mediaLarge','Fayl çox böyükdür.'));
          if(!blob.size||blob.size>MAX_PREVIEW_BYTES)throw Error('Fayl ölçüsü düzgün deyil.');
          blobUrl=URL.createObjectURL(blob);urls.add(blobUrl);
          const type=blob.type.split(';')[0],expected=host.dataset.mediaKind;if(expected==='image'&&!type.startsWith('image/'))throw Error(text('mediaInvalid','Fayl açıla bilmədi.'));if(expected==='audio'&&!type.startsWith('audio/')&&type!=='application/ogg')throw Error(text('mediaInvalid','Fayl açıla bilmədi.'));if(expected==='video'&&!type.startsWith('video/'))throw Error(text('mediaInvalid','Fayl açıla bilmədi.'));let node,media;
          if(['image/jpeg','image/png','image/webp','image/gif'].includes(type)){
            node=document.createElement('img');if(node.dataset)node.dataset.mediaPreview='image';node.className='operator-media-preview';node.alt=text('photo','Chat photo');node.title=text('openOriginal','Open original');node.tabIndex=0;node.setAttribute?.('role','button');node.onclick=()=>openPhotoViewer(node);node.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openPhotoViewer(node);}};media=node;
          }else if(type.startsWith('audio/')||type==='application/ogg'){
            ({node,media}=audioPlayer(blobUrl,host.dataset.mediaDuration));
          }else if(['video/mp4','video/webm'].includes(type)){
            node=document.createElement('video');if(node.dataset)node.dataset.mediaPreview='video';node.className='operator-media-video';node.controls=true;node.preload='metadata';media=node;
          }else{
            node=document.createElement('a');if(node.dataset)node.dataset.mediaPreview='file';node.href=blobUrl;node.target='_blank';node.rel='noopener';node.download=servedFilename||'attachment';node.textContent=servedFilename||text('downloadAttachment','Download attachment');
            const meta=host.querySelector('[data-media-meta]');if(meta?.dataset)meta.textContent=[servedFilename,type==='application/pdf'?'PDF':type,formatSize(contentLength||blob.size)].filter(Boolean).join(' · ');
          }
          if(node.tagName!=='A'){
            media.onerror=()=>{stop(media);previews.delete(blobUrl);if(valid()){node.remove();button.hidden=false;status.textContent=' '+text('mediaInvalid','Fayl açıla bilmədi. Yenidən yoxlayın.');}URL.revokeObjectURL(blobUrl);urls.delete(blobUrl);};
            if(media.tagName!=='AUDIO')media.src=blobUrl;
          }
          // Keep at most three decoded previews in this tab, not the whole history.
          if(previews.size>=3){const [oldUrl,old]=previews.entries().next().value;stop(old.media);old.node.remove();old.button.hidden=false;URL.revokeObjectURL(oldUrl);urls.delete(oldUrl);previews.delete(oldUrl);}
          previews.set(blobUrl,{node,button,media});
          if(host.insertBefore)host.insertBefore(node,button);else host.append(node);button.hidden=true;status.textContent='';
          delete host.dataset.mediaRetryAt;
          if(media?.tagName==='AUDIO'&&!auto&&typeof media.play==='function'){
            try{await media.play();}catch(_error){status.textContent=' '+text('mediaInvalid','Audio is ready. Press Play to listen.');}
          }
        }catch(error){if(blobUrl){URL.revokeObjectURL(blobUrl);urls.delete(blobUrl);}if(valid()){host.dataset.mediaPreviewState='unavailable';status.textContent=controller.signal.aborted?' '+text('mediaTimeout','Yükləmə vaxtı bitdi. Yenidən yoxlayın.'):' '+error.message;}}
        finally{clearTimeout(timer);active.delete(controller);button.disabled=false;button.setAttribute('aria-busy','false');}
      };
      button.onclick=()=>load(false);const knownImage=host.dataset.mediaKind==='image';if(knownImage&&!host.dataset.mediaPreviewState){host.dataset.mediaPreviewState='loading';button.hidden=true;load(true).then(()=>{if(!host.isConnected)return;if(!host.querySelector('[data-media-preview="image"]'))button.hidden=false;});}
    });
  }
  document.addEventListener('tenant-profile',event=>{if(identity(member)!==identity(event.detail.member)||JSON.stringify(member?.permissions)!==JSON.stringify(event.detail.member?.permissions)||member?.role!==event.detail.member?.role||member?.active!==event.detail.member?.active)clear(true);member=event.detail.member;});
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
