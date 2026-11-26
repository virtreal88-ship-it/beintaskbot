/* One authorized cached page first; provider work never blocks first paint. */
(() => {
  const size=40, $=id=>document.getElementById(id);
  const copy=()=>({ru:{loading:'Загружаем последние сообщения…',empty:'Сообщений пока нет.',updating:'Показана сохранённая история. Проверяем обновления…',stale:'Обновление не удалось. Сохранённая история может быть неактуальной.',gap:'Есть новая часть переписки.',latest:'Показать последние сообщения',retry:'Повторить'},az:{loading:'Son mesajlar yüklənir…',empty:'Hələ mesaj yoxdur.',updating:'Saxlanmış tarixçə göstərilir. Yenilənir…',stale:'Yeniləmə alınmadı. Saxlanmış tarixçə köhnəlmiş ola bilər.',gap:'Yeni yazışma hissəsi var.',latest:'Son mesajları göstər',retry:'Yenidən yoxla'},en:{loading:'Loading recent messages…',empty:'No messages yet.',updating:'Saved history shown. Checking for updates…',stale:'Refresh failed. Saved history may be out of date.',gap:'New conversation history is available.',latest:'Show latest messages',retry:'Retry'}}[window.tenantI18n?.language]||{loading:'Son mesajlar yüklənir…',empty:'Hələ mesaj yoxdur.',updating:'Saxlanmış tarixçə göstərilir. Yenilənir…',stale:'Yeniləmə alınmadı. Saxlanmış tarixçə köhnəlmiş ola bilər.',gap:'Yeni yazışma hissəsi var.',latest:'Son mesajları göstər',retry:'Yenidən yoxla'});
  function read({api,id,member,signal}) {
    const query=new URLSearchParams({lead_id:id,limit:size,expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
    // Attach rejection immediately: the deal request can finish after this read.
    return api('/api/platform/crm/chat/history?'+query,{signal}).then(data=>({data}),error=>({error}));
  }
  function mount({api,id,member,signal,isCurrent,line,first,box=$('chatRows'),refreshProvider=true}) {
    const scroller=box.closest('.deal-chat-history'),c=copy();
    const status=document.createElement('div');status.className='deal-chat-status';status.style.cssText='font-size:13px;color:var(--muted);white-space:normal;overflow-wrap:anywhere;display:flex;flex-wrap:wrap;gap:6px;align-items:center';status.setAttribute('role','status');box.before(status);
    let loaded=false,paging=false,flight=null,queued=null,refreshFlight=null,revision=0;
    const valid=()=>isCurrent()&&!signal.aborted&&box===$('chatRows');
    const announce=(name,detail={})=>document.dispatchEvent(new CustomEvent(name,{detail:{lead_id:id,...detail}}));
    const notice=(message,action,label=c.retry)=>{status.replaceChildren(document.createTextNode(message));if(action){const button=document.createElement('button');button.type='button';button.className='outline';button.textContent=label;button.onclick=action;status.append(' ',button);}};
    const denied=error=>[401,403,404,409].includes(error.status);
    function failure(error){if(!valid()||error.name==='AbortError')return;if(denied(error)){revision++;loaded=false;box.replaceChildren();announce('tenant-chat-history-reset');notice(error.message);document.querySelectorAll('#chatText,#sendChat,#tcComposerPrimary,#tcAiMenuToggle').forEach(n=>n.disabled=true);}else notice(loaded?c.stale:error.message,()=>load(true));}
    function apply(data,reset=false){
      if(!valid())return;
      const messages=data.messages||[],nodes=new Map([...box.querySelectorAll('[data-chat-id]')].map(n=>[n.dataset.chatId,n]));
      const bottom=!scroller||scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<40;
      // Never splice a disconnected tail onto an older page: that would hide a gap.
      if(loaded&&!reset&&nodes.size&&messages.length&&data.next_cursor&&!messages.some(m=>nodes.has(String(m.external_id)))){
        notice(c.gap,()=>load(false,undefined,true),c.latest);return;
      }
      if(!loaded||reset||!nodes.size){
        announce('tenant-chat-history-reset');box.innerHTML=messages.map(line).join('');
        loaded=true;paging=!!data.next_cursor;announce('tenant-chat-loaded',{messages,next_cursor:data.next_cursor,render_message:line,reload_cache:()=>load(false)});
      }else{
        let next=null;
        for(const message of [...messages].reverse()){
          const key=String(message.external_id),existing=nodes.get(key);
          if(existing){
            // Keep media elements and their playback intact while text/metadata updates.
            const body=existing.querySelector('[data-chat-body]');if(body)body.textContent=message.body||'Media mesajı';
            next=existing;continue;
          }
          const template=document.createElement('template');template.innerHTML=line(message);const node=template.content.firstElementChild;
          box.insertBefore(node,next);nodes.set(key,node);next=node;
        }
        announce('tenant-chat-history-added');
        // A previously complete short cache may gain older rows on refresh.
        // Install its first cursor without resetting an existing older-page chain.
        if(!paging&&data.next_cursor){paging=true;announce('tenant-chat-pagination-available',{next_cursor:data.next_cursor});}
      }
      notice(data.stale?c.stale:(messages.length||box.children.length?'':c.empty),data.stale?()=>load(true):null);
      if(scroller&&(bottom||reset||!nodes.size))scroller.scrollTop=scroller.scrollHeight;
    }
    function refresh(){
      if(refreshFlight)return refreshFlight;
      const serial=revision;
      const query=new URLSearchParams({lead_id:id,limit:size,recent:'1',refresh:'1',expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
      notice(c.updating);
      refreshFlight=(async()=>{
        try{const data=await api('/api/platform/crm/chat?'+query,{signal});if(valid()&&serial===revision)apply(data);}
        catch(error){if(serial===revision||denied(error))failure(error);}
      })().finally(()=>{refreshFlight=null;});
      return refreshFlight;
    }
    function load(provider=false,prefetched,reset=false){
      if(flight){if(!queued)queued=flight.then(()=>{queued=null;return valid()?load(provider,undefined,reset):undefined;});return queued;}
      const serial=++revision;
      if(!loaded)notice(c.loading);
      flight=(async()=>{const result=await(prefetched||read({api,id,member,signal}));if(!valid()||serial!==revision)return;if(result.error){failure(result.error);return;}apply(result.data,reset);if(provider&&valid())void refresh();})().finally(()=>{flight=null;});
      return flight;
    }
    void load(refreshProvider,first);
    return {reload:()=>load(false)};
  }
  window.tenantDealChat={read,mount};
})();
