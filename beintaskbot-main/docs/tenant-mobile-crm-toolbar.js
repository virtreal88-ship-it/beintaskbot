/* Compact mobile presentation only: move existing controls, never recreate handlers/state. */
(() => {
  const media=matchMedia('(max-width:700px)'), panels=[];
  const icons={create:'<path d="M12 5v14M5 12h14"/>',refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1"/>',sync:'<path d="m17 3 4 4-4 4M21 7H7M7 21l-4-4 4-4M3 17h14"/>',search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>'};
  const svg=key=>`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icons[key]}</svg>`;
  const style=document.createElement('style');style.textContent=`
.crm-mobile-search-toggle{display:none}.crm-mobile-search-box{display:contents}
@media(max-width:700px){
 #view-tasks .top,#view-deals .top{margin-bottom:10px;gap:8px}
 #view-tasks .top h1,#view-deals .top h1{font-size:23px;margin:0}
 #view-deals .top>div:first-child{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
 #view-deals .top .eyebrow{display:none}#view-deals #dealCount{font-size:12px;margin:0;padding:3px 6px}
 .crm-mobile-toolbar{display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:8px;border:1px solid var(--line);border-radius:12px;background:var(--card);margin-bottom:12px;max-width:100%;box-sizing:border-box}
 .crm-mobile-toolbar .crm-toolbar-actions,.crm-mobile-toolbar .task-flow-filters,.crm-mobile-toolbar .deal-source-filters{display:contents!important}
 .crm-mobile-toolbar .crm-toolbar-actions button{font-size:0!important;line-height:0;min-width:44px!important;width:44px!important;height:44px!important;padding:0!important;flex:none;position:relative}
 .crm-mobile-toolbar .crm-toolbar-actions button>*{display:none!important}
 .crm-mobile-toolbar .crm-toolbar-actions button::after{content:'';display:block;width:21px;height:21px;margin:auto;background:currentColor;mask:var(--crm-mobile-icon) center/contain no-repeat;-webkit-mask:var(--crm-mobile-icon) center/contain no-repeat}
 .crm-mobile-toolbar .crm-toolbar-actions button{order:1}
 .crm-mobile-toolbar label{margin:0!important;min-width:0!important;flex:none!important;gap:0!important}
 .crm-mobile-toolbar label>span{position:absolute!important;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
 .crm-mobile-toolbar select{height:44px!important;min-height:44px!important;font-size:14px;padding:6px!important;max-width:100%;box-sizing:border-box}
 .crm-mobile-toolbar label:has(#taskFeedSource),.crm-mobile-toolbar label:has(#dealFeedSource){width:112px;order:0}
 .crm-mobile-toolbar label:has(#taskStatus){width:112px;order:2}
 .crm-mobile-toolbar .task-view-controls,.crm-mobile-toolbar #dealViewControls{order:3;margin:0!important;flex:none}
 .crm-mobile-toolbar [data-task-view-controls],.crm-mobile-toolbar [data-deal-view-controls]{gap:4px}
 .crm-mobile-search-toggle{display:inline-flex;align-items:center;justify-content:center;order:4;width:44px;height:44px;min-width:44px;padding:0;border:1px solid var(--line);border-radius:9px;background:var(--card);color:var(--ink)}
 .crm-mobile-search-toggle svg{width:21px;height:21px}.crm-mobile-search-toggle[aria-expanded=true],.crm-mobile-search-toggle[data-active=true]{border-color:var(--brand);color:var(--brand)}
 .crm-mobile-search-box{display:none;order:5;width:100%;flex-basis:100%}.crm-mobile-search-box[data-open=true]{display:block}
 .crm-mobile-search-box label{display:block;width:100%}.crm-mobile-search-box input{width:100%!important;min-width:0!important;height:44px!important;margin:0;box-sizing:border-box}
 #view-tasks [data-task-flow-label=intro],#view-tasks [data-task-flow-label=windowHint],#taskSyncHint{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;overflow:hidden;clip-path:inset(50%)}
 #taskFeedPreferenceNotice:empty,#dealFeedPreferenceNotice:empty{display:none}
 .crm-mobile-toolbar [hidden]{display:none!important}
}`;document.head.append(style);
  for(const [viewId,filterSelector,searchId,refreshId] of [['view-tasks','.task-flow-filters','taskSearch','tasksRefresh'],['view-deals','.deal-source-filters','dealSearch','dealsRefresh']]){
    const view=document.getElementById(viewId),filters=view?.querySelector(filterSelector),actions=view?.querySelector('.crm-toolbar-actions'),input=document.getElementById(searchId);if(!view||!filters||!actions||!input)continue;
    const panel=document.createElement('div');panel.className='crm-mobile-toolbar';
    const actionMark=document.createComment('crm actions'),filterMark=document.createComment('crm filters');actions.before(actionMark);filters.before(filterMark);
    const searchNode=input.closest('label')||input,searchMark=document.createComment('crm search');searchNode.before(searchMark);
    const box=document.createElement('div');box.className='crm-mobile-search-box';box.id=searchId+'MobileBox';box.append(searchNode);
    const toggle=document.createElement('button');toggle.type='button';toggle.className='crm-mobile-search-toggle';toggle.setAttribute('aria-controls',box.id);toggle.innerHTML=svg('search');
    let expanded=false;
    function show(value,focus=false){expanded=value;box.dataset.open=String(value);toggle.setAttribute('aria-expanded',String(value));if(focus)(value?input:toggle).focus();}
    toggle.onclick=()=>show(!expanded,true);
    input.addEventListener('keydown',event=>{if(media.matches&&event.key==='Escape'){event.preventDefault();event.stopPropagation();show(false,true);}});
    function labels(){
      const label=input.closest('label')?.querySelector('span')?.textContent||input.getAttribute('aria-label')||input.placeholder||({ru:'Поиск',az:'Axtarış',en:'Search'}[window.tenantI18n?.language]||'Search');
      toggle.setAttribute('aria-label',label);toggle.title=label;toggle.dataset.active=String(!!input.value);if(input.value&&!expanded)show(true);
      [...actions.querySelectorAll('button')].forEach(button=>{const name=button.textContent.trim();if(name){button.setAttribute('aria-label',name);if(!button.title)button.title=name;}const key=button.id===refreshId?'refresh':button.id==='nativeTaskCreate'||button.id==='nativeDealCreate'?'create':'sync';button.style.setProperty('--crm-mobile-icon',`url("data:image/svg+xml,${encodeURIComponent(svg(key))}")`);});
    }
    input.addEventListener('input',()=>{toggle.dataset.active=String(!!input.value);});
    function layout(){
      if(media.matches){filterMark.after(panel);panel.append(actions,filters);filters.append(toggle,box);if(input.value)show(true);else show(expanded);}
      else{actionMark.after(actions);filterMark.after(filters);searchMark.after(searchNode);panel.remove();toggle.remove();box.remove();}
      labels();
      // Search node is preserved across breakpoint changes, including its value and handlers.
      if(media.matches&&!box.contains(searchNode))box.append(searchNode);
    }
    const observer=new MutationObserver(labels);observer.observe(actions,{childList:true,subtree:true,characterData:true});
    panels.push({layout,labels});layout();
  }
  media.addEventListener('change',()=>panels.forEach(p=>p.layout()));
  document.addEventListener('tenant-language',()=>panels.forEach(p=>p.labels()));
  document.addEventListener('tenant-view',()=>panels.forEach(p=>p.labels()));
})();
