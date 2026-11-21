/* Local reply templates: suggestions only edit the existing draft; this module never sends messages. */
(()=>{
  'use strict';
  const endpoint='/api/platform/ai/dialogs/reply-templates';
  const words={
    ru:{button:'Шаблоны',manage:'Управлять шаблонами',title:'Шаблоны ответов',new:'Новый шаблон',use:'Вставить в черновик',edit:'Изменить',remove:'Удалить',close:'Закрыть',name:'Название',body:'Текст шаблона',visibility:'Доступ',personal:'Личный',team:'Команде',shared:'Общий',save:'Сохранить',cancel:'Отмена',loading:'Загружаю шаблоны…',empty:'Пока нет доступных шаблонов.',hint:'Выбор только вставит текст в черновик. Отправка остаётся за вами.',saved:'Шаблон сохранён.',deleted:'Шаблон удалён.',error:'Не удалось загрузить или сохранить шаблоны.',suggestions:'Похожие шаблоны',unavailable:'Управление шаблонами недоступно.'},
    az:{button:'Şablonlar',manage:'Şablonları idarə et',title:'Cavab şablonları',new:'Yeni şablon',use:'Qaralamaya əlavə et',edit:'Dəyişdir',remove:'Sil',close:'Bağla',name:'Ad',body:'Şablon mətni',visibility:'Giriş',personal:'Şəxsi',team:'Komanda',shared:'Ümumi',save:'Yadda saxla',cancel:'Ləğv et',loading:'Şablonlar yüklənir…',empty:'Əlçatan şablon yoxdur.',hint:'Seçim mətni yalnız redaktə edilə bilən qaralamaya əlavə edir. Göndərmə sizin qərarınızdır.',saved:'Şablon saxlanıldı.',deleted:'Şablon silindi.',error:'Şablonları yükləmək və ya saxlamaq mümkün olmadı.',suggestions:'Oxşar şablonlar',unavailable:'Şablonların idarə edilməsi əlçatan deyil.'},
    en:{button:'Templates',manage:'Manage templates',title:'Reply templates',new:'New template',use:'Insert into draft',edit:'Edit',remove:'Delete',close:'Close',name:'Name',body:'Template text',visibility:'Access',personal:'Personal',team:'Team',shared:'Shared',save:'Save',cancel:'Cancel',loading:'Loading templates…',empty:'No templates are available yet.',hint:'Selecting a template only inserts text into the editable draft. You decide when to send.',saved:'Template saved.',deleted:'Template deleted.',error:'Could not load or save templates.',suggestions:'Similar templates',unavailable:'Template management is unavailable.'}
  };
  const copy=key=>(words[window.tenantI18n?.language]||words.az)[key]||key;
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
    '.operator-reply-template-suggestions small{overflow:hidden;color:var(--muted,#64776f);text-overflow:ellipsis;white-space:nowrap}',
    '.operator-reply-template-modal{position:fixed;z-index:1500;inset:0;display:grid;place-items:center;padding:12px;background:#071d1688}',
    '.operator-reply-template-modal[hidden]{display:none!important}',
    '.operator-reply-template-card{box-sizing:border-box;width:min(560px,100%);max-height:min(84dvh,760px);overflow:auto;overscroll-behavior:contain;padding:16px;border:1px solid var(--line,#d9e3de);border-radius:16px;background:var(--card,#fff);color:var(--ink,#18332c);box-shadow:0 16px 48px #0004}',
    '.operator-reply-template-head,.operator-reply-template-actions{display:flex;align-items:center;justify-content:space-between;gap:8px}',
    '.operator-reply-template-head h2{margin:0;font-size:20px}',
    '.operator-reply-template-card button,.operator-reply-template-card input,.operator-reply-template-card textarea,.operator-reply-template-card select{box-sizing:border-box;min-height:44px;font:inherit}',
    '.operator-reply-template-card button{padding:9px 12px;border:1px solid var(--line,#ccdcd3);border-radius:9px;background:var(--card,#fff);color:inherit}',
    '.operator-reply-template-card button.primary{background:var(--brand-deep,#14523c);border-color:var(--brand-deep,#14523c);color:#fff}',
    '.operator-reply-template-card button:disabled{opacity:.55}',
    '.operator-reply-template-card label{display:grid;gap:6px;margin:12px 0;font-weight:600}',
    '.operator-reply-template-card input,.operator-reply-template-card textarea,.operator-reply-template-card select{width:100%;min-width:0;padding:9px 10px;border:1px solid var(--line,#ccdcd3);border-radius:9px;background:var(--paper,#f8faf9);color:inherit;font-weight:400}',
    '.operator-reply-template-card textarea{min-height:140px;resize:vertical}',
    '.operator-reply-template-card [role=status]{min-height:1.3em;color:var(--muted,#64776f);overflow-wrap:anywhere}',
    '.operator-reply-template-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;padding:10px 0;border-bottom:1px solid var(--line,#d9e3de)}',
    '.operator-reply-template-row strong,.operator-reply-template-row small{display:block;overflow-wrap:anywhere}',
    '.operator-reply-template-row small{margin-top:3px;color:var(--muted,#64776f);white-space:pre-wrap}',
    '.operator-reply-template-row-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:6px}',
    '.operator-reply-template-hint{color:var(--muted,#64776f);font-size:13px}',
    '@media(max-width:600px){.operator-reply-template-modal{align-items:end;padding:8px}.operator-reply-template-card{max-height:calc(100dvh - 16px);padding:14px;border-radius:16px}.operator-reply-template-row{grid-template-columns:minmax(0,1fr)}.operator-reply-template-row-actions{justify-content:flex-start}}'
  ].join('');
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
  function insert(item,state){
    if(!canInsert(state))return false;
    const current=state.input.value||'';
    const separator=current.trim()?(current.endsWith('\n')?'':'\n'):'';
    state.input.value=current+separator+item.body;
    state.input.dispatchEvent(new Event('input',{bubbles:true}));
    state.input.focus();
    try{state.input.setSelectionRange(state.input.value.length,state.input.value.length);}catch{}
    state.suggestions.hidden=true;
    state.suggestions.replaceChildren();
    return true;
  }
  function renderSuggestions(state){
    if(!canInsert(state)){state.suggestions.hidden=true;state.suggestions.replaceChildren();return;}
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
      const button=document.createElement('button');button.type='button';
      const name=document.createElement('span');name.textContent=item.name;
      const snippet=document.createElement('small');snippet.textContent=String(item.body||'').replace(/\s+/g,' ').slice(0,140);
      button.append(name,snippet);button.setAttribute('aria-label',copy('use')+': '+item.name);
      button.addEventListener('click',()=>insert(item,state));state.suggestions.append(button);
    }
    state.suggestions.hidden=false;
  }
  async function loadTemplates(state){
    const serial=++state.loadSerial,owner=identity();
    if(!owner||owner.key!==state.identity)return;
    state.button.hidden=true;
    state.loaded=false;state.loading=true;state.error='';
    try{
      const result=await request('GET',null,owner);
      if(state!==mounted||serial!==state.loadSerial||identity()?.key!==state.identity)return;
      state.templates=Array.isArray(result.templates)?result.templates:[];
      state.allowed=Array.isArray(result.allowed_visibility)?result.allowed_visibility:[];
      state.available=result.available===true;state.button.hidden=!state.available;state.loaded=true;state.error='';
    }catch(error){
      if(state!==mounted||serial!==state.loadSerial||identity()?.key!==state.identity)return;
      if(error?.status===404){teardown();return;}
      state.templates=[];state.allowed=[];state.available=false;state.button.hidden=true;state.loaded=true;state.error=error?.message||copy('error');
    }finally{
      if(state===mounted&&serial===state.loadSerial){
        state.loading=false;renderSuggestions(state);state.renderManager?.();
      }
    }
  }
  function visibilityLabel(value){return copy(value==='personal'?'personal':value==='team'?'team':'shared');}
  function openManager(state){
    if(state!==mounted)return;
    modalClose?.();
    const restore=document.activeElement,modal=document.createElement('div'),card=document.createElement('section');
    modal.className='operator-reply-template-modal';modal.id='operatorReplyTemplateModal';
    card.className='operator-reply-template-card';card.setAttribute('role','dialog');card.setAttribute('aria-modal','true');card.setAttribute('aria-labelledby','operatorReplyTemplateTitle');
    const header=document.createElement('div');header.className='operator-reply-template-head';
    const title=document.createElement('h2');title.id='operatorReplyTemplateTitle';title.textContent=copy('title');
    const close=document.createElement('button');close.type='button';close.textContent=copy('close');
    header.append(title,close);
    const hint=document.createElement('p');hint.className='operator-reply-template-hint';hint.textContent=copy('hint');
    const notice=document.createElement('p');notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
    const list=document.createElement('div'),editor=document.createElement('div'),add=document.createElement('button');
    add.type='button';add.className='primary';add.textContent=copy('new');
    card.append(header,hint,notice,add,list,editor);modal.append(card);document.body.append(modal);
    let closed=false;
    const finish=()=>{
      if(closed)return;closed=true;modal.remove();document.removeEventListener('keydown',key,true);
      if(modalClose===finish)modalClose=null;
      if(restore?.isConnected)restore.focus({preventScroll:true});
    };
    const key=event=>{if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();finish();}};
    modalClose=finish;document.addEventListener('keydown',key,true);close.addEventListener('click',finish);
    modal.addEventListener('pointerdown',event=>{if(event.target===modal)finish();});
    function showEditor(item=null){
      editor.replaceChildren();notice.textContent='';
      if(!state.allowed.length){notice.textContent=state.error||copy('unavailable');return;}
      const form=document.createElement('form');form.noValidate=false;
      const nameLabel=document.createElement('label'),nameText=document.createElement('span'),name=document.createElement('input');
      nameText.textContent=copy('name');name.name='name';name.maxLength=120;name.required=true;name.value=item?.name||'';
      nameLabel.append(nameText,name);
      const bodyLabel=document.createElement('label'),bodyText=document.createElement('span'),body=document.createElement('textarea');
      bodyText.textContent=copy('body');body.name='body';body.maxLength=4000;body.required=true;body.value=item?.body||'';
      bodyLabel.append(bodyText,body);
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
      actions.append(save,cancel);form.append(nameLabel,bodyLabel,visibilityLabelNode,notice,actions);editor.append(form);
      cancel.addEventListener('click',()=>{editor.replaceChildren();notice.textContent='';});
      name.focus();
      form.addEventListener('submit',async event=>{
        event.preventDefault();if(!form.reportValidity())return;
        save.disabled=true;cancel.disabled=true;notice.textContent=copy('loading');
        const payload=item
          ?{action:'update',template_id:item.id,version:item.version,name:name.value,body:body.value,visibility:visibility.value}
          :{action:'create',request_id:makeId(),name:name.value,body:body.value,visibility:visibility.value};
        try{
          await request('POST',payload,identity());
          if(state!==mounted||identity()?.key!==state.identity){finish();return;}
          notice.textContent=copy('saved');editor.replaceChildren();await loadTemplates(state);
        }catch(error){notice.textContent=error?.message||copy('error');save.disabled=false;cancel.disabled=false;}
      });
    }
    function renderList(){
      list.replaceChildren();
      if(state.loading){list.textContent=copy('loading');add.disabled=true;return;}
      add.disabled=!state.allowed.length;
      if(state.error){list.textContent=state.error;return;}
      if(!state.templates.length){list.textContent=copy('empty');return;}
      for(const item of state.templates){
        const row=document.createElement('article');row.className='operator-reply-template-row';
        const details=document.createElement('div'),name=document.createElement('strong'),description=document.createElement('small');
        name.textContent=item.name+' · '+visibilityLabel(item.visibility);description.textContent=item.body;
        details.append(name,description);
        const controls=document.createElement('div');controls.className='operator-reply-template-row-actions';
        const use=document.createElement('button');use.type='button';use.textContent=copy('use');
        use.dataset.replyTemplateInsert='';use.disabled=!canInsert(state);use.addEventListener('click',()=>{if(insert(item,state))finish();});
        controls.append(use);
        if(item.can_edit){
          const edit=document.createElement('button');edit.type='button';edit.textContent=copy('edit');edit.addEventListener('click',()=>showEditor(item));controls.append(edit);
          const remove=document.createElement('button');remove.type='button';remove.textContent=copy('remove');remove.addEventListener('click',async()=>{
            if(!window.confirm(copy('remove')+'?'))return;
            remove.disabled=true;notice.textContent=copy('loading');
            try{await request('POST',{action:'delete',template_id:item.id,version:item.version},identity());notice.textContent=copy('deleted');await loadTemplates(state);}
            catch(error){notice.textContent=error?.message||copy('error');remove.disabled=false;}
          });controls.append(remove);
        }
        row.append(details,controls);list.append(row);
      }
    }
    state.renderManager=renderList;
    add.addEventListener('click',()=>showEditor());
    renderList();close.focus();
  }
  function clearContext(state){
    state.templates=[];state.allowed=[];state.loaded=false;state.error='';
    state.loadSerial++;
    state.suggestions.hidden=true;state.suggestions.replaceChildren();
    modalClose?.();renderSuggestions(state);
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
    const root=composerRoot(),actions=root?.querySelector('.operator-composer-actions'),input=root?.querySelector('#chatText[data-operator-composer]'),owner=identity();
    if(!actions||!input||!owner||window.tenantProfileSnapshot?.capabilities?.dialog_reply_templates!==true){teardown();return;}
    if(mounted?.actions===actions&&mounted.identity===owner.key){if(!mounted.loaded&&!mounted.loading)void loadTemplates(mounted);return;}
    teardown();
    root.classList.add('operator-reply-template-root');
    const button=document.createElement('button');button.type='button';button.hidden=true;button.id='operatorReplyTemplatesOpen';button.className='outline';button.textContent=copy('button');button.title=copy('manage');button.setAttribute('aria-label',copy('manage'));
    const suggestions=document.createElement('div');suggestions.className='operator-reply-template-suggestions';suggestions.hidden=true;suggestions.setAttribute('role','listbox');suggestions.setAttribute('aria-label',copy('suggestions'));
    const state={actions,input,root,button,suggestions,identity:owner.key,templates:[],allowed:[],loaded:false,loading:false,loadSerial:0,error:'',available:false,renderManager:null,onInput:null};
    state.onInput=()=>renderSuggestions(state);
    state.inputObserver=new MutationObserver(()=>{
      if(state!==mounted)return;
      renderSuggestions(state);
      document.querySelectorAll('#operatorReplyTemplateModal [data-reply-template-insert]').forEach(node=>{node.disabled=!canInsert(state);});
    });
    state.inputObserver.observe(input,{attributes:true,attributeFilter:['disabled','readonly']});
    state.input.addEventListener('input',state.onInput);
    button.addEventListener('click',()=>openManager(state));
    actions.insertBefore(button,actions.firstChild);root.append(suggestions);mounted=state;
    renderSuggestions(state);void loadTemplates(state);
  }
  document.addEventListener('tenant-chat-attachments-ready',()=>{void attach();});
  document.addEventListener('tenant-profile',event=>{
    if(event.detail?.capabilities?.dialog_reply_templates!==true){teardown();return;}
    if(!mounted){void attach();return;}
    const next=event.detail?.member;
    const key=next?.tenant_id!=null&&next?.telegram_id!=null?String(next.tenant_id)+':'+String(next.telegram_id):'';
    if(key!==mounted.identity){teardown();void attach();return;}
    clearContext(mounted);void loadTemplates(mounted);
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
