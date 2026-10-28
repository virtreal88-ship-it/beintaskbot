const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync(path.join(__dirname,'../docs/tenant-knowledge-campaigns.js'),'utf8');

(async()=>{
  const dom=new JSDOM('<div id="cases"></div>',{url:'https://example.test/app',runScripts:'outside-only'});
  const w=dom.window;w.setTimeout=()=>0;w.eval(script);const seen=[];
  const ctx={lang:()=> 'en',expected:()=>({expected_tenant_id:'tenant',expected_user_id:11}),channels:[],agents:[],api:async(url,options)=>{
    seen.push({url,options});
    if(url.includes('/preview?')){const parsed=new URL(url,'https://example.test'),retry=parsed.searchParams.get('acknowledge_prior_unknown')==='true';return {case_count:1,eligible_count:retry?1:0,reused_count:0,review_count:0,unknown_media_count:retry?1:2,unavailable_media_count:0,retry_unknown_count:retry?1:0,
      cases:[{case_id:'case-a',status:'pending_consent',media:[{id:'message-a',kind:'image',status:'unknown',code:'status_not_recorded',complete:false,source_version_verified:false,current_source_addressable:false,
        reconciliation:{state:'unknown',reason:'attempt_history_not_recorded',matching_runs:0}},
        {id:'video-a',kind:'video',status:retry?'pending_retry':'unknown',code:retry?'owner_acknowledged_new_attempt':'source_version_unverified',complete:false,source_version_verified:false,current_source_addressable:true,retry_authorized:retry,
          reconciliation:{state:'unknown',reason:'historical_source_version_unverified',matching_runs:1}}]}]};}
    return {campaign:null};
  }};
  const root=w.document.getElementById('cases');await w.TenantKnowledgeCampaigns.mount(root,ctx);
  root.querySelector('[data-all-existing]').checked=true;root.querySelector('[data-all-existing]').onchange();
  await new Promise(resolve=>setTimeout(resolve,0));await new Promise(resolve=>setTimeout(resolve,0));
  assert(seen.some(x=>x.url.includes('/preview?')&&x.url.includes('all_existing_cases=true')),'must call read-only snapshot preview');
  const preview=root.querySelector('[data-batch-preview]');
  assert.match(preview.textContent,/attempt history is not recorded/,'must explain why unverified media is excluded');
  assert.match(preview.textContent,/case-a/);assert.match(preview.textContent,/message-a/);
  assert.match(preview.textContent,/Choose a separate video model/i,'unprocessed video must be called out while no video model is selected');
  assert.match(preview.textContent,/current message only/i,'unverified historical bytes must be distinguished from the addressable current message');
  const retry=root.querySelector('[data-retry-unknown]');retry.checked=true;retry.onchange();
  await new Promise(resolve=>setTimeout(resolve,0));await new Promise(resolve=>setTimeout(resolve,0));
  const retryPreview=seen.filter(x=>x.url.includes('/preview?')).at(-1);
  assert.equal(new URL(retryPreview.url,'https://example.test').searchParams.get('acknowledge_prior_unknown'),'true');
  assert.match(preview.textContent,/1 new current-version attempts/i,'a loader-addressable exact current message becomes one explicit new attempt');
  assert.match(preview.textContent,/protected source loader/i,'a source that cannot be opened remains blocked even after acknowledgement');
  assert.doesNotMatch(preview.textContent,/video-a/,'the admitted current message is not still displayed as blocked');
  assert.equal(seen.filter(x=>x.options?.method==='POST').length,0,'preview must not create a grant or start analysis');
  dom.window.close();
  console.log('Owner batch preview: read-only zero-eligible reason and exact media scope passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
