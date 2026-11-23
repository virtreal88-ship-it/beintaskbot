const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('../.support-test-deps/node_modules/jsdom');
const read=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const dom=new JSDOM(read('docs/tenant-app.html'),{url:'https://fixture.test/app?view=dialogs',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window;
const profile={member:{tenant_id:'00000000-0000-0000-0000-000000000101',telegram_id:33,role:'worker',permissions:['dialogs']},capabilities:{modules:{dialogs:true}}};
const channel='00000000-0000-0000-0000-000000000201';
const otherChannel='00000000-0000-0000-0000-000000000202';
const peer='123456789012345@lid',oldRevision='a'.repeat(64),newRevision='b'.repeat(64);
const row=(n,extra={})=>({provider:'connect',lead_id:n,channel_id:channel,conversation_id:peer,contact_source_revision:oldRevision,customer_kind:'contact',customer_name:peer,avatar_status:'unavailable',last_message_at:'2026-10-23T10:00:00Z',agent_status:'manual',...extra});
const rows=[
  row(1,{phone:'+994500000001',customer_name:'Stored provider name'}),
  row(2,{customer_name:'Provider display name'}),
  row(3,{channel_id:otherChannel}),
  row(4,{conversation_id:'123456@g.us',conversation_kind:'group',customer_kind:'group',customer_name:'Synthetic group',group_participant_count:3}),
  row(5,{customer_name:'123456789012345'}),
  row(6,{conversation_id:'654321@lid'}),
  row(7,{conversation_id:'765432@lid',customer_name:'@actual_provider_handle'}),
  row(8,{conversation_id:'876543@lid',customer_name:'876543',manual_display_name:'876543',phone:'+994500000008'}),
  row(9,{contact_identity_conflict:true}),
  row(10,{contact_source_revision:''}),
];
const requests=[],history=[],intersect=new Map();
let mobile=false;
w.matchMedia=()=>({matches:mobile,addListener(){},removeListener(){}});
w.HTMLElement.prototype.getClientRects=function(){return this.dataset.openOperator!==undefined?[this.getBoundingClientRect()]:[];};
w.HTMLElement.prototype.getBoundingClientRect=function(){return {top:10,left:10,bottom:20,right:20,width:10,height:10};};
w.IntersectionObserver=class{
  constructor(callback){this.callback=callback;}
  observe(target){
    intersect.set(target.dataset.openOperator,value=>this.callback([{target,isIntersecting:value}]));
    // The last cards' visibility is controlled by the test.
    if(['6','7'].includes(target.dataset.openOperator))return;
    this.callback([{target,isIntersecting:true}]);
  }
  unobserve(){}
  disconnect(){}
};
w.tenantProfileSnapshot=profile;
w.document.getElementById("view-dialogs").classList.add("active");
const reply=result=>({ok:true,json:async()=>({success:true,result})});
w.fetch=async (target,options)=>{
 const url=new URL(target,w.location.origin);
 if(url.pathname.endsWith('/dialogs/contact-phone')){
   const route=Object.fromEntries(url.searchParams);
   return new Promise(resolve=>requests.push({route,signal:options.signal,finish(details){resolve(reply({...route,status:'available',display_only:true,...details}));},deny(){resolve({ok:false,status:403,json:async()=>({success:false,error:'route denied'})});}}));
 }
 if(url.pathname.endsWith('/dialogs/history'))history.push(url);
 if(url.pathname.endsWith('/dialogs'))return reply({dialogs:rows,channels:[],agents:[],unread_count:0,total_count:rows.length,next_offset:null});
 return {ok:true,json:async()=>({success:true,...profile})};
};
const settle=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
const field=(i,name)=>w.document.querySelector(`[data-open-operator="${i}"] .operator-row-${name}`).textContent;
(async()=>{
 w.eval(read('docs/tenant-operator-i18n.js'));w.eval(read('docs/tenant-operator-dialogs.js'));await settle();
 assert.equal(field(0,'sub'),'+994500000001','stored phone is printed before asynchronous lookup');
 assert.equal(field(1,'title'),'Provider display name');
 assert.equal(field(3,'title'),'Synthetic group');
 assert.match(field(3,'sub'),/^3/);
 assert.doesNotMatch(field(4,'title'),/123456789012345|@lid/);
 assert.equal(field(6,'title'),'@actual_provider_handle');
 assert.equal(field(7,'title'),'+994500000008','verified phone replaces opaque numeric name');
 assert.equal(field(7,'sub'),'','do not print the same number twice');
 assert.deepEqual(requests.map(r=>r.route.lead_id),['2','3'],'visible exact-route lookups are capped at two in flight');
 intersect.get('5')(false); // queued, then scrolled away before either slot freed
 requests[0].finish({phone:'+994500000002',display_name:'Alternate provider name'});await settle();
 assert.equal(field(1,'sub'),'+994500000002');
 assert.equal(field(1,'title'),'Provider display name');
 assert.equal(field(2,'sub'),'','same LID in a different channel does not reuse a phone');
 assert.equal(requests[2].route.lead_id,'5');
 requests[1].finish({phone:'+994500000003',channel_id:channel,display_name:'Misrouted name'});await settle();
 assert.equal(field(2,'sub'),'');
 assert.doesNotMatch(field(2,'title'),/Misrouted|@lid/);
 assert.equal(requests.length,3,'queued row that scrolled away did not get a transport');
 intersect.get('5')(true);await settle();
 assert.equal(requests[3].route.lead_id,'6','returning to the viewport makes it eligible again');
 requests[2].deny();requests[3].finish({display_name:'@actual_device_handle'});await settle();
 assert.equal(field(4,'sub'),'','optional route denial does not clear the authorized list');
 assert.doesNotMatch(field(4,'title'),/123456789012345|@lid/);
 assert.equal(field(5,'title'),'@actual_device_handle');
 assert.equal(requests.length,4,'no lookups for group, verified complete, or offscreen row');
 intersect.get('6')(true);await settle();
 assert.equal(requests[4].route.lead_id,'7','only now is the previously offscreen row looked up');
 intersect.get('6')(false);await settle();
 assert(requests[4].signal.aborted,'transport is cancelled when a visible row leaves the viewport');
 requests[4].finish({phone:'+994500000007',display_name:'Offscreen identity'});await settle();
 assert.equal(field(6,'title'),'@actual_provider_handle');
 mobile=true;
 w.document.getElementById('operatorDialogs').dataset.conversationOpen='true';
 intersect.get('7')(true);await settle();
 assert.equal(requests.length,5,'an open mobile conversation hides list lookup');
 mobile=false;
 delete w.document.getElementById('operatorDialogs').dataset.conversationOpen;
 await w.tenantOperator.refresh();await settle();
 assert.equal(requests.length,5,'refresh does not repeat recent route lookups');
 assert.equal(field(1,'sub'),'+994500000002');
 rows[1].contact_source_revision=newRevision;
 await w.tenantOperator.refresh();await settle();
 assert.equal(field(1,'sub'),'','a changed source generation discards cached verified phone');
 const revised=requests.findLast(r=>r.route.lead_id==='2');
 assert.equal(revised.route.contact_source_revision,newRevision,'the lookup sends the generation from the exact row');
 revised.finish({contact_source_revision:oldRevision,phone:'+994500000099'});
 await settle();assert.equal(field(1,'sub'),'','late reply from old source generation is ignored');
 intersect.get('6')(true);await settle();
 const pending=requests.findLast(r=>r.route.lead_id==='7');
 assert(!pending.signal.aborted);
 w.document.dispatchEvent(new w.CustomEvent('tenant-auth-pending'));await settle();
 assert(pending.signal.aborted,'auth transition cancels optional visible-row traffic');
 pending.finish({phone:'+994500000007'});await settle();
 assert.equal(field(6,'sub'),'','cancelled transport cannot alter the list');
 assert.equal(history.length,0,'contact identity appears before any chat is opened');
 console.log('PASS: list-only verified WhatsApp identity, provider fallback, scope, visibility and lookup bounds.');
 dom.window.close();
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
