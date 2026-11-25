const assert = require('node:assert/strict');
const fs = require('node:fs');
const {webcrypto} = require('node:crypto');
const {JSDOM} = require('jsdom');

(async () => {
  const dom = new JSDOM('<section id="view-dialogs" class="active"><div id="operatorWidget" hidden></div><div id="operatorDialogs"></div></section>',
    {url:'https://test.invalid/app', runScripts:'outside-only', pretendToBeVisual:true});
  const {window} = dom, {document} = window;
  Object.defineProperty(window,'crypto',{value:webcrypto});
  window.TextEncoder = TextEncoder;
  window.tenantI18n = {language:'ru'};
  const profile = {member:{tenant_id:'A',telegram_id:11},capabilities:{operator_widget:true}};
  const chat = {id:'5', name:'Demo <script>', revision:'a'.repeat(64), status:'open', can_reply:true,
    operator_url:'https://operator.beinsystems.az/chats?clientId=demo'};
  let hasOlder = false;
  let calls = [], history = [{id:'1',text:'client message',sender:'client'}], late;
  const event = (name,detail) => document.dispatchEvent(new window.CustomEvent(name,{detail}));
  const wait = async predicate => {
    for (let i=0;i<100;i++) {if(predicate())return; await new Promise(r=>setTimeout(r,5));}
    assert.fail('Timed out waiting for UI');
  };
  window.fetch = async (_, config) => {
    const body = JSON.parse(config.body);
    calls.push(body);
    assert.equal(body.expected_tenant_id, 'A');
    if (late && body.action === 'history') await late.promise;
    const payload = body.action === 'list' ? {chats:[chat]} : body.action === 'reply' ?
      {state:'unknown'} : {chat, messages:body.action === 'older' ? [{id:'99',sender:'client',text:'older'}] : history,has_older:body.action === 'older' ? false : hasOlder};
    return {ok:true,status:200,json:async()=>({success:true,...payload})};
  };
  window.eval(fs.readFileSync('docs/tenant-operator-widget.js','utf8'));
  event('tenant-profile',{member:profile.member,capabilities:{}});
  assert.equal(calls.length,0);
  event('tenant-profile',profile);
  const root = document.getElementById('operatorWidget');
  const find = (selector,text) => [...root.querySelectorAll(selector)].find(n=>n.textContent===text);
  const open = async () => {
    root.querySelector('summary').click();
    await wait(()=>find('button',chat.name));
    find('button',chat.name).click();
    await wait(()=>root.querySelector('article') && !root.querySelector('form').hidden);
  };
  await open();
  assert.equal(root.querySelector('script'),null);
  async function send() {
    root.querySelector('textarea').value='same reply';
    root.querySelector('form').dispatchEvent(new window.Event('submit',{cancelable:true}));
    await wait(()=>root.querySelector('[role=status]').textContent.includes('неизвестен') && !root.querySelector('textarea').disabled);
  }
  await send();
  const first = calls.find(c=>c.action==='reply').request_id;
  event('tenant-auth-invalid',{});
  assert.ok(root.hidden);
  event('tenant-profile',profile);
  await open();
  await send();
  assert.deepEqual(calls.filter(c=>c.action==='reply').map(c=>c.request_id),[first,first]);
  chat.can_reply = false; chat.reply_in_operator = true;
  find('button','Обновить').click();
  await wait(()=>find('a','Ответить в Operator'));
  assert.ok(root.querySelector('form').hidden);
  assert.equal(root.querySelector('a').href, chat.operator_url);
  history = [{id:'100',sender:'client',text:'new page after an outage'}]; hasOlder = true;
  find('button','Обновить').click();
  await wait(()=>root.textContent.includes('new page after an outage'));
  assert.ok(!root.textContent.includes('client message'));
  find('button','Предыдущие сообщения').click();
  await wait(()=>calls.some(c=>c.action==='older' && c.before_id==='100') && root.textContent.includes('older'));
  let release;
  late={promise:new Promise(r=>{release=r;})};
  const before = calls.length;
  find('button','Обновить').click();
  await wait(()=>calls.length>before);
  event('tenant-auth-invalid',{});
  release();
  await new Promise(r=>setTimeout(r,10));
  assert.equal(root.textContent,'');
  assert.ok(root.hidden);
  dom.window.close();
  console.log('Widget UI passed: permission, safe rendering, reply retry identity, stale response isolation');
})().catch(err=>{console.error(err);process.exit(1);});
