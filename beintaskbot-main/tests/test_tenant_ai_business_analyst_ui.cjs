const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const supportDeps=process.env.TEST_SUPPORT_DEPS||path.join(__dirname,'..','.support-test-deps','node_modules');
const {JSDOM}=require(path.join(supportDeps,'jsdom'));
const root=path.resolve(__dirname,'..'),read=name=>fs.readFileSync(path.join(root,name),'utf8');
(async()=>{
 const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://crm.test/app?view=ai_dashboard',runScripts:'outside-only'}),w=dom.window,d=w.document,calls=[];
 w.tenantI18n={language:'ru'};w.tenantProfileSnapshot={member:{tenant_id:'tenant-a',telegram_id:7,role:'admin'},capabilities:{}};
 Object.defineProperty(w.crypto,'randomUUID',{value:()=> '22222222-2222-4222-8222-222222222222'});
 w.fetch=async(url,options={})=>{const body=options.body?JSON.parse(options.body):null;calls.push({url,options,body});const payload=url.endsWith('/tools')?{success:true,result:{read_only:true,checked_at:'2026-11-11T08:00:00Z',partial:true,tasks:[
  {source:'linear',task_id:'BS-1364',title:'<img src=x onerror=alert(1)>',description:'<script>not markup</script>',status:'Todo',source_ref:{kind:'linear_issue',url:'https://linear.app/bein-systems/issue/BS-1364/task'}},
  {source:'native',task_id:'native-7',title:'Beintaskbot task',status:'open',source_ref:{kind:'crm_task',inbox_url:'/app'}},
  {source:'kommo',task_id:'kommo-9',title:'Untrusted URL task',source_ref:{kind:'crm_task',url:'https://evil.example/task'}}
 ],sources:{linear:{available:true,scope:'configured team and current member visibility',freshness:'record_timestamp_only; sync completeness unknown'},native:{available:true,scope:'company task records',has_more:true},kommo:{available:false,reason:'source_unavailable'}}}}:{success:true,result:{text:'<img src=x onerror=alert(1)>',read_only:true,source_refs:[{kind:'linear_task',task_id:'ABC-1',inbox_url:'/app?view=linear'},{kind:'case',candidate_id:'id',inbox_url:'https://evil.example'}]}};return{ok:true,status:200,json:async()=>payload}};
 w.eval(read('docs/tenant-ai-business-analyst.js'));
 assert.equal(d.querySelector('#aiBusinessAnalystHost form'),null,'non-owner never sees the analyst form');
 assert.equal(d.querySelector('.analyst-task-search-form'),null,'non-owner never sees direct task search');
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'tenant-a',telegram_id:7,role:'owner'},capabilities:{}}}));
 const form=d.querySelector('#aiBusinessAnalystHost form');assert.ok(form,'owner gets the isolated management analyst');
 const input=form.querySelector('textarea');input.value='Which task is overdue?';form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
 for(let i=0;i<20&&calls.length===0;i++)await new Promise(resolve=>setImmediate(resolve));
 for(let i=0;i<20&&d.querySelector('.analyst-answer')?.hidden!==false;i++)await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/platform/ai/analytics/ask');
 assert.deepEqual(calls[0].body,{expected_tenant_id:'tenant-a',expected_user_id:'7',request_id:'22222222-2222-4222-8222-222222222222',prompt:'Which task is overdue?'});
 assert.equal(calls[0].options.credentials,'same-origin');assert.equal(calls[0].options.cache,'no-store');
 const answer=d.querySelector('.analyst-answer');assert.equal(answer.textContent,'<img src=x onerror=alert(1)>');assert.equal(answer.querySelector('img'),null,'model output is rendered as text');
 assert.equal(d.querySelectorAll('.analyst-sources a').length,1,'only same-origin /app references are linked');
 const taskForm=d.querySelector('.analyst-task-search-form');assert.ok(taskForm,'owner can open direct task search');
 const taskQuery=taskForm.querySelector('input[type=search]');taskQuery.value='BS-1364';taskForm.querySelector('select').value='all';
 taskForm.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
 for(let i=0;i<20&&calls.length<2;i++)await new Promise(resolve=>setImmediate(resolve));
 for(let i=0;i<20&&d.querySelectorAll('.analyst-task-result').length<3;i++)await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.length,2,'direct search sends one read request');
 assert.equal(calls[1].url,'/api/platform/ai/analytics/tools','direct search uses the server task reader, not the model route');
 assert.deepEqual(calls[1].body,{expected_tenant_id:'tenant-a',expected_user_id:'7',tool:'search_tasks',arguments:{query:'BS-1364',source:'all',limit:10}});
 assert.equal(calls[1].options.credentials,'same-origin');assert.equal(calls[1].options.cache,'no-store');
 assert.equal(d.querySelectorAll('.analyst-task-result').length,3,'task results render from every returned source');
 assert.equal(d.querySelector('.analyst-task-result strong').textContent,'<img src=x onerror=alert(1)>','task content is rendered as text');
 assert.equal(d.querySelector('.analyst-task-result img'),null,'untrusted task content is not interpreted as markup');
 const taskLinks=[...d.querySelectorAll('.analyst-task-result a')];assert.equal(taskLinks.length,2,'only trusted Linear and same-origin app links are rendered');
 assert.equal(taskLinks[0].href,'https://linear.app/bein-systems/issue/BS-1364/task');
 assert.equal(taskLinks[1].getAttribute('href'),'/app?view=tasks');
 assert.match(d.querySelector('.analyst-task-status').textContent,/неполным/i,'partial source coverage is shown');
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'tenant-a',telegram_id:7,role:'admin'},capabilities:{}}}));
 assert.equal(d.querySelector('#aiBusinessAnalystHost').children.length,0,'role change clears the owner-only result');
 assert.equal(calls.filter(call=>call.url==='/api/platform/ai/analytics/ask').length,1,'direct task search does not send another AI request');
 assert.equal(calls.length,2,'no customer-message, CRM-write, or automatic retry request was made');
 dom.window.close();console.log('Business Analyst UI: owner-only, direct read-only task search, safe links/rendering, and role-change clearing passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
