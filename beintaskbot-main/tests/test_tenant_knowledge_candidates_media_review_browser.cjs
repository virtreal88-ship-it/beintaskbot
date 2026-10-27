const fs=require('node:fs');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.SUPPORT_CASES_PLAYWRIGHT||'playwright');
const app=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8');
const host=fs.readFileSync('docs/tenant-app.html','utf8');
const styles=[...host.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(match=>match[1]).join('\n');
const id='11111111-1111-4111-8111-111111111111';
const target='22222222-2222-4222-8222-222222222222';
const unrelated='33333333-3333-4333-8333-333333333333';
const saved='a'.repeat(64),current='b'.repeat(64);
const evidence=[{id:target,channel_id:'channel-a',role:'customer',sender_name:'Customer',happened_at:'2026-10-19T10:00:00Z',text:'Customer attached a photo.',attachment_evidence_status:'stale_unverified'}];
const route={provider:'connect',channel_id:'channel-a',conversation_id:'customer-a',lead_id:'lead-a'};

(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  for(const viewport of [{width:1440,height:900,name:'desktop'},{width:390,height:844,name:'mobile'}]){
   const page=await browser.newPage({viewport});
   const candidate={id,extraction_id:'extract-1',version:1,status:'needs_clarification',content_language:'en',title:'Old media based claim',symptoms:['Old media based symptom'],cause:{status:'confirmed',text:'Old cause'},cause_evidence:[{message_id:target,quote:'Old'}],resolution_steps:['Old fix'],outcome:{status:'confirmed',text:'Old confirmation',message_ids:[target]},source_message_ids:[target],start_message_id:target,end_message_id:target,resolution_method:'chat',operator_actions:['Old action'],operator_action_evidence:[],case_kind:'technical',work_status:'resolved',product:'Old product',product_version:'1',case_relation:'new_request',clarification_note:'',can_approve:false,approval_block_reason:'source_attachment_changed_review_required',media_coverage:{complete:false,total:1,boundary_status:'verified',scope:'case_interval'},media_source_diagnostics:[
    {id:unrelated,channel_id:'channel-a',saved_kind:'image',current_kind:'image',saved_version:saved,current_version:current,identity_fields:{}},
    {id:target,channel_id:'channel-a',saved_kind:'image',current_kind:'image',saved_version:saved,current_version:current,identity_fields:{}}
   ]};
   await page.setContent('<html><head></head><body><div id="supportCases"></div><span id="supportCasesPending"></span></body></html>');
   await page.addStyleTag({content:styles});
   await page.evaluate(({candidate,evidence,route})=>{
    window.tenantI18n={language:'en'};window.confirm=()=>true;window.TenantKnowledgeCampaigns={mount:async()=>{}};window.__requests=[];
    window.fetch=async(url,opt={})=>{const path=String(url);window.__requests.push({url:path,opt});let result={};
     if(path.includes('/source-review')&&opt.method==='POST'){
      const body=JSON.parse(opt.body);result={candidate:{...candidate,version:2,media_source_diagnostics:[],media_source_review:{revision:1,message_id:body.message_id}},evidence,route};
     } else if(path.includes('/source-review?')){
      const query=new URL(path,'https://example.test').searchParams;result={build_marker:'source-review-preflight-v1',candidate_id:id,extraction_id:'extract-1',candidate_version:1,expected_version:Number(query.get('expected_version')),message_id:query.get('message_id'),in_case_evidence:true,in_extraction_media:true,version_matches:true,case_reviewable:true,status:'eligible',reason:'membership_and_version_match'};
     } else if(path.includes('/knowledge-candidates/'))result={candidate,evidence,route};
     else if(path.includes('/knowledge-candidates'))result={candidates:[candidate],next_before:null};
     else if(path.includes('/ai/channels'))result={channels:[]};else if(path.includes('/ai/agents'))result={agents:[]};
     else if(path.includes('/ai/dialogs/history'))result={messages:[],next_before:null};else if(path.includes('/ai/dialogs'))result={dialogs:[]};
     return {ok:true,status:200,json:async()=>({success:true,result})};
    };
   },{candidate,evidence,route});
   await page.addScriptTag({content:app});
   await page.evaluate(()=>document.dispatchEvent(new CustomEvent('tenant-profile',{detail:{member:{tenant_id:'tenant-a',telegram_id:'42',role:'owner'}}})));
   await page.locator('[data-case-open]').waitFor();await page.locator('[data-case-open]').click();
   const form=page.locator('[data-source-review-form]');await form.waitFor();
   assert.equal(await form.locator('[data-source-review-message] option').count(),1,`${viewport.name}: excludes extraction media outside case claims`);
   assert.equal(await form.locator('[data-source-review-message]').inputValue(),target,`${viewport.name}: defaults to the case attachment`);
   assert.equal(await page.locator('[data-case-media-stale]').evaluate(node=>node.parentElement.classList.contains('case-content-column')),true,`${viewport.name}: stale warning is in the scrollable case column`);
   await page.locator('[data-source-review-note]').fill('Reviewed this current photo as a separate source.');
   if(viewport.name==='desktop'){
    await form.locator('[data-source-review-ack]').locator('xpath=..').click();
   }else{
    await page.locator('[data-case-open]').focus();let found=false;
    for(let i=0;i<80;i++){await page.keyboard.press('Tab');if(await form.locator('[data-source-review-ack]').evaluate(node=>node===document.activeElement)){found=true;break;}}
    assert(found,'mobile: normal Tab navigation reaches the acknowledgement');await page.keyboard.press('Space');
   }
   assert.equal(await form.locator('[data-source-review-ack]').isChecked(),true,`${viewport.name}: acknowledgement can be toggled by normal input`);
   await form.locator('[data-source-review-save]').click();
   await page.waitForFunction(()=>window.__requests.some(item=>item.url.includes('/source-review')&&item.opt.method==='POST'));
   const post=await page.evaluate(()=>window.__requests.find(item=>item.url.includes('/source-review')&&item.opt.method==='POST'));
   const payload=JSON.parse(post.opt.body);assert.equal(payload.message_id,target,`${viewport.name}: only case-evidence attachment is submitted`);assert.equal(payload.acknowledge_new_source,true);
   const preflight=await page.evaluate(()=>window.__requests.find(item=>item.url.includes('/source-review?')));assert(preflight,`${viewport.name}: owner preflight runs before POST`);assert(new URL(preflight.url,'https://example.test').searchParams.get('message_id')===target);
   await page.locator('[data-case-media-reviewed]').waitFor();
   console.log(`PASS ${viewport.name}: long hashes, selectable case source, acknowledgement, submit and reviewed-state layout`);
   await page.close();
  }
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
