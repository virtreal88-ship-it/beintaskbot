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
  const badge=document.createElement('span');badge.className='app-avatar-unread';badge.setAttribute('aria-hidden','true');badge.hidden=true;
  // Badge is a sibling: existing profile code updates avatar.textContent.
  const badgeDescription=document.createElement('span');badgeDescription.id='appAvatarNotifications';badgeDescription.className='app-visually-hidden';
  avatar.after(badge,badgeDescription);avatar.setAttribute('aria-describedby',badgeDescription.id);
  let row=null,heading=null,bell=null,bellHome=null,frame=0,avatarFocusOwned=false;
  avatar.addEventListener('focus',()=>{avatarFocusOwned=true});
  document.addEventListener('focusin',event=>{if(event.target!==avatar)avatarFocusOwned=false});
  document.addEventListener('pointerdown',event=>{if(!avatar.contains(event.target))avatarFocusOwned=false},true);
  const copy=()=>({ru:{language:'Язык',notifications:'Уведомления'},az:{language:'Dil',notifications:'Bildirişlər'},en:{language:'Language',notifications:'Notifications'}})[window.tenantI18n?.language||document.documentElement.lang]||{language:'Language',notifications:'Notifications'};
  function put(parent,node,before=null){if(node.parentNode!==parent||(before&&node.nextSibling!==before))parent.insertBefore(node,before);}
  function clearRow(){heading?.classList.remove('app-mobile-original-title');row?.remove();row=null;heading=null;}
  function update(){
    frame=0;
    if(!window.document?.documentElement||!main.isConnected)return;
    const found=document.getElementById('dialogConsultationBell');
    if(found&&found!==bell){bell=found;bellHome=document.createComment('notification home');actions.insertBefore(bellHome,languageHome);}
    const labels=copy();if(languageLabel.textContent!==labels.language)languageLabel.textContent=labels.language;
    if(bell&&bell.dataset.mobileLabel!==labels.notifications)bell.dataset.mobileLabel=labels.notifications;
    const sourceBadge=bell?.querySelector('.notification-unread-badge');
    const rawCount=sourceBadge?.textContent?.trim()||'';
    const count=sourceBadge&&!sourceBadge.hidden&&getComputedStyle(sourceBadge).display!=='none'&&/^[1-9]\d*\+?$/.test(rawCount)?rawCount:'';
    if(badge.textContent!==count)badge.textContent=count;
    const hideBadge=!mobile.matches||!count||!!bell?.hidden;if(badge.hidden!==hideBadge)badge.hidden=hideBadge;
    const description=count?`${labels.notifications}: ${count}`:'';
    if(badgeDescription.textContent!==description)badgeDescription.textContent=description;
    if(!mobile.matches){
      put(shell,topbar);clearRow();
      if(languageHome.parentNode&&languageHome.nextSibling!==languageSelect)languageHome.after(languageSelect);
      if(guideHome.parentNode&&guideHome.nextSibling!==instructionLink)guideHome.after(instructionLink);
      if(bellHome?.parentNode&&bellHome.nextSibling!==bell)bellHome.after(bell);
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
    if(bell)put(extras,bell,languageRow);
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
