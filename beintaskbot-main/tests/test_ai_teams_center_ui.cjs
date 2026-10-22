const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const html='<!doctype html><html><body><button id="aiTeamsNav" data-view="ai_teams"><span data-ai-teams-label></span></button><main><section class="view" id="view-ai_teams"><div id="aiTeamsCenter"></div></section></main></body></html>';
const dom=new JSDOM(html,{url:'https://app.test/app?view=ai_teams&site_lang=ru',runScripts:'dangerously',pretendToBeVisual:true});
const w=dom.window,tenant='tenant-a',user=42;w.tenantI18n={language:'ru'};w.tenantProfileSnapshot={member:{tenant_id:tenant,telegram_id:user},capabilities:{modules:{ai_agents:true}}};w.sessionStorage.setItem('tenant.app.view','ai_teams');
let patchBody=null;const agents=[
 {id:'primary-1',name:'Основной',role:'primary',enabled:true,mode:'manual',model:'cheap-model',expert_policy:{expert_agent_id:'expert-1',max_tokens:6500},version:7,triggers:[{id:'tr-1',name:'Передать эксперту',trigger_type:'phrase',enabled:true,actions:['call_expert'],expert_agent_ids:['expert-1'],version:3}]},
 {id:'primary-2',name:'Без экспертов',role:'primary',enabled:true,mode:'manual',expert_policy:{},version:1,triggers:[]},
 {id:'expert-1',name:'Поддержка',role:'expert',enabled:true,mode:'manual',model:'expert-model',expert_policy:{internal:true,allowed_primary_ids:['primary-1'],tools:['system_status'],max_tokens:4000},version:2,triggers:[]}
];
w.fetch=async(input,options={})=>{const url=new URL(input,w.location.href);if(options.method==='PATCH'){patchBody=JSON.parse(options.body);assert.equal(url.pathname,'/api/platform/ai/agents/primary-1');agents[0].expert_policy=patchBody.expert_policy;agents[0].version++;return {ok:true,status:200,json:async()=>({success:true,result:{}})};}assert.equal(url.searchParams.get('expected_tenant_id'),tenant);assert.equal(url.searchParams.get('expected_user_id'),String(user));assert.equal(url.pathname,'/api/platform/ai/agents');return {ok:true,status:200,json:async()=>({success:true,result:{agents,tool_registry:[{id:'system_status',registered:true,status:'integration_unavailable',read_only:true,customer_send_allowed:false},{id:'other_tool',registered:true,status:'available'}]}})};};
w.eval(fs.readFileSync(path.join(__dirname,'../docs/tenant-ai-teams.js'),'utf8'));
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{for(let i=0;i<20&&!w.document.querySelector('.team-selected');i++)await tick();for(let i=0;i<3;i++)await tick();
 assert.equal(w.document.querySelectorAll('.team-picker-row').length,2);assert.equal(w.document.querySelectorAll('.team-selected').length,1);assert.match(w.document.querySelector('#aiTeamsCenter').textContent,/только основной агент отвечает клиенту/i);
 assert.match(w.document.querySelector('#aiTeamsCenter').textContent,/Триггеры вызова экспертов/);assert.match(w.document.querySelector('#aiTeamsCenter').textContent,/Адаптер недоступен/);assert.match(w.document.querySelector('#aiTeamsCenter').textContent,/cheap-model/);
 const rows=w.document.querySelectorAll('.team-picker-row');rows[1].click();assert.match(w.document.querySelector('#teamDetail').textContent,/Без экспертов/);rows[0].click();
 const expertCheck=w.document.querySelector('input[data-expert="expert-1"]');assert.equal(expertCheck.checked,true);expertCheck.checked=false;
 w.document.querySelector('.team-save').click();for(let i=0;i<30&&!patchBody;i++)await tick();
 assert.ok(patchBody,`no PATCH received; notice=${w.document.querySelector('#teamNotice').textContent}; buttonDisabled=${w.document.querySelector('.team-save').disabled}`);assert.deepEqual(patchBody.expert_policy,{max_tokens:6500});assert.equal(patchBody.role,'primary');assert.equal(patchBody.expected_version,7);assert.equal(patchBody.expected_tenant_id,tenant);assert.equal(patchBody.expected_user_id,user);
 assert.equal(agents[0].version,8);assert.equal(w.document.querySelectorAll('.team-selected').length,1);assert.equal(w.document.getElementById('aiTeamsNav').hidden,true);assert.equal(patchBody.customer_send_allowed,undefined);
 w.tenantI18n.language='en';w.document.dispatchEvent(new w.CustomEvent('tenant-language'));
 for(let i=0;i<30&&(!w.document.querySelector('.team-selected')||w.document.querySelector('#aiTeamsCenter h1')?.textContent!=='AI Teams');i++)await tick();
 assert.equal(w.document.querySelector('#aiTeamsCenter h1')?.textContent,'AI Teams','language changes should refresh the complete Teams shell');
 assert.match(w.document.querySelector('#aiTeamsCenter').textContent,/Expert call triggers/);
 console.log('PASS: compact team picker, one selected Primary, truthful tool state, tenant-scoped versioned link editing');dom.window.close();
 const reloadDom=new JSDOM(html.replace('view=ai_teams','view=ai_agents'),{url:'https://app.test/app?view=ai_agents&site_lang=ru',runScripts:'dangerously',pretendToBeVisual:true});
 const rw=reloadDom.window;rw.tenantI18n={language:'ru'};rw.sessionStorage.setItem('tenant.ai.workspace.tab','teams');let reloadFetches=0;
 rw.fetch=async(input)=>{reloadFetches++;assert.match(String(input),/view=teams/);return {ok:true,status:200,json:async()=>({success:true,result:{agents,tool_registry:[]}})};};
 rw.eval(fs.readFileSync(path.join(__dirname,'../docs/tenant-ai-teams.js'),'utf8'));
 rw.document.dispatchEvent(new rw.CustomEvent('tenant-profile',{detail:{capabilities:{modules:{ai_agents:true}}}}));
 for(let i=0;i<30&&!rw.document.querySelector('.team-selected');i++)await tick();
 assert.equal(reloadFetches,1,'saved Teams workspace should load after tenant profile arrives');
 assert.equal(rw.document.querySelectorAll('.team-picker-row').length,2);
 assert.equal(rw.document.querySelectorAll('.team-selected').length,1);
 console.log('PASS: saved Teams tab restores and loads after a page reload');reloadDom.window.close();
})().catch(error=>{console.error(error.stack||error);dom.window.close();process.exitCode=1;});
