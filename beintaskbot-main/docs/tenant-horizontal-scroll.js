/* One compact, keyboard-accessible control for the visible wide data region.
   Native touch/trackpad scrolling stays on the region, never on the page. */
(() => {
  const selectors='.tl-board,.table:has(>.row),.support-cases-list,[data-scope="table"][data-part="scroll-area"],[data-horizontal-scroll]';
  const kanbanSelectors='[data-task-kanban],[data-horizontal-scroll]:has(>[data-kommo-stage-key]),[data-native-deal-board]>div:has(>[data-native-stage])';
  const mobile=()=>innerWidth<=700;
  const style=document.createElement('style');
  style.textContent=`
    .app-horizontal-scrollport{scrollbar-width:none}
    .app-horizontal-scrollport::-webkit-scrollbar{display:none}
    #appHorizontalScroll{position:fixed;right:20px;bottom:calc(18px + env(safe-area-inset-bottom));width:25vw;min-width:100px;max-width:360px;z-index:45;padding:4px 10px;border:1px solid var(--app-line,var(--line));border-radius:14px;background:var(--app-surface,var(--card,#fff));box-shadow:0 3px 14px #0002}
    #appHorizontalScroll[hidden]{display:none}
    #appHorizontalScroll input{display:block;width:100%;height:32px;margin:0;cursor:ew-resize;accent-color:var(--app-accent,var(--brand));touch-action:pan-y}
    @media(max-width:700px){
      html body[data-app-ui="kwid"] main{padding-left:env(safe-area-inset-left,0px)!important;padding-right:env(safe-area-inset-right,0px)!important}
      html body[data-app-ui="kwid"] main .panel{padding-left:8px;padding-right:8px}
      html body[data-app-ui="kwid"] :is(#view-tasks,#view-deals) .panel:has(.app-kanban-swipe){padding-left:0!important;padding-right:0!important;border-left:0!important;border-right:0!important;border-radius:0}
      html body[data-app-ui="kwid"] :is(#nativeTasksList,#tasksList,#nativeDealsList,#dealsList):has(.app-kanban-swipe){border-left:0!important;border-right:0!important;border-radius:0}
      html body[data-app-ui="kwid"] :is(#view-tasks,#view-deals)>.top{padding-left:8px;padding-right:8px}
      html body[data-app-ui="kwid"] .app-kanban-swipe>[data-kommo-stage-key]{padding-left:8px;padding-right:8px}
      .app-kanban-swipe{display:flex!important;min-width:0;max-width:100%;overflow-x:auto!important;scroll-snap-type:x mandatory!important;gap:8px!important;overscroll-behavior-x:contain}
      .app-kanban-swipe> :is([data-task-column],[data-kommo-stage-key],[data-native-stage]){box-sizing:border-box!important;flex:0 0 100%!important;width:100%!important;min-width:0!important;max-width:100%!important;scroll-snap-align:start;scroll-snap-stop:always;overflow-wrap:anywhere}
    }
    #appHorizontalScroll:focus-within{outline:2px solid var(--app-accent,var(--brand));outline-offset:2px}
  `;
  document.head.append(style);
  const control=document.createElement('div');control.id='appHorizontalScroll';control.hidden=true;
  const slider=document.createElement('input');slider.type='range';slider.min='0';slider.max='1000';slider.step='1';slider.value='0';slider.setAttribute('aria-label','Cədvəli üfüqi sürüşdür');
  control.append(slider);document.body.append(control);
  let active=null,frame=0;
  const visible=el=>{const r=el.getBoundingClientRect();return el.clientWidth>0&&r.width>0&&r.bottom>80&&r.top<innerHeight-60&&r.right>0&&r.left<innerWidth;};
  const wide=el=>visible(el)&&el.scrollWidth>el.clientWidth+1;
  function update(){
    frame=0;
    document.querySelectorAll(kanbanSelectors).forEach(el=>{
      if(!el.classList.contains('app-kanban-swipe'))el.classList.add('app-kanban-swipe');
      if(!el.hasAttribute('tabindex'))el.tabIndex=0;
      if(!el.hasAttribute('aria-label'))el.setAttribute('aria-label','Kanban');
    });
    const regions=[...document.querySelectorAll(selectors+',.app-kanban-swipe')];
    const eligible=el=>wide(el)&&!(mobile()&&el.classList.contains('app-kanban-swipe'));
    regions.forEach(el=>{if(!el.classList.contains('app-horizontal-scrollport'))el.classList.add('app-horizontal-scrollport');});
    if(!active?.isConnected||!eligible(active))active=regions.find(eligible)||null;
    const modalOpen=[...document.querySelectorAll('.modal.show,[role="dialog"][aria-modal="true"]')].some(visible);
    control.hidden=!active||modalOpen||document.body.dataset.mobileMenu==='open';
    if(control.hidden)return;
    const max=active.scrollWidth-active.clientWidth;
    slider.value=String(Math.round(Math.max(0,active.scrollLeft)/max*1000));
    slider.setAttribute('aria-valuetext',`${Math.round(Number(slider.value)/10)}%`);
    slider.title='↔ '+(active.closest('.view')?.querySelector('h1')?.textContent||'Cədvəl');
  }
  const schedule=()=>{if(!frame)frame=requestAnimationFrame(update);};
  function select(event){const region=event.target.closest?.(selectors);if(region&&wide(region))active=region;schedule();}
  document.addEventListener('pointerover',select,{passive:true});
  document.addEventListener('focusin',select);
  document.addEventListener('scroll',event=>{if(event.target.matches?.(selectors)&&wide(event.target))active=event.target;schedule();},{capture:true,passive:true});
  document.addEventListener('keydown',event=>{
    const board=event.target;
    if(!mobile()||!board.matches?.('.app-kanban-swipe')||!['ArrowLeft','ArrowRight'].includes(event.key))return;
    event.preventDefault();
    board.scrollBy({left:(event.key==='ArrowRight'?1:-1)*(board.clientWidth+8),behavior:'auto'});
  });
  window.addEventListener('resize',schedule,{passive:true});
  slider.addEventListener('input',()=>{if(active?.isConnected){active.scrollLeft=Number(slider.value)/1000*(active.scrollWidth-active.clientWidth);slider.setAttribute('aria-valuetext',`${Math.round(Number(slider.value)/10)}%`);}});
  // Observe body-level legacy modals and portalled dialogs as well as main content.
  // Ignore our own control mutations so range/visibility updates cannot reschedule themselves.
  const resize=new ResizeObserver(schedule);resize.observe(document.querySelector('main')||document.body);
  new MutationObserver(records=>{if(records.some(record=>!control.contains(record.target)))schedule();}).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['class','hidden','style','role','aria-modal']});
  new MutationObserver(schedule).observe(document.body,{attributes:true,attributeFilter:['data-sidebar','data-mobile-menu']});
  schedule();
})();
