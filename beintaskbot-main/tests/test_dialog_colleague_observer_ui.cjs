const {JSDOM}=require('jsdom'),fs=require('node:fs'),assert=require('node:assert/strict');
const tick=()=>new Promise(r=>setTimeout(r,0));
(async()=>{
 const dom=new JSDOM(fs.readFileSync('docs/tenant-app.html','utf8'),{
   url:'https://example.test/app?view=dialogs',runScripts:'outside-only'
 });
 const w=dom.window,d=w.document,find=id=>d.getElementById(id),calls=[];
 const route={provider:'connect',lead_id:41,channel_id:'source',conversation_id:'peer',customer_name:'Synthetic name',
  lifecycle_enabled:true,lifecycle:{state:'accepted',operator_id:'44',revision:2},colleague_read_only:true,archive_read_only:true};
 w.tenantProfileSnapshot={member:{tenant_id:'tenant',telegram_id:33,role:'operator',active:true},capabilities:{modules:{dialogs:true}}};
 w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
 let assigned=true,ack=0;
 w.tenantSharedReminders={markRows(rows){assert(!rows.flat().some(row=>row.colleague_read_only));},dialogOpened(){ack++;}};
 w.fetch=async(target,options={})=>{
  const url=new URL(target,w.location),q=url.searchParams,p=url.pathname;
  calls.push({p,q,method:options.method});
  let result={};
  if(p.endsWith('/me'))return {ok:true,json:async()=>w.tenantProfileSnapshot};
  if(p.endsWith('/dialogs')&&q.has('colleague_options'))result={enabled:true,operators:[{id:'44',name:'Synthetic colleague'}]};
  else if(p.endsWith('/dialogs'))result={dialogs:q.has('observed_operator_id')&&assigned?[route]:[],channels:[],agents:[],next_offset:null};
  else if(p.endsWith('/history')){
   assert.equal(q.get('observed_operator_id'),'44');assert.equal(q.get('expected_tenant_id'),'tenant');
   assert.equal(q.has('shared_reminder_events'),false);assert.equal(q.has('paired_history'),false);
   if(!assigned)return {ok:false,status:404,json:async()=>({success:false,error:'Transferred'})};
   result={dialog:route,messages:[{id:'m1',source:'connect',message_type:'text',direction:'incoming',body:'Synthetic chat'}],next_before:null};
  }
  return {ok:true,status:200,json:async()=>({success:true,result})};
 };
 try{
  for(const file of ['tenant-operator-i18n.js','tenant-operator-dialogs.js'])w.eval(fs.readFileSync('docs/'+file,'utf8'));
  await w.tenantOperator.refresh();
  find('operatorFiltersToggle').click();await tick();
  assert.equal(find('operatorColleague').value,'');
  assert.equal(find('operatorColleagueLabel').hidden,false);
  find('operatorColleague').value='44';find('operatorColleague').dispatchEvent(new w.Event('change'));
  await tick();
  d.querySelector('.operator-row').click();await tick();
  assert.match(find('chatRows').textContent,/Synthetic chat/);
  assert(!find('chatText')&&!find('sendChat')&&find('operatorMarkAllRead').hidden);
  assigned=false;
  await w.tenantOperator.refresh();
  assert(!find('chatRows'),'transferred dialogues must disappear from observer history');
  find('operatorColleague').value='';find('operatorColleague').dispatchEvent(new w.Event('change'));
  await tick();
  assert(!find('operatorMarkAllRead').hidden);
  assert.equal(ack,0);
  assert(!calls.some(c=>c.method==='POST'&&/read|reminder|send/.test(c.p)));
  console.log('PASS colleague filter: default self, view only, scope, transfer, no read writes');
 }finally{w.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
