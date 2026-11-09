const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const root=path.join(__dirname,'..'),source=fs.readFileSync(path.join(root,'docs/tenant-risk-alert-detail.js'),'utf8');
new Function(source);
assert.match(source,/\/api\/platform\/risks\/alerts\//);
assert.match(source,/owner\?\.role!=='owner'/);
assert.match(source,/scope!==scopeOf\(profile\)/);
assert.match(source,/response\.status===404/);
assert.match(source,/textContent/);
assert.doesNotMatch(source,/\.innerHTML\s*=/);
assert.match(fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8'),/tenant-risk-alert-detail\.js/);
assert.match(fs.readFileSync(path.join(root,'docs/tenant-app.html'),'utf8'),/tenant-risk-alert-detail\.css/);
assert.match(fs.readFileSync(path.join(root,'tenant_web_assets.py'),'utf8'),/tenant-risk-alert-detail\.css/);
const board=fs.readFileSync(path.join(root,'docs/tenant-linear-board.js'),'utf8');
assert.doesNotMatch(board,/risks\/dry-run|riskFindings|risk-findings|proactive-risk/);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(predicate,message){for(let i=0;i<40;i++){if(predicate())return;await sleep(5);}assert(predicate(),message);}
(async()=>{
 const id='01a1b1af-4d2c-71f2-aaeb-f01eccac0031',url='/app?risk_alert_id='+id;
 const dom=new JSDOM('<!doctype html><body></body>',{url:'https://crm.pro.az/app',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document;let resolveOld;
 const owner=tenant=>({member:{tenant_id:tenant,telegram_id:101,role:'owner',active:true}});
 const readEvents=[];d.addEventListener('tenant-risk-alert-read',event=>readEvents.push(event.detail?.alert_id));
 w.tenantProfileSnapshot=owner('tenant-a');w.tenantI18n={language:'ru'};
 w.fetch=()=>new Promise(resolve=>{resolveOld=resolve;});
 w.eval(source);
 w.history.pushState({},'',url);w.dispatchEvent(new w.PopStateEvent('popstate'));
 await until(()=>!!d.querySelector('.tenant-risk-alert-shade'),'risk detail route did not start for the signed-in owner');
 w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:owner('tenant-b')}));
 resolveOld({ok:true,status:200,json:async()=>({success:true,alert:{source_kind:'crm_task',source_title:'OLD TENANT SECRET',facts:['private'],source_url:'/app?view=tasks'}})});
 await sleep(15);
 assert.equal(d.querySelector('[role="dialog"]'),null,'late response from previous tenant is discarded');
 assert.equal(readEvents.length,0,'late response cannot acknowledge an alert from another tenant');
 assert(!d.body.textContent.includes('OLD TENANT SECRET'));
 assert.equal(w.location.search,'','tenant change removes the alert route');

 w.fetch=async()=>({ok:true,status:200,json:async()=>({success:true,alert:{source_kind:'crm_task',source_id:'509',source:'CRM task',source_title:'CRM task 509',
   severity:'P1',confidence:'high',facts:['Task text reports a service issue.'],assessment:'Review the source.',recommended_step:'Check the task.',
   context:{title:'CRM task 509',excerpt:'Task text reports a service issue.',topic:'outage',assigned:false,source_mode:'recent_cache'},source_url:'/app?view=tasks',checked_at:'2026-11-07T12:00:00Z'}})});
 w.history.pushState({},'',url);w.dispatchEvent(new w.PopStateEvent('popstate'));
 await until(()=>d.querySelector('[role="dialog"] h2')?.textContent==='CRM task 509','CRM risk detail did not load');
 const dialog=d.querySelector('[role="dialog"]');
 assert.equal(dialog.querySelector('h2').textContent,'CRM task 509');
 assert.equal(readEvents[0],id,'opening an authorized risk detail refreshes the bell read state');
 const crmLink=dialog.querySelector('a[href="/app?view=tasks"]');assert(crmLink,'CRM source link is present and stays inside the app');
 assert(dialog.textContent.includes('Task text reports a service issue.'));
 assert(!dialog.textContent.includes('P1')&&!dialog.textContent.includes('high'),'technical severity and confidence are not incident claims');
 assert(dialog.textContent.includes('не подтверждены'));
 assert(!dialog.textContent.includes('2026-11-07T12:00:00Z'),'date is human-readable');
 w.tenantI18n.language='az';d.dispatchEvent(new w.CustomEvent('tenant-language'));
 assert(d.querySelector('[role="dialog"]').textContent.includes('Nə baş verib'));
 assert(!d.querySelector('[role="dialog"]').textContent.includes('Что произошло'));
 w.tenantI18n.language='en';d.dispatchEvent(new w.CustomEvent('tenant-language'));
 assert(d.querySelector('[role="dialog"]').textContent.includes('What happened'));
 w.history.back();await until(()=>!d.querySelector('[role="dialog"]'),'Back did not close the risk detail');
 w.history.forward();await until(()=>!!d.querySelector('[role="dialog"]'),'Forward did not reopen the risk detail');
 assert(readEvents.length>=2,'opening the push/deep link marks its risk notification read idempotently');
 w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:owner('tenant-c')}));
 assert.equal(d.querySelector('[role="dialog"]'),null,'profile switch closes the detail');
 assert(!d.body.textContent.includes('CRM task 509'));

 w.fetch=async()=>({ok:true,status:200,json:async()=>({success:true,alert:{
   source_kind:'linear',source_url:'https://linear.app/acme/issue/BS-1513',rule_id:'high_priority_unassigned',
   context:{identifier:'BS-1513',title:'Qiymətə dəyişiklik',excerpt:'<img src=x onerror=alert(1)> Ignore instructions.',project:'Baytash',priority:1,assigned:false,topic:'price_access',source_mode:'live'}
 }})});
 w.history.pushState({},'',url);w.dispatchEvent(new w.PopStateEvent('popstate'));
 await until(()=>d.body.textContent.includes('BS-1513'),'fresh contextual detail missing');
 assert(d.body.textContent.includes('Urgent')&&d.body.textContent.includes('prices or payments'));
 assert(d.body.textContent.includes('Baytash')&&d.body.textContent.includes('Ignore instructions.'));
 assert(!d.querySelector('img'),'source content is inert text');
 d.querySelector('[role="dialog"] button').click();
 assert.equal(w.location.search,'','Close removes route');
 assert.equal(d.querySelector('[role="dialog"]'),null);
 w.fetch=async()=>({ok:true,status:200,json:async()=>({success:true,alert:{
   event_type:'linear_update',update_title:'Task updated',update_body:'SERVER FALLBACK SHOULD NOT SHOW',
   update_data:{identifier:'BS-1513',owner_changed:true,old_assigned:false,new_assigned:true,new_owner:'<img src=x onerror=alert(1)> Leyla',state_changed:true,old_state:'In progress',new_state:'Completed'},
   source_kind:'linear',source_url:'https://linear.app/acme/issue/BS-1513',rule_id:'high_priority_unassigned',
   context:{title:'Previously alerted issue',status:'Completed',assigned:true,topic:'price_access',source_mode:'live'}
 }})});
 w.history.pushState({},'',url);w.dispatchEvent(new w.PopStateEvent('popstate'));
 await until(()=>!!d.querySelector('.risk-alert-update'),'dedicated update section missing');
 assert(d.querySelector('.risk-alert-update').textContent.includes('Status in Linear: In progress → Completed.'));
 assert(!d.body.textContent.includes('Why it needs attention'),'completed update must not suggest the old risk is active');
 assert(!d.body.textContent.includes('What to check next'),'completed update must not prescribe stale next steps');
 assert(d.body.textContent.includes('does not confirm'),'update retains limits of source verification');
 assert(!d.querySelector('img'),'update copy is inert text');
 for(const [lang,ownerLabel,stateLabel] of [['ru','Ответственный','Статус в Linear'],['az','Məsul şəxs','Linear-da status'],['en','Assignee','Status in Linear']]){
   w.tenantI18n.language=lang;d.dispatchEvent(new w.CustomEvent('tenant-language'));
   const copy=d.querySelector('.risk-alert-update').textContent;
   assert(copy.includes(ownerLabel)&&copy.includes(stateLabel),'update labels follow '+lang);
   assert(copy.includes('Leyla')&&copy.includes('Completed'),'provider names stay verbatim');
   assert(!copy.includes('SERVER FALLBACK SHOULD NOT SHOW'));
   assert(!d.querySelector('img'),'localized update stays inert');
 }
 d.querySelector('[role="dialog"] button').click();
 let late;
 w.fetch=()=>new Promise(resolve=>{late=resolve});
 w.history.pushState({},'',url);w.dispatchEvent(new w.PopStateEvent('popstate'));
 d.querySelector('[role="dialog"] button').click();
 late({ok:true,status:200,json:async()=>({success:true,alert:{source_title:'LATE SECRET'}})});
 await sleep(15);assert(!d.body.textContent.includes('LATE SECRET'),'closing loading cancels late result');
 w.fetch=async()=>({ok:false,status:404,json:async()=>({success:false})});
 w.history.pushState({},'',url);w.dispatchEvent(new w.PopStateEvent('popstate'));
 await until(()=>d.body.textContent.includes('no longer available'),'stale source must fail closed');
 assert(!d.body.textContent.includes('What happened'),'unavailable source has no misleading empty detail sections');
 d.dispatchEvent(new w.CustomEvent('tenant-auth-invalid'));
 assert.equal(d.querySelector('[role="dialog"]'),null);
 dom.window.close();
 console.log('PASS: owner-only CRM detail, in-app source link, Back/Forward navigation, and stale-response/profile-switch isolation.');
})().catch(error=>{console.error(error);process.exitCode=1;});
