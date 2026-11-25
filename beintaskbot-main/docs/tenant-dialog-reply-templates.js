/* Local reply templates: suggestions only edit the existing draft; this module never sends messages. */
(()=>{
  'use strict';
  const endpoint='/api/platform/ai/dialogs/reply-templates';
  const words={
    ru:{button:'Шаблоны',manage:'Управлять шаблонами',title:'Шаблоны ответов',new:'Новый шаблон',use:'Вставить в черновик',edit:'Изменить',remove:'Удалить',close:'Закрыть',name:'Название',body:'Текст шаблона',visibility:'Доступ',personal:'Личный',team:'Команде',shared:'Общий',save:'Сохранить',cancel:'Отмена',loading:'Загружаю шаблоны…',empty:'Пока нет доступных шаблонов.',replace:'Заменить',hint:'Шаблон добавляется к черновику. Отправка остаётся за вами.',saved:'Шаблон сохранён.',deleted:'Шаблон удалён.',error:'Не удалось загрузить или сохранить шаблоны.',suggestions:'Похожие шаблоны',unavailable:'Управление шаблонами недоступно.'},
    az:{button:'Şablonlar',manage:'Şablonları idarə et',title:'Cavab şablonları',new:'Yeni şablon',use:'Qaralamaya əlavə et',edit:'Dəyişdir',remove:'Sil',close:'Bağla',name:'Ad',body:'Şablon mətni',visibility:'Giriş',personal:'Şəxsi',team:'Komanda',shared:'Ümumi',save:'Yadda saxla',cancel:'Ləğv et',loading:'Şablonlar yüklənir…',empty:'Əlçatan şablon yoxdur.',replace:'Əvəz et',hint:'Şablon qaralamaya əlavə olunur. Göndərməyə siz qərar verirsiniz.',saved:'Şablon saxlanıldı.',deleted:'Şablon silindi.',error:'Şablonları yükləmək və ya saxlamaq mümkün olmadı.',suggestions:'Oxşar şablonlar',unavailable:'Şablonların idarə edilməsi əlçatan deyil.'},
    en:{button:'Templates',manage:'Manage templates',title:'Reply templates',new:'New template',use:'Insert into draft',edit:'Edit',remove:'Delete',close:'Close',name:'Name',body:'Template text',visibility:'Access',personal:'Personal',team:'Team',shared:'Shared',save:'Save',cancel:'Cancel',loading:'Loading templates…',empty:'No templates are available yet.',replace:'Replace',hint:'The template is added to the draft. You decide when to send.',saved:'Template saved.',deleted:'Template deleted.',error:'Could not load or save templates.',suggestions:'Similar templates',unavailable:'Template management is unavailable.'}
  };
  const copy=key=>(words[window.tenantI18n?.language]||words.az)[key]||key;
  const icons={
    close:'<path d="m6 6 12 12M18 6 6 18"/>',
    edit:'<path d="m14 5 5 5M4 20l4-.8L20 7a2.8 2.8 0 0 0-4-4L4 15.9 4 20Z"/>',
    remove:'<path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"/>'
  };
  function iconButton(icon,label){
    const button=document.createElement('button');
    button.type='button';button.className='operator-reply-template-icon';
    button.title=label;button.setAttribute('aria-label',label);
    button.innerHTML='<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+icons[icon]+'</svg>';
    return button;
  }
  const identity=()=>{
    const member=window.tenantProfileSnapshot?.member;
    if(!member?.tenant_id||member?.telegram_id==null)return null;
    return {tenant:String(member.tenant_id),user:String(member.telegram_id),key:String(member.tenant_id)+':'+String(member.telegram_id)};
  };
  let mounted=null,modalClose=null;
  const style=document.createElement('style');
  style.textContent=[
    '.operator-reply-template-root{position:relative}',
    '.operator-reply-template-suggestions{position:absolute;z-index:35;left:6px;right:6px;bottom:calc(100% + 6px);max-height:36dvh;overflow:auto;overscroll-behavior:contain;padding:6px;border:1px solid var(--line,#d9e3de);border-radius:12px;background:var(--card,#fff);color:var(--ink,#18332c);box-shadow:0 10px 30px #0003}',
    '.operator-reply-template-suggestions[hidden]{display:none!important}',
    '.operator-reply-template-suggestions>strong{display:block;padding:7px 9px;color:var(--muted,#64776f);font-size:12px}',
    '.operator-reply-template-suggestions button{display:grid;width:100%;min-height:44px;gap:3px;padding:8px 10px;border:0;border-radius:8px;background:transparent;color:inherit;text-align:left;white-space:normal}',
    '.operator-reply-template-suggestions button:hover,.operator-reply-template-suggestions button:focus-visible{background:var(--paper,#f4f8f5)}',
    '.operator-reply-template-suggestion{display:flex;align-items:center;gap:4px}',
    '.operator-reply-template-suggestion>button:first-child{flex:1;min-width:0}',
    '.operator-reply-template-suggestion>button:first-child span{overflow-wrap:anywhere}',
    '.operator-reply-template-suggestion .operator-reply-template-replace{display:block;flex:0 0 auto;width:auto;min-width:44px;font-size:12px;color:var(--brand-deep,#14523c);white-space:nowrap}',
    '.operator-reply-template-suggestions small{overflow:hidden;color:var(--muted,#64776f);text-overflow:ellipsis;white-space:nowrap}',
  ].join('')+`
    .operator-reply-template-card .operator-reply-template-icon {
      display:inline-grid; place-items:center; width:44px; height:44px;
      min-width:44px; min-height:44px; padding:0; border:0; border-radius:10px;
      flex:none; background:transparent; color:var(--ink,#18332c);
    }
    #operatorReplyTemplatesOpen[hidden] { display:none!important }
    .operator-reply-template-icon svg {
      display:block; width:20px; height:20px; pointer-events:none;
    }
    .operator-reply-template-card .operator-reply-template-icon:hover { background:var(--paper,#f4f8f5) }
    .operator-reply-template-card .operator-reply-template-delete { color:var(--danger,#b42318) }
    .operator-reply-template-modal {
      position:fixed; z-index:1500; inset:0; display:grid; place-items:center;
      padding:10px; background:#071d165e;
    }
    .operator-reply-template-card {
      display:flex; flex-direction:column; gap:10px; box-sizing:border-box;
      width:min(464px,100%); max-height:calc(100dvh - 20px);
      overflow:auto; overscroll-behavior:contain; padding:14px 16px 16px;
      border:1px solid var(--line,#d9e3de); border-radius:18px;
      background:var(--card,#fff); color:var(--ink,#18332c); box-shadow:0 16px 44px #071d1633;
    }
    .operator-reply-template-head,.operator-reply-template-actions {
      display:flex; align-items:center; justify-content:space-between; gap:8px;
    }
    .operator-reply-template-head h2 { margin:0; font-size:18px; line-height:1.3 }
    .operator-reply-template-hint,.operator-reply-template-notice {
      margin:0; color:var(--muted,#64776f); font-size:13px; overflow-wrap:anywhere;
    }
    .operator-reply-template-list {
      display:grid; align-content:start; gap:8px; min-height:min(100px,22dvh);
      max-height:min(300px,38dvh); overflow:auto; overscroll-behavior:contain;
      scrollbar-width:thin; padding:2px 4px 2px 2px;
    }
    .operator-reply-template-list:empty { min-height:0 }
    .operator-reply-template-row {
      display:grid; grid-template-columns:minmax(0,1fr) auto; gap:2px;
      align-items:start; border:1px solid var(--line,#d9e3de);
      border-radius:12px; background:var(--card,#fff);
    }
    .operator-reply-template-row:has(.operator-reply-template-use:enabled:is(:hover,:focus-visible)) {
      background:var(--paper,#f4f8f5); border-color:var(--brand,#0a926e);
    }
    .operator-reply-template-row .operator-reply-template-use {
      display:grid; gap:4px; min-width:0; min-height:58px; width:100%; text-align:start;
      padding:10px 12px; border:0; border-radius:11px; background:transparent;
      color:inherit; font:inherit; white-space:normal;
    }
    .operator-reply-template-use strong { font-size:14px; overflow-wrap:anywhere }
    .operator-reply-template-use small {
      color:var(--muted,#64776f); overflow-wrap:anywhere; white-space:pre-line;
      font-size:13px; line-height:1.35; display:-webkit-box;
      -webkit-box-orient:vertical; -webkit-line-clamp:3; overflow:hidden;
    }
    .operator-reply-template-use .operator-reply-template-scope { color:var(--muted,#64776f); font-size:11px }
    .operator-reply-template-row-actions { display:flex; padding:2px 2px 0 0 }
    .operator-reply-template-editor { border-top:1px solid var(--line,#d9e3de); padding-top:10px }
    .operator-reply-template-editor:empty { display:none }
    .operator-reply-template-editor form { display:grid; gap:8px; margin:0 }
    .operator-reply-template-editor h3 { margin:0; font-size:15px }
    .operator-reply-template-editor input,.operator-reply-template-editor textarea,.operator-reply-template-editor select {
      box-sizing:border-box; width:100%; min-width:0; min-height:40px; margin:0;
      padding:8px 10px; border:1px solid var(--line,#ccdcd3); border-radius:9px;
      background:var(--card,#fff); color:inherit; font:inherit;
    }
    .operator-reply-template-editor textarea { min-height:70px; max-height:180px; resize:vertical }
    .operator-reply-template-editor label { display:flex; align-items:center; gap:8px; color:var(--muted,#64776f); font-size:13px }
    .operator-reply-template-editor select { width:auto; max-width:100%; min-height:36px }
    .operator-reply-template-actions { justify-content:flex-end }
    .operator-reply-template-actions button { min-height:44px }
    .operator-reply-template-card :is(button,input,textarea,select):focus-visible {
      outline:2px solid var(--brand,#0a926e); outline-offset:1px;
    }
    @media(max-height:500px) {
      .operator-reply-template-list { min-height:96px; max-height:150px; flex:none }
    }
  `;
  document.head.append(style);
  function request(method,data,owner){
    const params=new URLSearchParams({expected_tenant_id:owner.tenant,expected_user_id:owner.user});
    const options={method,credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}};
    let url=endpoint+'?'+params.toString();
    if(method==='POST'){
      options.headers['Content-Type']='application/json';
      options.body=JSON.stringify({...data,expected_tenant_id:owner.tenant,expected_user_id:owner.user});
    }
    return fetch(url,options).then(async response=>{
      let payload;
      try{payload=await response.json();}catch{throw new Error(copy('error'));}
      if(!response.ok||payload?.success!==true){
        const error=new Error(payload?.error||copy('error'));error.status=response.status;throw error;
      }
      return payload.result||{};
    });
  }
  function makeId(){
    if(window.crypto?.randomUUID)return window.crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{
      const r=Math.random()*16|0;return (c==='x'?r:(r&3|8)).toString(16);
    });
  }
  const composerRoot=()=>document.querySelector('#view-dialogs.active #operatorConversationContent [data-operator-dialog] .operator-composer');
  const canInsert=state=>state===mounted&&identity()?.key===state.identity&&window.tenantProfileSnapshot?.capabilities?.dialog_reply_templates===true&&
    state.input.isConnected&&state.root===composerRoot()&&!state.input.disabled&&!state.input.readOnly;
  function completesPrefix(current,body,input){
    const prefix=current.normalize('NFC');
    // Offer an explicit replacement only for a bare short fragment that
    // actually starts the first word. Even a matching fragment can be a
    // deliberate short word, so it is never discarded by ordinary insertion.
    if(!/^\p{L}{2,4}$/u.test(prefix)||input.selectionStart!==current.length||input.selectionEnd!==current.length)return false;
    const firstWord=body.normalize('NFC').match(/^\p{L}+/u)?.[0];
    if(!firstWord)return false;
    const language=window.tenantI18n?.language;
    const locale=language==='ru'||language==='en'?language:'az';
    const typed=prefix.toLocaleLowerCase(locale),start=firstWord.toLocaleLowerCase(locale);
    return start.length>typed.length&&start.startsWith(typed);
  }
  function insert(item,state,replacePrefix=null){
    if(!canInsert(state))return false;
    const {input}=state,current=input.value,body=String(item.body||'');
    if(!body)return false;
    if(replacePrefix!==null&&(current!==replacePrefix||!completesPrefix(current,body,input)))return false;
    input.value=!current||replacePrefix!==null
      ?body:current+(current.endsWith('\n')?'':'\n')+body;
    state.input.dispatchEvent(new CustomEvent('input',{bubbles:true,detail:{replyInserted:true}}));
    state.input.focus();
    try{state.input.setSelectionRange(state.input.value.length,state.input.value.length);}catch{}
    return true;
  }
  function renderSuggestions(state){
    if(!canInsert(state)||!state.showSuggestions){state.suggestions.hidden=true;state.suggestions.replaceChildren();return;}
    const language=window.tenantI18n?.language;
    const locale=language==='en'||language==='ru'?language:'az';
    const alternate=locale==='az'?'en':'az';
    const query=(state.input.value||'').trim();
    state.suggestions.replaceChildren();
    if(query.length<2||!state.templates.length){state.suggestions.hidden=true;return;}
    const words=query.split(/\s+/).slice(-8);
    const terms=words.map(word=>word.toLocaleLowerCase(locale));
    const alternateTerms=words.map(word=>word.toLocaleLowerCase(alternate));
    const score=(item,terms,language)=>{
      const name=String(item.name||'').toLocaleLowerCase(language);
      const body=String(item.body||'').toLocaleLowerCase(language);
      return terms.reduce((total,term)=>total+(name.includes(term)?3:body.includes(term)?1:0),0);
    };
    const matches=state.templates.map(item=>{
      const primary=score(item,terms,locale);
      // Uppercase I is ambiguous in a mixed catalogue. Try the other casing
      // rules only for misses, without removing marks or mapping lowercase i/ı.
      return {item,primary,fallback:primary?0:score(item,alternateTerms,alternate)};
    }).filter(entry=>entry.primary||entry.fallback)
      .sort((a,b)=>b.primary-a.primary||b.fallback-a.fallback||String(a.item.name).localeCompare(String(b.item.name))).slice(0,5);
    if(!matches.length){state.suggestions.hidden=true;return;}
    const title=document.createElement('strong');title.textContent=copy('suggestions');state.suggestions.append(title);
    for(const {item} of matches){
      const row=document.createElement('div');row.className='operator-reply-template-suggestion';
      const button=document.createElement('button');button.type='button';
      const name=document.createElement('span');name.textContent=item.name;
      const snippet=document.createElement('small');snippet.textContent=String(item.body||'').replace(/\s+/g,' ').slice(0,140);
      button.append(name,snippet);button.setAttribute('aria-label',copy('use')+': '+item.name);
      button.addEventListener('click',()=>insert(item,state));row.append(button);
      const prefix=state.input.value;
      if(completesPrefix(prefix,String(item.body||''),state.input)){
        const replace=document.createElement('button');replace.type='button';
        replace.className='operator-reply-template-replace';replace.dataset.replyTemplateReplace='';
        replace.textContent=copy('replace')+' «'+prefix+'»';
        replace.setAttribute('aria-label',replace.textContent+': '+item.name);
        replace.addEventListener('click',()=>{if(!insert(item,state,prefix))renderSuggestions(state);});
        row.append(replace);
      }
      state.suggestions.append(row);
    }
    state.suggestions.hidden=false;
  }
  async function loadTemplates(state){
    const serial=++state.loadSerial,owner=identity();
    if(!owner||owner.key!==state.identity)return;
    state.loading=true;state.error='';
    let changed=false;
    try{
      const result=await request('GET',null,owner);
      if(state!==mounted||serial!==state.loadSerial||identity()?.key!==state.identity)return;
      if(result.available!==true){teardown();return;}
      const templates=Array.isArray(result.templates)?result.templates:[];
      const allowed=Array.isArray(result.allowed_visibility)?result.allowed_visibility:[];
      changed=!state.loaded||JSON.stringify([state.templates,state.allowed])!==JSON.stringify([templates,allowed]);
      state.templates=templates;state.allowed=allowed;state.button.hidden=false;state.loaded=true;
    }catch(error){
      if(state!==mounted||serial!==state.loadSerial||identity()?.key!==state.identity)return;
      if([401,403,404,409].includes(error?.status)||!state.loaded){teardown();return;}
      // Keep an already loaded list during a temporary service/network failure.
    }finally{
      if(state===mounted&&serial===state.loadSerial){
        state.loading=false;
        if(changed){renderSuggestions(state);state.renderManager?.();}
      }
    }
  }
  function visibilityLabel(value){return copy(value==='personal'?'personal':value==='team'?'team':'shared');}
  function openManager(state){
    if(state!==mounted)return;
    modalClose?.();
    const restore=state.menu.closest('details')?.querySelector('summary')||document.activeElement;
    const modal=document.createElement('div'),card=document.createElement('section');
    modal.className='operator-reply-template-modal';modal.id='operatorReplyTemplateModal';
    card.className='operator-reply-template-card';card.setAttribute('role','dialog');card.setAttribute('aria-modal','true');card.setAttribute('aria-labelledby','operatorReplyTemplateTitle');
    const header=document.createElement('div');header.className='operator-reply-template-head';
    const title=document.createElement('h2');title.id='operatorReplyTemplateTitle';title.textContent=copy('title');
    const close=iconButton('close',copy('close'));
    header.append(title,close);
    const hint=document.createElement('p');hint.className='operator-reply-template-hint';hint.textContent=copy('hint');
    const notice=document.createElement('p');notice.className='operator-reply-template-notice';notice.setAttribute('role','status');
    const list=document.createElement('div'),editor=document.createElement('div');
    list.className='operator-reply-template-list';editor.className='operator-reply-template-editor';
    card.append(header,hint,list,editor,notice);modal.append(card);document.body.append(modal);
    let closed=false;
    const owner=()=>{
      const current=identity();
      return !closed&&state===mounted&&state.root===composerRoot()&&current?.key===state.identity&&
        window.tenantProfileSnapshot?.capabilities?.dialog_reply_templates===true?current:null;
    };
    const finish=(restoreFocus=true)=>{
      if(closed)return;closed=true;modal.remove();document.removeEventListener('keydown',key,true);
      if(modalClose===finish)modalClose=null;
      state.renderManager=null;
      state.button.setAttribute('aria-expanded','false');
      if(restoreFocus&&restore?.isConnected)restore.focus({preventScroll:true});
    };
    const key=event=>{
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();finish();return;}
      if(event.key!=='Tab')return;
      const controls=[...card.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled)')];
      const first=controls[0],last=controls.at(-1),focused=document.activeElement;
      if(!card.contains(focused)||event.shiftKey&&focused===first||!event.shiftKey&&focused===last){
        event.preventDefault();(event.shiftKey?last:first)?.focus();
      }
    };
    modalClose=finish;state.button.setAttribute('aria-expanded','true');
    document.addEventListener('keydown',key,true);close.addEventListener('click',()=>finish());
    modal.addEventListener('pointerdown',event=>{if(event.target===modal)finish();});
    function showEditor(item=null,focus=false){
      editor.replaceChildren();
      if(!state.allowed.length)return;
      const form=document.createElement('form'),heading=document.createElement('h3');
      heading.textContent=copy(item?'edit':'new');
      const name=document.createElement('input'),body=document.createElement('textarea');
      name.name='name';name.maxLength=120;name.required=true;name.value=item?.name||'';
      name.placeholder=copy('name');name.setAttribute('aria-label',copy('name'));
      body.name='body';body.maxLength=4000;body.required=true;body.value=item?.body||'';
      body.placeholder=copy('body');body.setAttribute('aria-label',copy('body'));
      const visibilityLabelNode=document.createElement('label'),visibilityText=document.createElement('span'),visibility=document.createElement('select');
      visibilityText.textContent=copy('visibility');visibility.name='visibility';visibility.required=true;
      const choices=state.allowed.slice();
      if(item?.can_edit&&item.visibility&&!choices.includes(item.visibility))choices.push(item.visibility);
      for(const choice of choices){const option=document.createElement('option');option.value=choice;option.textContent=visibilityLabel(choice);visibility.append(option);}
      if(item?.visibility&&choices.includes(item.visibility))visibility.value=item.visibility;
      visibilityLabelNode.append(visibilityText,visibility);
      const actions=document.createElement('div');actions.className='operator-reply-template-actions';
      const save=document.createElement('button');save.type='submit';save.className='primary';save.textContent=copy('save');
      const cancel=document.createElement('button');cancel.type='button';cancel.textContent=copy('cancel');
      if(item)actions.append(cancel);
      actions.append(save);form.append(heading,name,body,visibilityLabelNode,actions);editor.append(form);
      let saving=false;
      const updateSave=()=>{save.disabled=saving||!name.value.trim()||!body.value.trim();};
      form.addEventListener('input',updateSave);updateSave();
      cancel.addEventListener('click',()=>{notice.textContent='';showEditor(null,true);});
      if(focus)name.focus();
      form.addEventListener('submit',async event=>{
        event.preventDefault();
        const current=owner();
        if(!current||!form.isConnected||saving||!name.value.trim()||!body.value.trim()||!form.reportValidity())return;
        saving=true;updateSave();cancel.disabled=true;notice.textContent=copy('loading');
        const payload=item
          ?{action:'update',template_id:item.id,version:item.version,name:name.value,body:body.value,visibility:visibility.value}
          :{action:'create',request_id:makeId(),name:name.value,body:body.value,visibility:visibility.value};
        try{
          await request('POST',payload,current);
          if(state===mounted&&identity()?.key===state.identity){
            if(!closed){
              notice.textContent=copy('saved');
              if(form.isConnected)showEditor();
            }
            await loadTemplates(state);
          }
        }catch(error){if(!closed)notice.textContent=error?.message||copy('error');}
        finally{saving=false;updateSave();cancel.disabled=false;}
      });
    }
    function renderList(){
      if(closed)return;
      list.replaceChildren();
      if(state.loading&&!state.loaded){list.textContent=copy('loading');return;}
      if(state.error){list.textContent=state.error;return;}
      if(!state.templates.length){list.textContent=copy('empty');return;}
      for(const item of state.templates){
        const row=document.createElement('article');row.className='operator-reply-template-row';
        const name=document.createElement('strong'),description=document.createElement('small'),scope=document.createElement('span');
        name.textContent=item.name;description.textContent=item.body;
        scope.className='operator-reply-template-scope';scope.textContent=visibilityLabel(item.visibility);
        const controls=document.createElement('div');controls.className='operator-reply-template-row-actions';
        const use=document.createElement('button');use.type='button';use.className='operator-reply-template-use';
        use.setAttribute('aria-label',copy('use')+': '+item.name);
        use.append(name,description,scope);
        use.dataset.replyTemplateInsert='';use.disabled=!canInsert(state);use.addEventListener('click',()=>{if(insert(item,state))finish(false);});
        row.append(use);
        if(item.can_edit){
          const edit=iconButton('edit',copy('edit')+': '+item.name);
          edit.addEventListener('click',()=>{notice.textContent='';showEditor(item,true);});
          const remove=iconButton('remove',copy('remove')+': '+item.name);
          remove.classList.add('operator-reply-template-delete');remove.addEventListener('click',async()=>{
            const current=owner();
            if(!current||remove.disabled||!window.confirm(copy('remove')+': '+item.name+'?'))return;
            remove.disabled=true;notice.textContent=copy('loading');
            try{
              await request('POST',{action:'delete',template_id:item.id,version:item.version},current);
              if(state===mounted&&identity()?.key===state.identity){
                if(!closed)notice.textContent=copy('deleted');
                await loadTemplates(state);
              }
            }catch(error){if(!closed)notice.textContent=error?.message||copy('error');remove.disabled=false;}
          });
          controls.append(edit,remove);row.append(controls);
        }
        list.append(row);
      }
    }
    state.renderManager=renderList;
    renderList();showEditor();close.focus();
  }
  function teardown(){
    if(!mounted)return;
    mounted.inputObserver.disconnect();
    mounted.input.removeEventListener('input',mounted.onInput);
    mounted.button.remove();mounted.suggestions.remove();
    mounted.root.classList.remove('operator-reply-template-root');
    mounted=null;modalClose?.();
  }
  async function attach(){
    const root=composerRoot(),input=root?.querySelector('#chatText[data-operator-composer]'),owner=identity();
    const menu=document.querySelector('#view-dialogs.active #operatorConversationHeader #operatorMenuExtra');
    if(!menu||!input||!owner||window.tenantProfileSnapshot?.capabilities?.dialog_reply_templates!==true){teardown();return;}
    if(mounted?.root===root&&mounted.menu===menu&&mounted.identity===owner.key){if(!mounted.loaded&&!mounted.loading)void loadTemplates(mounted);return;}
    teardown();
    root.classList.add('operator-reply-template-root');
    const button=document.createElement('button');button.type='button';
    button.hidden=true;button.id='operatorReplyTemplatesOpen';button.textContent=copy('button');
    button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-expanded','false');
    button.setAttribute('role','menuitem');
    const suggestions=document.createElement('div');suggestions.className='operator-reply-template-suggestions';suggestions.hidden=true;suggestions.setAttribute('role','listbox');suggestions.setAttribute('aria-label',copy('suggestions'));
    const state={menu,input,root,button,suggestions,identity:owner.key,templates:[],allowed:[],loaded:false,loading:false,loadSerial:0,error:'',available:false,renderManager:null,onInput:null,showSuggestions:false};
    state.onInput=event=>{
      state.showSuggestions=event.detail?.replyInserted!==true;
      renderSuggestions(state);
    };
    state.inputObserver=new MutationObserver(()=>{
      if(state!==mounted)return;
      renderSuggestions(state);
      document.querySelectorAll('#operatorReplyTemplateModal [data-reply-template-insert]').forEach(node=>{node.disabled=!canInsert(state);});
    });
    state.inputObserver.observe(input,{attributes:true,attributeFilter:['disabled','readonly']});
    state.input.addEventListener('input',state.onInput);
    button.addEventListener('click',()=>{
      const details=menu.closest('details');
      if(details)details.open=false;
      openManager(state);
    });
    menu.append(button);root.append(suggestions);mounted=state;
    renderSuggestions(state);void loadTemplates(state);
  }
  document.addEventListener('tenant-chat-attachments-ready',()=>{void attach();});
  document.addEventListener('tenant-profile',event=>{
    if(event.detail?.capabilities?.dialog_reply_templates!==true){teardown();return;}
    if(!mounted){void attach();return;}
    const next=event.detail?.member;
    const key=next?.tenant_id!=null&&next?.telegram_id!=null?String(next.tenant_id)+':'+String(next.telegram_id):'';
    if(key!==mounted.identity){teardown();void attach();return;}
    if(!mounted.loading)void loadTemplates(mounted);
  });
  document.addEventListener('tenant-dialog-reauthorized',()=>{
    if(!mounted){void attach();return;}
    if(!mounted.loading)void loadTemplates(mounted);
  });
  for(const event of ['tenant-chat-close','tenant-auth-invalid','tenant-auth-pending'])document.addEventListener(event,teardown);
  document.addEventListener('tenant-view',event=>{
    if(event.detail?.view==='dialogs'){void attach();return;}
    teardown();
  });
  document.addEventListener('pointerdown',event=>{
    if(mounted&&!mounted.suggestions.hidden&&!mounted.root.contains(event.target)){mounted.suggestions.hidden=true;}
  });
  attach();
})();
