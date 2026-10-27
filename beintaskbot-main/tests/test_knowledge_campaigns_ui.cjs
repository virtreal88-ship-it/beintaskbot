const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync(require('node:path').join(__dirname,'../docs/tenant-knowledge-campaigns.js'),'utf8');
(async()=>{for(const language of ['ru','az','en']){
  const dom=new JSDOM('<div id="cases"></div>',{url:'https://example.test/app',runScripts:'outside-only'}),w=dom.window;
  w.setTimeout=()=>0;w.confirm=()=>false;w.eval(script);
  const requests=[];let state=null;
  const ctx={lang:()=>language,expected:()=>({expected_tenant_id:'tenant-a',expected_user_id:11}),channels:[{provider:'connect',channel_id:'channel-a',name:'Primary'},{provider:'connect',channel_id:'channel-b',name:'Support'}],agents:[{id:'agent-a',name:'Analysis'}],api:async(url,options)=>{
    if(options){const data=JSON.parse(options.body);requests.push({url,data});
      if(url.endsWith('/campaigns'))state={id:'campaign',version:1,status:'awaiting_budget',channel_ids:data.channel_ids,max_calls:data.max_calls,max_tokens:data.max_tokens,media_policy:{allow_external_media:data.allow_external_media,allow_paid_asr:data.allow_paid_asr,max_main_calls:data.max_main_calls,max_asr_calls:data.max_asr_calls,max_vision_calls:data.max_vision_calls},fragments:{},reserved_calls:2,reserved_tokens:12000,used_tokens:3500,cards:0};
      if(url.endsWith('/start'))state={...state,version:2,status:'running'};
      if(url.endsWith('/control'))state={...state,version:state.version+1,status:data.action==='pause'?'paused':'stopped'};
    }return {campaign:state};}};
  const host=w.document.getElementById('cases');await w.TenantKnowledgeCampaigns.mount(host,ctx);
  assert.equal(requests.length,0);assert.equal(host.querySelectorAll('[data-source]').length,2);
  host.querySelectorAll('[data-source]')[1].checked=true;host.querySelector('[data-limit-main]').value='4';host.querySelector('[data-calls]').value='4';host.querySelector('[data-tokens]').value='48000';
  await host.querySelector('[data-prepare]').onclick();assert.equal(requests.length,1);assert.deepEqual(requests[0].data.channel_ids,['channel-b']);
  const metrics=[...host.querySelectorAll('.campaign-metric')].map(row=>[row.querySelector('dt').textContent,row.querySelector('dd').textContent]);
  const digits=value=>value.replace(/[.,\s\u00a0]/g,'');
  assert.ok(metrics.some(([label,value])=>/token|бюджет|büdcə/i.test(label)&&digits(value)==='48000'),`${language}: campaign token limit should use API data`);
  assert.ok(metrics.some(([label,value])=>/actual|фактичес|faktiki/i.test(label)&&digits(value)==='3500'),`${language}: actual usage should use API data`);
  assert.ok(metrics.filter(([,value])=>/No data|Нет данных|Məlumat yoxdur/.test(value)).length>=4,`${language}: unavailable quotas and counters must not be shown as zero`);
  await host.querySelector('[data-actions] button').onclick();assert.equal(requests.length,1,'cancel cannot start');
  const consent=host.querySelector('[data-consent]');assert.ok(consent);consent.checked=true;consent.onchange();await host.querySelector('[data-actions] button').onclick();
  assert.equal(requests[1].data.media_policy.allow_paid_asr,false);assert.equal(requests[1].data.media_policy.allow_external_media,false);assert.equal(requests[1].data.media_policy.max_main_calls,4);assert.equal(requests[1].data.confirm_external_processing,true);assert.equal(requests[1].data.expected_version,1);
  await host.querySelector('[data-actions] button').onclick();assert.equal(requests[2].data.action,'pause');
  assert.equal(requests[2].data.expected_version,2);assert.ok(!host.innerHTML.includes('undefined'));
  const batchRequests=[];let batchState=null;
  const batchCtx={...ctx,api:async(url,options)=>{
    if(options){const data=JSON.parse(options.body);batchRequests.push({url,data});
      if(url.endsWith('/campaigns'))batchState={id:'batch',version:1,status:'awaiting_budget',channel_ids:['channel-a'],
        max_calls:data.max_calls,max_tokens:data.max_tokens,media_policy:{allow_external_media:data.allow_external_media,
          allow_paid_asr:data.allow_paid_asr,max_main_calls:data.max_main_calls,max_asr_calls:data.max_asr_calls,
          max_vision_calls:data.max_vision_calls},progress:{mode:'case_media_batch',case_count:3,eligible_count:2,
          reused_count:1,review_count:0,unknown_media_count:0,unavailable_media_count:0,case_plans:[]},
        max_cost_microusd:data.max_cost_microusd,video_model:data.video_model,grant_used_microusd:0,
        prior_unknown_holds_microusd:0,prior_settled_microusd:0,analysis_model:'text-model',asr_model:'asr-model',
        grant_approved:false,fragments:{},reserved_calls:0,reserved_tokens:0,used_tokens:0,cards:0};
    }return {campaign:batchState};}};
  await w.TenantKnowledgeCampaigns.mount(host,batchCtx);
  const all=host.querySelector('[data-all-existing]');all.checked=true;all.onchange();
  assert.equal(host.querySelector('[data-source]').disabled,true);
  host.querySelector('[data-video-model]').value='gpt-4.1';
  await host.querySelector('[data-prepare]').onclick();
  assert.equal(batchRequests.length,1);assert.equal(batchRequests[0].data.all_existing_cases,true);
  assert.equal(batchRequests[0].data.max_cost_microusd,10000000);assert.equal(batchRequests[0].data.video_model,'gpt-4.1');
  assert.deepEqual(batchRequests[0].data.channel_ids,[]);
  assert.ok(host.querySelector('.campaign-run-notice').textContent.includes('$10.00'));
  assert.ok(host.querySelector('.campaign-run-notice').textContent.includes('text-model'));
  dom.window.close();
}console.log('Campaign controls: RU/AZ/EN, explicit consent, sources, versioned pause passed');})().catch(e=>{console.error(e);process.exitCode=1;});
