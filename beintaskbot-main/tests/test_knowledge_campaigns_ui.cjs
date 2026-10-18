const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const script=fs.readFileSync(require('node:path').join(__dirname,'../docs/tenant-knowledge-campaigns.js'),'utf8');
(async()=>{for(const language of ['ru','az','en']){
  const dom=new JSDOM('<div id="cases"></div>',{url:'https://example.test/app',runScripts:'outside-only'}),w=dom.window;
  w.setTimeout=()=>0;w.confirm=()=>false;w.eval(script);
  const requests=[];let state=null;
  const ctx={lang:()=>language,expected:()=>({expected_tenant_id:'tenant-a',expected_user_id:11}),channels:[{provider:'connect',channel_id:'channel-a',name:'Primary'},{provider:'connect',channel_id:'channel-b',name:'Support'}],agents:[{id:'agent-a',name:'Analysis'}],api:async(url,options)=>{
    if(options){const data=JSON.parse(options.body);requests.push({url,data});
      if(url.endsWith('/campaigns'))state={id:'campaign',version:1,status:'awaiting_budget',channel_ids:data.channel_ids,max_calls:data.max_calls,max_tokens:data.max_tokens,fragments:{},reserved_calls:0,reserved_tokens:0,cards:0};
      if(url.endsWith('/start'))state={...state,version:2,status:'running'};
      if(url.endsWith('/control'))state={...state,version:state.version+1,status:data.action==='pause'?'paused':'stopped'};
    }return {campaign:state};}};
  const host=w.document.getElementById('cases');await w.TenantKnowledgeCampaigns.mount(host,ctx);
  assert.equal(requests.length,0);assert.equal(host.querySelectorAll('[data-source]').length,2);
  host.querySelector('[data-source]').checked=true;host.querySelector('[data-calls]').value='4';host.querySelector('[data-tokens]').value='48000';
  await host.querySelector('[data-prepare]').onclick();assert.equal(requests.length,1);assert.deepEqual(requests[0].data.channel_ids,['channel-a']);
  await host.querySelector('[data-actions] button').onclick();assert.equal(requests.length,1,'cancel cannot start');
  const consent=host.querySelector('[data-consent]');assert.ok(consent);consent.checked=true;consent.onchange();await host.querySelector('[data-actions] button').onclick();
  assert.equal(requests[1].data.confirm_external_processing,true);assert.equal(requests[1].data.expected_version,1);
  await host.querySelector('[data-actions] button').onclick();assert.equal(requests[2].data.action,'pause');
  assert.equal(requests[2].data.expected_version,2);assert.ok(!host.innerHTML.includes('undefined'));
  dom.window.close();
}console.log('Campaign controls: RU/AZ/EN, explicit consent, sources, versioned pause passed');})().catch(e=>{console.error(e);process.exitCode=1;});
