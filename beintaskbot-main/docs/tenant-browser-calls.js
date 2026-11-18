/* Test WhatsApp calls. Control uses same-origin membership; PCM uses WebRTC.
 * No provider keys, remote iframe, message sends, or local HTTP requests. */
(() => {
  'use strict';
  let identity='',revision=0,polling=false,timer=null,enabled=true,online=false,testNumber='',sources=[],calls=[],session=null,modal=null;
  const pendingRecordings=new Map();
  const sourceOf=route=>sources.find(item=>String(item.channel_id)===String(route?.channel_id))||{online:false};
  let ringContext=null,ringTimer=null,ringing=false;const ringNodes=new Set();
  function stopRing(){ringing=false;clearInterval(ringTimer);ringTimer=null;for(const oscillator of ringNodes){try{oscillator.stop();oscillator.disconnect();}catch{}}ringNodes.clear();}
  function ringPulse(){
    if(!ringing||!identity||key()!==identity||!enabled||ringContext?.state!=='running')return;
    const soundKey='tenant.call-ring.'+identity;
    const play=()=>{if(!ringing)return;try{if(Date.now()-Number(localStorage.getItem(soundKey)||0)<2200)return;localStorage.setItem(soundKey,String(Date.now()));}catch{}
      for(const delay of [0,.3]){const oscillator=ringContext.createOscillator(),gain=ringContext.createGain(),at=ringContext.currentTime+delay;oscillator.type='sine';oscillator.frequency.value=660;gain.gain.setValueAtTime(.0001,at);gain.gain.exponentialRampToValueAtTime(.09,at+.025);gain.gain.exponentialRampToValueAtTime(.0001,at+.22);oscillator.connect(gain);gain.connect(ringContext.destination);ringNodes.add(oscillator);oscillator.onended=()=>{ringNodes.delete(oscillator);oscillator.disconnect();gain.disconnect();};oscillator.start(at);oscillator.stop(at+.24);}};
    if(navigator.locks)void navigator.locks.request(soundKey,{ifAvailable:true},lock=>{if(lock)play();}).catch(()=>{});else play();
  }
  async function unlockRing(){
    if(!identity||!enabled)return;try{const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;ringContext=ringContext||new Audio();await ringContext.resume();if(ringing)ringPulse();if(modal)modal.sound.hidden=(session?.call||modal.call)?.state!=='incoming'||busy()||ringContext.state==='running';}catch{}
  }
  function updateRing(call){
    const shouldRing=enabled&&sourceOf(modal?.route).online&&call?.state==='incoming'&&!call.claimed&&!busy();
    if(!shouldRing){stopRing();return;}if(ringing)return;ringing=true;ringPulse();ringTimer=setInterval(ringPulse,3000);
  }
  window.addEventListener('pointerdown',()=>void unlockRing(),{passive:true});window.addEventListener('keydown',()=>void unlockRing());
  const terminal=call=>!call||['ended','failed'].includes(call.state);
  const busy=()=>!!session&&(session.preparing||session.stopping||!terminal(session.call));
  const profile=()=>window.tenantOperator?.member||window.tenantProfileSnapshot?.member;
  const key=()=>{const p=profile();return p?.tenant_id&&p?.telegram_id?String(p.tenant_id)+':'+p.telegram_id:'';};
  const query=(owner=profile())=>new URLSearchParams({expected_tenant_id:String(owner?.tenant_id||''),expected_user_id:String(owner?.telegram_id||'')});
  const routeOf=r=>({channel_id:String(r.channel_id),conversation_id:String(r.conversation_id),lead_id:Number(r.lead_id)});
  const sameRoute=(a,b)=>a&&b&&['channel_id','conversation_id','lead_id'].every(k=>String(a[k])===String(b[k]));
  const make=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
  const labels={incoming:'Входящий звонок',starting:'Подключение…',ringing:'Вызов…',connected:'Разговор',ending:'Завершаем звонок…',ended:'Звонок завершён',failed:'Звонок не начался',unknown:'Завершение звонка пока не подтверждено'};
  const icon={phone:'<path d="M7 3H4a1 1 0 0 0-1 1c0 9.4 7.6 17 17 17a1 1 0 0 0 1-1v-3l-5-2-2 2a14 14 0 0 1-7-7l2-2-2-5Z"/>',end:'<path d="M3 16v-4c5-5 13-5 18 0v4l-5-1v-3a13 13 0 0 0-8 0v3l-5 1Z"/>',mic:'<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',muted:'<path d="M9 9v3a3 3 0 0 0 5 2M15 9V5a3 3 0 0 0-6 0M5 10v2a7 7 0 0 0 12 5M19 12v-2M12 19v3M8 22h8M3 3l18 18"/>'};
  const buttonIcon=(button,kind,label)=>{button.setAttribute('aria-label',label);button.title=label;button.innerHTML='<span class="browser-call-control-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+icon[kind]+'</svg></span>';button.append(make('span',label,'browser-call-control-label'));};
  const contactName=(route,call)=>{const raw=String(call?.display_name||route?.customer_name||route?.display_name||route?.contact_name||'').trim();if(raw&&!/@(?:s\.whatsapp\.net|g\.us|lid)$/i.test(raw))return raw.slice(0,120);const jid=String(route?.conversation_id||'');return /^[1-9][0-9]{5,21}@s\.whatsapp\.net$/.test(jid)?'+'+jid.split('@')[0]:'Контакт WhatsApp';};
  const initials=name=>name.startsWith('+')||name==='Контакт WhatsApp'?'WA':name.split(/\s+/).filter(Boolean).slice(0,2).map(part=>Array.from(part)[0]).join('').toLocaleUpperCase();
  const style=make('style');style.textContent=`
    .browser-call-button{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:50%;border:1px solid var(--line,#e6eeea);background:transparent;color:inherit;cursor:pointer;flex:none}.browser-call-button svg{width:19px;height:19px}.browser-call-button:disabled{opacity:.45;cursor:default}
    .browser-call-panel{position:fixed;inset:72px auto auto 20px;margin:0;z-index:1200;border:1px solid var(--line,#e6eeea);border-radius:30px;padding:34px 28px 24px;width:min(480px,calc(100vw - 24px));max-height:calc(100dvh - 84px);overflow:auto;box-sizing:border-box;text-align:center;color:var(--ink,#102621);background:linear-gradient(150deg,color-mix(in srgb,var(--brand,#0a926e) 9%,var(--card,#fff)),var(--card,#fff) 62%);box-shadow:0 24px 90px #10262133;font-family:inherit}.browser-call-panel [hidden]{display:none!important}
    .browser-call-panel .browser-call-kicker{margin:4px 35px 12px;color:var(--muted,#698079);font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase}.browser-call-panel h2{margin:0 12px 10px;font-size:clamp(24px,5vw,32px);font-weight:650;line-height:1.2;overflow-wrap:anywhere}.browser-call-panel p{line-height:1.5}.browser-call-state{margin:0;min-height:24px;font-size:14px;color:var(--muted,#698079)}
    .browser-call-close{position:absolute;right:14px;top:14px;width:36px;height:36px;display:grid;place-items:center;border:0!important;border-radius:50%;background:transparent!important;color:var(--muted,#698079);font-size:27px;cursor:pointer}.browser-call-close:hover{background:var(--line,#e6eeea)!important}.browser-call-panel button:focus-visible{outline:3px solid var(--brand,#0a926e);outline-offset:4px}
    .browser-call-avatar{display:grid;place-items:center;width:136px;height:136px;margin:42px auto;border-radius:50%;background:color-mix(in srgb,var(--brand,#0a926e) 12%,var(--card,#fff));color:var(--brand-deep,#086e55);font-size:48px;font-weight:600;letter-spacing:-.04em;box-shadow:0 0 0 14px color-mix(in srgb,var(--brand,#0a926e) 5%,transparent)}.browser-call-panel[data-state=incoming] .browser-call-avatar,.browser-call-panel[data-state=ringing] .browser-call-avatar{animation:browser-call-ring 2s ease-in-out infinite}@keyframes browser-call-ring{50%{box-shadow:0 0 0 23px color-mix(in srgb,var(--brand,#0a926e) 2%,transparent)}}
    .browser-call-actions{display:flex;justify-content:center;gap:28px;margin:24px 0 20px}.browser-call-actions button{display:flex;flex-direction:column;align-items:center;gap:9px;min-width:72px;padding:0;border:0!important;background:transparent!important;color:var(--ink,#102621);cursor:pointer;font:inherit}.browser-call-control-icon{width:58px;height:58px;border-radius:50%;display:grid;place-items:center;background:var(--line,#e6eeea);color:var(--ink,#102621);transition:transform .15s,background .15s}.browser-call-control-icon svg{width:25px;height:25px}.browser-call-control-label{font-size:12px;line-height:1.4}.browser-call-actions .accept .browser-call-control-icon{background:var(--brand-deep,#086e55);color:#fff}.browser-call-actions .end .browser-call-control-icon,.browser-call-actions .reject .browser-call-control-icon{background:#c53645;color:#fff}.browser-call-actions button:hover .browser-call-control-icon{transform:translateY(-2px)}.browser-call-actions button:disabled{opacity:.45;cursor:default}.browser-call-actions .mute[aria-pressed=true] .browser-call-control-icon{background:color-mix(in srgb,var(--brand,#0a926e) 18%,var(--card,#fff));color:var(--brand-deep,#086e55)}
    .browser-call-source{margin:0 0 12px;color:var(--muted,#698079);font-size:13px;font-variant-numeric:tabular-nums}.browser-call-error{margin:16px 0;padding:10px 12px;border-radius:12px;color:var(--danger,#b42318);background:color-mix(in srgb,var(--danger,#b42318) 7%,var(--card,#fff));font-size:13px}.browser-call-error:empty{display:none}
    .browser-call-panel[data-state=incoming]{width:min(380px,calc(100vw - 24px));padding:20px 20px 16px;border-radius:22px;box-shadow:0 12px 45px #10262126}.browser-call-panel[data-state=incoming] .browser-call-kicker{margin:0 28px 6px 0;text-align:left;font-size:10px}.browser-call-panel[data-state=incoming] h2{text-align:left;font-size:20px;margin:0 28px 4px 0}.browser-call-panel[data-state=incoming] .browser-call-state{text-align:left;font-size:12px;min-height:18px}.browser-call-panel[data-state=incoming] .browser-call-avatar{display:none}.browser-call-panel[data-state=incoming] .browser-call-actions{margin:16px 0 10px;gap:32px}.browser-call-panel[data-state=incoming] .browser-call-control-icon{width:46px;height:46px}
    .browser-call-panel[data-state=incoming] .browser-call-source{text-align:left;font-size:12px;margin-bottom:6px}
    @media(max-width:480px){.browser-call-panel{left:12px;right:auto;top:64px;bottom:auto;max-height:calc(100dvh - 76px);padding:30px 20px 20px;border-radius:24px}.browser-call-avatar{margin:36px auto}.browser-call-actions{gap:20px}}@media(prefers-reduced-motion:reduce){.browser-call-panel .browser-call-avatar{animation:none}.browser-call-control-icon{transition:none}}
  `;document.head.append(style);
  async function api(data,owner=profile(),keepalive=false){
    const q=query(owner);if(!data&&session?.request){q.set('request_id',session.request);if(session.stopping)q.set('heartbeat','0');}
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),7000);
    try{const response=await fetch('/api/platform/browser-calls?'+q,{method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,
      headers:data?{'Content-Type':'application/json'}:undefined,body:data?JSON.stringify(data):undefined,keepalive});
      const payload=await response.json();if(!response.ok){const error=new Error(payload.error||'Не удалось подтвердить звонок');error.status=response.status;throw error;}return payload.result;
    }finally{clearTimeout(timeout);}
  }
  function release(s){if(!s)return;clearTimeout(s.connectTimer);s.media?.close();s.media=null;s.release?.();s.release=null;}
  async function stop(s=session,hide=false){
    if(!s){if(hide)closeModal();return;}
    s.stopping=true;release(s);draw();
    try{const result=await api({...s.route,op:'end',call_id:s.call?.id,request_id:s.request},s.owner,true);if(result.state==='not_started'){s.call={state:'failed',recording_state:'none'};s.preparing=false;s.stopping=false;s.closeError='';s.error=s.error||'Звонок отменён до подключения.';draw();}}catch(error){if(session===s&&!terminal(s.call)){s.closeError='Завершение пока не подтверждено. Микрофон выключен; сервер продолжает закрытие звонка.';draw();}}
    if(hide)closeModal();void poll();
  }
  function closeModal(){stopRing();if(modal){clearInterval(modal.clockTimer);modal.node.close();modal.node.remove();modal=null;}if(session&&!busy()){release(session);session=null;}}
  function trackRecording(s,call){
    if(s.recordingTracked||s.identity!==identity)return;s.recordingTracked=true;
    pendingRecordings.set(call.id,{route:s.route,expires:Date.now()+600000});
    if(pendingRecordings.size>20)pendingRecordings.delete(pendingRecordings.keys().next().value);
  }
  function refreshRecordings(){
    let refresh=null;
    for(const [id,entry] of pendingRecordings){const call=calls.find(c=>c.id===id);
      if(entry.expires<=Date.now()||call&&['none','unconfirmed'].includes(call.recording_state)){pendingRecordings.delete(id);continue;}
      if(!call?.recording_id)continue;pendingRecordings.delete(id);
      if(sameRoute(window.tenantOperator?.currentDialog,entry.route))refresh=entry.route;
    }
    if(refresh)void window.tenantOperator.openDialog({...refresh,provider:'connect'},true);
  }
  function updateDuration(m){
    const call=session?.call||m.call;
    if(m.clockCall!==call?.id){clearInterval(m.clockTimer);m.clockTimer=null;m.clockStart=null;m.clockEnd=null;m.clockCall=call?.id;}
    if(call?.state==='connected'&&m.clockStart===null)m.clockStart=performance.now();
    if(m.clockStart!==null&&terminal(call)&&m.clockEnd===null)m.clockEnd=performance.now();
    m.duration.hidden=m.clockStart===null;
    if(m.clockStart!==null){const seconds=Math.max(0,Math.floor(((m.clockEnd??performance.now())-m.clockStart)/1000));m.duration.textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');}
    if(m.clockStart!==null&&m.clockEnd===null&&!m.clockTimer)m.clockTimer=setInterval(()=>{if(modal===m)updateDuration(m);},1000);
    if(m.clockEnd!==null){clearInterval(m.clockTimer);m.clockTimer=null;}
  }
  function show(route,call=null){
    if(busy()&&modal){draw();if(!modal.node.open)modal.node.show();return;}
    if(busy()){route=session.route;call=session.call;}
    closeModal();const node=make('dialog',undefined,'browser-call-panel'),title=make('h2'),state=make('p','','browser-call-state'),error=make('p','','browser-call-error'),kicker=make('p','','browser-call-kicker'),avatar=make('div','','browser-call-avatar');state.setAttribute('aria-live','polite');error.setAttribute('role','alert');avatar.setAttribute('aria-hidden','true');
    node.setAttribute('aria-label','Звонок WhatsApp');const close=make('button','×','browser-call-close');close.setAttribute('aria-label','Закрыть звонок');
    const actions=make('div',undefined,'browser-call-actions'),start=make('button',undefined,'accept'),reject=make('button',undefined,'reject'),end=make('button',undefined,'end'),mute=make('button',undefined,'mute');
    buttonIcon(start,'phone',call?'Принять':'Позвонить');buttonIcon(reject,'end','Отклонить');buttonIcon(end,'end','Завершить');buttonIcon(mute,'mic','Выключить микрофон');mute.setAttribute('aria-pressed','false');
    for(const button of [start,reject,end,mute,close])button.type='button';actions.append(start,mute,reject,end);
    const sourceLine=make('p','','browser-call-source');
    const sound=make('button','Включить звук звонка','browser-call-sound');sound.type='button';sound.hidden=true;sound.onclick=()=>void unlockRing();
    const duration=make('div','','browser-call-duration');duration.hidden=true;duration.setAttribute('role','timer');duration.setAttribute('aria-label','Длительность разговора');duration.setAttribute('aria-live','off');duration.style.cssText='font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:8px';
    node.append(close,kicker,title,sourceLine,state,duration,avatar,error,actions,sound);document.body.append(node);
    modal={node,title,sourceLine,state,duration,clockCall:null,clockStart:null,clockEnd:null,clockTimer:null,error,start,reject,end,mute,kicker,avatar,sound,name:contactName(route,call),route:routeOf(route),call};
    close.onclick=()=>{if(busy())void stop(session,true);else if(modal?.call&&!terminal(modal.call))void rejectCall();else closeModal();};
    node.addEventListener('cancel',event=>{event.preventDefault();close.click();});
    start.onclick=()=>void begin();reject.onclick=()=>void rejectCall();end.onclick=()=>void stop();mute.onclick=()=>{const s=session;if(!s?.media||s.stopping)return;s.muted=!s.muted;s.media.setMuted(s.muted);draw();};
    draw();node.show();
  }
  async function rejectCall(){const m=modal;if(!m?.call)return;try{await api({...m.route,op:'reject',call_id:m.call.id,request_id:crypto.randomUUID()});closeModal();void poll();}catch(e){if(modal===m)m.error.textContent=e.message;}}
  function draw(){
    const m=modal;if(!m)return;const s=session,call=s?.call||m.call,active=!!s&&!terminal(s.call),pending=!!s&&!s.call;
    updateRing(call);updateDuration(m);m.sound.hidden=call?.state!=='incoming'||busy()||ringContext?.state==='running';
    m.kicker.textContent=call?.direction==='inbound'?'Входящий звонок':'Звонок WhatsApp';m.title.textContent=call?.display_name?contactName(m.route,call):m.name;m.avatar.textContent=initials(m.title.textContent);m.node.dataset.state=s?.preparing?'starting':call?.state||'idle';
    const source=sourceOf(m.route);
    m.state.textContent=s?.preparing?'Подключаем микрофон…':s?.stopping&&!terminal(call)?'Микрофон выключен. Завершаем звонок…':call?labels[call.state]||'Проверяем состояние…':source.online?'Готов к звонку':'Источник звонков не подключён к серверу';
    const number=/^[0-9]{5,22}$/.test(source.number||'')?'+'+source.number:'';
    m.sourceLine.textContent=number?'Источник '+number:'';m.sourceLine.hidden=!number;
    m.error.textContent=[s?.error,!terminal(call)&&s?.closeError].filter(Boolean).join(' ');m.start.hidden=active||pending||!!s?.preparing||!!s?.stopping||!!call&&call.state!=='incoming';
    m.start.disabled=!source.online;m.reject.hidden=!call||call.state!=='incoming'||!!s;
    m.end.hidden=!active&&!pending;m.end.disabled=!!s?.stopping;m.mute.hidden=!s?.media||!active||!!s?.stopping;m.mute.setAttribute('aria-pressed',String(!!s?.muted));buttonIcon(m.mute,s?.muted?'muted':'mic',s?.muted?'Включить микрофон':'Выключить микрофон');
  }
  async function media(s){
    if(!window.acquireTenantMicrophone)throw new Error('Захват микрофона недоступен в этом браузере.');
    const unlock=await window.acquireTenantMicrophone();if(typeof unlock!=='function')throw new Error('Микрофон уже используется в другой вкладке или голосовом режиме.');if(s.stopping||key()!==s.identity){unlock();throw new Error('Звонок отменён');}s.release=unlock;
    const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    if(s.stopping||key()!==s.identity){stream.getTracks().forEach(t=>t.stop());throw new Error('Звонок отменён');}
    const managed=s.transport==='websocket',pc=managed?null:new RTCPeerConnection({iceServers:[]});
    let dc=pc?.createDataChannel('pcm',{ordered:true})||null,closed=false;
    const ctx=new AudioContext({sampleRate:16000});if(dc)dc.binaryType='arraybuffer';
    const handle={pc,setMuted(muted){stream.getAudioTracks().forEach(track=>{track.enabled=!muted;});},close(){if(closed)return;closed=true;stream.getTracks().forEach(t=>t.stop());dc?.close();pc?.close();void ctx.close().catch(()=>{});}};s.media=handle;
    try{
      await ctx.audioWorklet.addModule('/assets/browser-call-audio.js');await ctx.resume();
      const capture=new AudioWorkletNode(ctx,'bein-call-capture'),playback=new AudioWorkletNode(ctx,'bein-call-playback');
      ctx.createMediaStreamSource(stream).connect(capture);capture.connect(ctx.destination);playback.connect(ctx.destination);
      capture.port.onmessage=e=>{if(closed||!dc||dc.readyState!==(managed?WebSocket.OPEN:'open'))return;if(dc.bufferedAmount>65536){void stop(s);return;}const floats=e.data,pcm=new Int16Array(floats.length);for(let i=0;i<floats.length;i++)pcm[i]=Math.round(Math.max(-1,Math.min(1,floats[i]))*32767);dc.send(pcm.buffer);s.pcmSent=true;};
      const onmessage=e=>{if(closed||!(e.data instanceof ArrayBuffer)||e.data.byteLength>6400||e.data.byteLength%2)return;const raw=new Int16Array(e.data),floats=new Float32Array(raw.length);for(let i=0;i<raw.length;i++)floats[i]=raw[i]/32768;playback.port.postMessage(floats,[floats.buffer]);};
      if(managed){
        handle.connect=()=>new Promise((resolve,reject)=>{
          if(closed||s.stopping||key()!==s.identity||!s.call?.id){reject(new Error('Звонок отменён'));return;}
          const url=new URL('/api/platform/browser-calls/'+encodeURIComponent(s.call.id)+'/audio',location.origin);url.protocol=location.protocol==='https:'?'wss:':'ws:';
          const parameters=query(s.owner);parameters.set('request_id',s.request);url.search=parameters.toString();
          dc=new WebSocket(url);dc.binaryType='arraybuffer';dc.onmessage=onmessage;
          const timeout=setTimeout(()=>{reject(new Error('Не удалось подключить звук'));dc.close();},7000);
          dc.onopen=()=>{clearTimeout(timeout);if(closed||s.stopping||key()!==s.identity){dc.close();reject(new Error('Звонок отменён'));}else resolve();};
          dc.onerror=()=>{clearTimeout(timeout);reject(new Error('Не удалось подключить звук'));};
          dc.onclose=()=>{clearTimeout(timeout);reject(new Error('Аудиосоединение закрыто'));if(!closed&&!s.stopping)void stop(s);};
        });
        if(closed||s.stopping)throw new Error('Звонок отменён');return '';
      }
      dc.onmessage=onmessage;
      pc.onconnectionstatechange=()=>{if(!closed&&['failed','closed'].includes(pc.connectionState))void stop(s);};
      const offer=await pc.createOffer();await pc.setLocalDescription(offer);
      await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{pc.removeEventListener('icegatheringstatechange',done);reject(new Error('Не удалось подготовить аудиосоединение.'));},10000);function done(){if(pc.iceGatheringState==='complete'){clearTimeout(timeout);pc.removeEventListener('icegatheringstatechange',done);resolve();}}pc.addEventListener('icegatheringstatechange',done);done();});
      if(closed||s.stopping)throw new Error('Звонок отменён');return pc.localDescription.sdp;
    }catch(e){handle.close();throw e;}
  }
  async function begin(){
    // Recording follows the configured owner policy. Starting/accepting remains
    // an explicit user click, never an automatic incoming claim.
    const m=modal;if(!m||!sourceOf(m.route).online||busy())return;
    const s={route:m.route,call:m.call,transport:sourceOf(m.route).media||'webrtc',request:crypto.randomUUID(),owner:{...profile()},identity:key(),preparing:true,stopping:false,error:''};session=s;draw();
    try{
      const offer=await media(s);if(s.stopping||session!==s||key()!==s.identity){release(s);return;}
      s.sent=true;const call=await api({...s.route,op:m.call?'accept':'start',call_id:m.call?.id,request_id:s.request,media:s.transport,sdp_offer:offer,consent_confirmed:true},s.owner);
      s.call=call;s.preparing=false;if(s.stopping||session!==s||key()!==s.identity){void stop(s);return;}
      s.connectTimer=setTimeout(()=>{if(session===s&&!s.stopping&&!terminal(s.call)&&(!s.pcmSent||s.call.direction==='inbound'&&s.call.state!=='connected')){s.error='Не удалось подтвердить подключение звука. Завершаем попытку; после подтверждения можно повторить.';void stop(s);}},20000);draw();void poll();
    }catch(e){release(s);s.preparing=false;s.error=e.message;if(s.sent||s.call&&!terminal(s.call)){if(!s.call)s.call={state:'unknown'};void stop(s);}else{s.call={state:'failed',recording_state:'none'};draw();}}
  }
  async function poll(){
    if(polling||!identity||!enabled)return;polling=true;const generation=revision;
    try{
      const result=await api();if(generation!==revision||key()!==identity)return;
      enabled=result.enabled===true;online=result.online===true;testNumber=/^[0-9]{5,22}$/.test(result.test_number||'')?result.test_number:'';calls=result.calls||[];
      sources=Array.isArray(result.sources)?result.sources:[];
      const s=session;
      if(s&&!s.call?.id){const recovered=calls.find(c=>c.request_matches&&sameRoute(c,s.route));if(recovered)s.call=recovered;}
      if(s?.call?.id){
        const call=calls.find(c=>c.id===s.call.id);
        if(call){s.call=call;if(call.state==='connected')clearTimeout(s.connectTimer);if(!terminal(call)&&(call.sdp_answer||call.audio_ready)&&s.media&&!s.answered&&!s.stopping){s.answered=true;try{if(s.transport==='websocket')await s.media.connect();else await s.media.pc.setRemoteDescription({type:'answer',sdp:call.sdp_answer});}catch{ s.error='Не удалось подключить звук.';void stop(s);}}
          if(terminal(call)){release(s);s.stopping=false;s.preparing=false;s.closeError='';trackRecording(s,call);if(session===s&&call.state==='ended'&&!s.error)closeModal();}
          if(call.error_code&&!terminal(call)&&!s.stopping){s.error='Не удалось подключить звонок. Завершаем соединение.';void stop(s);}}
      }
      const incoming=calls.find(c=>c.state==='incoming'&&!c.claimed);
      if(incoming&&!busy()&&(!modal||terminal(session?.call||modal.call))){closeModal();session=null;show(incoming,incoming);}
      if(modal?.call&&!session){const fresh=calls.find(c=>c.id===modal.call.id);if(fresh){modal.call=fresh;if(fresh.claimed||terminal(fresh))closeModal();}else closeModal();}
      refreshRecordings();mount();draw();
    }catch(e){online=false;sources=sources.map(source=>({...source,online:false}));if(e.status===403){enabled=false;release(session);session=null;pendingRecordings.clear();closeModal();}else {if(session&&!session.stopping)void stop(session);if(modal)modal.error.textContent='Связь управления потеряна. Микрофон выключен; проверяем завершение звонка…';}draw();}
    finally{polling=false;schedule();}
  }
  function schedule(){clearTimeout(timer);if(identity&&enabled)timer=setTimeout(()=>void poll(),2000);}
  function mount(){
    const host=document.getElementById('operatorBrowserCallHost'),route=window.tenantOperator?.currentDialog;if(!host)return;
    const routeKey=route?identity+':'+[route.channel_id,route.conversation_id,route.lead_id].join(':'):'';
    if(host.dataset.routeKey===routeKey&&host.firstElementChild&&route?.browser_call_available)return;
    host.replaceChildren();host.dataset.routeKey=routeKey;if(!route?.browser_call_available)return;
    const button=make('button',undefined,'browser-call-button');button.type='button';button.title='Звонок WhatsApp';button.setAttribute('aria-label','Звонок WhatsApp');
    button.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M7 3H4a1 1 0 0 0-1 1c0 9.4 7.6 17 17 17a1 1 0 0 0 1-1v-3l-5-2-2 2a14 14 0 0 1-7-7l2-2-2-5Z"/></svg>';
    button.onclick=()=>{if(session&&terminal(session.call)){release(session);session=null;}show(route);};host.append(button);
  }
  function changed(){const fresh=key();if(fresh!==identity){revision++;if(session){void stop(session);release(session);session=null;}closeModal();pendingRecordings.clear();identity=fresh;enabled=true;calls=[];sources=[];online=false;}mount();void poll();}
  document.addEventListener('tenant-profile',changed);document.addEventListener('tenant-chat-attachments-ready',mount);
  document.addEventListener('tenant-calls-updated',event=>{enabled=true;const route=window.tenantOperator?.currentDialog;if(route&&String(route.channel_id)===String(event.detail?.channel_id)&&!String(route.conversation_id).endsWith('@g.us'))route.browser_call_available=true;mount();void poll();});
  document.addEventListener('tenant-auth-invalid',()=>{revision++;clearTimeout(timer);release(session);session=null;identity='';sources=[];pendingRecordings.clear();enabled=false;closeModal();});
  window.addEventListener('pagehide',()=>{stopRing();clearTimeout(timer);clearInterval(modal?.clockTimer);if(busy())void stop(session);release(session);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void poll();});
  changed();
})();
