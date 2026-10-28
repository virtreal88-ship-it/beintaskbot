// Synthetic, in-memory campaign responses; never calls a provider.
const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const source=fs.readFileSync('docs/tenant-knowledge-campaigns.js','utf8'),tick=()=>new Promise(r=>setTimeout(r,0));
const campaign=(id='one',status='awaiting_budget',version=1)=>({id,status,version,channel_ids:[],max_calls:2,max_tokens:48000,media_policy:{allow_external_media:false,allow_paid_asr:false,max_main_calls:1,max_asr_calls:0,max_vision_calls:0},cards:1});
function create(){const w=new JSDOM('<div id="cases"></div>',{url:'https://fixture.test/app',runScripts:'outside-only'}).window;w.setTimeout=()=>0;w.eval(source);return w;}
function ctx(w,tenant,api,extra={}){return {lang:()=> 'en',expected:()=>({expected_tenant_id:tenant,expected_user_id:1}),channels:[],agents:[],api,...extra};}
function consent(w){const c=w.document.querySelector('[data-consent]');c.checked=true;c.onchange();return w.document.querySelector('[data-actions] button');}
(async()=>{
 {
  const w=create();let data=campaign(),reads=0,posts=0,releaseCards;const hold=new Promise(r=>releaseCards=r);
  const api=async(url,opt)=>{if(url.includes('/settings'))return {};if(opt){posts++;data=campaign('one','running',2);return {campaign:data};}reads++;return {campaign:{...data}};};
  const mount=w.TenantKnowledgeCampaigns.mount(w.document.getElementById('cases'),ctx(w,'a',api,{refreshCards:()=>hold}));await tick();await tick();const first=consent(w).onclick();await tick();let button=w.document.querySelector('[data-actions] button');assert(button.disabled,'POST blocks concurrent Start immediately');await button.onclick();assert.equal(posts,1);releaseCards();await mount;await first;assert(reads>=2,'post-start status requires a fresh GET after previous flight');assert.match(w.document.querySelector('[data-campaign-status]').textContent,/Processing/);assert.equal(posts,1);w.close();
 }
 {
  const w=create();let finish;const delayed=new Promise(r=>finish=r);let posts=0;
  const apiA=async(url,opt)=>{if(url.includes('/settings'))return {};if(opt){posts++;return delayed;}return {campaign:campaign('tenant-a')};};
  await w.TenantKnowledgeCampaigns.mount(w.document.getElementById('cases'),ctx(w,'a',apiA));const attempt=consent(w).onclick();await tick();w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'b',telegram_id:1}}}));
  const apiB=async(url,opt)=>url.includes('/settings')?{}:{campaign:campaign('tenant-b')};await w.TenantKnowledgeCampaigns.mount(w.document.getElementById('cases'),ctx(w,'b',apiB));finish({readback_pending:true,reference:'x'});await attempt;
  const keyA='case-processing-pending:'+encodeURIComponent(JSON.stringify(['a','1'])),keyB='case-processing-pending:'+encodeURIComponent(JSON.stringify(['b','1']));assert.equal(w.sessionStorage.getItem(keyB),null,'old response cannot write into new scope');assert.equal(JSON.parse(w.sessionStorage.getItem(keyA)).state,'unknown','old response cannot overwrite the originating fence');
  w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'a',telegram_id:'1'}}}));await w.TenantKnowledgeCampaigns.mount(w.document.getElementById('cases'),ctx(w,'a',apiA));assert(w.document.querySelector('[data-actions] button').disabled,'switching back preserves unresolved fence');assert.equal(posts,1);w.close();
 }
 {
  const w=create();let sent=null,data=campaign('renew','budget_exhausted',1);
  const api=async(url,opt)=>{if(url.includes('/settings'))return {};if(opt){sent=JSON.parse(opt.body);data=campaign('renew','running',2);return {campaign:data};}return {campaign:data};};
  await w.TenantKnowledgeCampaigns.mount(w.document.getElementById('cases'),ctx(w,'a',api));const d=w.document;d.querySelector('[data-renew-calls]').value='7';d.querySelector('[data-renew-tokens]').value='96000';d.querySelector('[data-renew-main]').value='3';await consent(w).onclick();assert.equal(sent.max_calls,7);assert.equal(sent.max_tokens,96000);assert.equal(sent.media_policy.max_main_calls,3,'input captured before redraw');w.close();
 }
 {
  const w=new JSDOM('<div id="cases"></div>',{url:'https://fixture.test/app',runScripts:'outside-only'}).window;w.sessionStorage.setItem('case-processing-pending:old','fixture');w.sessionStorage.setItem('other-key','retain');w.eval(source);w.document.dispatchEvent(new w.CustomEvent('tenant-auth-invalid'));assert.equal(w.sessionStorage.getItem('case-processing-pending:old'),null,'logout removes prior session marker even before mount');assert.equal(w.sessionStorage.getItem('other-key'),'retain');w.close();
 }
 console.log('PASS case processing races: prior read fencing, no duplicate start, tenant switch and immutable renewal payload');
})().catch(e=>{console.error(e);process.exitCode=1});
