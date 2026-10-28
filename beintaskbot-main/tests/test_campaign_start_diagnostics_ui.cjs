// Synthetic requests only; tests do not contact the application or a provider.
const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const source=fs.readFileSync('docs/tenant-knowledge-campaigns.js','utf8');
(async()=>{for(const scenario of ['committed_readback','post_succeeded_get_failed','post_failed','known_validation']){
 const w=new JSDOM('<div id="cases"></div>',{url:'https://example.test/app',runScripts:'outside-only'}).window;
 w.setTimeout=()=>0;w.eval(source);let posts=0,failRead=false;
 let campaign={id:'campaign',version:1,status:'awaiting_budget',channel_ids:[],max_calls:2,max_tokens:48000,media_policy:{max_main_calls:1,max_asr_calls:0,max_vision_calls:0},cards:0};
 const ctx={lang:()=> 'en',expected:()=>({expected_tenant_id:'tenant',expected_user_id:1}),channels:[],agents:[],api:async(url,opt)=>{
   if(url.includes('/chat/ai/settings'))return {};
   if(opt&&url.endsWith('/start')){posts++;if(scenario==='known_validation'){const error=Error('Plan changed');error.status=409;throw error;}if(scenario==='post_failed')throw Error('Code: fixture');campaign={...campaign,status:'running',version:2};if(scenario==='committed_readback')return {start_committed:true,readback_pending:true,reference:'reference-123'};failRead=true;return {campaign};}
   if(failRead)throw Error('read failure');return {campaign};
 }};
 await w.TenantKnowledgeCampaigns.mount(w.document.getElementById('cases'),ctx);
 const host=w.document.getElementById('cases'),box=host.querySelector('[data-consent]');box.checked=true;box.onchange();await host.querySelector('[data-actions] button').onclick();
 const error=host.querySelector('[data-error]').textContent;
 if(scenario==='known_validation'){assert.equal(error,'Plan changed');assert.equal(posts,1);assert.equal(host.querySelector('[data-actions] button').disabled,false,'known rejection does not leave uncertain fence');w.close();continue;}
 assert.match(error,scenario==='post_failed'?/outcome is unknown/i:/start was confirmed/i);
 if(scenario==='committed_readback')assert.match(error,/reference-123/);
 const start=[...host.querySelectorAll('[data-actions] button')].find(b=>b.textContent==='Start analysis');assert(start.disabled,'cannot blindly submit again');
 const newBox=host.querySelector('[data-consent]');newBox.checked=true;newBox.onchange();assert(start.disabled,'rechecking consent cannot re-enable start');await start.onclick();assert.equal(posts,1);
 failRead=false;const refresh=[...host.querySelectorAll('[data-actions] button')].find(b=>b.textContent==='Refresh');await refresh.onclick();assert.equal(posts,1,'refresh is read-only');assert(host.querySelector('[data-actions]').textContent.includes('Pause')||scenario==='post_failed');w.close();
 }console.log('PASS campaign start: confirmed/readback failure, uncertain POST and safe read-only recovery');})().catch(e=>{console.error(e);process.exitCode=1});
