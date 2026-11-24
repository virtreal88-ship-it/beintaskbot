const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const source=fs.readFileSync(__dirname+'/../ui/src/mobile-page-header.js','utf8').replace('export function','function');
(async()=>{
 const dom=new JSDOM(`<div class="shell"><main><section class="view active" id="view-tasks"><div class="top"><h1>Задачи</h1><button id="create">Create</button></div></section><section class="view" id="view-deals"><h1>Сделки</h1></section></main><header class="app-topbar"><div class="app-topbar-actions"><select data-app-language><option>ru</option></select><a data-app-instructions>Guide</a><button class="app-avatar-button">NQ</button></div><div class="app-user-menu" hidden><button data-app-open="profile">Profile</button><button data-app-open="settings" hidden>Settings</button><button data-app-logout>Logout</button></div></header></div>`,{runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document,mobile={matches:true,addEventListener(n,fn){this.listener=fn}};w.tenantI18n={language:'ru'};w.eval(source);
 const topbar=d.querySelector('.app-topbar'),avatar=d.querySelector('.app-avatar-button'),userMenu=d.querySelector('.app-user-menu'),languageSelect=d.querySelector('select'),instructionLink=d.querySelector('a'),heading=d.querySelector('h1'),originalParent=heading.parentElement;
 let changes=0,notifications=0;languageSelect.onchange=()=>changes++;
 w.installMobilePageHeader({topbar,avatar,userMenu,languageSelect,instructionLink,mobile,pageLabel:k=>k});
 const wait=()=>new Promise(r=>setTimeout(r,60));await wait();
 assert.equal(heading.parentElement,originalParent,'React heading ownership preserved');
 assert(heading.classList.contains('app-mobile-original-title'));assert.equal(d.querySelector('.app-mobile-title-row h1').textContent,'Задачи');
 assert.equal(topbar.parentElement.className,'app-mobile-title-row');assert(userMenu.contains(languageSelect));assert(userMenu.contains(instructionLink));
 languageSelect.dispatchEvent(new w.Event('change'));assert.equal(changes,1);assert.equal(d.querySelector('[data-app-open=settings]').hidden,true,'permission retained');
 // Notifications can mount after initial shell layout.
 const bell=d.createElement('button');bell.id='dialogConsultationBell';bell.onclick=()=>notifications++;languageSelect.before(bell);bell.innerHTML='bell<span class="notification-unread-badge">7</span>';await wait();
 assert(topbar.querySelector('.app-topbar-actions').contains(bell));
 assert(!userMenu.contains(bell));bell.click();assert.equal(notifications,1);
 assert.equal(bell.querySelector('.notification-unread-badge').textContent,'7');
 avatar.textContent='AB';await wait();assert.equal(bell.querySelector('.notification-unread-badge').textContent,'7');
 avatar.focus();assert.equal(d.activeElement,avatar);d.querySelector('#view-tasks').classList.remove('active');d.querySelector('#view-deals').classList.add('active');await wait();assert.equal(d.querySelectorAll('.app-mobile-title-row').length,1);assert.equal(d.querySelector('.app-mobile-title-row h1').textContent,'Сделки');assert(!heading.classList.contains('app-mobile-original-title'));assert.equal(d.activeElement,avatar,'route reparent preserves returned avatar focus');
 let mutations=0;const obs=new w.MutationObserver(()=>mutations++);obs.observe(d,{subtree:true,childList:true,attributes:true});await wait();assert.equal(mutations,0,'settled layout has no observer loop');
 mobile.matches=false;mobile.listener();await wait();assert.equal(topbar.parentElement.className,'shell');assert.equal(d.querySelectorAll('.app-mobile-title-row').length,0);assert.equal(languageSelect.parentElement.className,'app-topbar-actions');assert.equal(bell.parentElement.className,'app-topbar-actions');await wait();mutations=0;await wait();assert.equal(mutations,0,'desktop restoration settles');
 mobile.matches=true;mobile.listener();await wait();assert.equal(d.querySelectorAll('.app-mobile-title-row').length,1);
 dom.window.close();console.log('PASS mobile title, existing controls, unread state, route changes, desktop restoration, no observer loops');
})().catch(e=>{console.error(e);process.exit(1)});
