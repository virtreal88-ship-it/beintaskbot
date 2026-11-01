const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=p=>fs.readFileSync(path.join(__dirname,'..',p),'utf8');
const settle=async()=>{for(let i=0;i<25;i++)await new Promise(r=>setImmediate(r));};
(async()=>{for(const lang of ['ru','az','en']){
 const dom=new JSDOM('<div id="view-settings"></div>',{url:'https://crm.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 w.tenantI18n={language:lang};w.fetch=async(url,opts={})=>{assert(!opts.body,'read-only settings test');calls.push(url);return {ok:true,json:async()=>url==='/api/platform/me'?{member:{tenant_id:'a',telegram_id:11,role:'owner'}}:{success:true,configured:true,enabled:true,consent:true,audio_enabled:true,copilot_enabled:true,daily_limit:50,copilot_daily_limit:50,updated_at:'v1',photo_model:'gpt-4.1',video_model:'gpt-4o-mini',video_enabled:true}};};
 w.eval(read('docs/tenant-view-demand.js'));d.dispatchEvent(new w.CustomEvent('tenant-integration-panel',{detail:{provider:'openai',open:true}}));w.eval(read('docs/tenant-chat-ai-settings.js'));await settle();
 assert.equal(d.querySelectorAll('#tcAiPanel select').length,0);assert.equal(d.getElementById('tcAiKey').value,'');assert.match(d.getElementById('tcAiPanel').textContent,lang==='ru'?/агента/:lang==='az'?/agent/:/agent/);
 d.getElementById('tcAiKey').value='UNSAVED_PRIVATE_KEY';d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'b',telegram_id:22,role:'admin'}}}));await settle();assert.equal(d.getElementById('tcAiKey').value,'');assert(!d.body.textContent.includes('UNSAVED_PRIVATE_KEY'));
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'b',telegram_id:22,role:'employee'}}}));await settle();assert.equal(d.getElementById('tcAiPanel'),null);assert(calls.every(url=>url==='/api/platform/me'||url==='/api/platform/chat/ai/settings'));
 dom.window.close();}console.log('PASS RU/AZ/EN company access panel: model-free UI, credential/tenant reset, role boundary and no paid calls.');})().catch(error=>{console.error(error);process.exitCode=1;});
