const fs = require('node:fs');
const assert = require('node:assert/strict');
const {JSDOM} = require('jsdom');
const {webcrypto} = require('node:crypto');
const read = name => fs.readFileSync('docs/'+name,'utf8');
const tick = () => new Promise(done=>setTimeout(done,5));
const wait = async predicate => {
  for(let n=0;n<120;n++){if(predicate())return; await tick();}
  assert.fail('UI state did not settle');
};
(async()=>{
  const dom = new JSDOM(read('tenant-app.html'),{url:'https://test.invalid/app?view=dialogs',runScripts:'outside-only'});
  const w=dom.window,d=w.document;
  Object.defineProperty(w,'crypto',{value:webcrypto}); w.TextEncoder=TextEncoder;
  w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
  const profile={member:{tenant_id:'demo',telegram_id:11,role:'owner',active:true},
    capabilities:{operator_widget:true,modules:{dialogs:true}}};
  w.localStorage.setItem('tenant-app-language','ru');
  w.tenantProfileSnapshot=profile;
  const site={provider:'operator_widget',channel_id:'instance',conversation_id:'verified-route',lead_id:15,
    channel_name:'Чат сайта BeinSystems',customer_name:'Widget Test',customer_kind:'contact',
    widget_revision:'a'.repeat(64),available_actions:[],can_send:false,lifecycle_enabled:true,
    lifecycle:{state:'waiting',revision:1,last_inbound_id:'2',can_accept:true}};
  const calls=[];
  w.fetch=async(url,opt={})=>{
    url=String(url);const data=opt.body&&JSON.parse(opt.body);
    calls.push({url,data});let result;
    if(url.endsWith('/me'))return {ok:true,json:async()=>profile};
    if(url.includes('dialogs_lifecycle')){
      site.lifecycle={state:'accepted',operator_id:11,revision:2,can_close:true,can_dismiss:true,last_inbound_id:'2'};
      site.can_add_internal_comment=true;site.send_block_reason='widget_socket_unavailable';
      result={lifecycle:site.lifecycle};
    }else if(url.includes('dialogs/history'))result={dialog:{...site},messages:[{id:'2',body:'test',direction:'incoming'}]};
    else if(url.includes('dialogs?'))result={dialogs:[site],channels:[{id:'operator_widget:instance',name:site.channel_name}],agents:[],total_count:1};
    else if(url.includes('dialogs/read'))result={updated:true};
    else if(url.includes('dialogs/internal-comments'))result={comment:{id:'n',note:data.note,at:new Date().toISOString(),employee_name:'Tester'}};
    else if(url.endsWith('/operator/widget'))return {ok:true,status:200,json:async()=>({success:true,state:'saved'})};
    else throw Error('Unexpected '+url);
    return {ok:true,status:200,json:async()=>({success:true,result})};
  };
  for(const f of ['tenant-operator-i18n.js','tenant-dialog-source-tabs.js','tenant-operator-dialogs.js'])w.eval(read(f));
  await tick();await tick();await w.tenantOperator.openDialog(site);
  const selected=()=>d.querySelector('.dialog-source-tabs [aria-pressed=true]');
  assert.deepEqual([...d.querySelectorAll('.dialog-source-tabs button')].map(b=>b.textContent),['Чат','WhatsApp','Комментарий']);
  assert.equal(selected().textContent,'Чат');
  d.querySelector('[data-operator-lifecycle=accept]').click();
  await wait(()=>d.querySelector('.dialog-source-tabs button:last-child')?.disabled===false);
  assert.equal(selected().textContent,'Чат');
  assert(d.getElementById('sendChat').disabled);
  assert(d.getElementById('operatorSendReason').textContent.includes('авторизац'));
  d.querySelector('.dialog-source-tabs button:last-child').click();
  const field=d.querySelector('.dialog-note-form textarea');field.value='staff only';field.dispatchEvent(new w.Event('input'));
  d.querySelector('.dialog-note-form').dispatchEvent(new w.Event('submit',{cancelable:true}));
  await wait(()=>calls.some(c=>c.url.includes('internal-comments')));
  assert(!calls.some(c=>c.url.endsWith('/operator/widget')||c.url.includes('/crm/chat/send')));
  site.can_send=true;site.send_block_reason='';
  await w.tenantOperator.openDialog(site);
  d.querySelector('.dialog-source-tabs button').click();
  const input=d.getElementById('chatText');input.value='test reply';
  d.getElementById('sendChat').click();
  await wait(()=>calls.some(c=>c.url.endsWith('/operator/widget')));
  const reply=calls.find(c=>c.url.endsWith('/operator/widget')).data;
  assert.deepEqual([reply.chat_id,reply.text,reply.revision,reply.action],['15','test reply',site.widget_revision,'reply']);
  assert(!calls.some(c=>c.url.includes('/crm/chat/send')));
  dom.window.close();
  console.log('Widget uses the regular conversation, keeps its source on acceptance, isolates notes and addresses only the selected chat.');
})().catch(err=>{console.error(err);process.exit(1)});
