const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
(async()=>{
 const dom=new JSDOM('<div id="cases"></div>',{url:'https://example.test/app',runScripts:'outside-only'}),w=dom.window;
 w.setTimeout=()=>0;w.eval(fs.readFileSync('docs/tenant-knowledge-campaigns.js','utf8'));
 let campaign={id:'campaign',version:27,status:'paused',grant_approved:true,max_cost_microusd:10000000,channel_ids:[],media_policy:{},progress:{mode:'case_media_batch'},cards:0,limit_diagnostics:{current_blocker:'company_daily_ai_call_limit',utc_daily_ai_call_limit:50}};const requests=[];
 const api=async(url,opt)=>{if(url.includes('/settings'))return {};if(opt){const data=JSON.parse(opt.body);requests.push({url,data});campaign={...campaign,version:28,progress:{...campaign.progress,daily_call_ceiling:{ceiling:150}}};}return {campaign};};
 await w.TenantKnowledgeCampaigns.mount(w.document.querySelector('#cases'),{channels:[],agents:[],member:{role:'owner'},lang:()=> 'ru',expected:()=>({expected_tenant_id:'t',expected_user_id:1}),api});
 const button=w.document.querySelector('[data-case-daily-ceiling]');assert(button);w.confirm=()=>false;button.click();assert.equal(requests.length,0);w.confirm=()=>true;button.click();await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
 assert.equal(requests.length,1);assert.equal(requests[0].data.action,'approve_daily_ceiling_150');assert.equal(requests[0].data.expected_version,27);assert.equal(w.document.querySelector('[data-case-daily-ceiling]'),null);assert(!requests.some(r=>/start/.test(r.url)));dom.window.close();console.log('PASS scoped daily approval UI');
})().catch(e=>{console.error(e);process.exitCode=1;});
