const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const source=fs.readFileSync(path.join(__dirname,'../docs/tenant-support-inbox.js'),'utf8');
const ticket={id:'ticket-1',contact_name:'<img src=x onerror=alert(1)>',channel_name:'Night WhatsApp',conversation_id:'client',lead_id:41,description:'Нет связи',summary:'Проверить линию',summary_state:'completed',status:'waiting',source:'ai_handoff',reason:'Не помогла перезагрузка',created_at:'2026-10-16T00:00:00Z',last_message_at:'2026-10-16T01:00:00Z'};
const profile={tenant_id:'company-a',telegram_id:11,role:'owner'};
const members=[{telegram_id:11,display_name:'Support'}];
const response=value=>({ok:true,json:async()=>({success:true,result:value})});
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
function setup(fetch){
 const dom=new JSDOM('<section id="view-ai_agents"><article id="aiAgentsOverview"></article></section><div id="modalContent"></div>',{url:'https://crm.test/app',runScripts:'outside-only'});
 dom.window.HTMLElement.prototype.scrollIntoView=function(){};
 dom.window.fetch=fetch;dom.window.eval(source);return dom;
}
(async()=>{
 const calls=[];
 const dom=setup(async(url,init={})=>{
   calls.push({url,init});
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(url.includes('?'))return response({tickets:[ticket],members,next_offset:null});
   const body=init.body?JSON.parse(init.body):{};
   if(init.method==='PATCH')Object.assign(ticket,body);
   return response({ticket,messages:[{id:'message-1',direction:'incoming',body:'Не работает интернет',message_type:'text'}],next_before:null});
 });
 await settle();const doc=dom.window.document;
 assert.match(doc.querySelector('#supportRows').textContent,/Нет связи/);
 assert.equal(doc.querySelector('#supportRows img'),null,'client names must be escaped');
 doc.querySelector('[data-support-id]').click();await settle();
 assert.match(doc.querySelector('#supportHistory').textContent,/Не работает интернет/);
 assert.equal(calls.filter(x=>x.init.method&&x.init.method!=='GET').length,0,'viewing must be read-only');
 let opened=null;doc.addEventListener('tenant-support-open',e=>opened=e.detail.lead_id);
 doc.querySelector('#supportOpenChat').click();assert.equal(opened,41);
 doc.querySelector('#supportTake').click();await settle();
 const take=calls.find(x=>x.init.method==='PATCH');const data=JSON.parse(take.init.body);
 assert.equal(data.assigned_to,11);assert.equal(data.status,'in_progress');assert.equal(data.expected_tenant_id,'company-a');
 doc.querySelector('#supportStatus').value='waiting';doc.querySelector('#supportStatus').dispatchEvent(new dom.window.Event('change'));await settle();
 assert.ok(calls.some(x=>x.url.includes('status=waiting')));
 doc.dispatchEvent(new dom.window.CustomEvent('tenant-chat-open',{detail:{lead_id:41}}));
 doc.querySelector('#supportCreateFromChat').click();await settle();
 const creation=calls.find(x=>x.init.method==='POST');assert.equal(JSON.parse(creation.init.body).lead_id,41);assert.ok(JSON.parse(creation.init.body).request_id);
 assert.ok(!calls.some(x=>/handoff|resume|pause/.test(x.url)),'queue must not change AI state');
 dom.window.close();
 // A delayed response for company A must never be rendered after switching to company B.
 let release;let company='a';
 const stale=setup(async(url)=>{
   if(url==='/api/platform/me')return{ok:true,json:async()=>({member:profile})};
   if(company==='a')return new Promise(resolve=>{release=()=>resolve(response({tickets:[ticket],members,next_offset:null}));});
   return response({tickets:[],members:[],next_offset:null});
 });
 await settle();company='b';
 stale.window.document.dispatchEvent(new stale.window.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'company-b',telegram_id:22,role:'owner'}}}));await settle();
 release();await settle();assert.ok(!stale.window.document.querySelector('#supportRows').textContent.includes('Нет связи'));assert.equal(stale.window.document.querySelector('#supportDetail').hidden,true);
 stale.window.close();
 console.log('PASS: inbox renders safely, opens history read-only, filters, assigns, creates manually and rejects stale tenant responses.');
})().catch(error=>{console.error(error);process.exitCode=1;});
