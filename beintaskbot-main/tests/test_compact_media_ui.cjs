const assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const source=fs.readFileSync('docs/tenant-chat-attachments.js','utf8');
async function check(mime,kind,selector){
 const dom=new JSDOM('<div id="chatRows"><div data-chat-attachment="event-42" data-attachment-lead-id="902" data-connect-media="true"><button>Load</button><span role="status"></span><span data-media-meta></span></div></div>',{url:'https://test.invalid/app',runScripts:'outside-only'}),w=dom.window;
 const host=w.document.querySelector('[data-chat-attachment]');host.dataset.mediaKind=kind;let calls=0,revoked=0;
 w.Blob=Blob;w.AbortController=AbortController;w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>revoked++;
 w.HTMLMediaElement.prototype.pause=function(){this.stopped=true;};
 w.fetch=async(url)=>{if(url==='/api/platform/me')return{ok:true,json:async()=>({member:{tenant_id:'tenant',telegram_id:4}})};calls++;const query=new URL(url,'https://test.invalid').searchParams;assert.equal(query.get('lead_id'),'902');assert.equal(query.get('message_id'),'event-42');assert.equal(query.get('expected_tenant_id'),'tenant');assert.equal(query.get('expected_user_id'),'4');let sent=false;return{ok:true,headers:new Headers({'Content-Type':mime,'Content-Length':'3'}),body:{getReader:()=>({read:async()=>sent?{done:true}:(sent=true,{value:new Uint8Array([1,2,3]),done:false}),cancel:async()=>{}})}};};
 w.eval(source);w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'tenant',telegram_id:4}}}));w.document.dispatchEvent(new w.CustomEvent('tenant-chat-open',{detail:{lead_id:901}}));w.document.dispatchEvent(new w.Event('tenant-chat-attachments-ready'));
 assert.equal(calls,0);assert.equal(host.querySelector(selector),null);await host.querySelector('button').onclick();assert.equal(calls,1);const node=host.querySelector(selector);assert.ok(node,host.textContent);assert.equal(host.querySelector('[role=status]').textContent,'');
 if(selector==='a'){assert.equal(node.href,'blob:test');assert.match(host.querySelector('[data-media-meta]').textContent,/3 B/);}
 if(selector==='video'){assert.equal(node.preload,'metadata');assert.equal(node.autoplay,false);assert.equal(node.controls,true);}
 if(selector==='audio'){assert.equal(node.preload,'none');node.play=async()=>{throw Error('unsupported codec')};await node.parentElement.querySelector('button').onclick();assert.equal(node.parentElement.querySelector('button').getAttribute('aria-label'),'Play');assert.ok(host.querySelector('[role=status]').textContent);}
 w.document.dispatchEvent(new w.Event('tenant-chat-close'));assert.equal(host.querySelector(selector),null);assert.equal(revoked,1);if(['audio','video'].includes(selector))assert.equal(node.stopped,true);w.close();
}
(async()=>{for(const row of [['image/png','image','img'],['audio/ogg','audio','audio'],['video/mp4','video','video'],['application/pdf','document','a'],['application/octet-stream','document','a']])await check(...row);console.log('PASS: compact image/audio/video/PDF/file DOM, no eager requests, source route, failed play, cleanup.');})().catch(e=>{console.error(e);process.exitCode=1});
