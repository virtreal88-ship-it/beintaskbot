/* Desktop and mobile workflow against fixture responses. No external sends. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '../.support-test-deps/node_modules/playwright');
const docs = path.resolve(__dirname, '../docs');
const html = fs.readFileSync(path.join(docs, 'tenant-app.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');

async function verify(page) {
  await page.route('**/*', route => route.request().isNavigationRequest() ?
    route.fulfill({contentType:'text/html',body:html}) : route.abort());
  await page.goto('https://example.test/app?view=dialogs');
  await page.evaluate(() => {
    document.querySelectorAll('.view').forEach(node => node.classList.toggle('active',node.id==='view-dialogs'));
    window.tenantProfileSnapshot={
      member:{tenant_id:'fixture',telegram_id:44,role:'operator',active:true},
      capabilities:{modules:{dialogs:true}}
    };
    const group = '120363432608782851@g.us';
    const episode = 'b585cdde-a2a2-4fa5-aae5-47a3b620b051';
    let joined = false;
    let leader = false;
    let revision = 0;
    const peers = [{employee_id:'33',name:'Operator A'}];
    const makeRoute = (id,lead,number) => ({provider:'connect',channel_active:true,channel_id:id,
      channel_name:'Fixture '+id,lead_id:lead,conversation_id:group,number});
    const routes = [makeRoute('b',42,'+994500000112'),makeRoute('c',43,'+994500000113')];
    function dialog(route) {
      const own = joined && route.channel_id==='c';
      return {...route,customer_name:'Fixture group',customer_kind:'group',lifecycle_enabled:true,
        can_send:own,lifecycle:{state:own?'accepted':'waiting',operator_id:own?44:null,has_inbound:true,
          id:own?episode:null,revision,can_close:own&&leader,
          group_work:{id:episode,role:own?(leader?'leader':'participant'):null,can_join:!joined,
            can_leave:own&&!leader,number:route.number,colleagues:own?peers:[],...(!joined?{sources:routes}:{})}}};
    }
    window.groupRequests=[];
    window.makeLead=()=>{leader=true;return window.tenantOperator.openDialog(dialog(routes[1]),true);};
    window.fetch=async (input, options={}) => {
      const url=new URL(String(input),location.origin);
      if(url.pathname.endsWith('/me'))return {ok:true,json:async()=>window.tenantProfileSnapshot};
      let result;
      if(url.pathname.endsWith('/dialogs/history')){
        result={dialog:dialog(routes.find(route=>route.channel_id===url.searchParams.get('channel_id'))),
          messages:joined?[{id:'first',external_id:'first',direction:'incoming',source:'connect',message_type:'text',
            body:'Fixture reply',author_type:'employee',author_name:'Operator A',sending_number:'+994500000111',
            created_at:'2026-11-26T08:00:00Z'}]:[],read_through_message_id:null,next_before:null};
      }else if(url.pathname.endsWith('/dialogs_lifecycle')){
        const command=JSON.parse(options.body);
        window.groupRequests.push(command);
        revision++;
        if(command.action==='join_group')joined=true;
        if(command.action==='leave_group'||command.action==='close')joined=false;
        if(command.action==='transfer_group')leader=false;
        result={group_changed:true,lifecycle:dialog(routes[1]).lifecycle};
      }else if(url.pathname.endsWith('/dialogs')){
        const sources=joined?[dialog(routes[1])]:routes.map(dialog);
        result={dialogs:[{...sources[0],group_sources:sources,unread_count:2}],channels:[],agents:[],total_count:1};
      }else{
        result={};
      }
      return {ok:true,status:200,json:async()=>({success:true,result})};
    };
  });
  for (const asset of ['tenant-operator-i18n.js','tenant-operator-dialogs.js']) {
    await page.addScriptTag({path:path.join(docs,asset)});
  }
  await page.evaluate(()=>window.tenantOperator.refresh());
  const card=page.locator('.operator-row');
  assert.equal(await card.count(),1);
  assert.equal(await card.locator('.operator-unread-count').textContent(),'2');
  await card.click();
  await page.locator('#operatorComposerSourceSelect').selectOption('1');
  await page.waitForFunction(()=>window.tenantOperator.currentDialog?.channel_id==='c');
  const join=page.locator('[data-operator-lifecycle="join_group"]');
  await join.click();
  await page.locator('[data-group-sending-number]').filter({hasText:'+994500000113'}).waitFor();
  await page.waitForFunction(()=>document.querySelector('#chatText')?.disabled===false);
  assert.match(await page.locator('#chatRows').innerText(),/Operator A · \+994500000111/);
  assert.equal(await page.locator('#operatorComposerSourceSelect').count(),0);
  const leave=page.locator('[data-operator-lifecycle="leave_group"]');
  await leave.click();
  await join.waitFor();
  assert.equal(await page.locator('#chatText').isDisabled(),true);
  await join.click();
  await leave.waitFor();
  await page.evaluate(()=>window.makeLead());
  await page.locator('[data-operator-lifecycle="close"]').click();
  const warning=page.locator('#operatorLifecycleModal');
  await warning.waitFor();
  const bounds=await warning.locator('.operator-complete-card').boundingBox();
  assert(bounds.x>=0&&bounds.x+bounds.width<=page.viewportSize().width+1);
  await warning.locator('[data-group-transfer]').click();
  await warning.waitFor({state:'detached'});
  const sent=await page.evaluate(()=>window.groupRequests);
  assert.deepEqual(sent.map(cmd=>cmd.action),['join_group','leave_group','join_group','transfer_group']);
  assert(sent.every(cmd=>cmd.channel_id==='c'&&cmd.lead_id===43&&cmd.expected_group_episode_id===sent[0].expected_group_episode_id));
  assert.equal(sent.at(-1).target_employee_id,33);
}

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
  try {
    for (const width of [1440,360]) {
      const page=await browser.newPage({viewport:{width,height:900}});
      await verify(page);
      await page.close();
    }
  } finally {
    await browser.close();
  }
  console.log('PASS desktop and mobile group join, exact source, author, leave and leader handoff');
})().catch(error=>{console.error(error);process.exit(1);});
