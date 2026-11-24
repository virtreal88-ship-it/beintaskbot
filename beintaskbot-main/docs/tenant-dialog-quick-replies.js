/* Historical quick replies only fill the existing editable draft. */
(()=>{
  'use strict';
  const endpoint='/api/platform/ai/dialogs/quick-replies';
  const labels={
    ru:{title:'Быстрые ответы',hide:'Убрать подсказку',error:'Не удалось убрать подсказку.'},
    az:{title:'Sürətli cavablar',hide:'Təklifi gizlət',error:'Təklifi gizlətmək mümkün olmadı.'},
    en:{title:'Quick replies',hide:'Hide suggestion',error:'Could not hide the suggestion.'}
  };
  const style=document.createElement('style');
  style.textContent=`
    #view-dialogs .operator-quick-replies {
      max-height:min(162px,29dvh); overflow:auto; overscroll-behavior:contain;
      border:1px solid var(--line); border-radius:12px; background:var(--card);
      margin:0 4px 5px; padding:3px;
    }
    #view-dialogs .operator-quick-replies[hidden] {display:none!important}
    .operator-quick-reply {display:grid;grid-template-columns:minmax(0,1fr) 44px;align-items:center;gap:4px}
    #view-dialogs .operator-quick-reply button {
      min-width:0;min-height:44px;border:0;border-radius:8px;
      background:transparent;color:var(--ink);padding:0 8px;margin:0;
    }
    #view-dialogs .operator-quick-reply button:is(:hover,:focus-visible) {background:var(--paper)}
    #view-dialogs .operator-quick-reply button:focus-visible {outline:2px solid var(--brand);outline-offset:-2px}
    .operator-quick-reply-use {display:flex;align-items:center;gap:8px;text-align:start;font:inherit}
    .operator-quick-reply-count {color:var(--muted);font-size:12px;flex:none}
    .operator-quick-reply-text {overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    #view-dialogs .operator-quick-reply-hide {font-size:18px;color:var(--muted);width:44px}
    .operator-quick-reply-error {padding:6px 8px;color:var(--danger);font-size:13px}
  `;
  document.head.append(style);
  let detach=null,suspend=null,authorizing=false;

  function identity(){
    const member=window.tenantProfileSnapshot?.member;
    return member?.tenant_id&&member.telegram_id!=null
      ? {expected_tenant_id:String(member.tenant_id),expected_user_id:String(member.telegram_id)}
      : null;
  }

  function attach(input,route,routeIsCurrent,resize){
    detach?.();
    const owner=identity();
    if(!owner)return;
    const root=input.closest('.operator-composer');
    if(!root)return;
    const labelsNow=labels[window.tenantI18n?.language]||labels.az;
    const panel=document.createElement('div');
    panel.className='operator-quick-replies';
    panel.id='operatorQuickReplies';
    panel.setAttribute('role','group');
    panel.setAttribute('aria-label',labelsNow.title);
    panel.hidden=true;
    input.before(panel);
    const lifetime=new AbortController();
    let pending=null,timer=null,revision=0,inserted=false;

    function current(){
      const latest=identity();
      return !authorizing&&!lifetime.signal.aborted&&input.isConnected&&!input.disabled&&!input.readOnly&&
        root.closest('#view-dialogs.active')&&routeIsCurrent()&&
        latest?.expected_tenant_id===owner.expected_tenant_id&&latest?.expected_user_id===owner.expected_user_id;
    }
    function hide(){
      revision++;
      clearTimeout(timer);
      pending?.abort();
      pending=null;
      panel.replaceChildren();
      panel.hidden=true;
      if(root.isConnected)resize();
    }
    function relevant(prefix,version){
      return revision===version&&current()&&input.value.trimStart()===prefix;
    }
    async function request(action,prefix,replyId,signal){
      const response=await fetch(endpoint,{
        method:'POST',credentials:'same-origin',cache:'no-store',signal,
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...owner,action,prefix,reply_id:replyId,
          lead_id:route.lead_id,channel_id:route.channel_id,conversation_id:route.conversation_id})
      });
      const data=await response.json();
      if(!response.ok||data.success!==true)throw new Error('quick_replies_unavailable');
      return data.result.items;
    }
    function render(items,prefix,version){
      panel.replaceChildren();
      for(const item of items){
        const row=document.createElement('div'),use=document.createElement('button'),hideButton=document.createElement('button');
        const count=document.createElement('span'),body=document.createElement('span');
        row.className='operator-quick-reply';
        use.type=hideButton.type='button';
        use.className='operator-quick-reply-use';
        hideButton.className='operator-quick-reply-hide';
        hideButton.textContent='×';
        hideButton.title=labelsNow.hide;
        hideButton.setAttribute('aria-label',labelsNow.hide+': '+item.body.slice(0,120));
        count.className='operator-quick-reply-count';
        count.textContent=String(item.uses);
        count.setAttribute('aria-hidden','true');
        body.className='operator-quick-reply-text';
        body.textContent=item.body;
        use.title=item.body;
        use.append(count,body);
        use.onclick=()=>{
          if(!relevant(prefix,version))return;
          input.value=item.body;
          inserted=true;
          input.focus();
          input.setSelectionRange(input.value.length,input.value.length);
          input.dispatchEvent(new Event('input',{bubbles:true}));
        };
        hideButton.onclick=async()=>{
          if(!relevant(prefix,version)||hideButton.disabled)return;
          panel.querySelectorAll('.operator-quick-reply-hide').forEach(button=>{button.disabled=true;});
          input.focus();
          try{
            const remaining=await request('dismiss',prefix,item.id);
            if(relevant(prefix,version))render(remaining,prefix,version);
          }catch{
            if(!relevant(prefix,version))return;
            panel.querySelectorAll('.operator-quick-reply-hide').forEach(button=>{button.disabled=false;});
            if(!panel.querySelector('.operator-quick-reply-error')){
              const error=document.createElement('div');
              error.className='operator-quick-reply-error';
              error.setAttribute('role','status');
              error.textContent=labelsNow.error;
              panel.append(error);
              resize();
            }
          }
        };
        row.append(use,hideButton);
        panel.append(row);
      }
      panel.hidden=!items.length;
      resize();
    }
    function search(event){
      hide();
      if(inserted){inserted=false;return;}
      const prefix=input.value.trimStart();
      if(event?.isComposing||!current()||prefix.trimEnd().length<2||prefix.length>180)return;
      const version=revision;
      timer=setTimeout(async()=>{
        if(!relevant(prefix,version)||!root.contains(document.activeElement))return;
        pending=new AbortController();
        try{
          const items=await request('search',prefix,undefined,pending.signal);
          if(relevant(prefix,version))render(items,prefix,version);
        }catch{
          // A failed lookup leaves the regular composer usable.
        }finally{
          if(revision===version)pending=null;
        }
      },200);
    }

    const opts={signal:lifetime.signal};
    input.addEventListener('input',search,opts);
    input.addEventListener('compositionend',search,opts);
    root.addEventListener('focusout',event=>{
      if(event.relatedTarget&&!root.contains(event.relatedTarget))hide();
    },opts);
    document.addEventListener('pointerdown',event=>{
      if(!root.contains(event.target))hide();
    },opts);
    root.addEventListener('keydown',event=>{
      if(panel.hidden)return;
      if(event.key==='Escape'){
        event.preventDefault();event.stopPropagation();
        input.focus();hide();
      }else if(event.target===input&&event.key==='ArrowDown'&&!input.value.includes('\n')&&
          input.selectionStart===input.value.length&&input.selectionEnd===input.selectionStart){
        event.preventDefault();
        panel.querySelector('button')?.focus();
      }
    },opts);
    const observer=new MutationObserver(()=>{if(!current())hide();});
    observer.observe(input,{attributes:true,attributeFilter:['disabled','readonly']});
    suspend=hide;
    detach=()=>{
      lifetime.abort();
      observer.disconnect();
      hide();
      panel.remove();
      detach=suspend=null;
    };
  }

  for(const event of ['tenant-chat-close','tenant-auth-invalid']){
    document.addEventListener(event,()=>detach?.());
  }
  document.addEventListener('tenant-auth-pending',()=>{authorizing=true;suspend?.();});
  document.addEventListener('tenant-profile',()=>{authorizing=false;suspend?.();});
  document.addEventListener('tenant-view',event=>{if(event.detail?.view!=='dialogs')suspend?.();});
  window.tenantDialogQuickReplies={attach};
})();
