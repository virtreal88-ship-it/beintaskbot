const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.SUPPORT_CASES_JSDOM||'jsdom');
const script=fs.readFileSync('docs/tenant-knowledge-candidates.js','utf8');
const tick=()=>new Promise(r=>setTimeout(r,0));
(async()=>{for(const language of ['ru','az','en']){
 const dom=new JSDOM('<div id="supportCases"></div><span id="supportCasesPending"></span>',{url:'https://example.test/app',runScripts:'outside-only'}),w=dom.window,d=w.document,requests=[];
 let card={id:'11111111-1111-4111-8111-111111111111',version:3,status:'needs_clarification',title:'Old title',symptoms:['Old symptom'],cause:{status:'unknown',text:'Original cause'},resolution_steps:['Original steps'],outcome:{status:'unconfirmed',text:'Unknown',message_ids:[]},source_message_ids:['m1'],resolution_method:'phone',operator_actions:[],case_kind:'technical',can_approve:false,clarification_status:'required'};
 w.tenantI18n={language};w.confirm=()=>false;
 w.fetch=async(url,opt={})=>{requests.push({url,opt});let result={};
 if(url.endsWith('/improve'))result={candidate_id:card.id,expected_version:card.version,proposal:{...card,title:'Suggested title',symptoms:['Suggested symptom'],cause:{text:'MUST NOT APPLY'},resolution_steps:['MUST NOT APPLY']},requires_explicit_save:true};
 else if(opt.method==='PATCH'){card={...card,...JSON.parse(opt.body),version:card.version+1};result={candidate:card,evidence:[{id:'m1',role:'customer',text:'Evidence'}]};}
 else if(url.includes('/ai/dialogs'))result={dialogs:[]};else if(url.includes('/ai/agents'))result={agents:[]};else if(url.includes('/ai/channels'))result={channels:[]};else if(url.includes('/'+card.id))result={candidate:card,evidence:[{id:'m1',role:'customer',text:'Evidence'}]};else result={candidates:[card],next_before:null};
 return {ok:true,status:200,json:async()=>({success:true,result})};};
 w.eval(script);d.dispatchEvent(new w.CustomEvent('tenant-profile',{detail:{member:{tenant_id:'tenant-a',telegram_id:'42',role:'owner'}}}));await tick();await tick();
 assert.equal(d.getElementById('casesPeriodPreset').value,'7');assert(d.getElementById('casesFrom').disabled);
 d.getElementById('casesPeriodPreset').value='0';d.getElementById('casesPeriodPreset').onchange();assert(!d.getElementById('casesFrom').disabled);
 d.querySelector('[data-case-open]').click();await tick();await tick();assert.equal(d.getElementById('caseMethod').value,'phone');assert(d.querySelector('[data-case-approve]').disabled);
 d.querySelector('[data-case-improve]').click();await tick();await tick();assert.equal(d.getElementById('caseTitle').value,'Old title');assert(!requests.some(r=>r.opt.method==='PATCH'));
 d.querySelector('[data-case-improve]').click();await tick();await tick();const calls=requests.filter(r=>r.url.endsWith('/improve')).map(r=>JSON.parse(r.opt.body));assert.equal(calls[0].request_id,calls[1].request_id);assert.equal(calls[0].language,language);assert.equal(calls[0].expected_version,3);
 d.querySelector('[data-apply-proposal]').click();assert.equal(d.getElementById('caseTitle').value,'Suggested title');assert.equal(d.getElementById('caseCause').value,'Original cause');assert.equal(d.getElementById('caseSteps').value,'Original steps');assert(!requests.some(r=>r.opt.method==='PATCH'));
 d.querySelector('[data-case-close]').click();assert(d.getElementById('supportCaseModal'));
 d.getElementById('caseMethod').value='remote';d.getElementById('caseActions').value='Employee checked connection';d.getElementById('caseSteps').value='Check cable';d.querySelector('[data-case-save]').click();await tick();await tick();
 const patch=JSON.parse(requests.find(r=>r.opt.method==='PATCH').opt.body);assert.equal(patch.expected_version,3);assert.equal(patch.resolution_method,'remote');assert.deepEqual(patch.operator_actions,['Employee checked connection']);assert.deepEqual(patch.resolution_steps,['Check cable']);assert.equal(patch.title,'Suggested title');assert(!requests.some(r=>r.url.endsWith('/approve')));w.close();
 }console.log('PASS: RU/AZ/EN, 7-day preset, phone/remote actions, versioned save, improvement preview/replay and explicit application without factual overwrites or publication');})().catch(e=>{console.error(e);process.exitCode=1;});
