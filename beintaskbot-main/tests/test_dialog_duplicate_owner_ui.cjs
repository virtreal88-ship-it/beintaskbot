const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://fixture.test/app?view=dialogs',runScripts:'outside-only'}),w=dom.window;
const owner={member:{tenant_id:'00000000-0000-0000-0000-000000000101',telegram_id:33,role:'owner',permissions:['dialogs'],active:true},capabilities:{modules:{dialogs:true}}};
const worker={...owner,member:{...owner.member,role:'worker'}};
const channel='00000000-0000-0000-0000-000000000201';
const row=(n,extra={})=>({provider:'connect',channel_id:channel,lead_id:n,conversation_id:`10000000000${n}@lid`,customer_kind:'contact',customer_name:`Synthetic ${n}`,phone:'',channel_name:'Private workspace',avatar_status:'unavailable',...extra});
const first=row(1,{candidate_group:1,customer_name:'<img src=x onerror=alert(1)>',phone:'+994500000001'});
const second=row(2,{candidate_group:1,phone:'+994500000001'});
const more=[row(4,{candidate_group:2,customer_name:'Other A'}),row(5,{candidate_group:2,customer_name:'Other B'})];
const missing=row(3,{customer_name:'100000000003@lid'});
let profile=worker,pending;
const seen=[];
w.tenantProfileSnapshot=profile;w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}});
w.document.getElementById('view-dialogs').classList.add('active');
const response=result=>({ok:true,json:async()=>({success:true,result})});
w.fetch=async (target,options)=>{
 const url=new URL(target,w.location.origin),resource=url.pathname.split('/dialogs')[1];
 const body=options?.body?JSON.parse(options.body):null;
 if(resource==='/duplicates'){
   seen.push({resource,method:options.method,body,search:url.search});
   if(body.mode==='name')return new Promise(resolve=>pending=()=>resolve(response({dialogs:[row(9)],next_offset:null})));
   return response({dialogs:body.mode==='missing'?[missing]:[first,second,...more],next_offset:null});
 }
 if(resource==='/duplicates/preview'){
   seen.push({resource,body});return response({eligible:true,pair_id:'synthetic-pair',precondition:'synthetic-precondition',
     canonical_lead_id:2,split_lead_id:1,phone:'+994500000001',name_choices:[{lead_id:1,display_name:'First name'},{lead_id:2,display_name:'Second name'}]});
 }
 if(resource==='/duplicates/merge'){
   seen.push({resource,body});return response({merged:true});
 }
 if(resource==='/contact-display-phone'){
   seen.push({resource,body});return response({display_phone:'+994500000009'});
 }
 if(resource==='/history'){
   seen.push({resource,lead:url.searchParams.get('lead_id')});return response({dialog:missing,messages:[],next_before:null});
 }
 if(resource==='')return response({dialogs:[],channels:[],agents:[],total_count:0,unread_count:0,next_offset:null});
 return {ok:true,json:async()=>({...profile,success:true})};
};
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
const $=sel=>w.document.querySelector(sel);
const emitProfile=value=>{profile=value;w.document.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:value}));};
const submit=(form,value)=>{if(value!==undefined)form.querySelector('input').value=value;form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));};
(async()=>{
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await settle();
 assert.equal($('#operatorFindDuplicates').hidden,true);
 $('#operatorFindDuplicates').click();assert.equal($('#operatorDuplicateSearch'),null);
 emitProfile(owner);await settle();
 assert.equal($('#operatorFindDuplicates').hidden,false);$('#operatorFindDuplicates').click();
 assert.equal($('[data-dupe-mode="phone"]').checked,true);assert.equal($('[data-dupe-mode="name"]').checked,false);
 assert.equal($('[data-dupe-form] input[type="search"]'),null);assert.equal(seen.length,0,'no automatic discovery');
 submit($('[data-dupe-form]'));await settle();
 assert.deepEqual(seen[0],{resource:'/duplicates',method:'POST',search:'',body:{mode:'phone',offset:0,expected_tenant_id:owner.member.tenant_id,expected_user_id:33}});
 assert.match($('[data-dupe-count]').textContent,/1.*2/);
 assert.equal($('[data-dupe-results] img'),null,'provider name must remain text');
 assert.doesNotMatch($('[data-dupe-results]').innerHTML,/@lid|Other A/);
 $('[data-dupe-next]').click();assert.match($('[data-dupe-count]').textContent,/2.*2/);
 assert.match($('[data-dupe-results]').textContent,/Other A/);
 assert.equal(w.document.querySelectorAll('[data-dupe-open]').length,2,'only one candidate group is shown');
 $('[data-dupe-prev]').click();assert.match($('[data-dupe-count]').textContent,/1.*2/);
 $('[data-dupe-preview]').click();await settle();
 assert.equal(seen.filter(req=>req.resource==='/duplicates/merge').length,0,'preview must not merge');
 assert.equal(w.document.querySelectorAll('[data-dupe-compare]').length,2,'review compares side by side');
 assert.equal($('[data-dupe-name]').value,'2','server retained route controls defaults');
 $('[data-dupe-name]').value='1';$('[data-dupe-name]').dispatchEvent(new w.Event('change'));
 $('[data-dupe-confirm]').click();await settle();
 const merge=seen.find(req=>req.resource==='/duplicates/merge');
 assert.equal(merge.body.confirm,true);assert.equal(merge.body.name_lead_id,1);assert.equal(merge.body.phone_lead_id,2);
 assert(merge.body.request_id&&merge.body.precondition==='synthetic-precondition');
 assert.equal($('#operatorDuplicateSearch'),null);
 $('#operatorFindDuplicates').click();$('[data-dupe-mode="phone"]').checked=false;submit($('[data-dupe-form]'));await settle();
 assert.equal(seen.findLast(req=>req.resource==='/duplicates').body.mode,'missing');
 assert.equal($('[data-dupe-pick]'),null,'missing mode never selects merge pairs');
 const phoneForm=$('[data-dupe-phone-form]');submit(phoneForm,'+994500000009');await settle();
 const manual=seen.find(req=>req.resource==='/contact-display-phone');
 assert.equal(manual.body.phone,'+994500000009');assert.equal(manual.body.lead_id,3);
 submit($('[data-dupe-form]'));await settle();$('[data-dupe-open]').click();await settle();
 assert(seen.some(req=>req.resource==='/history'&&req.lead==='3'),'missing result opens exact dialog');
 $('#operatorClientCardOpen').click();await settle();
 const edit=$('#operatorManualDisplayPhone');
 assert(edit,'owner can add display-only phone in existing contact card');
 submit(edit,'+994500000009');await settle();
 assert.equal(seen.filter(req=>req.resource==='/contact-display-phone').length,2);
 assert.equal($('[data-panel-phone]').textContent,'+994500000009');
 $('#operatorFindDuplicates').click();$('[data-dupe-mode="name"]').checked=true;submit($('[data-dupe-form]'));await settle();
 assert.equal(seen.findLast(req=>req.resource==='/duplicates').body.mode,'both');
 $('[data-dupe-mode="phone"]').checked=false;submit($('[data-dupe-form]'));await settle();
 assert.equal(seen.findLast(req=>req.resource==='/duplicates').body.mode,'name');
 emitProfile(worker);pending();await settle();assert.equal($('#operatorDuplicateSearch'),null);
 console.log('PASS: owner-only duplicate modal, no query in URLs, preview/confirm, missing navigation and display-only phone, profile race.');
 dom.window.close();
})().catch(err=>{console.error(err);dom.window.close();process.exitCode=1;});
