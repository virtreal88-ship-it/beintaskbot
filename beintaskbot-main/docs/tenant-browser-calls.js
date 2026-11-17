/* Test WhatsApp calls. Control uses same-origin membership; PCM uses WebRTC.
 * No provider keys, remote iframe, message sends, or local HTTP requests. */
(() => {
  'use strict';
  let identity='',revision=0,polling=false,timer=null,enabled=true,online=false,testNumber='',calls=[],session=null,modal=null;
  const terminal=call=>!call||['ended','failed'].includes(call.state);
  const busy=()=>!!session&&(session.preparing||session.stopping||!terminal(session.call));
  const profile=()=>window.tenantOperator?.member||window.tenantProfileSnapshot?.member;
  const key=()=>{const p=profile();return p?.tenant_id&&p?.telegram_id?String(p.tenant_id)+':'+p.telegram_id:'';};
  const query=(owner=profile())=>new URLSearchParams({expected_tenant_id:String(owner?.tenant_id||''),expected_user_id:String(owner?.telegram_id||'')});
  const routeOf=r=>({channel_id:String(r.channel_id),conversation_id:String(r.conversation_id),lead_id:Number(r.lead_id)});
  const sameRoute=(a,b)=>a&&b&&['channel_id','conversation_id','lead_id'].every(k=>String(a[k])===String(b[k]));
  const make=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
  const labels={incoming:'Входящий звонок',starting:'Подключение…',ringing:'Вызов…',connected:'Разговор',ending:'Завершаем звонок…',ended:'Звонок завершён',failed:'Звонок не начался',unknown:'Завершение звонка пока не подтверждено'};
  const style=make('style');style.textContent=`
    .browser-call-button{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:50%;border:1px solid var(--border,#dde3e8);background:transparent;color:inherit;cursor:pointer;flex:none}.browser-call-button svg{width:19px;height:19px}.browser-call-button:disabled{opacity:.45;cursor:default}
    .browser-call-panel{border:1px solid #dce4e9;border-radius:20px;padding:24px;width:min(420px,calc(100vw - 32px));box-sizing:border-box;color:#17212b;background:#fff;box-shadow:0 18px 70px #10203033}.browser-call-panel::backdrop{background:#11223355}.browser-call-panel h2{margin:0 30px 12px 0;font-size:20px}.browser-call-panel p{line-height:1.5}.browser-call-close{position:absolute;right:12px;top:12px;background:none;border:0;font-size:24px;cursor:pointer}.browser-call-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}.browser-call-actions button{padding:10px 16px;border:1px solid #d7e0e8;border-radius:12px;cursor:pointer}.browser-call-actions .accept{background:#087e53;color:white;border-color:#087e53}.browser-call-actions .end{background:#b42336;color:white;border-color:#b42336}.browser-call-panel label{display:flex;align-items:flex-start;gap:10px;line-height:1.4}.browser-call-panel small{display:block;margin-top:12px;color:#62717d}.browser-call-panel audio{width:100%;margin-top:12px}.browser-call-error{color:#a91d2e}`;document.head.append(style);
  async function api(data,owner=profile(),keepalive=false){
    const q=query(owner);if(!data&&session?.request){q.set('request_id',session.request);if(session.stopping)q.set('heartbeat','0');}
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),7000);
    try{const response=await fetch('/api/platform/browser-calls?'+q,{method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,
      headers:data?{'Content-Type':'application/json'}:undefined,body:data?JSON.stringify(data):undefined,keepalive});
      const payload=await response.json();if(!response.ok){const error=new Error(payload.error||'Не удалось подтвердить звонок');error.status=response.status;throw error;}return payload.result;
    }finally{clearTimeout(timeout);}
  }
  function release(s){if(!s)return;s.media?.close();s.media=null;s.release?.();s.release=null;}
  async function stop(s=session,hide=false){
    if(!s){if(hide)closeModal();return;}
    s.stopping=true;release(s);draw();
    try{const result=await api({...s.route,op:'end',call_id:s.call?.id,request_id:s.request},s.owner,true);if(result.state==='not_started'){s.call={state:'failed',recording_state:'none'};s.preparing=false;s.stopping=false;s.error='Звонок отменён до подключения.';draw();}}catch(error){if(session===s){s.error='Завершение пока не подтверждено. Микрофон выключен; сервер продолжает закрытие звонка.';draw();}}
    if(hide)closeModal();void poll();
  }
  function closeModal(){if(modal){modal.node.close();modal.node.remove();modal=null;}if(session&&!busy()){release(session);session=null;}}
  function show(route,call=null){
    if(busy()&&modal){draw();if(!modal.node.open)modal.node.showModal();return;}
    if(busy()){route=session.route;call=session.call;}
    closeModal();const node=make('dialog',undefined,'browser-call-panel'),title=make('h2',call?'Входящий звонок':'Звонок WhatsApp'),state=make('p'),error=make('p','','browser-call-error');
    node.setAttribute('aria-label','Звонок WhatsApp');const close=make('button','×','browser-call-close');close.setAttribute('aria-label','Закрыть звонок');
    const consent=make('input');consent.type='checkbox';const consentLabel=make('label');consentLabel.append(consent,make('span','Участник предупреждён и согласен на запись обеих сторон.'));
    const actions=make('div',undefined,'browser-call-actions'),start=make('button',call?'Принять':'Позвонить','accept'),reject=make('button','Отклонить'),end=make('button','Завершить','end');
    for(const button of [start,reject,end,close])button.type='button';actions.append(start,reject,end);
    const info=make('small');const result=make('div');
    node.append(close,title,state,consentLabel,error,actions,info,result);document.body.append(node);
    modal={node,title,state,error,consent,consentLabel,start,reject,end,result,info,route:routeOf(route),call};
    close.onclick=()=>{if(busy())void stop(session,true);else if(modal?.call&&!terminal(modal.call))void rejectCall();else closeModal();};
    node.addEventListener('cancel',event=>{event.preventDefault();close.click();});
    consent.onchange=draw;start.onclick=()=>void begin();reject.onclick=()=>void rejectCall();end.onclick=()=>void stop();
    draw();node.showModal();
  }
  async function rejectCall(){const m=modal;if(!m?.call)return;try{await api({...m.route,op:'reject',call_id:m.call.id,request_id:crypto.randomUUID()});closeModal();void poll();}catch(e){if(modal===m)m.error.textContent=e.message;}}
  function draw(){
    const m=modal;if(!m)return;const s=session,call=s?.call||m.call,active=!!s&&!terminal(s.call),pending=!!s&&!s.call;
    m.title.textContent=call?.direction==='inbound'?'Входящий звонок':'Звонок WhatsApp';
    m.state.textContent=s?.preparing?'Подключаем микрофон…':s?.stopping&&!terminal(call)?'Микрофон выключен. Завершаем звонок…':call?labels[call.state]||'Проверяем состояние…':online?'Готов к звонку':'Тестовый сервер звонков не подключён';
    m.info.textContent='Звонок через тестовый WhatsApp'+(testNumber?' +'+testNumber:'')+'. Максимум 2 минуты; запись доступна 24 часа.';m.error.textContent=s?.error||'';m.start.hidden=active||pending||!!s?.preparing||!!s?.stopping||!!call&&call.state!=='incoming';
    m.start.disabled=!online||!m.consent.checked;m.reject.hidden=!call||call.state!=='incoming'||!!s;
    m.end.hidden=!active&&!pending;m.end.disabled=!!s?.stopping;m.consentLabel.hidden=!!s;
    if(call&&terminal(call)){
      m.result.textContent=call.recording_state==='pending'?'Сохраняем запись в диалог…':call.recording_state==='incomplete'?'Запись сохранена. Захват отметил промежутки в аудиопотоке.':call.recording_state==='ready'?'Запись сохранена в диалог.':'';
    }
  }
  async function media(s){
    if(!window.acquireTenantMicrophone)throw new Error('Захват микрофона недоступен в этом браузере.');
    const unlock=await window.acquireTenantMicrophone();if(typeof unlock!=='function')throw new Error('Микрофон уже используется в другой вкладке или голосовом режиме.');if(s.stopping||key()!==s.identity){unlock();throw new Error('Звонок отменён');}s.release=unlock;
    const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    if(s.stopping||key()!==s.identity){stream.getTracks().forEach(t=>t.stop());throw new Error('Звонок отменён');}
    const pc=new RTCPeerConnection({iceServers:[]}),dc=pc.createDataChannel('pcm',{ordered:true}),ctx=new AudioContext({sampleRate:16000});dc.binaryType='arraybuffer';let closed=false;
    const handle={pc,close(){if(closed)return;closed=true;stream.getTracks().forEach(t=>t.stop());pc.close();void ctx.close().catch(()=>{});}};s.media=handle;
    try{
      await ctx.audioWorklet.addModule('/assets/browser-call-audio.js');await ctx.resume();
      const capture=new AudioWorkletNode(ctx,'bein-call-capture'),playback=new AudioWorkletNode(ctx,'bein-call-playback');
      ctx.createMediaStreamSource(stream).connect(capture);capture.connect(ctx.destination);playback.connect(ctx.destination);
      capture.port.onmessage=e=>{if(closed||dc.readyState!=='open'||dc.bufferedAmount>131072)return;const floats=e.data,pcm=new Int16Array(floats.length);for(let i=0;i<floats.length;i++)pcm[i]=Math.round(Math.max(-1,Math.min(1,floats[i]))*32767);dc.send(pcm.buffer);};
      dc.onmessage=e=>{if(closed||!(e.data instanceof ArrayBuffer)||e.data.byteLength>6400||e.data.byteLength%2)return;const raw=new Int16Array(e.data),floats=new Float32Array(raw.length);for(let i=0;i<raw.length;i++)floats[i]=raw[i]/32768;playback.port.postMessage(floats,[floats.buffer]);};
      pc.onconnectionstatechange=()=>{if(!closed&&['failed','closed'].includes(pc.connectionState))void stop(s);};
      const offer=await pc.createOffer();await pc.setLocalDescription(offer);
      await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{pc.removeEventListener('icegatheringstatechange',done);reject(new Error('Не удалось подготовить аудиосоединение.'));},10000);function done(){if(pc.iceGatheringState==='complete'){clearTimeout(timeout);pc.removeEventListener('icegatheringstatechange',done);resolve();}}pc.addEventListener('icegatheringstatechange',done);done();});
      if(closed||s.stopping)throw new Error('Звонок отменён');return pc.localDescription.sdp;
    }catch(e){handle.close();throw e;}
  }
  async function begin(){
    const m=modal;if(!m||!m.consent.checked||!online||busy())return;
    const s={route:m.route,call:m.call,request:crypto.randomUUID(),owner:{...profile()},identity:key(),preparing:true,stopping:false,error:''};session=s;draw();
    try{
      const offer=await media(s);if(s.stopping||session!==s||key()!==s.identity){release(s);return;}
      s.sent=true;const call=await api({...s.route,op:m.call?'accept':'start',call_id:m.call?.id,request_id:s.request,sdp_offer:offer,consent_confirmed:true},s.owner);
      s.call=call;s.preparing=false;if(s.stopping||session!==s||key()!==s.identity){void stop(s);return;}draw();void poll();
    }catch(e){release(s);s.preparing=false;s.error=e.message;if(s.sent||s.call&&!terminal(s.call)){if(!s.call)s.call={state:'unknown'};void stop(s);}else{s.call={state:'failed',recording_state:'none'};draw();}}
  }
  async function poll(){
    if(polling||!identity||!enabled)return;polling=true;const generation=revision;
    try{
      const result=await api();if(generation!==revision||key()!==identity)return;
      enabled=result.enabled===true;online=result.online===true;testNumber=/^[0-9]{5,22}$/.test(result.test_number||'')?result.test_number:'';calls=result.calls||[];
      const s=session;
      if(s&&!s.call?.id){const recovered=calls.find(c=>c.request_matches&&sameRoute(c,s.route));if(recovered)s.call=recovered;}
      if(s?.call?.id){
        const call=calls.find(c=>c.id===s.call.id);
        if(call){s.call=call;if(!terminal(call)&&call.sdp_answer&&s.media&&!s.answered&&!s.stopping){s.answered=true;try{await s.media.pc.setRemoteDescription({type:'answer',sdp:call.sdp_answer});}catch{ s.error='Не удалось подключить звук.';void stop(s);}}
          if(terminal(call)){release(s);s.stopping=false;s.preparing=false;if(call.recording_id&&s.refreshed!==call.recording_id){s.refreshed=call.recording_id;if(sameRoute(window.tenantOperator?.currentDialog,s.route))void window.tenantOperator.openDialog({...s.route,provider:'connect'},true);}}
          if(call.error_code&&!terminal(call)&&!s.stopping){s.error='Не удалось подключить звонок. Завершаем соединение.';void stop(s);}}
      }
      const incoming=calls.find(c=>c.state==='incoming'&&!c.claimed);
      if(incoming&&!busy()&&(!modal||terminal(session?.call||modal.call))){closeModal();session=null;show(incoming,incoming);}
      if(modal?.call&&!session){const fresh=calls.find(c=>c.id===modal.call.id);if(fresh){modal.call=fresh;if(fresh.claimed)closeModal();}else closeModal();}
      mount();draw();
    }catch(e){online=false;if(e.status===403){enabled=false;release(session);session=null;closeModal();}else {if(session&&!session.stopping)void stop(session);if(modal)modal.error.textContent='Связь управления потеряна. Микрофон выключен; проверяем завершение звонка…';}}
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
  function changed(){const fresh=key();if(fresh!==identity){revision++;if(session){void stop(session);release(session);session=null;}closeModal();identity=fresh;enabled=true;calls=[];online=false;}mount();void poll();}
  document.addEventListener('tenant-profile',changed);document.addEventListener('tenant-chat-attachments-ready',mount);
  document.addEventListener('tenant-auth-invalid',()=>{revision++;clearTimeout(timer);release(session);session=null;identity='';enabled=false;closeModal();});
  window.addEventListener('pagehide',()=>{clearTimeout(timer);if(busy())void stop(session);release(session);});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void poll();});
  changed();
})();
