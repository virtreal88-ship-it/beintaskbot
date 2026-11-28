/* Test WhatsApp calls. Control uses same-origin membership; PCM uses WebRTC.
 * No provider keys, remote iframe, message sends, or local HTTP requests. */
(() => {
  'use strict';
  let identity='',revision=0,polling=false,timer=null,enabled=true,online=false,testNumber='',sources=[],calls=[],session=null,modal=null;
  const pendingRecordings=new Map();
  const sourceOf=route=>sources.find(item=>String(item.channel_id)===String(route?.channel_id))||{online:false};
  let ringContext=null,ringTimer=null,ringing=false;const ringNodes=new Set();
  function stopTones(nodes){
    for(const oscillator of nodes){try{oscillator.stop();oscillator.disconnect();}catch{}}
    nodes.clear();
  }
  function playTones(notes,nodes,peak,type){
    for(const [frequency,delay,duration] of notes){
      const tone=ringContext.createOscillator(),gain=ringContext.createGain();
      const at=ringContext.currentTime+delay;
      tone.type=type;tone.frequency.value=frequency;
      gain.gain.setValueAtTime(.0001,at);
      gain.gain.exponentialRampToValueAtTime(peak,at+.025);
      gain.gain.exponentialRampToValueAtTime(.0001,at+duration);
      tone.connect(gain);gain.connect(ringContext.destination);nodes.add(tone);
      tone.onended=()=>{nodes.delete(tone);tone.disconnect();gain.disconnect();};
      tone.start(at);tone.stop(at+duration+.01);
    }
  }
  function stopRing(){ringing=false;clearInterval(ringTimer);ringTimer=null;stopTones(ringNodes);}
  function ringPulse(){
    if(!ringing||!identity||key()!==identity||!enabled||ringContext?.state!=='running')return;
    const soundKey='tenant.call-ring.'+identity;
    const play=()=>{
      if(!ringing||ringContext?.state!=='running')return;
      try{if(Date.now()-Number(localStorage.getItem(soundKey)||0)<2200)return;localStorage.setItem(soundKey,String(Date.now()));}catch{}
      playTones([[660,0,.24],[880,.24,.24],[740,.48,.34]],ringNodes,.18,'triangle');
    };
    if(navigator.locks)void navigator.locks.request(soundKey,{ifAvailable:true},lock=>{if(lock)play();}).catch(()=>{});else play();
  }
  async function unlockRing(){
    if(!identity||!enabled)return;
    try{
      const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;
      ringContext=ringContext||new Audio();
      const wasRunning=ringContext.state==='running';
      await ringContext.resume();
      if(ringing)ringPulse();
      if(!wasRunning&&ringbackSession)playRingback(ringbackSession);
      if(modal)modal.sound.hidden=(session?.call||modal.call)?.state!=='incoming'||busy()||ringContext.state==='running';
    }catch{}
  }
  function updateRing(call){
    const shouldRing=enabled&&sourceOf(modal?.route).online&&call?.state==='incoming'&&!call.claimed&&!busy();
    if(!shouldRing){stopRing();return;}if(ringing)return;ringing=true;ringPulse();ringTimer=setInterval(ringPulse,3000);
  }
  let ringbackSession=null,ringbackTimer=null;
  const ringbackNodes=new Set();
  function stopRingback(){
    ringbackSession=null;clearInterval(ringbackTimer);ringbackTimer=null;
    stopTones(ringbackNodes);
  }
  function canPlayRingback(s){
    return !!s&&session===s&&s.identity===key()&&enabled&&sourceOf(s.route).online&&
      !s.stopping&&!s.preparing&&!s.error&&s.call?.direction==='outbound'&&
      s.call.state==='ringing';
  }
  function playRingback(s){
    if(ringbackSession!==s||!canPlayRingback(s)||ringContext?.state!=='running')return;
    // Local ringback. It is routed only to the speakers, never into call media.
    playTones([[440,0,.25],[440,.31,.25]],ringbackNodes,.065,'sine');
  }
  function updateRingback(s){
    if(!canPlayRingback(s)){stopRingback();return;}
    if(ringbackSession===s)return;
    stopRingback();ringbackSession=s;
    playRingback(s);ringbackTimer=setInterval(()=>playRingback(s),3000);
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
  const contactName=(route,call)=>{const raw=String(call?.display_name||route?.customer_name||route?.display_name||route?.contact_name||'').trim();return raw&&!/@(?:s\.whatsapp\.net|g\.us|lid)$/i.test(raw)?raw.slice(0,120):'Контакт WhatsApp';};
  const contactPhone=(route,call)=>{const phone=String(call?.phone||route?.phone||'');if(/^\+?[1-9][0-9]{6,14}$/.test(phone))return '+'+phone.replace(/^\+/,'');const jid=String(route?.conversation_id||'');return /^[1-9][0-9]{6,14}@s\.whatsapp\.net$/.test(jid)?'+'+jid.split('@')[0]:'';};
  const initials=name=>name.startsWith('+')||name==='Контакт WhatsApp'?'WA':name.split(/\s+/).filter(Boolean).slice(0,2).map(part=>Array.from(part)[0]).join('').toLocaleUpperCase();
  const style=make('style');style.textContent=`
    .browser-call-button{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:50%;border:1px solid var(--line,#e6eeea);background:transparent;color:inherit;cursor:pointer;flex:none}.browser-call-button svg{width:19px;height:19px}.browser-call-button:disabled{opacity:.45;cursor:default}
    .browser-call-panel {
      position:fixed; inset:72px auto auto 20px; z-index:1200; margin:0;
      width:min(380px,calc(100vw - 24px)); max-height:calc(100dvh - 24px);
      padding:8px; box-sizing:border-box; overflow:auto; border:1px solid #34514a;
      border-radius:20px; background:#132621; color:#f5faf8;
      box-shadow:0 12px 38px #071d165c; font:inherit;
    }
    .browser-call-panel [hidden] { display:none!important }
    html[data-app-theme=dark] .browser-call-panel { color:var(--ink,#f5faf8) }
    .browser-call-header {
      display:grid; grid-template-columns:46px minmax(0,1fr) 28px 38px;
      align-items:center; gap:6px; padding:8px;
      border-radius:13px; background:#213832; cursor:grab; touch-action:none; user-select:none;
    }
    .browser-call-panel[data-dragging=true] .browser-call-header { cursor:grabbing }
    .browser-call-copy { min-width:0 }
    .browser-call-panel h2 {
      margin:2px 0; color:inherit; font-size:16px; line-height:1.25;
      font-weight:650; overflow-wrap:anywhere;
    }
    .browser-call-kicker, .browser-call-state, .browser-call-phone {
      margin:0; color:#bfd3cb; font-size:12px; line-height:1.4;
    }
    .browser-call-kicker { font-size:10px; text-transform:uppercase; letter-spacing:.04em }
    .browser-call-phone, .browser-call-duration { font-variant-numeric:tabular-nums }
    .browser-call-status { display:flex; gap:8px; flex-wrap:wrap; align-items:center }
    .browser-call-duration { font-size:12px; font-weight:650; color:#a7edce }
    .browser-call-avatar {
      display:grid; place-items:center; width:44px; height:44px; border-radius:50%;
      background:#d8efe6; color:#105d49; font-size:16px; font-weight:700;
    }
    .browser-call-panel .browser-call-grip, .browser-call-panel .browser-call-close {
      align-self:start; width:100%; min-height:38px; padding:0; border:0;
      border-radius:9px; background:transparent; color:#f5faf8; line-height:1;
    }
    .browser-call-grip { font-size:20px; cursor:grab }
    .browser-call-close { font-size:24px; cursor:pointer }
    .browser-call-panel .browser-call-close:hover, .browser-call-panel .browser-call-grip:hover { background:#34514a }
    .browser-call-panel button:focus-visible { outline:2px solid #a7edce; outline-offset:2px }
    .browser-call-actions { display:flex; justify-content:flex-end; gap:8px }
    .browser-call-actions:has(button:not([hidden])) { padding:7px 2px 0 }
    .browser-call-actions button {
      display:flex; align-items:center; justify-content:center; gap:6px;
      min-width:44px; min-height:44px; padding:0 12px; border:0;
      border-radius:14px; background:#29433a; color:#f5faf8; cursor:pointer; font:inherit;
    }
    .browser-call-actions button:hover { filter:brightness(1.14) }
    .browser-call-actions button:disabled { opacity:.5; cursor:default }
    .browser-call-control-icon { display:grid; place-items:center }
    .browser-call-control-icon svg { width:20px; height:20px }
    .browser-call-control-label { font-size:12px; line-height:1.2 }
    .browser-call-actions .mute { margin-right:auto; padding:0 }
    .browser-call-actions .mute .browser-call-control-label { display:none }
    .browser-call-actions .mute[aria-pressed=true] { background:#c7ebd9; color:#133c30 }
    .browser-call-actions .accept { background:#147653 }
    .browser-call-actions .end, .browser-call-actions .reject { background:#b72940 }
    .browser-call-error {
      margin:6px 2px 0; padding:8px; border-radius:8px;
      color:#ffe2e8; background:#572a36; font-size:12px; line-height:1.4;
    }
    .browser-call-error:empty { display:none }
    .browser-call-panel .browser-call-sound {
      margin:6px 2px 0; min-height:38px; padding:4px 8px;
      border:0; border-radius:8px; background:#29433a; color:#f5faf8; font-size:12px;
    }
    @media(max-width:480px) {
      .browser-call-panel { left:12px; top:64px }
      .browser-call-header { grid-template-columns:44px minmax(0,1fr) 32px 44px }
      .browser-call-panel .browser-call-close, .browser-call-panel .browser-call-grip { min-height:44px }
    }
  `;document.head.append(style);
  async function api(data,owner=profile(),keepalive=false){
    const q=query(owner);if(!data&&session?.request){q.set('request_id',session.request);if(session.stopping)q.set('heartbeat','0');}
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),7000);
    try{const response=await fetch('/api/platform/browser-calls?'+q,{method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,
      headers:data?{'Content-Type':'application/json'}:undefined,body:data?JSON.stringify(data):undefined,keepalive});
      const payload=await response.json();if(!response.ok){const error=new Error(payload.error||'Не удалось подтвердить звонок');error.status=response.status;error.code=payload.code;throw error;}return payload.result;
    }finally{clearTimeout(timeout);}
  }
  function release(s){if(!s)return;clearTimeout(s.connectTimer);clearInterval(s.diagnosticTimer);s.media?.close();s.media=null;s.release?.();s.release=null;}
  function diagnostic(s,reason='ongoing'){
    s.diagnostics??={version:1,seq:0,context_suspensions:0};
    const d=s.diagnostics;
    if(reason!=='ongoing'&&!d.reason)d.reason=reason;
    return {...d,reason:d.reason||reason,seq:++d.seq,at_ms:Date.now()};
  }
  function reportDiagnostic(s,reason='ongoing'){
    if(!s?.call?.id)return;
    void api({...s.route,op:'diagnostics',call_id:s.call.id,request_id:s.request,
              diagnostics:diagnostic(s,reason)},s.owner,true).catch(()=>{});
  }
  async function stop(s=session,hide=false){
    if(!s){if(hide)closeModal();return;}
    const closingModal=modal;
    s.stopping=true;const evidence=diagnostic(s,'user_end');release(s);draw();
    try{const result=await api({...s.route,op:'end',call_id:s.call?.id,request_id:s.request,diagnostics:evidence},s.owner,true);if(result.state==='not_started'){s.call={state:'failed',recording_state:'none'};s.preparing=false;s.stopping=false;s.closeError='';s.error=s.error||'Звонок отменён до подключения.';draw();}}catch(error){if(session===s&&!terminal(s.call)){s.closeError='Завершение пока не подтверждено. Микрофон выключен; сервер продолжает закрытие звонка.';draw();}}
    if(hide&&session===s&&modal===closingModal)closeModal();void poll();
  }
  function closeModal(){stopRing();stopRingback();if(modal){clearInterval(modal.clockTimer);modal.cleanupDrag();modal.node.close();modal.node.remove();modal=null;}if(session&&!busy()){release(session);session=null;}}
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
  function installCallDrag(node,header,grip){
    let drag=null,position=null;
    const viewport=window.visualViewport;
    function move(x,y){
      const bounds=node.getBoundingClientRect(),padding=8;
      const left=(viewport?.offsetLeft||0)+padding,top=(viewport?.offsetTop||0)+padding;
      const right=left+Math.min(viewport?.width||innerWidth,innerWidth)-bounds.width-2*padding;
      const bottom=top+Math.min(viewport?.height||innerHeight,innerHeight)-bounds.height-2*padding;
      position={x:Math.max(left,Math.min(x,right)),y:Math.max(top,Math.min(y,bottom))};
      node.style.left=position.x+'px';node.style.top=position.y+'px';
    }
    function clamp(){
      if(!node.open)return;
      const rect=node.getBoundingClientRect();
      if(position||rect.right>innerWidth||rect.bottom>innerHeight)move(rect.left,rect.top);
    }
    header.addEventListener('pointerdown',event=>{
      if(drag||!event.isPrimary||event.button!==0||event.target.closest('button:not(.browser-call-grip)'))return;
      const rect=node.getBoundingClientRect();
      drag={id:event.pointerId,x:event.clientX-rect.left,y:event.clientY-rect.top};
      header.setPointerCapture(event.pointerId);node.dataset.dragging='true';
    });
    header.addEventListener('pointermove',event=>{
      if(drag?.id===event.pointerId)move(event.clientX-drag.x,event.clientY-drag.y);
    });
    for(const type of ['pointerup','pointercancel','lostpointercapture']){
      header.addEventListener(type,event=>{
        if(drag?.id!==event.pointerId)return;
        drag=null;delete node.dataset.dragging;
      });
    }
    const arrows={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
    grip.addEventListener('keydown',event=>{
      const step=arrows[event.key];if(!step)return;
      event.preventDefault();
      const rect=node.getBoundingClientRect();
      move(rect.left+step[0]*16,rect.top+step[1]*16);
    });
    const observer=new ResizeObserver(clamp);
    observer.observe(node);
    window.addEventListener('resize',clamp);
    viewport?.addEventListener('resize',clamp);
    return ()=>{
      observer.disconnect();window.removeEventListener('resize',clamp);
      viewport?.removeEventListener('resize',clamp);
    };
  }
  function show(route,call=null){
    if(busy()&&modal){draw();if(!modal.node.open)modal.node.show();return;}
    if(busy()){route=session.route;call=session.call;}
    closeModal();const node=make('dialog',undefined,'browser-call-panel'),title=make('h2'),state=make('p','','browser-call-state'),error=make('p','','browser-call-error'),kicker=make('p','','browser-call-kicker'),avatar=make('div','','browser-call-avatar');state.setAttribute('aria-live','polite');error.setAttribute('role','alert');avatar.setAttribute('aria-hidden','true');
    node.setAttribute('aria-label','Звонок WhatsApp');const close=make('button','×','browser-call-close');close.setAttribute('aria-label','Закрыть звонок');
    const actions=make('div',undefined,'browser-call-actions'),start=make('button',undefined,'accept'),reject=make('button',undefined,'reject'),end=make('button',undefined,'end'),mute=make('button',undefined,'mute');
    buttonIcon(start,'phone',call?'Принять':'Позвонить');buttonIcon(reject,'end','Отклонить');buttonIcon(end,'end','Завершить');buttonIcon(mute,'mic','Выключить микрофон');mute.setAttribute('aria-pressed','false');
    for(const button of [start,reject,end,mute,close])button.type='button';actions.append(start,mute,reject,end);
    const phoneLine=make('p','','browser-call-phone');
    const sound=make('button','Включить звук звонка','browser-call-sound');sound.type='button';sound.hidden=true;sound.onclick=()=>void unlockRing();
    const duration=make('div','','browser-call-duration');duration.hidden=true;duration.setAttribute('role','timer');duration.setAttribute('aria-label','Длительность разговора');duration.setAttribute('aria-live','off');
    const header=make('header',undefined,'browser-call-header'),copy=make('div',undefined,'browser-call-copy'),status=make('div',undefined,'browser-call-status');
    const grip=make('button','⠿','browser-call-grip');grip.type='button';grip.setAttribute('aria-label','Переместить окно звонка');grip.title='Перетащите окно или нажмите клавиши со стрелками';
    status.append(state,duration);copy.append(kicker,title,phoneLine,status);header.append(avatar,copy,grip,close);
    node.append(header,error,actions,sound);document.body.append(node);
    const cleanupDrag=installCallDrag(node,header,grip);
    modal={node,title,phoneLine,state,duration,clockCall:null,clockStart:null,clockEnd:null,clockTimer:null,error,start,reject,end,mute,kicker,avatar,sound,cleanupDrag,name:contactName(route,call),route:routeOf(route),phone:contactPhone(route,call),call};
    close.onclick=()=>{if(busy())void stop(session,true);else if(modal?.call&&!terminal(modal.call))void rejectCall();else closeModal();};
    node.addEventListener('cancel',event=>{event.preventDefault();close.click();});
    start.onclick=()=>void begin();reject.onclick=()=>void rejectCall();end.onclick=()=>void stop();mute.onclick=()=>{const s=session;if(!s?.media||s.stopping)return;s.muted=!s.muted;s.media.setMuted(s.muted);draw();};
    draw();node.show();
  }
  async function rejectCall(){const m=modal;if(!m?.call)return;try{await api({...m.route,op:'reject',call_id:m.call.id,request_id:crypto.randomUUID()});closeModal();void poll();}catch(e){if(modal===m)m.error.textContent=e.message;}}
  function draw(){
    const m=modal;if(!m)return;const s=session,call=s?.call||m.call,active=!!s&&!terminal(s.call),pending=!!s&&!s.call;
    updateRing(call);updateRingback(s);updateDuration(m);m.sound.hidden=call?.state!=='incoming'||busy()||ringContext?.state==='running';
    if(call?.display_name)m.name=contactName(m.route,call);
    m.kicker.textContent=call?.direction==='inbound'?'Входящий звонок':'Звонок WhatsApp';
    m.kicker.hidden=call?.state==='incoming'&&!s?.preparing;
    m.title.textContent=m.name;m.avatar.textContent=initials(m.title.textContent);m.node.dataset.state=s?.preparing?'starting':call?.state||'idle';
    const source=sourceOf(m.route);
    m.state.textContent=s?.preparing?'Подключаем микрофон…':s?.stopping&&!terminal(call)?'Микрофон выключен. Завершаем звонок…':call?labels[call.state]||'Проверяем состояние…':source.online?'Готов к звонку':'Источник звонков не подключён к серверу';
    // Only a phone-number JID is a customer phone; numeric LIDs are opaque IDs.
    const number=contactPhone(m.route,call)||m.phone;
    if(number)m.phone=number;
    m.phoneLine.textContent=number;m.phoneLine.hidden=!number;
    m.error.textContent=[s?.error,!terminal(call)&&s?.controlWarning,!terminal(call)&&s?.closeError].filter(Boolean).join(' ');m.start.hidden=active||pending||!!s?.preparing||!!s?.stopping||!!call&&call.state!=='incoming';
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
    s.diagnostics={version:1,seq:0,context_suspensions:0};
    ctx.onstatechange=()=>{if(!closed&&ctx.state==='suspended')s.diagnostics.context_suspensions++;};
    const handle={pc,setMuted(muted){stream.getAudioTracks().forEach(track=>{track.enabled=!muted;});},close(){if(closed)return;closed=true;stream.getTracks().forEach(t=>t.stop());dc?.close(s.mediaCloseCode||1000);pc?.close();void ctx.close().catch(()=>{});}};s.media=handle;
    try{
      await ctx.audioWorklet.addModule('/assets/browser-call-audio.js');await ctx.resume();
      const capture=new AudioWorkletNode(ctx,'bein-call-capture'),playback=new AudioWorkletNode(ctx,'bein-call-playback');
      capture.port.postMessage('bounded');capture.port.postMessage('diagnostics');playback.port.postMessage('diagnostics');
      playback.port.onmessage=e=>{if(e.data?.diagnostics){s.diagnostics.playback_underrun_samples=e.data.playback_underrun_samples;s.diagnostics.playback_overflow_samples=e.data.playback_overflow_samples;}};
      ctx.createMediaStreamSource(stream).connect(capture);capture.connect(ctx.destination);playback.connect(ctx.destination);
      capture.port.onmessage=e=>{if(e.data?.diagnostics){s.diagnostics.capture_dropped_samples=e.data.capture_dropped_samples;return;}try{if(closed||!dc||dc.readyState!==(managed?WebSocket.OPEN:'open'))return;if(dc.bufferedAmount>65536){s.mediaCloseCode=4001;diagnostic(s,'audio_backpressure');void stop(s);return;}const floats=e.data,pcm=new Int16Array(floats.length);for(let i=0;i<floats.length;i++)pcm[i]=Math.round(Math.max(-1,Math.min(1,floats[i]))*32767);dc.send(pcm.buffer);s.pcmSent=true;}finally{capture.port.postMessage('consumed');}};
      const onmessage=e=>{if(closed||!(e.data instanceof ArrayBuffer)||e.data.byteLength>6400||e.data.byteLength%2)return;const now=Date.now(),metrics=s.diagnostics.worker_to_browser??={frames:0,gaps_over_200ms:0,max_gap_ms:0,last_gap_at_ms:0,last_frame_at_ms:0};
        if(metrics.last_frame_at_ms){const gap=now-metrics.last_frame_at_ms;if(gap>200){metrics.gaps_over_200ms++;metrics.max_gap_ms=Math.max(metrics.max_gap_ms,gap);metrics.last_gap_at_ms=now;}}
        metrics.frames++;metrics.last_frame_at_ms=now;
        const raw=new Int16Array(e.data),floats=new Float32Array(raw.length);for(let i=0;i<raw.length;i++)floats[i]=raw[i]/32768;playback.port.postMessage(floats,[floats.buffer]);};
      if(managed){
        handle.connect=()=>new Promise((resolve,reject)=>{
          if(closed||s.stopping||key()!==s.identity||!s.call?.id){reject(new Error('Звонок отменён'));return;}
          const url=new URL('/api/platform/browser-calls/'+encodeURIComponent(s.call.id)+'/audio',location.origin);url.protocol=location.protocol==='https:'?'wss:':'ws:';
          const parameters=query(s.owner);parameters.set('request_id',s.request);url.search=parameters.toString();
          dc=new WebSocket(url);dc.binaryType='arraybuffer';dc.onmessage=onmessage;
          const timeout=setTimeout(()=>{reject(new Error('Не удалось подключить звук'));dc.close();},7000);
          dc.onopen=()=>{clearTimeout(timeout);if(closed||s.stopping||key()!==s.identity){dc.close();reject(new Error('Звонок отменён'));}else resolve();};
          dc.onerror=()=>{clearTimeout(timeout);reject(new Error('Не удалось подключить звук'));};
          dc.onclose=event=>{if(!closed){s.diagnostics.close_code=[1000,1001,1002,1003,1006,1007,1008,1009,1010,1011,1012,1013,1014,1015,4001].includes(event?.code)?event.code:0;diagnostic(s,'audio_closed');}clearTimeout(timeout);reject(new Error('Аудиосоединение закрыто'));if(!closed&&!s.stopping)void stop(s);};
        });
        if(closed||s.stopping)throw new Error('Звонок отменён');return '';
      }
      dc.onmessage=onmessage;
      pc.onconnectionstatechange=()=>{if(!closed&&['failed','closed'].includes(pc.connectionState)){diagnostic(s,'connection_failed');void stop(s);}};
      const offer=await pc.createOffer();await pc.setLocalDescription(offer);
      await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{pc.removeEventListener('icegatheringstatechange',done);reject(new Error('Не удалось подготовить аудиосоединение.'));},10000);function done(){if(pc.iceGatheringState==='complete'){clearTimeout(timeout);pc.removeEventListener('icegatheringstatechange',done);resolve();}}pc.addEventListener('icegatheringstatechange',done);done();});
      if(closed||s.stopping)throw new Error('Звонок отменён');return pc.localDescription.sdp;
    }catch(e){handle.close();throw e;}
  }
  async function begin(){
    // Recording follows the configured owner policy. Starting/accepting remains
    // an explicit user click, never an automatic incoming claim.
    const m=modal;if(!m||!sourceOf(m.route).online||busy())return;
    void unlockRing();
    const s={route:m.route,call:m.call,transport:sourceOf(m.route).media||'webrtc',request:crypto.randomUUID(),owner:{...profile()},identity:key(),preparing:true,stopping:false,error:''};session=s;draw();
    try{
      const offer=await media(s);if(s.stopping||session!==s||key()!==s.identity){release(s);return;}
      s.sent=true;const call=await api({...s.route,op:m.call?'accept':'start',call_id:m.call?.id,request_id:s.request,media:s.transport,sdp_offer:offer,consent_confirmed:true},s.owner);
      s.call=call;s.diagnosticTimer=setInterval(()=>reportDiagnostic(s),10000);s.preparing=false;if(s.stopping||session!==s||key()!==s.identity){void stop(s);return;}
      s.connectTimer=setTimeout(()=>{if(session===s&&!s.stopping&&!terminal(s.call)&&(!s.pcmSent||s.call.direction==='inbound'&&s.call.state!=='connected')){diagnostic(s,'connect_timeout');s.error='Не удалось подтвердить подключение звука. Завершаем попытку; после подтверждения можно повторить.';void stop(s);}},20000);draw();void poll();
    }catch(e){diagnostic(s,s.sent?'control_unavailable':'audio_setup_failed');release(s);s.preparing=false;s.error=e.message;if(s.sent||s.call&&!terminal(s.call)){if(!s.call)s.call={state:'unknown'};void stop(s);}else{s.call={state:'failed',recording_state:'none'};draw();}}
  }
  async function poll(){
    if(polling||!identity||!enabled)return;polling=true;const generation=revision;
    try{
      const result=await api();if(generation!==revision||key()!==identity)return;
      enabled=result.enabled===true;online=result.online===true;testNumber=/^[0-9]{5,22}$/.test(result.test_number||'')?result.test_number:'';calls=result.calls||[];
      sources=Array.isArray(result.sources)?result.sources:[];
      const s=session;if(s){s.controlBusySince=null;s.controlWarning='';}
      if(s&&!s.call?.id){const recovered=calls.find(c=>c.request_matches&&sameRoute(c,s.route));if(recovered)s.call=recovered;}
      if(s?.call?.id){
        const call=calls.find(c=>c.id===s.call.id);
        if(call){s.call=call;updateRingback(s);if(call.state==='connected')clearTimeout(s.connectTimer);if(!terminal(call)&&(call.sdp_answer||call.audio_ready)&&s.media&&!s.answered&&!s.stopping){s.answered=true;try{if(s.transport==='websocket')await s.media.connect();else await s.media.pc.setRemoteDescription({type:'answer',sdp:call.sdp_answer});}catch{ diagnostic(s,'audio_setup_failed');s.error='Не удалось подключить звук.';void stop(s);}}
          if(terminal(call)){if(!s.diagnosticFinalReported){s.diagnosticFinalReported=true;reportDiagnostic(s,'server_ended');}release(s);s.stopping=false;s.preparing=false;s.closeError='';trackRecording(s,call);if(session===s&&call.state==='ended'&&!s.error)closeModal();}
          if(call.error_code&&!terminal(call)&&!s.stopping){diagnostic(s,'connection_failed');s.error='Не удалось подключить звонок. Завершаем соединение.';void stop(s);}}
      }
      const incoming=calls.find(c=>c.state==='incoming'&&!c.claimed);
      if(incoming&&!busy()&&(!modal||terminal(session?.call||modal.call))){closeModal();session=null;show(incoming,incoming);}
      if(modal?.call&&!session){const fresh=calls.find(c=>c.id===modal.call.id);if(fresh){modal.call=fresh;if(fresh.claimed||terminal(fresh))closeModal();}else closeModal();}
      refreshRecordings();mount();draw();
    }catch(e){
      // Admission backpressure is not evidence that an established audio link
      // or membership has ended. Retry only this read, for at most six seconds;
      // the audio relay independently rechecks live authority every 3 seconds.
      const s=session;
      const capacity=e.status===429||e.status===503&&e.code==='call_service_busy';
      if(capacity&&s?.call?.id&&s.media&&!s.stopping&&!terminal(s.call)){
        const now=performance.now();s.controlBusySince??=now;
        if(now-s.controlBusySince<6000){s.controlWarning='Сервер занят. Проверяем состояние звонка…';draw();return;}
        diagnostic(s,'control_unavailable');s.controlWarning='';s.error='Связь управления не восстановлена. Завершаем звонок.';
      }
      if(s)diagnostic(s,e.status===403?'auth_changed':'control_unavailable');online=false;sources=sources.map(source=>({...source,online:false}));if(e.status===403){enabled=false;release(session);session=null;pendingRecordings.clear();closeModal();}else {if(session&&!session.stopping)void stop(session);if(modal)modal.error.textContent='Связь управления потеряна. Микрофон выключен; проверяем завершение звонка…';}draw();}
    finally{polling=false;schedule();}
  }
  function schedule(){
    clearTimeout(timer);
    if(!identity||!enabled)return;
    const s=session;
    const connecting=s?.sent&&s.call&&!s.stopping&&!terminal(s.call)&&!s.controlBusySince&&
      (!s.answered||!s.pcmSent||s.call.direction==='inbound'&&s.call.state!=='connected');
    timer=setTimeout(()=>void poll(),connecting?500:2000);
  }
  function mount(){
    const host=document.getElementById('operatorBrowserCallHost'),route=window.tenantOperator?.currentDialog;if(!host)return;
    const routeKey=route?identity+':'+[route.channel_id,route.conversation_id,route.lead_id].join(':'):'';
    if(host.dataset.routeKey===routeKey&&host.firstElementChild&&route?.browser_call_available)return;
    host.replaceChildren();host.dataset.routeKey=routeKey;if(!route?.browser_call_available)return;
    const button=make('button',undefined,'browser-call-button');button.type='button';button.title='Звонок WhatsApp';button.setAttribute('aria-label','Звонок WhatsApp');
    button.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M7 3H4a1 1 0 0 0-1 1c0 9.4 7.6 17 17 17a1 1 0 0 0 1-1v-3l-5-2-2 2a14 14 0 0 1-7-7l2-2-2-5Z"/></svg>';
    button.onclick=()=>{if(session&&terminal(session.call)){release(session);session=null;}show(route);};host.append(button);
  }
  function changed(){const fresh=key();if(fresh!==identity){revision++;if(session){diagnostic(session,'auth_changed');void stop(session);release(session);session=null;}closeModal();pendingRecordings.clear();identity=fresh;enabled=true;calls=[];sources=[];online=false;}mount();void poll();}
  document.addEventListener('tenant-profile',changed);document.addEventListener('tenant-chat-attachments-ready',mount);
  document.addEventListener('tenant-calls-updated',event=>{enabled=true;const route=window.tenantOperator?.currentDialog;if(route&&String(route.channel_id)===String(event.detail?.channel_id)&&!String(route.conversation_id).endsWith('@g.us'))route.browser_call_available=true;mount();void poll();});
  document.addEventListener('tenant-auth-invalid',()=>{revision++;clearTimeout(timer);release(session);session=null;identity='';sources=[];pendingRecordings.clear();enabled=false;closeModal();});
  window.addEventListener('pagehide',()=>{stopRing();stopRingback();clearTimeout(timer);clearInterval(modal?.clockTimer);if(busy()){diagnostic(session,'page_hidden');void stop(session);}release(session);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void poll();});
  changed();
})();
