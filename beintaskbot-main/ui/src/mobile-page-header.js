// Presentation only: move existing controls, retaining their listeners and access state.
export function installMobilePageHeader({topbar, mobile, avatar, userMenu, languageSelect, instructionLink, pageLabel}) {
  const shell=document.querySelector('.shell'),main=shell?.querySelector('main');
  if(!main)return;
  const actions=topbar.querySelector('.app-topbar-actions');
  const languageHome=document.createComment('language home'),guideHome=document.createComment('guide home');
  languageSelect.before(languageHome);instructionLink.before(guideHome);
  const extras=document.createElement('div');extras.className='app-mobile-account-extras';
  const languageRow=document.createElement('label');languageRow.className='app-account-language';
  const languageLabel=document.createElement('span');languageRow.append(languageLabel);
  userMenu.querySelector('[data-app-logout]').before(extras);
  let row=null,heading=null,frame=0,avatarFocusOwned=false;
  avatar.addEventListener('focus',()=>{avatarFocusOwned=true});
  document.addEventListener('focusin',event=>{if(event.target!==avatar)avatarFocusOwned=false});
  document.addEventListener('pointerdown',event=>{if(!avatar.contains(event.target))avatarFocusOwned=false},true);
  const languageText=()=>({ru:'Язык',az:'Dil',en:'Language'})[window.tenantI18n?.language||document.documentElement.lang]||'Language';
  function put(parent,node,before=null){if(node.parentNode!==parent||(before&&node.nextSibling!==before))parent.insertBefore(node,before);}
  function clearRow(){heading?.classList.remove('app-mobile-original-title');row?.remove();row=null;heading=null;}
  function update(){
    frame=0;
    if(!window.document?.documentElement||!main.isConnected)return;
    const bell=document.getElementById('dialogConsultationBell');
    if(bell)put(actions,bell,languageHome);
    const language=languageText();if(languageLabel.textContent!==language)languageLabel.textContent=language;
    if(!mobile.matches){
      put(shell,topbar);clearRow();
      if(languageHome.parentNode&&languageHome.nextSibling!==languageSelect)languageHome.after(languageSelect);
      if(guideHome.parentNode&&guideHome.nextSibling!==instructionLink)guideHome.after(instructionLink);
      document.body.removeAttribute('data-mobile-page-header');return;
    }
    // Do not reparent React-managed headings. Keep their state/ownership intact and
    // expose one visible page title in the mobile row instead.
    const page=main.querySelector(':scope>.view.active');
    const title=[...(page?.querySelectorAll('h1')||[])].find(el=>!el.closest('.app-mobile-title-row,[role="dialog"],.modal,dialog'));
    if(row?.parentElement!==page||heading!==title){
      put(shell,topbar);clearRow();heading=title;
      if(page){row=document.createElement('div');row.className='app-mobile-title-row';const text=document.createElement('h1');row.append(text,topbar);page.prepend(row);heading?.classList.add('app-mobile-original-title');}
    }
    const label=title?.textContent||pageLabel?.(page?.id.replace(/^view-/,''))||'';
    if(row&&row.firstElementChild.textContent!==label)row.firstElementChild.textContent=label;
    document.body.dataset.mobilePageHeader=row?'inline':'fallback';
    if(languageRow.parentNode!==extras)extras.append(languageRow);
    put(languageRow,languageSelect);put(extras,instructionLink);
    // Moving the existing avatar to the new route must not lose a returned keyboard focus.
    if(avatarFocusOwned&&document.activeElement!==avatar&&document.activeElement===document.body)avatar.focus({preventScroll:true});
  }
  function schedule(){if(window.document?.documentElement&&main.isConnected&&!frame)frame=requestAnimationFrame(update);}
  const observer=new MutationObserver(schedule);
  observer.observe(main,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['class']});
  observer.observe(topbar,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['hidden','aria-label','style','class']});
  mobile.addEventListener('change',()=>{userMenu.hidden=true;avatar.setAttribute('aria-expanded','false');schedule();});
  document.addEventListener('tenant-language',schedule);document.addEventListener('tenant-view',schedule);document.addEventListener('tenant-profile',schedule);
  userMenu.addEventListener('keydown',event=>{if(event.key==='Escape'){userMenu.hidden=true;avatar.setAttribute('aria-expanded','false');avatar.focus({preventScroll:true});}});
  update();
}
