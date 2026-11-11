/* Private Copilot voice mounts only after server preflight permits the pilot. */
((root, factory) => {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.mountTenantCopilotVoice = factory;
})(typeof window === 'object' ? window : globalThis, function mountTenantCopilotVoice(parent, options={}) {
  // Each host owns its own screen. A screen nested in another host (for
  // example the dialog workspace) must not prevent the analyst from mounting.
  if (!parent || [...parent.children].some(child => child.hasAttribute('data-copilot-voice'))) return null;
  const document=parent.ownerDocument, section=document.createElement('section');
  section.dataset.copilotVoice='';section.dataset.voiceState='idle';section.hidden=true;
  section.className='tenant-copilot-voice-screen';
  section.setAttribute('role','dialog');section.setAttribute('aria-modal','true');section.tabIndex=-1;
  const copy={ru:{title:'Голосовой разговор с AI'},az:{title:'AI ilə səsli söhbət'},en:{title:'Voice conversation with AI'}}[options.language]||{title:'Голосовой разговор с AI'};
  section.setAttribute('aria-label',copy.title);
  section.innerHTML=`<header class="tenant-voice-header"><span class="tenant-voice-live-mark" aria-hidden="true"></span><strong>${copy.title}</strong><span class="tenant-voice-mic-indicator" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.93V21h2v-3.07A7 7 0 0 0 19 11h-2Z"/></svg></span></header><main class="tenant-voice-stage"><button type="button" class="tenant-voice-orb" data-voice-orb data-state="idle" aria-label="AI отвечает голосом" disabled><i></i><i></i><i></i></button><div class="tenant-voice-controls" data-voice-controls></div></main>`;
  if(!document.querySelector('[data-tenant-voice-screen-style]')){
    const style=document.createElement('style');style.dataset.tenantVoiceScreenStyle='';
    style.textContent=`.tenant-copilot-voice-screen{position:fixed!important;inset:0!important;z-index:30000!important;display:flex;flex-direction:column;box-sizing:border-box;width:100vw;height:100vh;height:100dvh;margin:0;padding:env(safe-area-inset-top) 20px env(safe-area-inset-bottom);overflow:hidden;background:#fafafa;color:#202124;font-family:inherit}.tenant-copilot-voice-screen[hidden]{display:none!important}.tenant-voice-header{display:flex;align-items:center;justify-content:center;gap:10px;min-height:58px;flex:none}.tenant-voice-header strong{font-size:18px;font-weight:600}.tenant-voice-live-mark{position:absolute;left:24px;width:9px;height:9px;border-radius:50%;background:#19a35b;box-shadow:0 0 0 5px #19a35b20}.tenant-voice-mic-indicator{position:absolute;right:24px;display:grid;place-items:center;width:44px;height:44px;border-radius:50%;background:#1bad4b;color:#fff;box-shadow:0 2px 8px #0002}.tenant-voice-mic-indicator svg{width:25px;height:25px;fill:currentColor}.tenant-voice-stage{position:relative;display:flex;flex:1;min-height:0;flex-direction:column;align-items:center;justify-content:center;gap:clamp(24px,6vh,64px);padding:12px 0 28px}.tenant-voice-orb{position:relative;display:grid;place-items:center;width:min(64vw,48vh,420px);height:min(64vw,48vh,420px);min-width:230px;min-height:230px;max-width:420px;max-height:420px;overflow:hidden;border-radius:50%;isolation:isolate;background:radial-gradient(ellipse at 70% 78%,#d8d8d8 0%,#a9a9a9 30%,transparent 60%),radial-gradient(ellipse at 35% 25%,#121212 0%,#252525 47%,#777 72%,#d2d2d2 100%);box-shadow:0 22px 75px #0002;transition:filter .25s ease,transform .25s ease}.tenant-voice-orb:before,.tenant-voice-orb:after,.tenant-voice-orb i{position:absolute;inset:-18%;border-radius:44%;content:"";background:radial-gradient(ellipse at 74% 64%,#f7f7f7 0 8%,#d8d8d8 23%,transparent 52%),radial-gradient(ellipse at 27% 32%,#111 0 28%,transparent 63%);mix-blend-mode:screen;opacity:.74;filter:blur(10px);animation:tenantVoiceOrbDrift 9s ease-in-out infinite alternate}.tenant-voice-orb:after{inset:-12%;opacity:.44;filter:blur(17px);animation-duration:13s;animation-direction:alternate-reverse}.tenant-voice-orb i:nth-child(1){opacity:.24;filter:blur(22px);animation-duration:7s}.tenant-voice-orb i:nth-child(2){opacity:.18;animation-duration:11s;animation-direction:alternate-reverse}.tenant-voice-orb i:nth-child(3){opacity:.12;animation-duration:15s}.tenant-voice-orb[data-state=listening]{animation:tenantVoiceListenPulse 2.8s ease-in-out infinite}.tenant-voice-orb[data-state=responding]{filter:contrast(1.08) brightness(1.04);animation:tenantVoiceSpeakPulse 1.05s ease-in-out infinite}.tenant-voice-orb[data-state=connecting],.tenant-voice-orb[data-state=stopping]{animation:tenantVoiceListenPulse 1.2s ease-in-out infinite}.tenant-voice-orb[data-state=muted]{filter:saturate(.55);animation:none}.tenant-voice-controls{display:grid;justify-items:center;gap:18px;width:100%;padding-bottom:max(12px,env(safe-area-inset-bottom))}.tenant-voice-controls [role=status]{min-height:24px;margin:0;color:#62666a;font-size:16px;text-align:center}.tenant-voice-controls button{display:grid;place-items:center;box-sizing:border-box;width:64px;min-width:64px;height:64px;min-height:64px;padding:0;border:0;border-radius:50%;background:#f0f0f0;color:#151515;box-shadow:0 3px 12px #0000000b;cursor:pointer}.tenant-voice-controls button svg{width:27px;height:27px;fill:currentColor}.tenant-voice-controls button:focus-visible{outline:3px solid #3986f6;outline-offset:3px}.tenant-voice-controls button:disabled{opacity:.55}.tenant-voice-controls button.voice-session-end{width:78px;min-width:78px;height:78px;min-height:78px;background:#101010;color:white}.tenant-voice-controls button.voice-session-end svg{width:34px;height:34px}.tenant-voice-controls button.voice-session-start{background:#e9f3ff;color:#1468dc}.tenant-voice-controls button.voice-session-start[hidden],.tenant-voice-controls button.voice-session-end[hidden]{display:none!important}@keyframes tenantVoiceOrbDrift{from{transform:rotate(-18deg) translate3d(-4%,2%,0) scale(.94)}to{transform:rotate(22deg) translate3d(5%,-3%,0) scale(1.08)}}@keyframes tenantVoiceListenPulse{0%,100%{transform:scale(.985)}50%{transform:scale(1.015)}}@keyframes tenantVoiceSpeakPulse{0%,100%{transform:scale(.99);box-shadow:0 22px 75px #0002}50%{transform:scale(1.035);box-shadow:0 24px 95px #5746b833}}@media(max-width:600px){.tenant-copilot-voice-screen{padding-left:16px;padding-right:16px}.tenant-voice-header{min-height:54px}.tenant-voice-orb{width:min(78vw,44vh);height:min(78vw,44vh);min-width:220px;min-height:220px}.tenant-voice-stage{gap:clamp(22px,5vh,46px);padding-bottom:12px}.tenant-voice-controls{gap:16px}.tenant-voice-controls button{width:60px;min-width:60px;height:60px;min-height:60px}.tenant-voice-controls button.voice-session-end{width:72px;min-width:72px;height:72px;min-height:72px}}@media(prefers-reduced-motion:reduce){.tenant-voice-orb,.tenant-voice-orb:before,.tenant-voice-orb:after,.tenant-voice-orb i{animation:none!important;transition:none!important}}`;
    style.textContent += '.tenant-voice-orb{padding:0;border:0;cursor:default}.tenant-voice-orb:not(:disabled){cursor:pointer}.tenant-voice-orb:focus-visible{outline:3px solid #3986f6;outline-offset:6px}';
    document.head.append(style);
  }
  parent.append(section);
  const win=document.defaultView||globalThis;
  const controls=section.querySelector('[data-voice-controls]');
  let controller=null,disposed=false,activationPromise=null,endRequested=false;
  const setVoiceState=state=>{
    section.dataset.voiceState=state;
    const orb=section.querySelector('[data-voice-orb]');
    orb?.setAttribute('data-state',state);
    if(orb){const canInterrupt=state==='responding';orb.disabled=!canInterrupt;orb.setAttribute('aria-label',canInterrupt?(options.language==='az'?'Cavabı kəs və danış':'Перебить ответ и говорить'):(options.language==='az'?'AI səsli söhbəti dinləyir':'Анимация голосового разговора с AI'));}
    const status=controls?.querySelector('[role=status]');
    if(state==='idle'&&endRequested&&status&&!/не подтверж|təsdiqlənmədi|not confirmed/i.test(status.textContent||''))section.hidden=true;
    if(state==='idle')endRequested=false;
    try{options.onState?.(state);}catch(_){}
  };
  const abort=()=>{void section.disposeVoice().catch(()=>{});};
  section.addEventListener('keydown',event=>{
    if(event.key!=='Escape'||section.hidden)return;
    event.preventDefault();event.stopPropagation();
    if(controller?.isActive?.()){endRequested=true;void controller.end?.();}
    else section.hidden=true; // Dismiss only the local UI; never release a server lease.
  });
  section.addEventListener('keydown',event=>{
    if(event.key!=='Tab')return;
    const nodes=[...section.querySelectorAll('button:not([hidden]):not(:disabled)')];
    if(!nodes.length)return;
    const first=nodes[0],last=nodes[nodes.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  });
  const pagehide=()=>{
    // Mobile browsers may dispatch pagehide while the app is being
    // backgrounded or restored from history. Ask the server to end first;
    // destroy() closes the WebSocket immediately and loses that acknowledgement.
    if(controller?.isActive?.()){void controller.end?.();return;}
    abort();
  };
  section.isVoiceActive=()=>!!controller?.isActive?.();
  section.endVoice=()=>controller?.isActive?.()?controller.end?.():Promise.resolve(false);
  section.disposeVoice=async()=>{
    if(disposed)return;disposed=true;
    win.removeEventListener?.('pagehide',pagehide);
    document.removeEventListener?.('tenant-auth-invalid',abort);
    document.removeEventListener?.('tenant-chat-close',abort);
    if(controller){
      if(controller.isActive?.()){
        // Outlive the controller's 8s confirmation window before destroying it.
        try{await Promise.race([Promise.resolve(controller.end?.()),new Promise(resolve=>setTimeout(resolve,8500))]);}catch(_){}
      }
      await controller.destroy();
    }
    section.remove();
  };
  section.activateVoice=()=>{
    if(disposed||typeof options.preflight!=='function')return Promise.resolve(null);
    if(controller)return Promise.resolve(controller);
    if(activationPromise)return activationPromise;
    activationPromise=(async()=>{
      const permitted=await options.preflight();
      if(disposed||permitted?.available!==true||!section.isConnected||!options.isCurrent?.())return null;
      if(typeof win.mountExperimentalCopilotVoice!=='function'||typeof win.openExperimentalCopilotAudio!=='function'||
         typeof win.connectExperimentalCopilotVoice!=='function'||typeof win.acquireTenantMicrophone!=='function')return null;
      controls?.replaceChildren();
      controller=win.mountExperimentalCopilotVoice(controls,{
        acquireMicrophone:()=>win.acquireTenantMicrophone(),openAudio:callback=>win.openExperimentalCopilotAudio(callback,undefined,undefined,{pushToTalk:true}),
        connect:callback=>win.connectExperimentalCopilotVoice(options.socketPath,callback),
        lifecycle:document,interruptTarget:section.querySelector('[data-voice-orb]'),language:options.language,duplex:true,onState:setVoiceState,
        onDismiss:()=>{section.hidden=true;},
        onIdle:()=>{setVoiceState('idle');if(!disposed)options.onIdle?.();},
      });
      controls?.addEventListener('click',event=>{if(event.target.closest?.('.voice-session-end'))endRequested=true;},true);
      return controller;
    })().catch(()=>null).finally(()=>{activationPromise=null;});
    return activationPromise;
  };
  section.startVoice=async()=>{const voice=await section.activateVoice();if(!voice)return false;section.hidden=false;section.focus({preventScroll:true});voice.start?.();return true;};
  win.addEventListener?.('pagehide',pagehide);
  document.addEventListener?.('tenant-auth-invalid',abort);
  document.addEventListener?.('tenant-chat-close',abort);
  return section;
});
