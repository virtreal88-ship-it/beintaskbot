const {revealAccountControl,selectAppLanguage}=require('./account_controls_browser.cjs');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.join(__dirname,'..'),tenant='00000000-0000-0000-0000-000000000101',channel='00000000-0000-0000-0000-000000000201';
const group={provider:'connect',lead_id:41,channel_id:channel,conversation_id:'123456789@g.us',customer_kind:'group',customer_name:'Group',channel_name:'WhatsApp',display_state:'human',state:'human',ai_availability:'stopped',available_actions:[],can_send:true,controls_allowed:true,agent_status:'manual'};
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||(fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':chromium.executablePath()),args:['--no-sandbox']});try{
 for(const width of [320,390,1280]){const page=await browser.newPage({viewport:{width,height:760}});let mode='available',gate=null,release;const calls=[];
  await page.route('**/*',async route=>{const request=route.request(),url=new URL(request.url()),name=url.pathname;calls.push({name,method:request.method()});
   if(name==='/app')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8')});
   if(name.startsWith('/assets/')){const file=path.join(root,'docs',path.basename(name));return fs.existsSync(file)?route.fulfill({path:file}):route.fulfill({status:404,body:''});}
   if(name==='/api/platform/me')return route.fulfill({json:{success:true,member:{tenant_id:tenant,telegram_id:33,role:'worker',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}}});
   let result={items:[],tickets:[],agents:[],members:[]};
   if(name.endsWith('/dialogs/history'))result={dialog:group,messages:[],next_before:null};
   else if(name.endsWith('/dialogs'))result={dialogs:[group],channels:[{id:'connect:'+channel,name:'WhatsApp'}],agents:[],next_offset:null};
   else if(name.endsWith('/dialogs/group-participants')){assert.equal(request.method(),'GET');assert.equal(request.postData(),null);assert.equal(url.searchParams.get('channel_id'),channel);assert.equal(url.searchParams.get('conversation_id'),group.conversation_id);if(gate)await gate;result=mode==='available'?{status:'available',observed_at:new Date().toISOString(),phones:['+994500000001','+994500000002'],total:3,unresolved:1}:{status:'unavailable'};}
   return route.fulfill({json:{success:true,result}});
  });
  await page.goto('https://crm.test/app?view=dialogs');await selectAppLanguage(page,'ru');await page.locator('.operator-row').first().click();
  const avatar=page.locator('#operatorClientCardOpen'),card=page.locator('#operatorClientCard');
  const roster=card.locator('.operator-client-panel-block').filter({has:page.locator('[data-group-participants]')});
  const expandRoster=()=>roster.locator(':scope > summary').click();
  await avatar.click();
  assert.equal(await roster.getAttribute('open'),null);
  assert.equal(calls.filter(c=>c.name.endsWith('/dialogs/group-participants')).length,0,'a collapsed group does not fetch its roster');
  await expandRoster();await card.getByText('+994500000001',{exact:true}).waitFor();assert.match(await card.innerText(),/Участников без доступного номера: 1/);
  assert.equal(await card.locator('.operator-roster-row').count(),2);
  assert.equal(await card.locator('.operator-roster-avatar svg').count(),2);
  assert.match(await card.locator('.operator-roster-count').innerText(),/3/);
  assert.equal(await card.locator('.operator-roster-meta').getAttribute('open'),null);
  await card.getByRole('button',{name:'Поиск участников'}).click();
  const search=card.getByRole('searchbox',{name:'Поиск участников'});await search.fill('000002');await search.press('Enter');assert.equal(calls.some(c=>c.name.endsWith('/dialogs/display-name')),false);
  assert.equal(await card.locator('.operator-roster-row:visible').count(),1);
  await search.fill('99999');await card.getByText('Участники не найдены',{exact:true}).waitFor();
  await card.getByRole('button',{name:'Поиск участников'}).click();assert.equal(await card.locator('.operator-roster-row:visible').count(),2);
  assert.equal(await card.locator('.operator-roster-list').evaluate(e=>e.scrollWidth<=e.clientWidth),true);
  assert.deepEqual(await card.locator('.operator-roster-list').evaluate(list=>{
    const style=getComputedStyle(list);
    const panel=getComputedStyle(list.closest('.operator-client-panel-content'));
    return [style.overflowY,style.maxHeight,panel.overflowY];
  }),['visible','none','auto'],'the right panel is the only vertical scroller');
  assert((await card.locator('.operator-roster-row').first().boundingBox()).height<=36,'roster rows are compact');
  assert.equal(calls.filter(c=>c.name.endsWith('/dialogs/group-participants')).length,1);
  const box=await card.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);assert.equal(calls.some(c=>c.name.endsWith('/dialogs/display-name')),false);
  await page.keyboard.press('Escape');mode='unavailable';await avatar.click();assert.equal(await roster.getAttribute('open'),null);await expandRoster();await card.getByText('Коннектор пока не передал состав группы.').waitFor();assert.equal(await card.getByText('+994500000001',{exact:true}).count(),0);
  await page.keyboard.press('Escape');mode='available';gate=new Promise(r=>release=r);await avatar.click();await expandRoster();await card.getByText('Загрузка…',{exact:true}).waitFor();await page.keyboard.press('Escape');release();gate=null;await page.waitForTimeout(50);assert.equal(await card.isVisible(),false);
  await avatar.click();await expandRoster();await card.getByText('+994500000001',{exact:true}).waitFor();await page.close();
 }
 console.log('group participants card passed');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});

