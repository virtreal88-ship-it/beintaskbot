const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
(async()=>{
 const dom=new JSDOM('<div id="chatRows"><div data-chat-attachment="connect:synthetic" data-connect-media="true" data-attachment-lead-id="41"><button>Open</button><span role="status"></span></div></div>',{url:'https://test.invalid/app',runScripts:'outside-only'}),w=dom.window;
 const member={tenant_id:'demo',telegram_id:1};let downloads=0;
 w.Blob=Blob;w.AbortController=AbortController;w.URL.createObjectURL=()=> 'blob:synthetic';w.URL.revokeObjectURL=()=>{};
 w.HTMLMediaElement.prototype.pause=function(){};
 w.fetch=async url=>{if(String(url).includes('/me'))return {ok:true,json:async()=>({member})};downloads++;return new Response(new Uint8Array([0,0,0,1]),{headers:{'Content-Type':'video/mp4'}});};
 w.eval(fs.readFileSync('docs/tenant-chat-attachments.js','utf8'));
 for(let i=0;i<10;i++)await new Promise(r=>setImmediate(r));
 w.document.dispatchEvent(new w.CustomEvent('tenant-chat-open',{detail:{lead_id:41}}));
 w.document.dispatchEvent(new w.CustomEvent('tenant-chat-attachments-ready'));
 assert.equal(downloads,0);
 await w.document.querySelector('button').onclick();
 const video=w.document.querySelector('video');assert.ok(video);assert.equal(video.controls,true);assert.equal(video.autoplay,false);assert.equal(video.src,'blob:synthetic');
 w.document.dispatchEvent(new w.CustomEvent('tenant-chat-close'));assert.equal(w.document.querySelector('video'),null);
 dom.window.close();console.log('PASS: protected lazy video element, controls, no autoplay, cleanup');
})().catch(error=>{console.error(error);process.exitCode=1;});
