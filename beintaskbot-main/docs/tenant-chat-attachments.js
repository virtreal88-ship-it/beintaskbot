/* Explicit bounded downloads. No remote URLs or credentials are put in the DOM. */
(() => {
  let member, lead, epoch=0, profileRevision=0;
  const active=new Set(), urls=new Set(), previews=new Map(), mediaCache=new Map(), retryUntil=new Map(), loadedKeys=new Set(), pendingKeys=new Map();
  let cacheBytes=0;
  const AUDIO_INTENT_KEY='tenant.audio-reload-intent.v1',AUDIO_INTENT_TTL=3600000;
  let profilePolicy='',freshProfile=false,autoRestoreAttempted=false;
  const restorePending=new WeakSet();
  const hash=async value=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(byte=>byte.toString(16).padStart(2,'0')).join('');
  const forgetAudioIntent=()=>{try{sessionStorage.removeItem(AUDIO_INTENT_KEY);}catch{}};
  function audioIntent(){try{const value=JSON.parse(sessionStorage.getItem(AUDIO_INTENT_KEY)||'null');if(!value)return null;if(Object.keys(value).some(key=>!['v','owner','scope','source','version','expires_at','blocked','retry_until'].includes(key))||value.v!==1||!['owner','scope','source','version'].every(key=>/^[a-f0-9]{64}$/.test(value[key]||''))||!Number.isFinite(value.expires_at)||value.expires_at<=Date.now()||value.expires_at>Date.now()+AUDIO_INTENT_TTL||typeof value.blocked!=='boolean'||!Number.isFinite(value.retry_until)||value.retry_until<0){forgetAudioIntent();return null;}return value;}catch{forgetAudioIntent();return null;}}
  function writeAudioIntent(value){try{sessionStorage.setItem(AUDIO_INTENT_KEY,JSON.stringify(value));}catch{}}
  const policy=data=>JSON.stringify([identity(data?.member),data?.member?.role,data?.member?.active,data?.member?.permissions,data?.role_permissions,data?.capabilities?.modules?.dialogs]);
  async function audioBinding(host){
    if(!freshProfile||!member||member.active===false||!['audio','voice','voice_message','ptt'].includes(host.dataset.mediaKind)||!host.closest?.('#chatRows')||!/^[a-f0-9]{64}$/.test(host.dataset.mediaVersion||''))return null;
    const shell=host.closest?.('[data-dialog-key]');if(!shell)return null;
    const proof=profilePolicy,who=identity(member),current=epoch,version=host.dataset.mediaVersion;
    const [owner,scope,source]=await Promise.all([hash(proof),hash(shell.dataset.dialogKey),hash(String(host.dataset.chatAttachment||''))]);
    if(!freshProfile||proof!==profilePolicy||who!==identity(member)||current!==epoch||!host.isConnected||version!==host.dataset.mediaVersion)return null;
    return {owner,scope,source,version};
  }
  async function rememberAudio(host){try{await profileFetch;const binding=await audioBinding(host);if(binding&&host.dataset.mediaPreviewState==='loaded'&&host.querySelector('audio')?.getAttribute('src')&&!host.querySelector('audio')?.error)writeAudioIntent({v:1,...binding,expires_at:Date.now()+AUDIO_INTENT_TTL,blocked:false,retry_until:0});}catch{}}
  function blockAudioIntent(binding,status,retry=0){const value=audioIntent();if(!value||!binding||!['owner','scope','source','version'].every(key=>value[key]===binding[key]))return;if([403,404,410].includes(status)){forgetAudioIntent();return;}writeAudioIntent({...value,blocked:true,retry_until:Math.max(value.retry_until,retry)});}
  async function failAudio(host,binding,status,retry=0){try{blockAudioIntent(binding||await audioBinding(host),status,retry);}catch{}}
  async function restoreAudio(host,load){
    if(restorePending.has(host)||autoRestoreAttempted||!freshProfile||!audioIntent())return;
    restorePending.add(host);
    try{const binding=await audioBinding(host),value=audioIntent();if(!binding||!value)return;
      if(value.owner!==binding.owner){forgetAudioIntent();return;}
      if(value.scope!==binding.scope||value.source!==binding.source)return;
      if(value.version!==binding.version){forgetAudioIntent();return;}
      if(value.retry_until>Date.now())rememberRetry(host,host.querySelector('[data-media-open]'),value.retry_until);
      if(value.blocked||value.retry_until>Date.now()||autoRestoreAttempted)return;
      autoRestoreAttempted=true;await load(false,true,binding);
    }catch{}finally{restorePending.delete(host);}
  }
  const MAX_PREVIEW_BYTES=100*1024*1024,MAX_CACHE_BYTES=32*1024*1024;
  const text=(key,fallback)=>globalThis.window?.tenantI18n?.t(key)||fallback;
  const retryDelay=response=>Math.max(1,Math.min(3600,parseInt(response.headers.get('Retry-After')||'60',10)||60));
  const mediaFailureState=status=>status===429?'rate_limited':status===404||status===410?'unavailable':status===403?'forbidden':status===413?'too_large':status===415?'invalid':status===502||status===503?'retryable':'terminal';
  const retryKey=(host,fallback='')=>[identity(member),host?.dataset.channelId||'',host?.dataset.conversationId||'',host?.dataset.chatAttachment||fallback].join(':');
  const retryAt=(host,button,fallback='')=>Math.max(Number(host?.dataset.mediaRetryAt||0),Number(button?.dataset.retryAt||0),Number(retryUntil.get(retryKey(host,fallback))||0));
  const rememberRetry=(host,button,until,fallback='')=>{retryUntil.set(retryKey(host,fallback),until);if(host)host.dataset.mediaRetryAt=String(until);if(button)button.dataset.retryAt=String(until);};
  const canStartMedia=host=>active.size<3&&(!(host?.dataset.connectMedia==='true')||[...active].filter(controller=>controller.connectMedia).length<2);
  const busyText=()=>text('mediaBusy','Other attachments are loading. Try again when they finish.');
  function updateMediaMenu(host){const load=host?.querySelector('[data-media-menu-load]'),primary=host?.querySelector('[data-media-open]'),download=host?.querySelector('[data-media-download]');if(load){const loaded=!!host.querySelector('[data-media-preview]'),state=host.dataset.mediaPreviewState,blocked=['unavailable','forbidden','too_large','invalid','terminal'].includes(state);load.hidden=loaded;load.disabled=blocked;load.textContent=['retryable','rate_limited'].includes(state)?text('retryAttachment','Retry loading'):text('loadAttachment','Load file');if(primary)primary.disabled=blocked;if(download)download.disabled=blocked;}paintNativeAudioCard(host,primary,load);}
  function paintNativeAudioCard(host,primary,menuLoad){
    if(host?.dataset.nativeAudio!=='true')return;
    const audio=host.querySelector('.operator-native-audio'),busy=primary?.getAttribute('aria-busy')==='true';
    const loaded=!!audio?.getAttribute('src')&&audio?.dataset.mediaPreview==='audio';
    const blocked=['unavailable','forbidden','too_large','invalid','terminal'].includes(host.dataset.mediaPreviewState);
    if(audio)audio.hidden=!loaded;
    if(primary){primary.hidden=loaded;primary.disabled=busy||blocked;primary.textContent=busy?text('loading','Loading…'):['retryable','rate_limited'].includes(host.dataset.mediaPreviewState)?text('retryAttachment','Retry loading'):text('loadAttachment','Load file');}
    if(menuLoad)menuLoad.disabled=busy||blocked;
    const status=host.querySelector('[data-media-load-status]'),language=window.tenantI18n?.language||'en';
    const ready=({ru:'Файл загружен',az:'Səs yükləndi',en:'Audio loaded'})[language]||'Audio loaded';
    const idle=({ru:'Аудио не загружено',az:'Səs yüklənməyib',en:'Audio not loaded'})[language]||'Audio not loaded';
    if(status){if(loaded&&!status.textContent.trim())status.textContent=ready;else if(!loaded&&!busy&&status.textContent===ready)status.textContent=idle;}
    host.dataset.audioState=busy?'loading':loaded?'loaded':host.dataset.mediaPreviewState||'unloaded';
  }
  const identity=m=>m?.tenant_id+':'+m?.telegram_id;
  const formatSize=value=>{const bytes=Number(value);if(!Number.isFinite(bytes)||bytes<0)return '';if(bytes<1024)return `${bytes} B`;if(bytes<1024*1024)return `${Math.round(bytes/1024)} KB`;return `${(bytes/(1024*1024)).toFixed(1)} MB`;};
  const duration=value=>{const seconds=Number(value);if(!Number.isFinite(seconds)||seconds<0)return '';const whole=Math.round(seconds);return `${Math.floor(whole/60)}:${String(whole%60).padStart(2,'0')}`;};
  const dispositionName=header=>{const encoded=String(header||'').match(/filename\*=UTF-8''([^;]+)/i);if(encoded){try{return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g,''));}catch(_error){}}return String(header||'').match(/filename="?([^";]+)"?/i)?.[1]?.trim()||'';};
  async function downloadAttachment(button,url,status){
    if(button.disabled||!member||!url)return;
    const host=button.closest('[data-chat-attachment]'),statusNode=status||host?.querySelector('[role="status"]'),key=button.dataset.mediaDownload||url,attachmentKey=host?.dataset.chatAttachment||key;
    const retry=retryAt(host,button,attachmentKey);if(retry>Date.now()){if(statusNode)statusNode.textContent=text('mediaRetryAfter','Try again in {seconds} seconds.').replace('{seconds}',String(Math.ceil((retry-Date.now())/1000)));return;}
    if(!canStartMedia(host)){if(statusNode)statusNode.textContent=busyText();return;}
    const current=epoch,who=identity(member),controller=new AbortController();controller.connectMedia=host?.dataset.connectMedia==='true';active.add(controller);button.disabled=true;button.setAttribute('aria-busy','true');if(statusNode)statusNode.textContent=text('loading','Yüklənir…');
    try{const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if(!response.ok){const data=await response.json().catch(()=>({}));if(response.status===429){const seconds=retryDelay(response),until=Date.now()+seconds*1000;rememberRetry(host,button,until,attachmentKey);if(host){host.dataset.mediaPreviewState='rate_limited';updateMediaMenu(host);}throw Error(text('mediaRetryAfter','Try again in {seconds} seconds.').replace('{seconds}',String(seconds)));}if(host){host.dataset.mediaPreviewState=mediaFailureState(response.status);updateMediaMenu(host);}throw Error(response.status===403?text('mediaForbidden','You do not have access to this file.'):response.status===404||response.status===410?text('mediaUnavailable','File is unavailable or expired.'):response.status===413?text('mediaLarge','File is too large.'):response.status===415?text('mediaInvalid','File format is not supported.'):data.error||text('mediaFailed','File could not be downloaded.'));}
      const blob=await response.blob();if(current!==epoch||who!==identity(member)||!button.isConnected)return;if(!blob.size||blob.size>MAX_PREVIEW_BYTES)throw Error(text('mediaLarge','File is too large.'));
      const objectUrl=URL.createObjectURL(blob),link=document.createElement('a');link.href=objectUrl;link.download=dispositionName(response.headers.get('Content-Disposition'))||'attachment';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(objectUrl),60000);if(statusNode)statusNode.textContent='';retryUntil.delete(retryKey(host,attachmentKey));delete button.dataset.retryAt;if(host){delete host.dataset.mediaRetryAt;delete host.dataset.mediaPreviewState;updateMediaMenu(host);}
    }catch(error){if(current===epoch&&who===identity(member)&&button.isConnected){if(host&&!['rate_limited','unavailable','forbidden','too_large','invalid','terminal'].includes(host.dataset.mediaPreviewState)){host.dataset.mediaPreviewState='retryable';updateMediaMenu(host);}if(statusNode)statusNode.textContent=error.message||text('mediaFailed','File could not be downloaded.');}}
    finally{active.delete(controller);if(button.isConnected){button.setAttribute('aria-busy','false');if(host)updateMediaMenu(host);else button.disabled=false;}}
  }
  if(globalThis.window)window.tenantDownloadAttachment=downloadAttachment;
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
    active.forEach(controller=>{if(dropCache===true||!controller.audioReload){controller.abort();active.delete(controller);}});pendingKeys.clear();
    urls.forEach(url=>URL.revokeObjectURL(url));urls.clear();previews.forEach(preview=>stop(preview.media));previews.clear();
    document.querySelectorAll('[data-native-audio] audio').forEach(audio=>{audio.pause?.();audio.removeAttribute?.('src');audio.load?.();delete audio.dataset.mediaPreview;});
    if(dropCache===true){mediaCache.clear();cacheBytes=0;retryUntil.clear();loadedKeys.clear();}
    document.querySelectorAll('[data-chat-attachment]').forEach(host=>{
      host.querySelectorAll('[data-media-preview]:not(.operator-native-audio),img,audio:not(.operator-native-audio),video,a').forEach(node=>node.remove());
      const button=host.querySelectorAll('button[data-media-open]')[0]||host.querySelector('button');if(button){button.hidden=false;button.disabled=false;}delete host.dataset.mediaPreviewState;updateMediaMenu(host);
    });
  }
  function bind(){
    // Same-dialog renders retain stable media hosts. Retire only removed assets.
    for(const [url,preview] of previews){if(!preview.host.isConnected){stop(preview.media);URL.revokeObjectURL(url);urls.delete(url);previews.delete(url);}}

    document.querySelectorAll('[data-media-download]').forEach(button=>{button.onclick=()=>downloadAttachment(button,button.dataset.mediaDownload,button.closest('.operator-media-card')?.querySelector('[data-media-load-status]')||button.closest('.operator-media-card')?.querySelector('[role="status"]'));});
    document.querySelectorAll('#chatRows [data-chat-attachment], [data-learning-chat] [data-chat-attachment]').forEach(host=>{
      const button=host.querySelectorAll('button[data-media-open]')[0]||host.querySelector('button'),status=host.querySelector('[data-media-load-status]')||host.querySelector('[role="status"]');
      if(!button)return;
      const menuLoad=host.querySelector('[data-media-menu-load]');if(menuLoad&&!menuLoad.dataset.bound){menuLoad.dataset.bound='true';menuLoad.onclick=()=>{menuLoad.closest('details')?.removeAttribute('open');button.click();};}
      updateMediaMenu(host);
      if(host.querySelector('[data-media-preview]'))return;
      if(host.querySelector('.operator-native-audio')&&!status.textContent)status.textContent=({ru:'Аудио не загружено',az:'Səs yüklənməyib',en:'Audio not loaded'}[window.tenantI18n?.language]||'Audio is not loaded in this tab');
      const mediaQuery=()=>new URLSearchParams({lead_id:host.dataset.attachmentLeadId||lead,message_id:host.dataset.chatAttachment,expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id,...(host.dataset.channelId&&host.dataset.conversationId?{channel_id:host.dataset.channelId,conversation_id:host.dataset.conversationId}:{})});
      const previewKey=()=>mediaQuery().toString()+'|'+String(host.dataset.mediaVersion||'');
      const load=async(cachedOnly=false,automatic=false,intentBinding=null)=>{
        if(button.disabled||!member||!(lead||host.dataset.attachmentLeadId))return;
        const requestKey=previewKey();if(pendingKeys.has(requestKey)){status.textContent=busyText();return;}
        const retry=retryAt(host,button,host.dataset.chatAttachment||'');
        if(retry>Date.now()){
          const seconds=Math.ceil((retry-Date.now())/1000);
          status.textContent=' '+text('mediaRetryAfter','Try again in {seconds} seconds.').replace('{seconds}',String(seconds));
          return;
        }
        if(!cachedOnly&&!canStartMedia(host)){status.textContent=busyText();return;}
        const current=epoch,who=identity(member),selected=lead||host.dataset.attachmentLeadId,controller=new AbortController();
        controller.audioReload=automatic;controller.connectMedia=host.dataset.connectMedia==='true';if(!cachedOnly){active.add(controller);pendingKeys.set(requestKey,controller);}button.disabled=true;button.setAttribute('aria-busy','true');if(menuLoad)menuLoad.disabled=true;status.textContent=' '+text('loading','Yüklənir…');updateMediaMenu(host);
        const valid=()=>current===epoch&&who===identity(member)&&selected===(lead||host.dataset.attachmentLeadId)&&host.isConnected;
        const proof=profilePolicy,sourceVersion=host.dataset.mediaVersion,sourceDialog=host.closest?.('[data-dialog-key]')?.dataset.dialogKey;
        const canRetain=()=>automatic&&freshProfile&&proof===profilePolicy&&who===identity(member)&&selected===(lead||host.dataset.attachmentLeadId)&&[...document.querySelectorAll('#chatRows [data-chat-attachment]')].some(next=>next.dataset.chatAttachment===host.dataset.chatAttachment&&next.dataset.mediaVersion===sourceVersion&&next.dataset.channelId===host.dataset.channelId&&next.dataset.conversationId===host.dataset.conversationId&&next.closest('[data-dialog-key]')?.dataset.dialogKey===sourceDialog);
        const timer=setTimeout(()=>controller.abort(),70000);
        let blobUrl;
        try{
          const query=mediaQuery(),cacheKey=previewKey();let blob=mediaCache.get(cacheKey),contentLength='',servedFilename=host.dataset.mediaFilename||'';
          if(cachedOnly&&!blob)return;
          if(blob){mediaCache.delete(cacheKey);mediaCache.set(cacheKey,blob);}
          else{
            const response=await fetch('/api/platform/crm/chat/attachment?'+query,
              {credentials:'same-origin',cache:'no-store',signal:controller.signal});
            if(!response.ok){const data=await response.json().catch(()=>({}));const key=data.code==='media_duration_limit'?'mediaDuration':({404:'mediaUnavailable',410:'mediaUnavailable',413:'mediaLarge',415:'mediaInvalid',429:'mediaBusy'}[response.status]||'mediaFailed');await failAudio(host,intentBinding,response.status,response.status===429?Date.now()+retryDelay(response)*1000:0);if(response.status===429){const seconds=retryDelay(response),until=Date.now()+seconds*1000;rememberRetry(host,button,until,host.dataset.chatAttachment||'');host.dataset.mediaPreviewState='rate_limited';updateMediaMenu(host);throw Error(text('mediaRetryAfter','Try again in {seconds} seconds.').replace('{seconds}',String(seconds)));}host.dataset.mediaPreviewState=mediaFailureState(response.status);updateMediaMenu(host);throw Error(response.status===403?text('mediaForbidden','You do not have access to this file.'):response.status===404||response.status===410?text(key,'File is unavailable or expired.'):response.status===413?text(key,'File is too large.'):response.status===415?text(key,'File format is not supported.'):data.error||text('mediaFailed','File could not be downloaded.'));}
            servedFilename=dispositionName(response.headers.get('Content-Disposition'))||servedFilename;const declaredSize=Number(response.headers.get('Content-Length'));contentLength=response.headers.get('Content-Length')||'';
            const chunks=[],reader=response.body.getReader(),total=declaredSize;
            let size=0;
            while(true){
              const {done,value}=await reader.read();if(done)break;
              if(!valid()&&!canRetain()){await reader.cancel();return;}
              size+=value.length;
              if(size>MAX_PREVIEW_BYTES){await reader.cancel();throw Error('Fayl çox böyükdür.');}
              chunks.push(value);status.textContent=total>0?' Yüklənir: '+Math.min(100,Math.round(size/total*100))+'%':' Yüklənir…';
            }
            const contentType=response.headers.get('Content-Type')||'application/octet-stream';
            blob=new Blob(chunks,{type:contentType});if(!valid()&&!canRetain())return;
            // Large previews remain one-shot Blob URLs. They are never retained
            // in the 32 MiB in-tab cache after playback or preview is closed.
            if(blob.size<=MAX_CACHE_BYTES){while(cacheBytes+blob.size>MAX_CACHE_BYTES&&mediaCache.size){const [oldKey,oldBlob]=mediaCache.entries().next().value;mediaCache.delete(oldKey);cacheBytes-=oldBlob.size;}if(cacheBytes+blob.size<=MAX_CACHE_BYTES){mediaCache.set(cacheKey,blob);cacheBytes+=blob.size;}}
          }
          if(!valid()){if(canRetain()&&mediaCache.has(cacheKey)){loadedKeys.add(cacheKey);while(loadedKeys.size>3)loadedKeys.delete(loadedKeys.values().next().value);bind();}return;}
          if(!blob.size||blob.size>MAX_PREVIEW_BYTES){host.dataset.mediaPreviewState='too_large';updateMediaMenu(host);throw Error('Fayl ölçüsü düzgün deyil.');}
          blobUrl=URL.createObjectURL(blob);urls.add(blobUrl);
          const type=blob.type.split(';')[0],expected=String(host.dataset.mediaKind||'').toLowerCase();if(expected==='image'&&!type.startsWith('image/')){host.dataset.mediaPreviewState='invalid';updateMediaMenu(host);throw Error(text('mediaInvalid','Fayl açıla bilmədi.'));}if(['audio','voice','voice_message','ptt'].includes(expected)&&!type.startsWith('audio/')&&type!=='application/ogg'){host.dataset.mediaPreviewState='invalid';updateMediaMenu(host);throw Error(text('mediaInvalid','Fayl açıla bilmədi.'));}if(expected==='video'&&!type.startsWith('video/')){host.dataset.mediaPreviewState='invalid';updateMediaMenu(host);throw Error(text('mediaInvalid','Fayl açıla bilmədi.'));}let node,media;
          if(['image/jpeg','image/png','image/webp','image/gif'].includes(type)){
            node=document.createElement('img');if(node.dataset)node.dataset.mediaPreview='image';node.className='operator-media-preview';node.alt=text('photo','Chat photo');node.title=text('openOriginal','Open original');node.tabIndex=0;node.setAttribute?.('role','button');node.onclick=()=>openPhotoViewer(node);node.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openPhotoViewer(node);}};media=node;
          }else if(type.startsWith('audio/')||type==='application/ogg'){
            const nativeAudio=host.querySelector('.operator-native-audio');
            if(nativeAudio){node=media=nativeAudio;node.dataset.mediaPreview='audio';node.src=blobUrl;node.load?.();}
            else ({node,media}=audioPlayer(blobUrl,host.dataset.mediaDuration));
          }else if(['video/mp4','video/webm'].includes(type)){
            node=document.createElement('video');if(node.dataset)node.dataset.mediaPreview='video';node.className='operator-media-video';node.controls=true;node.preload='metadata';media=node;
          }else{
            node=document.createElement('a');if(node.dataset)node.dataset.mediaPreview='file';node.href=blobUrl;node.target='_blank';node.rel='noopener';node.download=servedFilename||'attachment';node.textContent=servedFilename||text('downloadAttachment','Download attachment');
            const meta=host.querySelector('[data-media-meta]');if(meta?.dataset)meta.textContent=[servedFilename,type==='application/pdf'?'PDF':type,formatSize(contentLength||blob.size)].filter(Boolean).join(' · ');
          }
          const loadedMeta=host.querySelector('[data-media-meta]');if(loadedMeta)loadedMeta.textContent=[servedFilename,type==='application/pdf'?'PDF':'',formatSize(blob.size)].filter(Boolean).join(' · ');
          if(node.tagName!=='A'){
            media.onerror=()=>{previews.delete(blobUrl);if(valid()){void failAudio(host,intentBinding,415);stop(media);loadedKeys.delete(cacheKey);const invalidBlob=mediaCache.get(cacheKey);if(invalidBlob===blob){cacheBytes-=invalidBlob.size;mediaCache.delete(cacheKey);}if(node.classList?.contains('operator-native-audio')){node.removeAttribute('src');node.load?.();delete node.dataset.mediaPreview;}else node.remove();host.dataset.mediaPreviewState='invalid';button.hidden=!!host.querySelector('.operator-native-audio');status.textContent=' '+text('mediaInvalid','Fayl açıla bilmədi. Yenidən yoxlayın.');updateMediaMenu(host);}URL.revokeObjectURL(blobUrl);urls.delete(blobUrl);};
            if(media.tagName!=='AUDIO')media.src=blobUrl;
          }
          // Keep at most three decoded previews in this tab, not the whole history.
          if(previews.size>=3){const [oldUrl,old]=previews.entries().next().value;stop(old.media);if(old.node.classList?.contains('operator-native-audio')){old.node.removeAttribute('src');old.node.load?.();delete old.node.dataset.mediaPreview;old.button.hidden=true;delete old.host.dataset.mediaPreviewState;updateMediaMenu(old.host);}else{old.node.remove();old.button.hidden=false;delete old.host.dataset.mediaPreviewState;updateMediaMenu(old.host);}URL.revokeObjectURL(oldUrl);urls.delete(oldUrl);previews.delete(oldUrl);loadedKeys.delete(old.cacheKey);}
          previews.set(blobUrl,{node,button,media,host,cacheKey});if(mediaCache.has(cacheKey)){loadedKeys.delete(cacheKey);loadedKeys.add(cacheKey);while(loadedKeys.size>3)loadedKeys.delete(loadedKeys.values().next().value);}
          if(host.insertBefore)host.insertBefore(node,button);else host.append(node);button.hidden=true;status.textContent='';
          delete host.dataset.mediaRetryAt;retryUntil.delete(retryKey(host,host.dataset.chatAttachment||''));host.dataset.mediaPreviewState='loaded';updateMediaMenu(host);if(!cachedOnly&&!automatic)void rememberAudio(host);
        }catch(error){if(blobUrl){URL.revokeObjectURL(blobUrl);urls.delete(blobUrl);}if(valid()){if(automatic)blockAudioIntent(intentBinding,0);if(!host.dataset.mediaPreviewState||host.dataset.mediaPreviewState==='loaded')host.dataset.mediaPreviewState='retryable';status.textContent=controller.signal.aborted?' '+text('mediaTimeout','Yükləmə vaxtı bitdi. Yenidən yoxlayın.'):' '+error.message;updateMediaMenu(host);}}
        finally{clearTimeout(timer);active.delete(controller);if(pendingKeys.get(requestKey)===controller)pendingKeys.delete(requestKey);button.setAttribute('aria-busy','false');if(menuLoad)menuLoad.disabled=false;updateMediaMenu(host);}
      };
      button.onclick=()=>load();
      if(member&&/^[a-f0-9]{64}$/.test(host.dataset.mediaVersion||'')&&loadedKeys.has(previewKey())&&mediaCache.has(previewKey()))void load(true);
      else void restoreAudio(host,load);
    });
  }
  document.addEventListener('tenant-profile',event=>{const nextPolicy=policy(event.detail),changed=!!profilePolicy&&profilePolicy!==nextPolicy;if(profilePolicy!==nextPolicy)profileRevision++;if(changed){freshProfile=false;forgetAudioIntent();clear(true);}member=event.detail.member;profilePolicy=nextPolicy;if(!member){freshProfile=false;forgetAudioIntent();}else if(changed)refreshAudioProfile();bind();});
  document.addEventListener('tenant-chat-open',event=>{clear();lead=event.detail.lead_id;});
  document.addEventListener('tenant-chat-history-reset',clear);
  document.addEventListener('tenant-chat-close',clear);
  // Operator history only needs preview binding; legacy loaded events carry a different contract.
  document.addEventListener('tenant-chat-attachments-ready',bind);
  document.addEventListener('tenant-chat-loaded',bind);
  document.addEventListener('tenant-chat-history-added',bind);
  document.getElementById('closeModal')?.addEventListener('click',clear);
  document.getElementById('dealModal')?.addEventListener('click',event=>{if(event.target.id==='dealModal')clear();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)bind();});
  let profileFetch;
  function refreshAudioProfile(){
    const revision=profileRevision,expected=profilePolicy;
    const flight=fetch('/api/platform/me',{credentials:'same-origin',cache:'no-store'}).then(response=>response.ok?response.json():null).then(data=>{
      if(revision!==profileRevision&&expected)return;
      if(data?.member){const nextPolicy=policy(data);if(!member&&revision===profileRevision){member=data.member;profilePolicy=nextPolicy;}if(profilePolicy===nextPolicy&&member?.active!==false){freshProfile=true;bind();}}else forgetAudioIntent();
    }).catch(()=>{});
    profileFetch=flight;return flight;
  }
  refreshAudioProfile();
})();
