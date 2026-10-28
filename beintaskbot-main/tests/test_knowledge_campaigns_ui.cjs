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
  const batchRequests=[],previewRequests=[];let batchState={id:'old-stopped',version:4,status:'stopped',channel_ids:['old-channel'],max_calls:10000,max_tokens:160000000,media_policy:{},progress:{mode:'case_media',attachment_count:1},reserved_calls:2648,reserved_tokens:259,used_tokens:259,cost:0,cards:0};
  const batchCtx={...ctx,api:async(url,options)=>{
    if(!options&&url==='/api/platform/chat/ai/settings')return {photo_model:'gpt-4.1-2025-04-14',video_model_tariffs:{
      'gpt-4.1-2025-04-14':{ready:true,max_microusd:22500},'gpt-4o':{ready:false,reason_code:'video_model_tariff_required'}}};
    if(options){const data=JSON.parse(options.body);batchRequests.push({url,data});
      if(url.endsWith('/campaigns'))batchState={id:'batch',version:1,status:'awaiting_budget',channel_ids:['channel-a'],
        max_calls:data.max_calls,max_tokens:data.max_tokens,media_policy:{allow_external_media:data.allow_external_media,
          allow_paid_asr:data.allow_paid_asr,max_main_calls:data.max_main_calls,max_asr_calls:data.max_asr_calls,
          max_vision_calls:data.max_vision_calls},progress:{mode:'case_media_batch',case_count:3,eligible_count:2,
          reused_count:1,review_count:0,unknown_media_count:1,unavailable_media_count:0,retry_unknown_count:data.acknowledge_prior_unknown?1:0,acknowledge_prior_unknown:data.acknowledge_prior_unknown===true,case_plans:[]},
        max_cost_microusd:data.max_cost_microusd,video_model:data.video_model,photo_model:'gpt-4.1-2025-04-14',grant_used_microusd:0,
        prior_unknown_holds_microusd:0,prior_settled_microusd:0,analysis_model:'text-model',asr_model:'asr-model',
        grant_approved:false,fragments:{},reserved_calls:0,reserved_tokens:0,used_tokens:0,cards:0};
      if(url.endsWith('/start'))batchState={...batchState,version:2,status:'running',grant_approved:true};
    }if(url.includes('/preview?')){const parsed=new URL(url,'https://example.test');previewRequests.push(parsed);return {result:{case_count:3,eligible_count:2,reused_count:1,review_count:0,unknown_media_count:1,unavailable_media_count:0,retry_unknown_count:parsed.searchParams.get('acknowledge_prior_unknown')==='true'?1:0,asr_model:'gpt-4o-mini-transcribe',asr_tariff:{ready:false,reason_code:'asr_media_tariff_required: model=gpt-4o-mini-transcribe operation=asr minimum_microusd=30000'},cases:[]}};}return {campaign:batchState};}};
  await w.TenantKnowledgeCampaigns.mount(host,batchCtx);
  assert.match(host.querySelector('[data-campaign-status]').textContent,/stopped|остановлено|dayandırılıb/i,'old stopped campaign is initially visible in its own context');
  const all=host.querySelector('[data-all-existing]');all.checked=true;all.onchange();
  await new Promise(resolve=>setTimeout(resolve,0));await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(host.querySelector('[data-source]').disabled,true);
  assert.equal(host.querySelector('[data-retry-wrap]').hidden,false);
  assert.match(host.querySelector('[data-asr-readiness]').textContent,/gpt-4o-mini-transcribe.*asr_media_tariff_required/,'preflight exposes the exact ASR model and missing operation tariff');
  assert.ok([...host.querySelector('[data-video-model]').options].some(option=>option.value==='gpt-4.1-2025-04-14'),'dated backend model with a ready tariff is selectable');
  assert.ok(![...host.querySelector('[data-video-model]').options].some(option=>option.value==='gpt-4o'),'video model without a configured tariff is not selectable');
  assert.equal(host.querySelector('[data-batch-grant]').hidden,false);
  assert.match(host.querySelector('[data-batch-grant]').textContent,/\$10/);
  assert.match(host.querySelector('[data-prepare]').textContent,/\$10/,'prepare button should name the fixed grant cap');
  assert.doesNotMatch(host.querySelector('[data-campaign-status]').textContent,/259|2648|old-channel/,'stopped unrelated campaign metrics must not masquerade as this snapshot');
  host.querySelector('[data-retry-unknown]').checked=true;host.querySelector('[data-retry-unknown]').onchange();
  await new Promise(resolve=>setTimeout(resolve,0));await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(previewRequests.at(-1).searchParams.get('all_existing_cases'),'true');
  assert.equal(previewRequests.at(-1).searchParams.get('acknowledge_prior_unknown'),'true','read-only preview must receive the explicit retry choice');
  assert.equal(previewRequests.at(-1).searchParams.get('expected_tenant_id'),'tenant-a');
  assert.equal(previewRequests.at(-1).searchParams.get('expected_user_id'),'11');
  assert.equal(previewRequests.at(-1).searchParams.get('analysis_agent_id'),'agent-a','ASR readiness must use the exact selected agent configuration');
  assert.ok(batchRequests.length===0,'preview must remain read-only');
  host.querySelector('[data-video-model]').value='gpt-4.1-2025-04-14';
  await host.querySelector('[data-prepare]').onclick();
  assert.equal(batchRequests.length,1);assert.equal(batchRequests[0].data.all_existing_cases,true);
  assert.equal(batchRequests[0].data.max_cost_microusd,10000000);assert.equal(batchRequests[0].data.video_model,'gpt-4.1-2025-04-14');
  assert.equal(batchRequests[0].data.acknowledge_prior_unknown,true);
  assert.deepEqual(batchRequests[0].data.channel_ids,[]);
  assert.ok(host.querySelector('.campaign-run-notice').textContent.includes('$10.00'));
  assert.ok(host.querySelector('.campaign-run-notice').textContent.includes('text-model'));
  assert.ok(host.querySelector('.campaign-run-notice').textContent.includes('gpt-4.1-2025-04-14'),'snapshot readback exposes selected company photo model');
  const batchConsent=host.querySelector('[data-consent]');assert.ok(batchConsent);batchConsent.checked=true;batchConsent.onchange();
  const startButton=[...host.querySelectorAll('[data-actions] button')].find(button=>button.textContent===({ru:'Запустить анализ',az:'Analizi başlat',en:'Start analysis'}[language]));
  assert.ok(startButton);await startButton.onclick();
  assert.equal(batchRequests[1].data.acknowledge_prior_unknown,true);
  assert.equal(batchRequests[1].data.photo_model,'gpt-4.1-2025-04-14');
  all.checked=false;all.onchange();
  assert.equal(host.querySelector('[data-batch-grant]').hidden,true);
  assert.equal(host.querySelector('[data-prepare]').textContent,({ru:'Подготовить',az:'Hazırla',en:'Prepare'}[language]));
  dom.window.close();
}
{
  const dom=new JSDOM('<div id="cases"></div>',{url:'https://example.test/app',runScripts:'outside-only'}),w=dom.window;
  w.setTimeout=()=>0;w.confirm=()=>false;w.eval(script);let resolveOld;
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  const ctx={lang:()=> 'en',expected:()=>({expected_tenant_id:'tenant',expected_user_id:11}),channels:[],
    agents:[{id:'agent-a',name:'A'},{id:'agent-b',name:'B'}],api:async(url)=>{
      if(url==='/api/platform/chat/ai/settings')return {video_model_tariffs:{}};
      if(url.includes('/preview?')){
        const agent=new URL(url,'https://example.test').searchParams.get('analysis_agent_id');
        const result={case_count:0,eligible_count:0,reused_count:0,review_count:0,unknown_media_count:0,unavailable_media_count:0,cases:[],
          asr_model:agent==='agent-a'?'model-a':'model-b',asr_tariff:{ready:true,reserve_microusd:1000}};
        if(agent==='agent-a')return new Promise(resolve=>{resolveOld=()=>resolve({result});});
        return {result};
      }
      return {campaign:null};
    }};
  const host=w.document.getElementById('cases');await w.TenantKnowledgeCampaigns.mount(host,ctx);
  const all=host.querySelector('[data-all-existing]');all.checked=true;all.onchange();await tick();
  assert.equal(typeof resolveOld,'function','first selected-agent preview should be pending');
  const agent=host.querySelector('[data-agent]');agent.value='agent-b';agent.onchange();await tick();
  assert.match(host.querySelector('[data-asr-readiness]').textContent,/model-b/);
  resolveOld();await tick();
  assert.match(host.querySelector('[data-asr-readiness]').textContent,/model-b/,'late result for the previous agent must not overwrite current readiness');
  assert.doesNotMatch(host.querySelector('[data-asr-readiness]').textContent,/model-a/);
  dom.window.close();
}
console.log('Campaign controls: RU/AZ/EN, explicit consent, sources, versioned pause and stale-preview fencing passed');})().catch(e=>{console.error(e);process.exitCode=1;});
