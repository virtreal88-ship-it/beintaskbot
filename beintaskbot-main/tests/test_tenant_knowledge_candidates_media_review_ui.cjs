const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
const script=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8');
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{
 const dom=new JSDOM('<div id="supportCases"></div><span id="supportCasesPending"></span>',{url:'https://example.test/app?site_lang=en',runScripts:'outside-only'});
 const {window:w}=dom,document=d=w.document,requests=[];
 const id='11111111-1111-4111-8111-111111111111',message='22222222-2222-4222-8222-222222222222';
 let card={id,extraction_id:'extract-1',version:1,status:'needs_clarification',content_language:'en',title:'Old media based claim',symptoms:['Old media based symptom'],cause:{status:'confirmed',text:'Old cause'},cause_evidence:[{message_id:message,quote:'Old'}],resolution_steps:['Old fix'],outcome:{status:'confirmed',text:'Old confirmation',message_ids:[message]},source_message_ids:[message],start_message_id:message,end_message_id:message,resolution_method:'chat',operator_actions:['Old action'],operator_action_evidence:[],case_kind:'technical',work_status:'resolved',product:'Old product',product_version:'1',case_relation:'new_request',clarification_note:'',can_approve:false,approval_block_reason:'source_attachment_changed_review_required',media_source_diagnostics:[{id:message,channel_id:'channel-a',saved_kind:'image',current_kind:'image',saved_version:'old-hash',current_version:'new-hash',identity_fields:{mediaId:{original_value_retained:false,current_present:true}}}]};
 const evidence=[{id:message,channel_id:'channel-a',role:'customer',sender_name:'Customer',happened_at:'2026-10-19T10:00:00Z',text:'Customer attached a photo.',attachment_evidence_status:'stale_unverified'}];
 const route={provider:'connect',channel_id:'channel-a',conversation_id:'customer-a',lead_id:'lead-a'};
 w.tenantI18n={language:'en'};w.confirm=()=>true;w.TenantKnowledgeCampaigns={mount:async()=>{}};
 w.fetch=async(url,opt={})=>{
  requests.push({url:String(url),opt});let result={};
  if(String(url).includes('/source-review')&&opt.method==='POST'){
   card={...card,version:2,title:'Review the current support request',symptoms:['Review the current customer request before saving'],cause:{status:'unknown',text:''},cause_evidence:[],resolution_steps:[],outcome:{status:'unconfirmed',text:'',message_ids:[]},operator_actions:[],work_status:'unknown',media_source_diagnostics:[],source_claims_unverified:true,media_source_review:{revision:1,message_id:message,current_kind:'image',current_version:'new-hash',receipt_reused:true}};
   return {ok:true,status:200,json:async()=>({success:true,result:{candidate:card,evidence:[{...evidence[0],attachment_evidence_status:'owner_reviewed_new_source',text:'[Saved observation for owner-reviewed current attachment; untrusted] Fresh observation'}],route}})};
  }
  if(String(url).includes('/source-review?')){
   const parsed=new URL(url,'https://example.test');return {ok:true,status:200,json:async()=>({success:true,result:{build_marker:'source-review-preflight-v1',candidate_id:id,extraction_id:'extract-1',candidate_version:1,expected_version:Number(parsed.searchParams.get('expected_version')),message_id:parsed.searchParams.get('message_id'),in_case_evidence:true,in_extraction_media:true,version_matches:true,case_reviewable:true,status:'eligible',reason:'membership_and_version_match'}})};
  }
  if(String(url).includes('/knowledge-candidates/')) result={candidate:card,evidence,route};
  else if(String(url).includes('/knowledge-candidates')) result={candidates:[card],next_before:null};
  else if(String(url).includes('/ai/channels')) result={channels:[]};
  else if(String(url).includes('/ai/agents')) result={agents:[]};
  else if(String(url).includes('/ai/dialogs/history')) result={messages:[],next_before:null};
  else if(String(url).includes('/ai/dialogs')) result={dialogs:[]};
  return {ok:true,status:200,json:async()=>({success:true,result})};
 };
 w.eval(script);d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'tenant-a',telegram_id:'42',role:'owner'}}}));await tick();await tick();
 d.querySelector('[data-case-open]').click();await tick();await tick();
 assert(d.querySelector('[data-case-media-stale]'));
 assert(d.querySelector('[data-source-review-form]'));
 assert.match(d.querySelector('[data-source-review-preflight]').textContent,/case=true; extraction_media=true; version=1\/1/);
 assert.match(d.querySelector('[data-case-media-stale]').textContent,/No link between the versions/);
 assert.equal(d.querySelector('[data-case-save]').disabled,true);
 d.querySelector('[data-source-review-ack]').checked=true;
 d.querySelector('[data-source-review-note]').value='Reviewed current image as a separate source.';
 d.querySelector('[data-source-review-save]').click();await tick();await tick();
 const review=requests.find(request=>request.url.includes('/source-review')&&request.opt.method==='POST');
 assert(review);const payload=JSON.parse(review.opt.body);
 const preflight=requests.find(request=>request.url.includes('/source-review?')&&request.opt.method===undefined);assert(preflight,'must perform a read-only preflight before the review write');
 const preflightQuery=new URL(preflight.url,'https://example.test').searchParams;assert.equal(preflightQuery.get('message_id'),message);assert.equal(preflightQuery.get('expected_version'),'1');assert.equal(preflightQuery.get('expected_tenant_id'),'tenant-a');assert.equal(preflightQuery.get('expected_user_id'),'42');assert(requests.indexOf(preflight)<requests.indexOf(review));
 assert.equal(payload.expected_version,1);assert.equal(payload.message_id,message);assert.equal(payload.acknowledge_new_source,true);
 assert.equal(d.querySelector('[data-case-media-stale]'),null);
 assert(d.querySelector('[data-case-media-reviewed]'));
 assert.equal(d.querySelector('[data-case-save]').disabled,false);
 assert.equal(d.querySelector('[data-case-approve]').disabled,true);
 assert.match(d.querySelector('#caseTitle').value,/Review the current support request/);
 w.close();console.log('PASS: owner-only explicit new-source review, editable reset claims, and publication remains blocked.');
})().catch(error=>{console.error(error);process.exitCode=1;});
