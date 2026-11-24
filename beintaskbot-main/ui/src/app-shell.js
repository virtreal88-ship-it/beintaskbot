/* Layout and appearance only: navigation invokes existing page buttons. */
import themeCss from './app-theme.css?inline'
import { installMobilePageHeader } from './mobile-page-header.js'

if (document.body.dataset.appUi === 'kwid') {
  const style=document.createElement('style')
  style.id='app-design-system'
  style.textContent=themeCss+'\n.app-nav-route-hidden{display:none!important}'
  document.head.append(style)
  const body=document.body, sidebar=document.querySelector('.shell>aside'), nav=sidebar.querySelector('.nav')
  const icon=path=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${path}"/></svg>`
  const icons={menu:'M4 6h16M4 12h16M4 18h16',chevron:'m14 7-5 5 5 5',settings:'M4 7h16M4 17h16M8 4v6M16 14v6',profile:'M20 21a8 8 0 0 0-16 0M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10',exit:'M9 3H4v18h5M10 12h11m-4-4 4 4-4 4',sun:'M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4m10 10 1.4 1.4M5.6 18.4 7 17m10-10 1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',moon:'M20 15a9 9 0 0 1-11-11 9 9 0 1 0 11 11',system:'M3 4h18v13H3zM8 21h8M12 17v4'}
  const navIconPaths={hot_orders:'M4 7.5 12 3l8 4.5v9L12 21l-8-4.5zM4.5 7.8 12 12l7.5-4.2M12 12v9M8 5.3l8 4.5',linear:'M4 5h6v6H4zM14 5h6v6h-6zM4 15h6v4H4zM14 15h6v4h-6z',news:'M5 4h15v16H5zM3 7v11a2 2 0 0 0 2 2M8 8h9M8 12h9M8 16h5',night_report:'M4 5h11a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-5l-4 3v-3H6a2 2 0 0 1-2-2zM18 8h1a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-1M19 3v2m-1-1h2'}
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
  const t=key=>window.tenantI18n?.t(key)||key
  const language=()=>window.tenantI18n?.language||document.documentElement.lang||'az'
  const crmLabels={ru:{tasks:'Задачи',customers:'Контакты',companies:'Компании',deals:'Сделки',hot_orders:'Заказы',instructions:'Инструкции'},az:{tasks:'Tapşırıqlar',customers:'Əlaqələr',companies:'Şirkətlər',deals:'Sövdələşmələr',hot_orders:'Sifarişlər',instructions:'Təlimatlar'},en:{tasks:'Tasks',customers:'Contacts',companies:'Companies',deals:'Deals',hot_orders:'Orders',instructions:'Instructions'}}
  const aiLabels={ru:{ai_administrator:'Обучение AI',ai_agents:'AI-агенты',ai_dashboard:'AI-панель',ai_analytics:'AI-аналитика',knowledge_center:'Центр знаний',support_cases:'Кейсы',ai_testing:'Тестирование AI'},az:{ai_administrator:'AI təlimi',ai_agents:'AI agentlər',ai_dashboard:'AI paneli',ai_analytics:'AI analitikası',knowledge_center:'Bilik mərkəzi',support_cases:'Keyslər',ai_testing:'AI sınağı'},en:{ai_administrator:'AI Learning',ai_agents:'AI agents',ai_dashboard:'AI Dashboard',ai_analytics:'AI Analytics',knowledge_center:'Knowledge Center',support_cases:'Cases',ai_testing:'AI testing'}}
  const employeeLabels={ru:'Сотрудники и доступ',az:'Əməkdaşlar və giriş',en:'Employees and access'}
  const reportLabel=()=>({ru:'Отчёты',az:'Hesabatlar',en:'Reports'}[language()]||'Reports');
  const pageLabel=page=>page==='night_report'?({ru:'Отчёты агентов',az:'Agent hesabatları',en:'Agent reports'}[language()]||'Agent reports'):page==='employees'?employeeLabels[language()]||employeeLabels.en:crmLabels[language()]?.[page]||aiLabels[language()]?.[page]||t(page)
  const originalHeader=sidebar.firstElementChild, brand=originalHeader.querySelector('.brand')
  const header=document.createElement('div');header.className='app-sidebar-header'
  const collapse=document.createElement('button');collapse.type='button';collapse.className='app-icon-button';collapse.innerHTML=icon(icons.chevron)
  header.append(brand,collapse);originalHeader.before(header)
  const topbar=document.createElement('header');topbar.className='app-topbar'
  topbar.innerHTML=`<button type="button" class="app-icon-button app-mobile-menu" aria-label="Menyunu aç" aria-expanded="false">${icon(icons.menu)}</button><div class="app-topbar-context"></div><div class="app-topbar-actions"><select class="app-language-select" aria-label="Interface language" data-app-language><option value="ru">RU</option><option value="az">AZ</option><option value="en">EN</option></select><a class="app-instructions-link" data-app-instructions href="/app/instructions" title="Instructions">${icon('M4 4h11l5 5v11H4zM15 4v6h5M8 14h8M8 17h8')}<span></span></a><button type="button" class="app-avatar-button" aria-label="İstifadəçi menyusu" aria-expanded="false" aria-controls="appUserMenu">CS</button></div><div class="app-user-menu" id="appUserMenu" hidden><div class="app-user-name"><span id="appUserName">CRM Smart Assistant</span><small id="appUserCompany"></small></div><button type="button" data-app-open="profile">${icon(icons.profile)} <span data-i18n="profile">${t('profile')}</span></button><button type="button" data-app-open="settings">${icon(icons.settings)} <span data-i18n="settings">${t('settings')}</span></button><button type="button" data-app-logout>${icon(icons.exit)} <span data-i18n="logout">${t('logout')}</span></button><div class="app-theme-options">${['light','dark','system'].map(value=>`<button type="button" data-app-theme-choice="${value}" aria-label="${{light:'İşıqlı görünüş',dark:'Tünd görünüş',system:'Sistem görünüşü'}[value]}" title="${{light:'İşıqlı',dark:'Tünd',system:'Sistem'}[value]}" aria-pressed="false">${icon(icons[value==='light'?'sun':value==='dark'?'moon':'system'])}</button>`).join('')}</div></div>`
  document.querySelector('.shell').append(topbar)
  const backdrop=document.createElement('button');backdrop.type='button';backdrop.className='app-sidebar-backdrop';backdrop.setAttribute('aria-label','Menyunu bağla');backdrop.tabIndex=-1;body.append(backdrop)
  const avatar=topbar.querySelector('.app-avatar-button'), userMenu=topbar.querySelector('.app-user-menu'), mobileButton=topbar.querySelector('.app-mobile-menu')
  function closeUserMenu() {userMenu.hidden=true;avatar.setAttribute('aria-expanded','false')}
  function mobileMenu(open) {
    const wasOpen=body.dataset.mobileMenu==='open'
    body.dataset.mobileMenu=open?'open':'closed'
    mobileButton.setAttribute('aria-expanded',String(open))
    sidebar.inert=false
    if(open)collapse.focus()
    else if(wasOpen&&mobile.matches)mobileButton.focus()
  }
  function syncSidebar() {
    collapse.setAttribute('aria-label',t(mobile.matches?'closeMenu':body.dataset.sidebar==='collapsed'?'expandSidebar':'collapseSidebar'))
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
  topbar.querySelectorAll('[data-app-open]').forEach(button=>button.addEventListener('click',()=>{if(button.dataset.appOpen==='settings'){window.tenantOpenSettingsHub?.();closeUserMenu();return}const target=nav.querySelector(`[data-view="${button.dataset.appOpen}"]`);if(target&&!target.hidden&&target.style.display!=='none')target.click();closeUserMenu()}))
  topbar.querySelector('[data-app-logout]').addEventListener('click',()=>{closeUserMenu();document.getElementById('logout')?.click()})
  topbar.querySelectorAll('[data-app-theme-choice]').forEach(button=>button.addEventListener('click',()=>{theme=button.dataset.appThemeChoice;remember('tenant-app-theme',theme);applyTheme()}))
  document.addEventListener('click',event=>{if(!topbar.contains(event.target))closeUserMenu()})
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeUserMenu();mobileMenu(false)}})
  applyTheme()

  const groups=[{id:'operator',label:t('operatorWorkspace'),pages:['dialogs','support']},{id:'crm',label:'CRM',pages:['tasks','deals','customers','companies','hot_orders']},{id:'ai',label:'AI',pages:['ai_agents','ai_administrator','knowledge_center','support_cases','ai_testing']},{id:'management',label:'İdarəetmə',pages:['approvals','linear','news','finance']},{id:'company',label:'Şirkət',pages:['employees','integrations','settings','profile']},{id:'reports',label:reportLabel(),pages:['night_report','ai_analytics','ai_dashboard']}]
  const containers=new Map()
  for(const group of groups){
    const section=document.createElement('div');section.className='app-nav-group';section.dataset.group=group.id
    section.innerHTML=`<button type="button" class="app-nav-group-title" aria-expanded="true"><span>${group.label}</span>${icon('m6 9 6 6 6-6')}</button><div class="app-nav-group-items"></div>`
    section.querySelector('.app-nav-group-title').addEventListener('click',event=>{if(group.id==='reports'&&mobile.matches){section.querySelector('.app-nav-group-items').togglePopover();return;}section.dataset.collapsed=section.dataset.collapsed==='true'?'false':'true';event.currentTarget.setAttribute('aria-expanded',String(section.dataset.collapsed!=='true'))})
    nav.append(section);containers.set(group.id,section)
  }
  const reports=containers.get('reports'),reportItems=reports.querySelector('.app-nav-group-items'),reportToggle=reports.querySelector('.app-nav-group-title');
  reportItems.id='appReportsMenu';reportToggle.setAttribute('aria-controls',reportItems.id);
  function reportMode(){if(reportItems.matches(':popover-open'))reportItems.hidePopover();if(mobile.matches){reportItems.setAttribute('popover','auto');reportItems.setAttribute('role','menu');reportItems.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('role','menuitem'));reportToggle.setAttribute('aria-haspopup','menu');reportToggle.setAttribute('aria-expanded','false');}else{reportItems.removeAttribute('popover');reportItems.removeAttribute('role');reportItems.querySelectorAll('[data-view]').forEach(b=>b.removeAttribute('role'));reportToggle.removeAttribute('aria-haspopup');reportToggle.setAttribute('aria-expanded',String(reports.dataset.collapsed!=='true'));}}
  reportItems.addEventListener('toggle',event=>{if(mobile.matches){reportToggle.setAttribute('aria-expanded',String(event.newState==='open'));if(event.newState==='open')[...reportItems.querySelectorAll('[data-view]')].find(b=>!b.hidden&&b.style.display!=='none')?.focus();else if(reportItems.contains(document.activeElement))reportToggle.focus();}});
  reportItems.addEventListener('keydown',event=>{if(!mobile.matches||!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;const items=[...reportItems.querySelectorAll('[data-view]')].filter(b=>!b.hidden&&b.style.display!=='none'&&!b.disabled);if(!items.length)return;event.preventDefault();event.stopPropagation();const i=items.indexOf(document.activeElement),next=event.key==='Home'?0:event.key==='End'?items.length-1:(i+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next].focus();});
  reportItems.addEventListener('click',event=>{if(mobile.matches&&event.target.closest('[data-view]')&&reportItems.matches(':popover-open'))reportItems.hidePopover();});
  mobile.addEventListener('change',reportMode);reportMode();
  function organizeNav() {
    for(const button of [...nav.querySelectorAll('[data-view],[data-report-link]')]){
      const page=button.dataset.view||button.dataset.reportLink
      if(navIconPaths[page]){let iconHost=button.querySelector('.nav-icon');if(!iconHost){iconHost=document.createElement('span');iconHost.className='nav-icon';iconHost.setAttribute('aria-hidden','true');button.prepend(iconHost)}const existingIcon=iconHost.querySelector('svg');if(existingIcon?.dataset.appIcon!==page){iconHost.innerHTML=icon(navIconPaths[page]);iconHost.querySelector('svg').dataset.appIcon=page}}
      if(['employees','integrations'].includes(page)&&nav.querySelector('#settingsFamilyNav')){button.classList.add('app-nav-route-hidden');button.setAttribute('aria-hidden','true');button.tabIndex=-1}
      if(page==='settings'||page==='profile'){button.classList.add('app-nav-route-hidden');button.setAttribute('aria-hidden','true');button.tabIndex=-1;continue}
      let label=button.querySelector('.app-nav-label,[data-i18n],[data-crm-nav-label],[data-ai-teams-label],[data-ai-dashboard-label],[data-ai-analytics-label],[data-knowledge-center-label]')
      if(!label)label=[...button.children].find(child=>child.matches('span:not(.nav-icon):not(.badge)'))||null
      if(!label){label=document.createElement('span');label.textContent=[...button.childNodes].filter(node=>node.nodeType===Node.TEXT_NODE).map(node=>node.textContent).join('').trim()||page}
      for(const node of [...button.childNodes])if(node.nodeType===Node.TEXT_NODE)node.remove()
      for(const extra of button.querySelectorAll('[data-i18n],[data-crm-nav-label],[data-ai-teams-label],[data-ai-dashboard-label],[data-ai-analytics-label],[data-knowledge-center-label],.app-nav-label,span:not(.nav-icon):not(.badge)'))if(extra!==label)extra.remove()
      label.removeAttribute('data-crm-nav-label')
      label.className='app-nav-label';label.dataset.appNavLabel=page
      label.removeAttribute('data-i18n')
      const labelText=pageLabel(page)
      if(label.textContent!==labelText)label.textContent=labelText
      if(!label.parentElement||label.parentElement!==button)button.append(label)
      button.title=label.textContent
      const group=groups.find(group=>group.pages.includes((button.dataset.view||button.dataset.reportLink)))||groups[0], section=containers.get(group.id),items=section.querySelector('.app-nav-group-items')
      if(button.parentElement!==items)items.append(button)
    }
    if(mobile.matches)reportItems.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('role','menuitem'));
    const crmItems=containers.get('crm')?.querySelector('.app-nav-group-items')
    if(crmItems){
      const ordered=groups.find(group=>group.id==='crm').pages.map(page=>crmItems.querySelector(`[data-view="${page}"]`)).filter(Boolean)
      ordered.forEach((button,index)=>{if(crmItems.children[index]!==button)crmItems.insertBefore(button,crmItems.children[index]||null)})
    }
    const settingsFamily=nav.querySelector('#settingsFamilyNav'),companyItems=containers.get('company')?.querySelector('.app-nav-group-items');
    if(settingsFamily&&companyItems&&settingsFamily.parentElement!==companyItems)companyItems.append(settingsFamily);
    const legacyCrm=nav.querySelector('#nativeCrmNav')
    if(legacyCrm&&!legacyCrm.querySelector('[data-view]'))legacyCrm.remove()
    for(const section of containers.values()){const hidden=![...section.querySelectorAll('[data-view],[data-report-link]')].some(button=>!['employees','integrations','settings','profile'].includes(button.dataset.view)&&!button.hidden&&button.style.display!=='none');if(section.hidden!==hidden)section.hidden=hidden;if(hidden&&section===reports&&reportItems.matches(':popover-open'))reportItems.hidePopover()}
    const settingsAvailable=['integrations','employees'].some(page=>{const button=nav.querySelector(`[data-view="${page}"]`);return button&&!button.hidden&&button.style.display!=='none'})
    topbar.querySelectorAll('[data-app-open="settings"]').forEach(button=>button.hidden=!settingsAvailable)
  }
  const languageSelect=topbar.querySelector('[data-app-language]')
  const instructionLink=topbar.querySelector('[data-app-instructions]')
  function updateInstructionLink(){const lang=language(),label=crmLabels[lang]?.instructions||t('instructions');instructionLink.href='/app/instructions?site_lang='+encodeURIComponent(lang);instructionLink.title=label;instructionLink.setAttribute('aria-label',label);instructionLink.querySelector('span').textContent=label}
  updateInstructionLink()
  languageSelect.value=window.tenantI18n?.language||'az'
  languageSelect.addEventListener('change',()=>window.tenantI18n?.setLanguage(languageSelect.value))
  function updateLanguage(){reportToggle.querySelector('span').textContent=reportLabel();const company=document.getElementById('company');if(company&&['Şirkət','Компания','Company'].includes(company.textContent))company.textContent=t('company');syncSidebar();mobileButton.setAttribute('aria-label',t('openMenu'));avatar.setAttribute('aria-label',t('userMenu'));backdrop.setAttribute('aria-label',t('closeMenu'));for(const button of topbar.querySelectorAll('[data-app-open=settings]')){button.setAttribute('aria-label',t('settings'));button.title=t('settings');}for(const button of topbar.querySelectorAll('[data-app-theme-choice]')){const label=t(button.dataset.appThemeChoice+'Theme');button.title=label;button.setAttribute('aria-label',label);}languageSelect.value=language();languageSelect.setAttribute('aria-label',t('language'));for(const button of nav.querySelectorAll('[data-view],[data-report-link]')){const label=button.querySelector('.app-nav-label'),page=button.dataset.view||button.dataset.reportLink,value=pageLabel(page);if(label&&label.textContent!==value)label.textContent=value;button.title=value;}updateInstructionLink();for(const section of containers.values()){const key={operator:'operatorWorkspace',management:'management',company:'company'}[section.dataset.group];if(key){const label=section.querySelector('.app-nav-group-title span'),value=t(key);if(label.textContent!==value)label.textContent=value;}}updateContext()}
  document.addEventListener('tenant-language',updateLanguage)
  const navObserver=new MutationObserver(organizeNav)
  navObserver.observe(nav,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','style']})
  organizeNav()
  nav.addEventListener('click',event=>{if(event.target.closest('[data-view],#settingsFamilyNav')){mobileMenu(false);closeUserMenu();requestAnimationFrame(updateContext)}})
  function updateContext() {
    const page=document.querySelector('main>.view.active'),title=page?.querySelector('h1')?.textContent||''
    topbar.querySelector('.app-topbar-context').textContent=title
    nav.querySelectorAll('[data-view],[data-report-link]').forEach(button=>button.setAttribute('aria-current',button.classList.contains('active')?'page':'false'))
  }
  document.addEventListener('tenant-view',()=>{if(reportItems.matches(':popover-open'))reportItems.hidePopover()});
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
  updateLanguage()
  installMobileBottomNav({sidebar,nav,mobile})
  installMobilePageHeader({topbar,mobile,avatar,userMenu,languageSelect,instructionLink,pageLabel})

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
    if(event.defaultPrevented||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return
    const tab=event.target.closest('[role="tab"]'),tablist=tab?.closest('[role="tablist"]')
    if(!tablist)return
    const tabs=[...tablist.querySelectorAll('[role="tab"]')].filter(button=>!button.hidden&&!button.disabled)
    const index=tabs.indexOf(tab);if(index<0||!tabs.length)return
    const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length
    event.preventDefault();tabs[next].click();tabs[next].focus()
  })
  decorate()
}

// Layout only. Keep the original route nodes, listeners, badges and permission state.
function installMobileBottomNav({sidebar,nav,mobile}) {
  const body=document.body,root=document.documentElement
  nav.setAttribute('role','navigation')
  let frame=0,lastActive=null
  function sync(){
    frame=0
    if(!window.document?.documentElement||!body.isConnected)return
    // Search/input focus alone must not hide navigation; overlays and open chat still do.
    const modal=[...document.querySelectorAll('[role="dialog"],dialog[open],.modal.show,.ai-agents-modal.show,#operatorDialogBody[data-copilot-open=true] #operatorCopilot')].some(el=>el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden')
    const conversationOpen=!!document.querySelector('#view-dialogs.active #operatorDialogs[data-conversation-open="true"]')
    const suppressed=mobile.matches&&(conversationOpen||modal)
    if(sidebar.dataset.bottomNavSuppressed!==String(suppressed))sidebar.dataset.bottomNavSuppressed=String(suppressed)
    sidebar.inert=suppressed
    const height=mobile.matches&&!suppressed?Math.ceil(sidebar.getBoundingClientRect().height):0
    if(root.style.getPropertyValue('--app-bottom-nav-height')!==height+'px')root.style.setProperty('--app-bottom-nav-height',height+'px')
    const active=nav.querySelector('[aria-current="page"]')
    if(mobile.matches&&!suppressed&&active&&active!==lastActive&&active.getClientRects().length){
      const a=active.getBoundingClientRect(),n=nav.getBoundingClientRect()
      if(a.left<n.left+8)nav.scrollLeft-=n.left+8-a.left
      else if(a.right>n.right-8)nav.scrollLeft+=a.right-(n.right-8)
    }
    lastActive=active
  }
  function schedule(){if(window.document?.documentElement&&body.isConnected&&!frame)frame=requestAnimationFrame(sync)}
  if(typeof ResizeObserver!=='undefined')new ResizeObserver(schedule).observe(sidebar)
  new MutationObserver(schedule).observe(body,{subtree:true,childList:true,attributes:true,attributeFilter:['class','style','hidden','open','aria-current','data-conversation-open']})
  document.addEventListener('focusin',schedule)
  document.addEventListener('focusout',schedule)
  window.visualViewport?.addEventListener('resize',schedule)
  window.visualViewport?.addEventListener('scroll',schedule)
  mobile.addEventListener('change',schedule)
  nav.addEventListener('keydown',event=>{
    if(!mobile.matches||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return
    const items=[...nav.querySelectorAll('[data-view],[data-report-link]')].filter(el=>el.getClientRects().length&&!el.disabled)
    const item=event.target.closest('[data-view],[data-report-link]'),index=items.indexOf(item)
    if(index<0)return
    event.preventDefault()
    const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowRight'?1:-1)+items.length)%items.length
    items[next].focus();items[next].scrollIntoView({block:'nearest',inline:'nearest'})
  })
  schedule()
}


