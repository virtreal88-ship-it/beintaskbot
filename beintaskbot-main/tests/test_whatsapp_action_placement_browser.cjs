/* Real Chromium placement and focus behavior with synthetic chat messages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '../.support-test-deps/node_modules/playwright');
const dialogs = fs.readFileSync(path.resolve(__dirname,'../docs/tenant-operator-dialogs.js'),'utf8');
const copies = dialogs.slice(dialogs.indexOf('  function reconcileGroupCopies('),dialogs.indexOf('  async function appendNewMessages('));

async function verify(browser, width) {
  const page = await browser.newPage({viewport:{width,height:720},isMobile:width<600,hasTouch:width<600});
  try {
    await page.setContent(`<meta name="viewport" content="width=device-width, initial-scale=1">
      <style>body{margin:0} article{position:absolute;right:30px;top:120px;width:180px;height:54px}</style>
      <article id="message" tabindex="-1">Sample WhatsApp message</article>`);
    await page.addScriptTag({path:path.resolve(__dirname,'../docs/tenant-whatsapp-message-actions.js')});
    await page.evaluate(() => { window.tenantI18n = {language:'en'}; });
    async function open(action) {
      await page.evaluate(action => {
        const route = {wecall:true,can_send:true,channel_id:'channel',conversation_id:'group',lead_id:1};
        const until = new Date(Date.now()+60000).toISOString();
        const message = {id:'row',source_channel_id:'channel',source_conversation_id:'group',source_lead_id:1,
          body:'Sample WhatsApp message',direction:'outgoing',message_type:'text',wa_edit_version:'version',
          wa_edit_until:until,wa_delete_version:'version',wa_delete_until:until};
        window.tenantWhatsAppMessageActions[action]({member:{tenant_id:'tenant',telegram_id:1},route,message,
          isCurrent:()=>true,anchor:()=>document.getElementById('message')});
      }, action);
      return page.getByRole('dialog');
    }
    const message = page.locator('#message');
    const edit = await open('openEdit');
    assert(await edit.isVisible());
    const box = await edit.boundingBox(), anchor = await message.boundingBox();
    assert.equal(box.y,anchor.y+anchor.height+8);
    assert(box.x>=12 && box.x+box.width<=width-12);
    assert(await edit.getByRole('textbox').evaluate(el=>el===document.activeElement));
    await page.keyboard.press('Escape');
    await edit.waitFor({state:'detached'});
    await message.evaluate(el=>{el.style.top='630px';});
    const deletion = await open('openDelete');
    assert(await deletion.isVisible());
    const beforeResize = await deletion.boundingBox(), moved = await message.boundingBox();
    assert.equal(beforeResize.y+beforeResize.height,moved.y-8);
    await page.setViewportSize({width,height:685});
    assert((await deletion.boundingBox()).y >= 12);
    await page.keyboard.press('Escape');
    await deletion.waitFor({state:'detached'});
    await page.addScriptTag({content:copies + `
      function messageMarkup([message]) {
        return '<article class="operator-message" data-message-id="'+message.id+'"></article>';
      }`});
    const merged = await page.evaluate(() => {
      const chat = document.createElement('section');
      chat.innerHTML = messageMarkup([{id:'peer'}]) + messageMarkup([{id:'unrelated'}]);
      const peer = {id:'peer',source_channel_id:'B',source_lead_id:2,external_id:'connect:seen'};
      const unrelated = {...peer,id:'unrelated',source_channel_id:'C'};
      const canonical = {id:'own',source_channel_id:'A',source_lead_id:1,external_id:'connect:sent',
        sources:[{channel_id:'A',lead_id:1,external_id:'connect:sent'},{channel_id:'B',lead_id:2,external_id:'connect:seen'}]};
      const current = {messages:[peer,unrelated]};
      const latest = {messages:[canonical]};
      reconcileGroupCopies(current,latest,chat,{});
      reconcileGroupCopies(current,latest,chat,{});
      return {model:current.messages.map(row=>row.id), render:[...chat.children].map(row=>row.dataset.messageId)};
    });
    assert.deepEqual(merged,{model:['own','unrelated'],render:['own','unrelated']});
  } finally { await page.close(); }
}

(async()=>{
  const browser = await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH || undefined});
  try {
    for (const width of [360,1280]) await verify(browser,width);
    console.log('PASS: message-action placement and live group-copy reconciliation');
  } finally { await browser.close(); }
})().catch(error=>{ console.error(error); process.exitCode=1; });
