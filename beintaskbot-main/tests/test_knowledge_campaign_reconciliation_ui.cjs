const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync(path.join(__dirname,'../docs/tenant-knowledge-campaigns.js'),'utf8');

(async()=>{
  const dom=new JSDOM('<div id="cases"></div>',{url:'https://example.test/app',runScripts:'outside-only'});
  const w=dom.window;w.setTimeout=()=>0;w.eval(script);const seen=[];
  const ctx={lang:()=> 'en',expected:()=>({expected_tenant_id:'tenant',expected_user_id:11}),channels:[],agents:[],api:async(url,options)=>{
    seen.push({url,options});
    if(url.includes('/preview?'))return {case_count:1,eligible_count:0,reused_count:0,review_count:0,unknown_media_count:1,unavailable_media_count:0,
      cases:[{case_id:'case-a',status:'pending_consent',media:[{id:'message-a',status:'unknown',code:'status_not_recorded',complete:false,
        reconciliation:{state:'unknown',reason:'attempt_history_not_recorded',matching_runs:0}}]}]};
    return {campaign:null};
  }};
  const root=w.document.getElementById('cases');await w.TenantKnowledgeCampaigns.mount(root,ctx);
  root.querySelector('[data-all-existing]').checked=true;root.querySelector('[data-all-existing]').onchange();
  await new Promise(resolve=>setTimeout(resolve,0));await new Promise(resolve=>setTimeout(resolve,0));
  assert(seen.some(x=>x.url.includes('/preview?')&&x.url.includes('all_existing_cases=true')),'must call read-only snapshot preview');
  const preview=root.querySelector('[data-batch-preview]');
  assert.match(preview.textContent,/attempt history is not recorded/,'must explain why unverified media is excluded');
  assert.match(preview.textContent,/case-a/);assert.match(preview.textContent,/message-a/);
  assert.equal(seen.filter(x=>x.options?.method==='POST').length,0,'preview must not create a grant or start analysis');
  dom.window.close();
  console.log('Owner batch preview: read-only zero-eligible reason and exact media scope passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
