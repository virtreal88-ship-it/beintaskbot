/* Desktop and mobile workflow against fixture responses. No external sends. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '../.support-test-deps/node_modules/playwright');
const docs = path.resolve(__dirname, '../docs');
const html = fs.readFileSync(path.join(docs, 'tenant-app.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');

async function setup(page, {emptySecondRoute=false, archiveFirstRoute=false, sharedFirstRoute=false}={}) {
  await page.route('**/*', route => route.request().isNavigationRequest() ?
    route.fulfill({contentType:'text/html',body:html}) : route.abort());
  await page.goto('https://example.test/app?view=dialogs');
  await page.evaluate(({emptySecondRoute, archiveFirstRoute, sharedFirstRoute}) => {
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
    let active = archiveFirstRoute||sharedFirstRoute;
    let alone = false;
    let holdHistory = false;
    const acceptance = {id:'accepted',kind:'accept',employee_name:'Operator A',at:'2026-11-26T08:00:01Z'};
    const peers = [{employee_id:'33',name:'Operator A'}];
    const makeRoute = (id,lead,number) => ({provider:'connect',channel_active:true,channel_id:id,
      channel_name:'Fixture '+id,lead_id:lead,conversation_id:group,number});
    const routes = [makeRoute('b',42,'+994500000112'),makeRoute('c',43,'+994500000113')];
    function dialog(route) {
      const own = joined && route.channel_id===(sharedFirstRoute?'b':'c');
      const shared = sharedFirstRoute&&route.channel_id==='b';
      const archive = archiveFirstRoute&&route.channel_id==='b';
      const hasInbound = !emptySecondRoute || route.channel_id!=='c' || own;
      return {...route,customer_name:'Fixture group',customer_kind:'group',lifecycle_enabled:true,
        group_work_revision:`${active?episode:'waiting'}:${revision}`,
        archive_read_only:archive,can_send:own,
        lifecycle:{state:own||shared?'accepted':archive?'closed':'waiting',operator_id:own?44:archive||shared?33:null,has_inbound:hasInbound,
          id:own||shared?episode:null,revision,can_close:own&&alone,
          ...(active?{group_work:{id:episode,role:own?(leader?'leader':'participant'):null,can_join:!joined&&!archive,
            can_leave:own&&!alone,number:route.number,colleagues:own&&!alone?peers:[],
            ...(!joined?{sources:archiveFirstRoute?[routes[1]]:sharedFirstRoute?[routes[0]]:routes}:{})}}:
            {group_available:true,can_accept:hasInbound,last_inbound_id:hasInbound?'fixture-inbound':null})}};
    }
    window.groupRequests=[];
    window.otherOperatorAccepted=()=>{active=true;};
    window.otherOperatorLeft=()=>{alone=leader=true;revision++;};
    window.makeLead=()=>{leader=true;return window.tenantOperator.openDialog(dialog(routes[1]),true);};
    window.fetch=async (input, options={}) => {
      const url=new URL(String(input),location.origin);
      if(url.pathname.endsWith('/me'))return {ok:true,json:async()=>window.tenantProfileSnapshot};
      let result;
      if(url.pathname.endsWith('/dialogs/history')){
        const view=dialog(routes.find(route=>route.channel_id===url.searchParams.get('channel_id')));
        if(url.searchParams.get('group_work_revision')==='1')result={revision:view.group_work_revision};
        else{
          if(holdHistory)await new Promise(resolve=>{window.finishGroupHistory=()=>{holdHistory=false;resolve();};});
          result={dialog:view,operator_events:active?[acceptance]:[],
            messages:joined?[{id:'first',external_id:'first',direction:'incoming',source:'connect',message_type:'text',
              body:'Fixture reply',author_type:'employee',author_name:'Operator A',sending_number:'+994500000111',
              created_at:'2026-11-26T08:00:00Z'}]:[],read_through_message_id:null,next_before:null};
        }
      }else if(url.pathname.endsWith('/dialogs_lifecycle')){
        const command=JSON.parse(options.body);
        window.groupRequests.push(command);
        if(command.action==='accept'){
          await new Promise(resolve=>{window.confirmGroupAcceptance=resolve;});
          active=joined=leader=holdHistory=true;
        }
        revision++;
        if(command.action==='join_group')joined=true;
        if(command.action==='leave_group'||command.action==='close')joined=false;
        if(command.action==='transfer_group')leader=false;
        const view=dialog(sharedFirstRoute?routes[0]:routes[1]);
        result={group_changed:true,lifecycle:view.lifecycle};
        if(command.action==='accept'){
          result.operator_event={...acceptance,employee_name:'Operator B'};
          result.controls={can_send:view.can_send,lifecycle:view.lifecycle,group_work_revision:view.group_work_revision};
        }
      }else if(url.pathname.endsWith('/dialogs')){
        const sources=joined?[dialog(sharedFirstRoute?routes[0]:routes[1])]:sharedFirstRoute?[dialog(routes[0])]:routes.map(dialog);
        result={dialogs:[{...sources[0],group_sources:sources,unread_count:2}],channels:[],agents:[],total_count:1};
      }else{
        result={};
      }
      return {ok:true,status:200,json:async()=>({success:true,result})};
    };
  }, {emptySecondRoute, archiveFirstRoute, sharedFirstRoute});
  for (const asset of ['tenant-operator-i18n.js','tenant-operator-dialogs.js']) {
    await page.addScriptTag({path:path.join(docs,asset)});
  }
  await page.evaluate(()=>window.tenantOperator.refresh());
}

async function verify(page) {
  await setup(page);
  const card=page.locator('.operator-row');
  assert.equal(await card.count(),1);
  assert.equal(await card.locator('.operator-unread-count').textContent(),'2');
  await card.click();
  await page.locator('[data-operator-lifecycle="accept"]').waitFor();
  await page.evaluate(()=>window.otherOperatorAccepted());
  const join=page.locator('[data-operator-lifecycle="join_group"]');
  await join.waitFor({timeout:8000});
  assert.match(await page.locator('[data-operator-timeline-event="accepted"]').innerText(),/Operator A/);
  assert.equal(await page.locator('#chatText').isDisabled(),true);
  assert.deepEqual(await page.evaluate(()=>window.groupRequests),[]);
  await page.locator('#operatorComposerSourceSelect').selectOption('1');
  await page.waitForFunction(()=>window.tenantOperator.currentDialog?.channel_id==='c');
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
  assert.equal(await page.locator('[data-operator-lifecycle="close"]').count(),0);
  assert.equal(await leave.count(),1);
  await page.locator('[data-operator-lifecycle="transfer_group"]').click();
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
  await page.evaluate(()=>window.otherOperatorLeft());
  await page.locator('[data-operator-lifecycle="close"]').waitFor({timeout:8000});
  assert.equal(await leave.count(),0);
}

async function verifyJoinFromArchive(page) {
  await setup(page,{archiveFirstRoute:true});
  await page.evaluate(async()=>{
    const card=document.querySelector('.operator-row');
    card.click();
  });
  const join=page.locator('[data-group-join-source]');
  await join.waitFor();
  assert.match(await join.innerText(),/994500000113/);
  assert.equal(await page.locator('#chatText').isDisabled(),true);
  await join.click();
  await page.waitForFunction(()=>window.tenantOperator.currentDialog?.lifecycle.group_work?.role==='participant');
  const commands=await page.evaluate(()=>window.groupRequests);
  assert.equal(commands.length,1);
  assert.equal(commands[0].action,'join_group');
  assert.equal(commands[0].channel_id,'c');
  assert.equal(commands[0].lead_id,43);
  assert.equal(await page.locator('#chatText').isDisabled(),false);
}

async function verifyJoinSharedNumber(page) {
  await setup(page,{sharedFirstRoute:true});
  await page.locator('.operator-row').click();
  const join=page.locator('[data-operator-lifecycle="join_group"]');
  await join.waitFor();
  assert.equal(await page.locator('#chatText').isDisabled(),true);
  await join.click();
  await page.waitForFunction(()=>window.tenantOperator.currentDialog?.lifecycle.group_work?.role==='participant');
  const [command]=await page.evaluate(()=>window.groupRequests);
  assert.equal(command.action,'join_group');
  assert.equal(command.channel_id,'b');
  assert.equal(command.lead_id,42);
  assert.equal(await page.locator('#chatText').isDisabled(),false);
}

async function verifyAcceptanceReceipt(page) {
  await setup(page);
  await page.locator('.operator-row').click();
  await page.evaluate(async()=>{
    const route=window.tenantOperator.currentDialog;
    await window.tenantOperator.openDialog({...route,channel_id:'c',lead_id:43},true);
  });
  const accept=page.locator('[data-operator-lifecycle="accept"]');
  await accept.click();
  await page.waitForFunction(()=>typeof window.confirmGroupAcceptance==='function');
  assert.equal(await accept.getAttribute('aria-busy'),'true');
  assert.equal(await page.locator('#chatText').isDisabled(),true);
  await page.evaluate(()=>window.confirmGroupAcceptance());
  await page.waitForFunction(()=>typeof window.finishGroupHistory==='function');
  assert.match(await page.locator('[data-operator-timeline-event="accepted"]').innerText(),/Operator B/);
  assert.equal(await page.locator('#chatText').isDisabled(),false);
  await page.evaluate(()=>window.finishGroupHistory());
}

async function verifyBackgroundAcceptance(page) {
  await setup(page,{emptySecondRoute:true});
  await page.locator('.operator-row').click();
  await page.evaluate(async()=>{
    const route=window.tenantOperator.currentDialog;
    await window.tenantOperator.openDialog({...route,channel_id:'c',lead_id:43},true);
    let hidden=true;
    Object.defineProperty(document,'hidden',{get:()=>hidden});
    document.dispatchEvent(new Event('visibilitychange'));
    window.otherOperatorAccepted();
    // Focus can return to another window without a fresh visibilitychange event.
    hidden=false;
    window.dispatchEvent(new Event('focus'));
  });
  await page.locator('[data-operator-lifecycle="join_group"]').waitFor({timeout:2000});
  assert.equal(await page.evaluate(()=>window.tenantOperator.currentDialog.channel_id),'c');
  assert.match(await page.locator('[data-operator-timeline-event="accepted"]').innerText(),/Operator A/);
  assert.deepEqual(await page.evaluate(()=>window.groupRequests),[]);
}

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
  try {
    for (const width of [1440,360]) {
      const page=await browser.newPage({viewport:{width,height:900}});
      await verify(page);
      await page.close();
      const receipt=await browser.newPage({viewport:{width,height:900}});
      await verifyAcceptanceReceipt(receipt);
      await receipt.close();
      const background=await browser.newPage({viewport:{width,height:900}});
      await verifyBackgroundAcceptance(background);
      await background.close();
      const archive=await browser.newPage({viewport:{width,height:900}});
      await verifyJoinFromArchive(archive);
      await archive.close();
      const shared=await browser.newPage({viewport:{width,height:900}});
      await verifyJoinSharedNumber(shared);
      await shared.close();
    }
  } finally {
    await browser.close();
  }
  console.log('PASS desktop and mobile group acceptance, shared-number and archive join, timeline, author, leave and handoff');
})().catch(error=>{console.error(error);process.exit(1);});
