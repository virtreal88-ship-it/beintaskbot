/* The deal owns selection and visibility. The operator pane owns the conversation. */
(() => {
  function mount({panel,chat,modal,source,id,member,api,current,resumeChat}){
    const c=({
      ru:{loading:'Загружаем диалог…',empty:'К сделке не привязан доступный диалог.',error:'Не удалось загрузить диалог.',retry:'Повторить',choose:'Выберите диалог',back:'К выбору диалога'},
      az:{loading:'Dialoq yüklənir…',empty:'Sövdələşməyə əlçatan dialoq bağlanmayıb.',error:'Dialoqu yükləmək mümkün olmadı.',retry:'Təkrar et',choose:'Dialoq seçin',back:'Dialoq seçiminə qayıt'},
      en:{loading:'Loading conversation…',empty:'There is no accessible conversation linked to this deal.',error:'Could not load conversation.',retry:'Retry',choose:'Choose a conversation',back:'Choose another conversation'}
    })[window.tenantI18n?.language||'az'];
    const linkedCopy=({
      ru:{full:'Открыть полную историю чата',scoped:'К переписке по сделке',earlier:'Более ранние сообщения',empty:'В сделке пока нет сообщений этого чата.'},
      az:{full:'Söhbətin tam tarixçəsini aç',scoped:'Sövdələşmənin yazışmasına qayıt',earlier:'Əvvəlki mesajlar',empty:'Bu sövdələşmədə hələ mesaj yoxdur.'},
      en:{full:'Open full conversation history',scoped:'Back to deal messages',earlier:'Earlier messages',empty:'This deal has no messages from this conversation yet.'}
    })[window.tenantI18n?.language||'az'];
    const whatsappCopy=({
      ru:{loading:'Ищем WhatsApp-диалог…',empty:'По этому номеру пока нет доступного WhatsApp-диалога.',
        attach:'Привязать WhatsApp',error:'Не удалось проверить WhatsApp-диалог. Повторите поиск.',close:'Закрыть поиск'},
      az:{loading:'WhatsApp dialoqu axtarılır…',empty:'Bu nömrə üçün əlçatan WhatsApp dialoqu hələ yoxdur.',
        attach:'WhatsApp dialoqunu bağla',error:'WhatsApp dialoqu yoxlanmadı. Yenidən axtarın.',close:'Axtarışı bağla'},
      en:{loading:'Finding the WhatsApp chat…',empty:'There is no accessible WhatsApp chat for this number yet.',
        attach:'Link WhatsApp chat',error:'Could not verify the WhatsApp chat. Search again.',close:'Close search'}
    })[window.tenantI18n?.language||'az'];
    const tools=document.createElement('div'),body=document.createElement('div');
    tools.className='deal-linked-dialog-tools';
    body.className='deal-linked-dialog-body';
    panel.append(tools,body);
    const slot=document.createComment('deal chat position');
    let active=false,disposed=false,resumeNeeded=false,request,conversation,routes,selection,historyRequest;
    let phoneRequest,autoLinkRequest,canFindWhatsApp=false;
    const snapshots=new Map();
    const visible=()=>active&&!disposed&&current()&&panel.isConnected&&!panel.hidden;
    const button=(label,callback)=>{
      const node=document.createElement('button');
      node.type='button';node.className='outline';node.textContent=label;node.onclick=callback;
      return node;
    };
    function message(text,retry){
      const status=document.createElement('span');
      status.setAttribute('role','status');
      status.textContent=text;
      tools.replaceChildren(status);
      if(retry)tools.append(button(c.retry,retry));
    }
    function stopConversation(keep=true){
      historyRequest?.abort();
      historyRequest=null;
      phoneRequest?.abort();
      phoneRequest=null;
      autoLinkRequest?.abort();
      autoLinkRequest=null;
      if(conversation){
        const snapshot=conversation.release();
        if(keep&&snapshot&&selection)snapshots.set(selection,snapshot);
        conversation=null;
      }
      if(slot.parentNode)slot.replaceWith(chat);
      body.replaceChildren();
    }
    async function findWhatsApp(route,messageId){
      if(!visible()||selection!==route)return;
      phoneRequest?.abort();
      const controller=new AbortController();
      phoneRequest=controller;
      const currentPhone=()=>visible()&&selection===route&&!controller.signal.aborted;
      const status=document.createElement('span');
      status.setAttribute('role','status');
      status.textContent=whatsappCopy.loading;
      const dismiss=button(whatsappCopy.close,()=>{
        controller.abort();
        tools.replaceChildren();
        if(routes.length>1)tools.append(button(c.back,choose));
      });
      tools.replaceChildren(status,dismiss);
      const query=new URLSearchParams({deal_id:id,message_id:messageId,
        expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
      try{
        const payload=await api('/api/platform/ai/dialogs/deal-phone-candidates?'+query,{signal:controller.signal});
        if(!currentPhone())return;
        const candidates=payload.result?.candidates||[];
        status.textContent=candidates.length?'':whatsappCopy.empty;
        const actions=candidates.map(candidate=>button(
          [whatsappCopy.attach,candidate.channel_name].filter(Boolean).join(' · '),async()=>{
            if(!currentPhone())return;
            for(const action of actions)action.disabled=true;
            dismiss.disabled=true;
            try{
              const result=await api('/api/platform/ai/dialogs/deal-link',{
                method:'POST',signal:controller.signal,body:JSON.stringify({
                  channel_id:candidate.channel_id,conversation_id:candidate.conversation_id,
                  lead_id:Number(candidate.lead_id),context_id:candidate.context_id,
                  deal_source:source,deal_id:String(id),suggested:false,phone_message_id:messageId,
                  expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id
                })
              });
              if(!currentPhone())return;
              if(!result.result?.link_id)throw Error(whatsappCopy.error);
              stopConversation();
              selection=null;
              routes=null;
              void resolve(result.result.link_id);
            }catch(error){
              if(!currentPhone())return;
              status.textContent=whatsappCopy.error;
              for(const action of actions)action.disabled=false;
              dismiss.disabled=false;
            }
          }));
        tools.append(...actions);
      }catch(error){
        if(currentPhone())status.textContent=whatsappCopy.error;
      }
    }
    async function attemptAutoLink(route){
      if(!visible()||selection!==route||phoneRequest||autoLinkRequest)return;
      const controller=new AbortController();
      autoLinkRequest=controller;
      const currentLink=()=>visible()&&selection===route&&!controller.signal.aborted;
      try{
        const payload=await api('/api/platform/ai/dialogs/deal-link',{
          method:'POST',signal:controller.signal,body:JSON.stringify({
            deal_id:String(id),auto_phone:true,
            expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id
          })
        });
        const linkId=payload.result?.link_id;
        if(!currentLink()||!linkId)return;
        const query=new URLSearchParams({source,deal_id:id,
          expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
        const refreshed=await api('/api/platform/ai/dialogs/for-deal?'+query,{signal:controller.signal});
        if(!currentLink())return;
        const next=refreshed.result?.dialogs||[];
        const linked=next.find(item=>item.deal_link_id===linkId);
        if(!linked)return;
        routes=next;
        const label=['WhatsApp',linked.channel_name==='WhatsApp'?'':linked.channel_name];
        tools.append(button(label.filter(Boolean).join(' · '),()=>show(linked)));
      }catch{
        // A failed or ambiguous automatic attempt leaves the chat and manual action available.
      }finally{
        if(autoLinkRequest===controller)autoLinkRequest=null;
      }
    }
    function choose(){
      stopConversation();
      selection=null;
      message(c.choose);
      for(const route of routes){
        const label=route.deal_link_id
          ? ['WhatsApp',route.channel_name==='WhatsApp'?'':route.channel_name]
          : [route.customer_name,route.channel_name];
        tools.append(button(label.filter(Boolean).join(' · '),()=>show(route)));
      }
    }
    async function showLinked(route){
      const controller=new AbortController();
      historyRequest=controller;
      const currentHistory=()=>visible()&&selection===route&&!controller.signal.aborted;
      const transcript=document.createElement('div');
      transcript.className='operator-chat-history deal-linked-transcript';
      transcript.setAttribute('aria-label','WhatsApp');
      const messages=[];
      let cursor=null,loading=false;
      async function load(before=null){
        if(loading)return;
        loading=true;
        const query=new URLSearchParams({source,deal_id:id,link_id:route.deal_link_id,
          expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
        if(before)query.set('before',before);
        try{
          const reply=await api('/api/platform/ai/dialogs/deal-history?'+query,{signal:controller.signal});
          if(!currentHistory())return;
          const page=reply.result;
          if(page?.dialog?.deal_link_id!==route.deal_link_id)throw Error(c.error);
          cursor=page.next_before;
          const height=transcript.scrollHeight,top=transcript.scrollTop;
          messages.unshift(...(page.messages||[]));
          transcript.innerHTML=window.tenantOperator.messageMarkup(messages,route,{readOnly:true});
          if(!messages.length)transcript.textContent=linkedCopy.empty;
          if(!transcript.isConnected)body.append(transcript);
          window.tenantOperator.bindHistoryMessages(transcript,messages,currentHistory);
          document.dispatchEvent(new CustomEvent('tenant-chat-attachments-ready'));
          transcript.scrollTop=before?top+transcript.scrollHeight-height:transcript.scrollHeight;
          tools.replaceChildren();
          if(routes.length>1)tools.append(button(c.back,choose));
          tools.append(button(linkedCopy.full,()=>show(route,true)));
          if(cursor)tools.append(button(linkedCopy.earlier,()=>load(cursor)));
        }catch(error){
          if(!currentHistory())return;
          if([401,403,404,409].includes(error.status)){
            body.replaceChildren();
            selection=null;
            routes=null;
            message(c.empty,resolve);
          }else message(c.error,()=>load(before));
        }finally{loading=false;}
      }
      message(c.loading);
      await load();
    }
    async function show(route,full=false){
      if(!visible())return;
      stopConversation();
      selection=route;
      if(route.deal_link_id&&!full){void showLinked(route);return;}
      tools.replaceChildren();
      if(routes.length>1)tools.append(button(c.back,choose));
      if(route.deal_link_id)tools.append(button(linkedCopy.scoped,()=>show(route)));
      chat.replaceWith(slot);
      resumeNeeded=true;
      const cached=snapshots.get(route);
      conversation=window.tenantOperator?.mountConversation(body,cached?.dialog||route,{
        snapshot:cached,
        onPhoneLookup:canFindWhatsApp&&route.provider==='kommo'?messageId=>findWhatsApp(route,messageId):null,
        onAccessLost:error=>{
          snapshots.delete(route);
          stopConversation(false);
          routes=null;
          selection=null;
          if(visible())message(error.status===409?c.error:c.empty,resolve);
        }
      });
      if(!conversation){stopConversation(false);message(c.empty);return;}
      const opening=conversation;
      const opened=await opening.open();
      if(!opened&&visible()&&conversation===opening){
        tools.append(button(c.retry,()=>{stopConversation(false);void show(route,full);}));
      }else if(opened&&conversation===opening&&route.provider==='kommo'
          &&canFindWhatsApp&&!routes.some(item=>item.deal_link_id)){
        void attemptAutoLink(route);
      }
    }
    async function resolve(preferredLink=null){
      if(!visible()||request)return;
      if(!member||!window.tenantOperator?.hasRight('dialogs')){message(c.empty);return;}
      const controller=new AbortController();
      request=controller;
      message(c.loading);
      const query=new URLSearchParams({source,deal_id:id,expected_tenant_id:member.tenant_id,expected_user_id:member.telegram_id});
      try{
        const response=await api('/api/platform/ai/dialogs/for-deal?'+query,{signal:controller.signal});
        if(!visible()||controller.signal.aborted)return;
        routes=response.result?.dialogs||[];
        canFindWhatsApp=response.result?.can_find_whatsapp===true;
        const selected=typeof preferredLink==='string'&&routes.find(route=>route.deal_link_id===preferredLink);
        if(selected)void show(selected);
        else if(routes.length===1)void show(routes[0]);
        else if(routes.length)choose();
        else message(c.empty);
      }catch(error){
        if(!visible()||controller.signal.aborted)return;
        if([401,403,404].includes(error.status))message(c.empty);
        else message(c.error,resolve);
      }finally{if(request===controller)request=null;}
    }
    function hide(){
      active=false;
      request?.abort();
      request=null;
      stopConversation();
    }
    function dispose(){
      if(disposed)return;
      disposed=true;
      hide();
      snapshots.clear();
      observer.disconnect();
      panel.removeEventListener('deal-pane-changed',changePane);
    }
    function changePane(event){
      if(event.detail.selected==='dialog'){
        active=true;
        if(selection)void show(selection);
        else if(routes?.length)choose();
        else void resolve();
      }else{
        if(active)hide();
        if(event.detail.selected==='chat'&&resumeNeeded){
          resumeNeeded=false;
          resumeChat();
        }
      }
    }
    const observer=new MutationObserver(()=>{if(!current()||!panel.isConnected)dispose();});
    observer.observe(modal,{attributes:true,attributeFilter:['class','data-revision','data-type'],subtree:true,childList:true});
    panel.addEventListener('deal-pane-changed',changePane);
    return {dispose};
  }
  window.tenantDealDialogue={mount};
})();
