const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '../.support-test-deps/node_modules/playwright');

const root = path.join(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'docs/tenant-app.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');

async function run(browser, group, mergeMode = null) {
  const page = await browser.newPage();
  try {
    await page.route('http://127.0.0.1/**', route => route.fulfill({
      contentType: 'text/html', body: app
    }));
    await page.goto('http://127.0.0.1/app?view=dialogs');
    await page.evaluate(({group, mergeMode}) => {
      document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
      document.getElementById('view-dialogs').classList.add('active');
      const tenant = '00000000-0000-4000-8000-000000000001';
      const channel = '00000000-0000-4000-8000-000000000002';
      window.testRoute = {
        provider:'connect', lead_id:41, channel_id:channel,
        conversation_id:group ? '15550000003@g.us' : '15550000002@s.whatsapp.net',
        customer_kind:group ? 'group' : 'contact', state:'human', can_send:true,
        controls_allowed:true, available_actions:[], wecall:true,
        ...(mergeMode?{history_merge_id:'synthetic-merge'}:{}),
        ...(mergeMode==='passive'?{owner_overview_read_only:true,can_send:false}:{})
      };
      window.tenantProfileSnapshot = {
        member:{tenant_id:tenant, telegram_id:33, role:'owner', active:true},
        capabilities:{modules:{dialogs:true,support:true}}
      };
      window.posts = [];
      window.checks = [];
      window.value = '';
      window.event = 0;
      window.peers = [];
      window.timeout = false;
      window.fetch = async (url, options={}) => {
        url = String(url);
        let payload = {success:true};
        if (url.includes('/chat/reaction')) {
          if (options.method === 'POST') {
            const body = JSON.parse(options.body);
            window.posts.push(body);
            window.value = body.emoji;
            if (window.timeout) throw new TypeError('fixture timeout');
            payload = {...payload, state:'accepted', emoji:body.emoji};
          } else {
            window.checks.push(new URL(url, location.origin).searchParams.get('request_id'));
            payload = {...payload, state:window.timeout?'unknown':'accepted', emoji:window.value};
          }
        } else if (url.includes('/dialogs/history')) {
          payload.result = {dialog:window.testRoute,messages:[
            {id:'original', external_id:'connect:original', provider_message_id:'TEST_MSG', direction:'incoming',
              author_type:group?'participant':'customer', message_type:'text', body:'Synthetic original',
              source_channel_id:channel,source_conversation_id:window.testRoute.conversation_id,source_lead_id:41,
              wa_reaction_available:true, wa_reaction:window.value, wa_reaction_event:String(window.event), wa_peer_reactions:window.peers},
            {id:'unavailable', external_id:'connect:unavailable', provider_message_id:'UNAVAILABLE_MSG',
              direction:'outgoing', author_type:'operator', message_type:'text', body:'Unavailable'},
            ...(mergeMode?[{id:'companion', external_id:'connect:companion', provider_message_id:'COMPANION_MSG',
              direction:'incoming', author_type:'customer', message_type:'text', body:'Authorized history only',
              source_channel_id:channel,source_conversation_id:'15550000002@lid',source_lead_id:42,
              wa_reaction_available:true}]:[])
          ],next_before:null};
        } else if (url.includes('/dialogs?')) {
          payload.result = {dialogs:[], channels:[], agents:[], attention_count:0, next_offset:null};
        } else if (url.includes('/api/platform/me')) {
          payload = window.tenantProfileSnapshot;
        }
        return {ok:true,json:async()=>payload};
      };
    }, {group, mergeMode});
    for (const file of ['tenant-operator-i18n.js','tenant-chat-send.js','tenant-operator-dialogs.js']) {
      await page.addScriptTag({path:path.join(root,'docs',file)});
    }
    await page.evaluate(() => window.tenantOperator.openDialog(window.testRoute));
    const article = page.locator('[data-message-id="original"]');
    const chip = article.locator('[data-whatsapp-reaction]');
    const menu = page.locator('.operator-reply-context-menu');
    if (mergeMode==='passive'){
      await article.dispatchEvent('contextmenu',{bubbles:true,cancelable:true});
      assert.equal(await menu.count(),0);
      assert.equal(await page.evaluate(()=>window.posts.length),0);
      return;
    }
    if (mergeMode==='active'){
      await page.locator('[data-message-id="companion"]').press('Shift+F10');
      assert.equal(await menu.locator('.operator-reaction-picker').count(),0);
      assert.equal(await page.evaluate(()=>window.posts.length),0);
    }
    async function pick(emoji) {
      await article.press('Shift+F10');
      await menu.locator('.operator-reaction-picker button').filter({hasText:emoji}).click();
      await menu.waitFor({state:'detached'});
    }
    await pick('👍');
    await page.waitForFunction(() => document.querySelector('[data-whatsapp-reaction]').textContent === '👍');
    await pick('😂');
    await page.waitForFunction(() => document.querySelector('[data-whatsapp-reaction]').textContent === '😂');
    await pick('😂');
    await chip.waitFor({state:'hidden'});
    assert.deepEqual(await page.evaluate(() => window.posts.map(p => p.emoji)), ['👍','😂','']);
    await page.evaluate(() => { window.timeout=true; });
    await pick('❤️');
    await page.waitForFunction(() => window.checks.length === 1);
    await page.evaluate(() => { window.timeout=false; });
    await article.press('Shift+F10');
    await menu.locator('button').filter({hasText:/Check reaction|Проверить реакцию|Reaksiyanı yoxla/}).click();
    await page.waitForFunction(() => document.querySelector('[data-whatsapp-reaction]').textContent === '❤️');
    const requests = await page.evaluate(() => ({posts:window.posts,checks:window.checks}));
    assert.equal(requests.posts.length,4);
    assert(requests.posts.every(post=>post.message_id==='original' && post.lead_id===41 && !('jid' in post)));
    assert.deepEqual(requests.checks,[requests.posts[3].request_id,requests.posts[3].request_id]);
    await page.evaluate(() => {
      window.value = '🙏';
      window.event++;
      window.peers = [{emoji:'😮',count:2}];
      // A stock WeCall dialogue with no lifecycle flag/list changes still
      // revisits its authorized history when a peer only reacts.
      return window.tenantOperator.refresh();
    });
    assert.equal(await chip.innerText(),'🙏');
    assert.match(await article.locator('.operator-peer-reaction').innerText(),/😮\s*2/);
    assert.equal(await page.evaluate(()=>window.posts.length),4,'refresh does not send a reaction');
    await page.locator('[data-message-id="unavailable"]').press('Shift+F10');
    assert.equal(await menu.locator('.operator-reaction-picker').count(),0);
  } finally {
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE_PATH||undefined,args:['--no-sandbox']});
  try{
    await run(browser,false);
    await run(browser,true);
    await run(browser,false,'active');
    await run(browser,false,'passive');
    console.log('PASS: direct and group reaction menu, replace/remove, timeout status, linked-phone sync');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode=1;});
