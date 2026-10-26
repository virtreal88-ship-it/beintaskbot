const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync(path.join(__dirname,'../docs/tenant-knowledge-campaigns.js'),'utf8');

(async()=>{
  const dom=new JSDOM('<div id="cases"></div>',{url:'https://example.test/app',runScripts:'outside-only'});
  const w=dom.window;w.setTimeout=()=>0;w.eval(script);
  const seen=[];let campaign=null;
  const ctx={lang:()=> 'en',expected:()=>({expected_tenant_id:'tenant',expected_user_id:11}),
    channels:[{provider:'connect',channel_id:'channel-a',name:'A'},{provider:'connect',channel_id:'channel-b',name:'B'}],
    agents:[{id:'agent-a',name:'Analysis'}],api:async(url,options)=>{
      if(options){const data=JSON.parse(options.body);seen.push({url,data});
        if(url.endsWith('/campaigns'))campaign={id:'campaign',version:1,status:'awaiting_budget',channel_ids:data.channel_ids,
          max_calls:data.max_calls,max_tokens:data.max_tokens,media_policy:{allow_external_media:data.allow_external_media,
          allow_paid_asr:data.allow_paid_asr,max_main_calls:data.max_main_calls,max_asr_calls:data.max_asr_calls,
          max_vision_calls:data.max_vision_calls},progress:{mode:'case_media',case_id:data.case_id,
          plan_hash:'a'.repeat(64),attachment_count:14},fragments:{},reserved_calls:0,reserved_tokens:0,cards:0};
        if(url.endsWith('/start'))campaign={...campaign,version:2,status:'running'};
      }return {campaign};}};
  const root=w.document.getElementById('cases');await w.TenantKnowledgeCampaigns.mount(root,ctx);
  assert.equal(seen.length,0,'opening must not create a campaign');
  w.dispatchEvent(new w.CustomEvent('case-media-admission',{detail:{id:'case-a',title:'Inventory question',
    channelId:'channel-b',attachmentCount:14,hasAudio:true,hasVisual:true}}));
  assert.equal(root.querySelector('[data-calls]').value,'56');
  assert.equal(root.querySelector('[data-tokens]').value,'448000');
  assert.deepEqual([...root.querySelectorAll('[data-source]:checked')].map(x=>x.value),['channel-b']);
  await root.querySelector('[data-prepare]').onclick();
  assert.equal(seen[0].data.case_id,'case-a');
  assert.deepEqual(seen[0].data.channel_ids,['channel-b']);
  assert.equal(seen[0].data.max_vision_calls,28);
  const consent=root.querySelector('[data-consent]');consent.checked=true;consent.onchange();
  await root.querySelector('[data-actions] button').onclick();
  assert.equal(seen[1].data.case_id,'case-a');
  assert.equal(seen[1].data.case_plan_hash,'a'.repeat(64));
  assert.equal(seen[1].data.confirm_external_processing,true);
  dom.window.close();
  console.log('Case media owner plan: exact case/channel/hash, bounded budget, explicit consent passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
