/* Layout and appearance only: navigation invokes existing page buttons. */
import themeCss from './app-theme.css?inline'

if (document.body.dataset.appUi === 'kwid') {
  const style=document.createElement('style')
  style.id='app-design-system'
  style.textContent=themeCss
  document.head.append(style)
  const body=document.body, sidebar=document.querySelector('.shell>aside'), nav=sidebar.querySelector('.nav')
  const icon=path=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`
  const icons={menu:'M4 6h16M4 12h16M4 18h16',chevron:'m14 7-5 5 5 5',settings:'M4 7h16M4 17h16M8 4v6M16 14v6',profile:'M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10',exit:'M9 3H4v18h5M10 12h11m-4-4 4 4-4 4',sun:'M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4m10 10 1.4 1.4M5.6 18.4 7 17m10-10 1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',moon:'M20 15a9 9 0 0 1-11-11 9 9 0 1 0 11 11',system:'M3 4h18v13H3zM8 21h8M12 17v4'}
  function preference(key,fallback) {try{return localStorage.getItem(key)||fallback}catch{return fallback}}
  function remember(key,value) {try{localStorage.setItem(key,value)}catch{}}
  const systemTheme=matchMedia('(prefers-color-scheme: dark)'), mobile=matchMedia('(max-width:1023px)')
  let theme=preference('tenant-app-theme','system')
  if(!['light','dark','system'].includes(theme))theme='system'
  function applyTheme() {
    document.documentElement.dataset.appTheme=theme==='system'?(systemTheme.matches?'dark':'light'):theme
    document.querySelectorAll('[data-app-theme-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.appThemeChoice===theme)))
  }
  applyTheme()
  systemTheme.addEventListener('change',applyTheme)
  body.dataset.sidebar=preference('tenant-app-sidebar','expanded')==='collapsed'?'collapsed':'expanded'
  const originalHeader=sidebar.firstElementChild, brand=originalHeader.querySelector('.brand')
  const header=document.createElement('div');header.className='app-sidebar-header'
  const collapse=document.createElement('button');collapse.type='button';collapse.className='app-icon-button';collapse.innerHTML=icon(icons.chevron)
  header.append(brand,collapse);originalHeader.before(header)
  const topbar=document.createElement('header');topbar.className='app-topbar'
  topbar.innerHTML=`<button type="button" class="app-icon-button app-mobile-menu" aria-label="Menyunu aç" aria-expanded="false">${icon(icons.menu)}</button><div class="app-topbar-context"></div><div class="app-topbar-actions"><button type="button" class="app-icon-button" data-app-open="settings" aria-label="Tənzimləmələr" title="Tənzimləmələr">${icon(icons.settings)}</button><button type="button" class="app-avatar-button" aria-label="İstifadəçi menyusu" aria-expanded="false" aria-controls="appUserMenu">CS</button></div><div class="app-user-menu" id="appUserMenu" hidden><div class="app-user-name"><span id="appUserName">CRM Smart Assistant</span><small id="appUserCompany"></small></div><button type="button" data-app-open="profile">${icon(icons.profile)} Profil</button><button type="button" data-app-open="settings">${icon(icons.settings)} Tənzimləmələr</button><button type="button" data-app-logout>${icon(icons.exit)} Çıxış et</button><div class="app-theme-options">${['light','dark','system'].map(value=>`<button type="button" data-app-theme-choice="${value}" aria-label="${{light:'İşıqlı görünüş',dark:'Tünd görünüş',system:'Sistem görünüşü'}[value]}" title="${{light:'İşıqlı',dark:'Tünd',system:'Sistem'}[value]}" aria-pressed="false">${icon(icons[value==='light'?'sun':value==='dark'?'moon':'system'])}</button>`).join('')}</div></div>`
  document.querySelector('.shell').append(topbar)
  const backdrop=document.createElement('button');backdrop.type='button';backdrop.className='app-sidebar-backdrop';backdrop.setAttribute('aria-label','Menyunu bağla');backdrop.tabIndex=-1;body.append(backdrop)
  const avatar=topbar.querySelector('.app-avatar-button'), userMenu=topbar.querySelector('.app-user-menu'), mobileButton=topbar.querySelector('.app-mobile-menu')
  function closeUserMenu() {userMenu.hidden=true;avatar.setAttribute('aria-expanded','false')}
  function mobileMenu(open) {
    const wasOpen=body.dataset.mobileMenu==='open'
    body.dataset.mobileMenu=open?'open':'closed'
    mobileButton.setAttribute('aria-expanded',String(open))
    sidebar.inert=mobile.matches&&!open
    if(open)collapse.focus()
    else if(wasOpen&&mobile.matches)mobileButton.focus()
  }
  function syncSidebar() {
    collapse.setAttribute('aria-label',mobile.matches?'Menyunu bağla':body.dataset.sidebar==='collapsed'?'Yan paneli aç':'Yan paneli yığ')
    collapse.setAttribute('aria-expanded',String(body.dataset.sidebar!=='collapsed'))
    collapse.style.transform=body.dataset.sidebar==='collapsed'?'rotate(180deg)':''
    if(mobile.matches){body.dataset.sidebar='expanded';mobileMenu(false)}
    else {body.dataset.sidebar=preference('tenant-app-sidebar','expanded');mobileMenu(false)}
  }
  collapse.addEventListener('click',()=>{if(mobile.matches)mobileMenu(false);else{body.dataset.sidebar=body.dataset.sidebar==='collapsed'?'expanded':'collapsed';remember('tenant-app-sidebar',body.dataset.sidebar)}syncSidebar()})
  mobileButton.addEventListener('click',()=>mobileMenu(body.dataset.mobileMenu!=='open'))
  backdrop.addEventListener('click',()=>mobileMenu(false))
  mobile.addEventListener('change',syncSidebar);syncSidebar()
  avatar.addEventListener('click',()=>{userMenu.hidden=!userMenu.hidden;avatar.setAttribute('aria-expanded',String(!userMenu.hidden))})
  topbar.querySelectorAll('[data-app-open]').forEach(button=>button.addEventListener('click',()=>{const target=nav.querySelector(`[data-view="${button.dataset.appOpen}"]`);if(target&&!target.hidden&&target.style.display!=='none')target.click();closeUserMenu()}))
  topbar.querySelector('[data-app-logout]').addEventListener('click',()=>{closeUserMenu();document.getElementById('logout')?.click()})
  topbar.querySelectorAll('[data-app-theme-choice]').forEach(button=>button.addEventListener('click',()=>{theme=button.dataset.appThemeChoice;remember('tenant-app-theme',theme);applyTheme()}))
  document.addEventListener('click',event=>{if(!topbar.contains(event.target))closeUserMenu()})
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeUserMenu();mobileMenu(false)}})
  applyTheme()

  const groups=[{id:'crm',label:'CRM',pages:['tasks','customers','deals','hot_orders']},{id:'ai',label:'AI',pages:['ai_agents']},{id:'management',label:'İdarəetmə',pages:['approvals','linear','news','finance']},{id:'company',label:'Şirkət',pages:['integrations','settings','profile']}]
  const containers=new Map()
  for(const group of groups){
    const section=document.createElement('div');section.className='app-nav-group';section.dataset.group=group.id
    section.innerHTML=`<button type="button" class="app-nav-group-title" aria-expanded="true"><span>${group.label}</span>${icon('m6 9 6 6 6-6')}</button><div class="app-nav-group-items"></div>`
    section.querySelector('.app-nav-group-title').addEventListener('click',event=>{section.dataset.collapsed=section.dataset.collapsed==='true'?'false':'true';event.currentTarget.setAttribute('aria-expanded',String(section.dataset.collapsed!=='true'))})
    nav.append(section);containers.set(group.id,section)
  }
  function organizeNav() {
    for(const button of [...nav.querySelectorAll('[data-view]')]){
      if(!button.querySelector('.app-nav-label')){
        const label=document.createElement('span');label.className='app-nav-label'
        const text=[...button.childNodes].filter(node=>node.nodeType===Node.TEXT_NODE).map(node=>node.textContent).join('').trim()
        label.textContent=text||button.dataset.view
        for(const node of [...button.childNodes])if(node.nodeType===Node.TEXT_NODE)node.remove()
        button.append(label);button.title=label.textContent
      }
      const group=groups.find(group=>group.pages.includes(button.dataset.view))||groups[0], section=containers.get(group.id),items=section.querySelector('.app-nav-group-items')
      if(button.parentElement!==items)items.append(button)
    }
    for(const section of containers.values()){const hidden=![...section.querySelectorAll('[data-view]')].some(button=>!button.hidden&&button.style.display!=='none');if(section.hidden!==hidden)section.hidden=hidden}
    const settings=nav.querySelector('[data-view="settings"]')
    topbar.querySelectorAll('[data-app-open="settings"]').forEach(button=>button.hidden=!settings||settings.hidden||settings.style.display==='none')
  }
  const navObserver=new MutationObserver(organizeNav)
  navObserver.observe(nav,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','style']})
  organizeNav()
  nav.addEventListener('click',event=>{if(event.target.closest('[data-view]')){mobileMenu(false);closeUserMenu();requestAnimationFrame(updateContext)}})
  function updateContext() {
    const page=document.querySelector('main>.view.active'),title=page?.querySelector('h1')?.textContent||''
    topbar.querySelector('.app-topbar-context').textContent=title
    nav.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-current',button.classList.contains('active')?'page':'false'))
  }
  document.addEventListener('tenant-profile',event=>{
    const member=event.detail.member||{},name=member.display_name||'İstifadəçi'
    document.getElementById('appUserName').textContent=name
    document.getElementById('appUserCompany').textContent=member.tenant_name||''
    avatar.textContent=name.split(/\s+/).slice(0,2).map(part=>part[0]||'').join('').toUpperCase()
    organizeNav();updateContext()
  })
  // Initial profile may have arrived before the bundle: use rendered text.
  const initialName=document.getElementById('profileName')?.textContent
  if(initialName&&!/Yüklənir/.test(initialName)){
    document.getElementById('appUserName').textContent=initialName
    avatar.textContent=initialName.split(/\s+/).slice(0,2).map(part=>part[0]||'').join('').toUpperCase()
  }
  document.getElementById('appUserCompany').textContent=document.getElementById('company')?.textContent||''
  const main=document.querySelector('main')
  new MutationObserver(updateContext).observe(main,{attributes:true,subtree:true,attributeFilter:['class']})
  updateContext()

  function installTabPicker(tablist) {
    if(tablist.dataset.appTabReady)return
    const picker=document.createElement('select');picker.className='app-tab-picker';picker.setAttribute('aria-label','Bölmə seçin')
    tablist.before(picker);tablist.dataset.appTabReady='true'
    const update=()=>{
      const buttons=[...tablist.querySelectorAll('button')].filter(button=>!button.hidden&&button.style.display!=='none')
      picker.replaceChildren(...buttons.map((button,index)=>{const option=document.createElement('option');option.value=String(index);option.textContent=button.textContent;option.selected=button.getAttribute('aria-selected')==='true';return option}))
      picker.hidden=tablist.hidden||buttons.length<2
    }
    picker.addEventListener('change',()=>{const buttons=[...tablist.querySelectorAll('button')].filter(button=>!button.hidden&&button.style.display!=='none');buttons[Number(picker.value)]?.click()})
    new MutationObserver(update).observe(tablist,{subtree:true,attributes:true,attributeFilter:['hidden','aria-selected'],childList:true})
    update()
  }
  function decorate() {
    document.querySelectorAll('.settings-sections,.wf-tabs').forEach(installTabPicker)
    const approvals=document.getElementById('systemApprovalTab')?.parentElement
    if(approvals&&!approvals.dataset.appTabReady){
      approvals.classList.add('app-page-tabs');approvals.dataset.appTabReady='true';approvals.setAttribute('role','tablist')
      const tabs=[['systemApprovalTab','systemApprovalsHost'],['hotApprovalTab','hotApprovalsHost']]
      const update=()=>tabs.forEach(([id,hostId])=>{
        const button=document.getElementById(id),host=document.getElementById(hostId)
        if(!button||!host)return
        button.setAttribute('role','tab');button.setAttribute('aria-controls',hostId)
        button.setAttribute('aria-selected',String(!host.hidden))
      })
      new MutationObserver(update).observe(approvals.parentElement,{subtree:true,attributes:true,attributeFilter:['hidden']})
      update()
    }
    const aiTiles=document.querySelector('#aiAgentsOverview .ai-overview-tiles')
    if(aiTiles)aiTiles.classList.add('app-page-tabs')
  }
  new MutationObserver(decorate).observe(main,{childList:true,subtree:true})
  document.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return
    const tab=event.target.closest('[role="tab"]'),tablist=tab?.closest('[role="tablist"]')
    if(!tablist)return
    const tabs=[...tablist.querySelectorAll('[role="tab"]')].filter(button=>!button.hidden&&!button.disabled)
    const index=tabs.indexOf(tab);if(index<0||!tabs.length)return
    const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length
    event.preventDefault();tabs[next].click();tabs[next].focus()
  })
  decorate()
}
