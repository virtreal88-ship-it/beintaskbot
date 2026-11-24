/* The production conversation composer in Chromium; all requests use fixtures. */
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('../.support-test-deps/node_modules/playwright');
const {docs, assets, appHtml, installNotificationCaseFixture} = require('./notification_case_targets_fixture.cjs');

const asset = 'tenant-dialog-quick-replies.js';
assets.push(asset, 'tenant-dialog-composer.js');
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/app') {
    res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
    return res.end(appHtml());
  }
  const name = path.basename(url.pathname);
  if (url.pathname.startsWith('/assets/') && assets.includes(name)) {
    res.writeHead(200, {'Content-Type':'text/javascript; charset=utf-8'});
    return res.end(fs.readFileSync(path.join(docs, name)));
  }
  res.writeHead(404);
  res.end();
});

function installQuickReplyFixture() {
  const f = window.notificationCaseFixture, base = window.fetch;
  const second = {...f.route, channel_id:'10000000-0000-4000-8000-000000000202',
    channel_name:'Secondary WhatsApp', customer_name:'Second customer', conversation_id:'second-client', lead_id:42};
  f.quickSearches = [];
  f.quickSends = [];
  f.waiting = [];
  let dismissed = false;
  const reply = (result) => new Response(JSON.stringify({success:true,result}), {
    headers:{'Content-Type':'application/json'}
  });
  window.fetch = async (input, options={}) => {
    const url = new URL(String(input), location);
    if (url.pathname.endsWith('/dialogs')) return reply({dialogs:[f.route,second],channels:[],agents:[],next_offset:null});
    if (url.pathname.endsWith('/dialogs/history')) {
      return reply({dialog:url.searchParams.get('channel_id')===second.channel_id?second:f.route,messages:[],next_before:null});
    }
    if (url.pathname.endsWith('/dialogs/quick-replies')) {
      const body = JSON.parse(options.body);
      f.quickSearches.push({body,method:options.method,query:url.search});
      if (body.action === 'dismiss') dismissed = true;
      if (body.prefix === 'Поздний') {
        return await new Promise(resolve=>f.waiting.push(()=>resolve(reply({
          items:[{id:'late',body:'Поздний ответ для другого диалога',uses:1}]
        }))));
      }
      const items = body.channel_id===f.channel&&body.prefix==='Добры'
        ? [...(dismissed?[]:[{id:'day',body:'Добрый день!',uses:3}]),{id:'evening',body:'Добрый вечер. <literal>',uses:1}]
        : [];
      return reply({items});
    }
    if (options.method==='POST'&&/\/chat.*send/.test(url.pathname)) {
      f.quickSends.push(url.pathname);
      throw new Error('A quick reply must not send');
    }
    return base(input,options);
  };
}

(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin = 'http://127.0.0.1:'+server.address().port;
  const browser = await chromium.launch();
  try {
    for (const width of [390,1440]) {
      const context = await browser.newContext({viewport:{width,height:844},isMobile:width===390,hasTouch:width===390});
      try {
        const page = await context.newPage(),errors=[];
        page.on('pageerror',error=>errors.push(error.message));
        await page.route('**/*', route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
        await page.addInitScript('('+installNotificationCaseFixture.toString()+')();('+installQuickReplyFixture.toString()+')();');
        await page.goto(origin+'/app?view=dialogs');
        await page.locator('.operator-row').filter({hasText:'Synthetic customer'}).click();
        const textbox = page.locator('#chatText'), panel = page.locator('#operatorQuickReplies');
        await textbox.fill('Добры');
        await panel.getByRole('button',{name:'Добрый день!',exact:true}).waitFor();
        assert.equal(await panel.locator('literal').count(),0);
        assert.ok((await panel.boundingBox()).y < (await textbox.boundingBox()).y);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
        const hide = panel.locator('.operator-quick-reply-hide').first();
        await (width===390?hide.tap():hide.click());
        await panel.getByRole('button',{name:'Добрый день!',exact:true}).waitFor({state:'detached'});
        assert.equal(await textbox.inputValue(),'Добры');
        const evening = panel.getByRole('button',{name:'Добрый вечер. <literal>',exact:true});
        if (width===390) await evening.tap();
        else {
          await textbox.press('End');
          await textbox.press('ArrowDown');
          assert.equal(await evening.evaluate(node=>node===document.activeElement),true);
          await evening.press('Enter');
        }
        assert.equal(await textbox.inputValue(),'Добрый вечер. <literal>');
        assert.equal(await panel.isHidden(),true);
        await textbox.press('End');
        await page.keyboard.insertText(' Уточнение');
        assert.ok((await textbox.inputValue()).endsWith(' Уточнение'));
        await textbox.fill('Поздний');
        await page.waitForFunction(()=>notificationCaseFixture.waiting.length===1);
        if(width===390)await page.locator('[data-operator-back]').click();
        await page.locator('.operator-row').filter({hasText:'Second customer'}).click();
        await textbox.fill('Добры');
        await page.waitForFunction(()=>notificationCaseFixture.quickSearches.some(r=>r.body.lead_id===42));
        await page.evaluate(()=>notificationCaseFixture.waiting.shift()());
        assert.equal(await panel.isHidden(),true);
        const requests = await page.evaluate(()=>({items:notificationCaseFixture.quickSearches,sends:notificationCaseFixture.quickSends}));
        assert.ok(requests.items.every(r=>r.method==='POST'&&!r.query&&r.body.expected_user_id==='11'));
        assert.equal(requests.items.filter(r=>r.body.action==='dismiss').length,1);
        assert.deepEqual(requests.sends,[]);
        assert.deepEqual(errors,[]);
        console.log('PASS quick replies in Chromium at '+width+' px');
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
