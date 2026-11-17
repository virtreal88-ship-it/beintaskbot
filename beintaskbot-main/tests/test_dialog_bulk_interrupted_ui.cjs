const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
async function scenario(mixed=false){
 const dom=new JSDOM(fs.readFileSync('docs/tenant-app.html','utf8'),{url:'https://example.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window,d=w.document;
 const tick=()=>new Promise(r=>setTimeout(r,0));let posts=0,confirmations=0,reopened=false;
 const row=id=>({provider:'connect',lead_id:id,channel_id:'channel-'+id,conversation_id:'client-'+id,customer_name:'Synthetic '+id,agent_status:'manual',can_send:false,lifecycle_enabled:true,lifecycle:{state:'waiting',revision:reopened&&id===1?2:0,can_dismiss:true,last_inbound_id:'inbound-'+id}});
 w.tenantProfileSnapshot={member:{tenant_id:'tenant',telegram_id:33,role:'owner',active:true},capabilities:{modules:{dialogs:true,support:true}}};
 w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});w.alert=()=>{};w.confirm=()=>{confirmations++;return true;};
 w.fetch=async(url,options={})=>{
  if(String(url).endsWith('dialogs_lifecycle')){
   posts++;if(mixed&&posts===2)return {ok:false,status:409,json:async()=>({success:false,error:'Changed'})};
   if(posts===99)return {ok:false,status:503,json:async()=>({success:false,error:'Unavailable'})};
   assert(posts<99,'ambiguous failure must stop the batch, not retry');
   return {ok:true,status:200,json:async()=>({success:true,result:{lifecycle:{state:'dismissed',revision:1,operator_id:33}}})};
  }
  assert(String(url).includes('/dialogs?'),'unexpected request '+url);
  return {ok:true,status:200,json:async()=>({success:true,result:{completion_preview:true,dialogs:Array.from({length:100},(_,i)=>row(i+1)),channels:[],agents:[],unread_count:0,total_count:100,next_offset:null}})};
 };
 w.eval(fs.readFileSync('docs/tenant-operator-i18n.js','utf8'));w.tenantI18n.setLanguage('ru');w.eval(fs.readFileSync('docs/tenant-operator-dialogs.js','utf8'));
 await tick();await tick();await w.tenantOperator.refresh();
 const button=d.querySelector('#operatorCompleteAll');button.click();
 for(let n=0;n<100&&button.disabled;n++)await tick();
 assert.equal(posts,99);assert.equal(confirmations,1);assert(!button.disabled);
 assert.match(d.querySelector('#operatorBulkCompletionStatus').textContent,mixed?/^Операция прервана\. Сохранено: 97\. Пропущено: 1\. Результат не подтверждён: 1\. Не обработано: 1\./:/^Операция прервана\. Сохранено: 98\. Пропущено: 0\. Результат не подтверждён: 1\. Не обработано: 1\./);
 assert.equal(d.querySelectorAll('[data-lifecycle-state="waiting"]').length,mixed?3:2,'only skipped/untouched/uncertain routes remain New');
 await w.tenantOperator.refresh();assert.match(d.querySelector('#operatorBulkCompletionStatus').textContent,/^Операция прервана/,'refresh must preserve the interruption result');assert.equal(d.querySelectorAll('[data-lifecycle-state="waiting"]').length,mixed?3:2,'stale snapshots cannot undo saved completion');
 reopened=true;await w.tenantOperator.refresh();assert.equal(d.querySelectorAll('[data-lifecycle-state="waiting"]').length,mixed?4:3,'a newer genuine inbound revision must remain New');
 d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'other',telegram_id:44,active:true,role:'owner'},capabilities:{modules:{dialogs:true,support:true}}}}));assert.equal(d.querySelector('#operatorBulkCompletionStatus').textContent,'','new identity must not inherit result');await tick();await tick();dom.window.close();
}
(async()=>{await scenario();await scenario(true);console.log('PASS 98 saved +503 and 97 saved +409 +503, complete accounting, persistent status and genuine reopen badges');})().catch(error=>{console.error(error);process.exitCode=1;});
