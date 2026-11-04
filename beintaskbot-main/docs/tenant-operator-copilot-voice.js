/* Private Copilot voice mounts only after server preflight permits the pilot. */
((root, factory) => {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.mountTenantCopilotVoice = factory;
})(typeof window === 'object' ? window : globalThis, function mountTenantCopilotVoice(parent, options={}) {
  if (!parent || parent.querySelector('[data-copilot-voice]')) return null;
  const document=parent.ownerDocument, section=document.createElement('section');
  section.dataset.copilotVoice='';section.hidden=true;
  const button=document.createElement('button');button.type='button';button.disabled=true;
  button.textContent='Голосовой разговор';
  const status=document.createElement('p');status.setAttribute('role','status');
  button.onclick=()=>{status.textContent='Голосовой режим пока недоступен.';};
  section.append(button,status);parent.append(section);
  const win=document.defaultView||globalThis;
  let controller=null,disposed=false,checking=false;
  const abort=()=>{void section.disposeVoice().catch(()=>{});};
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
        try{await Promise.race([Promise.resolve(controller.end?.()),new Promise(resolve=>setTimeout(resolve,2200))]);}catch(_){}
      }
      await controller.destroy();
    }
  };
  section.activateVoice=async()=>{
    if(disposed||checking||controller||typeof options.preflight!=='function')return;
    checking=true;
    try{
      const permitted=await options.preflight();
      if(disposed||permitted?.available!==true||!section.isConnected||!options.isCurrent?.())return;
      if(typeof win.mountExperimentalCopilotVoice!=='function'||typeof win.openExperimentalCopilotAudio!=='function'||
         typeof win.connectExperimentalCopilotVoice!=='function'||typeof win.acquireTenantMicrophone!=='function')return;
      section.replaceChildren();
      controller=win.mountExperimentalCopilotVoice(section,{
        acquireMicrophone:()=>win.acquireTenantMicrophone(),openAudio:callback=>win.openExperimentalCopilotAudio(callback),
        connect:callback=>win.connectExperimentalCopilotVoice(options.socketPath,callback),
        lifecycle:document,language:options.language,onIdle:()=>{if(!disposed)options.onIdle?.();},
      });
      section.hidden=false;
    }catch(_){/* Denied, stale, or unreachable: leave voice hidden. */}
    finally{checking=false;}
  };
  win.addEventListener?.('pagehide',pagehide);
  document.addEventListener?.('tenant-auth-invalid',abort);
  document.addEventListener?.('tenant-chat-close',abort);
  return section;
});
