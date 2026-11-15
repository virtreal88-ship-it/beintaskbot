/* Mobile layout only. Keep the original controls, handlers, permissions and React roots. */
(() => {
  const media = matchMedia('(max-width:700px)'), panels = [];
  const icons = {
    create: '<path d="M12 5v14M5 12h14"/>',
    filter: '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'
  };
  const svg = key => `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icons[key]}</svg>`;
  const style = document.createElement('style');
  style.textContent = `
@media(max-width:700px){
 .crm-mobile-toolbar{position:relative;display:flex;gap:8px;align-items:center;margin:0 0 12px;padding:0;max-width:100%;box-sizing:border-box}
 .crm-mobile-toolbar>.crm-mobile-trigger,.crm-mobile-toolbar>.crm-mobile-create{display:inline-flex;align-items:center;justify-content:center;flex:0 0 44px;width:44px!important;height:44px!important;min-height:44px;min-width:44px;padding:0!important;border-radius:10px;box-sizing:border-box;position:relative}
 .crm-mobile-trigger{border:1px solid var(--line);background:var(--card);color:var(--ink)}
 .crm-mobile-trigger[aria-expanded=true]{border-color:var(--brand);color:var(--brand)}
 .crm-mobile-trigger svg{display:block;width:20px;height:20px;margin:0;flex:none}
 .crm-mobile-create{font-size:0!important;line-height:0!important}
 .crm-mobile-create>*{display:none!important}
 .crm-mobile-create::after{content:'';width:20px;height:20px;background:currentColor;mask:var(--crm-create-icon) center/contain no-repeat;-webkit-mask:var(--crm-create-icon) center/contain no-repeat}
 .crm-mobile-popover{position:absolute;top:calc(100% + 8px);left:0;z-index:8;width:min(320px,100%);max-height:min(65dvh,460px);overflow:auto;overscroll-behavior:contain;padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);box-shadow:0 8px 28px #0003;box-sizing:border-box}
 .crm-mobile-popover .task-flow-filters,.crm-mobile-popover .deal-source-filters{display:flex!important;flex-direction:column;align-items:stretch;gap:12px;margin:0}
 .crm-mobile-popover label{display:grid;gap:5px;width:100%;min-width:0!important;flex:auto!important;margin:0!important;font-size:13px}
 .crm-mobile-popover input,.crm-mobile-popover select{width:100%!important;min-width:0!important;max-width:100%;height:44px;box-sizing:border-box;font-size:16px!important}
 .crm-mobile-more-panel{width:min(280px,100%)}
 .crm-mobile-more-panel .crm-toolbar-actions{display:flex;flex-direction:column;align-items:stretch;gap:6px;width:100%}
 .crm-mobile-more-panel button{min-height:44px;max-width:100%;white-space:normal}
 .crm-mobile-more-panel .crm-toolbar-actions button{display:flex;align-items:center;justify-content:flex-start;padding:10px 12px!important;font-size:14px!important;line-height:1.3!important}
 .crm-mobile-more-panel .task-view-controls,.crm-mobile-more-panel #dealViewControls{margin:8px 0 0!important}
 .crm-mobile-more-panel [data-task-view-controls],.crm-mobile-more-panel [data-deal-view-controls]{display:flex;flex-wrap:wrap;gap:6px}
 #view-deals:has(#kommoDealSourcePanel[hidden]) .crm-mobile-more-panel #dealViewControls{display:none!important}
 .crm-mobile-toolbar [hidden]{display:none!important}
 #view-tasks [data-task-flow-label=intro],#view-tasks [data-task-flow-label=windowHint],#taskSyncHint{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;overflow:hidden;clip-path:inset(50%)}
 #kommoTaskCount{display:none!important}
 #taskFeedPreferenceNotice:empty,#dealFeedPreferenceNotice:empty{display:none}
}`;
  document.head.append(style);
  for (const [viewId, filterSelector, createId] of [['view-tasks','.task-flow-filters','nativeTaskCreate'],['view-deals','.deal-source-filters','nativeDealCreate']]) {
    const view = document.getElementById(viewId), filters = view?.querySelector(filterSelector), actions = view?.querySelector('.crm-toolbar-actions'), create = document.getElementById(createId);
    if (!view || !filters || !actions || !create) continue;
    const remember = node => { const marker=document.createComment('crm responsive position');node.before(marker);return {node,marker}; };
    const originals = [remember(actions),remember(filters),remember(create)];
    const controls = [...filters.querySelectorAll('.task-view-controls,#dealViewControls')].map(remember);
    const panel = document.createElement('div');panel.className='crm-mobile-toolbar';
    const filterBox=document.createElement('div'), moreBox=document.createElement('div');
    filterBox.className='crm-mobile-popover crm-mobile-filter-panel';moreBox.className='crm-mobile-popover crm-mobile-more-panel';
    filterBox.id=viewId+'MobileFilters';moreBox.id=viewId+'MobileMore';
    // Nonmodal disclosure groups, not ARIA menus: native selects and React controls retain their semantics.
    for(const box of [filterBox,moreBox]){box.hidden=true;box.setAttribute('role','group');}
    const button=(key,box)=>{const node=document.createElement('button');node.type='button';node.className='crm-mobile-trigger crm-mobile-'+key;node.innerHTML=svg(key);node.setAttribute('aria-controls',box.id);node.setAttribute('aria-expanded','false');return node;};
    const filterButton=button('filter',filterBox),moreButton=button('more',moreBox);
    let open=null;
    function close(focus=false){const previous=open;open=null;filterBox.hidden=moreBox.hidden=true;filterButton.setAttribute('aria-expanded','false');moreButton.setAttribute('aria-expanded','false');if(focus&&previous)(previous===filterBox?filterButton:moreButton).focus();}
    function toggle(box){const was=open===box;close();if(!was){panels.forEach(p=>p.close());open=box;box.hidden=false;(box===filterBox?filterButton:moreButton).setAttribute('aria-expanded','true');}}
    filterButton.onclick=()=>toggle(filterBox);moreButton.onclick=()=>toggle(moreBox);
    panel.addEventListener('keydown',event=>{if(event.key==='Escape'&&open){event.preventDefault();event.stopPropagation();close(true);}});
    document.addEventListener('pointerdown',event=>{if(open&&!panel.contains(event.target))close();},true);
    panel.addEventListener('focusout',event=>{
      // During a pointer focus transition activeElement may briefly be body. Closing in
      // a microtask hides the target before its click. Prefer the announced target;
      // for null relatedTarget defer until the complete focus transition has finished.
      if(event.relatedTarget){if(open&&!panel.contains(event.relatedTarget))close();return;}
      setTimeout(()=>{if(open&&!panel.contains(document.activeElement))close();},0);
    });
    // Close after an action, without stealing focus from a dialog it may have opened.
    moreBox.addEventListener('click',event=>{if(event.target.closest('button')&&!event.target.closest('button').disabled)queueMicrotask(()=>{const stillInside=moreBox.contains(document.activeElement);close(stillInside);});});
    function labels(){
      const language=window.tenantI18n?.language||document.documentElement.lang||'az';
      const copy=({ru:['Фильтры и поиск','Другие действия'],en:['Filters and search','More actions'],az:['Filtrlər və axtarış','Digər əməliyyatlar']})[language]||['Filtrlər və axtarış','Digər əməliyyatlar'];
      [[filterButton,filterBox,copy[0]],[moreButton,moreBox,copy[1]]].forEach(([b,box,label])=>{b.setAttribute('aria-label',label);b.title=label;box.setAttribute('aria-label',label);});
      const name=create.textContent.trim();if(media.matches&&name){create.setAttribute('aria-label',name);create.title=name;}
    }
    function layout(){
      close();
      if(media.matches){
        originals[1].marker.after(panel);panel.append(create,filterButton,moreButton,filterBox,moreBox);
        filterBox.append(filters);moreBox.append(actions);controls.forEach(({node})=>moreBox.append(node));
        create.classList.add('crm-mobile-create');create.style.setProperty('--crm-create-icon',`url("data:image/svg+xml,${encodeURIComponent(svg('create'))}")`);
      } else {
        [...originals,...controls].forEach(({node,marker})=>marker.after(node));panel.remove();create.classList.remove('crm-mobile-create');create.style.removeProperty('--crm-create-icon');
      }
      labels();
    }
    new MutationObserver(labels).observe(create,{childList:true,subtree:true,characterData:true});
    panels.push({layout,labels,close});layout();
  }
  media.addEventListener('change',()=>panels.forEach(p=>p.layout()));
  document.addEventListener('tenant-language',()=>panels.forEach(p=>p.labels()));
  document.addEventListener('tenant-view',()=>panels.forEach(p=>{p.close();p.labels();}));
})();
