const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('../.support-test-deps/node_modules/playwright');
const {installFixture}=require('./task_read_save_flow_fixture.cjs');
const artifacts=path.join(__dirname,'../output/support-workspace');fs.mkdirSync(artifacts,{recursive:true});
const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true});
 try{for(const width of [1440,390,320]){
  const context=await browser.newContext({viewport:{width,height:900}});await context.tracing.start({screenshots:true,snapshots:true});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(installFixture,{manager:false});
  await page.addInitScript(()=>{
   taskFlowFixture.profile.capabilities.modules.support=true;taskFlowFixture.profile.capabilities.modules.dialogs=true;
   const original=fetch;window.testSupport={calls:[],writes:[],allowed:true,readOnly:false};
   window.tenantOperator={hasRight:()=>testSupport.allowed,messageMarkup:items=>items.map(m=>'<p>'+m.body+'</p>').join('')};
   const tickets=[
    {id:'00000001-0000-4000-8000-000000000001',contact_name:'Учебная компания',description:'Касса не печатает чек после замены принтера. Проверить подключение и настройки.',status:'waiting',priority:'high',channel_id:'channel-a',channel_name:'Техническая поддержка',conversation_id:'conversation-a',lead_id:41,assigned_to:null,assignee_name:'',source:'ai_handoff',last_message_at:'2026-11-17T08:15:00Z',ai_state:'human',summary_state:'unavailable',suggestions:'',next_steps:'Проверить кабель и тестовую печать',risk_assessment:''},
    {id:'00000002-0000-4000-8000-000000000002',contact_name:'Учебный магазин',description:'Уточнить порядок продолжения инвентаризации на следующий день.',status:'in_progress',priority:'normal',channel_id:'channel-a',channel_name:'Техническая поддержка',conversation_id:'conversation-b',lead_id:42,assigned_to:11,assignee_name:'Сотрудник поддержки',source:'manual',last_message_at:'2026-11-17T07:42:00Z',ai_state:'active',agent_id:'synthetic-agent',summary_state:'unavailable'},
    {id:'00000003-0000-4000-8000-000000000003',contact_name:'Учебный клиент',description:'Вопрос по настройке доступа нового кассира.',status:'new',priority:'normal',channel_id:'channel-a',channel_name:'Техническая поддержка',conversation_id:'conversation-c',lead_id:43,last_message_at:'2026-11-17T07:30:00Z',ai_state:'human',summary_state:'unavailable'}
   ];
   const ok=result=>new Response(JSON.stringify({success:true,result}),{headers:{'Content-Type':'application/json'}});
   window.fetch=async(url,options={})=>{
    const u=new URL(String(url),location.href),method=options.method||'GET';
    if(!u.pathname.startsWith('/api/platform/ai/'))return original(url,options);
    testSupport.calls.push({path:u.pathname,query:u.search,method});
    if(method!=='GET')testSupport.writes.push({path:u.pathname,method,body:JSON.parse(options.body||'{}')});
    if(u.pathname==='/api/platform/ai/support-tickets')return ok({tickets:u.searchParams.get('search')==='absent'?[]:tickets,members:[{telegram_id:11,display_name:'Сотрудник поддержки'}],channels:[{id:'channel-a',name:'Техническая поддержка'}],next_offset:null,unresolved_count:3,attention_count:1,sla_policy:testSupport.readOnly?null:{urgent:1,high:4,normal:24,low:72}});
    if(u.pathname.startsWith('/api/platform/ai/support-tickets/')){const ticket=tickets.find(t=>t.id===u.pathname.split('/').at(-1));if(!ticket)throw Error('Unexpected ticket route');if(method==='PATCH')Object.assign(ticket,JSON.parse(options.body));return ok({ticket,capabilities:{update:!testSupport.readOnly},history_context:{boundary_status:'accepted_interval',accepted_at:'2026-11-17T08:00:00Z',created_at:'2026-11-17T08:15:00Z'},messages:[{id:'message-1',direction:'incoming',message_type:'text',body:'После замены принтера чек не печатается.',happened_at:'2026-11-17T08:01:00Z'},{id:'message-2',direction:'outgoing',message_type:'text',body:'Проверим подключение и тестовую печать.',happened_at:'2026-11-17T08:02:00Z'}],next_before:null});}
    if(u.pathname==='/api/platform/ai/dialogs/history'){const ticket=tickets.find(t=>String(t.lead_id)===u.searchParams.get('lead_id'));return ok({dialog:{provider:'connect',lead_id:ticket.lead_id,channel_id:ticket.channel_id,conversation_id:ticket.conversation_id},messages:[{id:'message-1',body:'Учебное сообщение клиента'}],next_before:null});}
    if(u.pathname.endsWith('/destinations'))return ok({destinations:[]});
    if(u.pathname.endsWith('/replies'))return ok({consultations:[],replies:[]});
    if(u.pathname.includes('/public-shares'))return ok([]);
    return ok({});
   };
  });
  const assets=['tenant-operator-i18n.js','tenant-request-coordinator.js','tenant-view-demand.js','tenant-support-consultations.js','tenant-support-inbox.js'];
  let html=read('docs/tenant-app.html').replace(/<script\b[^>]*src="([^"]+)"[^>]*><\/script>/gi,(tag,url)=>assets.includes(path.basename(url))?'<script>'+read('docs/'+path.basename(url)).replace(/<\/script/gi,'<\\/script')+'</script>':'');
  html=html.replace('</body>',()=>'<script>'+read('docs/tenant-chakra-ui.js').replace(/<\/script/gi,'<\\/script')+'</script></body>');
  await page.route('**/*',r=>r.request().isNavigationRequest()?r.fulfill({contentType:'text/html',body:html}):r.abort());
  await page.goto('https://fixture.test/app?view=support&lang=ru');await page.locator('[data-support-id]').first().waitFor();
  assert.equal(await page.locator('.support-row').count(),3);assert.equal(await page.locator('#supportAdvanced').isVisible(),false);
  assert.equal(await page.locator('#supportRows').getByText('Обычный',{exact:true}).count(),0);
  assert.equal(await page.locator('#supportRows').getByText('Новое',{exact:true}).count(),1,'one status instead of duplicated source/status labels');
  assert(await page.locator('#supportSearch').evaluate(n=>!!n.form&&!Array.from(n.form.elements).some(e=>e.type==='password')));
  const screenshot=label=>page.screenshot({path:path.join(artifacts,`${width}-${label}.png`)});
  await screenshot('queue');
  assert(await page.locator('#supportInbox').evaluate(n=>n.scrollWidth<=n.clientWidth+1),'queue fits viewport');
  await page.locator('#supportSlaSettings>summary').click();
  assert(await page.locator('#supportSlaSettings form').evaluate(n=>{const r=n.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;}),'settings fit viewport');
  await page.locator('#supportSlaSettings>summary').click();
  const before=await page.evaluate(()=>testSupport.calls.length);await page.locator('#supportFiltersToggle').click();await page.locator('#supportAdvanced').waitFor();assert.equal(await page.evaluate(()=>testSupport.calls.length),before,'expanding filters is local');
  await page.locator('#supportChannel').selectOption('channel-a');await page.waitForFunction(()=>testSupport.calls.some(c=>c.query.includes('channel_id=channel-a')));
  await page.locator('#supportFiltersToggle').click();assert.match(await page.locator('#supportFiltersToggle').textContent(),/1/,'collapsed filters retain their active count');
  await page.locator('#supportMine').click();await page.waitForFunction(()=>document.getElementById('supportMine').getAttribute('aria-pressed')==='true');
  await page.locator('#supportSearch').focus();await page.locator('#supportSearch').fill('absent');await page.locator('#supportSearch').press('Enter');await page.locator('.support-empty').waitFor();assert.equal(new URL(page.url()).searchParams.get('view'),'support');
  await page.locator('#supportSearch').focus();await page.locator('#supportSearch').fill('');await page.locator('[data-support-id]').first().waitFor();
  await page.locator('[data-support-id]').first().click();await page.locator('#supportTicketTitle').waitFor();
  assert(await page.locator('#supportTake').isVisible());assert.equal(await page.locator('#supportHistory .support-message').count(),2);await screenshot('history');assert(await page.locator('#supportResolve').isVisible());assert.equal(await page.locator('#supportTakeover').count(),0);
  await page.locator('#supportTab-details').click();assert(await page.locator('#supportEditStatus').isVisible());assert.equal(await page.locator('#supportSummaryText').count(),0);
  assert(await page.locator('#supportDetailContent').evaluate(n=>n.scrollWidth<=n.clientWidth+1),'card fits viewport');
  await page.locator('#supportDescription').fill('Несохранённое описание');await page.locator('#supportTab-history').click();await page.locator('#supportTab-details').click();assert.equal(await page.locator('#supportDescription').inputValue(),'Несохранённое описание');
  await screenshot('card');assert.equal(await page.locator('#supportCaseContext').count(),0);assert.equal(await page.locator('#supportCaseMaterials').count(),0);
  await page.locator('#supportTake').click();await page.waitForFunction(()=>testSupport.writes.length===1);assert.equal(await page.evaluate(()=>testSupport.writes[0].body.status),'in_progress');
  await page.locator('#supportClose').click();await page.locator('#supportDetail').waitFor({state:'hidden'});
  assert(await page.locator('[data-support-id]').first().evaluate(n=>n===document.activeElement));
  // Removed AI diagnostics are not mounted even for a historically active AI.
  await page.locator('[data-support-id]').nth(1).click();await page.locator('#supportTicketTitle').waitFor();assert.equal(await page.locator('.support-card-header .support-ai-state[role="status"]').count(),0);await page.locator('#supportClose').click();
  await page.evaluate(()=>testSupport.readOnly=true);await page.locator('[data-support-id]').first().click();await page.locator('#supportTicketTitle').waitFor();assert(await page.locator('#supportTake').isDisabled());await page.locator('#supportTab-details').click();assert(await page.locator('#supportSave').isDisabled());assert(await page.locator('#supportEditStatus').isDisabled());
  // Permission loss removes list, modal, and previously shown counts.
  await page.evaluate(()=>{testSupport.allowed=false;document.dispatchEvent(new CustomEvent('tenant-profile',{detail:{...taskFlowFixture.profile,capabilities:{modules:{support:false}},member:{...taskFlowFixture.profile.member,active:false}}}));});
  assert.equal(await page.locator('.support-row').count(),0);assert.equal(await page.locator('#supportDetail').isVisible(),false);assert.equal(await page.locator('#supportCount').textContent(),'');
  assert.equal(await page.evaluate(()=>testSupport.writes.length),1,'UI exploration and denial never trigger sends/AI/extra writes');assert.deepEqual(errors,[]);
  await context.tracing.stop({path:path.join(artifacts,`${width}.zip`)});await context.close();
 }
 console.log('PASS: built app support queue/card at 1440/390/320, filters, Enter, draft, actions, read-only and permission revocation.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
