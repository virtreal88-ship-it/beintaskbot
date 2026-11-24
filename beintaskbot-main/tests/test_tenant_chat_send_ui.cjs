const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('../.support-test-deps/node_modules/jsdom');
const source = fs.readFileSync('docs/tenant-chat-send.js', 'utf8');
const page = fs.readFileSync('docs/tenant-app.html', 'utf8');

async function withComposer(run) {
  const dom = new JSDOM('<div><textarea id="chatText"></textarea><button id="sendChat">Göndər</button></div>', {
    url: 'https://crm.test/app',
    runScripts: 'outside-only'
  });
  const w = dom.window;
  const input = w.document.getElementById('chatText');
  const button = w.document.getElementById('sendChat');
  const calls = [];
  const key = 'tenant-chat-send:tenant:20:7';
  const env = {uuids:0, current:true, result:async()=>({state:'accepted'})};
  w.tenantI18n = {language:'az', t:()=>undefined};
  w.alert = ()=>{};
  w.crypto.randomUUID = ()=>{env.uuids++;return 'request-id';};
  w.eval(source);
  w.tenantChatSend.bind({
    api:async(path, options)=>{
      calls.push({path, method:options.method, body:options.body && JSON.parse(options.body)});
      return env.result();
    },
    me:{tenant_id:'tenant',telegram_id:20},
    identity:'tenant:20',
    leadId:7,
    isCurrent:()=>env.current,
    onAccepted:text=>{env.accepted=text;},
    reload:async()=>{}
  });
  input.value='Salam';
  try{
    await run({env,input,button,calls,pending:()=>w.sessionStorage.getItem(key),click:()=>button.onclick()});
  }finally{
    w.close();
  }
}

(async()=>{
  await withComposer(async({click,calls,pending,input,env})=>{
    await click();
    assert.equal(calls.length,1);
    assert.equal(calls[0].method,'POST');
    assert.equal(calls[0].body.expected_user_id,20);
    assert.equal(calls[0].body.expected_tenant_id,'tenant');
    assert.equal(pending(),null);
    assert.equal(input.value,'');
    assert.equal(env.accepted,'Salam');
  });
  await withComposer(async({click,calls,pending,input,button,env})=>{
    env.result=async()=>{throw new Error('network lost');};
    await click();
    assert.ok(pending());
    assert.equal(input.value,'Salam');
    assert.equal(button.textContent,'Vəziyyəti yoxla');
    input.value='Another message';
    env.result=async()=>({state:'unknown'});
    await click();
    assert.ok(pending(),'an unconfirmed request remains blocked');
    assert.equal(input.value,'Another message');
    env.result=async()=>({state:'accepted'});
    await click();
    assert.equal(pending(),null);
    assert.equal(input.value,'Another message','checking preserves the replacement draft');
    assert.deepEqual(calls.map(call=>call.method),['POST','GET','GET']);
    for(const call of calls.slice(1)){
      const query=new URL(call.path,'https://crm.test').searchParams;
      assert.equal(query.get('request_id'),calls[0].body.request_id);
      assert.equal(query.get('expected_user_id'),'20');
      assert.equal(query.get('expected_tenant_id'),'tenant');
      assert.equal(call.body,undefined);
    }
    assert.equal(env.uuids,1);
  });
  await withComposer(async({click,pending,env})=>{
    env.result=async()=>{throw Object.assign(new Error('rejected'),{keepRequest:false});};
    await click();
    assert.equal(pending(),null);
  });
  await withComposer(async({click,pending,input,env})=>{
    env.result=async()=>{env.current=false;input.value='New draft';return {state:'accepted'};};
    await click();
    assert.equal(input.value,'New draft');
    assert.equal(env.accepted,undefined);
    assert.equal(pending(),null);
  });
  assert.ok(page.includes('/assets/tenant-chat-send.js'));
  console.log('PASS: exact GET recovery, no duplicate POST, draft preservation and scoped late responses.');
})().catch(error=>{console.error(error);process.exitCode=1;});
